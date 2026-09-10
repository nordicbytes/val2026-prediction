from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
import polars as pl

from valforecast.evaluation.diagnostics import read_skr_municipality_groups
from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import evaluate_predictions
from valforecast.models.transition_matrix import (
    aggregate_party_shares,
    build_poll_state_proportional_predictions,
    build_transition_predictions,
)
from valforecast.polls.schema import PollWave
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_ingest import (
    extract_scb_2018_pdf_national_poll,
    extract_scb_2018_pdf_transition,
    extract_scb_national_poll,
    extract_scb_transition_cells,
    read_pxweb_jsonstat,
    validate_scb_2022_wave,
)
from valforecast.polls.transition_normalize import (
    matrix_to_frame,
    normalize_transition_cells,
)

PRIMARY_MODEL = "T0_calibrated"
BASELINE_MODEL = "B2_poll_state"
PRIMARY_PRIOR_STRENGTH = 200.0
SENSITIVITY_PRIOR_STRENGTHS = (0.0, 1000.0)


class CycleResult(TypedDict):
    cells: pl.DataFrame
    national_poll: pl.DataFrame
    predictions: list[pl.DataFrame]
    metrics: list[dict[str, object]]
    calibration: list[dict[str, object]]
    matrices: list[pl.DataFrame]
    normalization: pl.DataFrame


def run_milestone_four_a(root: Path) -> dict[str, object]:
    output = root / "reports" / "milestone_four_a"
    processed = root / "data" / "processed"
    output.mkdir(parents=True, exist_ok=True)
    canonical = pl.read_parquet(processed / "canonical_temporal_transitions.parquet")
    _assert_frozen_evaluation_population(canonical)

    available_cycles: list[int] = []
    prediction_frames: list[pl.DataFrame] = []
    metric_rows: list[dict[str, object]] = []
    calibration_rows: list[dict[str, object]] = []
    matrix_frames: list[pl.DataFrame] = []
    normalization_frames: list[pl.DataFrame] = []
    cell_frames: list[pl.DataFrame] = []
    poll_frames: list[pl.DataFrame] = []

    original_2018_path = (
        root / "data" / "raw" / "scb" / "psu" / "psu_may_2018_original.pdf"
    )
    if original_2018_path.exists():
        wave_2018 = PollWave(
            wave_id="scb_2018M05_original",
            election_cycle=2018,
            pollster="SCB",
            fieldwork_start=date(2018, 4, 27),
            fieldwork_end=date(2018, 5, 29),
            publication_date=date(2018, 6, 11),
            forecast_cutoff=date(2018, 6, 11),
            information_level="FULL_TRANSITION_TABLE",
            sample_size=4632,
        )
        wave_2018.validate_strict_cutoff()
        cycle_2018 = _run_cycle(
            wave_2018,
            canonical.filter(pl.col("transition_id") == "2014_2018"),
            processed / "election_results_2014.parquet",
            processed / "election_results_2018.parquet",
            extract_scb_2018_pdf_transition(original_2018_path),
            extract_scb_2018_pdf_national_poll(original_2018_path),
        )
        available_cycles.append(2018)
        prediction_frames.extend(cycle_2018["predictions"])
        metric_rows.extend(cycle_2018["metrics"])
        calibration_rows.extend(cycle_2018["calibration"])
        matrix_frames.extend(cycle_2018["matrices"])
        normalization_frames.append(cycle_2018["normalization"])
        cell_frames.append(cycle_2018["cells"])
        poll_frames.append(cycle_2018["national_poll"])

    transition_path = root / "data" / "raw" / "scb" / "psu" / "transition_2022M05.json"
    national_path = root / "data" / "raw" / "scb" / "psu" / "national_poll_2022M05.json"
    if transition_path.exists() and national_path.exists():
        wave = PollWave(
            wave_id="scb_2022M05",
            election_cycle=2022,
            pollster="SCB",
            fieldwork_start=date(2022, 4, 28),
            fieldwork_end=date(2022, 5, 25),
            publication_date=date(2022, 6, 8),
            forecast_cutoff=date(2022, 6, 8),
            information_level="FULL_TRANSITION_TABLE",
            sample_size=4274,
        )
        wave.validate_strict_cutoff()
        transition_2022 = read_pxweb_jsonstat(transition_path)
        validate_scb_2022_wave(transition_2022)
        cells_2022 = extract_scb_transition_cells(
            transition_2022,
            wave_id=wave.wave_id,
            time_value="2022M05",
        )
        poll_2022 = extract_scb_national_poll(
            read_pxweb_jsonstat(national_path),
            wave_id=wave.wave_id,
            time_value="2022M05",
        )
        cycle = _run_cycle(
            wave,
            canonical.filter(pl.col("transition_id") == "2018_2022"),
            processed / "election_results_2018.parquet",
            processed / "election_results_2022.parquet",
            cells_2022,
            poll_2022,
        )
        available_cycles.append(2022)
        prediction_frames.extend(cycle["predictions"])
        metric_rows.extend(cycle["metrics"])
        calibration_rows.extend(cycle["calibration"])
        matrix_frames.extend(cycle["matrices"])
        normalization_frames.append(cycle["normalization"])
        cell_frames.append(cycle["cells"])
        poll_frames.append(cycle["national_poll"])

    if not prediction_frames:
        raise ValueError("No strict pre-election transition wave is available")
    predictions = pl.concat(prediction_frames)
    metrics = pl.DataFrame(metric_rows).sort("election_cycle", "model")
    calibration = pl.DataFrame(calibration_rows).sort(
        "election_cycle", "prior_strength"
    )
    matrices = pl.concat(matrix_frames)
    normalization = pl.concat(normalization_frames)
    extracted_cells = pl.concat(cell_frames)
    national_polls = pl.concat(poll_frames)
    party_metrics = _group_metrics(predictions, ["party"])
    bloc_metrics = _group_metrics(_bloc_predictions(predictions), ["party"])
    county_metrics = _group_metrics(predictions, ["county_id"])
    robustness = _county_robustness(county_metrics, predictions)
    size_metrics = _district_size_metrics(predictions)
    urbanity_metrics = _urbanity_metrics(predictions, root)
    national_aggregation = aggregate_party_shares(predictions)
    uncertainty = _municipality_bootstrap(predictions)
    regime = _regime_dependence(canonical)
    transition_stability = _transition_stability(matrices)
    coverage = pl.read_csv(
        root / "reports" / "milestone_three" / "transition_coverage.csv"
    ).filter(pl.col("transition_id").is_in(["2014_2018", "2018_2022"]))
    selection_bias = pl.read_csv(
        root
        / "reports"
        / "milestone_three"
        / "selection_bias_by_transition_urbanity.csv"
    ).filter(pl.col("transition_id").is_in(["2014_2018", "2018_2022"]))

    _write_csv(metrics, output / "model_metrics.csv")
    _write_csv(party_metrics, output / "model_metrics_by_party.csv")
    _write_csv(bloc_metrics, output / "model_metrics_by_bloc.csv")
    _write_csv(county_metrics, output / "model_metrics_by_county.csv")
    _write_csv(robustness, output / "geographic_robustness.csv")
    _write_csv(size_metrics, output / "model_metrics_by_district_size.csv")
    _write_csv(urbanity_metrics, output / "model_metrics_by_urbanity.csv")
    _write_csv(
        national_aggregation,
        output / "evaluation_population_aggregation.csv",
    )
    _write_csv(calibration, output / "calibration_diagnostics.csv")
    _write_csv(matrices, output / "transition_matrices.csv")
    _write_csv(normalization, output / "normalization_diagnostics.csv")
    _write_csv(extracted_cells, output / "extracted_transition_cells.csv")
    _write_csv(national_polls, output / "same_wave_national_polls.csv")
    _write_csv(uncertainty, output / "bootstrap_uncertainty.csv")
    _write_csv(regime, output / "regime_dependence.csv")
    _write_csv(transition_stability, output / "transition_stability.csv")
    _write_csv(coverage, output / "evaluation_coverage.csv")
    _write_csv(
        selection_bias,
        output / "selection_bias_by_urbanity.csv",
    )

    conclusion = _gate(metrics, robustness, uncertainty)
    summary: dict[str, object] = {
        "conclusion": conclusion,
        "primary_model": PRIMARY_MODEL,
        "baseline_model": BASELINE_MODEL,
        "available_strict_cycles": available_cycles,
        "blocked_strict_cycles": [],
        "primary_prior_strength": PRIMARY_PRIOR_STRENGTH,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root / "reports" / "milestone_4a_transition_matrix.md",
        metrics,
        party_metrics,
        bloc_metrics,
        robustness,
        calibration,
        matrices,
        normalization,
        uncertainty,
        regime,
        transition_stability,
        coverage,
        conclusion,
    )
    return summary


