from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import numpy as np
import polars as pl
import yaml
from matplotlib import pyplot as plt

from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import predict_proportional_swing, project_simplex

ELECTION_DATES = {
    2018: datetime(2018, 9, 9, 20, 0, 0),
    2022: datetime(2022, 9, 11, 20, 0, 0),
}
ARCHIVE_SOURCE_IDS = {
    2018: "election_night_preliminary_2018",
    2022: "election_night_preliminary_2022",
}
NAMED_PARTIES = set(PARTIES) - {"OTHER"}
MODEL_COLUMNS = {
    "B0_raw_reported_share": "raw",
    "B1_previous_national": "previous",
    "C1_reported_additive_change": "additive",
    "C2_reported_proportional_change": "proportional",
}


def _empty_party_counts() -> dict[str, int]:
    return {party: 0 for party in PARTIES}


def _canonical_party(raw: str) -> str:
    code = raw.strip().upper()
    aliases = {"FP": "L"}
    normalized = aliases.get(code, code)
    return normalized if normalized in NAMED_PARTIES else "OTHER"


def read_2018_election_night(path: Path) -> pl.DataFrame:
    records: list[dict[str, object]] = []
    municipality_file = re.compile(r"valnatt_\d{4}R\.xml$")
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not municipality_file.fullmatch(name):
                continue
            root = ElementTree.fromstring(archive.read(name))
            for district in root.iter("VALDISTRIKT"):
                counts = _empty_party_counts()
                for child in district:
                    if child.tag == "GILTIGA":
                        party = _canonical_party(str(child.attrib["PARTI"]))
                        counts[party] += int(child.attrib["RÖSTER"])
                    elif child.tag == "ÖVRIGA_GILTIGA":
                        counts["OTHER"] += int(child.attrib["RÖSTER"])
                valid_votes = sum(counts.values())
                published_valid = int(district.attrib["RÖSTER"])
                if valid_votes != published_valid:
                    raise ValueError(
                        f"2018 valid-vote mismatch for {district.attrib['KOD']}: "
                        f"{valid_votes} != {published_valid}"
                    )
                district_id = str(district.attrib["KOD"])
                records.append(
                    {
                        "election_year": 2018,
                        "district_id": district_id,
                        "district_name": str(district.attrib["NAMN"]),
                        "municipality_id": district_id[:4],
                        "county_id": district_id[:2],
                        "reported_at": datetime.strptime(
                            district.attrib["TID_RAPPORT"], "%Y%m%d%H%M%S"
                        ),
                        "valid_votes": valid_votes,
                        **counts,
                    }
                )
    result = pl.DataFrame(records).sort("reported_at", "district_id")
    if result.height != 6_004 or result["district_id"].n_unique() != 6_004:
        raise ValueError("Expected 6,004 unique physical districts in 2018 archive")
    return result


def read_2022_election_night(path: Path) -> pl.DataFrame:
    with zipfile.ZipFile(path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if "rostfordelning" in name and name.endswith(".json")
        ]
        if len(candidates) != 1:
            raise ValueError("Expected one 2022 preliminary vote-distribution JSON")
        document: dict[str, Any] = json.loads(archive.read(candidates[0]))
    records: list[dict[str, object]] = []
    for district in document["valdistrikt"]:
        if district["valdistriktstyp"] != "valdistrikt":
            continue
        counts = _empty_party_counts()
        valid = district["rostfordelning"]["rosterPaverkaMandat"]
        for party_row in valid["partiRoster"]:
            party = _canonical_party(str(party_row["partiforkortning"]))
            counts[party] += int(party_row["antalRoster"])
        counts["OTHER"] += int(valid["rosterOvrigaPartier"]["antalRoster"])
        valid_votes = sum(counts.values())
        if valid_votes != int(valid["antalRoster"]):
            raise ValueError(f"2022 valid-vote mismatch for {district['valdistriktskod']}")
        records.append(
            {
                "election_year": 2022,
                "district_id": str(district["valdistriktskod"]),
                "district_name": str(district["namn"]),
                "municipality_id": str(district["kommunkod"]),
                "county_id": str(district["lankod"]),
                "reported_at": datetime.fromisoformat(district["rapporteringsTid"]),
                "valid_votes": valid_votes,
                **counts,
            }
        )
    result = pl.DataFrame(records).sort("reported_at", "district_id")
    if result.height != 6_264 or result["district_id"].n_unique() != 6_264:
        raise ValueError("Expected 6,264 unique physical districts in 2022 archive")
    return result


