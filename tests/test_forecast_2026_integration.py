from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from valforecast.config import load_sources
from valforecast.forecast.aggregator import aggregate_polls
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.kernel import load_2026_transition_cells, load_2026_vid10
from valforecast.forecast.lock import build_input_lock, load_input_lock, write_input_lock
from valforecast.forecast.polls import (
    derive_other_residual,
    extract_expressen_indikator_shares,
    extract_omni_party_shares,
    extract_placera_demoskop_final_named,
    load_forecast_polls,
)
from valforecast.forecast.produce import run_forecast_2026
from valforecast.forecast.snapshot import required_snapshot_fields
from valforecast.forecast.universe import (
    build_forecast_universe,
    national_valid_votes,
)
from valforecast.ingest.elections import read_val2022_district_results

ROOT = Path(__file__).resolve().parents[1]
RAW_POLL = ROOT / "data/raw/polls/verian_valjarbarometer_september_2026.pdf"
RAW_SCB = ROOT / "data/raw/scb/psu/transition_2026M05.json"
RAW_MAP = ROOT / "data/raw/valmyndigheten/2026/valdistrikt_jamforelser_2022_2026.xlsx"


@pytest.mark.skipif(not RAW_POLL.exists(), reason="Pinned 2026 poll files are absent")
def test_production_polls_respect_cutoff_and_overlap() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(ROOT, contract)
    aggregated = aggregate_polls(polls, contract, variant="production")
    assert {poll.poll_id for poll in aggregated.included} == {
        "verian_svt_2026_sep_final",
        "novus_tv4_2026_09_11",
        "dn_ipsos_2026_09_11",
        "indikator_ekot_2026_09_11",
        "demoskop_2026_09_11",
    }
    strict = aggregate_polls(polls, contract, variant="strict_hosted")
    assert [poll.poll_id for poll in strict.included] == [
        "verian_svt_2026_sep_final",
        "novus_tv4_2026_09_11",
        "demoskop_2026_sep_early",
    ]
    excluded_ids = {poll.poll_id: poll.exclusion_reason for poll in aggregated.excluded}
    assert excluded_ids["novus_tv4_2026_09_09"] == "overlapping_same_pollster"
    assert excluded_ids["verian_svt_2026_sep_monthly"] == "pollster_balance_not_latest"
    assert excluded_ids["novus_monthly_2026_sep"] == "pollster_balance_not_latest"
    assert excluded_ids["demoskop_2026_sep_early"] == "pollster_balance_not_latest"
    assert excluded_ids["indikator_ekot_2026_09_11_omni"] == "secondary_source"
    by_id = {poll.poll_id: poll for poll in aggregated.included}
    assert by_id["dn_ipsos_2026_09_11"].other_origin == "derived_residual"
    assert by_id["demoskop_2026_09_11"].other_origin == "derived_residual"
    assert by_id["indikator_ekot_2026_09_11"].other_origin == "published"
    for poll in aggregated.included:
        assert poll.publication_date <= contract.cutoff.date()
        assert poll.fieldwork_end >= contract.earliest_fieldwork_end
        assert abs(sum(poll.shares.values()) - 1.0) < 1e-9
        assert poll.verification_tier in {"a", "b"}


@pytest.mark.skipif(not RAW_POLL.exists(), reason="Pinned 2026 poll files are absent")
def test_media_table_gold_cells_come_from_pinned_html() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    ipsos = extract_omni_party_shares(
        ROOT / "data/raw/polls/omni_dn_ipsos_11-september-2026.html",
        short_labels=False,
    )
    assert abs(ipsos["S"] - 0.274) < 1e-9
    assert abs(ipsos["L"] - 0.050) < 1e-9
    assert "OTHER" not in ipsos
    derived = derive_other_residual(
        ipsos,
        named_sum_min=contract.derived_other_named_sum_min,
        named_sum_max=contract.derived_other_named_sum_max,
    )
    assert abs(derived["OTHER"] - 0.016) < 1e-9
    indikator = extract_expressen_indikator_shares(
        ROOT / "data/raw/polls/expressen_indikator_11-september-2026.html"
    )
    assert abs(indikator["S"] - 0.286) < 1e-9
    assert abs(indikator["OTHER"] - 0.016) < 1e-9
    demoskop = extract_placera_demoskop_final_named(
        ROOT / "data/raw/polls/placera_demoskop_11-september-2026.html"
    )
    assert abs(demoskop["S"] - 0.297) < 1e-9
    assert abs(demoskop["L"] - 0.047) < 1e-9
    assert "OTHER" not in demoskop


@pytest.mark.skipif(not RAW_MAP.exists(), reason="Official 2026 mapping is absent")
def test_national_point_equals_locked_poll_target() -> None:
    result = run_forecast_2026(ROOT)
    assert np.allclose(result.national_point, result.aggregated.point, atol=1e-9)
    assert np.allclose(result.national_point.sum(), 1.0)
    assert np.allclose(result.district_point.sum(axis=1), 1.0)
    weight_share = result.universe.eligible_weights / result.universe.eligible_weights.sum()
    assert np.allclose(weight_share @ result.district_point, result.aggregated.point, atol=1e-9)
    assert np.allclose(
        result.sensitivity_national_point,
        result.sensitivity_aggregated.point,
        atol=1e-9,
    )
    low = np.quantile(result.national_draws, 0.025, axis=0)
    high = np.quantile(result.national_draws, 0.975, axis=0)
    assert bool(np.all(low <= high))


