from __future__ import annotations

from dataclasses import dataclass

import polars as pl

REGION_IDS = (
    "SE2",
    "SE09",
    "SE0A",
    "SE01exkl0180",
    "0180",
    "SE12",
    "SE06",
    "SE07+SE08",
)

STOCKHOLM_MUNICIPALITY = "0180"
STOCKHOLM_COUNTY = "01"

REGION_LABELS = {
    "SE2": "Sydsverige",
    "SE09": "Småland med öarna",
    "SE0A": "Västsverige",
    "SE01exkl0180": "Stockholms län exkl. Stockholms kommun",
    "0180": "Stockholms kommun",
    "SE12": "Östra Mellansverige",
    "SE06": "Norra Mellansverige",
    "SE07+SE08": "Mellersta och övre Norrland",
}

COUNTY_TO_REGION = {
    "03": "SE12",
    "04": "SE12",
    "05": "SE12",
    "06": "SE09",
    "07": "SE09",
    "08": "SE09",
    "09": "SE09",
    "10": "SE2",
    "12": "SE2",
    "13": "SE0A",
    "14": "SE0A",
    "17": "SE06",
    "18": "SE12",
    "19": "SE12",
    "20": "SE06",
    "21": "SE06",
    "22": "SE07+SE08",
    "23": "SE07+SE08",
    "24": "SE07+SE08",
    "25": "SE07+SE08",
}

SWEDISH_COUNTIES = frozenset({STOCKHOLM_COUNTY, *COUNTY_TO_REGION})


@dataclass(frozen=True)
class RegionAssignment:
    frame: pl.DataFrame
    uncovered: pl.DataFrame


def region_id_for(municipality_id: str, county_id: str) -> str:
    municipality = str(municipality_id).strip()
    county = str(county_id).strip()
    if len(municipality) != 4 or len(county) != 2:
        raise ValueError(f"Invalid geography identifiers: {municipality_id}/{county_id}")
    if municipality[:2] != county:
        raise ValueError(f"Municipality {municipality} is not in county {county}")
    if municipality == STOCKHOLM_MUNICIPALITY:
        return STOCKHOLM_MUNICIPALITY
    if county == STOCKHOLM_COUNTY:
        return "SE01exkl0180"
    try:
        return COUNTY_TO_REGION[county]
    except KeyError as error:
        raise ValueError(f"County {county} is outside the locked Vid12 codebook") from error


def assign_regions(frame: pl.DataFrame) -> pl.DataFrame:
    required = {"municipality_id", "county_id"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing geography columns: {sorted(missing)}")
    keys = frame.select(
        pl.col("municipality_id").cast(pl.String),
        pl.col("county_id").cast(pl.String),
    ).unique()
    mapping = pl.DataFrame(
        [
            {
                "municipality_id": str(municipality_id),
                "county_id": str(county_id),
                "region_id": region_id_for(str(municipality_id), str(county_id)),
            }
            for municipality_id, county_id in keys.iter_rows()
        ]
    )
    assigned = frame.with_columns(
        pl.col("municipality_id").cast(pl.String),
        pl.col("county_id").cast(pl.String),
    ).join(mapping, on=["municipality_id", "county_id"])
    validate_region_coverage(assigned)
    return assigned


def validate_region_coverage(frame: pl.DataFrame) -> None:
    if "region_id" not in frame.columns:
        raise ValueError("Region assignment is missing region_id")
    unknown = set(frame["region_id"].drop_nulls().unique()) - set(REGION_IDS)
    if unknown:
        raise ValueError(f"Unexpected region ids: {sorted(unknown)}")
    if frame["region_id"].null_count():
        raise ValueError("Every district must map to exactly one Vid12 region")
    if frame.filter(~pl.col("county_id").is_in(sorted(SWEDISH_COUNTIES))).height:
        raise ValueError("Frame contains counties outside the locked codebook")
    stockholm = frame.filter(pl.col("municipality_id") == STOCKHOLM_MUNICIPALITY)
    if stockholm.height and set(stockholm["region_id"].unique()) != {STOCKHOLM_MUNICIPALITY}:
        raise ValueError("Stockholm municipality must map only to 0180")
    rest_of_county = frame.filter(
        (pl.col("county_id") == STOCKHOLM_COUNTY)
        & (pl.col("municipality_id") != STOCKHOLM_MUNICIPALITY)
    )
    if rest_of_county.height and set(rest_of_county["region_id"].unique()) != {"SE01exkl0180"}:
        raise ValueError("Rest of Stockholms län must map only to SE01exkl0180")


def region_index(region_id: str) -> int:
    try:
        return REGION_IDS.index(region_id)
    except ValueError as error:
        raise ValueError(f"Unknown region id: {region_id}") from error
