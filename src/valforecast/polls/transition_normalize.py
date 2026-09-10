from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES


@dataclass(frozen=True)
class NormalizedTransition:
    wave_id: str
    raw_matrix: np.ndarray
    shrunk_matrix: np.ndarray
    diagnostics: pl.DataFrame


def normalize_transition_cells(
    cells: pl.DataFrame,
    national_poll: pl.DataFrame,
    *,
    prior_strength: float = 200.0,
    suppressed_strategy: Literal["allocate_gap", "zero_sensitivity"] = "allocate_gap",
    nonparty_strategy: Literal["condition", "allocate_poll"] = "condition",
) -> NormalizedTransition:
    if prior_strength < 0:
        raise ValueError("Prior strength must be non-negative")
    if suppressed_strategy not in {"allocate_gap", "zero_sensitivity"}:
        raise ValueError(f"Unknown suppressed-cell strategy: {suppressed_strategy}")
    if nonparty_strategy not in {"condition", "allocate_poll"}:
        raise ValueError(f"Unknown nonparty strategy: {nonparty_strategy}")
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Transition cells must contain exactly one wave")
    poll = _party_vector(national_poll, "party", "poll_share")
    raw_rows: list[np.ndarray] = []
    shrunk_rows: list[np.ndarray] = []
    diagnostics: list[dict[str, object]] = []
    for previous_party in PARTIES:
        full_row = cells.filter(pl.col("previous_party") == previous_party)
        row = full_row.filter(pl.col("current_party").is_in(PARTIES))
        estimates_by_party = dict(
            row.select("current_party", "estimate").iter_rows()
        )
        values = np.array(
            [
                (
                    float(estimates_by_party[party])
                    if estimates_by_party.get(party) is not None
                    else np.nan
                )
                for party in PARTIES
            ],
            dtype=float,
        )
        published = np.isfinite(values)
        observed_party_mass = float(np.nansum(values))
        row_base_values = row["row_base"].drop_nulls().unique().to_list()
        row_base = int(row_base_values[0]) if row_base_values else 0
        published_total = float(full_row["estimate"].fill_null(0).sum())
        unreported_mass = max(0.0, 1.0 - published_total)
        nonparty_mass = float(
            full_row.filter(~pl.col("current_party").is_in(PARTIES))[
                "estimate"
            ].fill_null(0).sum()
        )
        if published.any():
            imputed_party_mass = 0.0
            if not published.all():
                if suppressed_strategy == "allocate_gap":
                    missing_prior = poll[~published]
                    if missing_prior.sum() <= 0:
                        raise ValueError("Cannot allocate unreported transition mass")
                    values[~published] = (
                        unreported_mass * missing_prior / missing_prior.sum()
                    )
                    imputed_party_mass = unreported_mass
                else:
                    values[~published] = 0
            if nonparty_strategy == "allocate_poll":
                values += nonparty_mass * poll
            raw = values / values.sum()
            reliability = row_base / (row_base + prior_strength) if row_base else 0.0
            status = (
                "OBSERVED_CONDITIONED"
                if published.all()
                else (
                    "PARTIAL_CONSTRAINED_PRIOR"
                    if suppressed_strategy == "allocate_gap"
                    else "PARTIAL_ZERO_SENSITIVITY"
                )
            )
        else:
            raw = poll.copy()
            reliability = 0.0
            status = "NO_SIGNAL_PRIOR"
            imputed_party_mass = 1.0
        shrunk = reliability * raw + (1 - reliability) * poll
        shrunk /= shrunk.sum()
        raw_rows.append(raw)
        shrunk_rows.append(shrunk)
        diagnostics.append(
            {
                "wave_id": wave_ids[0],
                "previous_party": previous_party,
                "row_base": row_base,
                "published_party_cells": int(published.sum()),
                "unreported_mass": unreported_mass,
                "observed_party_mass": observed_party_mass,
                "imputed_party_mass": imputed_party_mass,
                "nonparty_mass": nonparty_mass,
                "reliability": reliability,
                "normalization_status": status,
            }
        )
    raw_matrix = np.vstack(raw_rows)
    shrunk_matrix = np.vstack(shrunk_rows)
    _validate_stochastic(raw_matrix)
    _validate_stochastic(shrunk_matrix)
    return NormalizedTransition(
        wave_id=str(wave_ids[0]),
        raw_matrix=raw_matrix,
        shrunk_matrix=shrunk_matrix,
        diagnostics=pl.DataFrame(diagnostics),
    )


def matrix_to_frame(
    matrix: np.ndarray,
    *,
    wave_id: str,
    matrix_stage: str,
) -> pl.DataFrame:
    _validate_stochastic(matrix)
    rows = []
    for previous_index, previous_party in enumerate(PARTIES):
        for current_index, current_party in enumerate(PARTIES):
            rows.append(
                {
                    "wave_id": wave_id,
                    "matrix_stage": matrix_stage,
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "probability": float(matrix[previous_index, current_index]),
                }
            )
    return pl.DataFrame(rows)


def _party_vector(frame: pl.DataFrame, party_column: str, value_column: str) -> np.ndarray:
    by_party = dict(frame.select(party_column, value_column).iter_rows())
    vector = np.array([float(by_party[party]) for party in PARTIES], dtype=float)
    if np.any(vector < 0) or vector.sum() <= 0:
        raise ValueError("Party vector must be non-negative and non-empty")
    return np.asarray(vector / vector.sum(), dtype=float)


def _validate_stochastic(matrix: np.ndarray, tolerance: float = 1e-10) -> None:
    if matrix.shape != (len(PARTIES), len(PARTIES)):
        raise ValueError(f"Expected a {len(PARTIES)}x{len(PARTIES)} matrix")
    if np.any(matrix < 0) or not np.allclose(matrix.sum(axis=1), 1, atol=tolerance):
        raise ValueError("Transition rows must be non-negative and sum to one")
