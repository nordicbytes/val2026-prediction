from __future__ import annotations

from typing import cast

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS, assign_regions, region_index
from valforecast.models.baselines import predict_proportional_swing
from valforecast.polls.region_condition import calibrate_region_transition_matrices


def previous_region_vectors(results: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    assigned = assign_regions(results)
    shares = np.zeros((len(REGION_IDS), len(PARTIES)), dtype=float)
    weights = np.zeros(len(REGION_IDS), dtype=float)
    for region_id in REGION_IDS:
        region = assigned.filter(pl.col("region_id") == region_id)
        votes = region.group_by("canonical_party_code").agg(pl.col("votes").sum())
        by_party = dict(votes.iter_rows())
        vector = np.array([float(by_party.get(party, 0.0)) for party in PARTIES], dtype=float)
        if vector.sum() <= 0:
            raise ValueError(f"Region {region_id} has no previous-election votes")
        shares[region_index(region_id)] = vector / vector.sum()
        weights[region_index(region_id)] = float(vector.sum())
    if not np.allclose(weights.sum(), float(results["votes"].sum())):
        raise ValueError("Regional previous-election weights do not cover all Sweden")
    return shares, weights / weights.sum()


def attach_regions(canonical: pl.DataFrame) -> pl.DataFrame:
    return assign_regions(canonical)


def build_regional_transition_predictions(
    canonical: pl.DataFrame,
    matrices_by_region: np.ndarray,
    *,
    model: str,
) -> pl.DataFrame:
    if matrices_by_region.shape != (len(REGION_IDS), len(PARTIES), len(PARTIES)):
        raise ValueError("Regional transition stack has unexpected shape")
    district = _pivot_districts(canonical)
    rows: list[dict[str, object]] = []
    for record in district.iter_rows(named=True):
        previous = np.array(
            [record[f"previous_vote_share_{party}"] for party in PARTIES],
            dtype=float,
        )
        smoothed = replace_zero_shares(
            previous,
            valid_votes=int(record["previous_valid_votes"]),
        )
        predicted = smoothed @ matrices_by_region[region_index(str(record["region_id"]))]
        predicted /= predicted.sum()
        rows.extend(_prediction_rows(record, previous, predicted, model))
    return pl.DataFrame(rows)


def build_regional_proportional_predictions(
    canonical: pl.DataFrame,
    previous_by_region: np.ndarray,
    poll_by_region: np.ndarray,
    *,
    model: str,
) -> pl.DataFrame:
    if previous_by_region.shape != (len(REGION_IDS), len(PARTIES)):
        raise ValueError("Previous-by-region matrix has unexpected shape")
    if poll_by_region.shape != (len(REGION_IDS), len(PARTIES)):
        raise ValueError("Regional poll matrix has unexpected shape")
    district = _pivot_districts(canonical)
    rows: list[dict[str, object]] = []
    for record in district.iter_rows(named=True):
        previous = np.array(
            [record[f"previous_vote_share_{party}"] for party in PARTIES],
            dtype=float,
        )
        smoothed = replace_zero_shares(
            previous,
            valid_votes=int(record["previous_valid_votes"]),
        )
        index = region_index(str(record["region_id"]))
        predicted = predict_proportional_swing(
            smoothed,
            previous_by_region[index],
            poll_by_region[index],
        )
        rows.extend(_prediction_rows(record, previous, predicted, model))
    return pl.DataFrame(rows)


def posterior_mean_regional_predictions(
    canonical: pl.DataFrame,
    regional_draws: np.ndarray,
    *,
    model: str,
) -> pl.DataFrame:
    draws = np.asarray(regional_draws, dtype=float)
    if draws.ndim != 4:
        raise ValueError("Expected draw-by-region-by-previous-by-current matrices")
    return build_regional_transition_predictions(canonical, draws.mean(axis=0), model=model)


def score_prepared_regional_matrices(
    canonical: pl.DataFrame,
    matrices_draws: np.ndarray,
    comparison_predictions: pl.DataFrame,
    *,
    comparison_name: str,
) -> pl.DataFrame:
    previous, actual, weights, district_ids, region_ids = regional_canonical_arrays(
        canonical
    )
    comparison = (
        comparison_predictions.pivot(on="party", index="district_id", values="predicted_share")
        .select("district_id", *PARTIES)
        .sort("district_id")
    )
    if comparison["district_id"].to_list() != district_ids:
        raise ValueError("Comparison and canonical district populations differ")
    comparison_mae = _weighted_mae(comparison.select(PARTIES).to_numpy(), actual, weights)
    scores: list[dict[str, object]] = []
    for draw_index, matrices in enumerate(np.asarray(matrices_draws, dtype=float)):
        predicted = _predict_by_region(previous, region_ids, matrices)
        model_mae = _weighted_mae(predicted, actual, weights)
        scores.append(
            {
                "draw": draw_index,
                "comparison": comparison_name,
                "model_mae": model_mae,
                "comparison_mae": comparison_mae,
                "delta_mae": model_mae - comparison_mae,
            }
        )
    return pl.DataFrame(scores)


def score_regional_transition_draws(
    canonical: pl.DataFrame,
    national_draws: np.ndarray,
    previous_by_region: np.ndarray,
    regional_targets: np.ndarray,
    comparison_predictions: pl.DataFrame,
    *,
    comparison_name: str,
) -> pl.DataFrame:
    previous, actual, weights, district_ids, region_ids = regional_canonical_arrays(canonical)
    comparison = (
        comparison_predictions.pivot(on="party", index="district_id", values="predicted_share")
        .select("district_id", *PARTIES)
        .sort("district_id")
    )
    if comparison["district_id"].to_list() != district_ids:
        raise ValueError("Comparison and canonical district populations differ")
    comparison_values = comparison.select(PARTIES).to_numpy()
    comparison_mae = _weighted_mae(comparison_values, actual, weights)
    scores: list[dict[str, object]] = []
    for draw_index, national_matrix in enumerate(np.asarray(national_draws, dtype=float)):
        matrices = calibrate_region_transition_matrices(
            national_matrix,
            previous_by_region,
            regional_targets,
        )
        predicted = _predict_by_region(previous, region_ids, matrices)
        model_mae = _weighted_mae(predicted, actual, weights)
        scores.append(
            {
                "draw": draw_index,
                "comparison": comparison_name,
                "model_mae": model_mae,
                "comparison_mae": comparison_mae,
                "delta_mae": model_mae - comparison_mae,
            }
        )
    return pl.DataFrame(scores)


def score_region_margin_draws(
    canonical: pl.DataFrame,
    national_matrix: np.ndarray,
    previous_by_region: np.ndarray,
    regional_target_draws: np.ndarray,
    comparison_predictions: pl.DataFrame,
    *,
    comparison_name: str,
) -> pl.DataFrame:
    previous, actual, weights, district_ids, region_ids = regional_canonical_arrays(canonical)
    comparison = (
        comparison_predictions.pivot(on="party", index="district_id", values="predicted_share")
        .select("district_id", *PARTIES)
        .sort("district_id")
    )
    if comparison["district_id"].to_list() != district_ids:
        raise ValueError("Comparison and canonical district populations differ")
    comparison_values = comparison.select(PARTIES).to_numpy()
    scores: list[dict[str, object]] = []
    for draw_index, regional_targets in enumerate(np.asarray(regional_target_draws, dtype=float)):
        if comparison_name == "transition_value":
            comparison_draw = _regional_proportional_array(
                previous,
                region_ids,
                previous_by_region,
                regional_targets,
            )
            comparison_mae = _weighted_mae(comparison_draw, actual, weights)
        else:
            comparison_mae = _weighted_mae(comparison_values, actual, weights)
        matrices = calibrate_region_transition_matrices(
            national_matrix,
            previous_by_region,
            regional_targets,
        )
        predicted = _predict_by_region(previous, region_ids, matrices)
        model_mae = _weighted_mae(predicted, actual, weights)
        scores.append(
            {
                "draw": draw_index,
                "comparison": comparison_name,
                "model_mae": model_mae,
                "comparison_mae": comparison_mae,
                "delta_mae": model_mae - comparison_mae,
            }
        )
    return pl.DataFrame(scores)


def regional_canonical_arrays(
    canonical: pl.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], np.ndarray]:
    district = _pivot_districts(canonical)
    previous = district.select(
        [f"previous_vote_share_{party}" for party in PARTIES]
    ).to_numpy()
    actual = district.select(
        [f"current_vote_share_{party}" for party in PARTIES]
    ).to_numpy()
    previous_counts = previous * district["previous_valid_votes"].to_numpy()[:, None]
    previous_counts = np.where(previous_counts == 0, 0.5, previous_counts)
    previous = previous_counts / previous_counts.sum(axis=1, keepdims=True)
    weights = district["valid_votes"].to_numpy().astype(float)
    district_ids = [str(value) for value in district["to_district_id"].to_list()]
    region_ids = np.array(
        [region_index(str(value)) for value in district["region_id"].to_list()],
        dtype=int,
    )
    return (
        np.asarray(previous, dtype=float),
        np.asarray(actual, dtype=float),
        np.asarray(weights, dtype=float),
        district_ids,
        region_ids,
    )


