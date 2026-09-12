from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from valforecast.calibration.elections import ELECTION_DATES
from valforecast.calibration.html_tables import parse_tables
from valforecast.calibration.names import (
    days_to_election,
    institute_family,
    map_headers,
    parse_fieldwork,
    parse_sample_size,
    shares_from_row,
    strip_citations,
)
from valforecast.ingest.fetch import sha256_file

WINDOW_DAYS = 30
SV_2010_TABLE_FAMILIES = (
    "Demoskop",
    "Novus",
    "SCB",
    "Sentio",
    "Sifo/Verian",
    "SKOP",
    "Ipsos",
    "United Minds",
)


def _source_meta(path: Path, url: str, retrieved_at: str) -> dict[str, str]:
    return {
        "url": url,
        "raw_file": str(path),
        "sha256": sha256_file(path),
        "retrieved_at": retrieved_at,
    }


def _row_record(
    *,
    poll_id: str,
    cycle: int,
    pollster: str,
    family: str,
    commissioner: str,
    publication: date | None,
    start: date,
    end: date,
    sample_size: int | None,
    shares: dict[str, float],
    source_level: str,
    meta: dict[str, str],
) -> dict[str, object]:
    election = ELECTION_DATES[cycle]
    return {
        "poll_id": poll_id,
        "election_cycle": cycle,
        "pollster": pollster,
        "institute_family": family,
        "commissioner": commissioner,
        "publication_date": publication.isoformat() if publication else None,
        "fieldwork_start": start.isoformat(),
        "fieldwork_end": end.isoformat(),
        "sample_size": sample_size,
        "shares": shares,
        "source_level": source_level,
        "days_to_election": days_to_election(end, election),
        **meta,
    }


def _within_window(end: date, election: date) -> bool:
    days = days_to_election(end, election)
    return 1 <= days <= WINDOW_DAYS


def parse_en_wikipedia(
    path: Path,
    *,
    cycle: int,
    url: str,
    retrieved_at: str,
) -> list[dict[str, object]]:
    tables = parse_tables(path.read_text(encoding="utf-8", errors="replace"))
    if not tables:
        raise ValueError(f"No wikitables in {path}")
    table = max(tables, key=len)
    header_index = next(
        (
            index
            for index, row in enumerate(table[:4])
            if map_headers(row)
            and any(token in " ".join(row).lower() for token in ("polling", "firm", "date"))
        ),
        0,
    )
    header_map = map_headers(table[header_index])
    headers = [cell.lower() for cell in table[header_index]]
    firm_idx = next((i for i, h in enumerate(headers) if "firm" in h or "polling" in h), 1)
    date_idx = next((i for i, h in enumerate(headers) if "date" in h or "field" in h), 0)
    n_idx = next((i for i, h in enumerate(headers) if "sample" in h), None)
    meta = _source_meta(path, url, retrieved_at)
    election = ELECTION_DATES[cycle]
    rows: list[dict[str, object]] = []
    seen = 0
    for row in table[header_index + 1 :]:
        if len(row) < 6:
            continue
        raw_firm = row[firm_idx] if firm_idx < len(row) else ""
        joined = " ".join(row)
        citations = [int(match) for match in re.findall(r"\[(\d+)\]", joined)]
        cell_urls = [
            match
            for match in re.findall(r"https?://[^\s]+", joined)
            if _usable_url(match)
        ]
        firm = re.sub(r"https?://\S+", "", strip_citations(raw_firm)).strip()
        family = institute_family(firm)
        if family is None:
            continue
        date_text = re.sub(
            r"https?://\S+",
            "",
            row[date_idx] if date_idx < len(row) else "",
        )
        fieldwork = parse_fieldwork(date_text, election)
        if fieldwork is None:
            continue
        start, end = fieldwork
        if not _within_window(end, election):
            continue
        shares = shares_from_row(row, header_map)
        if shares is None:
            continue
        sample = parse_sample_size(row[n_idx]) if n_idx is not None and n_idx < len(row) else None
        seen += 1
        rows.append(
            _row_record(
                poll_id=(
                    f"wiki_en_{cycle}_{family.lower().replace('/', '_')}_"
                    f"{end.isoformat()}_{seen}"
                ),
                cycle=cycle,
                pollster=firm or family,
                family=family,
                commissioner="",
                publication=None,
                start=start,
                end=end,
                sample_size=sample,
                shares=shares,
                source_level="C",
                meta=meta,
            )
        )
        rows[-1]["citations"] = citations
        rows[-1]["citation_urls"] = cell_urls
    return rows


