from __future__ import annotations

import numpy as np
import polars as pl

from valforecast.evaluation.milestone_four_b import (
    BASELINE_MODEL,
    PRIMARY_MODEL,
    four_b_verdict,
    score_transition_draws,
)
from valforecast.features.election_history import PARTIES
from valforecast.models.transition_matrix import build_transition_predictions


def _canonical() -> pl.DataFrame:
    rows = []
    for district, previous in (
        ("0101", np.arange(1, 10, dtype=float)),
        ("0102", np.arange(9, 0, -1, dtype=float)),
    ):
        previous /= previous.sum()
        for index, party in enumerate(PARTIES):
            rows.append(
                {
                    "transition_id": "test",
                    "to_district_id": district,
                    "municipality_id": district[:2],
                    "county_id": "01",
                    "party": party,
                    "previous_vote_share": previous[index],
                    "current_vote_share": previous[index],
                    "valid_votes": 1000,
                    "previous_valid_votes": 1000,
                }
            )
    return pl.DataFrame(rows)


def test_draw_scoring_is_deterministic_and_scores_identity_exactly() -> None:
    canonical = _canonical()
    identity = np.eye(len(PARTIES))
    baseline = build_transition_predictions(
        canonical,
        identity,
        model=BASELINE_MODEL,
    )
    scores = score_transition_draws(
        canonical,
        np.repeat(identity[None, :, :], 3, axis=0),
        baseline,
        batch_size=2,
    )
    assert [score.delta_mae for score in scores] == [0.0, 0.0, 0.0]
    assert scores == score_transition_draws(
        canonical,
        np.repeat(identity[None, :, :], 3, axis=0),
        baseline,
        batch_size=1,
    )


def test_locked_gate_uses_primary_both_cycles() -> None:
    point = pl.DataFrame(
        {
            "election_cycle": [2018, 2022],
            "model": [PRIMARY_MODEL, PRIMARY_MODEL],
            "delta_mae": [-0.001, -0.002],
        }
    )
    intervals = point.rename({"delta_mae": "mean_delta_mae"}).with_columns(
        pl.lit(-0.003).alias("interval_low"),
        pl.lit(-0.0001).alias("interval_high"),
    )
    assert four_b_verdict(point, intervals) == "SUPPORTED"
    assert four_b_verdict(
        point.with_columns(
            pl.when(pl.col("election_cycle") == 2022)
            .then(0.0)
            .otherwise(pl.col("delta_mae"))
            .alias("delta_mae")
        ),
        intervals,
    ) == "NOT_SUPPORTED"
    assert four_b_verdict(
        point,
        intervals.with_columns(pl.lit(0.001).alias("interval_high")),
    ) == "UNCLEAR"