@pytest.mark.skipif(not RAW_SCB.exists(), reason="Pinned SCB 2026 files are absent")
def test_scb_2026_vintages_and_checksums() -> None:
    sources = {source.id: source for source in load_sources(ROOT / "config" / "sources.yaml")}
    transition = (ROOT / "data/raw/scb/psu/transition_2026M05.json").read_bytes()
    vid10 = (ROOT / "data/raw/scb/psu/national_poll_2026M05.json").read_bytes()
    assert hashlib.sha256(transition).hexdigest() == sources["scb_psu_transition_2026M05"].sha256
    assert hashlib.sha256(vid10).hexdigest() == sources["scb_psu_national_2026M05"].sha256
    cells = load_2026_transition_cells(ROOT)
    vector = load_2026_vid10(ROOT)
    assert cells.height == 99
    assert np.isclose(vector.sum(), 1.0)


@pytest.mark.skipif(not RAW_MAP.exists(), reason="Official 2026 mapping is absent")
def test_official_mapping_covers_all_2026_districts() -> None:
    universe = build_forecast_universe(ROOT)
    assert universe.districts.height == 6312
    assert universe.previous_matrix.shape == (6312, 9)
    assert np.allclose(universe.previous_matrix.sum(axis=1), 1.0)
    statuses = dict(
        universe.districts.group_by("official_status").len().iter_rows()
    )
    relations = dict(universe.districts.group_by("relation").len().iter_rows())
    assert sum(statuses.values()) == 6312
    assert statuses["Kan jämföras"] == 5024
    assert statuses["Kan jämföras mot flera"] == 35
    assert statuses["Ej jämförbart"] == 1253
    assert relations == {
        "SAME": 4937,
        "COMPARABLE": 87,
        "MERGED": 35,
        "UNMATCHED": 1253,
    }
    assert bool((universe.eligible_weights > 0).all())
    unmatched_rules = set(
        universe.districts.filter(pl.col("relation") == "UNMATCHED")["mapping_rule"].unique()
    )
    assert unmatched_rules <= {"municipality_2022_fallback", "national_2022_fallback"}
    assert 6_000_000 < universe.national_valid_votes < 7_000_000


@pytest.mark.skipif(not RAW_POLL.exists(), reason="Pinned 2026 poll files are absent")
def test_input_lock_is_deterministic() -> None:
    first = build_input_lock(ROOT)
    second = build_input_lock(ROOT)
    assert first["included_poll_ids"] == second["included_poll_ids"]
    assert first["strict_sensitivity_poll_ids"] == [
        "verian_svt_2026_sep_final",
        "novus_tv4_2026_09_11",
        "demoskop_2026_sep_early",
    ]
    assert first["raw_sha256"] == second["raw_sha256"]
    forbidden = first["forbidden_inputs_confirmed_absent"]
    assert isinstance(forbidden, list)
    assert "election_results_2026" in forbidden


def test_written_snapshot_has_required_metadata() -> None:
    snapshots = sorted((ROOT / "forecast_snapshots").glob("forecast_2026_*.json"))
    if not snapshots:
        pytest.skip("No 2026 snapshot has been written yet")
    document = json.loads(snapshots[-1].read_text(encoding="utf-8"))
    assert required_snapshot_fields() <= set(document)
    assert document["data_cutoff"].startswith("2026-09-11T22:29:00")
    assert document["seats"] is None
    assert document["random_seed"] == 20260911


@pytest.mark.skipif(not RAW_MAP.exists(), reason="Official 2026 mapping is absent")
def test_national_valid_votes_are_not_multiplied_by_party_rows() -> None:
    results = read_val2022_district_results(
        ROOT / "data/raw/valmyndigheten/2022/roster_per_distrikt_slutligt_riksdag.xlsx"
    )
    naive = int(results.select(pl.col("valid_votes").sum()).item())
    unique = national_valid_votes(results)
    assert naive > 5 * unique
    assert 6_000_000 < unique < 7_000_000
    universe = build_forecast_universe(ROOT)
    assert universe.national_valid_votes == unique


@pytest.mark.skipif(not RAW_POLL.exists(), reason="Pinned 2026 poll files are absent")
def test_input_lock_detects_poll_raw_byte_change(tmp_path: Path) -> None:
    lock_path = write_input_lock(ROOT)
    assert lock_path.exists()
    current = build_input_lock(ROOT)
    raw_hashes = current["raw_sha256"]
    assert isinstance(raw_hashes, dict)
    assert any(str(key).startswith("poll:") for key in raw_hashes)
    assert "poll_sha256" in current
    poll_file = ROOT / "data/raw/polls/verian_valjarbarometer_september_2026.pdf"
    original = poll_file.read_bytes()
    try:
        poll_file.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="stale"):
            load_input_lock(ROOT)
    finally:
        poll_file.write_bytes(original)
    restored = load_input_lock(ROOT)
    assert restored["poll_sha256"] == current["poll_sha256"]


def test_no_2026_result_or_valu_sources() -> None:
    text = (ROOT / "config" / "sources.yaml").read_text(encoding="utf-8").lower()
    assert "val2026_riksdag_results" not in text
    assert "exit poll" not in text
    assert "\nvalu\n" not in text and "valu_" not in text
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    assert "election_results_2026" in contract.raw["forbidden_inputs"]
    assert "valu" in contract.raw["forbidden_inputs"]
