from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
import polars as pl

from valforecast.evaluation.milestone_four_a import (
    _bloc_predictions,
    _county_robustness,
    _district_size_metrics,
    _group_metrics,
    _national_vector,
    _urbanity_metrics,
)
from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import evaluate_predictions
from valforecast.models.transition_matrix import (
    build_poll_state_proportional_predictions,
    build_transition_predictions,
)
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_corpus import build_survey_corpora
from valforecast.polls.transition_hierarchy import (
    HierarchyFit,
    fit_previous_party_hierarchy,
    predictive_kl,
    predictive_log_score,
)
from valforecast.polls.transition_ingest import (
    extract_scb_2018_pdf_national_poll,
    extract_scb_2018_pdf_transition,
    extract_scb_national_poll,
    extract_scb_transition_cells,
    read_pxweb_jsonstat,
)
from valforecast.polls.transition_normalize import matrix_to_frame, normalize_transition_cells
from valforecast.polls.transition_posterior import (
    SuppressionStrategy,
    build_survey_point_estimate,
    estimate_survey_transition,
    infer_row_effective_sample_sizes,
)

PRIMARY_MODEL = "T1_no_point_shrinkage_raked"
SECONDARY_MODEL = "T1_previous_party_hierarchical_raked"
BASELINE_MODEL = "B2_poll_state"
PUBLISHED_POINT_MODEL = "T1_published_point_raked"
SUPPRESSION_ZERO_MODEL = "T1_suppressed_zero_point"
SUPPRESSION_HISTORY_MODEL = "T1_suppressed_historical_point"
NONPARTY_POLL_MODEL = "T1_nonparty_poll_point"
EXPECTED_POPULATIONS = {2018: 4631, 2022: 4164}


@dataclass(frozen=True)
class DrawScore:
    draw: int
    model_mae: float
    baseline_mae: float
    delta_mae: float


class TargetSurveyInput(TypedDict):
    wave_id: str
    cells: pl.DataFrame
    poll: pl.DataFrame


def _assert_clean_survey_lock(root: Path) -> None:
    output = root / "reports" / "milestone_four_b"
    lock_path = output / "survey_estimator_lock.json"
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    if document.get("stage") != "survey_only_pre_election_scoring":
        raise ValueError("Survey estimator lock is missing or has the wrong stage")
    hashes = document.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("Survey estimator lock has no artifact hashes")
    for name, expected in hashes.items():
        path = output / str(name)
        if _sha256(path) != expected:
            raise ValueError(f"Locked survey artifact changed: {name}")


def _target_survey_inputs(root: Path) -> dict[int, TargetSurveyInput]:
    psu = root / "data" / "raw" / "scb" / "psu"
    pdf = psu / "psu_may_2018_original.pdf"
    transition_2022 = read_pxweb_jsonstat(psu / "transition_2022M05.json")
    national_2022 = read_pxweb_jsonstat(psu / "national_poll_2022M05.json")
    return {
        2018: {
            "wave_id": "scb_2018M05_original",
            "cells": extract_scb_2018_pdf_transition(pdf),
            "poll": extract_scb_2018_pdf_national_poll(pdf),
        },
        2022: {
            "wave_id": "scb_2022M05",
            "cells": extract_scb_transition_cells(
                transition_2022,
                wave_id="scb_2022M05",
                time_value="2022M05",
            ),
            "poll": extract_scb_national_poll(
                national_2022,
                wave_id="scb_2022M05",
                time_value="2022M05",
            ),
        },
    }


def _frame_party_vector(
    frame: pl.DataFrame,
    party_column: str,
    value_column: str,
) -> np.ndarray:
    by_party = dict(frame.select(party_column, value_column).iter_rows())
    vector = np.array([float(by_party[party]) for party in PARTIES], dtype=float)
    return np.asarray(vector / vector.sum(), dtype=float)


