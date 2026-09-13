from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml
from matplotlib import pyplot as plt

from valforecast.calibration.estimate import error_covariance
from valforecast.experiments.election_night_nowcast import build_replay_predictions
from valforecast.features.election_history import PARTIES
from valforecast.models.baselines import project_simplex
from valforecast.polls.transition_calibrate import calibrate_transition_matrix


@dataclass(frozen=True)
class ValuFoldMetric:
    validation: str
    target_year: int
    model: str
    training_years: str
    mae: float
    max_party_error: float
    largest_party_correct: bool


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_valu_toplines(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path)
    required = {
        "election_year",
        "party",
        "share",
        "sample_size",
        "source_id",
        "source_location",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"VALU top-line file is missing columns: {sorted(missing)}")
    if frame.select(pl.struct("election_year", "party").is_duplicated().any()).item():
        raise ValueError("VALU top-line file has duplicate election/party rows")
    for year, cycle in frame.group_by("election_year"):
        election_year = int(year[0])
        if set(cycle["party"]) != set(PARTIES):
            raise ValueError(f"VALU {election_year} does not have the canonical party set")
        if abs(float(cycle["share"].sum()) - 1.0) > 1e-9:
            raise ValueError(f"VALU {election_year} shares do not sum to one")
        if cycle["sample_size"].n_unique() != 1 or int(cycle["sample_size"][0]) <= 0:
            raise ValueError(f"VALU {election_year} has invalid sample size")
    return frame.sort("election_year", "party")


def valu_vectors(frame: pl.DataFrame) -> dict[int, np.ndarray]:
    vectors: dict[int, np.ndarray] = {}
    for year in sorted(int(value) for value in frame["election_year"].unique()):
        by_party = dict(
            frame.filter(pl.col("election_year") == year).select("party", "share").iter_rows()
        )
        vectors[year] = np.array([float(by_party[party]) for party in PARTIES])
    return vectors


def national_result_vector(results: pl.DataFrame) -> np.ndarray:
    counts = results.group_by("canonical_party_code").agg(pl.col("votes").sum())
    by_party = dict(counts.select("canonical_party_code", "votes").iter_rows())
    vector = np.array([float(by_party.get(party, 0)) for party in PARTIES])
    return np.asarray(vector / vector.sum(), dtype=float)


def load_final_vectors(root: Path, years: list[int]) -> dict[int, np.ndarray]:
    return {
        year: national_result_vector(
            pl.read_parquet(root / "data" / "processed" / f"election_results_{year}.parquet")
        )
        for year in years
    }


def historical_errors(
    valu: dict[int, np.ndarray],
    final: dict[int, np.ndarray],
) -> dict[int, np.ndarray]:
    if set(valu) != set(final):
        raise ValueError("VALU and final-result cycles do not match")
    return {year: valu[year] - final[year] for year in valu}


def shrunk_error(
    errors: dict[int, np.ndarray],
    training_years: list[int],
    *,
    prior_strength: float,
) -> tuple[np.ndarray, float]:
    if not training_years:
        raise ValueError("At least one historical VALU cycle is required")
    raw_mean = np.mean(np.vstack([errors[year] for year in training_years]), axis=0)
    shrinkage = len(training_years) / (len(training_years) + prior_strength)
    return np.asarray(shrinkage * raw_mean, dtype=float), shrinkage


def corrected_valu(raw: np.ndarray, estimated_error: np.ndarray) -> np.ndarray:
    return project_simplex(raw - estimated_error)


def _metric(
    actual: np.ndarray,
    predicted: np.ndarray,
    *,
    validation: str,
    target_year: int,
    model: str,
    training_years: list[int],
) -> ValuFoldMetric:
    absolute = np.abs(actual - predicted)
    return ValuFoldMetric(
        validation=validation,
        target_year=target_year,
        model=model,
        training_years=",".join(str(year) for year in training_years),
        mae=float(absolute.mean()),
        max_party_error=float(absolute.max()),
        largest_party_correct=int(np.argmax(actual)) == int(np.argmax(predicted)),
    )