def eval_region_coverage(
    full_results: pl.DataFrame,
    canonical: pl.DataFrame,
) -> pl.DataFrame:
    full_assigned = assign_regions(full_results)
    eval_assigned = attach_regions(canonical)
    rows: list[dict[str, object]] = []
    for region_id in REGION_IDS:
        full_votes = _sum_or_zero(
            full_assigned.filter(pl.col("region_id") == region_id)["votes"].sum()
        )
        eval_votes = _sum_or_zero(
            eval_assigned.filter(pl.col("region_id") == region_id)
            .select("to_district_id", "previous_valid_votes")
            .unique()["previous_valid_votes"]
            .sum()
        )
        rows.append(
            {
                "region_id": region_id,
                "full_previous_votes": full_votes,
                "eval_previous_votes": eval_votes,
                "eval_coverage": eval_votes / full_votes if full_votes else 0.0,
            }
        )
    return pl.DataFrame(rows)


def _pivot_districts(canonical: pl.DataFrame) -> pl.DataFrame:
    frame = attach_regions(canonical) if "region_id" not in canonical.columns else canonical
    return frame.pivot(
        on="party",
        index=[
            "transition_id",
            "to_district_id",
            "municipality_id",
            "county_id",
            "region_id",
            "valid_votes",
            "previous_valid_votes",
        ],
        values=["previous_vote_share", "current_vote_share"],
        separator="_",
    ).sort("to_district_id")


