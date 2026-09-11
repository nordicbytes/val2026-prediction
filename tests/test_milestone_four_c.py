from __future__ import annotations

import inspect

import polars as pl
import pytest

from valforecast.evaluation.milestone_four_c import (
    COMPARISON_REGIONAL,
    COMPARISON_TRANSITION,
    four_c_verdict,
    lock_region_estimators,
)
from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS
from valforecast.models.regional_transition import eval_region_coverage


def _gate_frames(
    *,
    point_deltas: tuple[float, float, float, float],
    interval_highs: tuple[float, float, float, float],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    keys = (
        (2018, COMPARISON_REGIONAL),
        (2018, COMPARISON_TRANSITION),
        (2022, COMPARISON_REGIONAL),
        (2022, COMPARISON_TRANSITION),
    )
    point = pl.DataFrame(
        {
            "election_cycle": [cycle for cycle, _ in keys],
            "comparison": [comparison for _, comparison in keys],
            "delta_mae": list(point_deltas),
        }
    )
    intervals = pl.DataFrame(
        {
            "election_cycle": [cycle for cycle, _ in keys],
            "comparison": [comparison for _, comparison in keys],
            "analysis": [
                "region_draws_fixed_transition",
                "transition_draws_fixed_region_margins",
                "region_draws_fixed_transition",
                "transition_draws_fixed_region_margins",
            ],
            "interval_high": list(interval_highs),
            "interval_low": [-0.01] * 4,
            "mean_delta_mae": list(point_deltas),
        }
    )
    return point, intervals


def test_locked_dual_gate_requires_both_comparisons_both_cycles() -> None:
    point, intervals = _gate_frames(
        point_deltas=(-0.001, -0.002, -0.003, -0.004),
        interval_highs=(-0.0001, -0.0002, -0.0003, -0.0004),
    )
    assert four_c_verdict(point, intervals) == "SUPPORTED"
    not_supported, _ = _gate_frames(
        point_deltas=(-0.001, 0.0, -0.003, -0.004),
        interval_highs=(-0.0001, -0.0002, -0.0003, -0.0004),
    )
    assert four_c_verdict(not_supported, intervals) == "NOT_SUPPORTED"
    _, unclear = _gate_frames(
        point_deltas=(-0.001, -0.002, -0.003, -0.004),
        interval_highs=(-0.0001, 0.001, -0.0003, -0.0004),
    )
    assert four_c_verdict(point, unclear) == "UNCLEAR"


def test_lock_function_does_not_open_target_outcomes() -> None:
    source = inspect.getsource(lock_region_estimators)
    assert 'f"election_results_{cycle - 4}.parquet"' in source
    assert "election_results_2022.parquet" not in source
    assert "canonical_temporal_transitions.parquet" not in source


def test_eval_coverage_is_reported_separately_from_full_sweden_weights() -> None:
    parties = list(PARTIES)
    full_rows = []
    eval_rows = []
    for municipality, county, full_votes, eval_votes in (
        ("0180", "01", 1000, 400),
        ("1280", "12", 2000, 2000),
    ):
        for party in parties:
            full_rows.append(
                {
                    "district_id": f"{municipality}A",
                    "municipality_id": municipality,
                    "county_id": county,
                    "canonical_party_code": party,
                    "votes": full_votes / len(parties),
                    "valid_votes": full_votes,
                }
            )
            eval_rows.append(
                {
                    "transition_id": "2014_2018",
                    "to_district_id": f"{municipality}A",
                    "municipality_id": municipality,
                    "county_id": county,
                    "party": party,
                    "previous_vote_share": 1.0 / len(parties),
                    "current_vote_share": 1.0 / len(parties),
                    "valid_votes": eval_votes,
                    "previous_valid_votes": eval_votes,
                }
            )
    coverage = eval_region_coverage(pl.DataFrame(full_rows), pl.DataFrame(eval_rows))
    stockholm = coverage.filter(pl.col("region_id") == "0180").item(0, "eval_coverage")
    south = coverage.filter(pl.col("region_id") == "SE2").item(0, "eval_coverage")
    assert stockholm == pytest.approx(0.4)
    assert south == pytest.approx(1.0)
    assert set(coverage["region_id"]) == set(REGION_IDS)
