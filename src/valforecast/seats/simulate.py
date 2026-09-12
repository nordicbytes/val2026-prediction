"""2026 seat draws from the locked national overlay, raked onto constituencies.

Constituency vote counts are eligible_voters times the raked share. That
assumes uniform turnout across constituencies. Inside a constituency the
Sainte-Laguë quotients depend only on relative votes, so a common turnout
factor cancels. Across constituencies it affects the national threshold
and the nationwide allocation through the implied national vote totals.
Those national totals equal the overlay draw because raking enforces the
eligible-weighted national shares.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from valforecast.calibration.probabilities import ERROR_DRAWS_PER_BASE, apply_overlay
from valforecast.features.election_history import PARTIES
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.seats.allocate import SeatAllocation, allocate_riksdag
from valforecast.seats.data import JSON_PARTIES, SnapshotConstituencies
from valforecast.seats.method import (
    FIXED_CONSTITUENCY_SEATS,
    NATIONAL_THRESHOLD,
    TOTAL_RIKSDAG_SEATS,
    allocate_hamilton_seats,
)

# The published decomposed intervals and probabilities draw election-day error
# with seed + 1 and ten replicates per forecast draw (see calibration.intervals).
# Reusing both means the seat draws are the very same 20 000 national draws that
# produced P(L over 4 %), so the two sections of the site cannot disagree.
SEAT_OVERLAY_SEED = 20260913
SEAT_OVERLAY_REPLICATES = ERROR_DRAWS_PER_BASE
DEFAULT_N_DRAWS = 20000

COALITIONS: dict[str, tuple[str, ...]] = {
    "S+V+MP+C": ("S", "V", "MP", "C"),
    "M+KD+L+SD": ("M", "KD", "L", "SD"),
    "S+M": ("S", "M"),
    "S+C+MP+L": ("S", "C", "MP", "L"),
    "M+KD+C+L": ("M", "KD", "C", "L"),
    "S+C+L+KD+MP+M": ("S", "C", "L", "KD", "MP", "M"),
    "S+V+MP": ("S", "V", "MP"),
    "S+V+MP+C+L": ("S", "V", "MP", "C", "L"),
    "M+KD+SD": ("M", "KD", "SD"),
    "M+KD+L+SD+C": ("M", "KD", "L", "SD", "C"),
}


def constituency_share_matrix(snapshot: SnapshotConstituencies) -> np.ndarray:
    matrix = np.empty((len(snapshot.constituency_ids), len(PARTIES)), dtype=np.float64)
    for row_index, constituency_id in enumerate(snapshot.constituency_ids):
        for party_index, party in enumerate(PARTIES):
            matrix[row_index, party_index] = snapshot.shares[constituency_id][party]
    return matrix


def eligible_vector(snapshot: SnapshotConstituencies) -> np.ndarray:
    counts = [
        snapshot.eligible_voters[constituency_id]
        for constituency_id in snapshot.constituency_ids
    ]
    return np.array(counts, dtype=np.float64)


def rake_constituency_shares(
    point_matrix: np.ndarray,
    eligible: np.ndarray,
    national_target: np.ndarray,
) -> np.ndarray:
    weights = eligible / eligible.sum()
    result = calibrate_transition_matrix(point_matrix, weights, national_target)
    if not result.converged:
        raise ValueError("Constituency raking did not converge")
    return result.matrix


def allocation_from_share_matrix(
    shares: np.ndarray,
    snapshot: SnapshotConstituencies,
    eligible: np.ndarray,
    fixed_seats: Mapping[str, int],
) -> SeatAllocation:
    votes_by_constituency: dict[str, dict[str, float]] = {}
    for row_index, constituency_id in enumerate(snapshot.constituency_ids):
        votes_by_constituency[constituency_id] = {
            party: float(shares[row_index, party_index] * eligible[row_index])
            for party_index, party in enumerate(PARTIES)
        }
    return allocate_riksdag(votes_by_constituency, fixed_seats)


def overlay_national_draws(
    base: np.ndarray,
    point: np.ndarray,
    sigma: np.ndarray,
    *,
    n_draws: int = DEFAULT_N_DRAWS,
    seed: int = SEAT_OVERLAY_SEED,
) -> np.ndarray:
    """Add election-day error, ten replicates per forecast draw."""
    needed, remainder = divmod(n_draws, SEAT_OVERLAY_REPLICATES)
    if remainder:
        raise ValueError(f"{n_draws} draws is not a multiple of {SEAT_OVERLAY_REPLICATES}")
    if base.shape[0] < needed:
        raise ValueError(f"Need {needed} base draws, found {base.shape[0]}")
    overlaid = apply_overlay(
        base[:needed],
        sigma,
        point,
        seed=seed,
        replicates=SEAT_OVERLAY_REPLICATES,
    )
    if overlaid.shape[0] != n_draws:
        raise ValueError(f"Overlay produced {overlaid.shape[0]} draws, expected {n_draws}")
    return overlaid


def _int_quantile(values: np.ndarray, q: float) -> int:
    return int(np.quantile(values, q, method="nearest"))


def _party_index(party: str) -> int:
    return JSON_PARTIES.index(party)


def summarize_seat_draws(
    draws: np.ndarray,
    national_shares: np.ndarray,
    *,
    majority: int = 175,
) -> dict[str, Any]:
    if draws.shape[1] != len(JSON_PARTIES):
        raise ValueError("Seat draws must follow JSON_PARTIES order")
    if not np.all(draws.sum(axis=1) == TOTAL_RIKSDAG_SEATS):
        raise ValueError("Every seat draw must sum to 349")

    median = {
        party: _int_quantile(draws[:, index], 0.5)
        for index, party in enumerate(JSON_PARTIES)
    }
    low = {
        party: _int_quantile(draws[:, index], 0.025)
        for index, party in enumerate(JSON_PARTIES)
    }
    high = {
        party: _int_quantile(draws[:, index], 0.975)
        for index, party in enumerate(JSON_PARTIES)
    }
    below = {
        party: float((national_shares[:, PARTIES.index(party)] < NATIONAL_THRESHOLD).mean())
        for party in JSON_PARTIES
    }
    coalitions = {
        name: float(
            (draws[:, [_party_index(party) for party in members]].sum(axis=1) >= majority).mean()
        )
        for name, members in COALITIONS.items()
    }
    l_below_mask = national_shares[:, PARTIES.index("L")] < NATIONAL_THRESHOLD
    l_below = _summarize_l_below(draws, l_below_mask, majority)
    return {
        "median_seats": median,
        "low_seats": low,
        "high_seats": high,
        "p_below_threshold": below,
        "coalition_majority": coalitions,
        "l_below": l_below,
    }


def _summarize_l_below(
    draws: np.ndarray,
    mask: np.ndarray,
    majority: int,
) -> dict[str, object]:
    n_below = int(mask.sum())
    if n_below == 0:
        return {
            "n": 0,
            "share": 0.0,
            "mean_seats_when_out": {party: 0.0 for party in JSON_PARTIES},
            "mean_seats_when_in": {
                party: float(draws[:, index].mean()) for index, party in enumerate(JSON_PARTIES)
            },
            "coalition_majority_when_out": {name: None for name in COALITIONS},
            "coalition_majority_when_in": {
                name: float(
                    (
                        draws[:, [_party_index(party) for party in members]].sum(axis=1)
                        >= majority
                    ).mean()
                )
                for name, members in COALITIONS.items()
            },
        }
    out = draws[mask]
    inside = draws[~mask]
    return {
        "n": n_below,
        "share": float(mask.mean()),
        "mean_seats_when_out": {
            party: float(out[:, index].mean()) for index, party in enumerate(JSON_PARTIES)
        },
        "mean_seats_when_in": {
            party: float(inside[:, index].mean()) for index, party in enumerate(JSON_PARTIES)
        },
        "coalition_majority_when_out": {
            name: float(
                (out[:, [_party_index(party) for party in members]].sum(axis=1) >= majority).mean()
            )
            for name, members in COALITIONS.items()
        },
        "coalition_majority_when_in": {
            name: float(
                (
                    inside[:, [_party_index(party) for party in members]].sum(axis=1) >= majority
                ).mean()
            )
            for name, members in COALITIONS.items()
        },
    }


def simulate_seat_draws(
    snapshot: SnapshotConstituencies,
    national_draws: np.ndarray,
    *,
    fixed_seats: Mapping[str, int],
) -> tuple[np.ndarray, list[SeatAllocation]]:
    point_matrix = constituency_share_matrix(snapshot)
    eligible = eligible_vector(snapshot)
    seat_draws = np.zeros((national_draws.shape[0], len(JSON_PARTIES)), dtype=np.int64)
    allocations: list[SeatAllocation] = []
    for draw_index, national in enumerate(national_draws):
        raked = rake_constituency_shares(point_matrix, eligible, national)
        allocation = allocation_from_share_matrix(raked, snapshot, eligible, fixed_seats)
        if allocation.seats.get("OTHER", 0) != 0:
            raise ValueError("OTHER received Riksdag seats; JSON_PARTIES cannot represent that")
        for party_index, party in enumerate(JSON_PARTIES):
            seat_draws[draw_index, party_index] = allocation.seats[party]
        allocations.append(allocation)
    return seat_draws, allocations


def run_point_allocation(
    snapshot: SnapshotConstituencies,
    national_point: np.ndarray,
    fixed_seats: Mapping[str, int],
) -> SeatAllocation:
    point_matrix = constituency_share_matrix(snapshot)
    eligible = eligible_vector(snapshot)
    raked = rake_constituency_shares(point_matrix, eligible, national_point)
    return allocation_from_share_matrix(raked, snapshot, eligible, fixed_seats)


def compute_2026_fixed_seats(snapshot: SnapshotConstituencies) -> dict[str, int]:
    eligible = {
        constituency_id: snapshot.eligible_voters[constituency_id]
        for constituency_id in snapshot.constituency_ids
    }
    seats = allocate_hamilton_seats(eligible, FIXED_CONSTITUENCY_SEATS)
    if sum(seats.values()) != FIXED_CONSTITUENCY_SEATS:
        raise ValueError("2026 Hamilton allocation does not sum to 310")
    return seats

