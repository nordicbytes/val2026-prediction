from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FROZEN_REPORT = ROOT / "reports" / "milestone_4a_transition_matrix.md"
EXPECTED_SHA256 = "cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3"


def test_milestone_four_a_report_remains_frozen() -> None:
    assert hashlib.sha256(FROZEN_REPORT.read_bytes()).hexdigest() == EXPECTED_SHA256
