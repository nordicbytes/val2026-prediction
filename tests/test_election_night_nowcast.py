from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from valforecast.experiments.election_night_nowcast import (
    PARTIES,
    _comparison_frame,
    build_replay_predictions,
    evaluate_gate,
    read_2018_election_night,
    read_2022_election_night,
    replay_cycle,
    score_replay,
)


def _party_values(**overrides: float) -> dict[str, float]:
    values = {party: 0.0 for party in PARTIES}
    values.update(overrides)
    return values


def test_replay_uses_only_reported_votes_and_previous_shares() -> None:
    preliminary = pl.DataFrame(
        [
            {
                "election_year": 2022,
                "district_id": "a",
                "reported_at": datetime(2022, 9, 11, 20, 30),
                "valid_votes": 100,
                **_party_values(S=60, M=40),
            },
            {
                "election_year": 2022,
                "district_id": "b",
                "reported_at": datetime(2022, 9, 11, 22, 30),
                "valid_votes": 100,
                **_party_values(S=20, M=80),
            },
        ]
    )
    comparison = pl.DataFrame(
        [
            {
                "district_id": "a",
                "previous_valid_votes": 100,
                **{
                    f"previous_{party}": value
                    for party, value in _party_values(S=0.5, M=0.5).items()
                },
            },
            {
                "district_id": "b",
                "previous_valid_votes": 100,
                **{
                    f"previous_{party}": value
                    for party, value in _party_values(S=0.7, M=0.3).items()
                },
            },
        ]
    )
    previous_national = np.array([_party_values(S=0.6, M=0.4)[party] for party in PARTIES])
    first = replay_cycle(
        preliminary,
        comparison,
        previous_national=previous_national,
        final_national=np.array([_party_values(S=0.1, M=0.9)[party] for party in PARTIES]),
        checkpoints=["21:00"],
    )
    changed_truth = replay_cycle(
        preliminary,
        comparison,
        previous_national=previous_national,
        final_national=np.array([_party_values(S=0.9, M=0.1)[party] for party in PARTIES]),
        checkpoints=["21:00"],
    )

    assert first["reported_districts"][0] == 1
    assert first["raw_S"][0] == pytest.approx(0.6)
    assert first["additive_S"][0] == pytest.approx(0.7)
    assert first["proportional_S"][0] == pytest.approx(9 / 13)
    assert first.select(pl.col("^additive_.*$")).equals(
        changed_truth.select(pl.col("^additive_.*$"))
    )


def test_comparison_frame_names_previous_columns_explicitly() -> None:
    canonical = pl.DataFrame(
        [
            {
                "to_election": 2022,
                "to_district_id": "x",
                "party": party,
                "previous_vote_share": value,
                "previous_valid_votes": 100,
            }
            for party, value in _party_values(S=0.6, M=0.4).items()
        ]
    )

    result = _comparison_frame(canonical, 2022)

    assert "previous_S" in result.columns
    assert "S" not in result.columns
    assert result["previous_S"][0] == pytest.approx(0.6)


def test_official_archives_have_expected_physical_districts() -> None:
    root = Path(__file__).resolve().parents[1]
    path_2018 = root / "data/raw/election_night/2018/valnatt.zip"
    path_2022 = root / "data/raw/election_night/2022/Val_20220911_preliminar_00_RD.zip"
    if not path_2018.exists() or not path_2022.exists():
        pytest.skip("experimental election-night archives have not been downloaded")

    election_2018 = read_2018_election_night(path_2018)
    election_2022 = read_2022_election_night(path_2022)

    assert election_2018.height == 6_004
    assert election_2018["reported_at"].min() == datetime(2018, 9, 9, 20, 23, 46)
    assert election_2022.height == 6_264
    assert election_2022["reported_at"].min() == datetime(2022, 9, 11, 20, 39, 47)


def test_historical_replay_reproduces_the_registered_result() -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "data/raw/election_night/2018/valnatt.zip").exists():
        pytest.skip("experimental election-night archives have not been downloaded")
    import yaml

    config = yaml.safe_load(
        (root / "config/election_night_nowcast.yaml").read_text(encoding="utf-8")
    )
    predictions = build_replay_predictions(root, config)
    metrics = score_replay(predictions)
    gate = evaluate_gate(
        metrics,
        start="21:00",
        end="23:00",
        minimum_win_fraction=0.75,
    )

    assert predictions.height == 24
    assert gate["passed"] is False
    assert gate["checkpoint_win_fraction"] == pytest.approx(11 / 16)
    proportional = metrics.filter(pl.col("model") == "C2_reported_proportional_change")
    raw = metrics.filter(pl.col("model") == "B0_raw_reported_share")
    early = ["21:00", "21:30"]
    assert (
        proportional.filter(pl.col("checkpoint").is_in(early))["mae"]
        < raw.filter(pl.col("checkpoint").is_in(early))["mae"]
    ).all()
