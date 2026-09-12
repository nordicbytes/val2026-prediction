from __future__ import annotations

import math
from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES

BLOC_LEFT = ("S", "V", "C", "MP")
BLOC_RIGHT = ("M", "SD", "KD", "L")
RIKSDAG_THRESHOLD = 0.04
ERROR_DRAWS_PER_BASE = 10


def _party_index(party: str) -> int:
    return PARTIES.index(party)


def _bloc_totals(draws: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    left = draws[:, [_party_index(party) for party in BLOC_LEFT]].sum(axis=1)
    right = draws[:, [_party_index(party) for party in BLOC_RIGHT]].sum(axis=1)
    return left, right


def monte_carlo_error(probability: float, n_draws: int) -> float:
    """Standard error of a proportion, so a published probability carries its own noise."""
    return math.sqrt(max(probability * (1.0 - probability), 0.0) / float(n_draws))


def summarize_draws(draws: np.ndarray) -> dict[str, Any]:
    left, right = _bloc_totals(draws)
    p_left = float((left > right).mean())
    n_draws = int(draws.shape[0])
    thresholds = {
        party: {
            "p_above": float((draws[:, _party_index(party)] >= RIKSDAG_THRESHOLD).mean()),
            "monte_carlo_se": monte_carlo_error(
                float((draws[:, _party_index(party)] >= RIKSDAG_THRESHOLD).mean()),
                n_draws,
            ),
        }
        for party in PARTIES
        if party != "OTHER"
    }
    return {
        "n_draws": n_draws,
        "bloc": {
            "left": list(BLOC_LEFT),
            "right": list(BLOC_RIGHT),
            "left_mean": float(left.mean()),
            "right_mean": float(right.mean()),
            "p_left_largest": p_left,
            "p_right_largest": float((right > left).mean()),
            "monte_carlo_se": monte_carlo_error(p_left, n_draws),
        },
        "threshold": thresholds,
        "threshold_level": RIKSDAG_THRESHOLD,
    }


def recenter_on_point(draws: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Renormalize to a simplex and shift the mean back onto the locked point."""
    out = np.clip(draws, 1e-8, None)
    out = out / out.sum(axis=1, keepdims=True)
    out = out - out.mean(axis=0, keepdims=True) + point
    out = np.clip(out, 1e-8, None)
    normalized: np.ndarray = out / out.sum(axis=1, keepdims=True)
    return normalized


def apply_overlay(
    base: np.ndarray,
    sigma: np.ndarray,
    point: np.ndarray,
    *,
    seed: int,
    replicates: int = ERROR_DRAWS_PER_BASE,
) -> np.ndarray:
    """Repeat each base draw `replicates` times with independent election-day error.

    The base carries only pollster heterogeneity, so replicating it lets the
    election-day component be estimated with less Monte Carlo noise than the
    2 000 forecast draws would allow on their own.
    """
    rng = np.random.default_rng(seed)
    tiled = np.repeat(base, replicates, axis=0)
    error = rng.multivariate_normal(
        mean=np.zeros(len(PARTIES)),
        cov=sigma,
        size=tiled.shape[0],
    )
    return recenter_on_point(tiled + error, point)


def overlay_probabilities(
    base: np.ndarray,
    point: np.ndarray,
    naive_sigma: np.ndarray,
    decomposed_sigma: np.ndarray,
    *,
    seed: int,
    replicates: int = ERROR_DRAWS_PER_BASE,
) -> dict[str, Any]:
    official = summarize_draws(recenter_on_point(base, point))
    naive = summarize_draws(
        apply_overlay(base, naive_sigma, point, seed=seed, replicates=replicates)
    )
    decomposed = summarize_draws(
        apply_overlay(base, decomposed_sigma, point, seed=seed + 1, replicates=replicates)
    )
    return {
        "seed": seed,
        "replicates": replicates,
        "base": "official_national_draws",
        "defensible": "decomposed",
        "interpretation": (
            "The official set answers how much the five current polls disagree. "
            "Only the decomposed set is a probability about the election result, "
            "and it still omits late opinion movement and turnout uncertainty."
        ),
        "sets": {
            "official": official,
            "naive": naive,
            "decomposed": decomposed,
        },
    }
