from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import (
    predict_proportional_swing,
    predict_uniform_swing,
)

CORE_PARTIES = PARTIES
FREE_PARTIES = PARTIES[:-1]
LEFT_PARTIES = ("V", "S", "MP")
ALLIANCE_PARTIES = ("C", "L", "M", "KD")

CORE_NUMERIC_FEATURES = (
    *(f"previous_share_{party}" for party in PARTIES),
    "previous_turnout",
    "previous_party_entropy",
    "previous_party_concentration",
    "previous_left_share",
    "previous_alliance_share",
    "previous_sd_share",
    "previous_bloc_margin",
    "previous_largest_party_margin",
    "previous_eligible_voters",
    "log_previous_eligible_voters",
)

RICH_FEATURES = (
    "population_change",
    "education",
    "income",
    "foreign_background",
    "labour_market",
    "housing",
)


@dataclass(frozen=True)
class TransitionCoverage:
    transition_id: str
    from_election: int
    to_election: int
    evaluation_districts: int
    physical_target_districts: int
    district_coverage: float
    evaluation_valid_votes: int
    national_valid_votes: int
    vote_coverage: float
    mapping_methods: str
    mapping_qualities: str
    comparable_target_districts: int
    merged_target_districts: int
    split_comparable_target_districts: int


def build_canonical_transition(
    previous: pl.DataFrame,
    current: pl.DataFrame,
    crosswalk: pl.DataFrame,
    *,
    from_election: int,
    to_election: int,
) -> tuple[pl.DataFrame, TransitionCoverage]:
    transition_id = f"{from_election}_{to_election}"
    mapping_timing = {
        "2010_2014": "INFERRED_RETROSPECTIVE",
        "2014_2018": "OFFICIAL_AVAILABILITY_NOT_MACHINE_VERIFIED",
        "2018_2022": "OFFICIAL_RETROSPECTIVE_POST_ELECTION",
    }.get(transition_id, "UNKNOWN")
    mapping = _strict_mapping(crosswalk)
    previous_counts = _canonical_counts(previous)
    current_counts = _canonical_counts(current)
    previous_totals = _district_totals(previous)
    current_totals = _district_totals(current)

    mapped_counts = (
        mapping.join(
            previous_counts,
            left_on="from_district_id",
            right_on="district_id",
            how="inner",
        )
        .group_by("to_district_id", "canonical_party_code")
        .agg(pl.col("votes").sum().alias("votes"))
    )
    mapped_totals = (
        mapping.join(
            previous_totals,
            left_on="from_district_id",
            right_on="district_id",
            how="inner",
        )
        .group_by("to_district_id")
        .agg(
            pl.col("valid_votes").sum().alias("previous_valid_votes"),
            pl.col("invalid_votes").sum().alias("previous_invalid_votes"),
            pl.col("eligible_voters").sum().alias("previous_eligible_voters"),
        )
    )
    mapping_labels = mapping.group_by("to_district_id").agg(
        pl.col("from_district_id").str.join(",").alias("from_district_id"),
        pl.col("mapping_method").unique().sort().str.join("+"),
        pl.col("mapping_quality").unique().sort().str.join("+"),
        pl.col("comparison_weight").min(),
    )
    previous_wide = _wide_counts(mapped_counts, "previous")
    current_wide = _wide_counts(
        current_counts.rename({"district_id": "to_district_id"}),
        "current",
        district_column="to_district_id",
    )
    current_physical = current_totals.filter(pl.col("district_kind") == "PHYSICAL")
    joined = (
        mapping_labels.join(mapped_totals, on="to_district_id", how="inner")
        .join(previous_wide, on="to_district_id", how="inner")
        .join(current_wide, on="to_district_id", how="inner")
        .join(
            current_physical.select(
                "district_id",
                pl.col("valid_votes").alias("current_valid_votes"),
                pl.col("eligible_voters").alias("current_eligible_voters"),
                "municipality_id",
                "county_id",
            ),
            left_on="to_district_id",
            right_on="district_id",
            how="inner",
        )
        .sort("to_district_id")
    )

    national_previous = _national_shares(previous_counts)
    national_current = _national_shares(current_counts)
    national_delta = national_current - national_previous
    rows: list[dict[str, object]] = []
    for record in joined.iter_rows(named=True):
        previous_vector = _share_vector(record, "previous")
        current_vector = _share_vector(record, "current")
        previous_for_proportional = _replace_zeros(
            previous_vector,
            valid_votes=int(record["previous_valid_votes"]),
        )
        no_change = previous_vector
        uniform = predict_uniform_swing(previous_vector, national_delta)
        proportional = predict_proportional_swing(
            previous_for_proportional,
            national_previous,
            national_current,
        )
        entropy = _entropy(previous_vector)
        sorted_shares = np.sort(previous_vector)[::-1]
        largest_index = int(np.argmax(previous_vector))
        district_features: dict[str, object] = {
            **{
                f"previous_share_{party}": previous_vector[index]
                for index, party in enumerate(PARTIES)
            },
            "previous_turnout": (
                (int(record["previous_valid_votes"]) + int(record["previous_invalid_votes"]))
                / int(record["previous_eligible_voters"])
            ),
            "previous_party_entropy": entropy,
            "previous_party_concentration": float(np.square(previous_vector).sum()),
            "previous_left_share": _party_sum(previous_vector, LEFT_PARTIES),
            "previous_alliance_share": _party_sum(previous_vector, ALLIANCE_PARTIES),
            "previous_sd_share": previous_vector[PARTIES.index("SD")],
            "previous_bloc_margin": _party_sum(previous_vector, LEFT_PARTIES)
            - _party_sum(previous_vector, ALLIANCE_PARTIES),
            "previous_largest_party": PARTIES[largest_index],
            "previous_largest_party_margin": float(sorted_shares[0] - sorted_shares[1]),
            "previous_eligible_voters": int(record["previous_eligible_voters"]),
            "log_previous_eligible_voters": float(
                np.log1p(int(record["previous_eligible_voters"]))
            ),
        }
        for index, party in enumerate(PARTIES):
            local_swing = current_vector[index] - previous_vector[index]
            proportional_swing = proportional[index] - previous_vector[index]
            rows.append(
                {
                    "transition_id": transition_id,
                    "from_election": from_election,
                    "to_election": to_election,
                    "from_district_id": record["from_district_id"],
                    "to_district_id": record["to_district_id"],
                    "municipality_id": record["municipality_id"],
                    "county_id": record["county_id"],
                    "party": party,
                    "previous_vote_share": previous_vector[index],
                    "current_vote_share": current_vector[index],
                    "local_swing": local_swing,
                    "national_swing": national_delta[index],
                    "proportional_national_swing": proportional_swing,
                    "local_residual_swing": current_vector[index] - proportional[index],
                    "previous_turnout": district_features["previous_turnout"],
                    "previous_bloc_share": (
                        district_features["previous_left_share"]
                        if party in LEFT_PARTIES
                        else (
                            district_features["previous_alliance_share"]
                            if party in ALLIANCE_PARTIES
                            else previous_vector[index]
                        )
                    ),
                    "previous_party_entropy": entropy,
                    "mapping_method": record["mapping_method"],
                    "mapping_quality": record["mapping_quality"],
                    "mapping_timing": mapping_timing,
                    "comparison_weight": record["comparison_weight"],
                    "eligible_voters": record["current_eligible_voters"],
                    "valid_votes": record["current_valid_votes"],
                    "national_swing_role": "ORACLE_TARGET_ELECTION",
                    "valid_votes_role": "EVALUATION_ONLY_TARGET",
                    "previous_valid_votes": record["previous_valid_votes"],
                    "baseline_no_change_share": no_change[index],
                    "baseline_uniform_swing_share": uniform[index],
                    "baseline_proportional_swing_share": proportional[index],
                    **district_features,
                }
            )
    result = pl.DataFrame(rows)
    evaluation_districts = joined.height
    physical_target_districts = current_physical.height
    evaluation_valid_votes = int(joined["current_valid_votes"].sum())
    national_valid_votes = int(current_counts["votes"].sum())
    methods = result["mapping_method"].unique().sort().to_list()
    qualities = result["mapping_quality"].unique().sort().to_list()
    merged_target_districts = joined.filter(
        pl.col("mapping_method").str.contains("MERGE")
    ).height
    split_comparable_target_districts = joined.filter(
        pl.col("mapping_method").str.contains("SPLIT_COMPARABLE")
    ).height
    coverage = TransitionCoverage(
        transition_id=transition_id,
        from_election=from_election,
        to_election=to_election,
        evaluation_districts=evaluation_districts,
        physical_target_districts=physical_target_districts,
        district_coverage=evaluation_districts / physical_target_districts,
        evaluation_valid_votes=evaluation_valid_votes,
        national_valid_votes=national_valid_votes,
        vote_coverage=evaluation_valid_votes / national_valid_votes,
        mapping_methods=" + ".join(str(method) for method in methods),
        mapping_qualities=" + ".join(str(quality) for quality in qualities),
        comparable_target_districts=evaluation_districts - merged_target_districts,
        merged_target_districts=merged_target_districts,
        split_comparable_target_districts=split_comparable_target_districts,
    )
    return result, coverage


