from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.models.transition_matrix import build_transition_predictions
from valforecast.polls.transition_corpus import build_survey_corpora
from valforecast.polls.transition_hierarchy import (
    HierarchyFit,
    fit_previous_party_hierarchy,
    predictive_kl,
    predictive_log_score,
)
from valforecast.polls.transition_posterior import (
    build_survey_point_estimate,
    infer_row_effective_sample_sizes,
)

PRIMARY_MODEL = "T1_no_point_shrinkage_raked"
SECONDARY_MODEL = "T1_previous_party_hierarchical_raked"
BASELINE_MODEL = "B2_poll_state"


@dataclass(frozen=True)
class DrawScore:
    draw: int
    model_mae: float
    baseline_mae: float
    delta_mae: float


def lock_survey_estimators(root: Path, *, fetch_missing: bool = True) -> dict[str, object]:
    """Fit and persist survey-only hyperparameters before election outcomes are read."""
    assert_frozen_four_a(root)
    corpus_summary = build_survey_corpora(root, fetch_missing=fetch_missing)
    processed = root / "data" / "processed"
    output = root / "reports" / "milestone_four_b"
    output.mkdir(parents=True, exist_ok=True)
    cells = pl.read_parquet(processed / "survey_corpus_cells.parquet")
    selected_frames: list[pl.DataFrame] = []
    fold_frames: list[pl.DataFrame] = []
    audit_frames: list[pl.DataFrame] = []
    for cycle, target_wave, validation_wave in (
        (2018, "2018M05", "2017M11"),
        (2022, "2022M05", "2021M11"),
    ):
        cycle_cells = cells.filter(
            (pl.col("election_cycle") == cycle)
            & (pl.col("corpus_role").is_in(["train", "validation"]))
        )
        fit = fit_previous_party_hierarchy(
            cycle_cells,
            target_wave_id=target_wave,
            untouched_validation_wave_ids=(validation_wave,),
        )
        selected_frames.append(
            fit.selected.with_columns(
                pl.lit(cycle).alias("election_cycle"),
                pl.lit(fit.previous_election).alias("previous_election"),
                pl.lit(target_wave).alias("target_wave"),
                pl.lit(fit.selection_metric).alias("selection_metric"),
            )
        )
        fold_frames.append(
            fit.fold_scores.with_columns(pl.lit(cycle).alias("election_cycle"))
        )
        audit_frames.append(
            _survey_validation_audit(
                cycle_cells.filter(pl.col("calendar_wave") == validation_wave),
                fit,
                election_cycle=cycle,
            )
        )
    selected = pl.concat(selected_frames).sort("election_cycle", "previous_party")
    folds = pl.concat(fold_frames).sort(
        "election_cycle", "previous_party", "held_out_year", "wave_id", "kappa"
    )
    audits = pl.concat(audit_frames).sort("election_cycle", "previous_party")
    selected_path = output / "hierarchy_selected_concentrations.csv"
    folds_path = output / "hierarchy_survey_cv.csv"
    audit_path = output / "hierarchy_untouched_survey_validation.csv"
    selected.write_csv(selected_path, float_precision=12)
    folds.write_csv(folds_path, float_precision=12)
    audits.write_csv(audit_path, float_precision=12)
    lock = {
        "schema_version": 1,
        "stage": "survey_only_pre_election_scoring",
        "corpus": corpus_summary,
        "selected_concentrations": _records_for_json(selected),
        "artifact_sha256": {
            selected_path.name: _sha256(selected_path),
            folds_path.name: _sha256(folds_path),
            audit_path.name: _sha256(audit_path),
        },
        "forbidden_inputs_confirmed_absent": [
            "election_results_2018",
            "election_results_2022",
            "target_election_actual_share",
        ],
    }
    lock_path = output / "survey_estimator_lock.json"
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return lock


def score_transition_draws(
    canonical: pl.DataFrame,
    matrices: np.ndarray,
    baseline_predictions: pl.DataFrame,
    *,
    batch_size: int = 64,
) -> list[DrawScore]:
    """Score already-locked survey draws; no estimator fitting occurs here."""
    draws = np.asarray(matrices, dtype=float)
    expected_shape = (len(PARTIES), len(PARTIES))
    if draws.ndim != 3 or draws.shape[1:] != expected_shape:
        raise ValueError(f"Expected draw matrices with trailing shape {expected_shape}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    previous, actual, district_weights, district_ids = _canonical_arrays(canonical)
    baseline = (
        baseline_predictions.pivot(
            on="party",
            index="district_id",
            values="predicted_share",
        )
        .select("district_id", *PARTIES)
        .sort("district_id")
    )
    if baseline["district_id"].to_list() != district_ids:
        raise ValueError("Baseline and canonical district populations differ")
    baseline_values = baseline.select(PARTIES).to_numpy()
    baseline_mae = _weighted_mae(baseline_values, actual, district_weights)
    scores: list[DrawScore] = []
    for start in range(0, draws.shape[0], batch_size):
        stop = min(start + batch_size, draws.shape[0])
        predicted = np.einsum("di,bij->bdj", previous, draws[start:stop])
        predicted /= predicted.sum(axis=2, keepdims=True)
        errors = np.abs(predicted - actual[None, :, :])
        maes = np.sum(errors * district_weights[None, :, None], axis=(1, 2))
        maes /= district_weights.sum() * len(PARTIES)
        scores.extend(
            DrawScore(
                draw=draw_index,
                model_mae=float(maes[draw_index - start]),
                baseline_mae=baseline_mae,
                delta_mae=float(maes[draw_index - start] - baseline_mae),
            )
            for draw_index in range(start, stop)
        )
    return scores


def posterior_mean_predictions(
    canonical: pl.DataFrame,
    matrices: np.ndarray,
    *,
    model: str,
) -> pl.DataFrame:
    draws = np.asarray(matrices, dtype=float)
    if draws.ndim != 3:
        raise ValueError("Expected a draw-by-previous-party-by-current-party array")
    return build_transition_predictions(canonical, draws.mean(axis=0), model=model)


def draw_score_frame(
    scores: list[DrawScore],
    *,
    election_cycle: int,
    model: str,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "election_cycle": [election_cycle] * len(scores),
            "model": [model] * len(scores),
            "draw": [score.draw for score in scores],
            "model_mae": [score.model_mae for score in scores],
            "baseline_mae": [score.baseline_mae for score in scores],
            "delta_mae": [score.delta_mae for score in scores],
        }
    )


