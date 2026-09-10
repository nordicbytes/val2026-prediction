from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SOURCE_FIELDS = frozenset(
    {
        "id",
        "source",
        "provider",
        "dataset",
        "url",
        "retrieved_at",
        "reference_period",
        "geography_version",
        "license",
        "raw_file",
        "sha256",
        "notes",
    }
)


@dataclass(frozen=True)
class Source:
    id: str
    source: str
    provider: str
    dataset: str
    url: str
    retrieved_at: str | None
    reference_period: str
    geography_version: str
    license: str
    raw_file: str | None
    sha256: str | None
    notes: str
    status: str | None = None


def load_sources(path: Path) -> list[Source]:
    document: Any
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)

    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Source manifest must be a mapping with schema_version: 1")
    rows = document.get("sources")
    if not isinstance(rows, list):
        raise ValueError("Source manifest 'sources' must be a list")

    sources: list[Source] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Source at index {index} must be a mapping")
        missing = REQUIRED_SOURCE_FIELDS - row.keys()
        if missing:
            raise ValueError(f"Source at index {index} is missing: {sorted(missing)}")
        source = Source(
            id=str(row["id"]),
            source=str(row["source"]),
            provider=str(row["provider"]),
            dataset=str(row["dataset"]),
            url=str(row["url"]),
            retrieved_at=(
                str(row["retrieved_at"]) if row["retrieved_at"] is not None else None
            ),
            reference_period=str(row["reference_period"]),
            geography_version=str(row["geography_version"]),
            license=str(row["license"]),
            raw_file=str(row["raw_file"]) if row["raw_file"] is not None else None,
            sha256=str(row["sha256"]) if row["sha256"] is not None else None,
            notes=str(row["notes"]),
            status=str(row["status"]) if row.get("status") is not None else None,
        )
        if source.id in seen_ids:
            raise ValueError(f"Duplicate source id: {source.id}")
        if source.sha256 is not None and (
            len(source.sha256) != 64
            or any(character not in "0123456789abcdef" for character in source.sha256.lower())
        ):
            raise ValueError(f"Invalid SHA-256 for source: {source.id}")
        seen_ids.add(source.id)
        sources.append(source)
    return sources

