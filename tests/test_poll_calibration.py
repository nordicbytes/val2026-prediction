from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from valforecast.calibration.components import kish_effective_institutes
from valforecast.calibration.corpus import POLL_GOLD, build_poll_corpus, last_polls
from valforecast.calibration.elections import RESULT_GOLD, load_official_national_results
from valforecast.calibration.estimate import HOUSE_PRIOR_STRENGTH, estimate_house_effects
from valforecast.calibration.gate import GATE_RULE, run_house_effect_gate
from valforecast.calibration.html_tables import parse_tables
from valforecast.calibration.names import parse_fieldwork
from valforecast.calibration.parse_wikipedia import (
    MAX_WINDOW_POLLS_PER_CYCLE,
    assign_wikipedia_block_years,
    impossible_archive_citations,
)
from valforecast.config import load_sources
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.lock import build_input_lock
from valforecast.forecast.polls import load_forecast_polls, parser_raw_files
from valforecast.forecast.snapshot import official_snapshot_path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = official_snapshot_path(ROOT)
RAW_2022 = ROOT / "data/raw/valmyndigheten/2022/roster_per_distrikt_slutligt_riksdag.xlsx"
HISTORY = ROOT / "data/raw/polls/history/wikipedia_en_polling_2022.html"


OFFICIAL_SHA256 = "48702a6de161f9605c833f4ae1337697d7ffee2fb8207fd57af12d084c9da6f7"


def test_official_snapshot_bytes_are_untouched() -> None:
    assert OFFICIAL.exists()
    digest = hashlib.sha256(OFFICIAL.read_bytes()).hexdigest()
    assert digest == OFFICIAL_SHA256


@pytest.mark.skipif(not RAW_2022.exists(), reason="Pinned election files are absent")
def test_official_results_match_gold_cells() -> None:
    results = load_official_national_results(ROOT)
    for year, gold in RESULT_GOLD.items():
        for party, expected in gold.items():
            assert abs(results[year][party] - expected) < 0.0006


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_corpus_has_gold_row_per_cycle() -> None:
    rows = build_poll_corpus(ROOT)
    cycles = {int(row["election_cycle"]) for row in rows}
    assert {2010, 2014, 2018, 2022} <= cycles
    for cycle, spec in POLL_GOLD.items():
        family, end, expected = spec["match"]
        matches = [
            row
            for row in rows
            if int(row["election_cycle"]) == cycle
            and row["institute_family"] == family
            and row["fieldwork_end"] == end
        ]
        assert matches
        assert abs(float(matches[0]["shares"][spec["party"]]) - float(expected)) < 0.002
    for row in rows:
        assert abs(sum(row["shares"].values()) - 1.0) < 1e-9
        assert int(row["days_to_election"]) >= 1
        assert row["source_level"] in {"A", "B", "C"}


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_single_cycle_house_effect_is_shrunk() -> None:
    results = load_official_national_results(ROOT)
    lasts = last_polls(build_poll_corpus(ROOT))
    errors = []
    for row in lasts:
        cycle = int(row["election_cycle"])
        errors.append(
            {
                **row,
                "error": {
                    party: float(row["shares"][party]) - float(results[cycle][party])
                    for party in row["shares"]
                },
            }
        )
    houses = estimate_house_effects(errors)
    assert HOUSE_PRIOR_STRENGTH == 3.0
    for _family, estimate in houses.items():
        n_cycles = int(estimate["n_cycles"])
        expected_shrink = n_cycles / (n_cycles + HOUSE_PRIOR_STRENGTH)
        assert abs(float(estimate["shrinkage"]) - expected_shrink) < 1e-12
        if n_cycles == 1:
            raw = abs(float(estimate["raw_mean"]["S"]))
            shrunk = abs(float(estimate["shrunk"]["S"]))
            assert shrunk < raw or raw == 0.0


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_gate_is_deterministic_and_pre_registered() -> None:
    assert "SUPPORTED only if" in GATE_RULE
    results = load_official_national_results(ROOT)
    lasts = last_polls(build_poll_corpus(ROOT))
    first = run_house_effect_gate(lasts, results)
    second = run_house_effect_gate(lasts, results)
    assert first == second
    assert first["verdict"] in {"SUPPORTED", "NOT_SUPPORTED"}


def test_forecast_contract_file_was_not_rewritten() -> None:
    expected = "11b248e06ff0eb7ecd92ad2ce7c9458e5cf483d17bc0173ed48915151c1360a4"
    digest = hashlib.sha256((ROOT / "config" / "forecast_2026.yaml").read_bytes()).hexdigest()
    assert digest == expected


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_level_c_rows_keep_citations() -> None:
    rows = build_poll_corpus(ROOT)
    twenty_eighteen = [
        row for row in rows if int(row["election_cycle"]) == 2018 and row["source_level"] == "C"
    ]
    assert twenty_eighteen
    assert any(row.get("citation_urls") for row in twenty_eighteen)
    twenty_twenty_two = [
        row for row in rows if int(row["election_cycle"]) == 2022 and row["source_level"] == "C"
    ]
    assert any(row.get("citations") for row in twenty_twenty_two)


