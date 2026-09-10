from pathlib import Path
from zipfile import ZipFile

import polars as pl
from openpyxl import Workbook

from valforecast.features.election_history import PARTIES
from valforecast.features.temporal import (
    CORE_NUMERIC_FEATURES,
    RICH_FEATURES,
    add_lagged_sensitivity,
)
from valforecast.geo.crosswalk import (
    build_stable_id_name_crosswalk,
    read_official_val2014_2018_crosswalk,
    read_official_val2018_2022_crosswalk,
)
from valforecast.ingest.elections import read_legacy_district_results
from valforecast.models.temporal import (
    ABLATION_CATEGORICAL_FEATURES,
    ABLATION_NUMERIC_FEATURES,
    M2_FROZEN_FEATURES,
    TEMPORAL_TESTS,
)


def test_legacy_result_parser_canonicalizes_parties(tmp_path: Path) -> None:
    path = tmp_path / "legacy.skv"
    path.write_text(
        "LAN;KOM;VALDIST;RIKSDAGSVALKRETS;KOMMUN;VALDISTRIKT;"
        "M tal;M proc;FP tal;FP proc;OVR tal;OVR proc;BL tal;BL proc;"
        "OG tal;OG proc;Rost Giltiga;Rostande;Rostb;VDT\n"
        "01;14;0101;Stockholm;Kommun;Distrikt;40;40,0;30;30,0;"
        "30;30,0;2;2,0;1;1,0;100;103;120;85,83\n",
        encoding="windows-1252",
    )
    result = read_legacy_district_results(path, election_year=2010)
    assert result["district_id"].unique().to_list() == ["01140101"]
    assert result.filter(pl.col("canonical_party_code") == "L")["votes"].sum() == 30
    assert result.filter(pl.col("canonical_party_code") == "OTHER")["votes"].sum() == 30
    assert result["invalid_votes"].unique().to_list() == [3]


def test_legacy_collection_id_does_not_collide_with_physical_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.skv"
    path.write_text(
        "LAN;KOM;KVK;VALDIST;RIKSDAGSVALKRETS;KOMMUN;Kommunvalkrets;"
        "Valdistrikt;M tal;M proc;OVR tal;OVR proc;BL tal;BL proc;"
        "OG tal;OG proc;Rost Giltiga;Rostande;Rostb;VDT\n"
        "01;14;0;0001;Stockholm;Kommun;Krets;Fysisk;80;80,0;20;20,0;"
        "1;1,0;0;0,0;100;101;120;84,17\n"
        "01;14;0;01;Stockholm;Kommun;Krets;Uppsamlingsdistrikt;"
        "8;80,0;2;20,0;0;0,0;0;0,0;10;10;;\n",
        encoding="windows-1252",
    )
    result = read_legacy_district_results(path, election_year=2014)
    assert set(result["district_id"]) == {"01140001", "011401"}
    assert result["district_id"].n_unique() == 2


def test_stable_mapping_requires_identifier_and_name() -> None:
    previous = pl.DataFrame(
        {
            "district_id": ["01010101", "01010102"],
            "district_name": ["Stable North", "Reused code"],
            "district_kind": ["PHYSICAL", "PHYSICAL"],
        }
    )
    current = pl.DataFrame(
        {
            "district_id": ["01010101", "01010102"],
            "district_name": ["Stable north", "Different boundary"],
            "district_kind": ["PHYSICAL", "PHYSICAL"],
        }
    )
    mapping = build_stable_id_name_crosswalk(
        previous,
        current,
        from_election=2010,
        to_election=2014,
    )
    assert mapping["to_district_id"].to_list() == ["01010101"]
    assert mapping["mapping_quality"].to_list() == ["MEDIUM"]