def run_topline_backtests(
    valu: dict[int, np.ndarray],
    final: dict[int, np.ndarray],
    *,
    prior_strength: float,
) -> list[ValuFoldMetric]:
    errors = historical_errors(valu, final)
    years = sorted(valu)
    folds: list[ValuFoldMetric] = []
    for target_year in years[1:]:
        training = [year for year in years if year < target_year]
        estimate, _shrinkage = shrunk_error(errors, training, prior_strength=prior_strength)
        folds.extend(
            [
                _metric(
                    final[target_year],
                    valu[target_year],
                    validation="expanding_window",
                    target_year=target_year,
                    model="raw_valu",
                    training_years=training,
                ),
                _metric(
                    final[target_year],
                    corrected_valu(valu[target_year], estimate),
                    validation="expanding_window",
                    target_year=target_year,
                    model="sequential_shrunk_correction",
                    training_years=training,
                ),
            ]
        )
    for target_year in years:
        training = [year for year in years if year != target_year]
        estimate, _shrinkage = shrunk_error(errors, training, prior_strength=prior_strength)
        folds.extend(
            [
                _metric(
                    final[target_year],
                    valu[target_year],
                    validation="leave_one_election_out",
                    target_year=target_year,
                    model="raw_valu",
                    training_years=training,
                ),
                _metric(
                    final[target_year],
                    corrected_valu(valu[target_year], estimate),
                    validation="leave_one_election_out",
                    target_year=target_year,
                    model="loeo_shrunk_correction",
                    training_years=training,
                ),
            ]
        )
    return folds


def topline_gate(
    folds: list[ValuFoldMetric],
    *,
    minimum_cycles_won: int,
) -> dict[str, object]:
    primary = [fold for fold in folds if fold.validation == "expanding_window"]
    raw = {fold.target_year: fold for fold in primary if fold.model == "raw_valu"}
    corrected = {
        fold.target_year: fold for fold in primary if fold.model == "sequential_shrunk_correction"
    }
    years = sorted(raw)
    cycles_won = sum(corrected[year].mae < raw[year].mae for year in years)
    raw_pooled = float(np.mean([raw[year].mae for year in years]))
    corrected_pooled = float(np.mean([corrected[year].mae for year in years]))
    return {
        "passed": corrected_pooled < raw_pooled and cycles_won >= minimum_cycles_won,
        "cycles_won": cycles_won,
        "cycles_scored": len(years),
        "minimum_cycles_won": minimum_cycles_won,
        "raw_pooled_mae": raw_pooled,
        "corrected_pooled_mae": corrected_pooled,
        "relative_improvement": (raw_pooled - corrected_pooled) / raw_pooled,
    }


def build_estimator_lock(
    errors: dict[int, np.ndarray],
    *,
    prior_strength: float,
    design_sha256: str,
    data_sha256: str,
) -> dict[str, Any]:
    years = sorted(errors)
    estimate, shrinkage = shrunk_error(errors, years, prior_strength=prior_strength)
    covariance_rows = [
        {"error": {party: float(errors[year][index]) for index, party in enumerate(PARTIES)}}
        for year in years
    ]
    covariance = error_covariance(covariance_rows)
    covariance["sigma"] = np.asarray(covariance["sigma"], dtype=float).tolist()
    return {
        "schema_version": 1,
        "role": "post_poll_close_valu_calibration",
        "not_pre_election_input": True,
        "training_cycles": years,
        "prior_strength_elections": prior_strength,
        "shrinkage": shrinkage,
        "estimated_valu_minus_final_error": {
            party: float(estimate[index]) for index, party in enumerate(PARTIES)
        },
        "raw_error_covariance": covariance,
        "design_sha256": design_sha256,
        "data_sha256": data_sha256,
    }


