from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import polars as pl

from valforecast.evaluation.milestone_four_a import (
    _bloc_predictions,
    _district_size_metrics,
    _group_metrics,
    _national_vector,
    _urbanity_metrics,
)
from valforecast.evaluation.milestone_four_b import (
    _frame_party_vector,
    _target_survey_inputs,
    assert_frozen_four_a,
    posterior_mean_predictions,
)
from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS
from valforecast.models.baselines import evaluate_predictions
from valforecast.models.regional_transition import (
    attach_regions,
    build_regional_proportional_predictions,
    build_regional_transition_predictions,
    eval_region_coverage,
    posterior_mean_regional_predictions,
    previous_region_vectors,
    score_prepared_regional_matrices,
)
from valforecast.models.transition_matrix import build_poll_state_proportional_predictions
from valforecast.polls.region_condition import (
    build_regional_point_margins,
    calibrate_region_transition_matrices,
    infer_region_effective_sample_sizes,
    reconcile_regional_draws,
    reconcile_regional_margins,
    sample_regional_margin_draws,
)
from valforecast.polls.region_ingest import load_target_regional_inputs
from valforecast.polls.transition_posterior import estimate_survey_transition

PRIMARY_MODEL = "R1_region_transition"
NATIONAL_TRANSITION = "T_nat"
NATIONAL_BASELINE = "B2_national"
REGIONAL_BASELINE = "B2_region"
PUBLISHED_POINT_MODEL = "R1_published_point"
SUPPRESSION_ZERO_MODEL = "R1_suppressed_zero"
EXPECTED_POPULATIONS = {2018: 4631, 2022: 4164}
RANDOM_SEED = 20260911
SURVEY_DRAWS = 2000
FROZEN_FOUR_B_REPORT = "16da240fe08876646fd97a60c53f5b01ac7d87e042b3b492051324a17533e7f7"
FROZEN_FOUR_B_LOCK = "2820459aeff35a3a1c40c969ecd9c3797efabce466a39a44fbbfc3dcdf4265d5"
LOCK_STAGE = "survey_only_pre_election_scoring"
COMPARISON_REGIONAL = "regional_value"
COMPARISON_TRANSITION = "transition_value"
GATE_INTERVALS = {
    COMPARISON_REGIONAL: "region_draws_fixed_transition",
    COMPARISON_TRANSITION: "transition_draws_fixed_region_margins",
}
PREDICTION_COLUMNS = [
    "transition_id",
    "district_id",
    "municipality_id",
    "county_id",
    "region_id",
    "party",
    "model",
    "actual_share",
    "predicted_share",
    "previous_share",
    "valid_votes",
    "previous_valid_votes",
]