def add_lagged_sensitivity(transitions: pl.DataFrame) -> pl.DataFrame:
    """Add only residuals available from the immediately preceding transition."""
    lookup = transitions.filter(pl.col("mapping_quality") == "HIGH").select(
        pl.col("to_election").alias("history_election"),
        pl.col("to_district_id").alias("history_district_id"),
        "party",
        pl.col("local_residual_swing").alias("historical_party_sensitivity"),
    )
    exploded = (
        transitions.with_row_index("_row")
        .with_columns(pl.col("from_district_id").str.split(",").alias("_source_ids"))
        .explode("_source_ids", empty_as_null=True)
    )
    party_history = (
        exploded.join(
            lookup,
            left_on=["from_election", "_source_ids", "party"],
            right_on=["history_election", "history_district_id", "party"],
            how="left",
        )
        .group_by("_row")
        .agg(
            pl.col("historical_party_sensitivity").mean(),
            pl.col("historical_party_sensitivity").std().alias("historical_residual_swing_std"),
            pl.col("historical_party_sensitivity").count().alias("historical_observations"),
        )
    )
    with_party = transitions.with_row_index("_row").join(party_history, on="_row", how="left")
    bloc_history = with_party.group_by("transition_id", "to_district_id").agg(
        (
            pl.when(pl.col("party").is_in(LEFT_PARTIES))
            .then(pl.col("historical_party_sensitivity"))
            .otherwise(0.0)
            .sum()
        ).alias("_historical_bloc_sensitivity"),
        (
            pl.col("party").is_in(LEFT_PARTIES)
            & pl.col("historical_party_sensitivity").is_not_null()
        )
        .sum()
        .alias("_historical_bloc_observations"),
    ).with_columns(
        pl.when(pl.col("_historical_bloc_observations") > 0)
        .then(pl.col("_historical_bloc_sensitivity"))
        .otherwise(None)
        .alias("historical_bloc_sensitivity")
    ).drop(
        "_historical_bloc_sensitivity",
        "_historical_bloc_observations",
    )
    return with_party.join(
        bloc_history,
        on=["transition_id", "to_district_id"],
        how="left",
    ).drop("_row")


