from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import pymupdf

from valforecast.features.election_history import PARTIES

BLOCKS_2022 = {
    "left_2022": ("V", "S", "MP", "C"),
    "right_tido_2022": ("L", "M", "KD", "SD"),
    "other": ("OTHER",),
}
BLOCKS_2018 = {
    "red_green_2018": ("V", "S", "MP"),
    "alliance_2018": ("C", "L", "M", "KD"),
    "sd_2018": ("SD",),
    "other": ("OTHER",),
}


def write_residual_diagnostics(
    proportional: pl.DataFrame,
    structural: pl.DataFrame,
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    residual = proportional.with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).alias("proportional_residual"),
        (pl.col("actual_local_swing") - pl.col("national_swing")).alias("additive_residual"),
        (pl.col("actual_share") - pl.col("predicted_share")).abs().alias("absolute_error"),
    ).join(
        structural.select(
            "to_district_id",
            "municipality_id",
            "county_id",
            "eligible_voters_2018",
            "party_entropy",
            "bloc_balance",
            *(f"previous_{party}" for party in PARTIES),
        ),
        left_on="district_id",
        right_on="to_district_id",
    )

    party_stats = _party_residual_stats(residual)
    party_stats.write_csv(output_dir / "residual_distribution_by_party.csv")
    _weighted_breakdown(residual, ["party"]).write_csv(output_dir / "baseline_error_by_party.csv")
    _weighted_breakdown(residual, ["municipality_id"]).write_csv(
        output_dir / "baseline_error_by_municipality.csv"
    )
    _weighted_breakdown(residual, ["county_id"]).write_csv(
        output_dir / "baseline_error_by_county.csv"
    )

    district = residual.group_by("district_id").agg(
        pl.col("valid_votes").first(),
        pl.col("eligible_voters_2018").first(),
        pl.col("municipality_id").first(),
        pl.col("county_id").first(),
        pl.col("party_entropy").first(),
        pl.col("bloc_balance").first(),
        pl.col("absolute_error").mean().alias("district_mae"),
        pl.col("actual_local_swing").abs().mean().alias("actual_swing_magnitude"),
        *(pl.col(f"previous_{party}").first() for party in PARTIES),
    )
    size_breakdown = _binned_district_breakdown(district, "eligible_voters_2018", "district_size")
    size_breakdown.write_csv(output_dir / "baseline_error_by_district_size.csv")
    swing_breakdown = _binned_district_breakdown(
        district, "actual_swing_magnitude", "actual_swing_size"
    )
    swing_breakdown.write_csv(output_dir / "baseline_error_by_actual_swing.csv")

    previous_matrix = district.select([f"previous_{party}" for party in PARTIES]).to_numpy()
    winner_index = np.argmax(previous_matrix, axis=1)
    composition = district.with_columns(
        pl.Series("previous_largest_party", [PARTIES[index] for index in winner_index])
    )
    _weighted_district_breakdown(composition, ["previous_largest_party"]).write_csv(
        output_dir / "baseline_error_by_previous_profile.csv"
    )

    matrix = _wide_residual_matrix(residual)
    weights = (
        residual.select("district_id", "valid_votes")
        .unique()
        .sort("district_id")["valid_votes"]
        .to_numpy()
    )
    correlation, eigenvalues, explained, loadings = _weighted_structure(matrix, weights)
    pl.DataFrame(correlation, schema=list(PARTIES), orient="row").insert_column(
        0, pl.Series("party", PARTIES)
    ).write_csv(output_dir / "residual_correlation.csv")
    pl.DataFrame(
        {
            "component": np.arange(1, len(PARTIES) + 1),
            "eigenvalue": eigenvalues,
            "explained_variance": explained,
        }
    ).write_csv(output_dir / "residual_pca.csv")
    pl.DataFrame(
        loadings,
        schema=[f"PC{index}" for index in range(1, len(PARTIES) + 1)],
        orient="row",
    ).insert_column(0, pl.Series("party", PARTIES)).write_csv(
        output_dir / "residual_pca_loadings.csv"
    )

    block_frame = _block_residuals(matrix, BLOCKS_2022)
    block_frame.write_csv(output_dir / "block_residuals.csv")
    _block_residuals(matrix, BLOCKS_2018).write_csv(
        output_dir / "block_residuals_2018_sensitivity.csv"
    )
    block_matrix = block_frame.to_numpy()
    block_correlation, _, _, _ = _weighted_structure(block_matrix, weights)
    block_names = list(BLOCKS_2022)
    pl.DataFrame(block_correlation, schema=block_names, orient="row").insert_column(
        0, pl.Series("block", block_names)
    ).write_csv(output_dir / "block_residual_correlation.csv")
    block_stats = []
    for index, block in enumerate(block_names):
        values = block_matrix[:, index] * 100
        mean = np.average(values, weights=weights)
        block_stats.append(
            {
                "block": block,
                "weighted_mean_pp": float(mean),
                "weighted_std_pp": float(
                    np.sqrt(np.average((values - mean) ** 2, weights=weights))
                ),
            }
        )
    pl.DataFrame(block_stats).write_csv(output_dir / "block_residual_stats.csv")
    previous_shares = (
        structural.sort("to_district_id")
        .select([f"previous_{party}" for party in PARTIES])
        .to_numpy()
    )
    equal_block_share = _block_approximation_share(matrix, weights, BLOCKS_2022, allocation=None)
    prior_block_share = _block_approximation_share(
        matrix, weights, BLOCKS_2022, allocation=previous_shares
    )
    _plot_residual_distributions(residual, output_dir / "residual_distributions.png")
    _plot_correlation(correlation, output_dir / "residual_correlation.png")

    summary = {
        "districts": matrix.shape[0],
        "weighted_mae": float(
            np.average(residual["absolute_error"].to_numpy(), weights=residual["valid_votes"])
        ),
        "first_pca_explained_variance": float(explained[0]),
        "second_pca_explained_variance": float(explained[1]),
        "largest_residual_std_party": str(
            party_stats.sort("weighted_std_pp", descending=True).item(0, "party")
        ),
        "largest_absolute_pc1_loading_party": PARTIES[int(np.argmax(np.abs(loadings[:, 0])))],
        "equal_block_residual_energy_share": equal_block_share,
        "prior_share_block_residual_energy_share": prior_block_share,
    }
    (output_dir / "residual_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def analyze_selection_bias(
    results_2018: pl.DataFrame,
    results_2022: pl.DataFrame,
    crosswalk: pl.DataFrame,
    output_dir: Path,
    population_path: Path,
    municipality_groups_path: Path,
) -> pl.DataFrame:
    included_ids = (
        crosswalk.filter(pl.col("relation").is_in(["SAME", "COMPARABLE", "MERGED"]))
        .select("to_district_id")
        .unique()
    )
    district_2022 = (
        results_2022.filter(pl.col("district_kind") == "PHYSICAL")
        .group_by("district_id")
        .agg(
            pl.col("eligible_voters").first().alias("eligible_voters_2022"),
            pl.col("valid_votes").first().alias("valid_votes_2022"),
            pl.col("invalid_votes").first().alias("invalid_votes_2022"),
        )
        .with_columns(
            pl.col("district_id").str.slice(0, 4).alias("municipality_id"),
            pl.col("district_id").str.slice(0, 2).alias("county_id"),
        )
        .join(
            included_ids.with_columns(pl.lit(True).alias("included")),
            left_on="district_id",
            right_on="to_district_id",
            how="left",
        )
        .with_columns(pl.col("included").fill_null(False))
    )
    municipal_2018 = _municipal_profile(results_2018, 2018)
    municipal_2022 = _municipal_profile(results_2022, 2022).select(
        "municipality_id",
        pl.col("municipality_eligible_voters").alias("municipality_eligible_voters_2022"),
    )
    comparison = (
        district_2022.join(municipal_2018, on="municipality_id", how="left")
        .join(municipal_2022, on="municipality_id", how="left")
        .join(_read_scb_population(population_path), on="municipality_id", how="left")
        .join(
            _read_skr_municipality_groups(municipality_groups_path, population_path),
            on="municipality_id",
            how="left",
        )
        .with_columns(
            (
                pl.col("municipality_eligible_voters_2022") / pl.col("municipality_eligible_voters")
                - 1
            ).alias("municipality_electorate_change"),
            (pl.col("population_2021") / pl.col("population_2017") - 1).alias(
                "municipality_population_change"
            ),
        )
    )

    numeric = [
        "eligible_voters_2022",
        "municipality_population_change",
        "municipality_electorate_change",
        "municipality_turnout",
        *(f"municipality_share_{party}" for party in PARTIES),
    ]
    rows = [_standardized_difference(comparison, column) for column in numeric]
    summary = pl.DataFrame(rows)
    summary.write_csv(output_dir / "selection_bias_numeric.csv")
    comparison.write_parquet(output_dir / "selection_bias_districts.parquet")
    comparison.group_by("county_id", "included").agg(
        pl.len().alias("districts"),
        pl.col("valid_votes_2022").sum().alias("valid_votes"),
    ).sort("county_id", "included").write_csv(output_dir / "selection_bias_by_county.csv")
    comparison.group_by("municipality_group_code", "municipality_group", "included").agg(
        pl.len().alias("districts"),
        pl.col("valid_votes_2022").sum().alias("valid_votes"),
    ).sort("municipality_group_code", "included").write_csv(
        output_dir / "selection_bias_by_municipality_group.csv"
    )
    comparison.group_by("municipality_main_group", "included").agg(
        pl.len().alias("districts"),
        pl.col("valid_votes_2022").sum().alias("valid_votes"),
    ).sort("municipality_main_group", "included").write_csv(
        output_dir / "selection_bias_by_urbanity.csv"
    )
    return summary


