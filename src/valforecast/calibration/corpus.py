from __future__ import annotations

from pathlib import Path
from typing import Any

from valforecast.calibration.parse_temo import parse_temo_archive
from valforecast.calibration.parse_wikipedia import parse_en_wikipedia, parse_sv_2010_wikipedia

RETRIEVED_AT = "2026-09-12T08:15:00+00:00"
HISTORY = Path("data/raw/polls/history")

POLL_GOLD = {
    2022: {
        "match": ("Sifo/Verian", "2022-09-07", 0.295),
        "party": "S",
    },
    2018: {
        "match": ("Inizio", "2018-09-07", 0.246),
        "party": "S",
    },
    2014: {
        "match": ("Sifo/Verian", "2014-09-11", 0.310),
        "party": "S",
    },
    2010: {
        "match": ("Sifo/Verian", "2010-09-16", 0.303),
        "party": "S",
    },
    2006: {
        "match": ("Sifo/Verian", "2006-09-11", 0.370),
        "party": "S",
    },
    2002: {
        "match": ("Sifo/Verian", "2002-09-12", 0.371),
        "party": "S",
    },
}


def build_poll_corpus(root: Path) -> list[dict[str, Any]]:
    history = root / HISTORY
    rows: list[dict[str, Any]] = []
    rows.extend(
        parse_en_wikipedia(
            history / "wikipedia_en_polling_2022.html",
            cycle=2022,
            url="https://en.wikipedia.org/wiki/Opinion_polling_for_the_2022_Swedish_general_election",
            retrieved_at=RETRIEVED_AT,
        )
    )
    rows.extend(
        parse_en_wikipedia(
            history / "wikipedia_en_polling_2018.html",
            cycle=2018,
            url="https://en.wikipedia.org/wiki/Opinion_polling_for_the_2018_Swedish_general_election",
            retrieved_at=RETRIEVED_AT,
        )
    )
    rows.extend(
        parse_en_wikipedia(
            history / "wikipedia_en_polling_2014.html",
            cycle=2014,
            url="https://en.wikipedia.org/wiki/Opinion_polling_for_the_2014_Swedish_general_election",
            retrieved_at=RETRIEVED_AT,
        )
    )
    rows.extend(
        parse_sv_2010_wikipedia(
            history / "wikipedia_sv_polling_2010.html",
            url="https://sv.wikipedia.org/wiki/Opinionsundersökningar_inför_riksdagsvalet_i_Sverige_2010",
            retrieved_at=RETRIEVED_AT,
        )
    )
    rows.extend(
        parse_temo_archive(
            history / "temo_valjarbarometer_2006.html",
            cycle=2006,
            url="https://web.archive.org/web/20060913105523/www.temo.se/upload/326/valjbsamtliga.htm",
            retrieved_at=RETRIEVED_AT,
        )
    )
    rows.extend(
        parse_temo_archive(
            history / "temo_valjarbarometer_2002.html",
            cycle=2002,
            url="https://web.archive.org/web/20060430024635/http://www.temo.se/upload/326/valjbsamtliga2000_sep2002.htm",
            retrieved_at=RETRIEVED_AT,
        )
    )
    _assert_gold_rows(rows)
    return rows


def _assert_gold_rows(rows: list[dict[str, Any]]) -> None:
    for cycle, spec in POLL_GOLD.items():
        family, end, expected = spec["match"]
        party = str(spec["party"])
        matches = [
            row
            for row in rows
            if int(row["election_cycle"]) == cycle
            and row["institute_family"] == family
            and row["fieldwork_end"] == end
        ]
        if not matches:
            raise ValueError(f"Gold poll missing for {cycle} {family} {end}")
        share = float(matches[0]["shares"][party])
        if abs(share - float(expected)) > 0.002:
            raise ValueError(f"Gold poll {cycle} {party}={share} != {expected}")


def last_polls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["election_cycle"]), str(row["institute_family"]))
        current = latest.get(key)
        if current is None or str(row["fieldwork_end"]) > str(current["fieldwork_end"]):
            latest[key] = row
    return sorted(
        latest.values(),
        key=lambda row: (int(row["election_cycle"]), str(row["institute_family"])),
    )


def internal_row_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in rows:
        shares = row["shares"]
        total = sum(float(shares[party]) for party in shares)
        if abs(total - 1.0) > 0.001:
            issues.append({"poll_id": row["poll_id"], "issue": "share_sum", "value": total})
        if int(row["days_to_election"]) < 1:
            issues.append({"poll_id": row["poll_id"], "issue": "not_before_election"})
        sample = row["sample_size"]
        if sample is not None and (int(sample) < 200 or int(sample) > 20000):
            issues.append({"poll_id": row["poll_id"], "issue": "sample_size", "value": sample})
    return issues
