from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES
from valforecast.forecast.aggregator import aggregate_polls, draw_poll_targets
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.polls import load_forecast_polls
from valforecast.forecast.snapshot import official_snapshot_path


def load_official_national(root: Path) -> dict[str, Any]:
    path = official_snapshot_path(root)
    if not path.exists():
        raise ValueError("Official 2026 snapshot is missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    national = document["prediction"]["national"]
    if not isinstance(national, dict):
        raise ValueError("Official snapshot is missing national prediction")
    return national


def _apply_overlay(
    current: np.ndarray,
    sigma: np.ndarray,
    official_point: np.ndarray,
    *,
    n_draws: int,
    seed: int,
    level: float,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    election_error = rng.multivariate_normal(
        mean=np.zeros(len(PARTIES)),
        cov=sigma,
        size=n_draws,
    )
    combined = current + election_error
    combined = np.clip(combined, 1e-8, None)
    combined = combined / combined.sum(axis=1, keepdims=True)
    combined = combined - combined.mean(axis=0, keepdims=True) + official_point
    combined = np.clip(combined, 1e-8, None)
    combined = combined / combined.sum(axis=1, keepdims=True)
    combined = combined - combined.mean(axis=0, keepdims=True) + official_point
    tail = (1.0 - level) / 2.0
    return np.quantile(combined, tail, axis=0), np.quantile(combined, 1.0 - tail, axis=0)


def _party_record(
    official: dict[str, Any],
    official_point: np.ndarray,
    naive_low: np.ndarray,
    naive_high: np.ndarray,
    decomposed_low: np.ndarray,
    decomposed_high: np.ndarray,
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for index, party in enumerate(PARTIES):
        comparison[party] = {
            "point": float(official_point[index]),
            "current_low": float(official[party]["low"]),
            "current_high": float(official[party]["high"]),
            "naive_low": float(naive_low[index]),
            "naive_high": float(naive_high[index]),
            "decomposed_low": float(decomposed_low[index]),
            "decomposed_high": float(decomposed_high[index]),
        }
    l_point = comparison["L"]["point"]
    comparison["L"]["threshold_gap"] = {
        "point_minus_4": l_point - 0.04,
        "current_low_minus_4": comparison["L"]["current_low"] - 0.04,
        "naive_low_minus_4": comparison["L"]["naive_low"] - 0.04,
        "decomposed_low_minus_4": comparison["L"]["decomposed_low"] - 0.04,
    }
    return comparison


def overlay_election_day_error(
    root: Path,
    naive_covariance: dict[str, Any],
    decomposed_covariance: dict[str, Any],
    *,
    n_draws: int = 2000,
    seed: int = 20260912,
) -> dict[str, Any]:
    official = load_official_national(root)
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(root, contract)
    aggregated = aggregate_polls(polls, contract, variant="production")
    current = draw_poll_targets(
        aggregated,
        n_draws=n_draws,
        seed=contract.random_seed + 17,
        bootstrap=contract.pollster_bootstrap,
    )
    official_point = np.array([float(official[party]["point"]) for party in PARTIES])
    naive_low, naive_high = _apply_overlay(
        current,
        np.asarray(naive_covariance["sigma"], dtype=float),
        official_point,
        n_draws=n_draws,
        seed=seed,
        level=contract.confidence_level,
    )
    decomposed_low, decomposed_high = _apply_overlay(
        current,
        np.asarray(decomposed_covariance["sigma"], dtype=float),
        official_point,
        n_draws=n_draws,
        seed=seed,
        level=contract.confidence_level,
    )
    return {
        "n_draws": n_draws,
        "seed": seed,
        "point_unchanged": True,
        "defensible": "decomposed",
        "defensible_reason": (
            "The naive overlay adds last-poll residuals on top of Dirichlet "
            "sampling error and treats a five-institute average as if it were "
            "one poll. The decomposed overlay keeps the common election-day "
            "miss and the institute component divided by Kish n_eff, and it "
            "leaves sampling error to Dirichlet."
        ),
        "parties": _party_record(
            official,
            official_point,
            naive_low,
            naive_high,
            decomposed_low,
            decomposed_high,
        ),
    }