def _read_scb_population(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path, encoding="windows-1252")
    return (
        frame.with_columns(pl.col("region").str.split(" ").list.first().alias("municipality_id"))
        .filter(pl.col("municipality_id").str.len_chars() == 4)
        .group_by("municipality_id")
        .agg(
            pl.col("Folkmängd 2017").sum().alias("population_2017"),
            pl.col("Folkmängd 2021").sum().alias("population_2021"),
        )
    )


def _read_skr_municipality_groups(
    pdf_path: Path,
    population_path: Path,
) -> pl.DataFrame:
    group_names = (
        "Storstäder",
        "Pendlingskommun nära storstad",
        "Större stad",
        "Pendlingskommun nära större stad",
        "Lågpendlingskommun nära större stad",
        "Mindre stad/tätort",
        "Pendlingskommun nära mindre stad/tätort",
        "Landsbygdskommun",
        "Landsbygdskommun med besöksnäring",
    )
    anchors = np.array([15.12, 77.40, 145.22, 202.01, 257.21, 325.03, 384.19, 437.86, 489.10])
    words = pymupdf.open(pdf_path)[0].get_text("words")  # type: ignore[no-untyped-call]
    cells: dict[tuple[float, int], list[tuple[float, str]]] = {}
    for x0, y0, _x1, _y1, text, *_rest in words:
        if not 120 <= y0 <= 690:
            continue
        column = int(np.searchsorted(anchors, x0 + 0.5, side="right") - 1)
        cells.setdefault((round(y0, 2), column), []).append((x0, str(text)))

    name_to_group = {
        " ".join(text for _, text in sorted(parts)): column + 1
        for (_row, column), parts in cells.items()
    }
    population = (
        pl.read_csv(population_path, encoding="windows-1252")
        .with_columns(
            pl.col("region").str.split(" ").list.first().alias("municipality_id"),
            pl.col("region").str.split(" ").list.slice(1).list.join(" ").alias("municipality_name"),
        )
        .filter(pl.col("municipality_id").str.len_chars() == 4)
        .select("municipality_id", "municipality_name")
        .unique()
    )
    rows = []
    for municipality_id, municipality_name in population.iter_rows():
        group_code = name_to_group.get(str(municipality_name))
        if group_code is None:
            raise ValueError(f"Municipality missing from SKR 2017 groups: {municipality_name}")
        rows.append(
            {
                "municipality_id": municipality_id,
                "municipality_group_code": group_code,
                "municipality_group": group_names[group_code - 1],
                "municipality_main_group": (
                    "Storstäder och storstadsnära"
                    if group_code <= 2
                    else (
                        "Större städer och närkommuner"
                        if group_code <= 5
                        else "Mindre orter och landsbygd"
                    )
                ),
            }
        )
    if len(rows) != 290:
        raise ValueError(f"Expected 290 SKR municipality mappings, got {len(rows)}")
    expected_group_counts = [3, 43, 21, 52, 35, 29, 52, 40, 15]
    observed_group_counts = [
        sum(row["municipality_group_code"] == group for row in rows) for group in range(1, 10)
    ]
    if observed_group_counts != expected_group_counts:
        raise ValueError(
            f"Unexpected SKR 2017 group counts: {observed_group_counts} != {expected_group_counts}"
        )
    return pl.DataFrame(rows)


