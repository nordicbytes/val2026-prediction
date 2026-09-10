from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl
import yaml

from valforecast.config import load_sources
from valforecast.features.election_history import PARTIES
from valforecast.ingest.fetch import fetch_source
from valforecast.polls.schema import POLL_PREVIOUS_CATEGORIES, PollWave, validate_transition_cells
from valforecast.polls.transition_ingest import (
    extract_scb_corpus_transition_cells,
    extract_scb_pdf_table21,
    read_pxweb_jsonstat,
    validate_scb_historical_pxweb,
)

ELECTION_YEARS = (2010, 2014, 2018, 2022, 2026)
FORECAST_CUTOFFS = {
    2018: date(2018, 6, 11),
    2022: date(2022, 6, 8),
}
PXWEB_HISTORICAL_WAVES = (
    "2018M11",
    "2019M05",
    "2019M11",
    "2020M05",
    "2020M11",
    "2021M05",
    "2021M11",
)
PXWEB_SOURCE_ID = "scb_psu_transition_2018M11_2021M11"
PDF_SOURCE_BY_WAVE = {
    "2014M11": "scb_psu_2014M11_original_report",
    "2015M05": "scb_psu_2015M05_original_report",
    "2015M11": "scb_psu_2015M11_original_report",
    "2016M05": "scb_psu_2016M05_original_report",
    "2016M11": "scb_psu_2016M11_original_report",
    "2017M05": "scb_psu_2017M05_original_report",
    "2017M11": "scb_psu_2017M11_original_report",
}

CorpusRole = Literal["train", "validation", "excluded"]


@dataclass(frozen=True)
class SurveyCorpusSpec:
    vintage: str
    previous_election: int
    train_waves: tuple[str, ...]
    validation_wave: str
    excluded_primary: dict[str, str]
    target_wave: str

    def ingested_waves(self) -> tuple[str, ...]:
        excluded = tuple(sorted(self.excluded_primary))
        return (*self.train_waves, self.validation_wave, *excluded)

    def role_for(self, calendar_wave: str) -> CorpusRole:
        if calendar_wave in self.excluded_primary:
            return "excluded"
        if calendar_wave == self.validation_wave:
            return "validation"
        if calendar_wave in self.train_waves:
            return "train"
        raise ValueError(f"{calendar_wave} is not in the locked survey corpus")


@dataclass(frozen=True)
class WaveCalendar:
    calendar_wave: str
    publication_date: date
    fieldwork_start: date
    fieldwork_end: date
    sample_size_basis: str


_WAVE_ROWS = (
    ("2014M11", "2014-12-04", "2014-10-29", "2014-11-25", "GROSS_SAMPLE"),
    ("2015M05", "2015-06-08", "2015-04-27", "2015-05-27", "GROSS_SAMPLE"),
    ("2015M11", "2015-12-03", "2015-11-02", "2015-11-25", "GROSS_SAMPLE"),
    ("2016M05", "2016-06-02", "2016-04-28", "2016-05-26", "GROSS_SAMPLE"),
    ("2016M11", "2016-11-30", "2016-10-28", "2016-11-27", "GROSS_SAMPLE"),
    ("2017M05", "2017-06-02", "2017-04-27", "2017-05-25", "GROSS_SAMPLE"),
    ("2017M11", "2017-12-08", "2017-10-30", "2017-11-26", "GROSS_SAMPLE"),
    ("2018M11", "2018-12-07", "2018-10-29", "2018-11-25", "RESPONDENTS"),
    ("2019M05", "2019-06-11", "2019-04-29", "2019-05-26", "RESPONDENTS"),
    ("2019M11", "2019-12-10", "2019-10-28", "2019-11-26", "RESPONDENTS"),
    ("2020M05", "2020-06-11", "2020-04-28", "2020-05-26", "RESPONDENTS"),
    ("2020M11", "2020-12-08", "2020-10-28", "2020-11-25", "RESPONDENTS"),
    ("2021M05", "2021-06-08", "2021-04-28", "2021-05-25", "RESPONDENTS"),
    ("2021M11", "2021-12-08", "2021-10-28", "2021-11-25", "RESPONDENTS"),
)
WAVE_CALENDAR = {
    wave: WaveCalendar(
        calendar_wave=wave,
        publication_date=date.fromisoformat(published),
        fieldwork_start=date.fromisoformat(start),
        fieldwork_end=date.fromisoformat(end),
        sample_size_basis=basis,
    )
    for wave, published, start, end, basis in _WAVE_ROWS
}


@dataclass(frozen=True)
class SurveyCorpus:
    election_cycle: int
    spec: SurveyCorpusSpec
    cells: pl.DataFrame
    waves: pl.DataFrame