def survey_interval(draw_scores: pl.DataFrame) -> pl.DataFrame:
    return (
        draw_scores.group_by("election_cycle", "model")
        .agg(
            pl.col("delta_mae").mean().alias("mean_delta_mae"),
            pl.col("delta_mae").quantile(0.025).alias("interval_low"),
            pl.col("delta_mae").quantile(0.975).alias("interval_high"),
            pl.len().alias("draws"),
        )
        .sort("election_cycle", "model")
    )


def four_b_verdict(point_comparison: pl.DataFrame, intervals: pl.DataFrame) -> str:
    primary_point = point_comparison.filter(pl.col("model") == PRIMARY_MODEL)
    primary_interval = intervals.filter(pl.col("model") == PRIMARY_MODEL)
    if primary_point["election_cycle"].n_unique() != 2:
        raise ValueError("The 4B gate requires both 2018 and 2022 point scores")
    if primary_interval["election_cycle"].n_unique() != 2:
        raise ValueError("The 4B gate requires both 2018 and 2022 survey intervals")
    if primary_point.filter(pl.col("delta_mae") >= 0).height:
        return "NOT_SUPPORTED"
    if primary_interval.filter(pl.col("interval_high") >= 0).is_empty():
        return "SUPPORTED"
    return "UNCLEAR"


def assert_frozen_four_a(root: Path) -> None:
    report = root / "reports" / "milestone_4a_transition_matrix.md"
    observed = hashlib.sha256(report.read_bytes()).hexdigest()
    expected = "cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3"
    if observed != expected:
        raise ValueError(f"Frozen Milestone 4A report changed: {observed}")


def _survey_validation_audit(
    cells: pl.DataFrame,
    fit: HierarchyFit,
    *,
    election_cycle: int,
) -> pl.DataFrame:
    wave_ids = cells["wave_id"].unique().to_list()
    if len(wave_ids) != 1:
        raise ValueError("Untouched survey audit requires exactly one wave")
    survey_cells = cells.drop(
        "previous_election",
        "calendar_year",
        "calendar_wave",
        "election_cycle",
        "vintage",
        "corpus_role",
        "exclusion_reason",
        strict=False,
    )
    point = build_survey_point_estimate(survey_cells)
    n_eff = infer_row_effective_sample_sizes(survey_cells).vector()
    kappa = fit.kappa_vector()
    rows = []
    for index, previous_party in enumerate(PARTIES):
        rows.append(
            {
                "election_cycle": election_cycle,
                "wave_id": str(wave_ids[0]),
                "previous_party": previous_party,
                "selected_kappa": float(kappa[index]),
                "n_eff": float(n_eff[index]),
                "predictive_log_score": predictive_log_score(
                    point.stated_party_point[index],
                    fit.means[previous_party],
                    kappa=float(kappa[index]),
                    n_eff=float(n_eff[index]),
                ),
                "weighted_kl": predictive_kl(
                    point.stated_party_point[index],
                    fit.means[previous_party],
                    kappa=float(kappa[index]),
                    n_eff=float(n_eff[index]),
                ),
                "used_for_selection": False,
            }
        )
    return pl.DataFrame(rows)


def _records_for_json(frame: pl.DataFrame) -> list[dict[str, object]]:
    return [
        {str(key): value for key, value in record.items()}
        for record in frame.iter_rows(named=True)
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_arrays(
    canonical: pl.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    district = (
        canonical.pivot(
            on="party",
            index=["to_district_id", "valid_votes", "previous_valid_votes"],
            values=["previous_vote_share", "current_vote_share"],
            separator="_",
        )
        .sort("to_district_id")
    )
    previous = district.select(
        [f"previous_vote_share_{party}" for party in PARTIES]
    ).to_numpy()
    actual = district.select(
        [f"current_vote_share_{party}" for party in PARTIES]
    ).to_numpy()
    previous_counts = previous * district["previous_valid_votes"].to_numpy()[:, None]
    previous_counts = np.where(previous_counts == 0, 0.5, previous_counts)
    previous = previous_counts / previous_counts.sum(axis=1, keepdims=True)
    weights = district["valid_votes"].to_numpy().astype(float)
    district_ids = [str(value) for value in district["to_district_id"].to_list()]
    return (
        np.asarray(previous, dtype=float),
        np.asarray(actual, dtype=float),
        np.asarray(weights, dtype=float),
        district_ids,
    )


def _weighted_mae(
    predicted: np.ndarray,
    actual: np.ndarray,
    district_weights: np.ndarray,
) -> float:
    numerator = cast(float, np.sum(np.abs(predicted - actual) * district_weights[:, None]))
    return float(numerator / (district_weights.sum() * len(PARTIES)))
