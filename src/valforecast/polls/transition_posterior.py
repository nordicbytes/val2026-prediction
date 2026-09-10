from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal, cast

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import POLL_CURRENT_CATEGORIES, validate_transition_cells
from valforecast.polls.transition_calibrate import CalibrationResult, calibrate_transition_matrix

Z_VALUE = 1.96
N_EFF_LOWER_BOUND = 1.0
N_EFF_LABEL = "approximate_margin_inverted"
DEFAULT_SEED = 20260911
DEFAULT_DRAWS = 2000
STATED_PARTY_INDEX = tuple(POLL_CURRENT_CATEGORIES.index(party) for party in PARTIES)
SuppressionStrategy = Literal["flat", "historical_origin", "zero_renormalize"]

_EXCLUDED_ZERO_ESTIMATE = "zero_estimate"
_EXCLUDED_ZERO_MARGIN = "zero_margin"
_EXCLUDED_MISSING_ESTIMATE = "missing_estimate"
_EXCLUDED_MISSING_MARGIN = "missing_margin"
_EXCLUDED_UNIT_ESTIMATE = "unit_estimate"


@dataclass(frozen=True)
class EffectiveSampleSizeResult:
    """Approximate row n_eff inverted from published margins. Not Kish ESS."""

    wave_id: str
    n_eff_label: str
    z_value: float
    wave_median_design_effect: float | None
    rows: pl.DataFrame
    cells: pl.DataFrame

    @property
    def is_kish_ess(self) -> bool:
        return False

    def vector(self) -> np.ndarray:
        by_party = dict(self.rows.select("previous_party", "n_eff").iter_rows())
        missing = [party for party in PARTIES if party not in by_party]
        if missing:
            raise ValueError(f"Effective-n result is missing previous parties: {missing}")
        return np.array([float(by_party[party]) for party in PARTIES], dtype=float)


@dataclass(frozen=True)
class TransitionPointEstimate:
    wave_id: str
    suppression_strategy: SuppressionStrategy
    categories: tuple[str, ...]
    full_category_point: np.ndarray
    full_category_center: np.ndarray
    stated_party_point: np.ndarray
    diagnostics: pl.DataFrame


@dataclass(frozen=True)
class TransitionDraws:
    wave_id: str
    seed: int
    n_draws: int
    estimator: str
    n_eff_label: str
    full_category_draws: np.ndarray
    stated_party_draws: np.ndarray
    point_full_category: np.ndarray
    point_stated_party: np.ndarray
    n_eff: np.ndarray
    dirichlet_alpha: np.ndarray


@dataclass(frozen=True)
class RakedTransitionDraws:
    wave_id: str
    seed: int
    n_draws: int
    estimator: str
    raked_draws: np.ndarray
    raked_point_matrix: np.ndarray
    mean_raked_draws: np.ndarray
    calibration_diagnostics: pl.DataFrame
    row_simplex_ok: bool
    national_reconciliation_ok: bool


@dataclass(frozen=True)
class SurveyTransitionEstimate:
    n_eff: EffectiveSampleSizeResult
    point: TransitionPointEstimate
    draws: TransitionDraws
    raked: RakedTransitionDraws | None


def approximate_cell_n_eff(
    estimate: float,
    margin_error: float,
    *,
    z_value: float = Z_VALUE,
) -> float:
    if not isfinite(estimate) or not isfinite(margin_error):
        raise ValueError("Cell n_eff requires a finite estimate and margin")
    if estimate <= 0 or estimate >= 1 or margin_error <= 0:
        raise ValueError("Cell n_eff is undefined for zero/unit estimates or non-positive margins")
    if z_value <= 0:
        raise ValueError("z-value must be positive")
    standard_error = margin_error / z_value
    return float(estimate * (1.0 - estimate) / standard_error**2)


