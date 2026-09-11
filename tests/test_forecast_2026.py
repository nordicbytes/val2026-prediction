from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.forecast.aggregator import (
    aggregate_polls,
    collapse_overlapping_polls,
    draw_poll_targets,
    intervals_overlap,
    keep_latest_per_pollster,
    select_included_polls,
)
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.polls import (
    NationalPoll,
    derive_other_residual,
    normalize_party_shares,
    published_before_cutoff,
)
from valforecast.forecast.produce import apply_and_reconcile
from valforecast.forecast.snapshot import (
    official_snapshot_path,
    required_snapshot_fields,
    write_snapshot,
)
from valforecast.forecast.universe import missing_merged_predecessors
from valforecast.polls.transition_calibrate import calibrate_transition_matrix

ROOT = Path(__file__).resolve().parents[1]
STOCKHOLM = ZoneInfo("Europe/Stockholm")


def _shares(s: float = 0.30) -> dict[str, float]:
    rest = (1.0 - s) / 8
    values = {
        "V": rest,
        "S": s,
        "MP": rest,
        "C": rest,
        "L": rest,
        "M": rest,
        "KD": rest,
        "SD": rest,
        "OTHER": rest,
    }
    return normalize_party_shares(values, tolerance=0.005)


def _poll(
    poll_id: str,
    *,
    pollster: str = "Test",
    publication: date = date(2026, 9, 10),
    published_at: datetime | None = None,
    start: date = date(2026, 9, 1),
    end: date = date(2026, 9, 8),
    n: int = 1000,
    included: bool = True,
    reason: str | None = None,
    s: float = 0.30,
) -> NationalPoll:
    return NationalPoll(
        poll_id=poll_id,
        pollster=pollster,
        commissioner="Test",
        publication_date=publication,
        publication_datetime=published_at,
        fieldwork_start=start,
        fieldwork_end=end,
        sample_size=n,
        sample_size_basis="RESPONDENTS",
        method="test",
        shares=_shares(s),
        url="https://example.com",
        retrieved_at="2026-09-11T20:35:00+00:00",
        raw_file="data/raw/polls/test.html",
        sha256="0" * 64,
        included=included,
        exclusion_reason=reason,
        source_class="static_primary_document",
        other_origin="published",
        verification_tier="a",
    )


def test_contract_locks_4b_and_forbids_4c() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    assert contract.primary_model == "T1_no_point_shrinkage_raked"
    assert contract.draws >= 2000
    assert contract.raw["model"]["regional_4c"] == "forbidden"
    assert contract.raw["model"]["milestone_3_local_structure"] == "forbidden"
    assert contract.cutoff.isoformat() == "2026-09-11T22:29:00+02:00"


def test_cutoff_rejects_post_cutoff_datetime() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    late = _poll(
        "late",
        published_at=datetime(2026, 9, 11, 22, 30, tzinfo=STOCKHOLM),
    )
    allowed, reason = published_before_cutoff(late, contract)
    assert allowed is False
    assert reason == "published_after_cutoff"


def test_cutoff_accepts_same_day_before_deadline() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    ok = _poll(
        "ok",
        publication=date(2026, 9, 11),
        published_at=datetime(2026, 9, 11, 14, 59, tzinfo=STOCKHOLM),
    )
    allowed, reason = published_before_cutoff(ok, contract)
    assert allowed is True
    assert reason is None


def test_party_schema_other_and_residual() -> None:
    shares = normalize_party_shares(
        {
            "V": 0.08,
            "S": 0.28,
            "MP": 0.07,
            "C": 0.08,
            "L": 0.05,
            "M": 0.16,
            "KD": 0.06,
            "SD": 0.19,
            "OTHER": 0.028,
        },
        tolerance=0.005,
    )
    assert set(shares) == set(PARTIES)
    assert abs(sum(shares.values()) - 1.0) < 1e-12
    with pytest.raises(ValueError, match="Missing named"):
        normalize_party_shares({"S": 0.3}, tolerance=0.005)