def _strict_mapping(crosswalk: pl.DataFrame) -> pl.DataFrame:
    mapping = crosswalk.filter(pl.col("relation").is_in(["SAME", "COMPARABLE", "MERGED"]))
    defaults: dict[str, pl.Expr] = {
        "mapping_method": pl.when(pl.col("relation") == "MERGED")
        .then(pl.lit("OFFICIAL_MERGE"))
        .otherwise(pl.lit("OFFICIAL_COMPARABLE")),
        "mapping_quality": pl.lit("HIGH"),
        "comparison_weight": pl.lit(1.0),
    }
    missing = [
        expression.alias(column)
        for column, expression in defaults.items()
        if column not in mapping.columns
    ]
    if missing:
        mapping = mapping.with_columns(*missing)
    return mapping.select(
        "from_district_id",
        "to_district_id",
        "mapping_method",
        "mapping_quality",
        "comparison_weight",
    )


def _canonical_counts(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.group_by("district_id", "canonical_party_code").agg(
        pl.col("votes").sum().alias("votes")
    )


def _district_totals(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.group_by("district_id").agg(
        pl.col("valid_votes").first(),
        pl.col("invalid_votes").first(),
        pl.col("eligible_voters").first(),
        pl.col("district_kind").first(),
        pl.col("municipality_id").first(),
        pl.col("county_id").first(),
    )


def _wide_counts(
    counts: pl.DataFrame,
    prefix: str,
    *,
    district_column: str = "to_district_id",
) -> pl.DataFrame:
    wide = counts.pivot(
        on="canonical_party_code",
        index=district_column,
        values="votes",
    )
    missing = set(PARTIES) - set(wide.columns)
    return wide.with_columns(*(pl.lit(0).alias(party) for party in missing)).select(
        district_column,
        *(pl.col(party).fill_null(0).alias(f"{prefix}_votes_{party}") for party in PARTIES),
    )


def _national_shares(counts: pl.DataFrame) -> np.ndarray:
    totals = counts.group_by("canonical_party_code").agg(pl.col("votes").sum())
    by_party = dict(totals.iter_rows())
    total = sum(int(value) for value in by_party.values())
    return np.array([int(by_party.get(party, 0)) / total for party in PARTIES])


def _share_vector(record: dict[str, object], prefix: str) -> np.ndarray:
    votes = np.array(
        [float(cast(int | float, record[f"{prefix}_votes_{party}"])) for party in PARTIES]
    )
    return np.asarray(votes / votes.sum(), dtype=float)


def _replace_zeros(
    shares: np.ndarray,
    *,
    valid_votes: int,
    pseudo_count: float = 0.5,
) -> np.ndarray:
    counts = shares * valid_votes
    counts = np.where(counts == 0, pseudo_count, counts)
    return np.asarray(counts / counts.sum(), dtype=float)


def _entropy(shares: np.ndarray) -> float:
    positive = shares[shares > 0]
    return float(-(positive * np.log(positive)).sum())


def _party_sum(shares: np.ndarray, parties: tuple[str, ...]) -> float:
    return float(sum(shares[PARTIES.index(party)] for party in parties))
