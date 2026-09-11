from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS
from valforecast.polls.region_ingest import validate_regional_cells
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_posterior import (
    N_EFF_LOWER_BOUND,
    approximate_cell_n_eff,
    sample_dirichlet_row,
)

REGION_N_EFF_LABEL = "approximate_margin_inverted_not_kish"


@dataclass(frozen=True)
class RegionalPointMargins:
    wave_id: str
    suppression: str
    margins: np.ndarray
    diagnostics: pl.DataFrame


@dataclass(frozen=True)
class RegionalEffectiveN:
    wave_id: str
    n_eff_label: str
    values: np.ndarray
    rows: pl.DataFrame

    @property
    def is_kish_ess(self) -> bool:
        return False


@dataclass(frozen=True)
class RegionalMarginDraws:
    wave_id: str
    seed: int
    n_draws: int
    point: np.ndarray
    draws: np.ndarray
    n_eff: np.ndarray


@dataclass(frozen=True)
class ReconciledRegionalMargins:
    wave_id: str
    point: np.ndarray
    draws: np.ndarray | None
    national_target: np.ndarray
    region_weights: np.ndarray
    row_simplex_ok: bool
    national_reconciliation_ok: bool


def build_regional_point_margins(
    cells: pl.DataFrame,
    *,
    suppression: str = "flat",
) -> RegionalPointMargins:
    validate_regional_cells(cells)
    if suppression not in {"flat", "zero_renormalize"}:
        raise ValueError(f"Unknown regional suppression strategy: {suppression}")
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Regional point estimates require exactly one wave")
    regional = cells.filter(pl.col("region_id").is_in(list(REGION_IDS)))
    margins = np.zeros((len(REGION_IDS), len(PARTIES)), dtype=float)
    diagnostics: list[dict[str, object]] = []
    for region_index, region_id in enumerate(REGION_IDS):
        row = regional.filter(pl.col("region_id") == region_id)
        values = np.full(len(PARTIES), np.nan, dtype=float)
        suppressed = np.zeros(len(PARTIES), dtype=bool)
        by_party = {
            str(record["party"]): record for record in row.iter_rows(named=True)
        }
        for party_index, party in enumerate(PARTIES):
            record = by_party.get(party)
            if record is None:
                raise ValueError(f"Region {region_id} is missing party {party}")
            if record["cell_status"] == "SUPPRESSED" or record["estimate"] is None:
                suppressed[party_index] = True
            else:
                values[party_index] = float(record["estimate"])
        published = ~suppressed & np.isfinite(values)
        published_mass = float(np.nansum(values[published])) if published.any() else 0.0
        residual = max(0.0, 1.0 - published_mass)
        allocated = np.where(np.isfinite(values), values, 0.0)
        if suppression == "zero_renormalize":
            allocated[suppressed] = 0.0
        elif suppressed.any():
            allocated[suppressed] = residual / float(suppressed.sum())
        allocated = _as_simplex(allocated)
        margins[region_index] = allocated
        diagnostics.append(
            {
                "wave_id": wave_ids[0],
                "region_id": region_id,
                "published_cells": int(published.sum()),
                "suppressed_cells": int(suppressed.sum()),
                "residual_mass": residual,
                "suppression": suppression,
            }
        )
    return RegionalPointMargins(
        wave_id=str(wave_ids[0]),
        suppression=suppression,
        margins=margins,
        diagnostics=pl.DataFrame(diagnostics),
    )


def infer_region_effective_sample_sizes(cells: pl.DataFrame) -> RegionalEffectiveN:
    validate_regional_cells(cells)
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Regional n_eff requires exactly one wave")
    rows: list[dict[str, object]] = []
    values = np.zeros(len(REGION_IDS), dtype=float)
    for region_index, region_id in enumerate(REGION_IDS):
        valid: list[float] = []
        for record in cells.filter(pl.col("region_id") == region_id).iter_rows(named=True):
            estimate = record["estimate"]
            margin = record["margin_error"]
            if estimate is None or margin is None:
                continue
            estimate_value = float(estimate)
            margin_value = float(margin)
            if (
                not isfinite(estimate_value)
                or not isfinite(margin_value)
                or estimate_value <= 0
                or estimate_value >= 1
                or margin_value <= 0
            ):
                continue
            valid.append(approximate_cell_n_eff(estimate_value, margin_value))
        if not valid:
            n_eff = N_EFF_LOWER_BOUND
            used_fallback = True
        else:
            n_eff = float(max(np.median(np.asarray(valid, dtype=float)), N_EFF_LOWER_BOUND))
            used_fallback = False
        values[region_index] = n_eff
        rows.append(
            {
                "wave_id": wave_ids[0],
                "region_id": region_id,
                "n_eff": n_eff,
                "valid_cells": len(valid),
                "used_fallback": used_fallback,
                "clip_applied": False,
                "n_eff_label": REGION_N_EFF_LABEL,
                "is_kish_ess": False,
                "base_capped": False,
            }
        )
    return RegionalEffectiveN(
        wave_id=str(wave_ids[0]),
        n_eff_label=REGION_N_EFF_LABEL,
        values=values,
        rows=pl.DataFrame(rows),
    )


