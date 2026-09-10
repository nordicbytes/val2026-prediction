from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl

from valforecast.evaluation.diagnostics import read_skr_municipality_groups
from valforecast.features.election_history import PARTIES
from valforecast.features.temporal import (
    TransitionCoverage,
    add_lagged_sensitivity,
    build_canonical_transition,
)
from valforecast.geo.crosswalk import (
    build_stable_id_name_crosswalk,
    read_official_val2014_2018_crosswalk,
    read_official_val2018_2022_crosswalk,
)
from valforecast.ingest.elections import (
    election_quality_issues,
    read_legacy_district_results,
    read_val2018_district_results,
    read_val2022_district_results,
)
from valforecast.models.temporal import (
    TEMPORAL_TESTS,
    canonical_to_model_frame,
    run_temporal_validation,
)

PRIMARY_ABLATION = "C_plus_turnout_size"
PRIMARY_MODEL = "lightgbm"
PRIMARY_TEST_ID = "B0"


def run_milestone_three(root: Path) -> dict[str, object]:
    raw = root / "data" / "raw" / "valmyndigheten"
    processed = root / "data" / "processed"
    interim = root / "data" / "interim"
    output = root / "reports" / "milestone_three"
    for directory in (processed, interim, output):
        directory.mkdir(parents=True, exist_ok=True)

    elections = {
        2010: read_legacy_district_results(
            raw / "2010" / "slutligt_valresultat_valdistrikt_R.skv",
            election_year=2010,
        ),
        2014: read_legacy_district_results(
            raw / "2014" / "2014_riksdagsval_per_valdistrikt.skv",
            election_year=2014,
        ),
        2018: read_val2018_district_results(raw / "2018" / "2018_R_per_valdistrikt.xlsx"),
        2022: read_val2022_district_results(
            raw / "2022" / "roster_per_distrikt_slutligt_riksdag.xlsx"
        ),
    }
    crosswalks = {
        "2010_2014": build_stable_id_name_crosswalk(
            elections[2010],
            elections[2014],
            from_election=2010,
            to_election=2014,
        ),
        "2014_2018": read_official_val2014_2018_crosswalk(raw / "2018" / "mappning_2014_2018.zip"),
        "2018_2022": read_official_val2018_2022_crosswalk(
            raw / "2022" / "jamforelser_2018_2022_valdistrikt_v2.xlsx"
        ),
    }
    for year, frame in elections.items():
        frame.write_parquet(processed / f"election_results_{year}.parquet")
        election_quality_issues(frame).write_parquet(
            interim / f"quality_issues_election_{year}.parquet"
        )
    for transition_id, frame in crosswalks.items():
        frame.write_parquet(processed / f"district_identity_edges_{transition_id}.parquet")

    transition_frames: list[pl.DataFrame] = []
    coverage_rows: list[TransitionCoverage] = []
    for from_election, to_election in ((2010, 2014), (2014, 2018), (2018, 2022)):
        transition_id = f"{from_election}_{to_election}"
        frame, coverage_record = build_canonical_transition(
            elections[from_election],
            elections[to_election],
            crosswalks[transition_id],
            from_election=from_election,
            to_election=to_election,
        )
        transition_frames.append(frame)
        coverage_rows.append(coverage_record)
    canonical = add_lagged_sensitivity(pl.concat(transition_frames, how="diagonal_relaxed"))
    canonical.write_parquet(processed / "canonical_temporal_transitions.parquet")
    coverage_frame = pl.DataFrame([asdict(row) for row in coverage_rows])
    coverage_frame.write_csv(output / "transition_coverage.csv")
    selection = _temporal_selection_bias(
        elections,
        crosswalks,
        root
        / "data"
        / "raw"
        / "scb"
        / "population"
        / "municipal_population_pre_election_2009_2021.csv",
        root / "data" / "raw" / "skr" / "kommungruppsindelning_2017_bilaga_4_sida_2.pdf",
    )
    selection.write_csv(output / "selection_bias_by_transition_urbanity.csv")

    baselines = _descriptive_baselines(canonical)
    baselines.write_csv(output / "descriptive_baselines.csv")
    residual_party = _residual_party_stats(canonical)
    residual_party.write_csv(output / "residual_stats_by_transition_party.csv")
    structure = _residual_structure(canonical, output)
    structure.write_csv(output / "residual_structure_by_transition.csv")

    model_frame = canonical_to_model_frame(canonical)
    metrics, predictions = run_temporal_validation(model_frame)
    metrics.write_csv(output / "temporal_model_metrics.csv")
    predictions.write_parquet(output / "temporal_predictions.parquet")
    per_party = _prediction_breakdown(predictions, ["party"])
    per_party.write_csv(output / "temporal_model_by_party.csv")
    per_county = _prediction_breakdown(predictions, ["county_id"])
    per_county.write_csv(output / "temporal_model_by_county.csv")
    failure_diagnostics = _primary_failure_diagnostics(predictions)
    failure_diagnostics.write_csv(output / "primary_failure_diagnostics.csv")
    relationship_drift = _relationship_drift(canonical)
    relationship_drift.write_csv(output / "feature_residual_relationship_drift.csv")
    robustness = _geographic_robustness(per_county, predictions)
    robustness.write_csv(output / "geographic_robustness.csv")
    uncertainty = _bootstrap_uncertainty(predictions)
    uncertainty.write_csv(output / "bootstrap_uncertainty.csv")

    conclusion = _gate_conclusion(metrics, robustness, uncertainty)
    summary: dict[str, object] = {
        "conclusion": conclusion,
        "primary_model": PRIMARY_MODEL,
        "primary_ablation": PRIMARY_ABLATION,
        "primary_test": PRIMARY_TEST_ID,
        "available_transitions": coverage_frame["transition_id"].to_list(),
        "temporal_tests": [test.test_id for test in TEMPORAL_TESTS],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root / "reports" / "milestone_3_temporal_validation.md",
        coverage_frame,
        baselines,
        metrics,
        per_party,
        robustness,
        uncertainty,
        structure,
        selection,
        failure_diagnostics,
        conclusion,
    )
    return summary