def test_derived_residual_other_requires_eight_named() -> None:
    named = {
        "V": 0.076,
        "S": 0.274,
        "MP": 0.068,
        "C": 0.076,
        "L": 0.050,
        "M": 0.183,
        "KD": 0.069,
        "SD": 0.188,
    }
    derived = derive_other_residual(named, named_sum_min=0.97, named_sum_max=1.00)
    assert abs(derived["OTHER"] - 0.016) < 1e-12
    assert abs(sum(derived.values()) - 1.0) < 1e-12
    with pytest.raises(ValueError, match="outside the DERIVED_RESIDUAL"):
        derive_other_residual({**named, "S": 0.20}, named_sum_min=0.97, named_sum_max=1.00)
    with pytest.raises(ValueError, match="already published"):
        derive_other_residual({**named, "OTHER": 0.016}, named_sum_min=0.97, named_sum_max=1.00)


def test_overlap_keeps_latest_same_pollster() -> None:
    older = _poll("old", pollster="Novus", start=date(2026, 9, 6), end=date(2026, 9, 8))
    newer = _poll("new", pollster="Novus", start=date(2026, 9, 7), end=date(2026, 9, 10))
    other = _poll("verian", pollster="Verian", start=date(2026, 9, 4), end=date(2026, 9, 10))
    kept = collapse_overlapping_polls([older, newer, other])
    assert {poll.poll_id for poll in kept} == {"new", "verian"}
    assert intervals_overlap(
        date(2026, 9, 6), date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 10)
    )


def test_aggregator_is_deterministic() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    polls = [
        _poll("a", pollster="A", s=0.31, n=2000, end=date(2026, 9, 10)),
        _poll("b", pollster="B", s=0.27, n=3000, end=date(2026, 9, 8)),
    ]
    first = aggregate_polls(polls, contract)
    second = aggregate_polls(polls, contract)
    assert np.allclose(first.point, second.point)
    draws_a = draw_poll_targets(first, n_draws=32, seed=20260911)
    draws_b = draw_poll_targets(second, n_draws=32, seed=20260911)
    assert np.allclose(draws_a, draws_b)
    assert np.allclose(draws_a.sum(axis=1), 1.0)
    no_bootstrap = draw_poll_targets(first, n_draws=32, seed=20260911, bootstrap=False)
    assert not np.allclose(draws_a, no_bootstrap)
    other_seed = draw_poll_targets(first, n_draws=32, seed=20260912)
    assert not np.allclose(draws_a, other_seed)


def test_district_reconciliation_hits_national_target() -> None:
    previous = np.array(
        [
            [0.4, 0.2, 0.05, 0.05, 0.05, 0.1, 0.05, 0.08, 0.02],
            [0.1, 0.4, 0.05, 0.05, 0.05, 0.2, 0.05, 0.08, 0.02],
        ],
        dtype=float,
    )
    previous = previous / previous.sum(axis=1, keepdims=True)
    identity = np.repeat(np.eye(len(PARTIES))[None, :, :], 5, axis=0)
    target = np.array([0.08, 0.29, 0.07, 0.08, 0.05, 0.16, 0.06, 0.19, 0.02])
    target = target / target.sum()
    targets = np.repeat(target[None, :], 5, axis=0)
    weights = np.array([1000.0, 2000.0])
    reconciled = apply_and_reconcile(previous, weights, identity, targets)
    national = (weights / weights.sum()) @ reconciled
    assert np.allclose(national, targets, atol=1e-9)
    assert np.allclose(reconciled.sum(axis=2), 1.0)


def test_transition_draw_rake_matches_target() -> None:
    matrix = np.full((len(PARTIES), len(PARTIES)), 1.0 / len(PARTIES))
    previous = np.array([0.08, 0.30, 0.05, 0.07, 0.05, 0.19, 0.05, 0.20, 0.01])
    previous = previous / previous.sum()
    target = np.array([0.07, 0.28, 0.07, 0.08, 0.05, 0.16, 0.07, 0.19, 0.03])
    target = target / target.sum()
    result = calibrate_transition_matrix(matrix, previous, target)
    assert result.converged
    assert np.allclose(previous @ result.matrix, target, atol=1e-10)