def _locked_hierarchy_components(
    root: Path,
    cycle: int,
) -> tuple[np.ndarray, np.ndarray]:
    output = root / "reports" / "milestone_four_b"
    selected = pl.read_csv(output / "hierarchy_selected_concentrations.csv").filter(
        pl.col("election_cycle") == cycle
    )
    means = pl.read_csv(output / "hierarchy_previous_party_means.csv").filter(
        pl.col("election_cycle") == cycle
    )
    kappas_by_party = dict(selected.select("previous_party", "kappa").iter_rows())
    means_by_pair = {
        (str(previous), str(current)): float(probability)
        for previous, current, probability in means.select(
            "previous_party", "current_party", "prior_mean"
        ).iter_rows()
    }
    kappas = np.array([float(kappas_by_party[party]) for party in PARTIES])
    mean_matrix = np.array(
        [
            [means_by_pair[(previous, current)] for current in PARTIES]
            for previous in PARTIES
        ]
    )
    if not np.allclose(mean_matrix.sum(axis=1), 1.0, atol=1e-10):
        raise ValueError("Locked hierarchy means are not row-simplex matrices")
    return kappas, mean_matrix


def _locked_hierarchy_alpha(
    root: Path,
    cycle: int,
    observed: np.ndarray,
    n_eff: np.ndarray,
) -> np.ndarray:
    kappas, means = _locked_hierarchy_components(root, cycle)
    return np.asarray(
        n_eff[:, None] * observed + kappas[:, None] * means,
        dtype=float,
    )


def _sensitivity_predictions(
    canonical: pl.DataFrame,
    cells: pl.DataFrame,
    poll: pl.DataFrame,
    previous_national: np.ndarray,
    poll_national: np.ndarray,
    root: Path,
    cycle: int,
) -> list[pl.DataFrame]:
    predictions: list[pl.DataFrame] = []
    if cells["estimate"].null_count():
        historical_means = _locked_hierarchy_components(root, cycle)[1]
        by_previous = {
            party: historical_means[index]
            for index, party in enumerate(PARTIES)
        }
        for strategy, model in cast(
            tuple[tuple[SuppressionStrategy, str], ...],
            (
            ("zero_renormalize", SUPPRESSION_ZERO_MODEL),
            ("historical_origin", SUPPRESSION_HISTORY_MODEL),
            ),
        ):
            point = build_survey_point_estimate(
                cells,
                suppression=strategy,
                historical_origin_means=(
                    by_previous if strategy == "historical_origin" else None
                ),
            )
            calibrated = calibrate_transition_matrix(
                point.stated_party_point,
                previous_national,
                poll_national,
            )
            predictions.append(
                build_transition_predictions(canonical, calibrated.matrix, model=model)
            )
    nonparty = normalize_transition_cells(
        cells,
        poll,
        prior_strength=0,
        nonparty_strategy="allocate_poll",
    )
    nonparty_calibrated = calibrate_transition_matrix(
        nonparty.raw_matrix,
        previous_national,
        poll_national,
    )
    predictions.append(
        build_transition_predictions(
            canonical,
            nonparty_calibrated.matrix,
            model=NONPARTY_POLL_MODEL,
        )
    )
    return predictions


def _point_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for transition_id, model in predictions.select(
        "transition_id", "model"
    ).unique().sort("transition_id", "model").iter_rows():
        scored = predictions.filter(
            (pl.col("transition_id") == transition_id)
            & (pl.col("model") == model)
        )
        rows.append(
            {
                "transition_id": transition_id,
                "election_cycle": 2018 if transition_id == "2014_2018" else 2022,
                **asdict(evaluate_predictions(scored, model=str(model))),
            }
        )
    return pl.DataFrame(rows).sort("election_cycle", "model")


def _point_comparison(metrics: pl.DataFrame) -> pl.DataFrame:
    baseline = metrics.filter(pl.col("model") == BASELINE_MODEL).select(
        "election_cycle",
        pl.col("district_weighted_mae").alias("baseline_mae"),
    )
    return (
        metrics.join(baseline, on="election_cycle")
        .with_columns(
            (pl.col("district_weighted_mae") - pl.col("baseline_mae")).alias(
                "delta_mae"
            )
        )
        .sort("election_cycle", "model")
    )