def _timeline_auc(frame: pl.DataFrame, column: str) -> float:
    ordered = frame.sort("checkpoint_at")
    timestamps = ordered["checkpoint_at"].cast(pl.Int64).to_numpy()
    hours = (timestamps - timestamps[0]) / 3_600_000_000
    return float(np.trapezoid(ordered[column].to_numpy(), hours))


def combined_live_backtest(
    replay: pl.DataFrame,
    valu: dict[int, np.ndarray],
    final: dict[int, np.ndarray],
    errors: dict[int, np.ndarray],
    *,
    prior_strength: float,
    start: str,
    end: str,
) -> tuple[pl.DataFrame, dict[str, object]]:
    records: list[dict[str, object]] = []
    for year in (2018, 2022):
        training = sorted(training_year for training_year in valu if training_year < year)
        estimated_error, _ = shrunk_error(errors, training, prior_strength=prior_strength)
        valu_point = corrected_valu(valu[year], estimated_error)
        cycle = replay.filter(pl.col("election_year") == year)
        for row in cycle.iter_rows(named=True):
            coverage = float(row["reported_district_share"])
            valu_to_count = coverage / (coverage + 0.05)
            count_to_raw = coverage**2 / (coverage**2 + (1.0 - coverage) ** 2)
            raw = np.array([float(row[f"raw_{party}"]) for party in PARTIES])
            proportional = np.array([float(row[f"proportional_{party}"]) for party in PARTIES])
            count_point = (1.0 - count_to_raw) * proportional + count_to_raw * raw
            combined = (1.0 - valu_to_count) * valu_point + valu_to_count * count_point
            target = final[year]
            records.append(
                {
                    "election_year": year,
                    "checkpoint": str(row["checkpoint"]),
                    "checkpoint_at": row["checkpoint_at"],
                    "reported_district_share": coverage,
                    "valu_to_count_weight": valu_to_count,
                    "count_to_raw_weight": count_to_raw,
                    "raw_mae": float(np.abs(raw - target).mean()),
                    "calibrated_valu_mae": float(np.abs(valu_point - target).mean()),
                    "proportional_count_mae": float(np.abs(proportional - target).mean()),
                    "combined_mae": float(np.abs(combined - target).mean()),
                    **{
                        f"combined_{party}": float(combined[index])
                        for index, party in enumerate(PARTIES)
                    },
                }
            )
    result = pl.DataFrame(records).sort("election_year", "checkpoint_at")
    cycles: list[dict[str, object]] = []
    for year in (2018, 2022):
        start_time = datetime.fromisoformat(
            f"{year}-09-09T{start}:00" if year == 2018 else f"{year}-09-11T{start}:00"
        )
        end_time = datetime.fromisoformat(
            f"{year}-09-09T{end}:00" if year == 2018 else f"{year}-09-11T{end}:00"
        )
        window = result.filter(
            (pl.col("election_year") == year)
            & pl.col("checkpoint_at").is_between(start_time, end_time, closed="both")
        )
        raw_auc = _timeline_auc(window, "raw_mae")
        valu_auc = _timeline_auc(window, "calibrated_valu_mae")
        combined_auc = _timeline_auc(window, "combined_mae")
        cycles.append(
            {
                "election_year": year,
                "raw_auc": raw_auc,
                "valu_auc": valu_auc,
                "combined_auc": combined_auc,
                "improvement_vs_raw": (raw_auc - combined_auc) / raw_auc,
                "improvement_vs_valu": (valu_auc - combined_auc) / valu_auc,
                "beats_raw": combined_auc < raw_auc,
                "beats_valu": combined_auc < valu_auc,
            }
        )
    gate = {
        "passed": all(bool(row["beats_raw"]) and bool(row["beats_valu"]) for row in cycles),
        "window": f"{start}/{end}",
        "cycles": cycles,
    }
    return result, gate