def infer_row_effective_sample_sizes(
    cells: pl.DataFrame,
    *,
    z_value: float = Z_VALUE,
) -> EffectiveSampleSizeResult:
    validate_transition_cells(cells)
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Effective-n inference requires exactly one wave")
    if z_value <= 0:
        raise ValueError("z-value must be positive")

    cell_rows: list[dict[str, object]] = []
    for record in cells.iter_rows(named=True):
        estimate = record["estimate"]
        margin = record["margin_error"]
        exclusion = _cell_exclusion_reason(estimate, margin)
        cell_n_eff = (
            None
            if exclusion is not None
            else approximate_cell_n_eff(float(estimate), float(margin), z_value=z_value)
        )
        cell_rows.append(
            {
                "wave_id": record["wave_id"],
                "previous_party": record["previous_party"],
                "current_party": record["current_party"],
                "estimate": estimate,
                "margin_error": margin,
                "row_base": record["row_base"],
                "cell_n_eff": cell_n_eff,
                "included": exclusion is None,
                "exclusion_reason": exclusion,
            }
        )
    cell_frame = pl.DataFrame(cell_rows)

    observed_rows: list[dict[str, object]] = []
    fallback_parties: list[str] = []
    for previous_party in PARTIES:
        party_cells = cell_frame.filter(pl.col("previous_party") == previous_party)
        row_base = _row_base(party_cells)
        valid = [
            float(value)
            for value in party_cells.filter(pl.col("included"))["cell_n_eff"].to_list()
            if value is not None
        ]
        if valid:
            raw_n_eff = float(np.median(np.asarray(valid, dtype=float)))
            n_eff, clip_applied = _clip_n_eff(raw_n_eff, row_base)
            observed_rows.append(
                _n_eff_row(
                    wave_id=str(wave_ids[0]),
                    previous_party=previous_party,
                    row_base=row_base,
                    n_eff=n_eff,
                    valid_cells=len(valid),
                    used_fallback=False,
                    clip_applied=clip_applied,
                )
            )
        else:
            fallback_parties.append(previous_party)

    design_effects = [
        _as_float(row["row_base"]) / _as_float(row["n_eff"])
        for row in observed_rows
        if _as_float(row["n_eff"]) > 0
    ]
    wave_median_deff = (
        float(np.median(np.asarray(design_effects, dtype=float))) if design_effects else None
    )
    for previous_party in fallback_parties:
        party_cells = cell_frame.filter(pl.col("previous_party") == previous_party)
        row_base = _row_base(party_cells)
        if wave_median_deff is None or wave_median_deff <= 0:
            raw_n_eff = float(max(row_base, N_EFF_LOWER_BOUND))
        else:
            raw_n_eff = float(row_base) / wave_median_deff
        n_eff, clip_applied = _clip_n_eff(raw_n_eff, row_base)
        observed_rows.append(
            _n_eff_row(
                wave_id=str(wave_ids[0]),
                previous_party=previous_party,
                row_base=row_base,
                n_eff=n_eff,
                valid_cells=0,
                used_fallback=True,
                clip_applied=clip_applied,
            )
        )

    row_frame = pl.DataFrame(observed_rows).sort("previous_party")
    return EffectiveSampleSizeResult(
        wave_id=str(wave_ids[0]),
        n_eff_label=N_EFF_LABEL,
        z_value=z_value,
        wave_median_design_effect=wave_median_deff,
        rows=row_frame,
        cells=cell_frame,
    )


def build_survey_point_estimate(
    cells: pl.DataFrame,
    *,
    suppression: SuppressionStrategy = "flat",
    historical_origin_means: dict[str, np.ndarray] | None = None,
) -> TransitionPointEstimate:
    validate_transition_cells(cells)
    if suppression not in {"flat", "historical_origin", "zero_renormalize"}:
        raise ValueError(f"Unknown suppression strategy: {suppression}")
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Point estimates require exactly one wave")
    if suppression == "historical_origin" and not historical_origin_means:
        raise ValueError("Historical-origin suppression requires previous-party means")

    full_rows: list[np.ndarray] = []
    stated_rows: list[np.ndarray] = []
    diagnostics: list[dict[str, object]] = []
    for previous_party in PARTIES:
        values, suppressed = _row_category_values(
            cells.filter(pl.col("previous_party") == previous_party)
        )
        published = np.isfinite(values) & ~suppressed
        published_mass = float(np.nansum(values[published])) if published.any() else 0.0
        residual = max(0.0, 1.0 - published_mass)
        allocated = values.copy()
        allocated[np.isnan(allocated)] = 0.0
        if suppression == "zero_renormalize":
            allocated[suppressed] = 0.0
            allocated = _as_simplex(allocated)
        elif suppressed.any():
            allocated[suppressed] = _suppressed_allocation(
                residual,
                previous_party=previous_party,
                suppressed=suppressed,
                suppression=suppression,
                historical_origin_means=historical_origin_means,
            )
        if np.any(allocated < 0):
            raise ValueError("Survey point estimates cannot be negative")
        stated = _condition_on_stated_parties(allocated)
        full_rows.append(allocated)
        stated_rows.append(stated)
        diagnostics.append(
            {
                "wave_id": wave_ids[0],
                "previous_party": previous_party,
                "published_cells": int(published.sum()),
                "suppressed_cells": int(suppressed.sum()),
                "residual_mass": residual,
                "full_category_mass": float(allocated.sum()),
                "stated_party_mass": float(
                    np.clip(allocated, 0.0, None)[list(STATED_PARTY_INDEX)].sum()
                ),
                "suppression_strategy": suppression,
            }
        )

    full_point = np.vstack(full_rows)
    stated_point = np.vstack(stated_rows)
    _validate_stated_matrix(stated_point)
    return TransitionPointEstimate(
        wave_id=str(wave_ids[0]),
        suppression_strategy=suppression,
        categories=POLL_CURRENT_CATEGORIES,
        full_category_point=full_point,
        full_category_center=np.vstack([_as_simplex(row) for row in full_point]),
        stated_party_point=stated_point,
        diagnostics=pl.DataFrame(diagnostics),
    )


