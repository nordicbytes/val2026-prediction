from __future__ import annotations

from pathlib import Path

import polars as pl
from openpyxl import load_workbook

CROSSWALK_COLUMNS = (
    "from_election",
    "from_district_id",
    "to_election",
    "to_district_id",
    "relation",
    "official_mapping",
    "confidence",
    "notes",
)


def classify_official_pairs(
    pairs: pl.DataFrame,
    *,
    from_election: int,
    to_election: int,
) -> pl.DataFrame:
    """Classify an official district-pair list from graph degree.

    A one-to-one renamed district is COMPARABLE, not SAME. Many-to-many
    components remain UNKNOWN because edge degree alone cannot identify a
    defensible split or merge.
    """
    required = {"from_district_id", "to_district_id"}
    missing = required - set(pairs.columns)
    if missing:
        raise ValueError(f"Missing official pair columns: {sorted(missing)}")

    clean = pairs.select(
        pl.col("from_district_id").cast(pl.String).str.strip_chars(),
        pl.col("to_district_id").cast(pl.String).str.strip_chars(),
    ).unique()
    if clean.select(pl.any_horizontal(pl.all().is_null() | (pl.all() == ""))).to_series().any():
        raise ValueError("Official crosswalk contains blank district identifiers")

    out_degree = clean.group_by("from_district_id").len().rename({"len": "out_degree"})
    in_degree = clean.group_by("to_district_id").len().rename({"len": "in_degree"})
    classified = clean.join(out_degree, on="from_district_id").join(
        in_degree, on="to_district_id"
    )

    relation = (
        pl.when((pl.col("out_degree") > 1) & (pl.col("in_degree") > 1))
        .then(pl.lit("UNKNOWN"))
        .when(pl.col("out_degree") > 1)
        .then(pl.lit("SPLIT"))
        .when(pl.col("in_degree") > 1)
        .then(pl.lit("MERGED"))
        .when(pl.col("from_district_id") == pl.col("to_district_id"))
        .then(pl.lit("SAME"))
        .otherwise(pl.lit("COMPARABLE"))
    )

    return classified.select(
        pl.lit(from_election).alias("from_election"),
        "from_district_id",
        pl.lit(to_election).alias("to_election"),
        "to_district_id",
        relation.alias("relation"),
        pl.lit(True).alias("official_mapping"),
        pl.when(relation == "UNKNOWN")
        .then(pl.lit(0.5))
        .otherwise(pl.lit(1.0))
        .alias("confidence"),
        pl.lit("Relation inferred from degrees in official mapping").alias("notes"),
    )


def directly_comparable(crosswalk: pl.DataFrame) -> pl.DataFrame:
    return crosswalk.filter(pl.col("relation").is_in(["SAME", "COMPARABLE"]))


def read_official_val2018_2022_crosswalk(path: Path) -> pl.DataFrame:
    """Read the physical-district sheet conservatively.

    The workbook's ``Jämförbart`` column contains ``ja``/``nej`` for most
    rows and one or more predecessor codes for the remaining comparable rows.
    Multi-code mappings are represented as one MERGED graph edge per source.
    """
    sheet = load_workbook(path, read_only=True, data_only=True)["Fysiska valdistrikt"]
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() for value in next(rows)]
    index = {name: position for position, name in enumerate(headers)}
    records: list[dict[str, object]] = []

    for values in rows:
        to_id = str(values[index["Kod_2022"]]).strip().zfill(8)
        status = str(values[index["Jämförbart"]] or "").strip().lower()
        from_values = [
            values[index[name]]
            for name in ("Valdistriktskod2018", "Kod 2 2018", "Kod 3 2018")
        ]
        from_ids = [
            str(value).strip().zfill(8)
            for value in from_values
            if value is not None and str(value).strip()
        ]
        status_ids = (
            [part.strip().zfill(8) for part in status.split(",")]
            if status not in {"ja", "nej", ""}
            else []
        )
        mapped_ids = list(dict.fromkeys([*status_ids, *from_ids]))

        mapped_ids_for_rows: list[str | None] = []
        if status == "nej" or not mapped_ids:
            mapped_ids_for_rows.append(None)
            relation = "NOT_COMPARABLE"
            confidence = 1.0
            notes = "Explicitly marked not comparable by Valmyndigheten"
        else:
            mapped_ids_for_rows.extend(mapped_ids)
            relation = (
                "MERGED"
                if len(mapped_ids) > 1
                else ("SAME" if mapped_ids[0] == to_id else "COMPARABLE")
            )
            confidence = 1.0
            notes = "Official Valmyndigheten comparability mapping"

        for from_id in mapped_ids_for_rows:
            records.append(
                {
                    "from_election": 2018,
                    "from_district_id": from_id,
                    "to_election": 2022,
                    "to_district_id": to_id,
                    "relation": relation,
                    "official_mapping": True,
                    "confidence": confidence,
                    "notes": notes,
                    "official_comparability_value": status,
                    "mapped_2018_district_count": len(mapped_ids),
                }
            )

    result = pl.DataFrame(records)
    reuse = (
        result.filter(pl.col("from_district_id").is_not_null())
        .group_by("from_district_id")
        .agg(pl.col("to_district_id").n_unique().alias("mapped_2022_district_count"))
    )
    return result.join(reuse, on="from_district_id", how="left").with_columns(
        (pl.col("mapped_2022_district_count").fill_null(0) > 1).alias("reused_2018_code")
    )

