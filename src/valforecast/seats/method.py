"""Building blocks of the Swedish Riksdag seat rules.

Fixed seats per constituency use Hamilton's largest-remainder method
(vallagen 4 kap. 3 § / RF 3 kap. 6 §). Party seats use the modified
Sainte-Laguë method (jämkade uddatalsmetoden, vallagen 14 kap. 4 §).

Statutory ties are resolved by lottery. This research code replaces the
lottery with a documented deterministic rule so every run is reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

FIXED_CONSTITUENCY_SEATS = 310
TOTAL_RIKSDAG_SEATS = 349
ADJUSTMENT_SEATS = 39
NATIONAL_THRESHOLD = 0.04
CONSTITUENCY_THRESHOLD = 0.12
FIRST_DIVISOR = 1.2


def modified_sainte_lague_divisor(seats_already: int) -> float:
    """Return the next divisor: 1.2 before the first seat, then 3, 5, 7, ..."""
    if seats_already < 0:
        raise ValueError("seats_already must be non-negative")
    if seats_already == 0:
        return FIRST_DIVISOR
    return float(2 * seats_already + 1)


def allocate_modified_sainte_lague(
    votes: Mapping[str, float],
    n_seats: int,
) -> dict[str, int]:
    """Award `n_seats` one at a time to the current highest quotient.

    Quotient is votes / 1.2 before a party has a seat, then votes / (2s+1).
    Equal quotients go to the lexicographically first party code. That is a
    deterministic stand-in for the statutory lottery (vallagen 14 kap. 4 §).
    """
    if n_seats < 0:
        raise ValueError("n_seats must be non-negative")
    if n_seats > 0 and not votes:
        raise ValueError("Cannot allocate seats without participating parties")
    if any(count < 0 for count in votes.values()):
        raise ValueError("Vote counts cannot be negative")

    seats = {party: 0 for party in votes}
    for _ in range(n_seats):
        winner: str | None = None
        best_quotient = -1.0
        for party in sorted(votes):
            quotient = votes[party] / modified_sainte_lague_divisor(seats[party])
            if winner is None or quotient > best_quotient:
                winner = party
                best_quotient = quotient
        if winner is None:
            raise ValueError("Sainte-Laguë allocation found no winner")
        seats[winner] += 1
    return seats


def allocate_hamilton_seats(
    eligible: Mapping[str, int],
    n_seats: int,
) -> dict[str, int]:
    """Distribute `n_seats` by largest remainder (Hamilton).

    Each unit first receives floor(eligible * n_seats / total). Leftover
    seats go to the largest remainders (eligible * n_seats) mod total.
    Equal remainders go to the lexicographically first unit id. That is a
    deterministic stand-in for the statutory lottery (vallagen 4 kap. 3 §).
    """
    if n_seats < 0:
        raise ValueError("n_seats must be non-negative")
    if n_seats > 0 and not eligible:
        raise ValueError("Cannot allocate seats without units")
    if any(count < 0 for count in eligible.values()):
        raise ValueError("Eligible-voter counts cannot be negative")

    total = sum(eligible.values())
    if n_seats > 0 and total <= 0:
        raise ValueError("Eligible-voter total must be positive")

    seats = {unit: (count * n_seats) // total for unit, count in eligible.items()}
    remainders = {unit: (count * n_seats) % total for unit, count in eligible.items()}
    leftover = n_seats - sum(seats.values())
    ranked: Sequence[str] = sorted(eligible, key=lambda unit: (-remainders[unit], unit))
    for unit in ranked[:leftover]:
        seats[unit] += 1
    return seats
