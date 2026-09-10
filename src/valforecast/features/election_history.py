from __future__ import annotations

import polars as pl

from valforecast.models.baselines import evaluation_mapping_rows

PARTIES = ("V", "S", "MP", "C", "L", "M", "KD", "SD", "OTHER")


def build_structural_model_frame(
    results_2018: pl.DataFrame,
    crosswalk: pl.DataFrame,
    proportional_predictions: pl.DataFrame,
) -> pl.DataFrame:
    mapping = evaluation_mapping_rows(crosswalk)
    prior_counts = results_2018.group_by("district_id", "canonical_party_code").agg(
        pl.col("votes").sum().alias("votes"),
        pl.col("valid_votes").first().alias("valid_votes"),
    )
    mapped_counts = (
        mapping.join(
            prior_counts,
            left_on="from_district_id",
            right_on="district_id",
            how="inner",
        )
        .group_by("to_district_id", "canonical_party_code")
        .agg(
            pl.col("votes").sum().alias("votes"),
            pl.col("valid_votes").sum().alias("valid_votes_2018"),
        )
        .with_columns((pl.col("votes") / pl.col("valid_votes_2018")).alias("previous_share"))
    )
    previous_wide = mapped_counts.pivot(
        on="canonical_party_code",
        index="to_district_id",
        values="previous_share",
    )
    missing_parties = set(PARTIES) - set(previous_wide.columns)
    previous_wide = previous_wide.with_columns(
        *(pl.lit(0.0).alias(party) for party in missing_parties)
    ).select(
        "to_district_id",
        *(pl.col(party).fill_null(0.0).alias(f"previous_{party}") for party in PARTIES),
    )

    prior_district = results_2018.group_by("district_id").agg(
        pl.col("valid_votes").first().alias("valid_votes_2018"),
        pl.col("invalid_votes").first().alias("invalid_votes_2018"),
        pl.col("eligible_voters").first().alias("eligible_voters_2018"),
    )
    mapped_totals = (
        mapping.join(
            prior_district,
            left_on="from_district_id",
            right_on="district_id",
            how="inner",
        )
        .group_by("to_district_id")
        .agg(
            pl.col("valid_votes_2018").sum(),
            pl.col("invalid_votes_2018").sum(),
            pl.col("eligible_voters_2018").sum(),
        )
    )

    outcomes = proportional_predictions.with_columns(
        (pl.col("actual_share") - pl.col("predicted_share")).alias("proportional_residual")
    )
    target_wide = outcomes.pivot(
        on="party",
        index=["district_id", "valid_votes"],
        values=["actual_share", "predicted_share", "proportional_residual"],
        separator="_",
    ).rename({"district_id": "to_district_id", "valid_votes": "valid_votes_2022"})

    frame = previous_wide.join(mapped_totals, on="to_district_id").join(
        target_wide, on="to_district_id"
    )
    entropy_terms = [
        pl.when(pl.col(f"previous_{party}") > 0)
        .then(-pl.col(f"previous_{party}") * pl.col(f"previous_{party}").log())
        .otherwise(0.0)
        for party in PARTIES
    ]
    return frame.with_columns(
        pl.col("to_district_id").str.slice(0, 4).alias("municipality_id"),
        pl.col("to_district_id").str.slice(0, 2).alias("county_id"),
        (
            (pl.col("valid_votes_2018") + pl.col("invalid_votes_2018"))
            / pl.col("eligible_voters_2018")
        ).alias("previous_turnout"),
        pl.sum_horizontal(entropy_terms).alias("party_entropy"),
        (pl.col("previous_S") + pl.col("previous_V") + pl.col("previous_MP")).alias(
            "red_green_share"
        ),
        (
            pl.col("previous_M")
            + pl.col("previous_C")
            + pl.col("previous_L")
            + pl.col("previous_KD")
        ).alias("alliance_share"),
        (
            pl.col("previous_S")
            + pl.col("previous_V")
            + pl.col("previous_MP")
            - pl.col("previous_M")
            - pl.col("previous_C")
            - pl.col("previous_L")
            - pl.col("previous_KD")
        ).alias("bloc_balance"),
        (
            (pl.col("eligible_voters_2018") - pl.col("valid_votes_2018"))
            / pl.col("eligible_voters_2018")
        ).alias("eligible_minus_valid_share"),
    )


def numeric_feature_columns() -> list[str]:
    return [
        *(f"previous_{party}" for party in PARTIES),
        "previous_turnout",
        "party_entropy",
        "eligible_voters_2018",
    ]


def target_columns() -> list[str]:
    return [f"proportional_residual_{party}" for party in PARTIES]