def _write_csv(frame: pl.DataFrame, path: Path) -> None:
    frame.write_csv(path, float_precision=12)


def _run_cycle(
    wave: PollWave,
    canonical: pl.DataFrame,
    previous_election_path: Path,
    target_election_path: Path,
    cells: pl.DataFrame,
    poll: pl.DataFrame,
) -> CycleResult:
    previous_national = _national_vector(
        pl.read_parquet(previous_election_path)
    )
    poll_national = _frame_party_vector(poll, "party", "poll_share")
    target_national = _national_vector(pl.read_parquet(target_election_path))
    national_poll_mae_full = float(np.mean(np.abs(poll_national - target_national)))
    normalized = normalize_transition_cells(
        cells,
        poll,
        prior_strength=PRIMARY_PRIOR_STRENGTH,
    )
    primary_calibration = calibrate_transition_matrix(
        normalized.shrunk_matrix,
        previous_national,
        poll_national,
    )
    if not primary_calibration.converged:
        raise ValueError("Primary transition calibration did not converge")

    predictions = [
        build_poll_state_proportional_predictions(
            canonical,
            previous_national,
            poll_national,
            model=BASELINE_MODEL,
        ),
        build_transition_predictions(
            canonical,
            normalized.raw_matrix,
            model="T0_raw",
        ),
        build_transition_predictions(
            canonical,
            normalized.shrunk_matrix,
            model="T0_shrunk_uncalibrated",
        ),
        build_transition_predictions(
            canonical,
            primary_calibration.matrix,
            model=PRIMARY_MODEL,
        ),
    ]
    matrices = [
        matrix_to_frame(
            normalized.raw_matrix,
            wave_id=wave.wave_id,
            matrix_stage="NORMALIZED_RAW",
        ),
        matrix_to_frame(
            normalized.shrunk_matrix,
            wave_id=wave.wave_id,
            matrix_stage="SHRUNK_200",
        ),
        matrix_to_frame(
            primary_calibration.matrix,
            wave_id=wave.wave_id,
            matrix_stage="CALIBRATED_200",
        ),
    ]
    calibration_rows = [
        {
            "election_cycle": wave.election_cycle,
            "wave_id": wave.wave_id,
            "prior_strength": PRIMARY_PRIOR_STRENGTH,
            "suppressed_strategy": "allocate_gap",
            "nonparty_strategy": "condition",
            **{
                key: value
                for key, value in asdict(primary_calibration).items()
                if key != "matrix"
            },
        }
    ]
    for prior_strength in SENSITIVITY_PRIOR_STRENGTHS:
        sensitivity = normalize_transition_cells(
            cells,
            poll,
            prior_strength=prior_strength,
        )
        calibrated = calibrate_transition_matrix(
            sensitivity.shrunk_matrix,
            previous_national,
            poll_national,
        )
        model_name = f"T0_calibrated_prior_{int(prior_strength)}"
        predictions.append(
            build_transition_predictions(
                canonical,
                calibrated.matrix,
                model=model_name,
            )
        )
        matrices.append(
            matrix_to_frame(
                calibrated.matrix,
                wave_id=wave.wave_id,
                matrix_stage=f"CALIBRATED_{int(prior_strength)}",
            )
        )
        calibration_rows.append(
            {
                "election_cycle": wave.election_cycle,
                "wave_id": wave.wave_id,
                "prior_strength": prior_strength,
                "suppressed_strategy": "allocate_gap",
                "nonparty_strategy": "condition",
                **{
                    key: value
                    for key, value in asdict(calibrated).items()
                    if key != "matrix"
                },
            }
        )
    if cells["estimate"].null_count():
        zero_sensitivity = normalize_transition_cells(
            cells,
            poll,
            prior_strength=0,
            suppressed_strategy="zero_sensitivity",
        )
        zero_calibrated = calibrate_transition_matrix(
            zero_sensitivity.raw_matrix,
            previous_national,
            poll_national,
        )
        predictions.append(
            build_transition_predictions(
                canonical,
                zero_calibrated.matrix,
                model="T0_calibrated_suppressed_zero",
            )
        )
        matrices.append(
            matrix_to_frame(
                zero_calibrated.matrix,
                wave_id=wave.wave_id,
                matrix_stage="CALIBRATED_SUPPRESSED_ZERO",
            )
        )
        calibration_rows.append(
            {
                "election_cycle": wave.election_cycle,
                "wave_id": wave.wave_id,
                "prior_strength": 0.0,
                "suppressed_strategy": "zero_sensitivity",
                "nonparty_strategy": "condition",
                **{
                    key: value
                    for key, value in asdict(zero_calibrated).items()
                    if key != "matrix"
                },
            }
        )
    nonparty_sensitivity = normalize_transition_cells(
        cells,
        poll,
        prior_strength=0,
        nonparty_strategy="allocate_poll",
    )
    nonparty_calibrated = calibrate_transition_matrix(
        nonparty_sensitivity.raw_matrix,
        previous_national,
        poll_national,
    )
    predictions.append(
        build_transition_predictions(
            canonical,
            nonparty_calibrated.matrix,
            model="T0_calibrated_nonparty_poll",
        )
    )
    matrices.append(
        matrix_to_frame(
            nonparty_calibrated.matrix,
            wave_id=wave.wave_id,
            matrix_stage="CALIBRATED_NONPARTY_POLL",
        )
    )
    calibration_rows.append(
        {
            "election_cycle": wave.election_cycle,
            "wave_id": wave.wave_id,
            "prior_strength": 0.0,
            "suppressed_strategy": "allocate_gap",
            "nonparty_strategy": "allocate_poll",
            **{
                key: value
                for key, value in asdict(nonparty_calibrated).items()
                if key != "matrix"
            },
        }
    )
    metric_rows = []
    for prediction in predictions:
        model = str(prediction["model"][0])
        metric = evaluate_predictions(prediction, model=model)
        metric_rows.append(
            {
                "election_cycle": wave.election_cycle,
                "wave_id": wave.wave_id,
                "forecast_cutoff": wave.forecast_cutoff.isoformat(),
                "national_poll_mae_full": national_poll_mae_full,
                **asdict(metric),
            }
        )
    return CycleResult(
        cells=cells,
        national_poll=poll,
        predictions=predictions,
        metrics=metric_rows,
        calibration=calibration_rows,
        matrices=matrices,
        normalization=normalized.diagnostics.with_columns(
            pl.lit(wave.election_cycle).alias("election_cycle")
        ),
    )


