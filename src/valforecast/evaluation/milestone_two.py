from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast

import polars as pl

from valforecast.evaluation.diagnostics import (
    BLOCKS_2018,
    BLOCKS_2022,
    analyze_selection_bias,
    write_residual_diagnostics,
)
from valforecast.features.election_history import build_structural_model_frame
from valforecast.models.district import run_grouped_oof_models, run_ridge_feature_ablation


def run_milestone_two(root: Path) -> dict[str, object]:
    processed = root / "data" / "processed"
    baseline_dir = root / "reports" / "backtests"
    output_dir = root / "reports" / "milestone_two"
    output_dir.mkdir(parents=True, exist_ok=True)

    results_2018 = pl.read_parquet(processed / "election_results_2018.parquet")
    results_2022 = pl.read_parquet(processed / "election_results_2022.parquet")
    crosswalk = pl.read_parquet(processed / "district_identity_edges_2018_2022.parquet")
    proportional = pl.read_parquet(baseline_dir / "2018_2022_proportional_swing.parquet")

    structural = build_structural_model_frame(results_2018, crosswalk, proportional)
    structural.write_parquet(output_dir / "structural_model_frame.parquet")

    selection = analyze_selection_bias(
        results_2018,
        results_2022,
        crosswalk,
        output_dir,
        root / "data" / "raw" / "scb" / "population" / "municipal_population_2017_2021.csv",
        root / "data" / "raw" / "skr" / "kommungruppsindelning_2017_bilaga_4_sida_2.pdf",
    )
    diagnostics = write_residual_diagnostics(proportional, structural, output_dir)
    municipality_predictions, municipality_metrics = run_grouped_oof_models(
        structural, group_column="municipality_id"
    )
    county_predictions, county_metrics = run_grouped_oof_models(
        structural, group_column="county_id", folds=21
    )
    predictions = pl.concat([municipality_predictions, county_predictions])
    model_metrics = [*municipality_metrics, *county_metrics]
    predictions.write_parquet(output_dir / "grouped_oof_predictions.parquet")
    metrics_frame = pl.DataFrame([asdict(metric) for metric in model_metrics])
    metrics_frame.write_csv(output_dir / "model_comparison.csv")
    run_ridge_feature_ablation(structural).write_csv(output_dir / "ridge_feature_ablation.csv")
    _party_model_comparison(predictions).write_csv(output_dir / "model_comparison_by_party.csv")
    _block_model_comparison(predictions, BLOCKS_2022).write_csv(
        output_dir / "model_comparison_by_block.csv"
    )
    _block_model_comparison(predictions, BLOCKS_2018).write_csv(
        output_dir / "model_comparison_by_block_2018_sensitivity.csv"
    )
    acceptance = _acceptance_summary(predictions)
    acceptance.write_csv(output_dir / "county_loco_acceptance.csv")

    _write_report(output_dir, selection, diagnostics, metrics_frame, acceptance)
    best = (
        metrics_frame.filter(
            (pl.col("grouping") == "county_id") & (pl.col("model") != "proportional_swing")
        )
        .sort("weighted_mae")
        .row(0, named=True)
    )
    return {
        "evaluation_districts": structural.height,
        "selection_features": selection.height,
        "best_model": best["model"],
        "best_grouping": best["grouping"],
        "best_weighted_mae": best["weighted_mae"],
        "baseline_weighted_mae": metrics_frame.filter(pl.col("model") == "proportional_swing").item(
            0, "weighted_mae"
        ),
        **diagnostics,
    }


