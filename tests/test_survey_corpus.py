from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import POLL_CURRENT_CATEGORIES, POLL_PREVIOUS_CATEGORIES
from valforecast.polls.transition_corpus import (
    SurveyCorpusSpec,
    load_survey_corpus,
    load_survey_corpus_specs,
    previous_election_for_psu_wave,
    validate_survey_corpus_spec,
)
from valforecast.polls.transition_ingest import (
    extract_scb_2018_pdf_transition,
    extract_scb_pdf_table21,
    parse_scb_pdf_table21_lines,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_2018_PDF = ROOT / "data" / "raw" / "scb" / "psu" / "psu_may_2018_original.pdf"


def _locked_spec(cycle: int) -> SurveyCorpusSpec:
    return load_survey_corpus_specs(ROOT / "config" / "milestone_4b.yaml")[cycle]


def _folkpartiet_table21_lines() -> list[str]:
    current_labels = [
        "Moderaterna",
        "Centerpartiet",
        "Folkpartiet",
        "Kristdemokraterna",
        "Miljöpartiet",
        "Socialdemokraterna",
        "Vänsterpartiet",
        "Sverigedemokraterna",
        "Övriga partier",
        "Blankt",
        "Vet ej",
        "Uppgift saknas",
    ]
    primary = {
        0: ("Uppgift saknas", 12.0),
        1: ("Moderaterna", 45.0),
        2: ("Centerpartiet", 56.0),
        3: ("Folkpartiet", 34.0),
        4: ("Kristdemokraterna", 67.0),
        5: ("Miljöpartiet", 23.0),
        6: ("Socialdemokraterna", 78.0),
        7: ("Vänsterpartiet", 56.0),
        8: ("Sverigedemokraterna", 45.0),
        9: ("Övriga partier", 12.0),
        10: ("Vet ej", 23.0),
        11: ("Blankt", 34.0),
        12: ("Socialdemokraterna", 45.0),
    }
    lines = [
        "Tabell 21",
        "Väljarkåren fördelad på parti man skulle rösta på i november 2014",
        "samt på valt parti vid 2014 års riksdagsval",
    ]
    matrix = [[0.0] * 13 for _ in current_labels]
    for column, (label, mass) in primary.items():
        primary_index = current_labels.index(label)
        remainder = (100.0 - mass) / 11.0
        for current_index in range(len(current_labels)):
            matrix[current_index][column] = mass if current_index == primary_index else remainder
    columns = [sum(row[column] for row in matrix) for column in range(13)]
    assert all(abs(total - 100.0) < 1e-6 for total in columns)
    for label, values in zip(current_labels, matrix, strict=True):
        lines.extend([label, "%", *(_format_swedish(value) for value in values)])
        lines.extend(["ost", *(_format_swedish(1.2) for _ in values)])
    lines.append("Antal i urvalet")
    bases = [100, 200, 80, 70, 60, 90, 300, 75, 110, 40, 55, 65]
    lines.extend(_format_int(value) for value in [*bases, sum(bases)])
    return lines


def _format_swedish(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _format_int(value: int) -> str:
    text = str(value)
    if len(text) <= 3:
        return text
    return f"{text[:-3]} {text[-3:]}"


def test_generic_parser_maps_folkpartiet_and_preserves_nonparty_rows() -> None:
    parsed = parse_scb_pdf_table21_lines(
        _folkpartiet_table21_lines(),
        wave_id="scb_2014M11_original",
    )
    assert set(parsed["previous_party"]) == set(POLL_PREVIOUS_CATEGORIES)
    assert set(parsed["current_party"]) == set(POLL_CURRENT_CATEGORIES)
    folkpartiet = parsed.filter(
        (pl.col("previous_party") == "L") & (pl.col("current_party") == "L")
    ).item(0, "estimate")
    socialdemocrats = parsed.filter(
        (pl.col("previous_party") == "S") & (pl.col("current_party") == "S")
    ).item(0, "estimate")
    missing_row = parsed.filter(
        (pl.col("previous_party") == "MISSING") & (pl.col("current_party") == "MISSING")
    ).item(0, "estimate")
    assert folkpartiet == pytest.approx(0.34)
    assert socialdemocrats == pytest.approx(0.78)
    assert missing_row == pytest.approx(0.12)
    assert parsed.filter(pl.col("previous_party") == "DID_NOT_VOTE").height == len(
        POLL_CURRENT_CATEGORIES
    )


def test_locked_config_keeps_target_wave_out_of_prior_corpus() -> None:
    for cycle in (2018, 2022):
        spec = _locked_spec(cycle)
        validate_survey_corpus_spec(spec, election_cycle=cycle)
        assert spec.target_wave not in spec.ingested_waves()
        assert spec.target_wave not in spec.train_waves
        assert spec.validation_wave not in spec.train_waves


def test_previous_election_reference_is_locked_per_hierarchy() -> None:
    assert previous_election_for_psu_wave("2014M05") == 2010
    assert previous_election_for_psu_wave("2014M11") == 2014
    assert previous_election_for_psu_wave("2018M05") == 2014
    assert previous_election_for_psu_wave("2018M11") == 2018
    assert previous_election_for_psu_wave("2021M11") == 2018
    spec_2018 = _locked_spec(2018)
    spec_2022 = _locked_spec(2022)
    assert {previous_election_for_psu_wave(wave) for wave in spec_2018.ingested_waves()} == {2014}
    assert {previous_election_for_psu_wave(wave) for wave in spec_2022.ingested_waves()} == {2018}


def test_cutoff_validation_rejects_future_or_target_wave() -> None:
    spec = _locked_spec(2018)
    leaked = SurveyCorpusSpec(
        vintage=spec.vintage,
        previous_election=spec.previous_election,
        train_waves=(*spec.train_waves, spec.target_wave),
        validation_wave=spec.validation_wave,
        excluded_primary=spec.excluded_primary,
        target_wave=spec.target_wave,
    )
    with pytest.raises(ValueError, match="absent from the prior training corpus"):
        validate_survey_corpus_spec(leaked, election_cycle=2018)
    mixed = SurveyCorpusSpec(
        vintage=spec.vintage,
        previous_election=2018,
        train_waves=spec.train_waves,
        validation_wave=spec.validation_wave,
        excluded_primary=spec.excluded_primary,
        target_wave=spec.target_wave,
    )
    with pytest.raises(ValueError, match="previous election"):
        validate_survey_corpus_spec(mixed, election_cycle=2018)


def test_generic_parser_matches_2018_gold_cells_when_pdf_exists() -> None:
    if not ORIGINAL_2018_PDF.exists():
        pytest.skip("Original 2018 PSU PDF is not in the local raw cache")
    exact = extract_scb_2018_pdf_transition(ORIGINAL_2018_PDF)
    generic, previous_election, gross_sample = extract_scb_pdf_table21(
        ORIGINAL_2018_PDF,
        wave_id="scb_2018M05_original",
    )
    assert previous_election == 2014
    assert gross_sample == 8951
    party_generic = generic.filter(pl.col("previous_party").is_in(PARTIES)).select(exact.columns)
    assert party_generic.equals(exact)
    gold_cells = {
        ("M", "M"): 0.532,
        ("S", "S"): 0.569,
        ("SD", "SD"): 0.726,
    }
    for (previous, current), expected in gold_cells.items():
        value = generic.filter(
            (pl.col("previous_party") == previous) & (pl.col("current_party") == current)
        ).item(0, "estimate")
        assert value == pytest.approx(expected)


def test_loaded_corpus_preserves_exclusions_and_omits_targets() -> None:
    pdf_sources = [
        ROOT / "data" / "raw" / "scb" / "psu" / f"psu_{wave}_original.pdf"
        for wave in (
            "2014M11",
            "2015M05",
            "2015M11",
            "2016M05",
            "2016M11",
            "2017M05",
            "2017M11",
        )
    ]
    pxweb = ROOT / "data" / "raw" / "scb" / "psu" / "transition_2018M11_2021M11.json"
    if not all(path.exists() for path in [*pdf_sources, pxweb]):
        pytest.skip("Historical PSU source files are not in the local raw cache")
    corpus_2018 = load_survey_corpus(ROOT, 2018, fetch_missing=False)
    corpus_2022 = load_survey_corpus(ROOT, 2022, fetch_missing=False)
    assert set(corpus_2018.waves["calendar_wave"]) == set(corpus_2018.spec.ingested_waves())
    assert "2018M05" not in set(corpus_2018.cells["calendar_wave"])
    assert "2022M05" not in set(corpus_2022.cells["calendar_wave"])
    excluded_2018 = dict(
        corpus_2018.waves.filter(pl.col("corpus_role") == "excluded")
        .select("calendar_wave", "exclusion_reason")
        .iter_rows()
    )
    assert excluded_2018 == {
        "2014M11": "first_post_election_wave",
        "2015M05": "anomalous_extra_panel",
    }
    excluded_2022 = dict(
        corpus_2022.waves.filter(pl.col("corpus_role") == "excluded")
        .select("calendar_wave", "exclusion_reason")
        .iter_rows()
    )
    assert excluded_2022 == {"2018M11": "first_post_election_wave"}
    assert set(corpus_2018.waves["vintage"]) == {"ORIGINAL_PRE_2020_PDF"}
    assert set(corpus_2022.waves["vintage"]) == {"REVISED_2020_AVAILABLE_AT_CUTOFF"}
    gold = {
        ("2014M11", "S", "S"): 0.855,
        ("2014M11", "M", "M"): 0.845,
        ("2015M05", "L", "L"): 0.449,
        ("2017M05", "S", "S"): 0.600,
    }
    for (wave, previous, current), expected in gold.items():
        value = corpus_2018.cells.filter(
            (pl.col("calendar_wave") == wave)
            & (pl.col("previous_party") == previous)
            & (pl.col("current_party") == current)
        ).item(0, "estimate")
        assert value == pytest.approx(expected)
    assert (
        corpus_2018.waves.filter(pl.col("calendar_wave") == "2017M05").item(0, "sample_size")
        == 8973
    )
    loyalty_2018m11 = corpus_2022.cells.filter(
        (pl.col("calendar_wave") == "2018M11")
        & (pl.col("previous_party") == "S")
        & (pl.col("current_party") == "S")
    ).item(0, "estimate")
    assert loyalty_2018m11 == pytest.approx(0.85)


def test_poll_wave_cutoff_uses_publication_date() -> None:
    from valforecast.polls.schema import PollWave

    wave = PollWave(
        wave_id="scb_2017M11_original",
        election_cycle=2018,
        pollster="SCB",
        fieldwork_start=date(2017, 10, 30),
        fieldwork_end=date(2017, 11, 26),
        publication_date=date(2017, 12, 8),
        forecast_cutoff=date(2018, 6, 11),
        information_level="FULL_TRANSITION_TABLE",
        sample_size=9000,
    )
    wave.validate_strict_cutoff()
    late = PollWave(
        wave_id="scb_2018M11_revised",
        election_cycle=2018,
        pollster="SCB",
        fieldwork_start=date(2018, 4, 27),
        fieldwork_end=date(2018, 5, 29),
        publication_date=date(2018, 6, 20),
        forecast_cutoff=date(2018, 6, 11),
        information_level="FULL_TRANSITION_TABLE",
        sample_size=4000,
    )
    with pytest.raises(ValueError, match="published after"):
        late.validate_strict_cutoff()
