from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from valforecast.calibration.audit import audit_level_c_rows, sample_level_c_rows
from valforecast.calibration.components import (
    decompose_error_components,
    institute_median_sample_sizes,
    production_overlay_covariance,
)
from valforecast.calibration.corpus import (
    build_poll_corpus,
    internal_row_checks,
    last_polls,
)
from valforecast.calibration.elections import load_official_national_results
from valforecast.calibration.estimate import (
    HOUSE_PRIOR_STRENGTH,
    error_covariance,
    estimate_house_effects,
    last_poll_errors,
    recency_curve,
)
from valforecast.calibration.gate import (
    GATE_CYCLES,
    GATE_RULE,
    leave_one_cycle_out,
    run_house_effect_gate,
)
from valforecast.calibration.intervals import overlay_election_day_error
from valforecast.calibration.lock import file_checksums, write_calibration_lock
from valforecast.calibration.probabilities import overlay_probabilities
from valforecast.calibration.quality import house_effect_source_quality, unverified_share
from valforecast.features.election_history import PARTIES
from valforecast.forecast.aggregator import aggregate_polls
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.polls import load_forecast_polls
from valforecast.forecast.produce import run_forecast_2026

HISTORY_FILES = [
    "data/raw/polls/history/wikipedia_en_polling_2022.html",
    "data/raw/polls/history/wikipedia_en_polling_2018.html",
    "data/raw/polls/history/wikipedia_en_polling_2014.html",
    "data/raw/polls/history/wikipedia_sv_polling_2010.html",
    "data/raw/polls/history/temo_valjarbarometer_2006.html",
    "data/raw/polls/history/temo_valjarbarometer_2002.html",
    "data/raw/polls/history/val_national_2022.html",
    "data/raw/polls/history/val_national_2018.html",
    "data/raw/polls/history/val_national_2014.html",
    "data/raw/polls/history/val_national_2010.html",
    "data/raw/polls/history/val_national_2006.html",
    "data/raw/polls/history/sample_sizes/mansmeg_swedishpolls.csv",
    "data/raw/polls/history/sample_sizes/inizio_2018_8_sep.html",
    "data/raw/polls/history/sample_sizes/yougov_2018_sista.html",
    "data/raw/polls/history/sample_sizes/yougov_2018_metro.pdf",
    "data/raw/polls/history/sample_sizes/ipsos_2018_augusti.pdf",
    "data/raw/polls/history/sample_sizes/novus_2018_30_aug.html",
    "data/raw/polls/history/sample_sizes/svd_2018_demoskop.html",
    "data/raw/polls/history/sample_sizes/aftonbladet_2018_skop.html",
    "data/raw/polls/history/sample_sizes/aftonbladet_2018_sifo.html",
    "data/raw/polls/history/sample_sizes/omni_2022_sifo.html",
]


def _level_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"A": 0, "B": 0, "C": 0}
    for row in rows:
        counts[str(row["source_level"])] += 1
    return counts