def _municipal_profile(results: pl.DataFrame, year: int) -> pl.DataFrame:
    physical = results.filter(pl.col("district_kind") == "PHYSICAL")
    counts = physical.group_by("municipality_id", "canonical_party_code").agg(
        pl.col("votes").sum().alias("votes")
    )
    district_totals = physical.group_by("municipality_id", "district_id").agg(
        pl.col("eligible_voters").first(),
        pl.col("valid_votes").first(),
        pl.col("invalid_votes").first(),
    )
    totals = (
        district_totals.group_by("municipality_id")
        .agg(
            pl.col("eligible_voters").sum().alias("municipality_eligible_voters"),
            pl.col("valid_votes").sum().alias("municipality_valid_votes"),
            pl.col("invalid_votes").sum().alias("municipality_invalid_votes"),
        )
        .with_columns(
            (
                (pl.col("municipality_valid_votes") + pl.col("municipality_invalid_votes"))
                / pl.col("municipality_eligible_voters")
            ).alias("municipality_turnout")
        )
    )
    shares = (
        counts.join(
            totals.select("municipality_id", "municipality_valid_votes"),
            on="municipality_id",
        )
        .with_columns((pl.col("votes") / pl.col("municipality_valid_votes")).alias("share"))
        .pivot(on="canonical_party_code", index="municipality_id", values="share")
        .with_columns(
            *(
                pl.col(party).fill_null(0.0).alias(f"municipality_share_{party}")
                for party in PARTIES
            )
        )
        .select("municipality_id", *(f"municipality_share_{party}" for party in PARTIES))
    )
    return totals.join(shares, on="municipality_id").with_columns(
        pl.lit(year).alias("profile_year")
    )