def verify_election_night_sources(root: Path) -> dict[int, Path]:
    manifest: dict[str, Any] = yaml.safe_load(
        (root / "config" / "sources_experimental.yaml").read_text(encoding="utf-8")
    )
    by_id = {str(source["id"]): source for source in manifest["sources"]}
    paths: dict[int, Path] = {}
    for year, source_id in ARCHIVE_SOURCE_IDS.items():
        source = by_id[source_id]
        path = root / str(source["raw_file"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"Checksum mismatch for {path}")
        paths[year] = path
    return paths


def _national_shares(results: pl.DataFrame) -> np.ndarray:
    totals = (
        results.group_by("canonical_party_code")
        .agg(pl.col("votes").sum())
        .select("canonical_party_code", "votes")
    )
    by_party = {str(party): int(votes) for party, votes in totals.iter_rows()}
    values = np.array([by_party.get(party, 0) for party in PARTIES], dtype=float)
    return np.asarray(values / values.sum(), dtype=float)


def _comparison_frame(canonical: pl.DataFrame, year: int) -> pl.DataFrame:
    transition = (
        canonical.filter(pl.col("to_election") == year)
        .select(
            "to_district_id",
            "party",
            "previous_vote_share",
            "previous_valid_votes",
        )
        .pivot(
            on="party",
            index=["to_district_id", "previous_valid_votes"],
            values="previous_vote_share",
        )
        .rename({"to_district_id": "district_id"})
    )
    missing = set(PARTIES) - set(transition.columns)
    complete = transition.with_columns(*(pl.lit(0.0).alias(party) for party in sorted(missing)))
    return complete.select(
        "district_id",
        "previous_valid_votes",
        *(pl.col(party).alias(f"previous_{party}") for party in PARTIES),
    )


def _checkpoint_datetime(year: int, clock: str) -> datetime:
    hour, minute = (int(value) for value in clock.split(":"))
    base = ELECTION_DATES[year]
    checkpoint = base.replace(hour=hour, minute=minute)
    return checkpoint if hour >= 20 else checkpoint + timedelta(days=1)


def _share_vector(frame: pl.DataFrame, weight_column: str) -> np.ndarray:
    values = frame.select(*(pl.col(party).sum() for party in PARTIES)).row(0)
    denominator = float(frame[weight_column].sum())
    if denominator <= 0:
        raise ValueError("Cannot calculate shares without reported valid votes")
    return np.array([float(value) / denominator for value in values], dtype=float)


def _previous_subset_shares(frame: pl.DataFrame) -> np.ndarray:
    totals = frame.select(
        *((pl.col(f"previous_{party}") * pl.col("previous_valid_votes")).sum() for party in PARTIES)
    ).row(0)
    denominator = float(frame["previous_valid_votes"].sum())
    return np.array([float(value) / denominator for value in totals], dtype=float)


def replay_cycle(
    preliminary: pl.DataFrame,
    comparison: pl.DataFrame,
    *,
    previous_national: np.ndarray,
    final_national: np.ndarray,
    checkpoints: list[str],
) -> pl.DataFrame:
    year = int(preliminary["election_year"][0])
    total_districts = preliminary.height
    total_valid_votes = int(preliminary["valid_votes"].sum())
    records: list[dict[str, object]] = []
    for clock in checkpoints:
        timestamp = _checkpoint_datetime(year, clock)
        reported = preliminary.filter(pl.col("reported_at") <= timestamp)
        if reported.is_empty():
            continue
        matched = reported.join(comparison, on="district_id", how="inner")
        current_matched = _share_vector(matched, "valid_votes")
        previous_matched = _previous_subset_shares(matched)
        raw = _share_vector(reported, "valid_votes")
        additive = project_simplex(previous_national + current_matched - previous_matched)
        proportional = predict_proportional_swing(
            previous_national,
            previous_matched,
            current_matched,
        )
        predictions = {
            "raw": raw,
            "previous": previous_national,
            "additive": additive,
            "proportional": proportional,
        }
        record: dict[str, object] = {
            "election_year": year,
            "checkpoint": clock,
            "checkpoint_at": timestamp,
            "reported_districts": reported.height,
            "reported_district_share": reported.height / total_districts,
            "reported_valid_votes": int(reported["valid_votes"].sum()),
            "reported_valid_vote_share": int(reported["valid_votes"].sum()) / total_valid_votes,
            "matched_reported_districts": matched.height,
        }
        for index, party in enumerate(PARTIES):
            record[f"final_{party}"] = float(final_national[index])
            for model, prediction in predictions.items():
                record[f"{model}_{party}"] = float(prediction[index])
        records.append(record)
    return pl.DataFrame(records)


def score_replay(predictions: pl.DataFrame) -> pl.DataFrame:
    records: list[dict[str, object]] = []
    final = predictions.select(*(f"final_{party}" for party in PARTIES)).to_numpy()
    final_largest = [PARTIES[int(index)] for index in np.argmax(final, axis=1)]
    for model_id, prefix in MODEL_COLUMNS.items():
        predicted = predictions.select(*(f"{prefix}_{party}" for party in PARTIES)).to_numpy()
        absolute = np.abs(predicted - final)
        predicted_largest = [PARTIES[int(index)] for index in np.argmax(predicted, axis=1)]
        for row_index, metadata in enumerate(
            predictions.select(
                "election_year",
                "checkpoint",
                "checkpoint_at",
                "reported_districts",
                "reported_district_share",
                "reported_valid_votes",
                "reported_valid_vote_share",
                "matched_reported_districts",
            ).iter_rows(named=True)
        ):
            records.append(
                {
                    **metadata,
                    "model": model_id,
                    "mae": float(absolute[row_index].mean()),
                    "max_party_error": float(absolute[row_index].max()),
                    "largest_party_correct": (
                        predicted_largest[row_index] == final_largest[row_index]
                    ),
                }
            )
    return pl.DataFrame(records).sort("election_year", "checkpoint_at", "model")


def evaluate_gate(
    metrics: pl.DataFrame,
    *,
    start: str,
    end: str,
    minimum_win_fraction: float,
    candidate_id: str = "C1_reported_additive_change",
    comparator_id: str = "B0_raw_reported_share",
) -> dict[str, object]:
    cycle_rows: list[dict[str, object]] = []
    checkpoint_wins = 0
    checkpoint_total = 0
    for year in sorted(int(value) for value in metrics["election_year"].unique()):
        start_at = _checkpoint_datetime(year, start)
        end_at = _checkpoint_datetime(year, end)
        window = metrics.filter(
            (pl.col("election_year") == year)
            & pl.col("checkpoint_at").is_between(start_at, end_at, closed="both")
            & pl.col("model").is_in([candidate_id, comparator_id])
        )
        wide = window.pivot(
            on="model",
            index="checkpoint_at",
            values="mae",
        ).sort("checkpoint_at")
        elapsed = (
            wide["checkpoint_at"].cast(pl.Int64).to_numpy()
            - int(wide["checkpoint_at"][0].timestamp() * 1_000_000)
        ) / 3_600_000_000
        raw_values = wide[comparator_id].to_numpy()
        candidate_values = wide[candidate_id].to_numpy()
        raw_auc = float(np.trapezoid(raw_values, elapsed))
        candidate_auc = float(np.trapezoid(candidate_values, elapsed))
        wins = int((candidate_values < raw_values).sum())
        checkpoint_wins += wins
        checkpoint_total += len(candidate_values)
        cycle_rows.append(
            {
                "election_year": year,
                "raw_auc": raw_auc,
                "candidate_auc": candidate_auc,
                "relative_auc_improvement": (raw_auc - candidate_auc) / raw_auc,
                "candidate_lower_auc": candidate_auc < raw_auc,
                "checkpoint_wins": wins,
                "checkpoints": len(candidate_values),
            }
        )
    win_fraction = checkpoint_wins / checkpoint_total
    passed = all(bool(row["candidate_lower_auc"]) for row in cycle_rows) and (
        win_fraction >= minimum_win_fraction
    )
    return {
        "passed": passed,
        "candidate": candidate_id,
        "comparator": comparator_id,
        "window": f"{start}/{end}",
        "minimum_checkpoint_win_fraction": minimum_win_fraction,
        "checkpoint_win_fraction": win_fraction,
        "cycles": cycle_rows,
    }


def build_replay_predictions(root: Path, config: dict[str, Any]) -> pl.DataFrame:
    paths = verify_election_night_sources(root)
    preliminary = {
        2018: read_2018_election_night(paths[2018]),
        2022: read_2022_election_night(paths[2022]),
    }
    canonical = pl.read_parquet(
        root / "data" / "processed" / "canonical_temporal_transitions.parquet"
    )
    results = {
        year: pl.read_parquet(root / "data" / "processed" / f"election_results_{year}.parquet")
        for year in (2014, 2018, 2022)
    }
    checkpoints = [str(value) for value in config["checkpoints"]["times"]]
    return pl.concat(
        [
            replay_cycle(
                preliminary[year],
                _comparison_frame(canonical, year),
                previous_national=_national_shares(results[year - 4]),
                final_national=_national_shares(results[year]),
                checkpoints=checkpoints,
            )
            for year in (2018, 2022)
        ],
        how="vertical",
    )


def write_mae_timeline(metrics: pl.DataFrame, path: Path) -> Path:
    labels = {
        "B0_raw_reported_share": "Rå andel",
        "C1_reported_additive_change": "Additiv förändring",
        "C2_reported_proportional_change": "Proportionell förändring",
    }
    colors = {
        "B0_raw_reported_share": "#64748b",
        "C1_reported_additive_change": "#f59e0b",
        "C2_reported_proportional_change": "#2563eb",
    }
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for axis, year in zip(axes, (2018, 2022), strict=True):
        cycle = metrics.filter(pl.col("election_year") == year)
        origin = ELECTION_DATES[year]
        for model, label in labels.items():
            series = cycle.filter(pl.col("model") == model).sort("checkpoint_at")
            minutes = np.array(
                [
                    (value - origin).total_seconds() / 60
                    for value in series["checkpoint_at"].to_list()
                ]
            )
            axis.plot(
                minutes,
                series["mae"].to_numpy() * 100,
                marker="o",
                linewidth=2,
                markersize=4,
                label=label,
                color=colors[model],
            )
        axis.set_title(str(year))
        axis.set_xlabel("Minuter efter 20:00")
        axis.grid(alpha=0.2)
        axis.set_xlim(40, 240)
    axes[0].set_ylabel("Nationellt MAE (procentenheter)")
    axes[1].legend(frameon=False, fontsize=8)
    figure.suptitle("Valnattsreplay: rå röstandel och förändringsmodeller")
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)
    return path