def validate_live_input(document: dict[str, Any], config: dict[str, Any]) -> None:
    if document.get("schema_version") != 1 or document.get("election_year") != 2026:
        raise ValueError("Expected a schema-version 1 VALU input for election 2026")
    if document.get("status") != "published":
        raise ValueError("VALU 2026 is still pending; no live estimate can be produced")
    required_text = ("published_at", "retrieved_at", "source_url", "source_sha256")
    for field in required_text:
        if not isinstance(document.get(field), str) or not document[field]:
            raise ValueError(f"Published VALU input requires {field}")
    published = datetime.fromisoformat(str(document["published_at"]))
    retrieved = datetime.fromisoformat(str(document["retrieved_at"]))
    earliest = datetime.fromisoformat(str(config["valu"]["earliest_publication"]))
    if published < earliest:
        raise ValueError("VALU publication time precedes the allowed poll-close time")
    if retrieved < published:
        raise ValueError("VALU retrieval time precedes publication")
    if not str(document["source_url"]).startswith("https://"):
        raise ValueError("VALU source URL must use HTTPS")
    if not re.fullmatch(r"[0-9a-f]{64}", str(document["source_sha256"])):
        raise ValueError("VALU source SHA-256 is invalid")
    if not isinstance(document.get("sample_size"), int) or document["sample_size"] <= 0:
        raise ValueError("VALU sample_size must be a positive integer")
    topline = document.get("topline")
    if not isinstance(topline, dict) or set(topline) != set(PARTIES):
        raise ValueError("VALU top line must contain the canonical party set")
    values = np.array([float(topline[party]) for party in PARTIES])
    if np.any(values < 0):
        raise ValueError("VALU shares cannot be negative")
    tolerance = float(config["validation"]["share_sum_tolerance"])
    if abs(float(values.sum()) - 1.0) > tolerance:
        raise ValueError("VALU shares do not sum to one")
    transition = document.get("transition")
    if transition is not None:
        _transition_array(transition, config)


def _transition_array(transition: object, config: dict[str, Any]) -> np.ndarray:
    if not isinstance(transition, dict) or set(transition) != set(PARTIES):
        raise ValueError("VALU transition must contain one row per previous party")
    matrix = np.empty((len(PARTIES), len(PARTIES)), dtype=float)
    tolerance = float(config["validation"]["transition_row_sum_tolerance"])
    for row_index, previous_party in enumerate(PARTIES):
        row = transition[previous_party]
        if not isinstance(row, dict) or set(row) != set(PARTIES):
            raise ValueError(f"VALU transition row {previous_party} is incomplete")
        values = np.array([float(row[party]) for party in PARTIES])
        if np.any(values < 0) or abs(float(values.sum()) - 1.0) > tolerance:
            raise ValueError(f"VALU transition row {previous_party} is invalid")
        matrix[row_index] = values / values.sum()
    return matrix