def test_snapshot_requires_immutable_metadata() -> None:
    required = required_snapshot_fields()
    assert {
        "forecast_timestamp",
        "data_cutoff",
        "git_commit",
        "source_manifest_hash",
        "model_version",
        "random_seed",
        "prediction",
    } <= required


def test_snapshot_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = {
        "forecast_timestamp": "2026-09-11T20:00:00+00:00",
        "data_cutoff": "2026-09-11T22:29:00+02:00",
        "git_commit": "abc",
        "source_manifest_hash": "0" * 64,
        "model_version": "test",
        "random_seed": 1,
        "prediction": {},
    }
    first = write_snapshot(tmp_path, document)
    assert first.exists()
    with pytest.raises(ValueError, match="overwrite"):
        write_snapshot(tmp_path, document)


def test_forbidden_markers_are_in_contract() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    forbidden = set(contract.raw["forbidden_inputs"])
    assert "election_results_2026" in forbidden
    assert "valu" in forbidden
    assert "regional_4c_conditioning" in forbidden
    assert contract.pollster_balance == "latest_fieldwork_end_per_pollster"
    assert contract.pollster_bootstrap is True
    assert contract.strict_hosted_poll_ids == (
        "verian_svt_2026_sep_final",
        "novus_tv4_2026_09_11",
        "demoskop_2026_sep_early",
    )
    assert contract.raw["poll_aggregation"]["inclusion"]["verification_ladder"][
        "b_established_media_table_crosschecked"
    ] == "include"


def test_pollster_balance_keeps_one_poll_per_institute() -> None:
    contract = load_forecast_contract(ROOT / "config" / "forecast_2026.yaml")
    older = _poll(
        "verian_old", pollster="Verian", start=date(2026, 8, 20), end=date(2026, 9, 2), n=3000
    )
    newer = _poll(
        "verian_new", pollster="Verian", start=date(2026, 9, 4), end=date(2026, 9, 10), n=3000
    )
    other = _poll("novus", pollster="Novus", start=date(2026, 9, 7), end=date(2026, 9, 10), n=1000)
    kept = keep_latest_per_pollster([older, newer, other])
    assert {poll.poll_id for poll in kept} == {"verian_new", "novus"}
    included, excluded = select_included_polls([older, newer, other], contract)
    assert {poll.poll_id for poll in included} == {"verian_new", "novus"}
    reasons = {poll.poll_id: poll.exclusion_reason for poll in excluded}
    assert reasons["verian_old"] == "pollster_balance_not_latest"
    aggregated = aggregate_polls([older, newer, other], contract)
    assert len(aggregated.included) == 2
    assert abs(aggregated.weights.sum() - 1.0) < 1e-12


def test_official_snapshot_is_immutable(tmp_path: Path) -> None:
    document = {
        "forecast_timestamp": "2026-09-11T20:00:00+00:00",
        "data_cutoff": "2026-09-11T22:29:00+02:00",
        "git_commit": "abc",
        "source_manifest_hash": "0" * 64,
        "model_version": "test",
        "random_seed": 1,
        "prediction": {},
    }
    first = write_snapshot(tmp_path, document, official=True)
    assert first == official_snapshot_path(tmp_path)
    assert first.exists()
    with pytest.raises(ValueError, match="Official 2026 snapshot already exists"):
        write_snapshot(tmp_path, document, official=True)
    later = {
        **document,
        "forecast_timestamp": "2026-09-11T21:00:00+00:00",
    }
    with pytest.raises(ValueError, match="Official 2026 snapshot already exists"):
        write_snapshot(tmp_path, later)


def test_merged_requires_all_listed_predecessors() -> None:
    available = {"0114K001", "0114K002"}
    assert missing_merged_predecessors(["0114K001", "0114K002"], available) == []
    assert missing_merged_predecessors(["0114K001", "0114K099"], available) == ["0114K099"]
    assert missing_merged_predecessors([], available) == ["<none listed>"]