def previous_election_for_psu_wave(calendar_wave: str) -> int:
    year = int(calendar_wave[:4])
    month = int(calendar_wave[5:7])
    matching = [
        election
        for election in ELECTION_YEARS
        if election < year or (election == year and month >= 11)
    ]
    if not matching:
        raise ValueError(f"No previous election for PSU wave {calendar_wave}")
    return max(matching)


def load_survey_corpus_specs(path: Path) -> dict[int, SurveyCorpusSpec]:
    document: Any
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Milestone 4B spec must be a mapping with schema_version: 1")
    raw_corpora = document.get("survey_corpora")
    if not isinstance(raw_corpora, dict):
        raise ValueError("Milestone 4B spec is missing survey_corpora")
    specs: dict[int, SurveyCorpusSpec] = {}
    for cycle_key, raw in raw_corpora.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid survey corpus spec: {cycle_key}")
        excluded_raw = raw.get("excluded_primary", {})
        if not isinstance(excluded_raw, dict):
            raise ValueError(f"excluded_primary must be a mapping: {cycle_key}")
        specs[int(cycle_key)] = SurveyCorpusSpec(
            vintage=str(raw["vintage"]),
            previous_election=int(raw["previous_election"]),
            train_waves=tuple(str(wave) for wave in raw["train_waves"]),
            validation_wave=str(raw["validation_wave"]),
            excluded_primary={str(wave): str(reason) for wave, reason in excluded_raw.items()},
            target_wave=str(raw["target_wave"]),
        )
    return specs


def validate_survey_corpus_spec(spec: SurveyCorpusSpec, *, election_cycle: int) -> None:
    ingested = spec.ingested_waves()
    if spec.target_wave in ingested:
        raise ValueError(
            f"Target wave {spec.target_wave} must be absent from the prior training corpus"
        )
    if spec.validation_wave in spec.train_waves:
        raise ValueError("Validation wave cannot also be a training wave")
    overlap = set(spec.train_waves) & set(spec.excluded_primary)
    if overlap:
        raise ValueError(f"Excluded waves cannot also be training waves: {sorted(overlap)}")
    if spec.validation_wave in spec.excluded_primary:
        raise ValueError("Validation wave cannot be excluded from the primary corpus")
    for calendar_wave in ingested:
        if previous_election_for_psu_wave(calendar_wave) != spec.previous_election:
            raise ValueError(
                f"{calendar_wave} does not use previous election {spec.previous_election}"
            )
    cutoff = FORECAST_CUTOFFS[election_cycle]
    for calendar_wave in ingested:
        meta = WAVE_CALENDAR[calendar_wave]
        if meta.publication_date > cutoff or meta.fieldwork_end > cutoff:
            raise ValueError(f"{calendar_wave} is not available before the {election_cycle} cutoff")
        if calendar_wave >= spec.target_wave:
            raise ValueError(f"{calendar_wave} is not strictly prior to {spec.target_wave}")


def model_party_cells(cells: pl.DataFrame) -> pl.DataFrame:
    result = cells.filter(pl.col("previous_party").is_in(PARTIES))
    validate_transition_cells(result)
    return result


def load_survey_corpus(
    root: Path,
    election_cycle: int,
    *,
    fetch_missing: bool = True,
) -> SurveyCorpus:
    spec = load_survey_corpus_specs(root / "config" / "milestone_4b.yaml")[election_cycle]
    validate_survey_corpus_spec(spec, election_cycle=election_cycle)
    if fetch_missing:
        _fetch_corpus_sources(root, election_cycle)
    if election_cycle == 2018:
        cells, waves = _load_pdf_corpus(root, spec, election_cycle)
    elif election_cycle == 2022:
        cells, waves = _load_pxweb_corpus(root, spec, election_cycle)
    else:
        raise ValueError(f"No locked survey corpus for {election_cycle}")
    validate_loaded_corpus(cells, waves, spec, election_cycle=election_cycle)
    return SurveyCorpus(
        election_cycle=election_cycle,
        spec=spec,
        cells=cells,
        waves=waves,
    )


def build_survey_corpora(root: Path, *, fetch_missing: bool = True) -> dict[str, object]:
    processed = root / "data" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    corpora = [
        load_survey_corpus(root, 2018, fetch_missing=fetch_missing),
        load_survey_corpus(root, 2022, fetch_missing=fetch_missing),
    ]
    cells = pl.concat([corpus.cells for corpus in corpora]).sort(
        "election_cycle", "calendar_wave", "previous_party", "current_party"
    )
    waves = pl.concat([corpus.waves for corpus in corpora]).sort("election_cycle", "calendar_wave")
    cells.write_parquet(processed / "survey_corpus_cells.parquet")
    waves.write_parquet(processed / "survey_corpus_waves.parquet")
    return {
        "waves": int(waves.height),
        "cells": int(cells.height),
        "election_cycles": [corpus.election_cycle for corpus in corpora],
        "excluded_waves": waves.filter(pl.col("corpus_role") == "excluded")[
            "calendar_wave"
        ].to_list(),
        "target_waves_absent": [corpus.spec.target_wave for corpus in corpora],
    }