def _national_vector(results: pl.DataFrame) -> np.ndarray:
    totals = results.group_by("canonical_party_code").agg(pl.col("votes").sum())
    by_party = dict(totals.iter_rows())
    vector = np.array([float(by_party.get(party, 0)) for party in PARTIES])
    return np.asarray(vector / vector.sum(), dtype=float)


def _frame_party_vector(
    frame: pl.DataFrame,
    party_column: str,
    value_column: str,
) -> np.ndarray:
    by_party = dict(frame.select(party_column, value_column).iter_rows())
    vector = np.array([float(by_party[party]) for party in PARTIES])
    return np.asarray(vector / vector.sum(), dtype=float)


def _group_metrics(predictions: pl.DataFrame, groups: list[str]) -> pl.DataFrame:
    return (
        predictions.with_columns(
            (pl.col("actual_share") - pl.col("predicted_share"))
            .abs()
            .alias("absolute_error")
        )
        .group_by("transition_id", "model", *groups)
        .agg(
            (
                (pl.col("absolute_error") * pl.col("valid_votes")).sum()
                / pl.col("valid_votes").sum()
            ).alias("weighted_mae"),
            pl.col("valid_votes").sum().alias("weighted_observations"),
        )
        .sort("transition_id", "model", *groups)
    )


