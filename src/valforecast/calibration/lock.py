from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from valforecast.ingest.fetch import sha256_file

LOCK_PATH = Path("reports/poll_calibration/estimator_lock.json")


def write_calibration_lock(root: Path, document: dict[str, Any]) -> Path:
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def file_checksums(root: Path, relatives: list[str]) -> dict[str, str]:
    return {relative: sha256_file(root / relative) for relative in relatives}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