def validate_loaded_corpus(
    cells: pl.DataFrame,
    waves: pl.DataFrame,
    spec: SurveyCorpusSpec,
    *,
    election_cycle: int,
) -> None:
    validate_transition_cells(cells)
    expected_waves = set(spec.ingested_waves())
    observed_waves = set(waves["calendar_wave"].to_list())
    if observed_waves != expected_waves:
        raise ValueError(
            f"Loaded waves {sorted(observed_waves)} != locked corpus {sorted(expected_waves)}"
        )
    if spec.target_wave in observed_waves or spec.target_wave in set(cells["calendar_wave"]):
        raise ValueError(f"Target wave {spec.target_wave} leaked into the prior corpus")
    if set(waves["previous_election"].unique()) != {spec.previous_election}:
        raise ValueError("Corpus mixes previous-election references")
    if set(waves["vintage"].unique()) != {spec.vintage}:
        raise ValueError("Corpus mixes estimator vintages")
    cutoff = FORECAST_CUTOFFS[election_cycle]
    for row in waves.iter_rows(named=True):
        wave = PollWave(
            wave_id=str(row["wave_id"]),
            election_cycle=election_cycle,
            pollster="SCB",
            fieldwork_start=date.fromisoformat(str(row["fieldwork_start"])),
            fieldwork_end=date.fromisoformat(str(row["fieldwork_end"])),
            publication_date=date.fromisoformat(str(row["publication_date"])),
            forecast_cutoff=cutoff,
            information_level="FULL_TRANSITION_TABLE",
            sample_size=row["sample_size"],
        )
        wave.validate_strict_cutoff()
    for calendar_wave in spec.ingested_waves():
        wave_cells = cells.filter(pl.col("calendar_wave") == calendar_wave)
        missing_previous = set(PARTIES) - set(wave_cells["previous_party"])
        if missing_previous:
            raise ValueError(f"{calendar_wave} is missing party rows: {sorted(missing_previous)}")
        extra_previous = set(wave_cells["previous_party"]) - set(POLL_PREVIOUS_CATEGORIES)
        if extra_previous:
            raise ValueError(f"{calendar_wave} has unknown previous rows: {sorted(extra_previous)}")


