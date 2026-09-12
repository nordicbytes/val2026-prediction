from pathlib import Path

import pytest

from valforecast.seats.allocate import allocate_adjustment_totals, allocate_riksdag
from valforecast.seats.data import (
    FIXED_SEATS_2022_XLSX,
    OFFICIAL_2022_PARTY_SEATS,
    OFFICIAL_2022_TOTAL_SEATS_BY_NAME,
    RESULTS_2022_XLSX,
    load_election_2022,
    load_official_fixed_seats_2026,
)
from valforecast.seats.produce import fixed_seats_for_2026_simulation

ROOT = Path(__file__).resolve().parents[1]


def test_twelve_percent_party_keeps_local_seats_and_is_set_aside() -> None:
    # C is 1.9% nationally but 20% in X, so it takes part only in X.
    votes = {
        "X": {"A": 70, "B": 10, "C": 20},
        "Y": {"A": 930, "B": 30, "C": 0},
    }
    allocation = allocate_riksdag(
        votes,
        {"X": 5, "Y": 3},
        parties=("A", "B", "C"),
        total_seats=9,
    )
    assert allocation.seats["C"] >= 1
    assert allocation.fixed_seats["C"] == allocation.seats["C"]
    assert ("X", "C") in allocation.twelve_percent_constituencies
    assert "C" in allocation.excluded_below_national_threshold
    assert sum(allocation.seats.values()) == 9


def test_other_residual_cannot_take_seats_even_above_four_percent() -> None:
    votes = {
        "X": {"S": 40, "M": 35, "OTHER": 25},
        "Y": {"S": 40, "M": 35, "OTHER": 25},
    }
    allocation = allocate_riksdag(
        votes,
        {"X": 2, "Y": 2},
        parties=("S", "M", "OTHER"),
        total_seats=5,
    )
    assert allocation.seats["OTHER"] == 0
    assert allocation.seats["S"] + allocation.seats["M"] == 5
    assert "OTHER" in allocation.excluded_below_national_threshold


def test_overrepresented_party_is_set_aside_iteratively() -> None:
    national = {"A": 100.0, "B": 100.0, "C": 100.0}
    fixed = {"A": 3, "B": 0, "C": 0}
    seats, _nationwide, over, below = allocate_adjustment_totals(
        national,
        fixed,
        total_seats=5,
    )
    assert below == ()
    assert over == ("A",)
    assert seats["A"] == 3
    assert seats["B"] + seats["C"] == 2
    assert sum(seats.values()) == 5


@pytest.mark.skipif(
    not (ROOT / RESULTS_2022_XLSX).exists(),
    reason="Pinned 2022 district results are absent",
)
def test_2022_allocation_matches_valmyndigheten_exactly() -> None:
    election = load_election_2022(ROOT)
    allocation = allocate_riksdag(election.votes_by_constituency, election.fixed_seats)
    observed = {party: allocation.seats[party] for party in OFFICIAL_2022_PARTY_SEATS}
    assert observed == OFFICIAL_2022_PARTY_SEATS
    assert allocation.seats.get("OTHER", 0) == 0
    assert sum(allocation.seats.values()) == 349
    assert allocation.excluded_overrepresented == ()
    assert allocation.twelve_percent_constituencies == ()
    assert sum(allocation.fixed_seats.values()) == 310
    for constituency_id, name in election.names.items():
        fixed = sum(allocation.fixed_by_constituency[constituency_id].values())
        assert fixed == election.fixed_seats[constituency_id]
        assert fixed <= OFFICIAL_2022_TOTAL_SEATS_BY_NAME[name]
    leftover = sum(
        OFFICIAL_2022_TOTAL_SEATS_BY_NAME[name] - election.fixed_seats[constituency_id]
        for constituency_id, name in election.names.items()
    )
    assert leftover == 39


@pytest.mark.skipif(
    not (ROOT / FIXED_SEATS_2022_XLSX).exists(),
    reason="Pinned official fixed-seat workbook is absent",
)
def test_2026_simulation_uses_official_fixed_seat_column() -> None:
    official = load_official_fixed_seats_2026(ROOT)
    used = fixed_seats_for_2026_simulation(ROOT)
    election = load_election_2022(ROOT)
    assert used == official
    assert used == election.official_fixed_2026
    assert len(used) == 29
    assert sum(used.values()) == 310
    by_name = {election.names[constituency_id]: seats for constituency_id, seats in used.items()}
    assert by_name["Stockholms kommun"] == 29
    assert by_name["Skåne läns västra"] == 9
    assert by_name["Värmlands län"] == 9
    assert by_name["Norrbottens län"] == 8
