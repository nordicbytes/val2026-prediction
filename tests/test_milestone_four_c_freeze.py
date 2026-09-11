from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FOUR_A = ROOT / "reports" / "milestone_4a_transition_matrix.md"
FROZEN_FOUR_B = ROOT / "reports" / "milestone_4b_transition_posterior.md"
FROZEN_FOUR_B_LOCK = ROOT / "reports" / "milestone_four_b" / "survey_estimator_lock.json"
EXPECTED_FOUR_A = "cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3"
EXPECTED_FOUR_B = "16da240fe08876646fd97a60c53f5b01ac7d87e042b3b492051324a17533e7f7"
EXPECTED_FOUR_B_LOCK = "2820459aeff35a3a1c40c969ecd9c3797efabce466a39a44fbbfc3dcdf4265d5"


def test_milestone_four_a_report_remains_frozen() -> None:
    assert hashlib.sha256(FROZEN_FOUR_A.read_bytes()).hexdigest() == EXPECTED_FOUR_A


def test_milestone_four_b_report_remains_frozen() -> None:
    assert hashlib.sha256(FROZEN_FOUR_B.read_bytes()).hexdigest() == EXPECTED_FOUR_B


def test_milestone_four_b_survey_lock_remains_frozen() -> None:
    assert hashlib.sha256(FROZEN_FOUR_B_LOCK.read_bytes()).hexdigest() == EXPECTED_FOUR_B_LOCK
