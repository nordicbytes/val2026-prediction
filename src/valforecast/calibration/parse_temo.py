from __future__ import annotations

import re
from pathlib import Path

from valforecast.calibration.elections import ELECTION_DATES
from valforecast.calibration.html_tables import parse_all_tables
from valforecast.calibration.names import (
    days_to_election,
    institute_family,
    map_headers,
    parse_fieldwork,
    parse_sample_size,
    shares_from_row,
    strip_citations,
)
from valforecast.calibration.parse_wikipedia import WINDOW_DAYS, _row_record, _source_meta


def parse_temo_archive(
    path: Path,
    *,
    cycle: int,
    url: str,
    retrieved_at: str,
) -> list[dict[str, object]]:
    tables = parse_all_tables(path.read_text(encoding="utf-8", errors="replace"))
    if not tables:
        raise ValueError(f"No tables in {path}")
    table = max(tables, key=len)
    header_index = next(
        (
            index
            for index, row in enumerate(table[:8])
            if map_headers(row) and any(cell.lower() in {"m", "s", "fp"} for cell in row)
        ),
        3,
    )
    header_map = map_headers(table[header_index])
    headers = [cell.lower() for cell in table[header_index]]
    pub_idx = next((i for i, h in enumerate(headers) if "public" in h), None)
    field_idx = next(
        (i for i, h in enumerate(headers) if "intervju" in h or "period" in h),
        None,
    )
    n_idx = next((i for i, h in enumerate(headers) if "antal" in h or "intervju" in h), None)
    comm_idx = next((i for i, h in enumerate(headers) if "uppdrag" in h), None)
    meta = _source_meta(path, url, retrieved_at)
    election = ELECTION_DATES[cycle]
    rows: list[dict[str, object]] = []
    seen = 0
    for row in table[header_index + 1 :]:
        if not row or "valresultat" in " ".join(row).lower():
            continue
        firm = strip_citations(row[0])
        family = institute_family(firm)
        if family is None:
            continue
        field_text = row[field_idx] if field_idx is not None and field_idx < len(row) else ""
        pub_text = row[pub_idx] if pub_idx is not None and pub_idx < len(row) else ""
        if not field_text:
            field_text = pub_text
        year_match = re.search(r"(20\d{2})", f"{pub_text} {field_text}")
        year = int(year_match.group(1)) if year_match else election.year
        if year != election.year:
            continue
        fieldwork = parse_fieldwork(f"{field_text} {year}", election)
        if fieldwork is None:
            continue
        start, end = fieldwork
        if not (1 <= days_to_election(end, election) <= WINDOW_DAYS):
            continue
        shares = shares_from_row(row, header_map)
        if shares is None:
            continue
        sample = None
        if n_idx is not None and n_idx < len(row) and n_idx != field_idx:
            sample = parse_sample_size(row[n_idx])
        publication = None
        if pub_idx is not None and pub_idx < len(row):
            parsed_pub = parse_fieldwork(f"{row[pub_idx]} {election.year}", election)
            if parsed_pub is not None:
                publication = parsed_pub[1]
        commissioner = ""
        if comm_idx is not None and comm_idx < len(row):
            commissioner = row[comm_idx]
        source_level = "A" if family == "Ipsos" else "C"
        seen += 1
        rows.append(
            _row_record(
                poll_id=f"temo_{cycle}_{family.lower().replace('/', '_')}_{end.isoformat()}_{seen}",
                cycle=cycle,
                pollster=firm or family,
                family=family,
                commissioner=commissioner,
                publication=publication,
                start=start,
                end=end,
                sample_size=sample,
                shares=shares,
                source_level=source_level,
                meta=meta,
            )
        )
    return rows