def lock_region_estimators(root: Path) -> dict[str, object]:
    """Persist survey-only regional margins before 2018/2022 outcomes are read."""
    assert_frozen_predecessors(root)
    output = root / "reports" / "milestone_four_c"
    output.mkdir(parents=True, exist_ok=True)
    regional = load_target_regional_inputs(root)
    national = _target_survey_inputs(root)
    cell_frames: list[pl.DataFrame] = []
    point_frames: list[pl.DataFrame] = []
    ess_frames: list[pl.DataFrame] = []
    reconciled_frames: list[pl.DataFrame] = []
    previous_frames: list[pl.DataFrame] = []
    weight_frames: list[pl.DataFrame] = []
    poll_frames: list[pl.DataFrame] = []
    for cycle in (2018, 2022):
        cells = regional[cycle]
        poll = _frame_party_vector(national[cycle]["poll"], "party", "poll_share")
        previous_results = pl.read_parquet(
            root / "data" / "processed" / f"election_results_{cycle - 4}.parquet"
        )
        previous_by_region, region_weights = previous_region_vectors(previous_results)
        point = build_regional_point_margins(cells)
        n_eff = infer_region_effective_sample_sizes(cells)
        reconciled = reconcile_regional_margins(point.margins, region_weights, poll)
        cell_frames.append(cells.with_columns(pl.lit(cycle).alias("election_cycle")))
        ess_frames.append(n_eff.rows.with_columns(pl.lit(cycle).alias("election_cycle")))
        poll_frames.append(
            pl.DataFrame(
                {
                    "election_cycle": [cycle] * len(PARTIES),
                    "party": list(PARTIES),
                    "poll_share": poll.tolist(),
                }
            )
        )
        for region_index, region_id in enumerate(REGION_IDS):
            weight_frames.append(
                pl.DataFrame(
                    {
                        "election_cycle": [cycle],
                        "region_id": [region_id],
                        "weight": [float(region_weights[region_index])],
                    }
                )
            )
            for party_index, party in enumerate(PARTIES):
                point_frames.append(
                    pl.DataFrame(
                        {
                            "election_cycle": [cycle],
                            "region_id": [region_id],
                            "party": [party],
                            "published_share": [float(point.margins[region_index, party_index])],
                        }
                    )
                )
                reconciled_frames.append(
                    pl.DataFrame(
                        {
                            "election_cycle": [cycle],
                            "region_id": [region_id],
                            "party": [party],
                            "reconciled_share": [
                                float(reconciled[region_index, party_index])
                            ],
                        }
                    )
                )
                previous_frames.append(
                    pl.DataFrame(
                        {
                            "election_cycle": [cycle],
                            "region_id": [region_id],
                            "party": [party],
                            "previous_share": [
                                float(previous_by_region[region_index, party_index])
                            ],
                        }
                    )
                )
    artifacts = {
        "regional_cells.csv": pl.concat(cell_frames).sort(
            "election_cycle", "region_id", "party"
        ),
        "regional_point_margins.csv": pl.concat(point_frames).sort(
            "election_cycle", "region_id", "party"
        ),
        "regional_effective_n.csv": pl.concat(ess_frames).sort(
            "election_cycle", "region_id"
        ),
        "reconciled_regional_margins.csv": pl.concat(reconciled_frames).sort(
            "election_cycle", "region_id", "party"
        ),
        "previous_region_vectors.csv": pl.concat(previous_frames).sort(
            "election_cycle", "region_id", "party"
        ),
        "region_weights.csv": pl.concat(weight_frames).sort(
            "election_cycle", "region_id"
        ),
        "national_poll_targets.csv": pl.concat(poll_frames).sort(
            "election_cycle", "party"
        ),
    }
    for name, frame in artifacts.items():
        if "actual_share" in frame.columns or "current_vote_share" in frame.columns:
            raise ValueError("Region lock leaked a target-election column")
        frame.write_csv(output / name, float_precision=12)
    lock = {
        "schema_version": 1,
        "stage": LOCK_STAGE,
        "primary_estimator": PRIMARY_MODEL,
        "secondary_estimator": "omitted_no_survey_only_pooling_lock",
        "random_seed": RANDOM_SEED,
        "survey_draws": SURVEY_DRAWS,
        "three_way_table": "unavailable",
        "pooling": "omitted",
        "gate_intervals": GATE_INTERVALS,
        "artifact_sha256": {name: _sha256(output / name) for name in artifacts},
        "forbidden_inputs_confirmed_absent": [
            "election_results_2022",
            "canonical_temporal_transitions",
            "target_election_actual_share",
        ],
    }
    lock_path = output / "region_estimator_lock.json"
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return lock


