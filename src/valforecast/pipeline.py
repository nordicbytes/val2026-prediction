from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import polars as pl

from valforecast.geo.crosswalk import (
    directly_comparable,
    read_official_val2018_2022_crosswalk,
)
from valforecast.ingest.elections import (
    election_quality_issues,
    read_val2018_district_results,
    read_val2022_district_results,
)
from valforecast.models.baselines import (
    build_2018_2022_predictions,
    evaluate_predictions,
)


def run_initial_milestone(root: Path) -> dict[str, object]:
    raw = root / "data" / "raw" / "valmyndigheten"
    processed = root / "data" / "processed"
    interim = root / "data" / "interim"
    backtests = root / "reports" / "backtests"
    for directory in (processed, interim, backtests):
        directory.mkdir(parents=True, exist_ok=True)

    results_2018 = read_val2018_district_results(
        raw / "2018" / "2018_R_per_valdistrikt.xlsx"
    )
    results_2022 = read_val2022_district_results(
        raw / "2022" / "roster_per_distrikt_slutligt_riksdag.xlsx"
    )
    crosswalk = read_official_val2018_2022_crosswalk(
        raw / "2022" / "jamforelser_2018_2022_valdistrikt_v2.xlsx"
    )

    results_2018.write_parquet(processed / "election_results_2018.parquet")
    results_2022.write_parquet(processed / "election_results_2022.parquet")
    crosswalk.write_parquet(processed / "district_identity_edges_2018_2022.parquet")

    issues_2018 = election_quality_issues(results_2018)
    issues_2022 = election_quality_issues(results_2022)
    issues_2018.write_parquet(interim / "quality_issues_election_2018.parquet")
    issues_2022.write_parquet(interim / "quality_issues_election_2022.parquet")

    predictions = build_2018_2022_predictions(results_2018, results_2022, crosswalk)
    metrics = []
    for model, frame in predictions.items():
        frame = frame.with_columns(
            (pl.col("actual_local_swing") - pl.col("national_swing")).alias(
                "actual_local_residual_swing"
            )
        )
        frame.write_parquet(backtests / f"2018_2022_{model}.parquet")
        metrics.append(asdict(evaluate_predictions(frame, model=model)))

    physical_district_count = crosswalk["to_district_id"].n_unique()
    comparable_count = directly_comparable(crosswalk)["to_district_id"].n_unique()
    merged_count = crosswalk.filter(pl.col("relation") == "MERGED")[
        "to_district_id"
    ].n_unique()
    problematic_count = crosswalk.filter(pl.col("relation") == "NOT_COMPARABLE")[
        "to_district_id"
    ].n_unique()
    evaluation_frame = predictions["uniform_national_residual"]
    evaluation_votes = (
        evaluation_frame.select("district_id", "valid_votes")
        .unique()["valid_votes"]
        .sum()
    )
    summary: dict[str, object] = {
        "election_2018_districts": results_2018["district_id"].n_unique(),
        "election_2022_districts_including_collection": results_2022[
            "district_id"
        ].n_unique(),
        "national_valid_votes_2018": int(results_2018["votes"].sum()),
        "national_valid_votes_2022": int(results_2022["votes"].sum()),
        "official_physical_district_rows_2022": physical_district_count,
        "directly_comparable_districts": comparable_count,
        "merged_comparable_districts": merged_count,
        "evaluation_districts": comparable_count + merged_count,
        "problematic_districts": problematic_count,
        "evaluation_valid_vote_coverage": float(evaluation_votes)
        / int(results_2022["votes"].sum()),
        "quality_issue_districts_2018": issues_2018.height,
        "quality_issue_districts_2022": issues_2022.height,
        "metrics": metrics,
    }
    (backtests / "2018_2022_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_reports(root, summary)
    return summary


def _write_reports(root: Path, summary: dict[str, object]) -> None:
    metrics = summary["metrics"]
    assert isinstance(metrics, list)
    metric_rows = "\n".join(
        "| {model} | {district_weighted_mae:.5f} | {district_unweighted_mae:.5f} | "
        "{national_mae:.5f} |".format(**row)
        for row in metrics
    )
    report = f"""# Data audit: initial 2018→2022 milestone

## Scope

This audit uses official final Riksdag results and the official Valmyndigheten
physical-district comparison. Collection districts are retained in canonical
election data and national totals but are not treated as physical districts.

## District coverage

- 2018 result identifiers: {summary["election_2018_districts"]}
- 2022 result identifiers including collection districts:
  {summary["election_2022_districts_including_collection"]}
- Canonical valid-vote total 2018: {summary["national_valid_votes_2018"]}
- Canonical valid-vote total 2022: {summary["national_valid_votes_2022"]}
- Physical districts in official 2022 comparison:
  {summary["official_physical_district_rows_2022"]}
- Directly comparable physical districts: {summary["directly_comparable_districts"]}
- Comparable merged districts: {summary["merged_comparable_districts"]}
- Districts in evaluation: {summary["evaluation_districts"]}
- Excluded/problematic physical districts: {summary["problematic_districts"]}
- Evaluation share of national valid votes:
  {summary["evaluation_valid_vote_coverage"]:.2%}

The official `Jämförbart` field uses `ja`, `nej`, one predecessor code, or
multiple predecessor codes. SAME and COMPARABLE rows use one 2018 district.
MERGED rows sum predecessor vote counts before shares are calculated.
`NOT_COMPARABLE` rows are excluded; no split is inferred from this file.

## Quality checks

- 2018 districts with logged issues: {summary["quality_issue_districts_2018"]}
- 2022 identifiers with logged issues: {summary["quality_issue_districts_2022"]}

The detailed rows are stored under `data/interim`. Expected collection-district
exceptions (votes but zero registered voters) remain logged rather than hidden.

## Baseline results

All errors are vote-share fractions; multiply by 100 for percentage points.

| Model | District weighted MAE | District unweighted MAE | National MAE |
|---|---:|---:|---:|
{metric_rows}

This is a structural benchmark using the *realized* 2022 national swing, not a
pre-election polling backtest. It isolates the first question: how much local
error remains after applying one common national movement?

## Gate conclusion

**UNCLEAR.** The proportional swing benchmark improves on no-change, but no
model using pre-election local features has been tested yet. This result cannot
establish predictive local signal beyond national swing.
"""
    (root / "reports" / "data_audit.md").write_text(report, encoding="utf-8")