def parse_sv_2010_wikipedia(
    path: Path,
    *,
    url: str,
    retrieved_at: str,
) -> list[dict[str, object]]:
    tables = parse_tables(path.read_text(encoding="utf-8", errors="replace"))
    meta = _source_meta(path, url, retrieved_at)
    election = ELECTION_DATES[2010]
    rows: list[dict[str, object]] = []
    seen = 0
    for table, family in zip(tables, SV_2010_TABLE_FAMILIES, strict=False):
        header_map = None
        for row in table:
            joined = " ".join(row).lower()
            if "valet" in joined or "valu" in joined:
                continue
            if header_map is None:
                mapped = map_headers(row)
                if len(mapped) >= 6:
                    header_map = {index + 1: party for index, party in mapped.items()}
                continue
            fieldwork = parse_fieldwork(row[0], election)
            if fieldwork is None:
                continue
            start, end = fieldwork
            if not _within_window(end, election):
                continue
            shares = shares_from_row(row, header_map)
            if shares is None:
                continue
            citations = [
                int(match) for match in re.findall(r"\[(\d+)\]", " ".join(row))
            ]
            seen += 1
            rows.append(
                _row_record(
                    poll_id=(
                        f"wiki_sv_2010_{family.lower().replace('/', '_')}_"
                        f"{end.isoformat()}_{seen}"
                    ),
                    cycle=2010,
                    pollster=family,
                    family=family,
                    commissioner="",
                    publication=None,
                    start=start,
                    end=end,
                    sample_size=None,
                    shares=shares,
                    source_level="C",
                    meta=meta,
                )
            )
            rows[-1]["citations"] = citations
    return rows


_SKIP_HOSTS = (
    "en.wikipedia.org",
    "sv.wikipedia.org",
    "wikipedia.org",
    "wikimedia.org",
    "wikidata.org",
    "mediawiki.org",
)


def _usable_url(url: str) -> bool:
    lowered = url.lower()
    return not any(host in lowered for host in _SKIP_HOSTS)


def _pick_reference_url(chunk: str) -> str | None:
    archive = re.search(r'"archive-url":\{"wt":"(https?://[^"]+)"\}', chunk)
    if archive and _usable_url(archive.group(1)):
        return archive.group(1)
    template = re.search(r'"url":\{"wt":"(https?://[^"]+)"\}', chunk)
    if template and _usable_url(template.group(1)) and "t.co/" not in template.group(1):
        return template.group(1)
    ranked: list[str] = []
    for found in re.findall(r'https?://[^"\\\s<>]+', chunk):
        cleaned = found.rstrip("\\.,);")
        if _usable_url(cleaned):
            ranked.append(cleaned)
    if not ranked:
        return None
    for preferred in ("web.archive.org", "svd.se", "dn.se", "svt.se", "novus.se", "skop.se"):
        for url in ranked:
            if preferred in url:
                return url
    return ranked[0]


def extract_wikipedia_references(html: str) -> dict[int, str]:
    refs: dict[int, str] = {}
    starts = list(re.finditer(r'id="cite_note-(\d+)"', html))
    for index, match in enumerate(starts):
        number = int(match.group(1))
        end = starts[index + 1].start() if index + 1 < len(starts) else match.start() + 8000
        url = _pick_reference_url(html[match.start() : end])
        if url:
            refs.setdefault(number, url)
    return refs