def run_milestone_four_c(root: Path) -> dict[str, object]:
    assert_frozen_predecessors(root)
    _assert_clean_region_lock(root)
    output = root / "reports" / "milestone_four_c"
    canonical_all = pl.read_parquet(
        root / "data" / "processed" / "canonical_temporal_transitions.parquet"
    )
    locked_cells = pl.read_csv(output / "regional_cells.csv")
    locked_weights = pl.read_csv(output / "region_weights.csv")
    locked_previous = pl.read_csv(output / "previous_region_vectors.csv")
    national = _target_survey_inputs(root)
    predictions: list[pl.DataFrame] = []
    draw_scores: list[pl.DataFrame] = []
    coverage_frames: list[pl.DataFrame] = []
    ess_frames: list[pl.DataFrame] = []
    for cycle in (2018, 2022):
        transition_id = "2014_2018" if cycle == 2018 else "2018_2022"
        canonical = attach_regions(
            canonical_all.filter(pl.col("transition_id") == transition_id)
        )
        if canonical["to_district_id"].n_unique() != EXPECTED_POPULATIONS[cycle]:
            raise ValueError(f"Frozen {cycle} evaluation population changed")
        previous_national = _national_vector(
            pl.read_parquet(
                root / "data" / "processed" / f"election_results_{cycle - 4}.parquet"
            )
        )
        previous_results = pl.read_parquet(
            root / "data" / "processed" / f"election_results_{cycle - 4}.parquet"
        )
        poll_national = _frame_party_vector(national[cycle]["poll"], "party", "poll_share")
        previous_by_region = _matrix_from_long(
            locked_previous.filter(pl.col("election_cycle") == cycle),
            "previous_share",
        )
        region_weights = np.array(
            [
                float(
                    locked_weights.filter(
                        (pl.col("election_cycle") == cycle)
                        & (pl.col("region_id") == region_id)
                    ).item(0, "weight")
                )
                for region_id in REGION_IDS
            ],
            dtype=float,
        )
        cells = locked_cells.filter(pl.col("election_cycle") == cycle).drop(
            "election_cycle"
        )
        point = build_regional_point_margins(cells)
        n_eff = infer_region_effective_sample_sizes(cells)
        reconciled_point = reconcile_regional_margins(
            point.margins,
            region_weights,
            poll_national,
        )
        margin_draws = sample_regional_margin_draws(
            point,
            n_eff,
            n_draws=SURVEY_DRAWS,
            seed=RANDOM_SEED,
        )
        reconciled_draws = reconcile_regional_draws(
            margin_draws,
            region_weights,
            poll_national,
        )
        if reconciled_draws.draws is None:
            raise AssertionError("Regional margin draws were not reconciled")
        national_estimate = estimate_survey_transition(
            national[cycle]["cells"],
            previous_national=previous_national,
            target_national=poll_national,
            n_draws=SURVEY_DRAWS,
            seed=RANDOM_SEED,
            suppression="flat",
            estimator=NATIONAL_TRANSITION,
        )
        if national_estimate.raked is None:
            raise AssertionError("National 4B draws were not raked")
        t_draws = national_estimate.raked.raked_draws
        t_mean = national_estimate.raked.mean_raked_draws
        t_point = national_estimate.raked.raked_point_matrix
        national_baseline = build_poll_state_proportional_predictions(
            canonical,
            previous_national,
            poll_national,
            model=NATIONAL_BASELINE,
        )
        national_transition = posterior_mean_predictions(
            canonical,
            t_draws,
            model=NATIONAL_TRANSITION,
        )
        regional_baseline = build_regional_proportional_predictions(
            canonical,
            previous_by_region,
            reconciled_point,
            model=REGIONAL_BASELINE,
        )
        r1_matrices = np.empty(
            (SURVEY_DRAWS, len(REGION_IDS), len(PARTIES), len(PARTIES)),
            dtype=float,
        )
        for draw_index, matrix in enumerate(t_draws):
            r1_matrices[draw_index] = calibrate_region_transition_matrices(
                matrix,
                previous_by_region,
                reconciled_point,
            )
        primary = posterior_mean_regional_predictions(
            canonical,
            r1_matrices,
            model=PRIMARY_MODEL,
        )
        published_point = build_regional_transition_predictions(
            canonical,
            calibrate_region_transition_matrices(
                t_point,
                previous_by_region,
                reconciled_point,
            ),
            model=PUBLISHED_POINT_MODEL,
        )
        zero_point = build_regional_point_margins(cells, suppression="zero_renormalize")
        zero_reconciled = reconcile_regional_margins(
            zero_point.margins,
            region_weights,
            poll_national,
        )
        suppression_zero = build_regional_transition_predictions(
            canonical,
            calibrate_region_transition_matrices(
                t_mean,
                previous_by_region,
                zero_reconciled,
            ),
            model=SUPPRESSION_ZERO_MODEL,
        )
        cycle_predictions = [
            _ensure_region_id(frame, canonical)
            for frame in (
                national_baseline,
                national_transition,
                regional_baseline,
                primary,
                published_point,
                suppression_zero,
            )
        ]
        predictions.extend(cycle_predictions)
        transition_vs_t = score_prepared_regional_matrices(
            canonical,
            r1_matrices,
            national_transition,
            comparison_name=COMPARISON_REGIONAL,
        )
        transition_vs_b2 = score_prepared_regional_matrices(
            canonical,
            r1_matrices,
            regional_baseline,
            comparison_name=COMPARISON_TRANSITION,
        )
        region_matrices = np.empty_like(r1_matrices)
        for draw_index, regional_targets in enumerate(reconciled_draws.draws):
            region_matrices[draw_index] = calibrate_region_transition_matrices(
                t_mean,
                previous_by_region,
                regional_targets,
            )
        region_vs_t = score_prepared_regional_matrices(
            canonical,
            region_matrices,
            national_transition,
            comparison_name=COMPARISON_REGIONAL,
        )
        region_vs_b2 = score_prepared_regional_matrices(
            canonical,
            region_matrices,
            regional_baseline,
            comparison_name=COMPARISON_TRANSITION,
        )
        joint = _score_joint_draws(
            canonical,
            t_draws,
            previous_by_region,
            reconciled_draws.draws,
            national_transition,
            regional_baseline,
        )
        draw_scores.extend(
            [
                _analysis_frame(
                    transition_vs_t,
                    election_cycle=cycle,
                    analysis="transition_draws_fixed_region_margins",
                ),
                _analysis_frame(
                    transition_vs_b2,
                    election_cycle=cycle,
                    analysis="transition_draws_fixed_region_margins",
                ),
                _analysis_frame(
                    region_vs_t,
                    election_cycle=cycle,
                    analysis="region_draws_fixed_transition",
                ),
                _analysis_frame(
                    region_vs_b2,
                    election_cycle=cycle,
                    analysis="region_draws_fixed_transition",
                ),
                _analysis_frame(
                    joint,
                    election_cycle=cycle,
                    analysis="independent_joint_draws_labeled_dependence_approximation",
                ),
            ]
        )
        coverage_frames.append(
            eval_region_coverage(previous_results, canonical).with_columns(
                pl.lit(cycle).alias("election_cycle")
            )
        )
        ess_frames.append(n_eff.rows.with_columns(pl.lit(cycle).alias("election_cycle")))

    all_predictions = pl.concat(predictions)
    metrics = _point_metrics(all_predictions)
    comparison = _point_gate_comparison(metrics)
    all_draw_scores = pl.concat(draw_scores).sort(
        "election_cycle", "analysis", "comparison", "draw"
    )
    intervals = _survey_intervals(all_draw_scores)
    verdict = four_c_verdict(comparison, intervals)
    party_metrics = _group_metrics(all_predictions, ["party"])
    bloc_metrics = _group_metrics(_bloc_predictions(all_predictions), ["party"])
    region_metrics = _group_metrics(_with_regions(all_predictions, canonical_all), ["region_id"])
    county_metrics = _group_metrics(all_predictions, ["county_id"])
    robustness = _four_c_county_robustness(all_predictions)
    size_metrics = _district_size_metrics(all_predictions)
    urbanity_metrics = _urbanity_metrics(all_predictions, root)
    municipality_bootstrap = _four_c_municipality_bootstrap(all_predictions)
    artifacts = {
        "model_metrics.csv": metrics,
        "point_comparison.csv": comparison,
        "survey_draw_metrics.csv": all_draw_scores,
        "survey_intervals.csv": intervals,
        "model_metrics_by_party.csv": party_metrics,
        "model_metrics_by_bloc.csv": bloc_metrics,
        "model_metrics_by_region.csv": region_metrics,
        "model_metrics_by_county.csv": county_metrics,
        "geographic_robustness.csv": robustness,
        "model_metrics_by_district_size.csv": size_metrics,
        "model_metrics_by_urbanity.csv": urbanity_metrics,
        "municipality_bootstrap.csv": municipality_bootstrap,
        "eval_region_coverage.csv": pl.concat(coverage_frames).sort(
            "election_cycle", "region_id"
        ),
        "target_wave_effective_n.csv": pl.concat(ess_frames).sort(
            "election_cycle", "region_id"
        ),
    }
    for name, frame in artifacts.items():
        frame.write_csv(output / name, float_precision=12)
    _write_four_c_report(
        root / "reports" / "milestone_4c_regional_conditioning.md",
        metrics,
        comparison,
        intervals,
        artifacts["target_wave_effective_n.csv"],
        artifacts["eval_region_coverage.csv"],
        robustness,
        municipality_bootstrap,
        verdict,
    )
    summary = {
        "verdict": verdict,
        "primary_estimator": PRIMARY_MODEL,
        "cycles": [2018, 2022],
        "draws": SURVEY_DRAWS,
        "seed": RANDOM_SEED,
        "four_b_sha256": _sha256(root / "reports" / "milestone_4b_transition_posterior.md"),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def four_c_verdict(point_comparison: pl.DataFrame, intervals: pl.DataFrame) -> str:
    required = point_comparison.filter(
        pl.col("comparison").is_in([COMPARISON_REGIONAL, COMPARISON_TRANSITION])
    )
    if required.select("election_cycle", "comparison").n_unique() != 4:
        raise ValueError("The 4C gate requires both comparisons in both cycles")
    if required.filter(pl.col("delta_mae") >= 0).height:
        return "NOT_SUPPORTED"
    regional_gate = (pl.col("comparison") == COMPARISON_REGIONAL) & (
        pl.col("analysis") == GATE_INTERVALS[COMPARISON_REGIONAL]
    )
    transition_gate = (pl.col("comparison") == COMPARISON_TRANSITION) & (
        pl.col("analysis") == GATE_INTERVALS[COMPARISON_TRANSITION]
    )
    gate_intervals = intervals.filter(regional_gate | transition_gate)
    if gate_intervals.select("election_cycle", "comparison").n_unique() != 4:
        raise ValueError("The 4C gate is missing a required survey interval")
    if gate_intervals.filter(pl.col("interval_high") >= 0).is_empty():
        return "SUPPORTED"
    return "UNCLEAR"


def assert_frozen_predecessors(root: Path) -> None:
    assert_frozen_four_a(root)
    report = root / "reports" / "milestone_4b_transition_posterior.md"
    observed = hashlib.sha256(report.read_bytes()).hexdigest()
    if observed != FROZEN_FOUR_B_REPORT:
        raise ValueError(f"Frozen Milestone 4B report changed: {observed}")
    lock = root / "reports" / "milestone_four_b" / "survey_estimator_lock.json"
    observed_lock = hashlib.sha256(lock.read_bytes()).hexdigest()
    if observed_lock != FROZEN_FOUR_B_LOCK:
        raise ValueError(f"Frozen Milestone 4B survey lock changed: {observed_lock}")


def _assert_clean_region_lock(root: Path) -> None:
    output = root / "reports" / "milestone_four_c"
    lock_path = output / "region_estimator_lock.json"
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    if document.get("stage") != LOCK_STAGE:
        raise ValueError("Region estimator lock is missing or has the wrong stage")
    hashes = document.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("Region estimator lock has no artifact hashes")
    for name, expected in hashes.items():
        if _sha256(output / str(name)) != expected:
            raise ValueError(f"Locked regional artifact changed: {name}")


def _point_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for transition_id, model in predictions.select(
        "transition_id", "model"
    ).unique().sort("transition_id", "model").iter_rows():
        scored = predictions.filter(
            (pl.col("transition_id") == transition_id) & (pl.col("model") == model)
        )
        rows.append(
            {
                "transition_id": transition_id,
                "election_cycle": 2018 if transition_id == "2014_2018" else 2022,
                **asdict(evaluate_predictions(scored, model=str(model))),
            }
        )
    return pl.DataFrame(rows).sort("election_cycle", "model")


def _point_gate_comparison(metrics: pl.DataFrame) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for cycle in (2018, 2022):
        cycle_metrics = metrics.filter(pl.col("election_cycle") == cycle)
        r1 = float(
            cycle_metrics.filter(pl.col("model") == PRIMARY_MODEL).item(
                0, "district_weighted_mae"
            )
        )
        t_nat = float(
            cycle_metrics.filter(pl.col("model") == NATIONAL_TRANSITION).item(
                0, "district_weighted_mae"
            )
        )
        b2_region = float(
            cycle_metrics.filter(pl.col("model") == REGIONAL_BASELINE).item(
                0, "district_weighted_mae"
            )
        )
        b2_national = float(
            cycle_metrics.filter(pl.col("model") == NATIONAL_BASELINE).item(
                0, "district_weighted_mae"
            )
        )
        rows.extend(
            [
                {
                    "election_cycle": cycle,
                    "comparison": COMPARISON_REGIONAL,
                    "model_mae": r1,
                    "reference_mae": t_nat,
                    "delta_mae": r1 - t_nat,
                    "reference_model": NATIONAL_TRANSITION,
                },
                {
                    "election_cycle": cycle,
                    "comparison": COMPARISON_TRANSITION,
                    "model_mae": r1,
                    "reference_mae": b2_region,
                    "delta_mae": r1 - b2_region,
                    "reference_model": REGIONAL_BASELINE,
                },
                {
                    "election_cycle": cycle,
                    "comparison": "r1_minus_b2_national",
                    "model_mae": r1,
                    "reference_mae": b2_national,
                    "delta_mae": r1 - b2_national,
                    "reference_model": NATIONAL_BASELINE,
                },
            ]
        )
    return pl.DataFrame(rows).sort("election_cycle", "comparison")


def _survey_intervals(draw_scores: pl.DataFrame) -> pl.DataFrame:
    return (
        draw_scores.group_by("election_cycle", "analysis", "comparison")
        .agg(
            pl.col("delta_mae").mean().alias("mean_delta_mae"),
            pl.col("delta_mae").quantile(0.025).alias("interval_low"),
            pl.col("delta_mae").quantile(0.975).alias("interval_high"),
            pl.len().alias("draws"),
        )
        .sort("election_cycle", "analysis", "comparison")
    )


def _analysis_frame(
    scores: pl.DataFrame,
    *,
    election_cycle: int,
    analysis: str,
) -> pl.DataFrame:
    return scores.with_columns(
        pl.lit(election_cycle).alias("election_cycle"),
        pl.lit(analysis).alias("analysis"),
    )


def _score_joint_draws(
    canonical: pl.DataFrame,
    national_draws: np.ndarray,
    previous_by_region: np.ndarray,
    regional_target_draws: np.ndarray,
    t_nat_predictions: pl.DataFrame,
    b2_region_predictions: pl.DataFrame,
) -> pl.DataFrame:
    from valforecast.models.regional_transition import (
        _predict_by_region,
        _regional_proportional_array,
        _weighted_mae,
        regional_canonical_arrays,
    )

    previous, actual, weights, district_ids, region_ids = regional_canonical_arrays(
        canonical
    )
    t_nat = (
        t_nat_predictions.pivot(on="party", index="district_id", values="predicted_share")
        .select("district_id", *PARTIES)
        .sort("district_id")
    )
    if t_nat["district_id"].to_list() != district_ids:
        raise ValueError("T_nat and canonical district populations differ")
    t_nat_values = t_nat.select(PARTIES).to_numpy()
    t_nat_mae = _weighted_mae(t_nat_values, actual, weights)
    rows: list[dict[str, object]] = []
    for draw_index, (national_matrix, regional_targets) in enumerate(
        zip(national_draws, regional_target_draws, strict=True)
    ):
        matrices = calibrate_region_transition_matrices(
            national_matrix,
            previous_by_region,
            regional_targets,
        )
        predicted = _predict_by_region(previous, region_ids, matrices)
        model_mae = _weighted_mae(predicted, actual, weights)
        b2 = _regional_proportional_array(
            previous,
            region_ids,
            previous_by_region,
            regional_targets,
        )
        b2_mae = _weighted_mae(b2, actual, weights)
        rows.append(
            {
                "draw": draw_index,
                "comparison": COMPARISON_REGIONAL,
                "model_mae": model_mae,
                "comparison_mae": t_nat_mae,
                "delta_mae": model_mae - t_nat_mae,
            }
        )
        rows.append(
            {
                "draw": draw_index,
                "comparison": COMPARISON_TRANSITION,
                "model_mae": model_mae,
                "comparison_mae": b2_mae,
                "delta_mae": model_mae - b2_mae,
            }
        )
    return pl.DataFrame(rows)


def _matrix_from_long(frame: pl.DataFrame, value_column: str) -> np.ndarray:
    matrix = np.zeros((len(REGION_IDS), len(PARTIES)), dtype=float)
    by_cell = {
        (str(region_id), str(party)): float(value)
        for region_id, party, value in frame.select(
            "region_id", "party", value_column
        ).iter_rows()
    }
    for region_index, region_id in enumerate(REGION_IDS):
        for party_index, party in enumerate(PARTIES):
            matrix[region_index, party_index] = by_cell[(region_id, party)]
    return matrix


def _ensure_region_id(predictions: pl.DataFrame, canonical: pl.DataFrame) -> pl.DataFrame:
    frame = predictions if "region_id" in predictions.columns else _with_regions(
        predictions, canonical
    )
    return frame.select(PREDICTION_COLUMNS)


def _with_regions(predictions: pl.DataFrame, canonical: pl.DataFrame) -> pl.DataFrame:
    if "region_id" in predictions.columns and predictions["region_id"].null_count() == 0:
        return predictions
    keys = attach_regions(canonical).select(
        "transition_id",
        pl.col("to_district_id").alias("district_id"),
        "region_id",
    ).unique()
    return predictions.join(keys, on=["transition_id", "district_id"])


def _four_c_county_robustness(predictions: pl.DataFrame) -> pl.DataFrame:
    county_metrics = _group_metrics(predictions, ["county_id"])
    rows: list[pl.DataFrame] = []
    district_votes = predictions.select(
        "transition_id", "district_id", "county_id", "valid_votes"
    ).unique()
    county_votes = district_votes.group_by("transition_id", "county_id").agg(
        pl.col("valid_votes").sum().alias("county_votes")
    )
    for reference in (NATIONAL_TRANSITION, REGIONAL_BASELINE):
        baseline = county_metrics.filter(pl.col("model") == reference).select(
            "transition_id",
            "county_id",
            pl.col("weighted_mae").alias("baseline_mae"),
        )
        compared = county_metrics.join(
            baseline,
            on=["transition_id", "county_id"],
        ).with_columns((pl.col("weighted_mae") - pl.col("baseline_mae")).alias("delta_mae"))
        rows.append(
            compared.join(county_votes, on=["transition_id", "county_id"])
            .group_by("transition_id", "model")
            .agg(
                (pl.col("delta_mae") < 0).sum().alias("counties_won"),
                pl.len().alias("counties_total"),
                (
                    pl.when(pl.col("delta_mae") < 0)
                    .then(pl.col("county_votes"))
                    .otherwise(0)
                    .sum()
                    / pl.col("county_votes").sum()
                ).alias("winning_vote_coverage"),
                pl.col("delta_mae").median().alias("median_county_delta"),
            )
            .with_columns(pl.lit(reference).alias("reference_model"))
        )
    return pl.concat(rows).sort("transition_id", "reference_model", "model")


def _four_c_municipality_bootstrap(
    predictions: pl.DataFrame,
    *,
    draws: int = 2000,
    seed: int = RANDOM_SEED,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for transition_id in sorted(predictions["transition_id"].unique().to_list()):
        subset = predictions.filter(pl.col("transition_id") == transition_id)
        for reference, comparison in (
            (NATIONAL_TRANSITION, COMPARISON_REGIONAL),
            (REGIONAL_BASELINE, COMPARISON_TRANSITION),
        ):
            baseline = subset.filter(pl.col("model") == reference).select(
                "district_id",
                "party",
                pl.col("predicted_share").alias("baseline_share"),
            )
            municipality = (
                subset.filter(pl.col("model") == PRIMARY_MODEL)
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
            indices = rng.integers(0, municipality.height, size=(draws, municipality.height))
            sampled = numerators[indices].sum(axis=1) / denominators[indices].sum(axis=1)
            rows.append(
                {
                    "transition_id": transition_id,
                    "comparison": comparison,
                    "reference_model": reference,
                    "observed_delta_mae": float(numerators.sum() / denominators.sum()),
                    "ci_low": float(np.quantile(sampled, 0.025)),
                    "ci_high": float(np.quantile(sampled, 0.975)),
                    "draws": draws,
                    "cluster_unit": "municipality",
                }
            )
    return pl.DataFrame(rows).sort("transition_id", "comparison")


def _write_four_c_report(
    path: Path,
    metrics: pl.DataFrame,
    comparison: pl.DataFrame,
    intervals: pl.DataFrame,
    effective_n: pl.DataFrame,
    coverage: pl.DataFrame,
    robustness: pl.DataFrame,
    municipality_bootstrap: pl.DataFrame,
    verdict: str,
) -> None:
    metric_rows = "\n".join(
        "| {cycle} | {model} | {mae:.3f} |".format(
            cycle=int(row["election_cycle"]),
            model=row["model"],
            mae=100 * float(row["district_weighted_mae"]),
        )
        for row in metrics.iter_rows(named=True)
    )
    point_rows = "\n".join(
        "| {cycle} | {comparison} | {model:.3f} | {reference:.3f} | {delta:+.3f} |".format(
            cycle=int(row["election_cycle"]),
            comparison=row["comparison"],
            model=100 * float(row["model_mae"]),
            reference=100 * float(row["reference_mae"]),
            delta=100 * float(row["delta_mae"]),
        )
        for row in comparison.iter_rows(named=True)
    )
    interval_rows = "\n".join(
        "| {cycle} | {analysis} | {comparison} | {mean:+.3f} | [{low:+.3f}, {high:+.3f}] |".format(
            cycle=int(row["election_cycle"]),
            analysis=row["analysis"],
            comparison=row["comparison"],
            mean=100 * float(row["mean_delta_mae"]),
            low=100 * float(row["interval_low"]),
            high=100 * float(row["interval_high"]),
        )
        for row in intervals.iter_rows(named=True)
    )
    ess_rows = "\n".join(
        "| {cycle} | {region} | {n_eff:.1f} | {valid} |".format(
            cycle=int(row["election_cycle"]),
            region=row["region_id"],
            n_eff=float(row["n_eff"]),
            valid=int(row["valid_cells"]),
        )
        for row in effective_n.iter_rows(named=True)
    )
    coverage_rows = "\n".join(
        "| {cycle} | {region} | {coverage:.1%} |".format(
            cycle=int(row["election_cycle"]),
            region=row["region_id"],
            coverage=float(row["eval_coverage"]),
        )
        for row in coverage.iter_rows(named=True)
    )
    robustness_rows = "\n".join(
        "| {transition} | {model} | {reference} | {won}/{total} | "
        "{coverage:.1%} | {delta:+.3f} |".format(
            transition=row["transition_id"],
            model=row["model"],
            reference=row["reference_model"],
            won=int(row["counties_won"]),
            total=int(row["counties_total"]),
            coverage=float(row["winning_vote_coverage"]),
            delta=100 * float(row["median_county_delta"]),
        )
        for row in robustness.filter(pl.col("model") == PRIMARY_MODEL).iter_rows(named=True)
    )
    bootstrap_rows = "\n".join(
        "| {transition} | {comparison} | {delta:+.3f} | [{low:+.3f}, {high:+.3f}] |".format(
            transition=row["transition_id"],
            comparison=row["comparison"],
            delta=100 * float(row["observed_delta_mae"]),
            low=100 * float(row["ci_low"]),
            high=100 * float(row["ci_high"]),
        )
        for row in municipality_bootstrap.iter_rows(named=True)
    )
    if robustness.height == 0:
        raise ValueError("Geographic robustness is empty")
    path.write_text(
        f"""# Milestone 4C — Regional conditioning

## Status

Milestone 4B remains byte-for-byte frozen as **SUPPORTED**. Its report SHA-256
is `{FROZEN_FOUR_B_REPORT}`. This is a separate 4C result and does not revise
4A or 4B.

Verdict: **{verdict}**

## What 4C can test

Public PSU data has no `previous_party × current_party × region` table. 4C
therefore does not estimate an observed three-way joint. It keeps the locked
national 4B kernel `T1_no_point_shrinkage_raked` and calibrates region-specific
copies to vintage-correct Vid12 current-vote margins.

`R1 − T_nat` is the value of regional current-state information. `R1 −
B2_region` is the value of the voter-flow kernel given the same regional
polls. A win only against national B2 is not evidence of regional transition
structure. Secondary pooling is omitted because no vintage-correct historical
regional val-idag series is available for both holdouts.

## Survey-only lock

All regional estimator choices were committed before election scoring. The
2018 margins come from the original 2018-06-05 news table, not live revised
`Vid12` `2018M05`. The 2022 margins come from the 2022-06-02 news table and
match the pinned Vid12 extract. Region weights are full-Sweden
previous-election valid votes. The eight-group codebook is county-nested
except Stockholm municipality.

Approximate regional n_eff is the median margin-inverted cell size. It is not
Kish ESS, and it is not capped when the public regional base is missing.

| Cycle | Region | Approx. n_eff | Valid cells |
|---:|---|---:|---:|
{ess_rows}

## Locked point backtest

MAE is vote-weighted over district-party cells on the unchanged 4A/4B
populations. Negative ΔMAE is improvement.

| Election | Model | Weighted MAE (pp) |
|---:|---|---:|
{metric_rows}

| Election | Comparison | R1 MAE (pp) | Reference MAE (pp) | ΔMAE (pp) |
|---:|---|---:|---:|---:|
{point_rows}

R1 is worse than the national 4B kernel in both years: +0.145 pp in 2018 and
+0.193 pp in 2022. That fails the regional-value gate on the point comparison
alone, so the locked verdict is NOT_SUPPORTED. R1 is slightly better than
regional proportional swing on the point estimate, but that cannot rescue the
gate. R1 is also worse than national B2. The published-point and
zero-suppression sensitivities stay close to the primary and do not change
the comparison.

## Survey-draw intervals

`regional_value` uses region-draw intervals with the national kernel fixed.
`transition_value` uses transition-draw intervals with regional margins
fixed. Joint intervals are a dependence approximation: both tables come from
the same PSU respondents and no public covariance is published.

| Cycle | Analysis | Comparison | Mean ΔMAE (pp) | 95% interval |
|---|---|---|---|---|
{interval_rows}

The region-draw intervals for `regional_value` lie entirely above zero in
both years. The transition-draw interval for `transition_value` is negative
in 2018 and crosses zero in 2022. None of this is used to choose a different
estimator after seeing the scores.

## Evaluation coverage

The frozen eval populations are 4,631 districts in 2018 and 4,164 in 2022.
Region weights remain full-Sweden previous-election valid votes.
Eval-population coverage is reported separately and does not select the
model.

| Cycle | Region | Eval coverage of previous votes |
|---|---|---|
{coverage_rows}

## Geographic diagnostics

County splits and municipality-cluster bootstrap are diagnostics only.

| Transition | Model | Reference | Counties won | Winning vote coverage | Median county ΔMAE (pp) |
|---|---|---|---|---|---|
{robustness_rows}

| Transition | Comparison | Observed ΔMAE (pp) | Bootstrap 95% |
|---|---|---|---|
{bootstrap_rows}

## What this does not say

4C does not overturn 4B. The national stated-party kernel still beats
national proportional swing. What fails here is the added regional
current-state step: grafting SCB's eight published val-idag margins onto that
kernel makes the district forecast worse on the locked holdouts. 4C also does
not estimate a directly observed regional voter-flow interaction, and it does
not implement demographic MRP.
""",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