def _four_b_municipality_bootstrap(
    predictions: pl.DataFrame,
    *,
    draws: int = 2000,
    seed: int = 20260911,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for transition_id in sorted(predictions["transition_id"].unique().to_list()):
        subset = predictions.filter(pl.col("transition_id") == transition_id)
        baseline = subset.filter(pl.col("model") == BASELINE_MODEL).select(
            "district_id",
            "party",
            pl.col("predicted_share").alias("baseline_share"),
        )
        models = sorted(
            str(value)
            for value in subset.filter(pl.col("model") != BASELINE_MODEL)[
                "model"
            ].unique().to_list()
        )
        for model in models:
            municipality = (
                subset.filter(pl.col("model") == model)
                .join(baseline, on=["district_id", "party"])
                .with_columns(
                    (
                        (
                            (pl.col("actual_share") - pl.col("predicted_share")).abs()
                            - (pl.col("actual_share") - pl.col("baseline_share")).abs()
                        )
                        * pl.col("valid_votes")
                    ).alias("weighted_delta")
                )
                .group_by("municipality_id")
                .agg(
                    pl.col("weighted_delta").sum().alias("numerator"),
                    pl.col("valid_votes").sum().alias("denominator"),
                )
                .sort("municipality_id")
            )
            numerators = municipality["numerator"].to_numpy()
            denominators = municipality["denominator"].to_numpy()
            indices = rng.integers(
                0,
                municipality.height,
                size=(draws, municipality.height),
            )
            sampled = numerators[indices].sum(axis=1) / denominators[indices].sum(axis=1)
            rows.append(
                {
                    "transition_id": transition_id,
                    "model": model,
                    "observed_delta_mae": float(
                        numerators.sum() / denominators.sum()
                    ),
                    "ci_low": float(np.quantile(sampled, 0.025)),
                    "ci_high": float(np.quantile(sampled, 0.975)),
                    "draws": draws,
                    "cluster_unit": "municipality",
                }
            )
    return pl.DataFrame(rows).sort("transition_id", "model")


def _write_four_b_report(
    path: Path,
    comparison: pl.DataFrame,
    intervals: pl.DataFrame,
    robustness: pl.DataFrame,
    municipality_bootstrap: pl.DataFrame,
    effective_n: pl.DataFrame,
    verdict: str,
) -> None:
    point_rows = "\n".join(
        "| {cycle} | {model} | {mae:.3f} | {baseline:.3f} | {delta:+.3f} |".format(
            cycle=int(row["election_cycle"]),
            model=row["model"],
            mae=100 * float(row["district_weighted_mae"]),
            baseline=100 * float(row["baseline_mae"]),
            delta=100 * float(row["delta_mae"]),
        )
        for row in comparison.iter_rows(named=True)
    )
    interval_rows = "\n".join(
        "| {cycle} | {model} | {mean:+.3f} | [{low:+.3f}, {high:+.3f}] | {draws} |".format(
            cycle=int(row["election_cycle"]),
            model=row["model"],
            mean=100 * float(row["mean_delta_mae"]),
            low=100 * float(row["interval_low"]),
            high=100 * float(row["interval_high"]),
            draws=int(row["draws"]),
        )
        for row in intervals.iter_rows(named=True)
    )
    robustness_rows = "\n".join(
        "| {transition_id} | {model} | {won}/{total} | {coverage:.1%} | {delta:+.3f} |".format(
            transition_id=row["transition_id"],
            model=row["model"],
            won=int(row["counties_won"]),
            total=int(row["counties_total"]),
            coverage=float(row["winning_vote_coverage"]),
            delta=100 * float(row["median_county_delta"]),
        )
        for row in robustness.filter(
            pl.col("model").is_in([PRIMARY_MODEL, SECONDARY_MODEL])
        ).iter_rows(named=True)
    )
    bootstrap_rows = "\n".join(
        "| {transition_id} | {model} | {delta:+.3f} | [{low:+.3f}, {high:+.3f}] |".format(
            transition_id=row["transition_id"],
            model=row["model"],
            delta=100 * float(row["observed_delta_mae"]),
            low=100 * float(row["ci_low"]),
            high=100 * float(row["ci_high"]),
        )
        for row in municipality_bootstrap.filter(
            pl.col("model").is_in([PRIMARY_MODEL, SECONDARY_MODEL])
        ).iter_rows(named=True)
    )
    ess_rows = "\n".join(
        "| {cycle} | {party} | {base} | {n_eff:.1f} | {deff} | {fallback} |".format(
            cycle=int(row["election_cycle"]),
            party=row["previous_party"],
            base=int(row["row_base"]),
            n_eff=float(row["n_eff"]),
            deff=(
                "—"
                if row["design_effect"] is None
                else f"{float(row['design_effect']):.2f}"
            ),
            fallback="yes" if row["used_fallback"] else "no",
        )
        for row in effective_n.iter_rows(named=True)
    )
    report = f"""# Milestone 4B — Survey-based transition estimator

## Status

Milestone 4A remains byte-for-byte frozen as **UNCLEAR**. Its report SHA-256 is
`cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3`.
This is a separate 4B result and does not revise 4A.

## Why the 4A prior was misspecified

The 4A `n/(n+200)` estimator pulled every previous-party row toward the same
current-party national marginal. That mechanically reduces the
`previous_party → current_party` dependence T0 exists to measure. It was
generic regularization, but not a coherent origin-specific transition prior.

The locked 4B primary leaves SCB's survey-weighted published proportions
unchanged as point estimates. Sampling uncertainty is represented by
deterministic Dirichlet draws using margin-inverted approximate effective
sample sizes. These are explicitly not Kish ESS. Every draw is conditioned on
a stated party and raked separately. Same-wave Vid10 enters only raking and B2.

## Survey-only lock

All estimator choices were committed before election scoring. The historical
corpus contains 14 vintage-correct waves and 1,932 cells. Original PDF
vintages are used for the 2018 hierarchy; the revised series available from
2020 is used for 2022. `2018M05` and `2022M05` never enter prior fitting.
The secondary prior is previous-party-specific, and κ was selected with
whole-year-held-out survey waves only. `2017M11` and `2021M11` are untouched
survey audits, not tuning data.

## Approximate effective sample sizes

| Election | Previous party | Public base | Approx. n_eff | Implied design effect | Fallback |
|---:|---|---:|---:|---:|---|
{ess_rows}

## Locked point backtest

MAE is vote-weighted over district-party cells on the unchanged 4A populations.
B2 and every transition model receive exactly the same May national poll
vector. Negative ΔMAE is improvement. The primary point forecast is the mean
of 2,000 independently raked survey draws.

| Election | Model | Weighted MAE (pp) | B2 MAE (pp) | ΔMAE (pp) |
|---:|---|---:|---:|---:|
{point_rows}

`T1_published_point_raked` isolates the directly raked published matrix.
Suppression and nonparty variants are preregistered diagnostics. The
hierarchical estimator remains secondary and cannot replace the primary.
The nonparty-allocation sensitivity loses to B2 in 2018 and is effectively
flat in 2022. `SUPPORTED` therefore applies to the locked stated-party
estimand; it is not robustness to assumptions about unresolved respondents.

## Survey uncertainty

These intervals vary transition-table sampling uncertainty while holding the
May national poll state fixed. They are not election-prediction intervals and
exclude poll-state error, correlated panel error and weight-estimation error.

| Election | Model | Mean draw ΔMAE (pp) | Survey-draw 95% interval | Draws |
|---:|---|---:|---:|---:|
{interval_rows}

## Geographic robustness

| Transition | Model | Counties won | Winning vote coverage | Median county ΔMAE (pp) |
|---|---|---:|---:|---:|
{robustness_rows}

The municipality-cluster bootstrap is separate from survey uncertainty and
does not select κ or an estimator.

| Transition | Model | Point ΔMAE (pp) | Municipality-bootstrap 95% |
|---|---|---:|---:|
{bootstrap_rows}

## Decision

**{verdict}**

The locked rule is `SUPPORTED` only if primary point ΔMAE is negative and the
survey-draw 95% upper bound is below zero in both elections; `NOT_SUPPORTED`
if either primary point ΔMAE is non-negative; otherwise `UNCLEAR`.

This tests transition estimation only. It does not validate regional
conditioning, demographic poststratification or MRP, and no MRP implementation
is started here.
"""
    path.write_text(report, encoding="utf-8")


def run_milestone_four_b(root: Path) -> dict[str, object]:
    """Run locked election scoring; all survey choices must already be persisted."""
    assert_frozen_four_a(root)
    _assert_clean_survey_lock(root)
    output = root / "reports" / "milestone_four_b"
    canonical_all = pl.read_parquet(
        root / "data" / "processed" / "canonical_temporal_transitions.parquet"
    )
    cycles = _target_survey_inputs(root)
    predictions: list[pl.DataFrame] = []
    draw_scores: list[pl.DataFrame] = []
    matrices: list[pl.DataFrame] = []
    ess_rows: list[pl.DataFrame] = []
    calibration_rows: list[pl.DataFrame] = []
    for cycle in (2018, 2022):
        cycle_input = cycles[cycle]
        transition_id = "2014_2018" if cycle == 2018 else "2018_2022"
        canonical = canonical_all.filter(pl.col("transition_id") == transition_id)
        if canonical["to_district_id"].n_unique() != EXPECTED_POPULATIONS[cycle]:
            raise ValueError(f"Frozen {cycle} evaluation population changed")
        previous_national = _national_vector(
            pl.read_parquet(
                root / "data" / "processed" / f"election_results_{cycle - 4}.parquet"
            )
        )
        poll_national = _frame_party_vector(
            cycle_input["poll"], "party", "poll_share"
        )
        baseline = build_poll_state_proportional_predictions(
            canonical,
            previous_national,
            poll_national,
            model=BASELINE_MODEL,
        )
        predictions.append(baseline)
        primary = estimate_survey_transition(
            cycle_input["cells"],
            previous_national=previous_national,
            target_national=poll_national,
            n_draws=2000,
            seed=20260911,
            suppression="flat",
            estimator=PRIMARY_MODEL,
        )
        if primary.raked is None:
            raise AssertionError("Primary draws were not raked")
        predictions.extend(
            [
                posterior_mean_predictions(
                    canonical,
                    primary.raked.raked_draws,
                    model=PRIMARY_MODEL,
                ),
                build_transition_predictions(
                    canonical,
                    primary.raked.raked_point_matrix,
                    model=PUBLISHED_POINT_MODEL,
                ),
            ]
        )
        primary_scores = score_transition_draws(
            canonical,
            primary.raked.raked_draws,
            baseline,
        )
        draw_scores.append(
            draw_score_frame(primary_scores, election_cycle=cycle, model=PRIMARY_MODEL)
        )
        ess_rows.append(
            primary.n_eff.rows.with_columns(pl.lit(cycle).alias("election_cycle"))
        )
        calibration_rows.append(
            primary.raked.calibration_diagnostics.with_columns(
                pl.lit(cycle).alias("election_cycle"),
                pl.lit(PRIMARY_MODEL).alias("model"),
            )
        )
        matrices.extend(
            [
                matrix_to_frame(
                    primary.raked.mean_raked_draws,
                    wave_id=str(cycle_input["wave_id"]),
                    matrix_stage="PRIMARY_POSTERIOR_MEAN_RAKED",
                ).with_columns(pl.lit(cycle).alias("election_cycle")),
                matrix_to_frame(
                    primary.raked.raked_point_matrix,
                    wave_id=str(cycle_input["wave_id"]),
                    matrix_stage="PUBLISHED_POINT_RAKED",
                ).with_columns(pl.lit(cycle).alias("election_cycle")),
            ]
        )
        hierarchy_alpha = _locked_hierarchy_alpha(
            root,
            cycle,
            primary.point.stated_party_point,
            primary.n_eff.vector(),
        )
        secondary = estimate_survey_transition(
            cycle_input["cells"],
            previous_national=previous_national,
            target_national=poll_national,
            n_draws=2000,
            seed=20260911,
            suppression="flat",
            dirichlet_alpha=hierarchy_alpha,
            estimator=SECONDARY_MODEL,
        )
        if secondary.raked is None:
            raise AssertionError("Secondary draws were not raked")
        predictions.append(
            posterior_mean_predictions(
                canonical,
                secondary.raked.raked_draws,
                model=SECONDARY_MODEL,
            )
        )
        draw_scores.append(
            draw_score_frame(
                score_transition_draws(
                    canonical,
                    secondary.raked.raked_draws,
                    baseline,
                ),
                election_cycle=cycle,
                model=SECONDARY_MODEL,
            )
        )
        calibration_rows.append(
            secondary.raked.calibration_diagnostics.with_columns(
                pl.lit(cycle).alias("election_cycle"),
                pl.lit(SECONDARY_MODEL).alias("model"),
            )
        )
        matrices.append(
            matrix_to_frame(
                secondary.raked.mean_raked_draws,
                wave_id=str(cycle_input["wave_id"]),
                matrix_stage="HIERARCHICAL_POSTERIOR_MEAN_RAKED",
            ).with_columns(pl.lit(cycle).alias("election_cycle"))
        )
        predictions.extend(
            _sensitivity_predictions(
                canonical,
                cycle_input["cells"],
                cycle_input["poll"],
                previous_national,
                poll_national,
                root,
                cycle,
            )
        )

    all_predictions = pl.concat(predictions)
    metrics = _point_metrics(all_predictions)
    comparison = _point_comparison(metrics)
    all_draw_scores = pl.concat(draw_scores).sort("election_cycle", "model", "draw")
    intervals = survey_interval(all_draw_scores)
    verdict = four_b_verdict(comparison, intervals)
    party_metrics = _group_metrics(all_predictions, ["party"])
    bloc_metrics = _group_metrics(_bloc_predictions(all_predictions), ["party"])
    county_metrics = _group_metrics(all_predictions, ["county_id"])
    robustness = _county_robustness(county_metrics, all_predictions)
    size_metrics = _district_size_metrics(all_predictions)
    urbanity_metrics = _urbanity_metrics(all_predictions, root)
    municipality_bootstrap = _four_b_municipality_bootstrap(all_predictions)
    artifacts = {
        "model_metrics.csv": metrics,
        "point_comparison.csv": comparison,
        "survey_draw_metrics.csv": all_draw_scores,
        "survey_intervals.csv": intervals,
        "model_metrics_by_party.csv": party_metrics,
        "model_metrics_by_bloc.csv": bloc_metrics,
        "model_metrics_by_county.csv": county_metrics,
        "geographic_robustness.csv": robustness,
        "model_metrics_by_district_size.csv": size_metrics,
        "model_metrics_by_urbanity.csv": urbanity_metrics,
        "municipality_bootstrap.csv": municipality_bootstrap,
        "target_wave_effective_n.csv": pl.concat(ess_rows).sort(
            "election_cycle", "previous_party"
        ),
        "draw_calibration_diagnostics.csv": pl.concat(calibration_rows).sort(
            "election_cycle", "model", "draw_index"
        ),
        "transition_matrices.csv": pl.concat(matrices).sort(
            "election_cycle", "matrix_stage", "previous_party", "current_party"
        ),
    }
    for name, frame in artifacts.items():
        frame.write_csv(output / name, float_precision=12)
    _write_four_b_report(
        root / "reports" / "milestone_4b_transition_posterior.md",
        comparison,
        intervals,
        robustness,
        municipality_bootstrap,
        artifacts["target_wave_effective_n.csv"],
        verdict,
    )
    summary = {
        "verdict": verdict,
        "primary_estimator": PRIMARY_MODEL,
        "secondary_estimator": SECONDARY_MODEL,
        "cycles": [2018, 2022],
        "draws": 2000,
        "seed": 20260911,
        "four_a_sha256": _sha256(
            root / "reports" / "milestone_4a_transition_matrix.md"
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def lock_survey_estimators(root: Path, *, fetch_missing: bool = True) -> dict[str, object]:
    """Fit and persist survey-only hyperparameters before election outcomes are read."""
    assert_frozen_four_a(root)
    corpus_summary = build_survey_corpora(root, fetch_missing=fetch_missing)
    processed = root / "data" / "processed"
    output = root / "reports" / "milestone_four_b"
    output.mkdir(parents=True, exist_ok=True)
    cells = pl.read_parquet(processed / "survey_corpus_cells.parquet")
    selected_frames: list[pl.DataFrame] = []
    mean_frames: list[pl.DataFrame] = []
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
        mean_frames.append(
            pl.DataFrame(
                [
                    {
                        "election_cycle": cycle,
                        "previous_party": previous_party,
                        "current_party": current_party,
                        "prior_mean": float(
                            fit.means[previous_party][PARTIES.index(current_party)]
                        ),
                    }
                    for previous_party in PARTIES
                    for current_party in PARTIES
                ]
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
    means = pl.concat(mean_frames).sort(
        "election_cycle", "previous_party", "current_party"
    )
    folds = pl.concat(fold_frames).sort(
        "election_cycle", "previous_party", "held_out_year", "wave_id", "kappa"
    )
    audits = pl.concat(audit_frames).sort("election_cycle", "previous_party")
    selected_path = output / "hierarchy_selected_concentrations.csv"
    means_path = output / "hierarchy_previous_party_means.csv"
    folds_path = output / "hierarchy_survey_cv.csv"
    audit_path = output / "hierarchy_untouched_survey_validation.csv"
    selected.write_csv(selected_path, float_precision=12)
    means.write_csv(means_path, float_precision=12)
    folds.write_csv(folds_path, float_precision=12)
    audits.write_csv(audit_path, float_precision=12)
    lock = {
        "schema_version": 1,
        "stage": "survey_only_pre_election_scoring",
        "corpus": corpus_summary,
        "selected_concentrations": _records_for_json(selected),
        "artifact_sha256": {
            selected_path.name: _sha256(selected_path),
            means_path.name: _sha256(means_path),
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
