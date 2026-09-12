from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from valforecast.features.election_history import PARTIES

MONTHS = {
    "jan": 1,
    "januari": 1,
    "january": 1,
    "feb": 2,
    "februari": 2,
    "february": 2,
    "mar": 3,
    "mars": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "maj": 5,
    "jun": 6,
    "juni": 6,
    "june": 6,
    "jul": 7,
    "juli": 7,
    "july": 7,
    "aug": 8,
    "augusti": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "okt": 10,
    "oktober": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

HEADER_TO_PARTY = {
    "v": "V",
    "s": "S",
    "mp": "MP",
    "c": "C",
    "l": "L",
    "fp": "L",
    "m": "M",
    "kd": "KD",
    "sd": "SD",
    "oth": "OTHER",
    "oth.": "OTHER",
    "others": "OTHER",
    "other": "OTHER",
    "övr": "OTHER",
    "övr.": "OTHER",
    "övriga": "OTHER",
    "fi": "OTHER",
    "jl": "OTHER",
}

INSTITUTE_FAMILY = {
    "sifo": "Sifo/Verian",
    "tns sifo": "Sifo/Verian",
    "kantar sifo": "Sifo/Verian",
    "kantar public": "Sifo/Verian",
    "verian": "Sifo/Verian",
    "novus": "Novus",
    "novus opinion": "Novus",
    "demoskop": "Demoskop",
    "ipsos": "Ipsos",
    "temo": "Ipsos",
    "synovate": "Ipsos",
    "synovate temo": "Ipsos",
    "scb": "SCB",
    "scb psu": "SCB",
    "skop": "SKOP",
    "inizio": "Inizio",
    "sentio": "Sentio",
    "sentio research": "Sentio",
    "yougov": "YouGov",
    "united minds": "United Minds",
}

TRACKED_FAMILIES = frozenset(INSTITUTE_FAMILY.values())
EXCLUDED_POLLS = (
    "valu",
    "svt valu",
    "exit poll",
    "exit polls",
    "general election",
    "valet 20",
)


def clean_header(text: str) -> str:
    return re.sub(r"[^a-zåäö.]+", "", text.lower().strip())


def map_headers(headers: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, header in enumerate(headers):
        party = HEADER_TO_PARTY.get(clean_header(header))
        if party is not None:
            mapping[index] = party
    return mapping


def parse_percent(text: str) -> float | None:
    cleaned = text.replace("%", " ")
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    match = re.search(r"(\d+(?:[.,]\d+)?)", cleaned.replace("\xa0", " "))
    if match is None:
        return None
    return float(match.group(1).replace(",", ".")) / 100.0


def parse_sample_size(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    value = int(digits)
    if value < 200 or value > 20000:
        return None
    return value


def strip_citations(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", text).strip()


def institute_family(raw_name: str) -> str | None:
    name = strip_citations(raw_name).lower()
    name = re.sub(r"archived.*", "", name).strip(" -–")
    name = re.sub(r"\s+", " ", name)
    if any(marker in name for marker in EXCLUDED_POLLS):
        return None
    for key, family in sorted(INSTITUTE_FAMILY.items(), key=lambda item: -len(item[0])):
        if name == key or name.startswith(key + " ") or name.startswith(key + "/"):
            return family
    return None


def _parse_one_date(text: str, default_year: int) -> date | None:
    cleaned = text.lower().replace(".", " ").replace("/", " ")
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    match = re.search(
        r"(\d{1,2})\s*([a-zåäö]+)?\s*(\d{4})?",
        cleaned,
    )
    if match is None:
        return None
    day = int(match.group(1))
    month_token = match.group(2)
    year = int(match.group(3)) if match.group(3) else default_year
    if month_token is None:
        return None
    month = MONTHS.get(month_token)
    if month is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_slash_date(text: str, default_year: int) -> date | None:
    match = re.search(r"(\d{1,2})/(\d{1,2})(?:\s+(\d{4}))?", text)
    if match is None:
        return None
    year = int(match.group(3) or default_year)
    try:
        return date(year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def parse_fieldwork(text: str, election_date: date) -> tuple[date, date] | None:
    if re.search(r"\b(20:00|19:30|16:00|exit)\b", text, flags=re.I):
        return None
    cleaned = text.replace("–", "-").replace("—", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    slash_range = re.search(
        r"(\d{1,2}/\d{1,2})\s*-\s*(\d{1,2}/\d{1,2})(?:\s+(\d{4}))?",
        cleaned,
    )
    if slash_range is not None:
        year = int(slash_range.group(3) or election_date.year)
        start = _parse_slash_date(slash_range.group(1), year)
        end = _parse_slash_date(slash_range.group(2), year)
        if start is not None and end is not None:
            return start, end
    month_only = re.fullmatch(
        r"([a-zåäö]+)\s+(\d{4})",
        cleaned.lower(),
    )
    if month_only is not None:
        month = MONTHS.get(month_only.group(1))
        year = int(month_only.group(2))
        if month is None:
            return None
        start = date(year, month, 1)
        end = (
            date(year, 12, 31)
            if month == 12
            else date(year, month + 1, 1) - timedelta(days=1)
        )
        if end >= election_date:
            end = election_date - timedelta(days=1)
        return start, end
    parts = re.split(r"\s*-\s*", cleaned, maxsplit=1)
    if len(parts) == 1:
        parsed = _parse_one_date(parts[0], election_date.year)
        if parsed is None:
            compact = re.match(r"(\d{1,2})/(\d{1,2})\s*-?\s*(\d{1,2})/(\d{1,2})\s+(\d{4})", cleaned)
            if compact is None:
                return None
            year = int(compact.group(5))
            start = date(year, int(compact.group(2)), int(compact.group(1)))
            end = date(year, int(compact.group(4)), int(compact.group(3)))
            return start, end
        return parsed, parsed
    end = _parse_one_date(parts[1], election_date.year)
    if end is None:
        return None
    start = _parse_one_date(parts[0] + " " + parts[1], election_date.year)
    if start is None:
        start_match = re.search(r"(\d{1,2})", parts[0])
        if start_match is None:
            return None
        try:
            start = date(end.year, end.month, int(start_match.group(1)))
        except ValueError:
            return None
    return start, end


def shares_from_row(
    row: list[str],
    header_map: dict[int, str],
) -> dict[str, float] | None:
    collected: dict[str, float] = {party: 0.0 for party in PARTIES}
    seen: set[str] = set()
    for index, party in header_map.items():
        if index >= len(row):
            continue
        value = parse_percent(row[index])
        if value is None:
            continue
        collected[party] += value
        seen.add(party)
    named = [party for party in PARTIES[:-1] if party in seen]
    if len(named) < 6:
        return None
    if "OTHER" not in seen:
        named_sum = sum(collected[party] for party in PARTIES[:-1])
        residual = 1.0 - named_sum
        if residual < 0 or residual > 0.05:
            return None
        collected["OTHER"] = residual
    total = sum(collected[party] for party in PARTIES)
    if total < 0.95 or total > 1.05:
        return None
    return {party: collected[party] / total for party in PARTIES}


def days_to_election(fieldwork_end: date, election_date: date) -> int:
    return (election_date - fieldwork_end).days


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()