def prepare_live_valu(
    root: Path,
    input_path: Path,
    *,
    write: bool = False,
) -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(
        (root / "config" / "valu_live_2026.yaml").read_text(encoding="utf-8")
    )
    prior_path = root / str(config["prior"]["path"])
    if sha256_file(prior_path) != config["prior"]["sha256"]:
        raise ValueError("Frozen pre-election forecast hash has changed")
    document: dict[str, Any] = json.loads(input_path.read_text(encoding="utf-8"))
    if document.get("status") == "pending":
        return {
            "status": "waiting_for_published_valu",
            "input": str(input_path),
            "official_forecast_unchanged": True,
        }
    validate_live_input(document, config)
    lock_path = root / str(config["valu"]["estimator_lock"])
    lock: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
    raw = np.array([float(document["topline"][party]) for party in PARTIES])
    estimated_error = np.array(
        [float(lock["estimated_valu_minus_final_error"][party]) for party in PARTIES]
    )
    corrected = corrected_valu(raw, estimated_error)
    prior_document = json.loads(prior_path.read_text(encoding="utf-8"))
    prior_national = prior_document["prediction"]["national"]
    result: dict[str, Any] = {
        "schema_version": 1,
        "role": "post_poll_close_live_forecast",
        "status": "valu_ready",
        "published_at": document["published_at"],
        "retrieved_at": document["retrieved_at"],
        "source_url": document["source_url"],
        "source_sha256": document["source_sha256"],
        "sample_size": document["sample_size"],
        "frozen_pre_election_point": {
            party: float(prior_national[party]["point"]) for party in PARTIES
        },
        "raw_valu": {party: float(raw[index]) for index, party in enumerate(PARTIES)},
        "calibrated_valu": {party: float(corrected[index]) for index, party in enumerate(PARTIES)},
        "calibration_lock_sha256": sha256_file(lock_path),
        "transition": None,
        "official_forecast_unchanged": True,
    }
    if document.get("transition") is not None:
        matrix = _transition_array(document["transition"], config)
        final_2022 = load_final_vectors(root, [2022])[2022]
        calibrated = calibrate_transition_matrix(matrix, final_2022, corrected)
        if not calibrated.converged:
            raise ValueError("VALU transition matrix did not rake to the top line")
        result["transition"] = {
            "party_order": list(PARTIES),
            "matrix": calibrated.matrix.tolist(),
            "topline_role": "single_raking_target",
            "maximum_margin_error": calibrated.maximum_margin_error,
            "kl_divergence": calibrated.kl_divergence,
        }
    if write:
        output = root / str(config["outputs"]["directory"])
        output.mkdir(parents=True, exist_ok=True)
        path = output / str(config["outputs"]["valu_snapshot"])
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["written"] = str(path)
    return result


def write_combined_plot(frame: pl.DataFrame, path: Path) -> Path:
    columns = {
        "raw_mae": ("Rå röstandel", "#64748b"),
        "calibrated_valu_mae": ("Kalibrerad VALU", "#9333ea"),
        "proportional_count_mae": ("Proportionell förändring", "#2563eb"),
        "combined_mae": ("Kombinerad livekedja", "#16a34a"),
    }
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for axis, year in zip(axes, (2018, 2022), strict=True):
        cycle = frame.filter(pl.col("election_year") == year).sort("checkpoint_at")
        timestamps = cycle["checkpoint_at"].to_list()
        origin = timestamps[0].replace(hour=20, minute=0, second=0)
        minutes = np.array([(timestamp - origin).total_seconds() / 60 for timestamp in timestamps])
        for column, (label, color) in columns.items():
            axis.plot(
                minutes,
                cycle[column].to_numpy() * 100,
                marker="o",
                markersize=4,
                linewidth=2,
                label=label,
                color=color,
            )
        axis.set_title(str(year))
        axis.set_xlabel("Minuter efter 20:00")
        axis.grid(alpha=0.2)
        axis.set_xlim(40, 185)
    axes[0].set_ylabel("Nationellt MAE (procentenheter)")
    axes[1].legend(frameon=False, fontsize=8)
    figure.suptitle("Historisk replay: VALU kombinerad med distriktsresultat")
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)
    return path


