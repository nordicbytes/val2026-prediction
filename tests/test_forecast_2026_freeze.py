from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "forecast_2026.yaml"
SOURCES = ROOT / "config" / "sources.yaml"
OFFICIAL = ROOT / "forecast_snapshots" / "official_forecast_2026.json"
FOUR_B = ROOT / "reports" / "milestone_4b_transition_posterior.md"
FOUR_B_LOCK = ROOT / "reports" / "milestone_four_b" / "survey_estimator_lock.json"
FOUR_A = ROOT / "reports" / "milestone_4a_transition_matrix.md"

EXPECTED_FOUR_A = "cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3"
EXPECTED_FOUR_B = "16da240fe08876646fd97a60c53f5b01ac7d87e042b3b492051324a17533e7f7"
EXPECTED_FOUR_B_LOCK = "2820459aeff35a3a1c40c969ecd9c3797efabce466a39a44fbbfc3dcdf4265d5"
EXPECTED_CONTRACT = "11b248e06ff0eb7ecd92ad2ce7c9458e5cf483d17bc0173ed48915151c1360a4"
EXPECTED_SOURCES = "2362f165518a4bb730679d403efd7a1f16b48b384503f3747a95baeb7d449005"


def test_forecast_contract_is_hashed() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == EXPECTED_CONTRACT


def test_official_snapshot_can_be_tracked() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "forecast_snapshots/*.json" in text
    assert "!forecast_snapshots/official_forecast_2026.json" in text


def test_frozen_milestones_were_not_rewritten() -> None:
    assert hashlib.sha256(FOUR_A.read_bytes()).hexdigest() == EXPECTED_FOUR_A
    assert hashlib.sha256(FOUR_B.read_bytes()).hexdigest() == EXPECTED_FOUR_B
    assert hashlib.sha256(FOUR_B_LOCK.read_bytes()).hexdigest() == EXPECTED_FOUR_B_LOCK


def test_production_source_manifest_matches_official_snapshot() -> None:
    digest = hashlib.sha256(SOURCES.read_bytes()).hexdigest()
    assert digest == EXPECTED_SOURCES
    document = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    assert document["source_manifest_hash"] == digest