def _prediction_rows(
    record: dict[str, object],
    previous: np.ndarray,
    predicted: np.ndarray,
    model: str,
) -> list[dict[str, object]]:
    return [
        {
            "transition_id": record["transition_id"],
            "district_id": record["to_district_id"],
            "municipality_id": record["municipality_id"],
            "county_id": record["county_id"],
            "region_id": record["region_id"],
            "party": party,
            "model": model,
            "actual_share": record[f"current_vote_share_{party}"],
            "predicted_share": float(predicted[party_index]),
            "previous_share": float(previous[party_index]),
            "valid_votes": record["valid_votes"],
            "previous_valid_votes": record["previous_valid_votes"],
        }
        for party_index, party in enumerate(PARTIES)
    ]


def _predict_by_region(
    previous: np.ndarray,
    region_ids: np.ndarray,
    matrices_by_region: np.ndarray,
) -> np.ndarray:
    predicted = np.empty_like(previous)
    for index in range(len(REGION_IDS)):
        mask = region_ids == index
        if not np.any(mask):
            continue
        values = previous[mask] @ matrices_by_region[index]
        values /= values.sum(axis=1, keepdims=True)
        predicted[mask] = values
    return predicted


def _regional_proportional_array(
    previous: np.ndarray,
    region_ids: np.ndarray,
    previous_by_region: np.ndarray,
    poll_by_region: np.ndarray,
) -> np.ndarray:
    predicted = np.empty_like(previous)
    for district_index, region in enumerate(region_ids):
        predicted[district_index] = predict_proportional_swing(
            previous[district_index],
            previous_by_region[region],
            poll_by_region[region],
        )
    return predicted


def _sum_or_zero(value: object) -> float:
    if value is None:
        return 0.0
    return float(cast(float, value))


def replace_zero_shares(
    shares: np.ndarray,
    *,
    valid_votes: int,
    pseudo_count: float = 0.5,
) -> np.ndarray:
    counts = shares * valid_votes
    counts = np.where(counts == 0, pseudo_count, counts)
    return np.asarray(counts / counts.sum(), dtype=float)


def _weighted_mae(
    predicted: np.ndarray,
    actual: np.ndarray,
    district_weights: np.ndarray,
) -> float:
    numerator = cast(float, np.sum(np.abs(predicted - actual) * district_weights[:, None]))
    return float(numerator / (district_weights.sum() * len(PARTIES)))
