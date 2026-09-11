from __future__ import annotations

import polars as pl
import pytest

from valforecast.geo.region_codebook import (
    COUNTY_TO_REGION,
    REGION_IDS,
    STOCKHOLM_MUNICIPALITY,
    assign_regions,
    region_id_for,
    validate_region_coverage,
)


def test_stockholm_is_split_from_the_rest_of_the_county() -> None:
    assert region_id_for("0180", "01") == "0180"
    assert region_id_for("0123", "01") == "SE01exkl0180"


def test_every_modern_county_has_exactly_one_locked_region() -> None:
        mapped = {region_id_for(f"{county}00", county) for county in COUNTY_TO_REGION}
        assert mapped == set(REGION_IDS) - {STOCKHOLM_MUNICIPALITY, "SE01exkl0180"}
        assert region_id_for("0123", "01") == "SE01exkl0180"


def test_assign_regions_covers_all_districts_exactly_once() -> None:
    rows = [
        {"district_id": "0180A", "municipality_id": "0180", "county_id": "01"},
        {"district_id": "0123A", "municipality_id": "0123", "county_id": "01"},
        {"district_id": "1280A", "municipality_id": "1280", "county_id": "12"},
        {"district_id": "1480A", "municipality_id": "1480", "county_id": "14"},
        {"district_id": "0380A", "municipality_id": "0380", "county_id": "03"},
        {"district_id": "0680A", "municipality_id": "0680", "county_id": "06"},
        {"district_id": "1780A", "municipality_id": "1780", "county_id": "17"},
        {"district_id": "2480A", "municipality_id": "2480", "county_id": "24"},
    ]
    assigned = assign_regions(pl.DataFrame(rows))
    assert assigned["region_id"].n_unique() == 8
    assert assigned.group_by("district_id").len().filter(pl.col("len") != 1).is_empty()
    validate_region_coverage(assigned)


def test_unknown_county_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the locked"):
        region_id_for("1100", "11")
