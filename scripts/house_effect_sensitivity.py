"""What the forecast would look like with institute-specific corrections.

This is a sensitivity run, not a production path. The official forecast sets
``poll_aggregation.house_effects: none``, and this script does not change it:
it reads the same locked contract, the same five polls and the shrunk house
effects from the calibration lock, then runs the identical pipeline a second
time on corrected poll shares and reports the two side by side.

Everything downstream is shared with production: the same district universe,
the same transition kernel and seed, the same election-day error covariance,
and the same constituency pattern for the seat raking. Only the poll shares
that enter the aggregate differ.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from valforecast.calibration.probabilities import apply_overlay
from valforecast.features.election_history import PARTIES
from valforecast.forecast.aggregator import AggregatedPolls, aggregate_polls
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.kernel import estimate_2026_kernel
from valforecast.forecast.polls import load_forecast_polls
from valforecast.forecast.produce import _national_from_polls, run_forecast_2026
from valforecast.forecast.universe import build_forecast_universe
from valforecast.seats.data import load_official_fixed_seats_2026, load_snapshot_constituencies
from valforecast.seats.simulate import (
    SEAT_OVERLAY_SEED,
    overlay_national_draws,
    run_point_allocation,
    simulate_seat_draws,
    summarize_seat_draws,
)

CALIBRATION_LOCK = Path("reports/poll_calibration/estimator_lock.json")
OVERLAY_SIGMA_KEY = "production_overlay_covariance"
N_OVERLAY_DRAWS = 20000
LEFT = ("S", "V", "C", "MP")
RIGHT = ("M", "SD", "KD", "L")
THRESHOLD = 0.04

# The five included polls carry these pollster labels; the calibration corpus
# keys house effects by institute family. Indikator Opinion has no final poll
# in any cycle from 2002 to 2022, so it is an unseen institute and the
# pre-registered gate rule assigns it a zero correction.
FAMILY_BY_POLLSTER = {
    "Verian": "Sifo/Verian",
    "Novus": "Novus",
    "Demoskop": "Demoskop",
    "Ipsos": "Ipsos",
    "Indikator Opinion": None,
}


def load_lock(root: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((root / CALIBRATION_LOCK).read_text(encoding="utf-8"))
    return document


def house_effects(lock: dict[str, Any]) -> dict[str, dict[str, float]]:
    return {
        family: {party: float(values["shrunk"][party]) for party in PARTIES}
        for family, values in lock["house_effects"].items()
    }


def effect_for(pollster: str, effects: dict[str, dict[str, float]]) -> dict[str, float]:
    """The correction for one pollster; all zeroes for an institute with no history."""
    family = FAMILY_BY_POLLSTER.get(pollster)
    return effects[family] if family else dict.fromkeys(PARTIES, 0.0)


def correct_shares(shares: dict[str, float], effect: dict[str, float]) -> dict[str, float]:
    """Subtract the institute's estimated skew, then renormalise to one."""
    corrected = np.array([shares[party] - effect[party] for party in PARTIES], dtype=float)
    corrected = np.clip(corrected, 1e-6, None)
    corrected = corrected / corrected.sum()
    return dict(zip(PARTIES, (float(value) for value in corrected), strict=True))


