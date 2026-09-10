from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CalibrationResult:
    matrix: np.ndarray
    iterations: int
    converged: bool
    maximum_margin_error: float
    kl_divergence: float


def calibrate_transition_matrix(
    matrix: np.ndarray,
    previous_national: np.ndarray,
    target_national: np.ndarray,
    *,
    tolerance: float = 1e-10,
    max_iterations: int = 10_000,
    epsilon: float = 1e-12,
) -> CalibrationResult:
    transition = np.asarray(matrix, dtype=float).copy()
    previous = _normalize(previous_national)
    target = _normalize(target_national)
    if transition.shape != (previous.size, target.size):
        raise ValueError("Transition matrix dimensions do not match party vectors")
    if np.any(transition < 0):
        raise ValueError("Transition matrix cannot contain negative probabilities")
    transition = np.maximum(transition, epsilon)
    transition /= transition.sum(axis=1, keepdims=True)
    original = transition.copy()
    converged = False
    maximum_error = float("inf")
    iterations = 0
    for _ in range(max_iterations):
        iterations += 1
        current = previous @ transition
        if np.any(current <= 0):
            raise ValueError("Calibration encountered an empty target column")
        transition *= target / current
        transition /= transition.sum(axis=1, keepdims=True)
        maximum_error = float(np.max(np.abs(previous @ transition - target)))
        if maximum_error <= tolerance:
            converged = True
            break
    kl_divergence = float(
        np.sum(previous[:, None] * transition * np.log(transition / original))
    )
    return CalibrationResult(
        matrix=transition,
        iterations=iterations,
        converged=converged,
        maximum_margin_error=maximum_error,
        kl_divergence=kl_divergence,
    )


def _normalize(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1 or np.any(vector < 0) or vector.sum() <= 0:
        raise ValueError("Expected a non-negative one-dimensional party vector")
    return np.asarray(vector / vector.sum(), dtype=float)
