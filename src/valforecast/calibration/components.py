from __future__ import annotations

from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES

DESIGN_EFFECT = 1.0
MISSING_N_FALLBACK = 1000
SMALL_SAMPLE_CYCLES = 6


def sampling_variance(
    share: float,
    sample_size: int | None,
    *,
    fallback: int = MISSING_N_FALLBACK,
) -> float:
    size = float(sample_size if sample_size is not None else fallback)
    bounded = min(max(float(share), 1e-8), 1.0 - 1e-8)
    return DESIGN_EFFECT * bounded * (1.0 - bounded) / size


def institute_median_sample_sizes(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Median published n per institute, used to replace the flat fallback.

    This is an imputation, not a source. It exists because the flat fallback of
    1000 is demonstrably below what these institutes actually field, which
    overstates their sampling error and pushes the institute component down.
    """
    by_family: dict[str, list[int]] = {}
    for row in rows:
        size = row.get("sample_size")
        if size is None:
            continue
        by_family.setdefault(str(row["institute_family"]), []).append(int(size))
    return {
        family: int(np.median(np.array(sizes, dtype=float)))
        for family, sizes in sorted(by_family.items())
    }


def kish_effective_institutes(weights: np.ndarray) -> float:
    normalized = np.asarray(weights, dtype=float)
    if normalized.size == 0:
        raise ValueError("Cannot compute Kish n_eff without weights")
    if abs(float(normalized.sum()) - 1.0) > 1e-9:
        normalized = normalized / normalized.sum()
    return float(1.0 / np.square(normalized).sum())


def _residual_vector(row: dict[str, Any]) -> np.ndarray:
    return np.array([float(row["error"][party]) for party in PARTIES], dtype=float)


def _sampling_vector(
    row: dict[str, Any],
    fallback_sizes: dict[str, int] | None,
) -> np.ndarray:
    shares = row["shares"]
    sample = row.get("sample_size")
    size = int(sample) if sample is not None else None
    fallback = MISSING_N_FALLBACK
    if size is None and fallback_sizes is not None:
        fallback = int(fallback_sizes.get(str(row["institute_family"]), MISSING_N_FALLBACK))
    return np.array(
        [sampling_variance(float(shares[party]), size, fallback=fallback) for party in PARTIES],
        dtype=float,
    )


def _floor_covariance(
    matrix: np.ndarray,
) -> tuple[np.ndarray, list[str]]:
    floored = matrix.copy()
    floored_parties: list[str] = []
    for index, party in enumerate(PARTIES):
        if floored[index, index] < 0.0:
            floored[index, index] = 0.0
            floored_parties.append(party)
    eigenvalues, eigenvectors = np.linalg.eigh(floored)
    if float(eigenvalues.min()) < 0.0:
        eigenvalues = np.clip(eigenvalues, 0.0, None)
        floored = (eigenvectors * eigenvalues) @ eigenvectors.T
        floored = 0.5 * (floored + floored.T)
    return floored, floored_parties


def _sd_pp(matrix: np.ndarray) -> dict[str, float]:
    return {
        party: float(np.sqrt(max(matrix[index, index], 0.0)) * 100.0)
        for index, party in enumerate(PARTIES)
    }


def decompose_error_components(
    errors: list[dict[str, Any]],
    *,
    fallback_sizes: dict[str, int] | None = None,
) -> dict[str, Any]:
    by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in errors:
        by_cycle.setdefault(int(row["election_cycle"]), []).append(row)
    cycles = sorted(by_cycle)
    if len(cycles) < 2:
        raise ValueError("Need at least two cycles for a common error component")
    cycle_means = []
    cycle_sampling = []
    cycle_sizes = []
    within_centered: list[np.ndarray] = []
    within_sampling: list[np.ndarray] = []
    missing_n = 0
    imputed: dict[str, int] = {}
    for cycle in cycles:
        rows = by_cycle[cycle]
        vectors = np.array([_residual_vector(row) for row in rows], dtype=float)
        sampling = np.array([_sampling_vector(row, fallback_sizes) for row in rows], dtype=float)
        missing_n += sum(1 for row in rows if row.get("sample_size") is None)
        for row in rows:
            if row.get("sample_size") is None:
                family = str(row["institute_family"])
                imputed[f"{cycle}:{family}"] = (
                    int((fallback_sizes or {}).get(family, MISSING_N_FALLBACK))
                )
        mean = vectors.mean(axis=0)
        cycle_means.append(mean)
        cycle_sampling.append(sampling.mean(axis=0))
        cycle_sizes.append(len(rows))
        if len(rows) >= 2:
            centered = vectors - mean
            for index, line in enumerate(centered):
                within_centered.append(line)
                within_sampling.append(sampling[index])
    means = np.array(cycle_means, dtype=float)
    n_cycles = means.shape[0]
    n_parties = len(PARTIES)
    common_raw = ((means - means.mean(axis=0)).T @ (means - means.mean(axis=0))) / (
        n_cycles - 1
    )
    if not within_centered:
        raise ValueError("Need a cycle with at least two last polls")
    within = np.array(within_centered, dtype=float)
    n_within = within.shape[0]
    institute_raw = (within.T @ within) / (n_within - 1)
    sampling_diag = np.diag(np.mean(np.array(within_sampling, dtype=float), axis=0))
    institute, institute_floored = _floor_covariance(institute_raw - sampling_diag)
    mean_noise = np.zeros((n_parties, n_parties), dtype=float)
    for size, sampling_mean in zip(cycle_sizes, cycle_sampling, strict=True):
        mean_noise += (institute + np.diag(sampling_mean)) / float(size)
    mean_noise = mean_noise / float(len(cycle_sizes))
    common, common_floored = _floor_covariance(common_raw - mean_noise)
    return {
        "n_cycles": n_cycles,
        "n_last_polls": len(errors),
        "n_within": n_within,
        "missing_n_fallback": MISSING_N_FALLBACK,
        "missing_n_count": missing_n,
        "fallback_mode": "institute_median" if fallback_sizes else "flat_1000",
        "imputed_sample_sizes": imputed,
        "design_effect": DESIGN_EFFECT,
        "small_sample_caveat": (
            f"The common component is estimated from {n_cycles} cycle-mean "
            "residual vectors with a Bessel correction (divide by C-1) and "
            "with the expected noise of those means subtracted. Six cycles "
            "is a small sample; the common variance is noisy."
        ),
        "sampling_removed_from_overlay": True,
        "floored": {
            "institute": institute_floored,
            "common": common_floored,
        },
        "sd_percentage_points": {
            "common": _sd_pp(common),
            "institute": _sd_pp(institute),
            "sampling": _sd_pp(sampling_diag),
        },
        "sigma_common": common,
        "sigma_institute": institute,
        "sigma_sampling": sampling_diag,
        "sigma_common_raw": common_raw,
        "sigma_institute_raw": institute_raw,
    }


def production_overlay_covariance(
    components: dict[str, Any],
    weights: np.ndarray,
) -> dict[str, Any]:
    n_eff = kish_effective_institutes(weights)
    sigma = components["sigma_common"] + components["sigma_institute"] / n_eff
    sigma, floored = _floor_covariance(sigma)
    return {
        "formula": "sigma_2026 = Sigma_common + Sigma_institute / n_eff",
        "n_eff_formula": "n_eff = 1 / sum_i w_i^2",
        "n_eff": n_eff,
        "n_institutes": int(np.asarray(weights).size),
        "weights": [float(value) for value in np.asarray(weights, dtype=float)],
        "floored_parties": floored,
        "sigma": sigma,
        "sd_percentage_points": _sd_pp(sigma),
    }
