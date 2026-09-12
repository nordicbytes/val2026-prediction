from __future__ import annotations

from typing import Any

from valforecast.calibration.estimate import (
    HOUSE_PRIOR_STRENGTH,
    apply_house_effects,
    estimate_house_effects,
    l1_error,
    last_poll_errors,
    mean_shares,
)

GATE_CYCLES = (2018, 2022)
GATE_RULE = (
    "For each T in {2018, 2022}, estimate house effects from last polls in "
    "cycles with election year < T, using partial pooling toward zero with "
    f"prior_strength={HOUSE_PRIOR_STRENGTH}. In cycle T, the unadjusted "
    "forecast is the equal-weight mean of last polls. The adjusted forecast "
    "subtracts the shrunk house effect (zero if the institute is unseen). "
    "The score is the L1 distance to the official national result. "
    "SUPPORTED only if the adjusted forecast has strictly smaller L1 in both "
    "2018 and 2022. The rule is not changed after seeing the scores."
)


def run_house_effect_gate(
    last_rows: list[dict[str, Any]],
    results: dict[int, dict[str, float]],
) -> dict[str, Any]:
    errors = last_poll_errors(last_rows, results)
    by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in last_rows:
        by_cycle.setdefault(int(row["election_cycle"]), []).append(row)
    cycle_scores: dict[int, dict[str, Any]] = {}
    improvements: list[bool] = []
    for cycle in GATE_CYCLES:
        houses = estimate_house_effects(errors, max_cycle=cycle)
        current = by_cycle[cycle]
        unadjusted = mean_shares(current)
        adjusted_rows = apply_house_effects(current, houses)
        adjusted = mean_shares(adjusted_rows)
        result = results[cycle]
        unadj_l1 = l1_error(unadjusted, result)
        adj_l1 = l1_error(adjusted, result)
        improved = adj_l1 < unadj_l1
        improvements.append(improved)
        cycle_scores[cycle] = {
            "n_last_polls": len(current),
            "unadjusted_l1": unadj_l1,
            "adjusted_l1": adj_l1,
            "improved": improved,
            "training_institutes": sorted(houses),
        }
    verdict = "SUPPORTED" if all(improvements) else "NOT_SUPPORTED"
    return {
        "rule": GATE_RULE,
        "prior_strength": HOUSE_PRIOR_STRENGTH,
        "cycles": cycle_scores,
        "verdict": verdict,
    }


def leave_one_cycle_out(
    last_rows: list[dict[str, Any]],
    results: dict[int, dict[str, float]],
) -> dict[str, Any]:
    """Diagnostic only. This must not change GATE_RULE or the locked verdict."""
    errors = last_poll_errors(last_rows, results)
    by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in last_rows:
        by_cycle.setdefault(int(row["election_cycle"]), []).append(row)
    scores: dict[int, dict[str, Any]] = {}
    for cycle in sorted(by_cycle):
        held_out = [row for row in errors if int(row["election_cycle"]) != cycle]
        houses = estimate_house_effects(held_out)
        current = by_cycle[cycle]
        unadjusted = mean_shares(current)
        adjusted = mean_shares(apply_house_effects(current, houses))
        result = results[cycle]
        unadj_l1 = l1_error(unadjusted, result)
        adj_l1 = l1_error(adjusted, result)
        scores[cycle] = {
            "n_last_polls": len(current),
            "unadjusted_l1": unadj_l1,
            "adjusted_l1": adj_l1,
            "improved": adj_l1 < unadj_l1,
        }
    return {
        "role": "diagnostic_only_does_not_change_verdict",
        "cycles": scores,
    }
