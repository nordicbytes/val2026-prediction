from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.ingest.elections import (
    read_legacy_district_results,
    read_val2018_district_results,
    read_val2022_district_results,
)

ELECTION_DATES = {
    2022: date(2022, 9, 11),
    2018: date(2018, 9, 9),
    2014: date(2014, 9, 14),
    2010: date(2010, 9, 19),
    2006: date(2006, 9, 17),
    2002: date(2002, 9, 15),
}

RESULT_GOLD = {
    2022: {"S": 0.3033, "SD": 0.2054, "L": 0.0461},
    2018: {"S": 0.2826, "SD": 0.1753, "L": 0.0549},
    2014: {"S": 0.3101, "SD": 0.1286, "L": 0.0542},
    2010: {"S": 0.3066, "SD": 0.0570, "L": 0.0706},
    2006: {"S": 0.3499, "M": 0.2623, "L": 0.0754},
    2002: {"S": 0.3985, "M": 0.1526, "L": 0.1339},
}

DISTRICT_FILES = {
    2022: Path("data/raw/valmyndigheten/2022/roster_per_distrikt_slutligt_riksdag.xlsx"),
    2018: Path("data/raw/valmyndigheten/2018/2018_R_per_valdistrikt.xlsx"),
    2014: Path("data/raw/valmyndigheten/2014/2014_riksdagsval_per_valdistrikt.skv"),
    2010: Path("data/raw/valmyndigheten/2010/slutligt_valresultat_valdistrikt_R.skv"),
}

OFFICIAL_HTML_2006 = Path("data/raw/polls/history/val_national_2006.html")


def _from_districts(root: Path, year: int) -> dict[str, float]:
    path = root / DISTRICT_FILES[year]
    if year == 2022:
        results = read_val2022_district_results(path)
    elif year == 2018:
        results = read_val2018_district_results(path)
    else:
        results = read_legacy_district_results(path, election_year=year)
    votes = dict(
        results.group_by("canonical_party_code")
        .agg(pl.col("votes").sum())
        .select("canonical_party_code", "votes")
        .iter_rows()
    )
    valid = float(
        results.group_by("district_id")
        .agg(pl.col("valid_votes").first())
        .select(pl.col("valid_votes").sum())
        .item()
    )
    shares = {party: float(votes.get(party, 0.0)) / valid for party in PARTIES}
    _assert_gold(year, shares)
    return shares


def _from_2006_html(root: Path, year: int) -> dict[str, float]:
    text = (root / OFFICIAL_HTML_2006).read_text(encoding="utf-8", errors="replace")
    if year == 2006:
        if "34,99%" not in text or "26,23%" not in text:
            raise ValueError("2006 official cells missing")
        raw = {
            "M": 0.2623,
            "C": 0.0788,
            "L": 0.0754,
            "KD": 0.0659,
            "S": 0.3499,
            "V": 0.0585,
            "MP": 0.0524,
            "SD": 0.0,
            "OTHER": 0.0567,
        }
    else:
        if "39,85" not in text or "15,26" not in text:
            raise ValueError("2002 official cells missing from the 2006 result page")
        raw = {
            "M": 0.1526,
            "C": 0.0619,
            "L": 0.1339,
            "KD": 0.0915,
            "S": 0.3985,
            "V": 0.0839,
            "MP": 0.0465,
            "SD": 0.0,
            "OTHER": 0.0312,
        }
    _assert_gold(year, raw)
    total = sum(raw[party] for party in PARTIES)
    return {party: raw[party] / total for party in PARTIES}


def _assert_gold(year: int, shares: dict[str, float]) -> None:
    for party, expected in RESULT_GOLD[year].items():
        if abs(shares[party] - expected) > 0.0006:
            raise ValueError(
                f"Official {year} gold cell failed for {party}: "
                f"{shares[party]:.4f} != {expected:.4f}"
            )


def load_official_national_results(root: Path) -> dict[int, dict[str, float]]:
    results = {year: _from_districts(root, year) for year in (2022, 2018, 2014, 2010)}
    results[2006] = _from_2006_html(root, 2006)
    results[2002] = _from_2006_html(root, 2002)
    return results