def condition_on_stated_parties(full_row: np.ndarray) -> np.ndarray:
    return _condition_on_stated_parties(full_row)


def sample_dirichlet_row(rng: np.random.Generator, alpha: np.ndarray) -> np.ndarray:
    parameters = np.asarray(alpha, dtype=float)
    if parameters.ndim != 1 or parameters.size == 0:
        raise ValueError("Dirichlet parameters must be a one-dimensional vector")
    if np.any(parameters < 0):
        raise ValueError("Dirichlet parameters cannot be negative")
    positive = parameters > 0
    if not np.any(positive):
        raise ValueError("Dirichlet row has no positive concentration")
    draw = np.zeros(parameters.shape, dtype=float)
    if int(positive.sum()) == 1:
        draw[positive] = 1.0
        return draw
    draw[positive] = rng.dirichlet(np.maximum(parameters[positive], 1e-12))
    return draw


def sample_transition_draws(
    point: TransitionPointEstimate,
    n_eff: np.ndarray,
    *,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    dirichlet_alpha: np.ndarray | None = None,
    estimator: str = "T1_no_point_shrinkage_raked",
) -> TransitionDraws:
    if n_draws <= 0:
        raise ValueError("Draw count must be positive")
    concentration = np.asarray(n_eff, dtype=float)
    if concentration.shape != (len(PARTIES),):
        raise ValueError("n_eff must contain one value per previous party")
    if np.any(concentration <= 0):
        raise ValueError("n_eff must be positive")

    if dirichlet_alpha is None:
        alpha = point.full_category_center * concentration[:, None]
        sample_full = True
    else:
        alpha = np.asarray(dirichlet_alpha, dtype=float)
        if alpha.shape == (len(PARTIES), len(PARTIES)):
            sample_full = False
        elif alpha.shape == (len(PARTIES), len(POLL_CURRENT_CATEGORIES)):
            sample_full = True
        else:
            raise ValueError("Dirichlet alpha must be 9x9 stated-party or 9x12 full-category")

    rng = np.random.default_rng(seed)
    if sample_full:
        full_draws = np.empty((n_draws, len(PARTIES), len(POLL_CURRENT_CATEGORIES)), dtype=float)
        stated_draws = np.empty((n_draws, len(PARTIES), len(PARTIES)), dtype=float)
        for draw_index in range(n_draws):
            for row_index in range(len(PARTIES)):
                full_row = sample_dirichlet_row(rng, alpha[row_index])
                full_draws[draw_index, row_index] = full_row
                stated_draws[draw_index, row_index] = _condition_on_stated_parties(full_row)
        full_point = point.full_category_point
    else:
        full_draws = np.empty((0, len(PARTIES), len(POLL_CURRENT_CATEGORIES)), dtype=float)
        stated_draws = np.empty((n_draws, len(PARTIES), len(PARTIES)), dtype=float)
        for draw_index in range(n_draws):
            for row_index in range(len(PARTIES)):
                stated_draws[draw_index, row_index] = sample_dirichlet_row(rng, alpha[row_index])
        full_point = point.full_category_point

    for draw_index in range(n_draws):
        _validate_stated_matrix(stated_draws[draw_index])
    return TransitionDraws(
        wave_id=point.wave_id,
        seed=seed,
        n_draws=n_draws,
        estimator=estimator,
        n_eff_label=N_EFF_LABEL,
        full_category_draws=full_draws,
        stated_party_draws=stated_draws,
        point_full_category=full_point,
        point_stated_party=point.stated_party_point,
        n_eff=concentration,
        dirichlet_alpha=alpha,
    )