def _temporal_selection_bias(
    elections: dict[int, pl.DataFrame],
    crosswalks: dict[str, pl.DataFrame],
    population_path: Path,
    municipality_groups_path: Path,
) -> pl.DataFrame:
    population_raw = pl.read_csv(population_path, encoding="windows-1252")
    population = (
        population_raw.with_columns(
            pl.col("region").str.split(" ").list.first().alias("municipality_id")
        )
        .filter(pl.col("municipality_id").str.len_chars() == 4)
        .group_by("municipality_id")
        .agg(
            *(
                pl.col(f"Folkmängd {year}").sum().alias(f"population_{year}")
                for year in (2009, 2013, 2017, 2021)
            )
        )
    )
    municipality_groups = read_skr_municipality_groups(municipality_groups_path, population_path)
    population_years = {
        "2010_2014": (2009, 2013),
        "2014_2018": (2013, 2017),
        "2018_2022": (2017, 2021),
    }
    rows: list[dict[str, object]] = []
    for transition_id, (from_year, to_year) in population_years.items():
        to_election = int(transition_id[-4:])
        included_ids = (
            crosswalks[transition_id]
            .filter(pl.col("relation").is_in(["SAME", "COMPARABLE", "MERGED"]))
            .select("to_district_id")
            .unique()
            .with_columns(pl.lit(True).alias("included"))
        )
        districts = (
            elections[to_election]
            .filter(pl.col("district_kind") == "PHYSICAL")
            .group_by("district_id")
            .agg(
                pl.col("municipality_id").first(),
                pl.col("valid_votes").first(),
            )
            .join(
                included_ids,
                left_on="district_id",
                right_on="to_district_id",
                how="left",
            )
            .with_columns(pl.col("included").fill_null(False))
            .join(population, on="municipality_id", how="left")
            .join(municipality_groups, on="municipality_id", how="left")
            .with_columns(
                (pl.col(f"population_{to_year}") / pl.col(f"population_{from_year}") - 1).alias(
                    "population_change"
                )
            )
        )
        for (urbanity, included), subset in districts.group_by(
            "municipality_main_group", "included"
        ):
            rows.append(
                {
                    "transition_id": transition_id,
                    "urbanity_vintage": "SKR_2017_FIXED_DESCRIPTIVE",
                    "municipality_main_group": urbanity,
                    "included": included,
                    "districts": subset.height,
                    "valid_votes": subset["valid_votes"].sum(),
                    "mean_population_change": subset["population_change"].mean(),
                }
            )
    result = pl.DataFrame(rows)
    totals = result.group_by("transition_id", "municipality_main_group").agg(
        pl.col("districts").sum().alias("total_districts"),
        pl.col("valid_votes").sum().alias("total_valid_votes"),
    )
    return (
        result.join(
            totals,
            on=["transition_id", "municipality_main_group"],
        )
        .with_columns(
            (pl.col("districts") / pl.col("total_districts")).alias(
                "district_fraction_within_urbanity"
            ),
            (pl.col("valid_votes") / pl.col("total_valid_votes")).alias(
                "vote_fraction_within_urbanity"
            ),
        )
        .sort("transition_id", "municipality_main_group", "included")
    )