def _bloc_predictions(predictions: pl.DataFrame) -> pl.DataFrame:
    bloc_map = {
        "RED_GREEN_2018_TAXONOMY": ("V", "S", "MP"),
        "ALLIANCE_2018_TAXONOMY": ("C", "L", "M", "KD"),
    }
    frames = []
    for bloc, parties in bloc_map.items():
        frames.append(
            predictions.filter(pl.col("party").is_in(parties))
            .group_by(
                "transition_id",
                "district_id",
                "municipality_id",
                "county_id",
                "model",
            )
            .agg(
                pl.lit(bloc).alias("party"),
                pl.col("actual_share").sum(),
                pl.col("predicted_share").sum(),
                pl.col("previous_share").sum(),
                pl.col("valid_votes").first(),
                pl.col("previous_valid_votes").first(),
            )
        )
    return pl.concat(frames)


def _county_robustness(
    county_metrics: pl.DataFrame,
    predictions: pl.DataFrame,
) -> pl.DataFrame:
    baseline = county_metrics.filter(pl.col("model") == BASELINE_MODEL).select(
        "transition_id",
        "county_id",
        pl.col("weighted_mae").alias("baseline_mae"),
    )
    compared = county_metrics.join(
        baseline,
        on=["transition_id", "county_id"],
    ).with_columns(
        (pl.col("weighted_mae") - pl.col("baseline_mae")).alias("delta_mae")
    )
    district_votes = predictions.select(
        "transition_id", "district_id", "county_id", "valid_votes"
    ).unique()
    county_votes = district_votes.group_by("transition_id", "county_id").agg(
        pl.col("valid_votes").sum().alias("county_votes")
    )
    return (
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
        .sort("transition_id", "model")
    )


def _district_size_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    size_frames = []
    for transition_id in predictions["transition_id"].unique().sort():
        district = predictions.filter(
            pl.col("transition_id") == transition_id
        ).select(
            "transition_id", "district_id", "previous_valid_votes"
        ).unique()
        q1, q2, q3 = (
            float(
                cast(
                    float,
                    district["previous_valid_votes"].quantile(probability),
                )
            )
            for probability in (0.25, 0.5, 0.75)
        )
        size_frames.append(
            district.with_columns(
                pl.when(pl.col("previous_valid_votes") <= q1)
                .then(pl.lit("Q1_smallest"))
                .when(pl.col("previous_valid_votes") <= q2)
                .then(pl.lit("Q2"))
                .when(pl.col("previous_valid_votes") <= q3)
                .then(pl.lit("Q3"))
                .otherwise(pl.lit("Q4_largest"))
                .alias("district_size")
            )
        )
    with_size = predictions.join(
        pl.concat(size_frames),
        on=["transition_id", "district_id", "previous_valid_votes"],
    )
    return _group_metrics(with_size, ["district_size"])


def _urbanity_metrics(predictions: pl.DataFrame, root: Path) -> pl.DataFrame:
    groups = read_skr_municipality_groups(
        root
        / "data"
        / "raw"
        / "skr"
        / "kommungruppsindelning_2017_bilaga_4_sida_2.pdf",
        root
        / "data"
        / "raw"
        / "scb"
        / "population"
        / "municipal_population_pre_election_2009_2021.csv",
    )
    return _group_metrics(
        predictions.join(groups, on="municipality_id", how="left"),
        ["municipality_main_group"],
    )