def rake_transition_draws(
    draws: TransitionDraws,
    previous_national: np.ndarray,
    target_national: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> RakedTransitionDraws:
    previous = _party_vector(previous_national)
    target = _party_vector(target_national)
    raked = np.empty_like(draws.stated_party_draws)
    diagnostics: list[dict[str, object]] = []
    for draw_index, matrix in enumerate(draws.stated_party_draws):
        result = calibrate_transition_matrix(
            matrix,
            previous,
            target,
            tolerance=tolerance,
        )
        raked[draw_index] = result.matrix
        _validate_stated_matrix(result.matrix, tolerance=tolerance)
        diagnostics.append(_calibration_record(draw_index, result, previous, target, tolerance))

    point_result = calibrate_transition_matrix(
        draws.point_stated_party,
        previous,
        target,
        tolerance=tolerance,
    )
    _validate_stated_matrix(point_result.matrix, tolerance=tolerance)
    mean_raked = raked.mean(axis=0)
    _validate_stated_matrix(mean_raked, tolerance=1e-8)
    diagnostic_frame = pl.DataFrame(diagnostics)
    return RakedTransitionDraws(
        wave_id=draws.wave_id,
        seed=draws.seed,
        n_draws=draws.n_draws,
        estimator=draws.estimator,
        raked_draws=raked,
        raked_point_matrix=point_result.matrix,
        mean_raked_draws=mean_raked,
        calibration_diagnostics=diagnostic_frame,
        row_simplex_ok=bool(
            diagnostic_frame["row_simplex_ok"].all() and np.allclose(raked.sum(axis=2), 1.0)
        ),
        national_reconciliation_ok=bool(diagnostic_frame["national_reconciliation_ok"].all()),
    )


def estimate_survey_transition(
    cells: pl.DataFrame,
    *,
    previous_national: np.ndarray | None = None,
    target_national: np.ndarray | None = None,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    suppression: SuppressionStrategy = "flat",
    historical_origin_means: dict[str, np.ndarray] | None = None,
    dirichlet_alpha: np.ndarray | None = None,
    estimator: str = "T1_no_point_shrinkage_raked",
    z_value: float = Z_VALUE,
    tolerance: float = 1e-10,
) -> SurveyTransitionEstimate:
    n_eff = infer_row_effective_sample_sizes(cells, z_value=z_value)
    point = build_survey_point_estimate(
        cells,
        suppression=suppression,
        historical_origin_means=historical_origin_means,
    )
    draws = sample_transition_draws(
        point,
        n_eff.vector(),
        n_draws=n_draws,
        seed=seed,
        dirichlet_alpha=dirichlet_alpha,
        estimator=estimator,
    )
    raked = None
    if previous_national is not None or target_national is not None:
        if previous_national is None or target_national is None:
            raise ValueError("Raking requires both previous and same-wave national vectors")
        raked = rake_transition_draws(
            draws,
            previous_national,
            target_national,
            tolerance=tolerance,
        )
    return SurveyTransitionEstimate(n_eff=n_eff, point=point, draws=draws, raked=raked)


def _cell_exclusion_reason(estimate: object, margin: object) -> str | None:
    if estimate is None:
        return _EXCLUDED_MISSING_ESTIMATE
    if margin is None:
        return _EXCLUDED_MISSING_MARGIN
    estimate_value = _as_float(estimate)
    margin_value = _as_float(margin)
    if not isfinite(estimate_value):
        return _EXCLUDED_MISSING_ESTIMATE
    if not isfinite(margin_value):
        return _EXCLUDED_MISSING_MARGIN
    if estimate_value <= 0:
        return _EXCLUDED_ZERO_ESTIMATE
    if estimate_value >= 1:
        return _EXCLUDED_UNIT_ESTIMATE
    if margin_value <= 0:
        return _EXCLUDED_ZERO_MARGIN
    return None


def _row_base(party_cells: pl.DataFrame) -> int:
    bases = party_cells["row_base"].drop_nulls().unique().to_list()
    if not bases:
        return 0
    return int(bases[0])


def _clip_n_eff(raw_n_eff: float, row_base: int) -> tuple[float, bool]:
    upper = max(float(row_base), N_EFF_LOWER_BOUND)
    clipped = min(max(raw_n_eff, N_EFF_LOWER_BOUND), upper)
    return clipped, not np.isclose(clipped, raw_n_eff)


def _n_eff_row(
    *,
    wave_id: str,
    previous_party: str,
    row_base: int,
    n_eff: float,
    valid_cells: int,
    used_fallback: bool,
    clip_applied: bool,
) -> dict[str, object]:
    design_effect = float(row_base) / n_eff if n_eff > 0 and row_base > 0 else None
    return {
        "wave_id": wave_id,
        "previous_party": previous_party,
        "row_base": row_base,
        "n_eff": n_eff,
        "design_effect": design_effect,
        "valid_cells": valid_cells,
        "used_fallback": used_fallback,
        "clip_applied": clip_applied,
        "n_eff_label": N_EFF_LABEL,
        "is_kish_ess": False,
    }


def _row_category_values(row: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    by_category = {record["current_party"]: record for record in row.iter_rows(named=True)}
    values = np.full(len(POLL_CURRENT_CATEGORIES), np.nan, dtype=float)
    suppressed = np.zeros(len(POLL_CURRENT_CATEGORIES), dtype=bool)
    for index, category in enumerate(POLL_CURRENT_CATEGORIES):
        record = by_category.get(category)
        if record is None:
            values[index] = 0.0
            continue
        estimate = record["estimate"]
        status = str(record["cell_status"])
        if estimate is None or status == "SUPPRESSED":
            suppressed[index] = True
            values[index] = np.nan
        else:
            values[index] = float(estimate)
    return values, suppressed


def _suppressed_allocation(
    residual: float,
    *,
    previous_party: str,
    suppressed: np.ndarray,
    suppression: SuppressionStrategy,
    historical_origin_means: dict[str, np.ndarray] | None,
) -> np.ndarray:
    n_suppressed = int(suppressed.sum())
    if n_suppressed == 0:
        return np.zeros(0, dtype=float)
    if residual <= 0:
        return np.zeros(n_suppressed, dtype=float)
    if suppression == "flat":
        return np.full(n_suppressed, residual / n_suppressed, dtype=float)
    if historical_origin_means is None or previous_party not in historical_origin_means:
        raise ValueError(f"Missing historical origin mean for {previous_party}")
    weights = _aligned_historical_weights(historical_origin_means[previous_party], suppressed)
    if weights.sum() <= 0:
        return np.full(n_suppressed, residual / n_suppressed, dtype=float)
    return cast(np.ndarray, residual * weights / weights.sum())


def _aligned_historical_weights(mean: np.ndarray, suppressed: np.ndarray) -> np.ndarray:
    vector = np.asarray(mean, dtype=float)
    if vector.shape == (len(POLL_CURRENT_CATEGORIES),):
        return cast(np.ndarray, np.clip(vector[suppressed], 0.0, None))
    if vector.shape == (len(PARTIES),):
        full = np.zeros(len(POLL_CURRENT_CATEGORIES), dtype=float)
        full[list(STATED_PARTY_INDEX)] = np.clip(vector, 0.0, None)
        return cast(np.ndarray, full[suppressed])
    raise ValueError("Historical origin means must be 9-party or full-category vectors")


def _condition_on_stated_parties(full_row: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(full_row, dtype=float), 0.0, None)
    if values.shape != (len(POLL_CURRENT_CATEGORIES),):
        if values.shape == (len(PARTIES),):
            return _as_simplex(values)
        raise ValueError("Full-category row must match POLL_CURRENT_CATEGORIES")
    stated = values[list(STATED_PARTY_INDEX)]
    return _as_simplex(stated)


def _as_simplex(values: np.ndarray) -> np.ndarray:
    vector = np.clip(np.asarray(values, dtype=float), 0.0, None)
    total = float(vector.sum())
    if total <= 0:
        raise ValueError("Probability vector must contain positive mass")
    return np.asarray(vector / total, dtype=float)


def _validate_stated_matrix(matrix: np.ndarray, *, tolerance: float = 1e-10) -> None:
    if matrix.shape != (len(PARTIES), len(PARTIES)):
        raise ValueError(f"Expected a {len(PARTIES)}x{len(PARTIES)} stated-party matrix")
    if np.any(matrix < -tolerance) or not np.allclose(matrix.sum(axis=1), 1.0, atol=tolerance):
        raise ValueError("Stated-party rows must be non-negative and sum to one")


def _as_float(value: object) -> float:
    return float(cast(int | float, value))


def _party_vector(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if vector.shape != (len(PARTIES),):
        raise ValueError("National party vectors must follow PARTIES order")
    return _as_simplex(vector)


def _calibration_record(
    draw_index: int,
    result: CalibrationResult,
    previous: np.ndarray,
    target: np.ndarray,
    tolerance: float,
) -> dict[str, object]:
    reconciled = previous @ result.matrix
    return {
        "draw_index": draw_index,
        "iterations": result.iterations,
        "converged": result.converged,
        "maximum_margin_error": result.maximum_margin_error,
        "kl_divergence": result.kl_divergence,
        "row_simplex_ok": bool(np.allclose(result.matrix.sum(axis=1), 1.0, atol=tolerance)),
        "national_reconciliation_ok": bool(np.allclose(reconciled, target, atol=tolerance)),
    }
