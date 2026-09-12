from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OVERRIDE_PATH = Path("config/poll_sample_sizes.yaml")
REQUIRED_FIELDS = (
    "poll_id",
    "sample_size",
    "source_url",
    "source_level",
    "note",
)
ALLOWED_LEVELS = frozenset({"A", "B", "C"})
OVERWRITE_MARKERS = ("overwrite", "skriver över")


def load_sample_size_overrides(root: Path) -> list[dict[str, Any]]:
    path = root / OVERRIDE_PATH
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Sample-size override file must have schema_version: 1")
    rows = document.get("overrides")
    if not isinstance(rows, list):
        raise ValueError("Sample-size override file must contain an overrides list")
    overrides: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Override at index {index} must be a mapping")
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"Override at index {index} is missing: {missing}")
        poll_id = str(row["poll_id"])
        if poll_id in seen:
            raise ValueError(f"Duplicate sample-size override: {poll_id}")
        level = str(row["source_level"])
        if level not in ALLOWED_LEVELS:
            raise ValueError(f"Invalid source_level for {poll_id}: {level}")
        sample_size = int(row["sample_size"])
        if sample_size < 200 or sample_size > 20000:
            raise ValueError(f"sample_size out of range for {poll_id}: {sample_size}")
        seen.add(poll_id)
        overrides.append(
            {
                "poll_id": poll_id,
                "sample_size": sample_size,
                "source_url": str(row["source_url"]),
                "source_level": level,
                "note": str(row["note"]),
            }
        )
    return overrides


def apply_sample_size_overrides(
    rows: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(row["poll_id"]): row for row in rows}
    for override in overrides:
        poll_id = override["poll_id"]
        row = by_id.get(poll_id)
        if row is None:
            raise ValueError(f"Sample-size override has no matching poll_id: {poll_id}")
        parsed = row.get("sample_size")
        new_value = override["sample_size"]
        if parsed is not None and int(parsed) != int(new_value):
            note = override["note"].lower()
            if not any(marker in note for marker in OVERWRITE_MARKERS):
                raise ValueError(
                    f"Override for {poll_id} would replace parsed n={parsed} "
                    f"with {new_value} without an explicit overwrite note"
                )
        row["sample_size"] = new_value
        row["sample_size_override"] = {
            "source_url": override["source_url"],
            "source_level": override["source_level"],
            "note": override["note"],
        }
    return rows
