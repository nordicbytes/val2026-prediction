"""Pinned inputs for seat allocation. Research only; not a production lock."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from openpyxl import load_workbook

from valforecast.features.election_history import PARTIES
from valforecast.forecast.snapshot import official_snapshot_path
from valforecast.forecast.universe import read_val2026_eligible_districts
from valforecast.ingest.elections import read_val2022_district_results

FIXED_SEATS_2022_XLSX = Path(
    "data/raw/valmyndigheten/seats/fordelning-av-mandat-2022-och-2026.xlsx"
)
RESULTS_2022_XLSX = Path(
    "data/raw/valmyndigheten/2022/roster_per_distrikt_slutligt_riksdag.xlsx"
)
ELIGIBLE_2026_XLSX = Path(
    "data/raw/valmyndigheten/2026/rostberattigade_riksdag_alder_kon_2026-08-14.xlsx"
)
ESTIMATOR_LOCK = Path("reports/poll_calibration/estimator_lock.json")

OFFICIAL_2022_PARTY_SEATS: dict[str, int] = {
    "S": 107,
    "SD": 73,
    "M": 68,
    "V": 24,
    "C": 24,
    "KD": 19,
    "MP": 18,
    "L": 16,
}

# Valmyndigheten totals (fixed + adjustment) per constituency in 2022.
# Sanity check only — not an input. We do not place adjustment seats.
OFFICIAL_2022_TOTAL_SEATS_BY_NAME: dict[str, int] = {
    "Blekinge län": 5,
    "Dalarnas län": 11,
    "Gotlands län": 2,
    "Gävleborgs län": 11,
    "Göteborgs kommun": 18,
    "Hallands län": 12,
    "Jämtlands län": 4,
    "Jönköpings län": 13,
    "Kalmar län": 8,
    "Kronobergs län": 6,
    "Malmö kommun": 11,
    "Norrbottens län": 8,
    "Skåne läns norra och östra": 10,
    "Skåne läns södra": 14,
    "Skåne läns västra": 10,
    "Stockholms kommun": 34,
    "Stockholms län": 43,
    "Södermanlands län": 11,
    "Uppsala län": 13,
    "Värmlands län": 11,
    "Västerbottens län": 9,
    "Västernorrlands län": 9,
    "Västmanlands län": 8,
    "Västra Götalands läns norra": 8,
    "Västra Götalands läns södra": 8,
    "Västra Götalands läns västra": 14,
    "Västra Götalands läns östra": 9,
    "Örebro län": 12,
    "Östergötlands län": 17,
}

JSON_PARTIES = ("V", "S", "MP", "C", "L", "M", "KD", "SD")


@dataclass(frozen=True)
class ConstituencyMeta:
    constituency_id: str
    name: str


@dataclass(frozen=True)
class Election2022Votes:
    votes_by_constituency: dict[str, dict[str, int]]
    names: dict[str, str]
    fixed_seats: dict[str, int]
    official_fixed_2026: dict[str, int]


@dataclass(frozen=True)
class SnapshotConstituencies:
    constituency_ids: tuple[str, ...]
    names: dict[str, str]
    shares: dict[str, dict[str, float]]
    eligible_voters: dict[str, int]


def load_constituency_names(root: Path) -> dict[str, str]:
    eligible = read_val2026_eligible_districts(root / ELIGIBLE_2026_XLSX)
    names: dict[str, str] = {}
    for record in eligible.group_by("constituency_id", "constituency_name").len().iter_rows(
        named=True
    ):
        names[str(record["constituency_id"])] = str(record["constituency_name"])
    if len(names) != 29:
        raise ValueError(f"Expected 29 constituencies, found {len(names)}")
    return names


def load_official_fixed_seats_workbook(root: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Read Valmyndigheten's 2022 and official 2026 fixed-seat columns.

    The 2026 column is the decision of 11 May 2026 under vallagen 4 kap. 3 §,
    based on eligible voters on 1 March 2026. That column is the input to
    the 2026 seat simulation. A Hamilton allocation from the 14 August
    eligible-voter snapshot is kept only as a documented sensitivity check.
    """
    path = root / FIXED_SEATS_2022_XLSX
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["Mandat 2022 och 2026"]
    seats_2022: dict[str, int] = {}
    seats_2026: dict[str, int] = {}
    for values in sheet.iter_rows(values_only=True):
        if values[0] != "Val till Riksdagen":
            continue
        name = str(values[4]).strip()
        seats_2022[name] = int(str(values[5]))
        seats_2026[name] = int(str(values[6]))
    if sum(seats_2022.values()) != 310 or len(seats_2022) != 29:
        raise ValueError("Official 2022 fixed-seat table does not sum to 310 over 29 units")
    if sum(seats_2026.values()) != 310 or len(seats_2026) != 29:
        raise ValueError("Official 2026 fixed-seat table does not sum to 310 over 29 units")
    return seats_2022, seats_2026