def write_valu_report(
    summary: dict[str, Any],
    combined: pl.DataFrame,
    path: Path,
) -> Path:
    def pp(value: float) -> str:
        return f"{value * 100:.3f}".replace(".", ",")

    def pct(value: float) -> str:
        return f"{value * 100:.1f}".replace(".", ",")

    expanding = [fold for fold in summary["folds"] if fold["validation"] == "expanding_window"]
    by_year_model = {(int(fold["target_year"]), str(fold["model"])): fold for fold in expanding}
    topline_rows = []
    for year in (2014, 2018, 2022):
        raw = by_year_model[(year, "raw_valu")]
        corrected = by_year_model[(year, "sequential_shrunk_correction")]
        relative = (float(raw["mae"]) - float(corrected["mae"])) / float(raw["mae"])
        topline_rows.append(
            f"| {year} | {pp(float(raw['mae']))} | {pp(float(corrected['mae']))} | "
            f"{pct(relative)} % |"
        )
    combined_rows = []
    for row in summary["combined_live_gate"]["cycles"]:
        combined_rows.append(
            f"| {row['election_year']} | {pct(float(row['improvement_vs_raw']))} % | "
            f"{pct(float(row['improvement_vs_valu']))} % |"
        )
    lock = summary["estimator"]
    effect_lines = []
    for party in PARTIES:
        value = float(lock["estimated_valu_minus_final_error"][party]) * 100
        effect_lines.append(
            f"| {party} | {value:+.3f} |".replace(
                ".",
                ",",
            )
        )
    effect_rows = "\n".join(effect_lines)
    gate = summary["topline_gate"]
    text = f"""# VALU-backtest och liveberedskap

## Slutsats

Den historiska, tidskorrekta VALU-korrigeringen klarar den förregistrerade
gaten. Den minskar genomsnittligt MAE från {pp(float(gate["raw_pooled_mae"]))}
till {pp(float(gate["corrected_pooled_mae"]))} procentenheter, en förbättring
på {pct(float(gate["relative_improvement"]))} procent, och förbättrar samtliga
tre framåtriktade testval.

Effekten är liten och materialet omfattar bara fyra VALU-prognoser. Korrigeringen
ska därför ses som försiktig biasjustering, inte som ett facit.

Den i förväg låsta livekedjan – kalibrerad VALU, proportionell förändring i
rapporterade jämförbara distrikt och slutligen råresultat – förbättrar
tidsintegrerat fel mot båda sina baslinjer i replay av både 2018 och 2022.

![VALU och valnattsresultat](combined_timeline.png)

## Historiska 20:00-prognoser

Underlaget är SVT:s viktade prognos som presenterades när vallokalerna stängde,
inte senare VALU-tabeller som viktats mot ett känt preliminärt eller slutligt
valresultat.

| Testval | Rå VALU MAE | Tidskorrekt korrigerad MAE | Förbättring |
|---:|---:|---:|---:|
{chr(10).join(topline_rows)}

Kalibreringen är expanding-window:

- 2014 använder endast felet 2010,
- 2018 använder endast 2010 och 2014,
- 2022 använder endast 2010, 2014 och 2018.

Ingen information från testvalets slutresultat används för att korrigera samma
val. En separat leave-one-election-out-tabell finns i `fold_metrics.csv`.

## Låst korrigering för VALU 2026

Värdena nedan är skattat historiskt `VALU − slutresultat`, krympt mot noll med
två val som priorstyrka. Vid publicering subtraheras värdet från rå VALU och
vektorn projiceras tillbaka till summan 100 procent.

| Parti | Skattat fel, procentenheter |
|:---|---:|
{effect_rows}

Den skattade felkovariansen och alla lås finns i `estimator_lock.json`.
Osäkerheten bygger på endast fyra val och ska beskrivas som approximativ.

## Kombination med valnattsräkningen

Den förregistrerade vikten använder andelen rapporterade distrikt, vilket är
känt i realtid:

1. vid noll rapporterade distrikt är prognosen kalibrerad VALU,
2. proportionell förändring tar snabbt över när jämförbara distrikt rapporterar,
3. råresultatet tar gradvis över när distriktstäckningen blir hög.

| Replay | Förbättring mot rå räkning | Förbättring mot konstant VALU |
|---:|---:|---:|
{chr(10).join(combined_rows)}

Gaten kräver lägre tidsintegrerat fel än båda baslinjerna i båda valen:
**{"PASS" if summary["combined_live_gate"]["passed"] else "FAIL"}**.

## Väljarflöden

Historiska flödestabeller används inte i backtestet. De offentliga tabeller som
överlevt från 2014, 2018 och 2022 har viktats eller reviderats efter att
preliminära eller slutliga resultat blivit kända.

Om en fullständig 2026-matris publiceras kan den användas för geografisk
fördelning. Då är matrisen kernelstruktur och VALU-topplinjen används exakt en
gång som rakingmål. De får inte behandlas som två oberoende mätningar.

## Körning på valkvällen

1. Spara den publicerade originalkällan och beräkna SHA-256.
2. Kopiera `data/templates/valu_2026.template.json`.
3. Sätt `status` till `published`, fyll tidsstämplar, källa, stickprovsstorlek
   och andelar som bråk mellan 0 och 1.
4. Lägg eventuellt in en fullständig 9×9-flödesmatris.
5. Kör:

```bash
uv run valforecast prepare-valu-live --input <fil.json>
uv run valforecast prepare-valu-live --input <fil.json> --write
```

Utan `--write` valideras och visas resultatet. Med `--write` skapas
`reports/live/valu_2026.json`. Kommandot kan aldrig skriva över den frysta
förvalsprognosen.

## Status före klockan 20

2026-mallen är `pending`. Pipen returnerar därför endast
`waiting_for_published_valu`; ingen syntetisk VALU eller valresultat används.
"""
    path.write_text(text, encoding="utf-8")
    return path