def _cycle_coverage(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    coverage: dict[int, dict[str, Any]] = {}
    for row in rows:
        cycle = int(row["election_cycle"])
        bucket = coverage.setdefault(
            cycle,
            {"n": 0, "families": set(), "levels": {"A": 0, "B": 0, "C": 0}},
        )
        bucket["n"] += 1
        bucket["families"].add(str(row["institute_family"]))
        bucket["levels"][str(row["source_level"])] += 1
    return {
        cycle: {
            "n": item["n"],
            "families": sorted(item["families"]),
            "levels": item["levels"],
        }
        for cycle, item in sorted(coverage.items())
    }


def _matrix(matrix: np.ndarray) -> list[list[float]]:
    return [[float(value) for value in line] for line in matrix.tolist()]


def _serialize_components(components: dict[str, Any]) -> dict[str, Any]:
    payload = dict(components)
    for key in (
        "sigma_common",
        "sigma_institute",
        "sigma_sampling",
        "sigma_common_raw",
        "sigma_institute_raw",
    ):
        payload[key] = _matrix(np.asarray(components[key]))
    return payload


def _serialize_production(production: dict[str, Any]) -> dict[str, Any]:
    payload = dict(production)
    payload["sigma"] = _matrix(np.asarray(production["sigma"]))
    return payload


def _gate_diagnostics(
    lasts: list[dict[str, Any]],
    results: dict[int, dict[str, float]],
    gate: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    scores_2018 = gate["cycles"][2018]
    margin = float(scores_2018["unadjusted_l1"]) - float(scores_2018["adjusted_l1"])
    relative = margin / float(scores_2018["unadjusted_l1"])
    training = [row for row in lasts if int(row["election_cycle"]) < 2018]
    evaluation = [row for row in lasts if int(row["election_cycle"]) == 2018]
    return {
        "does_not_change_verdict": True,
        "t2018_l1_margin": margin,
        "t2018_relative_improvement": relative,
        "training_before_2018": unverified_share(training, audit),
        "evaluation_2018": unverified_share(evaluation, audit),
        "contributing_2018": unverified_share([*training, *evaluation], audit),
        "leave_one_cycle_out": leave_one_cycle_out(lasts, results),
        "gate_cycles": list(GATE_CYCLES),
    }


def run_poll_calibration(root: Path) -> dict[str, Any]:
    results = load_official_national_results(root)
    rows = build_poll_corpus(root)
    lasts = last_polls(rows)
    issues = internal_row_checks(rows)
    errors = last_poll_errors(lasts, results)
    houses = estimate_house_effects(errors)
    naive = error_covariance(errors)
    median_sizes = institute_median_sample_sizes(rows)
    components = decompose_error_components(errors, fallback_sizes=median_sizes)
    flat_components = decompose_error_components(errors)
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(root, contract)
    aggregated = aggregate_polls(polls, contract, variant="production")
    production = production_overlay_covariance(components, aggregated.weights)
    flat_production = production_overlay_covariance(flat_components, aggregated.weights)
    sampled = sample_level_c_rows(rows)
    audit = audit_level_c_rows(root, sampled)
    gate = run_house_effect_gate(lasts, results)
    gate["diagnostics"] = _gate_diagnostics(lasts, results, gate, audit)
    forecast = run_forecast_2026(root, contract)
    base = forecast.national_draws
    intervals = overlay_election_day_error(
        root,
        base,
        {"sigma": naive["sigma"]},
        production,
    )
    probabilities = overlay_probabilities(
        base,
        forecast.national_point,
        np.asarray(naive["sigma"], dtype=float),
        np.asarray(production["sigma"], dtype=float),
        seed=20260912,
    )
    flat_intervals = overlay_election_day_error(
        root,
        base,
        {"sigma": naive["sigma"]},
        flat_production,
    )
    flat_probabilities = overlay_probabilities(
        base,
        forecast.national_point,
        np.asarray(naive["sigma"], dtype=float),
        np.asarray(flat_production["sigma"], dtype=float),
        seed=20260912,
    )
    recency = recency_curve(rows, results)
    document: dict[str, Any] = {
        "schema_version": 1,
        "stage": "historical_poll_calibration",
        "not_production_input": True,
        "production_aggregator_still_forbids_secondary_shares": True,
        "production_sources_manifest": "config/sources.yaml",
        "calibration_sources_manifest": "config/sources_calibration.yaml",
        "method": {
            "window_days": 30,
            "house_prior_strength": HOUSE_PRIOR_STRENGTH,
            "house_pooling": "partial_pooling_toward_zero",
            "error_covariance": "last_poll_residual_shrunk_sample_covariance",
            "error_components": "common_plus_institute_over_kish_n_eff",
            "gate_rule": GATE_RULE,
            "parties": list(PARTIES),
        },
        "coverage": {
            "n_rows": len(rows),
            "n_last_polls": len(lasts),
            "levels": _level_counts(rows),
            "cycles": _cycle_coverage(rows),
        },
        "internal_issues": issues,
        "audit": audit,
        "house_effects": houses,
        "house_effect_source_quality": house_effect_source_quality(lasts, houses, audit),
        "final_poll_error": {
            "n_obs": naive["n_obs"],
            "shrinkage": naive["shrinkage"],
            "sd_percentage_points": naive["sd_percentage_points"],
            "mean_error": naive["mean_error"],
            "sigma": _matrix(np.asarray(naive["sigma"])),
        },
        "error_components": _serialize_components(components),
        "production_overlay_covariance": _serialize_production(production),
        "recency_curve": recency,
        "gate": gate,
        "intervals_2026_overlay": intervals,
        "probabilities_2026": probabilities,
        "sensitivity_flat_fallback_n": {
            "why": (
                "Three of 34 last polls still publish no sample size. The "
                "headline numbers impute each institute's own median n, "
                "because the flat fallback of 1000 is below what SKOP, Novus "
                "and Sifo actually field and therefore overstates their "
                "sampling error. The imputation is an active choice and can "
                "be wrong; this branch keeps the flat fallback so the effect "
                "of that choice is visible."
            ),
            "imputed_sample_sizes": components["imputed_sample_sizes"],
            "institute_medians": median_sizes,
            "error_components": _serialize_components(flat_components),
            "production_overlay_covariance": _serialize_production(flat_production),
            "intervals_2026_overlay": flat_intervals,
            "probabilities_2026": flat_probabilities,
        },
        "raw_sha256": file_checksums(root, HISTORY_FILES),
        "suggested_contract_diff": [
            "poll_aggregation.house_effects: none -> empirical_partial_pool_if_gate_supported",
            "uncertainty.poll_uncertainty: keep pollster_bootstrap_then_dirichlet",
            "uncertainty.election_day_error: add common_plus_institute_over_kish_n_eff",
            "Do not use the naive last-poll residual covariance in production",
            "Do not change this in the present round because the contract hash is frozen",
        ],
    }
    if gate["verdict"] != "SUPPORTED":
        document["suggested_contract_diff"] = [
            "No production activation. Keep poll_aggregation.house_effects: none.",
            "Optional later addition if a new gate is pre-registered: "
            "uncertainty.election_day_error from the decomposed covariance.",
        ]
    write_calibration_lock(root, document)
    return document