def _load_pdf_corpus(
    root: Path,
    spec: SurveyCorpusSpec,
    election_cycle: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    sources = {source.id: source for source in load_sources(root / "config" / "sources.yaml")}
    cell_frames: list[pl.DataFrame] = []
    wave_rows: list[dict[str, object]] = []
    for calendar_wave in spec.ingested_waves():
        source = sources[PDF_SOURCE_BY_WAVE[calendar_wave]]
        if source.raw_file is None:
            raise ValueError(f"Source {source.id} has no raw file")
        path = root / source.raw_file
        wave_id = f"scb_{calendar_wave}_original"
        cells, previous_election, sample_size = extract_scb_pdf_table21(path, wave_id=wave_id)
        if previous_election != spec.previous_election:
            raise ValueError(
                f"{calendar_wave} Table 21 references {previous_election}, "
                f"expected {spec.previous_election}"
            )
        meta = WAVE_CALENDAR[calendar_wave]
        role = spec.role_for(calendar_wave)
        exclusion_reason = spec.excluded_primary.get(calendar_wave)
        cell_frames.append(
            _annotate_cells(
                cells,
                election_cycle=election_cycle,
                calendar_wave=calendar_wave,
                previous_election=previous_election,
                vintage=spec.vintage,
                role=role,
                exclusion_reason=exclusion_reason,
            )
        )
        wave_rows.append(
            _wave_row(
                calendar_wave=calendar_wave,
                wave_id=wave_id,
                election_cycle=election_cycle,
                spec=spec,
                role=role,
                exclusion_reason=exclusion_reason,
                source_id=source.id,
                raw_file=source.raw_file,
                meta=meta,
                sample_size=sample_size,
            )
        )
    return pl.concat(cell_frames), pl.DataFrame(wave_rows)


def _load_pxweb_corpus(
    root: Path,
    spec: SurveyCorpusSpec,
    election_cycle: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    sources = {source.id: source for source in load_sources(root / "config" / "sources.yaml")}
    source = sources[PXWEB_SOURCE_ID]
    if source.raw_file is None:
        raise ValueError(f"Source {source.id} has no raw file")
    frame = read_pxweb_jsonstat(root / source.raw_file)
    validate_scb_historical_pxweb(frame, expected_times=set(PXWEB_HISTORICAL_WAVES))
    cell_frames: list[pl.DataFrame] = []
    wave_rows: list[dict[str, object]] = []
    for calendar_wave in spec.ingested_waves():
        if calendar_wave not in PXWEB_HISTORICAL_WAVES:
            raise ValueError(f"{calendar_wave} is not in the revised Rostningssympati1900 extract")
        wave_id = f"scb_{calendar_wave}_revised"
        cells = extract_scb_corpus_transition_cells(
            frame,
            wave_id=wave_id,
            time_value=calendar_wave,
        )
        previous_election = previous_election_for_psu_wave(calendar_wave)
        if previous_election != spec.previous_election:
            raise ValueError(
                f"{calendar_wave} uses previous election {previous_election}, "
                f"expected {spec.previous_election}"
            )
        meta = WAVE_CALENDAR[calendar_wave]
        role = spec.role_for(calendar_wave)
        exclusion_reason = spec.excluded_primary.get(calendar_wave)
        sample_size = _pxweb_wave_sample_size(frame, calendar_wave)
        cell_frames.append(
            _annotate_cells(
                cells,
                election_cycle=election_cycle,
                calendar_wave=calendar_wave,
                previous_election=previous_election,
                vintage=spec.vintage,
                role=role,
                exclusion_reason=exclusion_reason,
            )
        )
        wave_rows.append(
            _wave_row(
                calendar_wave=calendar_wave,
                wave_id=wave_id,
                election_cycle=election_cycle,
                spec=spec,
                role=role,
                exclusion_reason=exclusion_reason,
                source_id=source.id,
                raw_file=source.raw_file,
                meta=meta,
                sample_size=sample_size,
            )
        )
    return pl.concat(cell_frames), pl.DataFrame(wave_rows)


def _annotate_cells(
    cells: pl.DataFrame,
    *,
    election_cycle: int,
    calendar_wave: str,
    previous_election: int,
    vintage: str,
    role: CorpusRole,
    exclusion_reason: str | None,
) -> pl.DataFrame:
    return cells.with_columns(
        pl.lit(election_cycle).cast(pl.Int64).alias("election_cycle"),
        pl.lit(calendar_wave).alias("calendar_wave"),
        pl.lit(previous_election).cast(pl.Int64).alias("previous_election"),
        pl.lit(vintage).alias("vintage"),
        pl.lit(role).alias("corpus_role"),
        pl.lit(exclusion_reason, dtype=pl.String).alias("exclusion_reason"),
    )


def _wave_row(
    *,
    calendar_wave: str,
    wave_id: str,
    election_cycle: int,
    spec: SurveyCorpusSpec,
    role: CorpusRole,
    exclusion_reason: str | None,
    source_id: str,
    raw_file: str,
    meta: WaveCalendar,
    sample_size: int | None,
) -> dict[str, object]:
    return {
        "calendar_wave": calendar_wave,
        "wave_id": wave_id,
        "election_cycle": election_cycle,
        "previous_election": spec.previous_election,
        "vintage": spec.vintage,
        "corpus_role": role,
        "exclusion_reason": exclusion_reason,
        "source_id": source_id,
        "raw_file": raw_file,
        "publication_date": meta.publication_date.isoformat(),
        "fieldwork_start": meta.fieldwork_start.isoformat(),
        "fieldwork_end": meta.fieldwork_end.isoformat(),
        "forecast_cutoff": FORECAST_CUTOFFS[election_cycle].isoformat(),
        "sample_size": sample_size,
        "sample_size_basis": meta.sample_size_basis,
        "information_level": "FULL_TRANSITION_TABLE",
        "target_wave": spec.target_wave,
    }


def _pxweb_wave_sample_size(frame: pl.DataFrame, calendar_wave: str) -> int | None:
    whole = frame.filter(
        (pl.col("Tid") == calendar_wave)
        & (pl.col("PvalRV") == "hela väljarkåren")
        & (pl.col("ContentsCode") == "000001IV")
        & pl.col("value").is_not_null()
    )
    if whole.is_empty():
        return None
    return int(whole["value"][0])


def _fetch_corpus_sources(root: Path, election_cycle: int) -> None:
    sources = {source.id: source for source in load_sources(root / "config" / "sources.yaml")}
    source_ids = list(PDF_SOURCE_BY_WAVE.values()) if election_cycle == 2018 else [PXWEB_SOURCE_ID]
    for source_id in source_ids:
        fetch_source(sources[source_id], root)