def _descriptive_baselines(canonical: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for transition in canonical["transition_id"].unique().sort():
        subset = canonical.filter(pl.col("transition_id") == transition)
        for model, column in (
            ("B0_no_change", "baseline_no_change_share"),
            ("B1_uniform_swing", "baseline_uniform_swing_share"),
            ("B2_proportional_swing", "baseline_proportional_swing_share"),
        ):
            absolute_error = (subset["current_vote_share"] - subset[column]).abs()
            weighted_mae = float(
                cast(float, (absolute_error * subset["valid_votes"]).sum())
            ) / float(cast(float, subset["valid_votes"].sum()))
            rows.append(
                {
                    "transition_id": transition,
                    "model": model,
                    "weighted_mae": weighted_mae,
                    "districts": subset["to_district_id"].n_unique(),
                }
            )
    return pl.DataFrame(rows)


def _residual_party_stats(canonical: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for (transition, party), subset in canonical.group_by("transition_id", "party"):
        values = subset["local_residual_swing"].to_numpy()
        weights = subset["valid_votes"].to_numpy()
        mean = np.average(values, weights=weights)
        rows.append(
            {
                "transition_id": transition,
                "party": party,
                "weighted_mean": mean,
                "weighted_std": np.sqrt(np.average((values - mean) ** 2, weights=weights)),
            }
        )
    return pl.DataFrame(rows).sort("transition_id", "party")


def _residual_structure(
    canonical: pl.DataFrame,
    output: Path,
) -> pl.DataFrame:
    summary_rows = []
    for transition in canonical["transition_id"].unique().sort():
        subset = canonical.filter(pl.col("transition_id") == transition)
        wide = subset.pivot(
            on="party",
            index=["to_district_id", "valid_votes"],
            values="local_residual_swing",
        ).sort("to_district_id")
        matrix = wide.select(PARTIES).to_numpy()
        weights = wide["valid_votes"].to_numpy()
        mean = np.average(matrix, axis=0, weights=weights)
        centered = matrix - mean
        covariance = (centered * weights[:, None]).T @ centered / weights.sum()
        scales = np.sqrt(np.diag(covariance))
        correlation = covariance / np.outer(scales, scales)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        order = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[order]
        eigenvectors = eigenvectors[:, order]
        explained = eigenvalues / eigenvalues.sum()
        pl.DataFrame(correlation, schema=list(PARTIES), orient="row").insert_column(
            0, pl.Series("party", PARTIES)
        ).write_csv(output / f"residual_correlation_{transition}.csv")
        pl.DataFrame(
            eigenvectors,
            schema=[f"PC{index}" for index in range(1, len(PARTIES) + 1)],
            orient="row",
        ).insert_column(0, pl.Series("party", PARTIES)).write_csv(
            output / f"residual_pca_loadings_{transition}.csv"
        )
        summary_rows.append(
            {
                "transition_id": transition,
                "pc1_explained": explained[0],
                "pc2_explained": explained[1],
                "largest_pc1_loading_party": PARTIES[int(np.argmax(np.abs(eigenvectors[:, 0])))],
            }
        )
    return pl.DataFrame(summary_rows)


def _prediction_breakdown(
    predictions: pl.DataFrame,
    groups: list[str],
) -> pl.DataFrame:
    return (
        predictions.with_columns(
            (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("model_error"),
            (pl.col("actual_share") - pl.col("baseline_share")).abs().alias("baseline_error"),
        )
        .group_by("test_id", "test", "ablation", "model", *groups)
        .agg(
            (
                (pl.col("model_error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
            ).alias("weighted_mae"),
            (
                (pl.col("baseline_error") * pl.col("valid_votes")).sum()
                / pl.col("valid_votes").sum()
            ).alias("baseline_mae"),
        )
        .with_columns((pl.col("weighted_mae") - pl.col("baseline_mae")).alias("delta_mae"))
        .sort("test_id", "ablation", "model", *groups)
    )


def _primary_failure_diagnostics(predictions: pl.DataFrame) -> pl.DataFrame:
    primary = predictions.filter(
        (pl.col("ablation") == PRIMARY_ABLATION) & (pl.col("model") == PRIMARY_MODEL)
    ).with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("model_error"),
        (pl.col("actual_share") - pl.col("baseline_share")).abs().alias("baseline_error"),
    )
    rows = []
    for test_id in primary["test_id"].unique().sort():
        subset = primary.filter(pl.col("test_id") == test_id)
        party = _prediction_breakdown(subset, ["party"])
        for scope, scoped in (
            ("all_parties", subset),
            ("excluding_other", subset.filter(pl.col("party") != "OTHER")),
        ):
            scoped_party = (
                party
                if scope == "all_parties"
                else party.filter(pl.col("party") != "OTHER")
            )
            total_weight = float(cast(float, scoped["valid_votes"].sum()))
            model_mae = (
                float(
                    cast(
                        float,
                        (scoped["model_error"] * scoped["valid_votes"]).sum(),
                    )
                )
                / total_weight
            )
            baseline_mae = (
                float(
                    cast(
                        float,
                        (scoped["baseline_error"] * scoped["valid_votes"]).sum(),
                    )
                )
                / total_weight
            )
            rows.append(
                {
                    "test_id": test_id,
                    "scope": scope,
                    "model_mae": model_mae,
                    "baseline_mae": baseline_mae,
                    "delta_mae": model_mae - baseline_mae,
                    "parties_improved": scoped_party.filter(
                        pl.col("delta_mae") < 0
                    ).height,
                    "parties_total": scoped_party.height,
                    "other_delta_mae": party.filter(pl.col("party") == "OTHER").item(
                        0, "delta_mae"
                    ),
                }
            )
    return pl.DataFrame(rows)


def _relationship_drift(canonical: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for (transition, party), subset in canonical.group_by("transition_id", "party"):
        x = subset["previous_vote_share"].to_numpy()
        y = subset["local_residual_swing"].to_numpy()
        weights = subset["valid_votes"].to_numpy()
        x_centered = x - np.average(x, weights=weights)
        y_centered = y - np.average(y, weights=weights)
        covariance = np.average(x_centered * y_centered, weights=weights)
        denominator = np.sqrt(
            np.average(x_centered**2, weights=weights) * np.average(y_centered**2, weights=weights)
        )
        rows.append(
            {
                "transition_id": transition,
                "party": party,
                "weighted_correlation_previous_share_residual": covariance / denominator,
            }
        )
    return pl.DataFrame(rows).sort("party", "transition_id")


def _geographic_robustness(
    per_county: pl.DataFrame,
    predictions: pl.DataFrame,
) -> pl.DataFrame:
    primary = per_county.filter(
        (pl.col("ablation") == PRIMARY_ABLATION) & (pl.col("model") == PRIMARY_MODEL)
    )
    district_votes = predictions.select(
        "test_id", "district_id", "county_id", "valid_votes"
    ).unique()
    rows = []
    for test_id in primary["test_id"].unique().sort():
        counties = primary.filter(pl.col("test_id") == test_id)
        winning = counties.filter(pl.col("delta_mae") < 0)["county_id"].to_list()
        votes = district_votes.filter(pl.col("test_id") == test_id)
        winning_votes = votes.filter(pl.col("county_id").is_in(winning))["valid_votes"].sum()
        rows.append(
            {
                "test_id": test_id,
                "counties_won": len(winning),
                "counties_total": counties.height,
                "winning_vote_coverage": float(cast(float, winning_votes))
                / float(cast(float, votes["valid_votes"].sum())),
                "median_county_delta_mae": counties["delta_mae"].median(),
            }
        )
    return pl.DataFrame(rows)


def _bootstrap_uncertainty(
    predictions: pl.DataFrame,
    *,
    replicates: int = 2_000,
    random_seed: int = 20260910,
) -> pl.DataFrame:
    primary = predictions.filter(
        (pl.col("ablation") == PRIMARY_ABLATION) & (pl.col("model") == PRIMARY_MODEL)
    )
    rng = np.random.default_rng(random_seed)
    rows = []
    for test_id in primary["test_id"].unique().sort():
        district = (
            primary.filter(pl.col("test_id") == test_id)
            .with_columns(
                (
                    (pl.col("actual_share") - pl.col("predicted_share")).abs()
                    - (pl.col("actual_share") - pl.col("baseline_share")).abs()
                ).alias("error_delta")
            )
            .group_by("district_id", "municipality_id")
            .agg(
                pl.col("error_delta").mean(),
                pl.col("valid_votes").first(),
            )
        )
        for unit in ("district", "municipality"):
            draws = _cluster_bootstrap_delta(
                district,
                cluster_column=("district_id" if unit == "district" else "municipality_id"),
                replicates=replicates,
                rng=rng,
            )
            observed = np.average(
                district["error_delta"].to_numpy(),
                weights=district["valid_votes"].to_numpy(),
            )
            rows.append(
                {
                    "test_id": test_id,
                    "bootstrap_unit": unit,
                    "observed_delta_mae": observed,
                    "ci_low": np.quantile(draws, 0.025),
                    "ci_high": np.quantile(draws, 0.975),
                    "replicates": replicates,
                }
            )
    return pl.DataFrame(rows)


def _cluster_bootstrap_delta(
    district: pl.DataFrame,
    *,
    cluster_column: str,
    replicates: int,
    rng: np.random.Generator,
) -> np.ndarray:
    clusters = district[cluster_column].unique().to_list()
    cluster_stats = (
        district.group_by(cluster_column)
        .agg(
            (pl.col("error_delta") * pl.col("valid_votes")).sum().alias("weighted_error"),
            pl.col("valid_votes").sum().alias("weight"),
        )
        .sort(cluster_column)
    )
    weighted_error = cluster_stats["weighted_error"].to_numpy()
    weight = cluster_stats["weight"].to_numpy()
    draws = np.empty(replicates)
    for replicate in range(replicates):
        sampled = rng.integers(0, len(clusters), len(clusters))
        draws[replicate] = weighted_error[sampled].sum() / weight[sampled].sum()
    return draws


def _gate_conclusion(
    metrics: pl.DataFrame,
    robustness: pl.DataFrame,
    uncertainty: pl.DataFrame,
) -> str:
    primary = metrics.filter(
        (pl.col("ablation") == PRIMARY_ABLATION)
        & (pl.col("model") == PRIMARY_MODEL)
        & (pl.col("test_id") == PRIMARY_TEST_ID)
    )
    improved_tests = primary.filter(pl.col("delta_mae") < 0)["test_id"].to_list()
    robust_tests = robustness.filter(
        (pl.col("test_id") == PRIMARY_TEST_ID)
        & (pl.col("counties_won") > pl.col("counties_total") / 2)
        & (pl.col("winning_vote_coverage") >= 0.5)
    )["test_id"].to_list()
    precise_tests = uncertainty.filter(
        (pl.col("test_id") == PRIMARY_TEST_ID)
        & (pl.col("bootstrap_unit") == "municipality")
        & (pl.col("ci_high") < 0)
    )["test_id"].to_list()
    if (
        improved_tests
        and set(improved_tests).intersection(robust_tests)
        and set(improved_tests).intersection(precise_tests)
    ):
        return "PASS"
    if improved_tests:
        return "UNCLEAR"
    return "FAIL"


def _write_report(
    path: Path,
    coverage: pl.DataFrame,
    baselines: pl.DataFrame,
    metrics: pl.DataFrame,
    per_party: pl.DataFrame,
    robustness: pl.DataFrame,
    uncertainty: pl.DataFrame,
    structure: pl.DataFrame,
    selection: pl.DataFrame,
    failure_diagnostics: pl.DataFrame,
    conclusion: str,
) -> None:
    coverage_rows = "\n".join(
        "| {transition_id} | {evaluation_districts} | {district_coverage:.1%} | "
        "{vote_coverage:.1%} | {mapping_methods} | {mapping_qualities} |".format(
            **row
        )
        for row in coverage.iter_rows(named=True)
    )
    baseline_rows = "\n".join(
        "| {transition_id} | {model} | {weighted_mae:.5f} |".format(**row)
        for row in baselines.iter_rows(named=True)
    )
    model_rows = "\n".join(
        "| {train} | {test} | {ablation} | {model} | {weighted_mae:.5f} | "
        "{baseline_mae:.5f} | {delta_mae:.5f} |".format(**row)
        for row in metrics.filter(~pl.col("model").str.starts_with("B")).iter_rows(named=True)
    )
    primary_party_rows = "\n".join(
        "| {test_id} | {test} | {party} | {weighted_mae:.5f} | "
        "{baseline_mae:.5f} | {delta_mae:.5f} |".format(**row)
        for row in per_party.filter(
            (pl.col("ablation") == PRIMARY_ABLATION) & (pl.col("model") == PRIMARY_MODEL)
        ).iter_rows(named=True)
    )
    robustness_rows = "\n".join(
        "| {test_id} | {counties_won}/{counties_total} | "
        "{winning_vote_coverage:.1%} | {median_county_delta_mae:.5f} |".format(**row)
        for row in robustness.iter_rows(named=True)
    )
    uncertainty_rows = "\n".join(
        "| {test_id} | {bootstrap_unit} | {observed_delta_mae:.5f} | "
        "[{ci_low:.5f}, {ci_high:.5f}] |".format(**row)
        for row in uncertainty.iter_rows(named=True)
    )
    structure_rows = "\n".join(
        "| {transition_id} | {pc1_explained:.1%} | {pc2_explained:.1%} | "
        "{largest_pc1_loading_party} |".format(**row)
        for row in structure.iter_rows(named=True)
    )
    selection_rows = "\n".join(
        "| {transition_id} | {municipality_main_group} | {included} | "
        "{district_fraction_within_urbanity:.1%} | "
        "{vote_fraction_within_urbanity:.1%} | "
        "{mean_population_change:.1%} |".format(**row)
        for row in selection.iter_rows(named=True)
    )
    failure_rows = "\n".join(
        "| {test_id} | {scope} | {model_mae:.5f} | {baseline_mae:.5f} | "
        "{delta_mae:.5f} | {parties_improved}/{parties_total} | "
        "{other_delta_mae:.5f} |".format(**row)
        for row in failure_diagnostics.iter_rows(named=True)
    )
    gate_row = metrics.filter(
        (pl.col("test_id") == PRIMARY_TEST_ID)
        & (pl.col("ablation") == PRIMARY_ABLATION)
        & (pl.col("model") == PRIMARY_MODEL)
    ).row(0, named=True)
    gate_robustness = robustness.filter(
        pl.col("test_id") == PRIMARY_TEST_ID
    ).row(0, named=True)
    m1_population = coverage.filter(
        pl.col("transition_id") == "2018_2022"
    ).row(0, named=True)
    report = f"""# Milestone 3 — Temporal Generalization

## Frozen predecessor

Milestone 2 is frozen at tag `structural-signal-2018-2022-v1`.
Its strict model uses election-history CORE features only. SCB population is
selection-bias diagnostics, not model input.

## Data

| Transition | Districts | District coverage | Vote coverage | Mapping | Quality |
|---|---:|---:|---:|---|---|
{coverage_rows}

The 2010→2014 fallback requires both an identical official district code and
an identical normalized district name. It is a MEDIUM, sensitivity-only
inference, not an official comparison file. The later transitions use official
Valmyndigheten classifications. Exact 2014→2018 publication timing is not
machine-verified; the 2018→2022 file is retrospective/post-election and defines
a backtest evaluation population, not a live forecast universe. The 2018→2022
population is exactly {int(m1_population["comparable_target_districts"]):,}
officially comparable targets plus
{int(m1_population["merged_target_districts"]):,} merged districts, with
{float(m1_population["vote_coverage"]):.2%} vote coverage.
{int(m1_population["split_comparable_target_districts"]):,} of the comparable
targets share one
officially comparable predecessor and are explicitly marked
`OFFICIAL_SPLIT_COMPARABLE`; this preserves the frozen Milestone 1 evaluation
population but is not a one-to-one identity. The 2006→2010 transition remains
unavailable because no official pair mapping or GIS reconstruction was
established. Results never represent every Swedish district.

| Transition | SKR 2017 group | Included | District share | Vote share | Pop. change |
|---|---|---|---:|---:|---:|
{selection_rows}

SKR 2017 groups are held fixed solely for a comparable descriptive
metro/rural split and are not model features. Population changes use the two
pre-election 31 December stocks surrounding each transition. Separate included
and excluded rows are retained in the machine-readable output.

CORE consists of prior party shares, turnout, entropy, concentration, fixed
party-group summaries, largest-party margin and electorate size. RICH is
explicitly reserved for later SCB demographic enrichment and is absent here.

## Baselines

| Transition | Baseline | Weighted MAE |
|---|---|---:|
{baseline_rows}

All district models receive realized national party swing as deliberate oracle
input. Target-election valid votes are evaluation weights only, never model
features. Both roles are recorded in the canonical dataset.

## Descriptive residual structure

| Transition | PC1 | PC2 | Largest absolute PC1 loading |
|---|---:|---:|---|
{structure_rows}

Party residual standard deviations, full correlations and PCA loadings are in
`reports/milestone_three/`. Sum-to-zero closure mechanically induces negative
correlations, so PC1 is not automatically a substantive left-right factor.

## Temporal models

All preprocessing is fit on training transitions only. No test-transition
target is used in scaling, imputation, encoding or fitting. LightGBM with
ablation C is preregistered as the primary model because it won Milestone 2;
Ridge and ElasticNet are comparators. B0 (official 2014→2018 only, tested on
2018→2022) is the strict primary gate. A and pooled B use the non-official
MEDIUM stable-ID 2010→2014 mapping and are sensitivity tests.

| Train | Test | Ablation | Model | Weighted MAE | Baseline | ΔMAE |
|---|---|---|---|---:|---:|---:|
{model_rows}

Negative ΔMAE is improvement over proportional national swing.

## Primary LightGBM per party

| Test ID | Test transition | Party | Model MAE | Baseline MAE | ΔMAE |
|---|---|---|---:|---:|---:|
{primary_party_rows}

## Geographic robustness

| Test | Counties won | Winning vote coverage | Median county ΔMAE |
|---|---:|---:|---:|
{robustness_rows}

## Uncertainty

Districts are not independent. The intervals below therefore include a
municipality-cluster bootstrap as the more defensible diagnostic. They quantify
sampling variation within one realized election, not uncertainty over future
elections.

| Test | Bootstrap unit | Observed ΔMAE | 95% interval |
|---|---|---:|---:|
{uncertainty_rows}

## Ablations

- A: previous party shares only
- B: A plus political structure
- C: B plus prior turnout and electorate size
- D: C plus strictly lagged historical sensitivity
- E: D plus municipality/county identifiers

Historical sensitivity for a target transition uses only the immediately prior
transition and is unavailable where no high-quality identity chain exists.
It could therefore be computed for 2018→2022, but not for its 2014→2018
training rows; D/E were not estimable without importing the MEDIUM 2010→2014
mapping. Geography is never part of the primary model.

## Block experiment

Deferred pending the direct-party temporal result. Milestone 2 showed that
54.5–72.2% of residual energy remains within coalition blocks, so a bloc-first
model is not allowed to replace the party model without temporal evidence.

## Failure diagnostics

| Test | Scope | Model MAE | Baseline MAE | ΔMAE | Parties improved | OTHER ΔMAE |
|---|---|---:|---:|---:|---:|---:|
{failure_rows}

`OTHER` is compositionally unstable across elections because its underlying
minor-party mix changes. Excluding it is diagnostic only and cannot change the
official all-party gate. The feature/residual correlation output shows whether
even simple local relationships preserve sign across transitions.

## Conclusion

**{conclusion}**

In strict B0, LightGBM reaches
{100 * float(gate_row["weighted_mae"]):.3f} pp versus
{100 * float(gate_row["baseline_mae"]):.3f} pp for proportional swing:
ΔMAE {100 * float(gate_row["delta_mae"]):+.3f} pp. It wins
{gate_robustness["counties_won"]}/{gate_robustness["counties_total"]} counties,
covering {float(gate_robustness["winning_vote_coverage"]):.1%} of evaluation
votes.

The decision is based only on preregistered LightGBM CORE ablation C in strict
test B0 against proportional swing on a wholly unseen future election, plus
county robustness. This gate concerns temporal allocation of known national
swing; it does not validate polling, RICH demographics or a 2026 forecast.
"""
    path.write_text(report, encoding="utf-8")