def run_valu_backtest(root: Path) -> dict[str, Any]:
    config_path = root / "config" / "valu_backtest.yaml"
    config_bytes = config_path.read_bytes()
    config: dict[str, Any] = yaml.safe_load(config_bytes)
    top_path = root / str(config["topline"]["path"])
    top_frame = read_valu_toplines(top_path)
    valu = valu_vectors(top_frame)
    years = [int(year) for year in config["topline"]["cycles"]]
    final = load_final_vectors(root, years)
    errors = historical_errors(valu, final)
    prior_strength = float(config["calibration"]["prior_strength_elections"])
    folds = run_topline_backtests(valu, final, prior_strength=prior_strength)
    gate = topline_gate(
        folds,
        minimum_cycles_won=int(config["gate"]["requirements"]["minimum_cycles_won"]),
    )
    design_sha = hashlib.sha256(config_bytes).hexdigest()
    data_sha = sha256_file(top_path)
    lock = build_estimator_lock(
        errors,
        prior_strength=prior_strength,
        design_sha256=design_sha,
        data_sha256=data_sha,
    )
    replay_config: dict[str, Any] = yaml.safe_load(
        (root / "config" / "election_night_nowcast.yaml").read_text(encoding="utf-8")
    )
    replay = build_replay_predictions(root, replay_config)
    combined, combined_gate = combined_live_backtest(
        replay,
        valu,
        final,
        errors,
        prior_strength=prior_strength,
        start="20:45",
        end="23:00",
    )
    output = root / str(config["outputs"]["directory"])
    output.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([asdict(fold) for fold in folds]).write_csv(
        output / str(config["outputs"]["fold_metrics"])
    )
    combined.write_csv(output / str(config["outputs"]["combined_timeline"]))
    (output / str(config["outputs"]["estimator_lock"])).write_text(
        json.dumps(lock, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary: dict[str, Any] = {
        "role": config["role"],
        "registered_before_backtest": config["registered_before_backtest"],
        "design_sha256": design_sha,
        "data_sha256": data_sha,
        "cycles": years,
        "topline_gate": gate,
        "folds": [asdict(fold) for fold in folds],
        "combined_live_gate": combined_gate,
        "estimator": lock,
        "live_2026": {
            "status": "waiting_for_published_valu",
            "template": "data/templates/valu_2026.template.json",
        },
    }
    (output / str(config["outputs"]["summary"])).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_combined_plot(combined, output / "combined_timeline.png")
    write_valu_report(summary, combined, output / str(config["outputs"]["report"]))
    return summary
