"""Run the 2026 seat simulation and write site/data/seats.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES
from valforecast.forecast.produce import ForecastResult, run_forecast_2026
from valforecast.seats.allocate import SeatAllocation
from valforecast.seats.data import (
    ESTIMATOR_LOCK,
    JSON_PARTIES,
    load_official_fixed_seats_2026,
    load_production_overlay_sigma,
    load_snapshot_constituencies,
)
from valforecast.seats.method import TOTAL_RIKSDAG_SEATS
from valforecast.seats.simulate import (
    DEFAULT_N_DRAWS,
    SEAT_OVERLAY_REPLICATES,
    SEAT_OVERLAY_SEED,
    compute_2026_fixed_seats,
    overlay_national_draws,
    run_point_allocation,
    simulate_seat_draws,
    summarize_seat_draws,
)

SEATS_JSON = Path("site/data/seats.json")
MAJORITY = 175


def fixed_seats_for_2026_simulation(root: Path) -> dict[str, int]:
    """The 2026 simulation uses Valmyndigheten's published 2026 column."""
    return load_official_fixed_seats_2026(root)


def _party_seats(allocation: SeatAllocation) -> dict[str, int]:
    return {party: int(allocation.seats[party]) for party in JSON_PARTIES}


def build_seats_document(
    root: Path,
    *,
    forecast: ForecastResult | None = None,
    n_draws: int = DEFAULT_N_DRAWS,
) -> dict[str, Any]:
    snapshot = load_snapshot_constituencies(root)
    official_fixed = fixed_seats_for_2026_simulation(root)
    august_fixed = compute_2026_fixed_seats(snapshot)
    if sum(official_fixed.values()) != 310 or len(official_fixed) != 29:
        raise ValueError("Official 2026 fixed seats must be 310 over 29 constituencies")
    forecast = forecast or run_forecast_2026(root)
    sigma = np.asarray(load_production_overlay_sigma(root), dtype=np.float64)
    national = overlay_national_draws(
        forecast.national_draws,
        forecast.national_point,
        sigma,
        n_draws=n_draws,
        seed=SEAT_OVERLAY_SEED,
    )
    point = run_point_allocation(snapshot, forecast.national_point, official_fixed)
    draws, _allocations = simulate_seat_draws(snapshot, national, fixed_seats=official_fixed)
    summary = summarize_seat_draws(draws, national, majority=MAJORITY)
    august_point = run_point_allocation(snapshot, forecast.national_point, august_fixed)
    august_draws, _august_allocations = simulate_seat_draws(
        snapshot, national, fixed_seats=august_fixed
    )
    august_summary = summarize_seat_draws(august_draws, national, majority=MAJORITY)
    if not np.all(draws.sum(axis=1) == TOTAL_RIKSDAG_SEATS):
        raise ValueError("A simulated chamber does not sum to 349")
    point_seats = _party_seats(point)
    if sum(point_seats.values()) != TOTAL_RIKSDAG_SEATS:
        raise ValueError("Point forecast seats do not sum to 349")
    differing = {
        snapshot.names[constituency_id]: {
            "official": official_fixed[constituency_id],
            "august": august_fixed[constituency_id],
        }
        for constituency_id in snapshot.constituency_ids
        if official_fixed[constituency_id] != august_fixed[constituency_id]
    }
    return {
        "schema_version": 1,
        "generated_from": str(ESTIMATOR_LOCK).replace("\\", "/"),
        "n_draws": int(n_draws),
        "majority": MAJORITY,
        "total_seats": TOTAL_RIKSDAG_SEATS,
        "parties": list(JSON_PARTIES),
        "point_seats": point_seats,
        "median_seats": summary["median_seats"],
        "low_seats": summary["low_seats"],
        "high_seats": summary["high_seats"],
        "p_below_threshold": summary["p_below_threshold"],
        "draws": draws.astype(int).tolist(),
        "diagnostics": {
            "overlay_seed": SEAT_OVERLAY_SEED,
            "overlay_replicates": SEAT_OVERLAY_REPLICATES,
            "fixed_seats_source": "valmyndigheten_official_2026_column",
            "official_fixed_seats_2026": {
                snapshot.names[constituency_id]: official_fixed[constituency_id]
                for constituency_id in snapshot.constituency_ids
            },
            "august_hamilton_fixed_seats_2026": {
                snapshot.names[constituency_id]: august_fixed[constituency_id]
                for constituency_id in snapshot.constituency_ids
            },
            "fixed_seat_differences": differing,
            "august_sensitivity": {
                "point_seats": _party_seats(august_point),
                "median_seats": august_summary["median_seats"],
                "low_seats": august_summary["low_seats"],
                "high_seats": august_summary["high_seats"],
                "p_below_threshold": august_summary["p_below_threshold"],
                "coalition_majority": august_summary["coalition_majority"],
            },
            "coalition_majority": summary["coalition_majority"],
            "l_below": summary["l_below"],
            "point_fixed_seats": point.fixed_seats,
            "point_adjustment_seats": point.adjustment_seats,
            "point_overrepresented": list(point.excluded_overrepresented),
            "twelve_percent_constituencies": [
                list(item) for item in point.twelve_percent_constituencies
            ],
            "p_other_residual_ge_threshold": float(
                (national[:, PARTIES.index("OTHER")] >= 0.04).mean()
            ),
        },
    }


def write_seats_json(path: Path, document: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    draws = document["draws"]
    public = {key: value for key, value in document.items() if key not in {"draws", "diagnostics"}}
    head = json.dumps(public, ensure_ascii=False, indent=2)
    draws_json = json.dumps(draws, ensure_ascii=False, separators=(",", ":"))
    path.write_text(head[:-2] + ',\n  "draws": ' + draws_json + "\n}\n", encoding="utf-8")
    return path


def run_seat_simulation(root: Path, *, n_draws: int = DEFAULT_N_DRAWS) -> dict[str, Any]:
    document = build_seats_document(root, n_draws=n_draws)
    write_seats_json(root / SEATS_JSON, document)
    return document
