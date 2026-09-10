from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import polars as pl


@dataclass(frozen=True)
class BaselineMetrics:
    model: str
    district_weighted_mae: float
    district_unweighted_mae: float
    national_mae: float
    observations: int
    districts: int


def project_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection of a vector onto the probability simplex."""
    if values.ndim != 1 or values.size == 0:
        raise ValueError("Expected a non-empty one-dimensional vector")
    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered) - 1
    indices = np.arange(1, values.size + 1)
    positive = ordered - cumulative / indices > 0
    rho = np.flatnonzero(positive)[-1]
    threshold = cumulative[rho] / (rho + 1)
    return np.asarray(np.maximum(values - threshold, 0), dtype=float)


def predict_uniform_swing(previous: np.ndarray, national_delta: np.ndarray) -> np.ndarray:
    clipped = np.maximum(previous + national_delta, 0)
    return np.asarray(clipped / clipped.sum(), dtype=float)


def predict_proportional_swing(
    previous: np.ndarray,
    national_previous: np.ndarray,
    national_current: np.ndarray,
    *,
    epsilon: float = 1e-9,
) -> np.ndarray:
    ratios = (national_current + epsilon) / (national_previous + epsilon)
    prediction = previous * ratios
    return prediction / prediction.sum()


def evaluate_predictions(
    predictions: pl.DataFrame,
    *,
    model: str,
) -> BaselineMetrics:
    required = {
        "district_id",
        "party",
        "actual_share",
        "predicted_share",
        "valid_votes",
    }
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"Missing prediction columns: {sorted(missing)}")
    scored = predictions.with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("absolute_error")
    )
    weight_total = _as_float(scored["valid_votes"].sum())
    if weight_total <= 0:
        raise ValueError("Prediction weights must sum to a positive number")

    national = scored.group_by("party").agg(
        (
            (pl.col("actual_share") * pl.col("valid_votes")).sum()
            / pl.col("valid_votes").sum()
        ).alias("actual"),
        (
            (pl.col("predicted_share") * pl.col("valid_votes")).sum()
            / pl.col("valid_votes").sum()
        ).alias("predicted"),
    )
    return BaselineMetrics(
        model=model,
        district_weighted_mae=(
            _as_float((scored["absolute_error"] * scored["valid_votes"]).sum())
            / weight_total
        ),
        district_unweighted_mae=_as_float(scored["absolute_error"].mean()),
        national_mae=_as_float((national["actual"] - national["predicted"]).abs().mean()),
        observations=scored.height,
        districts=scored["district_id"].n_unique(),
    )


def build_2018_2022_predictions(
    results_2018: pl.DataFrame,
    results_2022: pl.DataFrame,
    crosswalk: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    parties = ["V", "S", "MP", "C", "L", "M", "KD", "SD", "OTHER"]
    previous = _canonical_district_counts(results_2018)
    current = _canonical_district_counts(results_2022)
    national_previous = _national_shares(previous, parties)
    national_current = _national_shares(current, parties)
    national_delta = national_current - national_previous

    comparable = evaluation_mapping_rows(crosswalk)
    mapped_previous = (
        comparable.join(
            previous,
            left_on="from_district_id",
            right_on="district_id",
            how="inner",
        )
        .group_by("to_district_id", "canonical_party_code")
        .agg(
            pl.col("votes").sum().alias("votes"),
            pl.col("valid_votes").sum().alias("valid_votes"),
        )
        .rename({"to_district_id": "district_id"})
    )
    mapping_labels = comparable.group_by("to_district_id").agg(
        pl.col("from_district_id").str.join(",").alias("from_district_id")
    )
    previous_wide = _wide_shares(mapped_previous, "to_district_id", parties)
    current_wide = _wide_shares(current, "to_district_id", parties)
    joined = (
        mapping_labels.join(previous_wide, on="to_district_id", how="inner")
        .join(current_wide, on="to_district_id", how="inner", suffix="_actual")
    )

    rows: dict[str, list[dict[str, object]]] = {
        "previous_result": [],
        "uniform_national_residual": [],
        "uniform_national_swing": [],
        "proportional_swing": [],
    }
    for record in joined.iter_rows(named=True):
        previous_vector = np.array([record[f"{party}_share"] for party in parties], dtype=float)
        actual_vector = np.array(
            [record[f"{party}_share_actual"] for party in parties], dtype=float
        )
        predictions = {
            "previous_result": previous_vector,
            "uniform_national_residual": previous_vector + national_delta,
            "uniform_national_swing": predict_uniform_swing(previous_vector, national_delta),
            "proportional_swing": predict_proportional_swing(
                _replace_zeros(
                    previous_vector,
                    valid_votes=int(record["valid_votes"]),
                ),
                national_previous,
                national_current,
            ),
        }
        for model, prediction in predictions.items():
            for party, actual_share, predicted_share in zip(
                parties, actual_vector, prediction, strict=True
            ):
                rows[model].append(
                    {
                        "district_id": record["to_district_id"],
                        "from_district_id": record["from_district_id"],
                        "party": party,
                        "actual_share": actual_share,
                        "predicted_share": float(predicted_share),
                        "valid_votes": record["valid_votes_actual"],
                        "national_swing": float(national_delta[parties.index(party)]),
                        "actual_local_swing": float(
                            actual_share - previous_vector[parties.index(party)]
                        ),
                    }
                )
    return {model: pl.DataFrame(model_rows) for model, model_rows in rows.items()}


def evaluation_mapping_rows(crosswalk: pl.DataFrame) -> pl.DataFrame:
    return crosswalk.filter(
        pl.col("relation").is_in(["SAME", "COMPARABLE", "MERGED"])
    ).select(
        "from_district_id", "to_district_id"
    )


def _canonical_district_counts(results: pl.DataFrame) -> pl.DataFrame:
    return results.group_by("district_id", "canonical_party_code").agg(
        pl.col("votes").sum().alias("votes"),
        pl.col("valid_votes").first().alias("valid_votes"),
    )


def _national_shares(counts: pl.DataFrame, parties: list[str]) -> np.ndarray:
    totals = counts.group_by("canonical_party_code").agg(pl.col("votes").sum())
    by_party = dict(totals.select("canonical_party_code", "votes").iter_rows())
    denominator = sum(int(value) for value in by_party.values())
    return np.array([int(by_party.get(party, 0)) / denominator for party in parties])


def _wide_shares(
    counts: pl.DataFrame,
    district_name: str,
    parties: list[str],
) -> pl.DataFrame:
    complete = (
        counts.with_columns((pl.col("votes") / pl.col("valid_votes")).alias("share"))
        .select("district_id", "canonical_party_code", "share", "valid_votes")
        .pivot(on="canonical_party_code", index=["district_id", "valid_votes"], values="share")
    )
    missing = set(parties) - set(complete.columns)
    complete = complete.with_columns(*(pl.lit(0.0).alias(party) for party in missing))
    return complete.select(
        pl.col("district_id").alias(district_name),
        "valid_votes",
        *(pl.col(party).fill_null(0.0).alias(f"{party}_share") for party in parties),
    )


def _as_float(value: object) -> float:
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _replace_zeros(
    shares: np.ndarray,
    *,
    valid_votes: int,
    pseudo_count: float = 0.5,
) -> np.ndarray:
    counts = shares * valid_votes
    counts = np.where(counts == 0, pseudo_count, counts)
    return np.asarray(counts / counts.sum(), dtype=float)