def bloc_probability(draws: np.ndarray, left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_sum = draws[:, [PARTIES.index(party) for party in left]].sum(axis=1)
    right_sum = draws[:, [PARTIES.index(party) for party in right]].sum(axis=1)
    return float((left_sum > right_sum).mean())


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(root, contract)
    lock = load_lock(root)
    effects = house_effects(lock)

    official = run_forecast_2026(root, contract)
    baseline: AggregatedPolls = official.aggregated

    corrected_polls = []
    for poll in polls:
        effect = effect_for(poll.pollster, effects)
        corrected_polls.append(replace(poll, shares=correct_shares(poll.shares, effect)))
    adjusted_agg = aggregate_polls(corrected_polls, contract, variant="production")
    if [poll.poll_id for poll in adjusted_agg.included] != [
        poll.poll_id for poll in baseline.included
    ]:
        raise ValueError("Correcting the shares changed which polls are included")

    universe = build_forecast_universe(root)
    kernel = estimate_2026_kernel(
        root,
        universe.national_previous,
        n_draws=contract.draws,
        seed=contract.random_seed,
    )
    adjusted_point, adjusted_draws, adjusted_districts, _, _ = _national_from_polls(
        universe,
        kernel,
        adjusted_agg,
        contract,
        seed_offset=17,
    )

    print("=== VIKTER OCH HUSFAKTOR SOM DRAS BORT (pp) ===")
    header = "Institut".ljust(19) + "vikt".rjust(7) + "".join(p.rjust(7) for p in PARTIES)
    print(header)
    for poll, weight in zip(baseline.included, baseline.weights, strict=True):
        effect = effect_for(poll.pollster, effects)
        line = poll.pollster.ljust(19) + f"{weight * 100:.1f} %".rjust(7)
        line += "".join(f"{effect[party] * 100:+.2f}".rjust(7) for party in PARTIES)
        print(line)

    sigma = np.asarray(lock[OVERLAY_SIGMA_KEY]["sigma"], dtype=np.float64)
    official_overlay = apply_overlay(
        official.national_draws[: N_OVERLAY_DRAWS // 10],
        sigma,
        official.national_point,
        seed=SEAT_OVERLAY_SEED,
        replicates=10,
    )
    adjusted_overlay = apply_overlay(
        adjusted_draws[: N_OVERLAY_DRAWS // 10],
        sigma,
        adjusted_point,
        seed=SEAT_OVERLAY_SEED,
        replicates=10,
    )

    print("\n=== NATIONELLT, PROCENT AV GILTIGA ROSTER ===")
    print("Parti  officiell            justerad             skillnad")
    for index, party in enumerate(PARTIES):
        off_lo, off_hi = np.quantile(official_overlay[:, index], [0.025, 0.975])
        adj_lo, adj_hi = np.quantile(adjusted_overlay[:, index], [0.025, 0.975])
        print(
            f"{party:6} {official.national_point[index] * 100:5.2f} "
            f"({off_lo * 100:4.1f}-{off_hi * 100:4.1f})   "
            f"{adjusted_point[index] * 100:5.2f} ({adj_lo * 100:4.1f}-{adj_hi * 100:4.1f})   "
            f"{(adjusted_point[index] - official.national_point[index]) * 100:+5.2f}"
        )

    print("\n=== BLOCK OCH SPARR ===")
    for name, draws, point in (
        ("officiell", official_overlay, official.national_point),
        ("justerad", adjusted_overlay, adjusted_point),
    ):
        left = sum(point[PARTIES.index(party)] for party in LEFT) * 100
        right = sum(point[PARTIES.index(party)] for party in RIGHT) * 100
        print(
            f"{name:10} S+V+C+MP {left:5.2f}  M+SD+KD+L {right:5.2f}  "
            f"ledning {left - right:+5.2f}  "
            f"P(vanster fler roster) {bloc_probability(draws, LEFT, RIGHT) * 100:5.1f} %  "
            f"P(L over 4 %) {(draws[:, PARTIES.index('L')] >= THRESHOLD).mean() * 100:5.1f} %  "
            f"P(MP over) {(draws[:, PARTIES.index('MP')] >= THRESHOLD).mean() * 100:5.1f} %"
        )

    snapshot = load_snapshot_constituencies(root)
    fixed = load_official_fixed_seats_2026(root)
    results = {}
    for name, point, base in (
        ("officiell", official.national_point, official.national_draws),
        ("justerad", adjusted_point, adjusted_draws),
    ):
        overlaid = overlay_national_draws(
            base, point, sigma, n_draws=N_OVERLAY_DRAWS, seed=SEAT_OVERLAY_SEED
        )
        allocation = run_point_allocation(snapshot, point, fixed)
        draws, _ = simulate_seat_draws(snapshot, overlaid, fixed_seats=fixed)
        results[name] = {
            "point": {party: int(allocation.seats[party]) for party in allocation.seats},
            "summary": summarize_seat_draws(draws, overlaid, majority=175),
        }
        print(f"\n[{name}] mandatsimulering klar")

    print("\n=== MANDAT, PUNKTPROGNOS ===")
    print("Parti  officiell  justerad  skillnad")
    for party in ("V", "S", "MP", "C", "L", "M", "KD", "SD"):
        off = results["officiell"]["point"][party]
        adj = results["justerad"]["point"][party]
        print(f"{party:6} {off:9d}  {adj:8d}  {adj - off:+8d}")

    print("\n=== EGEN MAJORITET, ANDEL AV 20 000 SIMULERINGAR ===")
    print("Underlag".ljust(16) + "officiell".rjust(11) + "justerad".rjust(11))
    off_coalitions = results["officiell"]["summary"]["coalition_majority"]
    adj_coalitions = results["justerad"]["summary"]["coalition_majority"]
    for name in off_coalitions:
        print(
            name.ljust(16)
            + f"{off_coalitions[name] * 100:9.1f} %"
            + f"{adj_coalitions[name] * 100:9.1f} %"
        )

    def variant_payload(
        label: str,
        point: np.ndarray,
        base: np.ndarray,
        overlay: np.ndarray,
        seats: dict[str, Any],
    ) -> dict[str, Any]:
        narrow_low, narrow_high = np.quantile(base, [0.025, 0.975], axis=0)
        wide_low, wide_high = np.quantile(overlay, [0.025, 0.975], axis=0)
        left = float(sum(point[PARTIES.index(party)] for party in LEFT))
        right = float(sum(point[PARTIES.index(party)] for party in RIGHT))
        return {
            "label": label,
            "parties": {
                party: {
                    "point": round(float(point[index]) * 100, 2),
                    "low": round(float(narrow_low[index]) * 100, 2),
                    "high": round(float(narrow_high[index]) * 100, 2),
                    "calLow": round(float(wide_low[index]) * 100, 2),
                    "calHigh": round(float(wide_high[index]) * 100, 2),
                    "pAbove": round(
                        float((overlay[:, index] >= THRESHOLD).mean()),
                        5,
                    ),
                }
                for index, party in enumerate(PARTIES)
            },
            "blocs": {
                "left": round(left * 100, 2),
                "right": round(right * 100, 2),
                "pLeftMoreVotes": round(bloc_probability(overlay, LEFT, RIGHT), 5),
            },
            "seats": seats["point"],
            "seatLow": seats["summary"]["low_seats"],
            "seatHigh": seats["summary"]["high_seats"],
            "coalitionMajority": {
                name: round(value, 5)
                for name, value in seats["summary"]["coalition_majority"].items()
            },
        }

    payload = {
        "role": "sensitivity_only_not_production",
        "method": {
            "house_pooling": lock["method"]["house_pooling"],
            "house_prior_strength": lock["method"]["house_prior_strength"],
            "gate_verdict": lock["gate"]["verdict"],
            "unseen_institute": "Indikator Opinion",
            "n_overlay_draws": N_OVERLAY_DRAWS,
        },
        "effects": {
            poll.pollster: {
                "family": FAMILY_BY_POLLSTER.get(poll.pollster) or None,
                "weight": round(float(weight), 5),
                "shrunk_pp": {
                    party: round(effect_for(poll.pollster, effects)[party] * 100, 2)
                    for party in PARTIES
                },
            }
            for poll, weight in zip(baseline.included, baseline.weights, strict=True)
        },
        "official": variant_payload(
            "Officiell prognos",
            official.national_point,
            official.national_draws,
            official_overlay,
            results["officiell"],
        ),
        "adjusted": variant_payload(
            "Med institutskorrigering",
            adjusted_point,
            adjusted_draws,
            adjusted_overlay,
            results["justerad"],
        ),
    }
    out = root / "reports" / "forecast_2026" / "house_effect_sensitivity.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nskrev {out.relative_to(root)}")
    assert adjusted_districts.shape[0] == universe.eligible_weights.shape[0]


if __name__ == "__main__":
    main()