def _map_names_to_ids(
    seats_by_name: Mapping[str, int],
    names: Mapping[str, str],
) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for constituency_id, name in names.items():
        if name not in seats_by_name:
            raise ValueError(f"No official fixed-seat row for {name}")
        mapped[constituency_id] = seats_by_name[name]
    return mapped


def load_official_fixed_seats_2026(root: Path) -> dict[str, int]:
    """Official 2026 fixed seats per constituency id. Sums to 310 over 29 units."""
    names = load_constituency_names(root)
    _seats_2022, seats_2026 = load_official_fixed_seats_workbook(root)
    return _map_names_to_ids(seats_2026, names)


def load_election_2022(root: Path) -> Election2022Votes:
    names = load_constituency_names(root)
    official_2022, official_2026 = load_official_fixed_seats_workbook(root)
    results = read_val2022_district_results(root / RESULTS_2022_XLSX)
    grouped = results.group_by("constituency_id", "canonical_party_code").agg(
        pl.col("votes").sum()
    )
    votes = {
        constituency_id: {party: 0 for party in PARTIES} for constituency_id in names
    }
    for record in grouped.iter_rows(named=True):
        constituency_id = str(record["constituency_id"])
        party = str(record["canonical_party_code"])
        if constituency_id not in votes:
            raise ValueError(f"Unexpected 2022 constituency id {constituency_id}")
        if party not in votes[constituency_id]:
            raise ValueError(f"Unexpected 2022 party {party}")
        votes[constituency_id][party] = int(record["votes"])
    return Election2022Votes(
        votes_by_constituency=votes,
        names=names,
        fixed_seats=_map_names_to_ids(official_2022, names),
        official_fixed_2026=_map_names_to_ids(official_2026, names),
    )


def load_snapshot_constituencies(root: Path) -> SnapshotConstituencies:
    names = load_constituency_names(root)
    document = json.loads(official_snapshot_path(root).read_text(encoding="utf-8"))
    rows = document["prediction"]["aggregates"]["constituency"]
    shares: dict[str, dict[str, float]] = {
        constituency_id: {} for constituency_id in names
    }
    eligible: dict[str, int] = {}
    for row in rows:
        constituency_id = str(row["unit_id"])
        party = str(row["party"])
        if constituency_id not in shares:
            raise ValueError(f"Snapshot constituency {constituency_id} is not in 2026 geography")
        shares[constituency_id][party] = float(row["point"])
        eligible[constituency_id] = int(row["eligible_voters"])
    for constituency_id, party_shares in shares.items():
        missing = set(PARTIES) - set(party_shares)
        if missing:
            raise ValueError(f"{constituency_id} missing parties: {sorted(missing)}")
        total = sum(party_shares[party] for party in PARTIES)
        if abs(total - 1.0) > 1e-8:
            raise ValueError(f"{constituency_id} share sum is {total}")
    ordered = tuple(sorted(names))
    return SnapshotConstituencies(
        constituency_ids=ordered,
        names=names,
        shares=shares,
        eligible_voters=eligible,
    )


def load_production_overlay_sigma(root: Path) -> list[list[float]]:
    document = json.loads((root / ESTIMATOR_LOCK).read_text(encoding="utf-8"))
    sigma = document["production_overlay_covariance"]["sigma"]
    if len(sigma) != len(PARTIES) or any(len(row) != len(PARTIES) for row in sigma):
        raise ValueError("Overlay covariance does not match PARTIES")
    return [[float(value) for value in row] for row in sigma]
