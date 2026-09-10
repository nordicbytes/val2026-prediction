from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.polls.transition_posterior import (
    TransitionPointEstimate,
    build_survey_point_estimate,
    infer_row_effective_sample_sizes,
)

CONCENTRATION_GRID = (0.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
ZERO_SMOOTHING = 0.5
_WAVE_YEAR = re.compile(r"(19|20)\d{2}")


@dataclass(frozen=True)
class _PreparedWaveRow:
    wave_id: str
    calendar_wave: str | None
    calendar_year: int
    previous_election: int
    previous_party: str
    stated: np.ndarray
    n_eff: float


@dataclass(frozen=True)
class HierarchyFit:
    previous_election: int
    concentration_grid: tuple[float, ...]
    selected: pl.DataFrame
    means: dict[str, np.ndarray]
    fold_scores: pl.DataFrame
    training_wave_ids: tuple[str, ...]
    excluded_wave_ids: tuple[str, ...]
    selection_metric: str

    def kappa_vector(self) -> np.ndarray:
        by_party = dict(self.selected.select("previous_party", "kappa").iter_rows())
        return np.array([float(by_party[party]) for party in PARTIES], dtype=float)

    def mean_matrix(self) -> np.ndarray:
        return np.vstack([self.means[party] for party in PARTIES])


def parse_calendar_year(wave_id: str) -> int:
    match = _WAVE_YEAR.search(wave_id)
    if match is None:
        raise ValueError(f"Cannot parse calendar year from wave_id {wave_id!r}")
    return int(match.group(0))


def smooth_simplex(values: np.ndarray, *, zero_smoothing: float = ZERO_SMOOTHING) -> np.ndarray:
    vector = np.clip(np.asarray(values, dtype=float), 0.0, None)
    if vector.ndim != 1 or vector.size == 0:
        raise ValueError("Expected a one-dimensional simplex vector")
    total = float(vector.sum())
    vector = (
        np.full(vector.shape, 1.0 / vector.size, dtype=float)
        if total <= 0
        else vector / total
    )
    if zero_smoothing <= 0 or np.all(vector > 0):
        return np.asarray(vector, dtype=float)
    crumb = zero_smoothing / vector.size
    vector = np.where(vector > 0, vector, crumb)
    return np.asarray(vector / vector.sum(), dtype=float)


def previous_party_mean(
    rows: Sequence[np.ndarray],
    *,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> np.ndarray:
    if not rows:
        raise ValueError("Cannot form a previous-party mean from zero survey rows")
    stacked = np.vstack([smooth_simplex(row, zero_smoothing=0.0) for row in rows])
    if stacked.shape[1] != len(PARTIES):
        raise ValueError("Previous-party means must be stated-party rows")
    return smooth_simplex(stacked.mean(axis=0), zero_smoothing=zero_smoothing)


def log_dirichlet_multinomial(counts: np.ndarray, alpha: np.ndarray) -> float:
    observations = np.asarray(counts, dtype=float)
    parameters = np.asarray(alpha, dtype=float)
    if observations.shape != parameters.shape:
        raise ValueError("Dirichlet-multinomial counts and parameters must align")
    if np.any(observations < 0) or np.any(parameters <= 0):
        raise ValueError("Dirichlet-multinomial requires non-negative counts and positive alpha")
    n_total = float(observations.sum())
    alpha_total = float(parameters.sum())
    return (
        math.lgamma(alpha_total)
        - math.lgamma(alpha_total + n_total)
        + sum(
            math.lgamma(float(parameter + count)) - math.lgamma(float(parameter))
            for parameter, count in zip(parameters, observations, strict=True)
        )
    )


def predictive_log_score(
    observed: np.ndarray,
    mean: np.ndarray,
    *,
    kappa: float,
    n_eff: float,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> float:
    if n_eff <= 0:
        raise ValueError("Predictive scores require positive n_eff")
    if kappa < 0:
        raise ValueError("Concentration cannot be negative")
    probability = smooth_simplex(observed, zero_smoothing=zero_smoothing)
    counts = n_eff * probability
    if kappa == 0:
        return float(n_eff * math.log(1.0 / len(PARTIES)))
    alpha = kappa * smooth_simplex(mean, zero_smoothing=zero_smoothing)
    return log_dirichlet_multinomial(counts, alpha)


def predictive_kl(
    observed: np.ndarray,
    mean: np.ndarray,
    *,
    kappa: float,
    n_eff: float,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> float:
    probability = smooth_simplex(observed, zero_smoothing=zero_smoothing)
    forecast = (
        np.full(len(PARTIES), 1.0 / len(PARTIES), dtype=float)
        if kappa == 0
        else smooth_simplex(mean, zero_smoothing=zero_smoothing)
    )
    return float(n_eff * np.sum(probability * np.log(probability / forecast)))


def fit_previous_party_hierarchy(
    cells: pl.DataFrame,
    *,
    target_wave_id: str,
    untouched_validation_wave_ids: Sequence[str] = (),
    concentration_grid: Sequence[float] = CONCENTRATION_GRID,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> HierarchyFit:
    _validate_hierarchy_cells(cells)
    grid = tuple(float(value) for value in concentration_grid)
    if tuple(grid) != CONCENTRATION_GRID and set(grid) != set(CONCENTRATION_GRID):
        missing = [value for value in CONCENTRATION_GRID if value not in grid]
        extra = [value for value in grid if value not in CONCENTRATION_GRID]
        if missing or extra:
            raise ValueError("Concentration grid must be the locked set {0,2,5,10,20,50,100}")
    grid = CONCENTRATION_GRID
    if _wave_is_present(cells, target_wave_id):
        raise ValueError("Target May wave cannot enter the hierarchical prior")

    prepared = _prepare_wave_rows(cells)
    excluded = tuple(sorted(set(untouched_validation_wave_ids)))
    training = [row for row in prepared if not _identifiers_of(row) & set(excluded)]
    if not training:
        raise ValueError("No training waves remain after excluding validation waves")
    references = {row.previous_election for row in training}
    if len(references) != 1:
        raise ValueError("Hierarchy means must use a single previous-election reference")
    previous_election = next(iter(references))

    training_wave_ids = tuple(sorted({row.wave_id for row in training}))
    fold_scores: list[dict[str, object]] = []
    selected_rows: list[dict[str, object]] = []
    means: dict[str, np.ndarray] = {}
    for previous_party in PARTIES:
        party_rows = [row for row in training if row.previous_party == previous_party]
        years = sorted({row.calendar_year for row in party_rows})
        underidentified = len(years) < 2
        means[previous_party] = (
            np.full(len(PARTIES), 1.0 / len(PARTIES), dtype=float)
            if not party_rows
            else previous_party_mean(
                [row.stated for row in party_rows],
                zero_smoothing=zero_smoothing,
            )
        )
        party_scores: dict[float, float] = {kappa: 0.0 for kappa in grid}
        scored_folds = 0
        if not underidentified:
            for year in years:
                train_rows = [row for row in party_rows if row.calendar_year != year]
                test_rows = [row for row in party_rows if row.calendar_year == year]
                if not train_rows or not test_rows:
                    continue
                fold_mean = previous_party_mean(
                    [row.stated for row in train_rows],
                    zero_smoothing=zero_smoothing,
                )
                scored_folds += 1
                for test in test_rows:
                    for kappa in grid:
                        log_score = predictive_log_score(
                            test.stated,
                            fold_mean,
                            kappa=kappa,
                            n_eff=test.n_eff,
                            zero_smoothing=zero_smoothing,
                        )
                        kl_score = predictive_kl(
                            test.stated,
                            fold_mean,
                            kappa=kappa,
                            n_eff=test.n_eff,
                            zero_smoothing=zero_smoothing,
                        )
                        party_scores[kappa] += log_score
                        fold_scores.append(
                            {
                                "previous_party": previous_party,
                                "held_out_year": year,
                                "wave_id": test.wave_id,
                                "kappa": kappa,
                                "n_eff": test.n_eff,
                                "log_score": log_score,
                                "weighted_kl": kl_score,
                            }
                        )
        if underidentified or scored_folds == 0:
            kappa = 0.0
            used_fallback = True
            selected_score = None
        else:
            best_score = max(party_scores.values())
            tied = [kappa for kappa, score in party_scores.items() if np.isclose(score, best_score)]
            kappa = min(tied)
            used_fallback = False
            selected_score = party_scores[kappa]
        selected_rows.append(
            {
                "previous_party": previous_party,
                "kappa": kappa,
                "log_score": selected_score,
                "underidentified": underidentified or scored_folds == 0,
                "used_fallback": used_fallback,
                "n_calendar_years": len(years),
                "n_training_rows": len(party_rows),
                "n_scored_folds": scored_folds,
            }
        )

    return HierarchyFit(
        previous_election=previous_election,
        concentration_grid=grid,
        selected=pl.DataFrame(selected_rows),
        means=means,
        fold_scores=pl.DataFrame(fold_scores) if fold_scores else _empty_fold_scores(),
        training_wave_ids=training_wave_ids,
        excluded_wave_ids=excluded,
        selection_metric="effective_n_weighted_predictive_kl_log_score",
    )


def hierarchical_dirichlet_parameters(
    fit: HierarchyFit,
    point: TransitionPointEstimate,
    n_eff: np.ndarray,
    *,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> np.ndarray:
    if point.stated_party_point.shape != (len(PARTIES), len(PARTIES)):
        raise ValueError("Hierarchical posteriors require a stated-party point estimate")
    concentration = np.asarray(n_eff, dtype=float)
    if concentration.shape != (len(PARTIES),):
        raise ValueError("n_eff must contain one value per previous party")
    kappas = fit.kappa_vector()
    alpha = np.zeros((len(PARTIES), len(PARTIES)), dtype=float)
    for index, party in enumerate(PARTIES):
        observed = smooth_simplex(point.stated_party_point[index], zero_smoothing=0.0)
        prior_mean = smooth_simplex(fit.means[party], zero_smoothing=zero_smoothing)
        alpha[index] = kappas[index] * prior_mean + concentration[index] * observed
    return alpha


def hierarchical_posterior_mean(
    fit: HierarchyFit,
    point: TransitionPointEstimate,
    n_eff: np.ndarray,
    *,
    zero_smoothing: float = ZERO_SMOOTHING,
) -> np.ndarray:
    alpha = hierarchical_dirichlet_parameters(
        fit,
        point,
        n_eff,
        zero_smoothing=zero_smoothing,
    )
    totals = alpha.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise ValueError("Hierarchical posterior has an empty row")
    return cast(np.ndarray, alpha / totals)


def _validate_hierarchy_cells(cells: pl.DataFrame) -> None:
    required = {
        "wave_id",
        "previous_party",
        "current_party",
        "estimate",
        "margin_error",
        "row_base",
        "cell_status",
        "previous_election",
    }
    missing = required - set(cells.columns)
    if missing:
        raise ValueError(f"Hierarchy cells are missing columns: {sorted(missing)}")
    forbidden = {
        "election_result",
        "actual_share",
        "target_may",
        "2018_election_result",
        "2022_election_result",
    }
    leaked = forbidden.intersection(cells.columns)
    if leaked:
        raise ValueError(f"Election outcomes cannot enter the hierarchy: {sorted(leaked)}")


def _prepare_wave_rows(cells: pl.DataFrame) -> list[_PreparedWaveRow]:
    prepared: list[_PreparedWaveRow] = []
    wave_ids = cells["wave_id"].unique().to_list()
    for wave_id in wave_ids:
        wave = cells.filter(pl.col("wave_id") == wave_id)
        references = wave["previous_election"].unique().to_list()
        if len(references) != 1:
            raise ValueError(f"Wave {wave_id} mixes previous-election references")
        calendar_wave = _unique_optional_string(wave, "calendar_wave")
        if "calendar_year" in wave.columns:
            years = wave["calendar_year"].unique().to_list()
            if len(years) != 1:
                raise ValueError(f"Wave {wave_id} mixes calendar years")
            calendar_year = int(years[0])
        elif calendar_wave is not None:
            calendar_year = parse_calendar_year(calendar_wave)
        else:
            calendar_year = parse_calendar_year(str(wave_id))
        survey_wave = wave.drop(
            "previous_election",
            "calendar_year",
            "calendar_wave",
            "election_cycle",
            "vintage",
            "corpus_role",
            "exclusion_reason",
            strict=False,
        )
        point = build_survey_point_estimate(survey_wave)
        n_eff = infer_row_effective_sample_sizes(survey_wave).vector()
        for index, previous_party in enumerate(PARTIES):
            prepared.append(
                _PreparedWaveRow(
                    wave_id=str(wave_id),
                    calendar_wave=calendar_wave,
                    calendar_year=calendar_year,
                    previous_election=int(references[0]),
                    previous_party=previous_party,
                    stated=point.stated_party_point[index].copy(),
                    n_eff=float(n_eff[index]),
                )
            )
    return prepared


def _wave_is_present(cells: pl.DataFrame, wave_id: str) -> bool:
    if wave_id in {str(value) for value in cells["wave_id"].unique().to_list()}:
        return True
    if "calendar_wave" in cells.columns:
        return wave_id in {str(value) for value in cells["calendar_wave"].unique().to_list()}
    return False


def _identifiers_of(row: _PreparedWaveRow) -> set[str]:
    identifiers = {row.wave_id}
    if row.calendar_wave is not None:
        identifiers.add(row.calendar_wave)
    return identifiers


def _unique_optional_string(frame: pl.DataFrame, column: str) -> str | None:
    if column not in frame.columns:
        return None
    values = [str(value) for value in frame[column].unique().to_list() if value is not None]
    if len(values) > 1:
        raise ValueError(f"Wave mixes {column} values")
    return values[0] if values else None


def _empty_fold_scores() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "previous_party": pl.String,
            "held_out_year": pl.Int64,
            "wave_id": pl.String,
            "kappa": pl.Float64,
            "n_eff": pl.Float64,
            "log_score": pl.Float64,
            "weighted_kl": pl.Float64,
        }
    )


__all__ = [
    "CONCENTRATION_GRID",
    "HierarchyFit",
    "ZERO_SMOOTHING",
    "fit_previous_party_hierarchy",
    "hierarchical_dirichlet_parameters",
    "hierarchical_posterior_mean",
    "log_dirichlet_multinomial",
    "parse_calendar_year",
    "predictive_kl",
    "predictive_log_score",
    "previous_party_mean",
    "smooth_simplex",
]