def _standardized_difference(frame: pl.DataFrame, column: str) -> dict[str, object]:
    included = frame.filter(pl.col("included"))[column].drop_nulls().to_numpy()
    excluded = frame.filter(~pl.col("included"))[column].drop_nulls().to_numpy()
    pooled = np.sqrt((np.var(included, ddof=1) + np.var(excluded, ddof=1)) / 2)
    difference = float((np.mean(included) - np.mean(excluded)) / pooled) if pooled else 0.0
    return {
        "feature": column,
        "included_mean": float(np.mean(included)),
        "excluded_mean": float(np.mean(excluded)),
        "standardized_mean_difference": difference,
        "included_n": len(included),
        "excluded_n": len(excluded),
    }


def _party_residual_stats(frame: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for party in PARTIES:
        subset = frame.filter(pl.col("party") == party)
        values = subset["proportional_residual"].to_numpy() * 100
        weights = subset["valid_votes"].to_numpy()
        mean = np.average(values, weights=weights)
        variance = np.average((values - mean) ** 2, weights=weights)
        rows.append(
            {
                "party": party,
                "weighted_mean_pp": float(mean),
                "weighted_std_pp": float(np.sqrt(variance)),
                "median_pp": float(np.median(values)),
                "mad_pp": float(np.median(np.abs(values - np.median(values)))),
                "p05_pp": float(np.quantile(values, 0.05)),
                "p95_pp": float(np.quantile(values, 0.95)),
            }
        )
    return pl.DataFrame(rows)


def _weighted_breakdown(frame: pl.DataFrame, groups: list[str]) -> pl.DataFrame:
    return frame.group_by(groups).agg(
        (
            (pl.col("absolute_error") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
        ).alias("weighted_mae"),
        pl.col("absolute_error").mean().alias("unweighted_mae"),
        pl.col("valid_votes").sum().alias("party_observation_weight"),
        pl.len().alias("observations"),
    )


def _weighted_district_breakdown(frame: pl.DataFrame, groups: list[str]) -> pl.DataFrame:
    return frame.group_by(groups).agg(
        (
            (pl.col("district_mae") * pl.col("valid_votes")).sum() / pl.col("valid_votes").sum()
        ).alias("weighted_mae"),
        pl.col("district_mae").mean().alias("unweighted_mae"),
        pl.col("valid_votes").sum().alias("valid_votes"),
        pl.len().alias("districts"),
    )


def _binned_district_breakdown(
    frame: pl.DataFrame,
    column: str,
    label: str,
) -> pl.DataFrame:
    values = frame[column].to_numpy()
    boundaries = np.unique(np.quantile(values, [0, 0.2, 0.4, 0.6, 0.8, 1]))
    bins = np.clip(np.digitize(values, boundaries[1:-1], right=True), 0, 4)
    labelled = frame.with_columns(pl.Series(label, [f"Q{value + 1}" for value in bins]))
    return _weighted_district_breakdown(labelled, [label]).sort(label)


def _wide_residual_matrix(frame: pl.DataFrame) -> np.ndarray:
    wide = (
        frame.select("district_id", "party", "proportional_residual")
        .pivot(on="party", index="district_id", values="proportional_residual")
        .sort("district_id")
    )
    return wide.select(list(PARTIES)).to_numpy()


def _weighted_structure(
    matrix: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = np.average(matrix, axis=0, weights=weights)
    centered = matrix - mean
    covariance = (centered * weights[:, None]).T @ centered / weights.sum()
    scale = np.sqrt(np.diag(covariance))
    correlation = covariance / np.outer(scale, scale)
    eigenvalues_ascending, eigenvectors_ascending = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues_ascending)[::-1]
    eigenvalues = eigenvalues_ascending[order]
    eigenvectors = eigenvectors_ascending[:, order]
    explained = eigenvalues / eigenvalues.sum()
    return correlation, eigenvalues, explained, eigenvectors


def _block_residuals(
    matrix: np.ndarray,
    blocks: dict[str, tuple[str, ...]],
) -> pl.DataFrame:
    party_index = {party: index for index, party in enumerate(PARTIES)}
    return pl.DataFrame(
        {
            block: matrix[:, [party_index[party] for party in parties]].sum(axis=1)
            for block, parties in blocks.items()
        }
    )


def _block_approximation_share(
    matrix: np.ndarray,
    weights: np.ndarray,
    blocks: dict[str, tuple[str, ...]],
    *,
    allocation: np.ndarray | None,
) -> float:
    """Share of weighted squared residual energy represented by block totals."""
    party_index = {party: index for index, party in enumerate(PARTIES)}
    approximation = np.zeros_like(matrix)
    for parties in blocks.values():
        indices = [party_index[party] for party in parties]
        block_total = matrix[:, indices].sum(axis=1)
        if allocation is None:
            shares = np.full((matrix.shape[0], len(indices)), 1 / len(indices))
        else:
            raw = allocation[:, indices]
            totals = raw.sum(axis=1, keepdims=True)
            shares = np.divide(
                raw,
                totals,
                out=np.full_like(raw, 1 / len(indices)),
                where=totals > 0,
            )
        approximation[:, indices] = block_total[:, None] * shares
    total_energy = np.sum(weights[:, None] * matrix**2)
    unexplained_energy = np.sum(weights[:, None] * (matrix - approximation) ** 2)
    return float(1 - unexplained_energy / total_energy)


def _plot_residual_distributions(frame: pl.DataFrame, path: Path) -> None:
    figure, axes = plt.subplots(3, 3, figsize=(12, 9), constrained_layout=True)
    for party, axis in zip(PARTIES, axes.flat, strict=True):
        subset = frame.filter(pl.col("party") == party)
        axis.hist(
            subset["proportional_residual"].to_numpy() * 100,
            bins=40,
            weights=subset["valid_votes"].to_numpy(),
            color="#35618f",
            alpha=0.85,
        )
        axis.axvline(0, color="black", linewidth=0.8)
        axis.set_title(party)
        axis.set_xlabel("Residual (pp)")
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _plot_correlation(correlation: np.ndarray, path: Path) -> None:
    figure, axis = plt.subplots(figsize=(8, 7), constrained_layout=True)
    image = axis.imshow(correlation, vmin=-1, vmax=1, cmap="RdBu_r")
    axis.set_xticks(range(len(PARTIES)), PARTIES, rotation=45, ha="right")
    axis.set_yticks(range(len(PARTIES)), PARTIES)
    axis.set_title("Weighted correlation of proportional-swing residuals")
    figure.colorbar(image, ax=axis)
    figure.savefig(path, dpi=160)
    plt.close(figure)