def test_official_mapping_only_admits_o_and_s(tmp_path: Path) -> None:
    path = tmp_path / "mapping.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "vd-mappning-2014-2018.skv",
            "#from;to;procent\n"
            "01010101;01010101;100.0\n"
            "01010102;01010103;100.0\n"
            "01010104;01010103;100.0\n"
            "01010105;01010105;50.0\n",
        )
        archive.writestr(
            "vd-indelning-2018.skv",
            "#to;class\n01010101;O\n01010103;S\n01010105;M\n01010106;N\n",
        )
    mapping = read_official_val2014_2018_crosswalk(path)
    admitted = mapping.filter(pl.col("relation") != "NOT_COMPARABLE")
    assert admitted["to_district_id"].n_unique() == 2
    assert admitted.filter(pl.col("relation") == "MERGED").height == 2
    assert mapping.filter(pl.col("relation") == "NOT_COMPARABLE")["to_district_id"].n_unique() == 2


def test_official_split_comparable_predecessor_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "mapping.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Fysiska valdistrikt"
    sheet.append(
        [
            "Kod_2022",
            "Jämförbart",
            "Valdistriktskod2018",
            "Kod 2 2018",
            "Kod 3 2018",
        ]
    )
    sheet.append(["25810005", "ja", "25810529", None, None])
    sheet.append(["25810006", "ja", "25810529", None, None])
    workbook.save(path)

    mapping = read_official_val2018_2022_crosswalk(path)
    assert mapping["reused_2018_code"].all()
    assert mapping["mapping_method"].unique().to_list() == [
        "OFFICIAL_SPLIT_COMPARABLE"
    ]


def test_core_and_ablation_features_are_forecast_time_fields() -> None:
    forbidden_prefixes = ("actual_", "current_", "target_", "local_residual_")
    all_model_features = {
        feature for features in ABLATION_NUMERIC_FEATURES.values() for feature in features
    }
    assert set(CORE_NUMERIC_FEATURES).issubset(all_model_features | {"previous_largest_party"})
    assert not any(feature.startswith(forbidden_prefixes) for feature in all_model_features)
    assert not set(RICH_FEATURES).intersection(all_model_features)
    assert ABLATION_CATEGORICAL_FEATURES["A_previous_shares"] == ()
    assert (
        *(f"previous_share_{party}" for party in PARTIES),
        "previous_turnout",
        "previous_party_entropy",
        "previous_eligible_voters",
    ) == M2_FROZEN_FEATURES


def test_b0_is_official_only_primary_temporal_split() -> None:
    b0 = next(test for test in TEMPORAL_TESTS if test.test_id == "B0")
    assert b0.train_transitions == ("2014_2018",)
    assert b0.test_transition == "2018_2022"


def test_lagged_sensitivity_uses_only_prior_transition_target() -> None:
    transitions = pl.DataFrame(
        {
            "transition_id": ["2014_2018", "2018_2022", "2022_2026"],
            "from_election": [2014, 2018, 2022],
            "to_election": [2018, 2022, 2026],
            "from_district_id": ["D2014", "D2018", "D2022"],
            "to_district_id": ["D2018", "D2022", "D2026"],
            "party": ["S", "S", "S"],
            "mapping_quality": ["HIGH", "HIGH", "HIGH"],
            "local_residual_swing": [0.02, 0.99, -0.50],
        }
    )
    result = add_lagged_sensitivity(transitions)
    current = result.filter(pl.col("transition_id") == "2018_2022")
    assert current["historical_party_sensitivity"].to_list() == [0.02]
    assert current["historical_observations"].to_list() == [1]

    medium_history = transitions.with_columns(
        pl.when(pl.col("transition_id") == "2014_2018")
        .then(pl.lit("MEDIUM"))
        .otherwise(pl.col("mapping_quality"))
        .alias("mapping_quality")
    )
    current_without_strict_history = add_lagged_sensitivity(medium_history).filter(
        pl.col("transition_id") == "2018_2022"
    )
    assert current_without_strict_history["historical_party_sensitivity"].is_null().all()
    assert current_without_strict_history["historical_bloc_sensitivity"].is_null().all()