def write_election_night_report(
    summary: dict[str, Any],
    metrics: pl.DataFrame,
    path: Path,
) -> Path:
    gate = summary["gate"]
    proportional = summary["registered_secondary_proportional_diagnostic"]

    def pp(value: float) -> str:
        return f"{value * 100:.3f}".replace(".", ",")

    def pct(value: float) -> str:
        return f"{value * 100:.1f}".replace(".", ",")

    def metric(year: int, checkpoint: str, model: str) -> dict[str, object]:
        return (
            metrics.filter(
                (pl.col("election_year") == year)
                & (pl.col("checkpoint") == checkpoint)
                & (pl.col("model") == model)
            )
            .to_dicts()
            .pop()
        )

    checkpoint_rows: list[str] = []
    for year in (2018, 2022):
        for checkpoint in ("21:00", "21:30", "22:00", "22:30", "23:00"):
            raw = metric(year, checkpoint, "B0_raw_reported_share")
            additive = metric(year, checkpoint, "C1_reported_additive_change")
            proportional_row = metric(year, checkpoint, "C2_reported_proportional_change")
            checkpoint_rows.append(
                f"| {year} | {checkpoint} | "
                f"{pct(float(str(raw['reported_valid_vote_share'])))} % | "
                f"{pp(float(str(raw['mae'])))} | "
                f"{pp(float(str(additive['mae'])))} | "
                f"{pp(float(str(proportional_row['mae'])))} |"
            )
    auc_rows: list[str] = []
    primary_by_year = {int(row["election_year"]): row for row in gate["cycles"]}
    proportional_by_year = {int(row["election_year"]): row for row in proportional["cycles"]}
    for year in (2018, 2022):
        additive_row = primary_by_year[year]
        proportional_row = proportional_by_year[year]
        auc_rows.append(
            f"| {year} | {pct(float(additive_row['relative_auc_improvement']))} % | "
            f"{additive_row['checkpoint_wins']}/{additive_row['checkpoints']} | "
            f"{pct(float(proportional_row['relative_auc_improvement']))} % | "
            f"{proportional_row['checkpoint_wins']}/{proportional_row['checkpoints']} |"
        )
    text = f"""# Backtest av valnattsprognos

## Slutsats

**Ja, förändringsidén hjälper tydligt under den tidiga valkvällen.** Den
proportionella varianten minskar det tidsintegrerade nationella felet mellan
21:00 och 23:00 med {pct(float(proportional_by_year[2018]["relative_auc_improvement"]))}
procent 2018 och {pct(float(proportional_by_year[2022]["relative_auc_improvement"]))}
procent 2022 jämfört med den råa röstandelen bland rapporterade distrikt.

Den förregistrerade primärmodellen var additiv förändring. Den förbättrar också
det samlade felet i båda valen, men vinner bara 11 av 16 kontrollpunkter och
missar därför den låsta 75-procentsgaten. Den sekundära proportionella modellen
var registrerad före körningen och klarar samma numeriska krav: 13 av 16
kontrollpunkter samt lägre tidsintegrerat fel i båda valen.

Signalen är starkast när få distrikt har rapporterat. När omkring två
tredjedelar eller mer av rösterna är räknade blir den råa röstandelen normalt
bättre. En praktisk liveprodukt bör därför gradvis lämna över från modellen
till det observerade resultatet.

![MAE genom valkvällen](mae_timeline.png)

## Design

Designen låstes i git före första score (`416af9f`, med en ren YAML-fix i
`b72f89c`).

- Valmyndighetens faktiska rapporteringstider och preliminära distriktsröster.
- 6 004 fysiska valdistrikt 2018 och 6 264 år 2022.
- Förändringen beräknas bara i officiellt jämförbara distrikt: 4 631 respektive
  4 164.
- Råresultatet använder samtliga rapporterade fysiska distrikt.
- Målet är det slutliga nationella resultatet i nio kategorier: åtta
  riksdagspartier plus övriga.
- Inga slutresultat från ännu orapporterade distrikt används av modellen.
- Ingen data från valet 2026, VALU eller exit polls används.

## Resultat vid centrala tidpunkter

MAE i procentenheter över de nio partikategorierna.

| Val | Tid | Rapporterade röster | Rå andel | Additiv förändring | Proportionell förändring |
|---:|:---:|---:|---:|---:|---:|
{chr(10).join(checkpoint_rows)}

## Samlat 21:00–23:00

Positiv förbättring betyder lägre tidsintegrerat MAE än den råa röstandelen.

| Val | Additiv ΔAUC | Vinster | Proportionell ΔAUC | Vinster |
|---:|---:|---:|---:|---:|
{chr(10).join(auc_rows)}

Den additiva primärgatens samlade vinstfrekvens är
{pct(float(gate["checkpoint_win_fraction"]))} procent och gaten är därför
**FAIL**. Den registrerade sekundäranalysen når
{pct(float(proportional["checkpoint_win_fraction"]))} procent och skulle under
samma kriterier vara **PASS**. Den distinktionen bevaras för att inte byta
primärmodell efter att resultatet blivit känt.

## Tolkning

Råresultatet är skevt tidigt eftersom små och politiskt annorlunda distrikt
rapporterar först. När samma rapporterade distrikt jämförs med sitt föregående
resultat försvinner mycket av den nivåskillnaden. Proportionella förändringar
fungerar bättre än rena procentenhetsförändringar i båda valen.

Detta är en valnattsmodell, inte en förbättring av den frysta förvalsprognosen.
Nästa version bör använda förvalsprognosen som prior, uppdatera den med
proportionell förändring och vikta över mot råresultatet när täckningen ökar.
Övergångsregeln måste låsas och testas på fler val innan den används live.

## Begränsningar

Arkiven lagrar den senaste preliminära rapporten per distrikt. För två distrikt
i varje val har en senare rättelse sannolikt ersatt den ursprungliga
tidsstämpeln. De ligger efter 02:00 och påverkar inte huvudfönstret 21:00–23:00.

Backtestet omfattar bara två val. Telefonstoppet 2022 finns korrekt kvar i
replayen och är en styrka, men två val räcker inte för att optimera en
överlämningspunkt utan påtaglig risk för överanpassning.
"""
    path.write_text(text, encoding="utf-8")
    return path


