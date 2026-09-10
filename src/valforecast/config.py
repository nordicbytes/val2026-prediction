from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

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
class SourceVintage:
    value: str
    reference_date: str
    available_date: str


@dataclass(frozen=True)
class PollSourceMetadata:
    election_cycle: int
    pollster: str
    publication_date: str
    fieldwork_start: str
    fieldwork_end: str
    information_level: str
    population: str
    sample_size: int | None
    sample_size_basis: str
    effective_n: float | None
    survey_weight_method: str
    question_wording: str
    extraction_method: str
    page_or_table: str
    archive_status: str


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
    http_method: str = "GET"
    request_body: dict[str, object] | None = None
    usage: str | None = None
    vintages: tuple[SourceVintage, ...] = ()
    poll: PollSourceMetadata | None = None


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
        vintages_raw = row.get("vintages", [])
        if not isinstance(vintages_raw, list):
            raise ValueError(f"Source vintages must be a list: {row['id']}")
        vintages: list[SourceVintage] = []
        for vintage in vintages_raw:
            if not isinstance(vintage, dict):
                raise ValueError(f"Invalid source vintage: {row['id']}")
            required_vintage = {"value", "reference_date", "available_date"}
            if not required_vintage.issubset(vintage):
                raise ValueError(f"Incomplete source vintage: {row['id']}")
            reference_date = str(vintage["reference_date"])
            available_date = str(vintage["available_date"])
            if date.fromisoformat(available_date) < date.fromisoformat(reference_date):
                raise ValueError(f"Source available before its reference date: {row['id']}")
            vintages.append(
                SourceVintage(
                    value=str(vintage["value"]),
                    reference_date=reference_date,
                    available_date=available_date,
                )
            )
        poll_raw = row.get("poll")
        poll: PollSourceMetadata | None = None
        if poll_raw is not None:
            if not isinstance(poll_raw, dict):
                raise ValueError(f"Poll metadata must be a mapping: {row['id']}")
            required_poll = {
                "election_cycle",
                "pollster",
                "publication_date",
                "fieldwork_start",
                "fieldwork_end",
                "information_level",
                "population",
                "sample_size",
                "sample_size_basis",
                "effective_n",
                "survey_weight_method",
                "question_wording",
                "extraction_method",
                "page_or_table",
                "archive_status",
            }
            missing_poll = required_poll - poll_raw.keys()
            if missing_poll:
                raise ValueError(
                    f"Incomplete poll metadata for {row['id']}: {sorted(missing_poll)}"
                )
            fieldwork_start = date.fromisoformat(str(poll_raw["fieldwork_start"]))
            fieldwork_end = date.fromisoformat(str(poll_raw["fieldwork_end"]))
            publication_date = date.fromisoformat(str(poll_raw["publication_date"]))
            if fieldwork_start > fieldwork_end or fieldwork_end > publication_date:
                raise ValueError(f"Invalid poll chronology: {row['id']}")
            information_level = str(poll_raw["information_level"])
            allowed_levels = {
                "MICRODATA",
                "FULL_TRANSITION_TABLE",
                "PARTIAL_TRANSITION_TABLE",
                "MARGINAL_ONLY",
            }
            if information_level not in allowed_levels:
                raise ValueError(f"Invalid poll information level: {row['id']}")
            poll = PollSourceMetadata(
                election_cycle=int(poll_raw["election_cycle"]),
                pollster=str(poll_raw["pollster"]),
                publication_date=publication_date.isoformat(),
                fieldwork_start=fieldwork_start.isoformat(),
                fieldwork_end=fieldwork_end.isoformat(),
                information_level=information_level,
                population=str(poll_raw["population"]),
                sample_size=(
                    int(poll_raw["sample_size"])
                    if poll_raw["sample_size"] is not None
                    else None
                ),
                sample_size_basis=str(poll_raw["sample_size_basis"]),
                effective_n=(
                    float(poll_raw["effective_n"])
                    if poll_raw["effective_n"] is not None
                    else None
                ),
                survey_weight_method=str(poll_raw["survey_weight_method"]),
                question_wording=str(poll_raw["question_wording"]),
                extraction_method=str(poll_raw["extraction_method"]),
                page_or_table=str(poll_raw["page_or_table"]),
                archive_status=str(poll_raw["archive_status"]),
            )
        source = Source(
            id=str(row["id"]),
            source=str(row["source"]),
            provider=str(row["provider"]),
            dataset=str(row["dataset"]),
            url=str(row["url"]),
            retrieved_at=(str(row["retrieved_at"]) if row["retrieved_at"] is not None else None),
            reference_period=str(row["reference_period"]),
            geography_version=str(row["geography_version"]),
            license=str(row["license"]),
            raw_file=str(row["raw_file"]) if row["raw_file"] is not None else None,
            sha256=str(row["sha256"]) if row["sha256"] is not None else None,
            notes=str(row["notes"]),
            status=str(row["status"]) if row.get("status") is not None else None,
            http_method=str(row.get("http_method", "GET")).upper(),
            request_body=(
                cast(dict[str, object], row["request_body"])
                if isinstance(row.get("request_body"), dict)
                else None
            ),
            usage=str(row["usage"]) if row.get("usage") is not None else None,
            vintages=tuple(vintages),
            poll=poll,
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
