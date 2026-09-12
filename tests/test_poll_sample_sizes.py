from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from valforecast.calibration.corpus import build_poll_corpus, last_polls
from valforecast.calibration.parse_temo import parse_temo_archive
from valforecast.calibration.sample_sizes import (
    apply_sample_size_overrides,
    load_sample_size_overrides,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data/raw/polls/history/wikipedia_en_polling_2022.html"
IDENTITY_FIELDS = (
    "shares",
    "fieldwork_start",
    "fieldwork_end",
    "institute_family",
    "pollster",
    "publication_date",
    "election_cycle",
)


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_sample_size_overrides_match_existing_poll_ids() -> None:
    rows = build_poll_corpus(ROOT)
    known = {str(row["poll_id"]) for row in rows}
    overrides = load_sample_size_overrides(ROOT)
    assert overrides
    missing = [item["poll_id"] for item in overrides if item["poll_id"] not in known]
    assert missing == []


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_sample_size_overrides_only_change_n() -> None:
    overrides = load_sample_size_overrides(ROOT)
    rows = build_poll_corpus(ROOT)
    by_id = {str(row["poll_id"]): row for row in rows}
    before = {
        item["poll_id"]: {
            field: deepcopy(by_id[item["poll_id"]][field])
            for field in IDENTITY_FIELDS
        }
        for item in overrides
    }
    after_rows = apply_sample_size_overrides(deepcopy(rows), overrides)
    after = {str(row["poll_id"]): row for row in after_rows}
    for item in overrides:
        poll_id = item["poll_id"]
        for field in IDENTITY_FIELDS:
            assert after[poll_id][field] == before[poll_id][field]
        assert after[poll_id]["sample_size"] == item["sample_size"]


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_sample_size_override_refuses_unjustified_overwrite() -> None:
    rows = build_poll_corpus(ROOT)
    parsed = next(row for row in rows if row["sample_size"] is not None)
    conflict = {
        "poll_id": str(parsed["poll_id"]),
        "sample_size": int(parsed["sample_size"]) + 17,
        "source_url": "https://example.test/conflict",
        "source_level": "C",
        "note": "Ingen motivering för att byta ett redan parsat n.",
    }
    with pytest.raises(ValueError, match="without an explicit overwrite note"):
        apply_sample_size_overrides(deepcopy(rows), [conflict])
    allowed = dict(conflict)
    allowed["note"] = "skriver över parsad n efter kontroll mot instituts-PDF"
    updated = apply_sample_size_overrides(deepcopy(rows), [allowed])
    match = next(row for row in updated if row["poll_id"] == parsed["poll_id"])
    assert match["sample_size"] == allowed["sample_size"]
    assert match["shares"] == parsed["shares"]
    assert match["fieldwork_end"] == parsed["fieldwork_end"]
    assert match["institute_family"] == parsed["institute_family"]


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_temo_2006_parser_reads_antal_intervjuer() -> None:
    rows = parse_temo_archive(
        ROOT / "data/raw/polls/history/temo_valjarbarometer_2006.html",
        cycle=2006,
        url="https://example.test/temo-2006",
        retrieved_at="2026-09-12T08:15:00+00:00",
    )
    assert rows
    assert all(row["sample_size"] is not None for row in rows)
    lasts = last_polls(rows)
    expected = {
        ("Demoskop", "2006-09-05"): 1007,
        ("Ipsos", "2006-09-10"): 1793,
        ("SKOP", "2006-09-04"): 1075,
        ("Sifo/Verian", "2006-09-11"): 1186,
    }
    got = {(row["institute_family"], row["fieldwork_end"]): row["sample_size"] for row in lasts}
    assert got == expected