def _municipality_bootstrap(
    predictions: pl.DataFrame,
    *,
    draws: int = 2000,
    seed: int = 20260910,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for transition in predictions["transition_id"].unique().sort():
        subset = predictions.filter(pl.col("transition_id") == transition)
        baseline = subset.filter(pl.col("model") == BASELINE_MODEL).select(
            "district_id",
            "party",
            pl.col("predicted_share").alias("baseline_share"),
        )
        candidate_models = (
            PRIMARY_MODEL,
            *(
                f"T0_calibrated_prior_{int(value)}"
                for value in SENSITIVITY_PRIOR_STRENGTHS
            ),
            "T0_calibrated_suppressed_zero",
            "T0_calibrated_nonparty_poll",
        )
        for model in candidate_models:
            if subset.filter(pl.col("model") == model).is_empty():
                continue
            scored = (
                subset.filter(pl.col("model") == model)
                .join(baseline, on=["district_id", "party"])
                .with_columns(
                    (
                        (pl.col("actual_share") - pl.col("predicted_share")).abs()
                        - (pl.col("actual_share") - pl.col("baseline_share")).abs()
                    ).alias("error_delta")
                )
            )
            municipalities = scored["municipality_id"].unique().sort().to_list()
            bootstrap = np.empty(draws)
            for draw in range(draws):
                sampled = rng.choice(
                    municipalities,
                    size=len(municipalities),
                    replace=True,
                )
                counts = pl.DataFrame(
                    {"municipality_id": sampled}
                ).group_by("municipality_id").len()
                replicated = scored.join(counts, on="municipality_id")
                numerator = float(
                    cast(
                        float,
                        (
                            replicated["error_delta"]
                            * replicated["valid_votes"]
                            * replicated["len"]
                        ).sum(),
                    )
                )
                denominator = float(
                    cast(
                        float,
                        (
                            replicated["valid_votes"] * replicated["len"]
                        ).sum(),
                    )
                )
                bootstrap[draw] = numerator / denominator
            observed = float(
                cast(
                    float,
                    (scored["error_delta"] * scored["valid_votes"]).sum(),
                )
            ) / float(
                cast(float, scored["valid_votes"].sum())
            )
            rows.append(
                {
                    "transition_id": transition,
                    "model": model,
                    "observed_delta_mae": observed,
                    "ci_low": float(np.quantile(bootstrap, 0.025)),
                    "ci_high": float(np.quantile(bootstrap, 0.975)),
                    "draws": draws,
                }
            )
    return pl.DataFrame(rows)


def _regime_dependence(canonical: pl.DataFrame) -> pl.DataFrame:
    prior = canonical.filter(pl.col("transition_id") == "2014_2018").select(
        pl.col("to_district_id").alias("bridge_district_id"),
        "party",
        pl.col("local_residual_swing").alias("residual_2014_2018"),
    )
    current = (
        canonical.filter(pl.col("transition_id") == "2018_2022")
        .with_columns(pl.col("from_district_id").str.split(",").alias("_source"))
        .explode("_source", empty_as_null=True)
        .select(
            pl.col("_source").alias("bridge_district_id"),
            "party",
            pl.col("local_residual_swing").alias("residual_2018_2022"),
        )
    )
    joined = prior.join(current, on=["bridge_district_id", "party"])
    rows = []
    for party in PARTIES:
        subset = joined.filter(pl.col("party") == party)
        pearson = float(
            np.corrcoef(
                subset["residual_2014_2018"],
                subset["residual_2018_2022"],
            )[0, 1]
        )
        ranked = subset.with_columns(
            pl.col("residual_2014_2018").rank().alias("rank_previous"),
            pl.col("residual_2018_2022").rank().alias("rank_current"),
        )
        rows.append(
            {
                "scope": "party",
                "label": party,
                "district_pairs": subset.height,
                "pearson_residual_correlation": pearson,
                "spearman_rank_correlation": float(
                    ranked.select(pl.corr("rank_previous", "rank_current")).item()
                ),
            }
        )
    bloc_definitions = {
        "LEFT_BLOC": ("V", "S", "MP"),
        "ALLIANCE_BLOC": ("C", "L", "M", "KD"),
    }
    for label, parties in bloc_definitions.items():
        subset = joined.filter(pl.col("party").is_in(parties)).group_by(
            "bridge_district_id"
        ).agg(
            pl.col("residual_2014_2018").sum(),
            pl.col("residual_2018_2022").sum(),
        )
        ranked = subset.with_columns(
            pl.col("residual_2014_2018").rank().alias("rank_previous"),
            pl.col("residual_2018_2022").rank().alias("rank_current"),
        )
        rows.append(
            {
                "scope": "bloc",
                "label": label,
                "district_pairs": subset.height,
                "pearson_residual_correlation": float(
                    np.corrcoef(
                        subset["residual_2014_2018"],
                        subset["residual_2018_2022"],
                    )[0, 1]
                ),
                "spearman_rank_correlation": float(
                    ranked.select(pl.corr("rank_previous", "rank_current")).item()
                ),
            }
        )
    return pl.DataFrame(rows)


def _transition_stability(matrices: pl.DataFrame) -> pl.DataFrame:
    raw = matrices.filter(pl.col("matrix_stage") == "NORMALIZED_RAW")
    first = raw.filter(pl.col("wave_id") == "scb_2018M05_original").select(
        "previous_party",
        "current_party",
        pl.col("probability").alias("probability_2018"),
    )
    second = raw.filter(pl.col("wave_id") == "scb_2022M05").select(
        "previous_party",
        "current_party",
        pl.col("probability").alias("probability_2022"),
    )
    joined = first.join(second, on=["previous_party", "current_party"])
    rows = []
    for party in PARTIES:
        subset = joined.filter(pl.col("previous_party") == party)
        rows.append(
            {
                "previous_party": party,
                "pearson_cell_correlation": float(
                    np.corrcoef(
                        subset["probability_2018"],
                        subset["probability_2022"],
                    )[0, 1]
                ),
                "total_variation_distance": float(
                    (
                        subset["probability_2018"]
                        - subset["probability_2022"]
                    ).abs().sum()
                    / 2
                ),
                "maximum_cell_change": float(
                    cast(
                        float,
                        (
                            subset["probability_2018"]
                            - subset["probability_2022"]
                        ).abs().max(),
                    )
                ),
            }
        )
    return pl.DataFrame(rows)


def _assert_frozen_evaluation_population(canonical: pl.DataFrame) -> None:
    expected = {"2014_2018": 4631, "2018_2022": 4164}
    observed = {
        transition: canonical.filter(pl.col("transition_id") == transition)[
            "to_district_id"
        ].n_unique()
        for transition in expected
    }
    if observed != expected:
        raise ValueError(f"Frozen evaluation population changed: {observed}")


def _gate(
    metrics: pl.DataFrame,
    robustness: pl.DataFrame,
    uncertainty: pl.DataFrame,
) -> str:
    primary = metrics.filter(pl.col("model") == PRIMARY_MODEL)
    baseline = metrics.filter(pl.col("model") == BASELINE_MODEL).select(
        "election_cycle",
        pl.col("district_weighted_mae").alias("baseline_mae"),
    )
    comparison = primary.join(baseline, on="election_cycle").with_columns(
        (pl.col("district_weighted_mae") - pl.col("baseline_mae")).alias(
            "delta_mae"
        )
    )
    improved = comparison.filter(pl.col("delta_mae") < 0)
    sensitivity = metrics.filter(
        pl.col("model").str.starts_with("T0_calibrated_prior_")
    ).join(baseline, on="election_cycle").with_columns(
        (pl.col("district_weighted_mae") - pl.col("baseline_mae")).alias(
            "delta_mae"
        )
    )
    if improved.is_empty():
        if sensitivity.filter(pl.col("delta_mae") < 0).height:
            return "UNCLEAR"
        return "FAIL"
    if sensitivity.filter(pl.col("delta_mae") >= 0).height:
        return "UNCLEAR"
    robust = robustness.filter(
        (pl.col("model") == PRIMARY_MODEL)
        & (pl.col("counties_won") > pl.col("counties_total") / 2)
        & (pl.col("winning_vote_coverage") >= 0.5)
    )
    precise = uncertainty.filter(
        (pl.col("model") == PRIMARY_MODEL) & (pl.col("ci_high") < 0)
    )
    if robust.height == improved.height and precise.height == improved.height:
        return "PASS"
    return "WEAK PASS"


def _write_report(
    path: Path,
    metrics: pl.DataFrame,
    party_metrics: pl.DataFrame,
    bloc_metrics: pl.DataFrame,
    robustness: pl.DataFrame,
    calibration: pl.DataFrame,
    matrices: pl.DataFrame,
    normalization: pl.DataFrame,
    uncertainty: pl.DataFrame,
    regime: pl.DataFrame,
    transition_stability: pl.DataFrame,
    coverage: pl.DataFrame,
    conclusion: str,
) -> None:
    baseline = metrics.filter(pl.col("model") == BASELINE_MODEL).select(
        "election_cycle",
        pl.col("district_weighted_mae").alias("baseline_mae"),
    )
    comparison = metrics.join(baseline, on="election_cycle").with_columns(
        (pl.col("district_weighted_mae") - pl.col("baseline_mae")).alias(
            "delta_mae"
        )
    )
    model_rows = "\n".join(
        "| {election_cycle} | {forecast_cutoff} | {model} | "
        "{district_weighted_mae:.5f} | {national_mae:.5f} | "
        "{delta_mae:+.5f} |".format(**row)
        for row in comparison.iter_rows(named=True)
    )
    poll_source_rows = "\n".join(
        f"| {int(row['election_cycle'])} | "
        f"{float(row['national_poll_mae_full']):.5f} |"
        for row in metrics.select(
            "election_cycle", "national_poll_mae_full"
        ).unique().sort("election_cycle").iter_rows(named=True)
    )
    party_rows = "\n".join(
        "| {transition_id} | {party} | {model} | {weighted_mae:.5f} |".format(**row)
        for row in party_metrics.filter(
            pl.col("model").is_in([BASELINE_MODEL, PRIMARY_MODEL])
        ).iter_rows(named=True)
    )
    bloc_rows = "\n".join(
        "| {transition_id} | {party} | {model} | {weighted_mae:.5f} |".format(
            **row
        )
        for row in bloc_metrics.filter(
            pl.col("model").is_in([BASELINE_MODEL, PRIMARY_MODEL])
        ).iter_rows(named=True)
    )
    robustness_rows = "\n".join(
        "| {transition_id} | {model} | {counties_won}/{counties_total} | "
        "{winning_vote_coverage:.1%} | {median_county_delta:+.5f} |".format(
            **row
        )
        for row in robustness.filter(
            pl.col("model").is_in([PRIMARY_MODEL, "T0_raw"])
        ).iter_rows(named=True)
    )
    calibration_rows = "\n".join(
        "| {election_cycle} | {prior_strength:.0f} | {suppressed_strategy} | "
        "{nonparty_strategy} | "
        "{iterations} | {maximum_margin_error:.2e} | "
        "{kl_divergence:.5f} |".format(**row)
        for row in calibration.iter_rows(named=True)
    )
    normalization_rows = "\n".join(
        "| {election_cycle} | {previous_party} | {row_base} | {published_party_cells}/9 | "
        "{unreported_mass:.1%} | {imputed_party_mass:.1%} | {nonparty_mass:.1%} | "
        "{reliability:.1%} | "
        "{normalization_status} |".format(
            **row
        )
        for row in normalization.iter_rows(named=True)
    )
    matrix_sections = "\n\n".join(
        _matrix_markdown(matrices, wave_id=wave_id, matrix_stage=matrix_stage)
        for wave_id in matrices["wave_id"].unique().sort()
        for matrix_stage in ("NORMALIZED_RAW", "CALIBRATED_200")
    )
    uncertainty_rows = "\n".join(
        "| {transition_id} | {model} | {observed_delta_mae:+.5f} | "
        "[{ci_low:+.5f}, {ci_high:+.5f}] |".format(**row)
        for row in uncertainty.iter_rows(named=True)
    )
    regime_rows = "\n".join(
        "| {label} | {district_pairs} | {pearson_residual_correlation:+.3f} | "
        "{spearman_rank_correlation:+.3f} |".format(**row)
        for row in regime.iter_rows(named=True)
    )
    transition_stability_rows = "\n".join(
        "| {previous_party} | {pearson_cell_correlation:+.3f} | "
        "{total_variation_distance:.3f} | {maximum_cell_change:.3f} |".format(
            **row
        )
        for row in transition_stability.iter_rows(named=True)
    )
    coverage_rows = "\n".join(
        "| {transition_id} | {evaluation_districts} | {physical_target_districts} | "
        "{district_coverage:.2%} | {evaluation_valid_votes} | "
        "{vote_coverage:.2%} | {mapping_qualities} |".format(**row)
        for row in coverage.iter_rows(named=True)
    )
    gate_rows = []
    for primary in comparison.filter(
        pl.col("model") == PRIMARY_MODEL
    ).iter_rows(named=True):
        transition_id = (
            "2014_2018"
            if int(primary["election_cycle"]) == 2018
            else "2018_2022"
        )
        primary_robustness = robustness.filter(
            (pl.col("transition_id") == transition_id)
            & (pl.col("model") == PRIMARY_MODEL)
        ).row(0, named=True)
        primary_uncertainty = uncertainty.filter(
            (pl.col("transition_id") == transition_id)
            & (pl.col("model") == PRIMARY_MODEL)
        ).row(0, named=True)
        gate_rows.append(
            "| {cycle} | {model:.3f} | {baseline:.3f} | {delta:+.3f} | "
            "{won}/{total} | [{low:+.3f}, {high:+.3f}] |".format(
                cycle=primary["election_cycle"],
                model=100 * float(primary["district_weighted_mae"]),
                baseline=100 * float(primary["baseline_mae"]),
                delta=100 * float(primary["delta_mae"]),
                won=primary_robustness["counties_won"],
                total=primary_robustness["counties_total"],
                low=100 * float(primary_uncertainty["ci_low"]),
                high=100 * float(primary_uncertainty["ci_high"]),
            )
        )
    gate_summary_rows = "\n".join(gate_rows)
    no_shrink_rows = comparison.filter(
        pl.col("model") == "T0_calibrated_prior_0"
    )
    no_shrink_summary = "; ".join(
        f"{int(row['election_cycle'])}: {100 * float(row['delta_mae']):+.3f} pp"
        for row in no_shrink_rows.iter_rows(named=True)
    )
    suppressed_zero = comparison.filter(
        pl.col("model") == "T0_calibrated_suppressed_zero"
    ).row(0, named=True)
    suppressed_zero_delta = 100 * float(suppressed_zero["delta_mae"])
    nonparty_summary = "; ".join(
        f"{int(row['election_cycle'])}: {100 * float(row['delta_mae']):+.3f} pp"
        for row in comparison.filter(
            pl.col("model") == "T0_calibrated_nonparty_poll"
        ).iter_rows(named=True)
    )
    report = f"""# Milestone 4A — Current-election voter-transition gate

## Frozen predecessor

Milestone 3 remains frozen at commit `ea5def3` with conclusion **FAIL**. No
CORE model, target or gate was retuned.

## Sources

The full source inventory is in `reports/poll_transition_availability.md`.
Only SCB PSU was verified to publish pre-election national previous-vote ×
current-intention tables. The original 2018 publication was recovered through
the Kungliga biblioteket URN resolver; its PDF Table 21 is parsed directly so
current post-2020 PxWeb revisions are not substituted. The strict waves ended
2018-05-29 and 2022-05-25 and were published 2018-06-11 and 2022-06-08.

Both 2018 and 2022 are executable strict holdouts. T-30 through T-1 are
unavailable because no full public transition matrices were found.
No second pollster exposes a comparable full table, so pollster disagreement
or precision-weighted pooling cannot be estimated in 4A.

## Evaluation population

| Transition | Eval n | Target n | District cov. | Eval votes | Vote cov. | Mapping |
|---|---:|---:|---:|---:|---:|---|
{coverage_rows}

Coverage is not representative of all Sweden, especially in 2022. Detailed
metro, larger-town and rural included/excluded shares and population-growth
differences are retained in
`reports/milestone_four_a/selection_bias_by_urbanity.csv`.

## Transition matrix normalization

Published SCB percentages include blank, unknown and—in the 2018 original—
missing-response categories. T0 conditions on a stated party choice.
Suppressed 2022 cells retain their explicit status; remaining rounded row mass
is allocated according to the same-wave national poll prior. Rows are then
shrunk toward that prior using
`n / (n + {PRIMARY_PRIOR_STRENGTH:.0f})`.
`suppressed_zero` is reported only as a lower-bound sensitivity; it does not
reinterpret SCB's `..` cells as observed zeroes.
`nonparty_poll` instead allocates blank/unknown/missing mass by the national
poll before conditioning, testing the primary assumption that stated choosers
represent unresolved respondents within each previous-party row.

| Election | Prev. | n | Published | Omitted | Imputed | Nonparty | Reliability | Status |
|---:|---|---:|---:|---:|---:|---:|---:|---|
{normalization_rows}

The calibrated matrix is the minimum-KL iterative-raking solution whose rows
sum to one and whose national aggregate matches SCB's same-wave `val idag`
vector. No target-election result enters calibration.
Calibration uses the full-Sweden previous-election vector. Because the frozen
evaluation populations cover only 75.01% and 64.86% of votes, their aggregate
predictions are not expected to equal the national poll exactly.

| Election | Prior | Suppressed | Nonparty | Iterations | Max margin error | KL divergence |
|---:|---:|---|---|---:|---:|---:|
{calibration_rows}

### Normalized and calibrated transition matrices

Rows are previous-election party and columns are current intention.

{matrix_sections}

All stages, including shrinkage sensitivities, are in
`reports/milestone_four_a/transition_matrices.csv`.

## Backtest

B2 and T0 receive exactly the same May national poll state within each wave.
Actual target-election results and valid votes are evaluation-only. Negative
ΔMAE is improvement over B2.

| Election | Cutoff | Model | District weighted MAE | Eval-pop aggregate MAE | ΔMAE vs B2 |
|---:|---|---|---:|---:|---:|
{model_rows}

The national poll itself has the following all-Sweden MAE against the eventual
result. It is a source diagnostic and is identical input to every model.

| Election | National poll MAE |
|---:|---:|
{poll_source_rows}

Current-share error and local swing-residual error are algebraically identical
for each district-party cell.

## Per-party error

| Transition | Party | Model | Weighted MAE |
|---|---|---|---:|
{party_rows}

## Bloc error

| Transition | Bloc | Model | Weighted MAE |
|---|---|---|---:|
{bloc_rows}

## Geographic robustness

| Transition | Model | Counties won | Winning vote coverage | Median county ΔMAE |
|---|---|---:|---:|---:|
{robustness_rows}

District-size and fixed SKR-2017 municipality-type breakdowns are retained in
the machine-readable report directory.

## Uncertainty and shrinkage sensitivity

Municipality-cluster bootstrap uses 2,000 seeded replicates.

| Transition | Model | Observed ΔMAE | 95% interval |
|---|---|---:|---:|
{uncertainty_rows}

## Why Milestone 3 failed

The same official district chains show weak or unstable residual rank
relationships across regimes:

| Party/bloc | Matched mapping links | Pearson residual correlation | Spearman rank correlation |
|---|---:|---:|---:|
{regime_rows}

This is diagnostic only; no Milestone 3 model was changed.
Official split mappings are exploded to source-target links, so this count is
not a count of independent districts.
The frozen M3 feature-drift artifact also records sign changes for MP, V and SD
between the 2014→2018 and 2018→2022 regimes. The Spearman column above is the
district-sensitivity ranking comparison.

## Voter-flow stability

Raw conditioned transition rows also change between the May 2018 and May 2022
cycles. Total-variation distance is zero only for identical rows.

| Previous party | Cell correlation | Total-variation distance | Largest cell change |
|---|---:|---:|---:|
{transition_stability_rows}

This comparison is national and unconditional on demographics; the public
strict tables do not support a valid age-specific cross-cycle comparison.

## Limitations

T0 can poststratify only on previous party vote shares known by district. The
2018 table also contains previous non-voter and newly eligible columns, but no
district-level joint distribution exists for those groups; they are excluded.
National calibration therefore absorbs their national contribution into the
previous-party rows rather than locating it geographically. Survey weights and
effective cell counts are not public, and the 2018 `Antal i urvalet` values are
gross previous-party column bases rather than effective sample sizes.

## Gate A

**{conclusion}**

| Election | T0 calibrated | B2 | ΔMAE | Counties won | Municipality-bootstrap 95% |
|---:|---:|---:|---:|---:|---:|
{gate_summary_rows}

The primary `n/(n+200)` matrix loses to B2 in both cycles, while calibrated
no-shrink T0 has ΔMAE {no_shrink_summary}. Both no-shrink improvements have
negative municipality-bootstrap intervals. The 2022 lower-bound suppression
sensitivity has ΔMAE {suppressed_zero_delta:+.3f} pp, so the favorable no-shrink
result is not driven by allocating suppressed mass. The α=200 rule was fixed
in code before the backtest was run, but was not externally preregistered.
Allocating blank/unknown/missing responses by the poll prior gives
{nonparty_summary}. Because these defensible treatment choices reverse or erase
the gain, the transition signal is real enough to investigate but not robust
enough to pass Gate A.

This result tests national voter-flow information from strict May 2018 and May
2022 waves. The gate also requires stability across declared shrinkage
sensitivities and cycles. It does not validate regional transitions,
demographics, MRP or a 2026 forecast. Those stages remain blocked.
"""
    path.write_text(report, encoding="utf-8")


def _matrix_markdown(
    matrices: pl.DataFrame,
    *,
    wave_id: str,
    matrix_stage: str,
) -> str:
    subset = matrices.filter(
        (pl.col("wave_id") == wave_id)
        & (pl.col("matrix_stage") == matrix_stage)
    )
    by_pair = {
        (str(previous), str(current)): float(probability)
        for previous, current, probability in subset.select(
            "previous_party", "current_party", "probability"
        ).iter_rows()
    }
    header = "| Previous | " + " | ".join(PARTIES) + " |"
    separator = "|---|" + "|".join("---:" for _ in PARTIES) + "|"
    rows = [
        "| "
        + previous
        + " | "
        + " | ".join(
            f"{by_pair[(previous, current)]:.3f}" for current in PARTIES
        )
        + " |"
        for previous in PARTIES
    ]
    return "\n".join(
        [f"#### {wave_id} — {matrix_stage}", "", header, separator, *rows]
    )