def sample_regional_margin_draws(
    point: RegionalPointMargins,
    n_eff: RegionalEffectiveN,
    *,
    n_draws: int,
    seed: int,
) -> RegionalMarginDraws:
    if n_draws <= 0:
        raise ValueError("Draw count must be positive")
    if n_eff.values.shape != (len(REGION_IDS),):
        raise ValueError("Regional n_eff must contain one value per Vid12 region")
    rng = np.random.default_rng(seed)
    draws = np.empty((n_draws, len(REGION_IDS), len(PARTIES)), dtype=float)
    for draw_index in range(n_draws):
        for region_index in range(len(REGION_IDS)):
            alpha = n_eff.values[region_index] * point.margins[region_index]
            draws[draw_index, region_index] = sample_dirichlet_row(rng, alpha)
    return RegionalMarginDraws(
        wave_id=point.wave_id,
        seed=seed,
        n_draws=n_draws,
        point=point.margins,
        draws=draws,
        n_eff=n_eff.values,
    )


def reconcile_regional_margins(
    margins: np.ndarray,
    region_weights: np.ndarray,
    national_target: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> np.ndarray:
    result = calibrate_transition_matrix(
        margins,
        region_weights,
        national_target,
        tolerance=tolerance,
    )
    if not result.converged:
        raise ValueError("Regional margin reconciliation did not converge")
    reconciled = result.matrix
    if not np.allclose(reconciled.sum(axis=1), 1.0, atol=tolerance):
        raise ValueError("Reconciled regional margins are not row-simplex")
    if not np.allclose(region_weights @ reconciled, _as_simplex(national_target), atol=tolerance):
        raise ValueError("Reconciled regional margins miss the national Vid10 target")
    return np.asarray(reconciled, dtype=float)


def reconcile_regional_draws(
    draws: RegionalMarginDraws,
    region_weights: np.ndarray,
    national_target: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> ReconciledRegionalMargins:
    point = reconcile_regional_margins(
        draws.point,
        region_weights,
        national_target,
        tolerance=tolerance,
    )
    reconciled = np.empty_like(draws.draws)
    for draw_index, margin in enumerate(draws.draws):
        reconciled[draw_index] = reconcile_regional_margins(
            margin,
            region_weights,
            national_target,
            tolerance=tolerance,
        )
    return ReconciledRegionalMargins(
        wave_id=draws.wave_id,
        point=point,
        draws=reconciled,
        national_target=_as_simplex(national_target),
        region_weights=_as_simplex(region_weights),
        row_simplex_ok=bool(np.allclose(reconciled.sum(axis=2), 1.0, atol=tolerance)),
        national_reconciliation_ok=all(
            np.allclose(
                _as_simplex(region_weights) @ reconciled[index],
                _as_simplex(national_target),
                atol=1e-8,
            )
            for index in range(reconciled.shape[0])
        ),
    )


def calibrate_region_transition_matrices(
    national_matrix: np.ndarray,
    previous_by_region: np.ndarray,
    regional_targets: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> np.ndarray:
    if national_matrix.shape != (len(PARTIES), len(PARTIES)):
        raise ValueError("National transition matrix has unexpected shape")
    if previous_by_region.shape != (len(REGION_IDS), len(PARTIES)):
        raise ValueError("Previous-by-region matrix has unexpected shape")
    if regional_targets.shape != (len(REGION_IDS), len(PARTIES)):
        raise ValueError("Regional targets have unexpected shape")
    matrices = np.empty((len(REGION_IDS), len(PARTIES), len(PARTIES)), dtype=float)
    for region_index in range(len(REGION_IDS)):
        result = calibrate_transition_matrix(
            national_matrix,
            previous_by_region[region_index],
            regional_targets[region_index],
            tolerance=tolerance,
        )
        if not result.converged:
            raise ValueError(f"Region {REGION_IDS[region_index]} transition calibration failed")
        matrices[region_index] = result.matrix
    return matrices


def _as_simplex(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if np.any(vector < 0) or vector.sum() <= 0:
        raise ValueError("Expected a non-negative vector with positive mass")
    return np.asarray(vector / vector.sum(), dtype=float)
