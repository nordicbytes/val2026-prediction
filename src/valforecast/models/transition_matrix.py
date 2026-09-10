from __future__ import annotations

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import predict_proportional_swing


def build_transition_predictions(
    canonical: pl.DataFrame,
    matrix: np.ndarray,
    *,
    model: str,
) -> pl.DataFrame:
    if matrix.shape != (len(PARTIES), len(PARTIES)):
        raise ValueError("Transition matrix has unexpected shape")
    district = canonical.pivot(
        on="party",
        index=[
            "transition_id",
            "to_district_id",
            "municipality_id",
            "county_id",
            "valid_votes",
            "previous_valid_votes",
        ],
        values=["previous_vote_share", "current_vote_share"],
        separator="_",
    ).sort("to_district_id")
    rows: list[dict[str, object]] = []
    for record in district.iter_rows(named=True):
        previous = np.array(
            [record[f"previous_vote_share_{party}"] for party in PARTIES],
            dtype=float,
        )
        smoothed = _replace_zero_shares(
            previous,
            valid_votes=int(record["previous_valid_votes"]),
        )
        predicted = smoothed @ matrix
        predicted /= predicted.sum()
        for party_index, party in enumerate(PARTIES):
            rows.append(
                {
                    "transition_id": record["transition_id"],
                    "district_id": record["to_district_id"],
                    "municipality_id": record["municipality_id"],
                    "county_id": record["county_id"],
                    "party": party,
                    "model": model,
                    "actual_share": record[f"current_vote_share_{party}"],
                    "predicted_share": float(predicted[party_index]),
                    "previous_share": float(previous[party_index]),
                    "valid_votes": record["valid_votes"],
                    "previous_valid_votes": record["previous_valid_votes"],
                }
            )
    return pl.DataFrame(rows)


def build_poll_state_proportional_predictions(
    canonical: pl.DataFrame,
    previous_national: np.ndarray,
    poll_national: np.ndarray,
    *,
    model: str = "B2_poll_state",
) -> pl.DataFrame:
    district = canonical.pivot(
        on="party",
        index=[
            "transition_id",
            "to_district_id",
            "municipality_id",
            "county_id",
            "valid_votes",
            "previous_valid_votes",
        ],
        values=["previous_vote_share", "current_vote_share"],
        separator="_",
    ).sort("to_district_id")
    rows: list[dict[str, object]] = []
    for record in district.iter_rows(named=True):
        previous = np.array(
            [record[f"previous_vote_share_{party}"] for party in PARTIES],
            dtype=float,
        )
        smoothed = _replace_zero_shares(
            previous,
            valid_votes=int(record["previous_valid_votes"]),
        )
        predicted = predict_proportional_swing(
            smoothed,
            previous_national,
            poll_national,
        )
        for party_index, party in enumerate(PARTIES):
            rows.append(
                {
                    "transition_id": record["transition_id"],
                    "district_id": record["to_district_id"],
                    "municipality_id": record["municipality_id"],
                    "county_id": record["county_id"],
                    "party": party,
                    "model": model,
                    "actual_share": record[f"current_vote_share_{party}"],
                    "predicted_share": float(predicted[party_index]),
                    "previous_share": float(previous[party_index]),
                    "valid_votes": record["valid_votes"],
                    "previous_valid_votes": record["previous_valid_votes"],
                }
            )
    return pl.DataFrame(rows)


def aggregate_party_shares(predictions: pl.DataFrame) -> pl.DataFrame:
    return predictions.group_by("transition_id", "model", "party").agg(
        (
            (pl.col("predicted_share") * pl.col("valid_votes")).sum()
            / pl.col("valid_votes").sum()
        ).alias("predicted_national_share"),
        (
            (pl.col("actual_share") * pl.col("valid_votes")).sum()
            / pl.col("valid_votes").sum()
        ).alias("actual_evaluation_share"),
    ).sort("transition_id", "model", "party")


def _replace_zero_shares(
    shares: np.ndarray,
    *,
    valid_votes: int,
    pseudo_count: float = 0.5,
) -> np.ndarray:
    counts = shares * valid_votes
    counts = np.where(counts == 0, pseudo_count, counts)
    return np.asarray(counts / counts.sum(), dtype=float)
