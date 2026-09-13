from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from valforecast.experiments.valu import (
    PARTIES,
    _transition_array,
    load_final_vectors,
    prepare_live_valu,
    read_valu_toplines,
    run_valu_backtest,
    validate_live_input,
)


def _valid_published_input() -> dict[str, object]:
    return {
        "schema_version": 1,
        "election_year": 2026,
        "status": "published",
        "published_at": "2026-09-13T20:00:00+02:00",
        "retrieved_at": "2026-09-13T20:01:00+02:00",
        "source_url": "https://example.test/valu-2026.pdf",
        "source_sha256": "a" * 64,
        "sample_size": 11_000,
        "topline": {
            "V": 0.07,
            "S": 0.29,
            "MP": 0.06,
            "C": 0.08,
            "L": 0.05,
            "M": 0.17,
            "KD": 0.07,
            "SD": 0.19,
            "OTHER": 0.02,
        },
        "transition": None,
    }


def test_curated_valu_toplines_have_four_complete_cycles() -> None:
    root = Path(__file__).resolve().parents[1]

    frame = read_valu_toplines(root / "data/curated/valu_toplines.csv")

    assert frame.height == 36
    assert frame["election_year"].unique().sort().to_list() == [2010, 2014, 2018, 2022]
    assert frame.filter((frame["election_year"] == 2022) & (frame["party"] == "S"))["share"][
        0
    ] == pytest.approx(0.293)
    sums = frame.group_by("election_year").agg(pl.col("share").sum())
    assert all(value == pytest.approx(1.0) for value in sums["share"])


def test_live_input_rejects_pre_poll_close_publication() -> None:
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config/valu_live_2026.yaml").read_text(encoding="utf-8"))
    document = _valid_published_input()
    document["published_at"] = "2026-09-13T19:59:59+02:00"

    with pytest.raises(ValueError, match="precedes"):
        validate_live_input(document, config)


def test_transition_is_one_complete_row_stochastic_matrix_per_party() -> None:
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config/valu_live_2026.yaml").read_text(encoding="utf-8"))
    transition = {
        previous: {current: float(previous == current) for current in PARTIES}
        for previous in PARTIES
    }

    matrix = _transition_array(transition, config)

    np.testing.assert_allclose(matrix, np.eye(len(PARTIES)))


def test_pending_live_input_does_not_touch_the_official_snapshot() -> None:
    root = Path(__file__).resolve().parents[1]
    official = root / "forecast_snapshots/official_forecast_2026.json"
    before = official.read_bytes()

    result = prepare_live_valu(
        root,
        root / "data/templates/valu_2026.template.json",
        write=False,
    )

    assert result["status"] == "waiting_for_published_valu"
    assert official.read_bytes() == before


def test_published_live_input_is_calibrated_without_writing(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "valu.json"
    path.write_text(json.dumps(_valid_published_input()), encoding="utf-8")

    result = prepare_live_valu(root, path, write=False)

    assert result["status"] == "valu_ready"
    assert sum(result["calibrated_valu"].values()) == pytest.approx(1.0)
    assert result["official_forecast_unchanged"] is True
    assert "written" not in result


def test_additional_survey_is_raw_and_sample_size_weighted(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    document = _valid_published_input()
    tv4 = {
        "name": "TV4 test",
        "provider": "TV4",
        "status": "published",
        "published_at": "2026-09-13T19:58:00+02:00",
        "retrieved_at": "2026-09-13T20:02:00+02:00",
        "source_url": "https://example.test/tv4",
        "source_sha256": "b" * 64,
        "sample_size": 4_000,
        "topline": {
            "V": 0.08,
            "S": 0.28,
            "MP": 0.07,
            "C": 0.09,
            "L": 0.05,
            "M": 0.17,
            "KD": 0.06,
            "SD": 0.18,
            "OTHER": 0.02,
        },
    }
    document["additional_surveys"] = [tv4]
    path = tmp_path / "two-surveys.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = prepare_live_valu(root, path, write=False)

    valu_weight = 11_000 / 15_000
    tv4_weight = 4_000 / 15_000
    expected_s = result["calibrated_valu"]["S"] * valu_weight + 0.28 * tv4_weight
    assert result["survey_live"]["S"] == pytest.approx(expected_s)
    assert result["additional_surveys"][0]["correction"] == "none"
    assert result["survey_blend"]["total_sample_size"] == 15_000
    if result.get("election_night") and result["election_night"].get("proportional"):
        assert result["current_live"]["S"] != pytest.approx(expected_s)
        assert sum(result["current_live"].values()) == pytest.approx(1.0)


def test_same_sample_transition_is_raked_once_to_calibrated_topline(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "data/processed/election_results_2022.parquet").exists():
        pytest.skip("historical election data are unavailable")
    document = _valid_published_input()
    document["transition"] = {
        previous: {current: float(previous == current) for current in PARTIES}
        for previous in PARTIES
    }
    path = tmp_path / "valu-with-transition.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = prepare_live_valu(root, path, write=False)
    transition = np.asarray(result["transition"]["matrix"], dtype=float)
    previous = load_final_vectors(root, [2022])[2022]
    target = np.array([result["calibrated_valu"][party] for party in PARTIES])

    np.testing.assert_allclose(previous @ transition, target, atol=1e-10)
    assert result["transition"]["topline_role"] == "single_raking_target"


def test_historical_valu_and_combined_live_gates_pass() -> None:
    root = Path(__file__).resolve().parents[1]
    required = (
        root / "data/processed/election_results_2010.parquet",
        root / "data/raw/election_night/2018/valnatt.zip",
    )
    if not all(path.exists() for path in required):
        pytest.skip("historical election and election-night data are unavailable")

    summary = run_valu_backtest(root)

    assert summary["topline_gate"]["passed"] is True
    assert summary["topline_gate"]["cycles_won"] == 3
    assert summary["combined_live_gate"]["passed"] is True
