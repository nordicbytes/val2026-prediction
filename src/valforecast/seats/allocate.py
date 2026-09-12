"""National Riksdag seat totals from constituency votes.

This implements the party-level rules in RF 3 kap. 6–7 §§ and vallagen
14 kap. 3–5 §§. It deliberately does not implement vallagen 14 kap. 6 §
(placing adjustment seats into constituencies) or the constituency-level
återföring of surplus fixed seats. Those steps change which constituency
holds a seat. We only need the national party totals, and 14 kap. 5 §
already determines those: an over-represented party's extra fixed seats
stay with that party, the party is set aside, and the remaining seats are
reallocated among the others. The reallocation is iterative because a
new nationwide draw can make another party over-represented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from valforecast.features.election_history import PARTIES
from valforecast.seats.method import (
    NATIONAL_THRESHOLD,
    TOTAL_RIKSDAG_SEATS,
    allocate_modified_sainte_lague,
)

CONSTITUENCY_THRESHOLD = 0.12
# OTHER is a residual of many small parties, not a party that can clear 4%.
SEAT_INELIGIBLE_PARTIES = frozenset({"OTHER"})


@dataclass(frozen=True)
class SeatAllocation:
    seats: dict[str, int]
    fixed_seats: dict[str, int]
    adjustment_seats: dict[str, int]
    nationwide_target: dict[str, int]
    excluded_overrepresented: tuple[str, ...]
    excluded_below_national_threshold: tuple[str, ...]
    twelve_percent_constituencies: tuple[tuple[str, str], ...]
    fixed_by_constituency: dict[str, dict[str, int]]


def participating_parties(
    national_votes: Mapping[str, float],
    constituency_votes: Mapping[str, float],
    *,
    national_threshold: float = NATIONAL_THRESHOLD,
    constituency_threshold: float = CONSTITUENCY_THRESHOLD,
) -> list[str]:
    """Parties that may take fixed seats in one constituency."""
    national_total = sum(national_votes.values())
    local_total = sum(constituency_votes.values())
    if national_total <= 0:
        raise ValueError("National vote total must be positive")
    participating: list[str] = []
    for party, votes in national_votes.items():
        if party in SEAT_INELIGIBLE_PARTIES:
            continue
        local_share = (
            constituency_votes.get(party, 0.0) / local_total if local_total > 0 else 0.0
        )
        over_national = votes / national_total >= national_threshold
        if over_national or local_share >= constituency_threshold:
            participating.append(party)
    return participating


def allocate_fixed_seats(
    votes_by_constituency: Mapping[str, Mapping[str, float]],
    fixed_seats_by_constituency: Mapping[str, int],
    *,
    parties: Sequence[str] = PARTIES,
    national_threshold: float = NATIONAL_THRESHOLD,
    constituency_threshold: float = CONSTITUENCY_THRESHOLD,
) -> tuple[dict[str, int], dict[str, dict[str, int]], tuple[tuple[str, str], ...]]:
    """Allocate the 310 fixed seats constituency by constituency."""
    national_votes = {
        party: sum(float(votes.get(party, 0.0)) for votes in votes_by_constituency.values())
        for party in parties
    }
    fixed = {party: 0 for party in parties}
    by_constituency: dict[str, dict[str, int]] = {}
    twelve_percent: list[tuple[str, str]] = []
    national_total = sum(national_votes.values())

    for constituency, n_fixed in fixed_seats_by_constituency.items():
        local = {
            party: float(votes_by_constituency[constituency].get(party, 0.0)) for party in parties
        }
        allowed = participating_parties(
            national_votes,
            local,
            national_threshold=national_threshold,
            constituency_threshold=constituency_threshold,
        )
        for party in allowed:
            if (
                national_total > 0
                and national_votes[party] / national_total < national_threshold
            ):
                twelve_percent.append((constituency, party))
        awarded = allocate_modified_sainte_lague(
            {party: local[party] for party in allowed},
            n_fixed,
        )
        complete = {party: awarded.get(party, 0) for party in parties}
        by_constituency[constituency] = complete
        for party, seats in complete.items():
            fixed[party] += seats
    return fixed, by_constituency, tuple(twelve_percent)


def allocate_adjustment_totals(
    national_votes: Mapping[str, float],
    fixed_seats: Mapping[str, int],
    *,
    total_seats: int = TOTAL_RIKSDAG_SEATS,
    national_threshold: float = NATIONAL_THRESHOLD,
) -> tuple[dict[str, int], dict[str, int], tuple[str, ...], tuple[str, ...]]:
    """Turn fixed seats into national totals via vallagen 14 kap. 5 §.

    Parties under the national threshold are set aside with whatever fixed
    seats the 12 percent rule gave them. If a remaining party already has
    at least as many fixed seats as the current nationwide allocation, it
    is set aside with those seats and the rest are reallocated. Repeat
    until no active party is over-represented.
    """
    national_total = sum(national_votes.values())
    if national_total <= 0:
        raise ValueError("National vote total must be positive")

    seats = {party: 0 for party in national_votes}
    below = tuple(
        party
        for party, votes in national_votes.items()
        if party in SEAT_INELIGIBLE_PARTIES or votes / national_total < national_threshold
    )
    remaining = total_seats
    for party in below:
        seats[party] = fixed_seats.get(party, 0)
        remaining -= seats[party]
    if remaining < 0:
        raise ValueError("Fixed seats of sub-threshold parties exceed the chamber")

    active = [party for party in national_votes if party not in below]
    excluded_over: list[str] = []
    nationwide = {party: 0 for party in national_votes}

    while active:
        if remaining == 0:
            break
        nationwide = allocate_modified_sainte_lague(
            {party: national_votes[party] for party in active},
            remaining,
        )
        over = [party for party in active if fixed_seats.get(party, 0) >= nationwide[party]]
        if not over:
            for party, count in nationwide.items():
                seats[party] = count
            break
        over.sort(
            key=lambda party: (
                fixed_seats.get(party, 0) - nationwide[party],
                party,
            ),
            reverse=True,
        )
        party = over[0]
        seats[party] = fixed_seats.get(party, 0)
        remaining -= seats[party]
        active.remove(party)
        excluded_over.append(party)
        if remaining < 0:
            raise ValueError("Over-represented fixed seats exceed the remaining chamber")
    else:
        if remaining != 0:
            raise ValueError("No parties left to receive remaining seats")

    return seats, nationwide, tuple(excluded_over), below


def allocate_riksdag(
    votes_by_constituency: Mapping[str, Mapping[str, float]],
    fixed_seats_by_constituency: Mapping[str, int],
    *,
    parties: Sequence[str] = PARTIES,
    total_seats: int = TOTAL_RIKSDAG_SEATS,
    national_threshold: float = NATIONAL_THRESHOLD,
    constituency_threshold: float = CONSTITUENCY_THRESHOLD,
) -> SeatAllocation:
    """Return national party seat totals from constituency vote counts."""
    missing = set(fixed_seats_by_constituency) - set(votes_by_constituency)
    if missing:
        raise ValueError(f"Votes missing for constituencies: {sorted(missing)}")

    fixed, by_constituency, twelve = allocate_fixed_seats(
        votes_by_constituency,
        fixed_seats_by_constituency,
        parties=parties,
        national_threshold=national_threshold,
        constituency_threshold=constituency_threshold,
    )
    national_votes = {
        party: sum(float(votes.get(party, 0.0)) for votes in votes_by_constituency.values())
        for party in parties
    }
    seats, nationwide, over, below = allocate_adjustment_totals(
        national_votes,
        fixed,
        total_seats=total_seats,
        national_threshold=national_threshold,
    )
    adjustment = {party: seats[party] - fixed[party] for party in parties}
    if any(value < 0 and party not in over for party, value in adjustment.items()):
        raise ValueError("Negative adjustment seats for a party that was not set aside")
    if sum(seats.values()) != total_seats:
        raise ValueError(f"Seat total is {sum(seats.values())}, expected {total_seats}")
    return SeatAllocation(
        seats=seats,
        fixed_seats=fixed,
        adjustment_seats=adjustment,
        nationwide_target=nationwide,
        excluded_overrepresented=over,
        excluded_below_national_threshold=below,
        twelve_percent_constituencies=twelve,
        fixed_by_constituency=by_constituency,
    )