def test_temo_third_party_rows_are_level_c() -> None:
    if not HISTORY.exists():
        pytest.skip("Pinned historical poll files are absent")
    rows = build_poll_corpus(ROOT)
    temo = [
        row
        for row in rows
        if int(row["election_cycle"]) in {2002, 2006}
    ]
    assert temo
    assert all(
        row["source_level"] == "A"
        for row in temo
        if row["institute_family"] == "Ipsos"
    )
    assert all(
        row["source_level"] == "C"
        for row in temo
        if row["institute_family"] != "Ipsos"
    )


def test_kish_n_eff_is_inverse_sum_of_squared_weights() -> None:
    weights = np.array([0.2, 0.2, 0.2, 0.2, 0.2], dtype=float)
    assert abs(kish_effective_institutes(weights) - 5.0) < 1e-12


def test_production_cannot_reach_calibration_history() -> None:
    production = load_sources(ROOT / "config" / "sources.yaml")
    for source in production:
        assert source.usage != "poll_calibration_history"
        assert source.usage != "poll_calibration_audit"
        assert source.usage != "poll_calibration_sample_size"
        if source.raw_file:
            assert not str(source.raw_file).startswith("data/raw/polls/history/")
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(ROOT, contract)
    for poll in polls:
        assert "polls/history" not in poll.raw_file
    parsed = parser_raw_files(ROOT)
    for relative in parsed.values():
        assert "polls/history" not in relative
    lock = build_input_lock(ROOT)
    for poll in lock["polls"]:
        assert isinstance(poll, dict)
        assert "polls/history" not in str(poll["raw_file"])
    calibration = load_sources(ROOT / "config" / "sources_calibration.yaml")
    calibration_files = {
        str(source.raw_file) for source in calibration if source.raw_file
    }
    assert calibration_files
    assert calibration_files.isdisjoint(set(parsed.values()))


def test_parse_fieldwork_wraps_december_into_previous_year() -> None:
    election = date(2014, 9, 14)
    parsed = parse_fieldwork("9 Dec–7 Jan", election, default_year=2014)
    assert parsed == (date(2013, 12, 9), date(2014, 1, 7))
    same_year = parse_fieldwork("25 Aug–6 Sep", election, default_year=2014)
    assert same_year == (date(2014, 8, 25), date(2014, 9, 6))
    older_block = parse_fieldwork("3–13 Sep", election, default_year=2012)
    assert older_block == (date(2012, 9, 3), date(2012, 9, 13))


def test_2014_year_dividers_mark_the_block_above() -> None:
    path = ROOT / "data/raw/polls/history/wikipedia_en_polling_2014.html"
    if not path.exists():
        pytest.skip("Pinned historical poll files are absent")
    table = parse_tables(path.read_text(encoding="utf-8", errors="replace"))[0]
    years = assign_wikipedia_block_years(table, election_year=2014)
    assert years[10] == 2014
    assert years[87] == 2014
    assert years[88] is None
    assert years[89] == 2013
    assert years[163] == 2013
    assert years[164] is None
    assert years[165] == 2012
    assert years[244] is None
    assert years[245] == 2011
    assert years[325] is None
    assert years[326] == 2010


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_2014_sifo_last_poll_is_the_real_final() -> None:
    lasts = last_polls(build_poll_corpus(ROOT))
    sifo = next(
        row
        for row in lasts
        if int(row["election_cycle"]) == 2014 and row["institute_family"] == "Sifo/Verian"
    )
    assert sifo["fieldwork_end"] == "2014-09-11"
    assert abs(float(sifo["shares"]["S"]) - 0.310) < 0.002


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_archive_snapshot_is_not_before_fieldwork_end() -> None:
    rows = build_poll_corpus(ROOT)
    assert impossible_archive_citations(rows) == []


@pytest.mark.skipif(not HISTORY.exists(), reason="Pinned historical poll files are absent")
def test_window_poll_counts_stay_below_campaign_ceiling() -> None:
    rows = build_poll_corpus(ROOT)
    counts = Counter(int(row["election_cycle"]) for row in rows)
    assert counts
    assert MAX_WINDOW_POLLS_PER_CYCLE == 80
    for cycle, n_rows in sorted(counts.items()):
        assert n_rows <= MAX_WINDOW_POLLS_PER_CYCLE, (
            f"{cycle} has {n_rows} polls in the 30-day window; "
            f"the cap is {MAX_WINDOW_POLLS_PER_CYCLE}"
        )


def test_calibration_lock_records_method_choices() -> None:
    path = ROOT / "reports" / "poll_calibration" / "estimator_lock.json"
    if not path.exists():
        pytest.skip("Calibration lock has not been written yet")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["not_production_input"] is True
    assert document["production_aggregator_still_forbids_secondary_shares"] is True
    assert document["method"]["house_prior_strength"] == 3.0
    assert document["gate"]["verdict"] in {"SUPPORTED", "NOT_SUPPORTED"}
    assert document["intervals_2026_overlay"]["point_unchanged"] is True