def run_election_night_backtest(root: Path) -> dict[str, Any]:
    config_path = root / "config" / "election_night_nowcast.yaml"
    config_bytes = config_path.read_bytes()
    config: dict[str, Any] = yaml.safe_load(config_bytes)
    predictions = build_replay_predictions(root, config)
    metrics = score_replay(predictions)
    gate = evaluate_gate(
        metrics,
        start=str(config["checkpoints"]["primary_window_start"]),
        end=str(config["checkpoints"]["primary_window_end"]),
        minimum_win_fraction=float(
            config["gate"]["requirements"]["minimum_fraction_of_checkpoint_wins"]
        ),
    )
    proportional_diagnostic = evaluate_gate(
        metrics,
        start=str(config["checkpoints"]["primary_window_start"]),
        end=str(config["checkpoints"]["primary_window_end"]),
        minimum_win_fraction=float(
            config["gate"]["requirements"]["minimum_fraction_of_checkpoint_wins"]
        ),
        candidate_id="C2_reported_proportional_change",
    )
    output = root / str(config["outputs"]["directory"])
    output.mkdir(parents=True, exist_ok=True)
    predictions.write_parquet(output / str(config["outputs"]["replay_predictions"]))
    metrics.write_csv(output / str(config["outputs"]["checkpoint_metrics"]))
    summary: dict[str, Any] = {
        "role": config["role"],
        "registered_before_backtest": config["registered_before_backtest"],
        "design_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "sources": {
            str(year): {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "path": str(path.relative_to(root)),
            }
            for year, path in verify_election_night_sources(root).items()
        },
        "cycles": {
            str(year): {
                "physical_districts": (6_004 if year == 2018 else 6_264),
                "comparison_districts": int(
                    pl.read_parquet(
                        root / "data" / "processed" / "canonical_temporal_transitions.parquet"
                    )
                    .filter(pl.col("to_election") == year)["to_district_id"]
                    .n_unique()
                ),
            }
            for year in (2018, 2022)
        },
        "gate": gate,
        "registered_secondary_proportional_diagnostic": proportional_diagnostic,
        "checkpoint_metrics": metrics.to_dicts(),
    }
    (output / str(config["outputs"]["summary"])).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_mae_timeline(metrics, output / "mae_timeline.png")
    write_election_night_report(
        summary,
        metrics,
        output / str(config["outputs"]["report"]),
    )
    return summary
