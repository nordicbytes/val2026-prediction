import json
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import PollWave
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_ingest import read_pxweb_jsonstat
from valforecast.polls.transition_normalize import normalize_transition_cells


def test_poll_wave_rejects_future_publication() -> None:
    wave = PollWave(
        wave_id="post-election",
        election_cycle=2022,
        pollster="Test",
        fieldwork_start=date(2022, 9, 1),
        fieldwork_end=date(2022, 9, 10),
        publication_date=date(2022, 9, 12),
        forecast_cutoff=date(2022, 9, 11),
        information_level="FULL_TRANSITION_TABLE",
        sample_size=1000,
    )
    with pytest.raises(ValueError, match="published after"):
        wave.validate_strict_cutoff()


def test_normalization_uses_no_signal_prior_for_suppressed_row() -> None:
    rows = []
    for previous_party in PARTIES:
        for current_party in PARTIES:
            suppressed = previous_party == "L"
            rows.append(
                {
                    "wave_id": "test",
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "estimate": (
                        None
                        if suppressed
                        else (0.8 if previous_party == current_party else 0.025)
                    ),
                    "margin_error": None,
                    "row_base": 100 if suppressed else 500,
                    "cell_status": "SUPPRESSED" if suppressed else "PUBLISHED",
                }
            )
    poll = pl.DataFrame(
        {
            "wave_id": ["test"] * len(PARTIES),
            "party": PARTIES,
            "poll_share": np.repeat(1 / len(PARTIES), len(PARTIES)),
        }
    )
    normalized = normalize_transition_cells(pl.DataFrame(rows), poll)
    assert np.allclose(normalized.raw_matrix.sum(axis=1), 1)
    assert np.allclose(
        normalized.raw_matrix[PARTIES.index("L")],
        np.repeat(1 / len(PARTIES), len(PARTIES)),
    )
    status = normalized.diagnostics.filter(pl.col("previous_party") == "L").item(
        0, "normalization_status"
    )
    assert status == "NO_SIGNAL_PRIOR"


def test_calibration_recovers_national_target() -> None:
    matrix = np.eye(len(PARTIES)) * 0.8 + np.ones((len(PARTIES), len(PARTIES))) * (
        0.2 / len(PARTIES)
    )
    previous = np.arange(1, len(PARTIES) + 1, dtype=float)
    target = np.arange(len(PARTIES), 0, -1, dtype=float)
    result = calibrate_transition_matrix(matrix, previous, target)
    repeated = calibrate_transition_matrix(matrix, previous, target)
    assert result.converged
    assert np.array_equal(result.matrix, repeated.matrix)
    assert np.allclose(result.matrix.sum(axis=1), 1)
    assert np.allclose(
        previous / previous.sum() @ result.matrix,
        target / target.sum(),
        atol=1e-10,
    )


def test_jsonstat_parser_uses_last_dimension_as_fastest(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps(
            {
                "id": ["row", "column"],
                "size": [2, 2],
                "dimension": {
                    "row": {"category": {"index": {"a": 0, "b": 1}}},
                    "column": {"category": {"index": {"x": 0, "y": 1}}},
                },
                "value": [1, 2, 3, None],
            }
        ),
        encoding="utf-8",
    )
    parsed = read_pxweb_jsonstat(path)
    assert parsed.rows() == [
        ("a", "x", 1.0),
        ("a", "y", 2.0),
        ("b", "x", 3.0),
        ("b", "y", None),
    ]