def _write_report(
    output_dir: Path,
    selection: pl.DataFrame,
    diagnostics: dict[str, object],
    metrics: pl.DataFrame,
    acceptance: pl.DataFrame,
) -> None:
    selection_rows = "\n".join(
        "| {feature} | {included_mean:.4f} | {excluded_mean:.4f} | "
        "{standardized_mean_difference:.3f} |".format(**row)
        for row in selection.sort(pl.col("standardized_mean_difference").abs(), descending=True)
        .head(8)
        .iter_rows(named=True)
    )
    metric_rows = "\n".join(
        "| {model} | {grouping} | {weighted_mae:.5f} | "
        "{improvement_vs_proportional:.5f} | "
        "{relative_improvement:.2%} |".format(**row)
        for row in metrics.iter_rows(named=True)
    )
    urbanity = pl.read_csv(output_dir / "selection_bias_by_urbanity.csv")
    urbanity_summary = (
        urbanity.group_by("municipality_main_group")
        .agg(
            pl.col("districts").sum().alias("districts"),
            pl.when(pl.col("included"))
            .then(pl.col("districts"))
            .otherwise(0)
            .sum()
            .alias("included_districts"),
            pl.col("valid_votes").sum().alias("valid_votes"),
            pl.when(pl.col("included"))
            .then(pl.col("valid_votes"))
            .otherwise(0)
            .sum()
            .alias("included_valid_votes"),
        )
        .with_columns(
            (pl.col("included_districts") / pl.col("districts")).alias("district_inclusion_rate"),
            (pl.col("included_valid_votes") / pl.col("valid_votes")).alias("vote_inclusion_rate"),
        )
    )
    urbanity_rows = "\n".join(
        "| {municipality_main_group} | {district_inclusion_rate:.1%} | "
        "{vote_inclusion_rate:.1%} |".format(**row)
        for row in urbanity_summary.sort("municipality_main_group").iter_rows(named=True)
    )
    max_smd = selection.select(pl.col("standardized_mean_difference").abs().max()).item()
    ablation = pl.read_csv(output_dir / "ridge_feature_ablation.csv")
    ablation_rows = "\n".join(
        "| {grouping} | {feature_set} | {weighted_mae:.5f} | {relative_improvement:.2%} |".format(
            **row
        )
        for row in ablation.iter_rows(named=True)
    )
    acceptance_rows = "\n".join(
        "| {model} | {counties_won}/21 | {winning_county_vote_coverage:.1%} | "
        "{national_mae:.5f} | {materially_worsened_parties} | "
        "{passes_gate} |".format(**row)
        for row in acceptance.iter_rows(named=True)
    )
    equal_within_block_remainder = 1 - cast(float, diagnostics["equal_block_residual_energy_share"])
    prior_within_block_remainder = 1 - cast(
        float, diagnostics["prior_share_block_residual_energy_share"]
    )
    report = f"""# Milestone 2: structural signal before SCB

## Fixed evaluation population

All models use the same 4,164 physical 2022 districts as milestone 1:
4,153 direct official comparisons plus 11 official merged comparisons.
Validation is five-fold municipality-group out-of-fold plus the official
21-fold leave-one-county-out (LOCO) protocol. Primary models use structural
features only; municipality and county identifiers are ablations. Training
weights use 2018 valid votes and evaluation weights use 2022 valid votes.

The model target is `actual_share - proportional_swing_prediction`. The
additive `actual_local_swing - national_percentage_point_swing` remains a
separate diagnostic target.

## Selection bias

Positive standardized differences mean higher values among included districts.
The 2018 political comparison for excluded districts is necessarily a
municipality-level proxy because `NOT_COMPARABLE` districts have no defensible
2018 district predecessor.

| Feature | Included mean | Excluded mean | Standardized difference |
|---|---:|---:|---:|
{selection_rows}

| SKR 2017 main group | District inclusion | Valid-vote inclusion |
|---|---:|---:|
{urbanity_rows}

`municipality_electorate_change` is change in registered eligible voters.
`municipality_population_change` uses SCB's official 2017-12-31 and
2021-12-31 municipal population totals. The time-correct SKR 2017 kommuntyp is
reported separately.

The maximum absolute standardized difference is {max_smd:.3f}. Excluded
districts are concentrated in faster-growing and more metropolitan
municipalities. The frozen 1.469 pp benchmark therefore describes the
officially comparable subset and should not be generalized to all districts.

## Residual structure

- Districts: {diagnostics["districts"]}
- First weighted PCA component: {diagnostics["first_pca_explained_variance"]:.2%}
- Second weighted PCA component: {diagnostics["second_pca_explained_variance"]:.2%}
- Largest residual standard deviation: {diagnostics["largest_residual_std_party"]}
- Largest absolute PC1 loading: {diagnostics["largest_absolute_pc1_loading_party"]}
- Residual energy represented by equal-within-block 2022 coalition totals:
  {diagnostics["equal_block_residual_energy_share"]:.2%}
- Residual energy represented when allocated by prior within-block shares:
  {diagnostics["prior_share_block_residual_energy_share"]:.2%}

Party distributions, correlations, block residuals, size/profile/swing
breakdowns and diagnostic PNGs are in this directory.

PC1 must not be interpreted as a latent left-right factor: its dominant loading
is reported above, residuals have an exact sum-to-zero constraint that induces
negative correlations, and the represented-energy figures include OTHER as a
tautological singleton block. The unexplained within-coalition remainder is
{equal_within_block_remainder:.2%} under equal allocation and
{prior_within_block_remainder:.2%} under prior-share allocation. The primary
block diagnostic and model comparison use the 2022 S-led (V+S+MP+C) and
Tidö/right (L+M+KD+SD) grouping; the 2018
red-green/Alliance grouping is retained in separately labelled sensitivity
outputs.

## Ridge feature ablation

| Held-out group | Feature set | Weighted MAE | Improvement |
|---|---|---:|---:|
{ablation_rows}

The previous vote shares alone beat proportional swing. The next ablation adds
only prior turnout, party entropy and electorate size; exact linear
combinations of party shares are excluded. Geography identifiers do not improve
the stricter held-out-county result.

## Out-of-fold model comparison

Errors are vote-share fractions. The frozen primary benchmark is the
proportional-swing row.

| Model | Held-out group | Weighted MAE | Absolute improvement | Relative improvement |
|---|---|---:|---:|---:|
{metric_rows}

An improvement counts as evidence only if it is positive out-of-fold against
the frozen benchmark. In-sample fit is not reported.

## LOCO acceptance gate

The gate requires lower structure-only MAE than 1.469 pp, wins in at least
11/21 counties covering at least 50% of evaluation votes, national aggregate
MAE at most 0.003, and no more than two parties worsened by over 0.0001.

| Model | Counties won | Winning vote coverage | National MAE | Worsened parties | Pass |
|---|---:|---:|---:|---:|---|
{acceptance_rows}
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def _party_model_comparison(predictions: pl.DataFrame) -> pl.DataFrame:
    baseline = (
        predictions.filter(pl.col("model") == "ridge")
        .with_columns(
            (pl.col("actual_share") - pl.col("baseline_share")).abs().alias("absolute_error"),
            pl.lit("proportional_swing").alias("model"),
        )
        .select("model", "grouping", "party", "valid_votes", "absolute_error")
    )
    models = predictions.with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("absolute_error")
    ).select("model", "grouping", "party", "valid_votes", "absolute_error")
    return (
        pl.concat([baseline, models])
        .group_by("model", "grouping", "party")
        .agg(
            (
                (pl.col("absolute_error") * pl.col("valid_votes")).sum()
                / pl.col("valid_votes").sum()
            ).alias("weighted_mae"),
            pl.col("absolute_error").mean().alias("unweighted_mae"),
        )
        .sort("grouping", "party", "weighted_mae")
    )


def _block_model_comparison(
    predictions: pl.DataFrame,
    definitions: dict[str, tuple[str, ...]],
) -> pl.DataFrame:
    party_to_block = {party: block for block, parties in definitions.items() for party in parties}
    blocks = (
        predictions.with_columns(pl.col("party").replace_strict(party_to_block).alias("block"))
        .group_by("district_id", "model", "grouping", "block")
        .agg(
            pl.col("actual_share").sum(),
            pl.col("baseline_share").sum(),
            pl.col("predicted_share").sum(),
            pl.col("valid_votes").first(),
        )
    )
    baseline = (
        blocks.filter(pl.col("model") == "ridge")
        .with_columns(
            (pl.col("actual_share") - pl.col("baseline_share")).abs().alias("absolute_error"),
            pl.lit("proportional_swing").alias("model"),
        )
        .select("model", "grouping", "block", "valid_votes", "absolute_error")
    )
    models = blocks.with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("absolute_error")
    ).select("model", "grouping", "block", "valid_votes", "absolute_error")
    return (
        pl.concat([baseline, models])
        .group_by("model", "grouping", "block")
        .agg(
            (
                (pl.col("absolute_error") * pl.col("valid_votes")).sum()
                / pl.col("valid_votes").sum()
            ).alias("weighted_mae"),
            pl.col("absolute_error").mean().alias("unweighted_mae"),
        )
        .sort("grouping", "block", "weighted_mae")
    )


def _acceptance_summary(predictions: pl.DataFrame) -> pl.DataFrame:
    loco = predictions.filter(pl.col("grouping") == "county_id")
    baseline_by_county = (
        loco.filter(pl.col("model") == "ridge")
        .with_columns((pl.col("actual_share") - pl.col("baseline_share")).abs().alias("error"))
        .group_by("county_id")
        .agg(
            ((pl.col("error") * pl.col("valid_votes")).sum() / (pl.col("valid_votes").sum())).alias(
                "baseline_mae"
            )
        )
    )
    party_baseline = (
        loco.filter(pl.col("model") == "ridge")
        .with_columns((pl.col("actual_share") - pl.col("baseline_share")).abs().alias("error"))
        .group_by("party")
        .agg(
            ((pl.col("error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()).alias(
                "baseline_mae"
            )
        )
    )
    district_votes = loco.select("district_id", "county_id", "valid_votes").unique()
    total_votes = district_votes["valid_votes"].sum()
    rows: list[dict[str, object]] = []
    for model in loco["model"].unique().sort():
        model_rows = loco.filter(pl.col("model") == model)
        county = (
            model_rows.with_columns(
                (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("error")
            )
            .group_by("county_id")
            .agg(
                (
                    (pl.col("error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
                ).alias("model_mae")
            )
            .join(baseline_by_county, on="county_id")
            .with_columns((pl.col("model_mae") < pl.col("baseline_mae")).alias("won"))
        )
        winning_counties = county.filter(pl.col("won"))["county_id"]
        winning_votes = district_votes.filter(
            pl.col("county_id").is_in(winning_counties.implode())
        )["valid_votes"].sum()
        party = (
            model_rows.with_columns(
                (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("error")
            )
            .group_by("party")
            .agg(
                (
                    (pl.col("error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
                ).alias("model_mae")
            )
            .join(party_baseline, on="party")
        )
        national = model_rows.group_by("party").agg(
            (
                (pl.col("actual_share") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
            ).alias("actual"),
            (
                (pl.col("predicted_share") * pl.col("valid_votes")).sum()
                / pl.col("valid_votes").sum()
            ).alias("predicted"),
        )
        weighted_mae = (
            model_rows.with_columns(
                (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("error")
            )
            .select((pl.col("error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum())
            .item()
        )
        counties_won = county["won"].sum()
        coverage = float(winning_votes) / float(total_votes)
        national_mae = national.select((pl.col("actual") - pl.col("predicted")).abs().mean()).item()
        worsened = party.filter(pl.col("model_mae") > pl.col("baseline_mae") + 0.0001).height
        passes = (
            weighted_mae < 0.0146916871
            and counties_won >= 11
            and coverage >= 0.5
            and national_mae <= 0.003
            and worsened <= 2
        )
        rows.append(
            {
                "model": model,
                "counties_won": counties_won,
                "winning_county_vote_coverage": coverage,
                "national_mae": national_mae,
                "materially_worsened_parties": worsened,
                "passes_gate": passes,
            }
        )
    return pl.DataFrame(rows).sort("model")
