from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml
from sklearn.linear_model import Ridge  # type: ignore[import-untyped]

from valforecast.models.baselines import predict_proportional_swing, project_simplex

PARTIES = ("V", "S", "MP", "C", "L", "M", "KD", "SD", "OTHER")
NAMED_PARTIES = PARTIES[:-1]
FEATURES = (
    "reception_rate_d2",
    "delta_reception_rate_d2",
    "early_share_d7_of_d2",
    "delta_early_share_d7_of_d2",
    "active_locations_per_10000_d2",
    "delta_active_locations_per_10000_d2",
)
ELECTION_DATES = {
    2010: date(2010, 9, 19),
    2014: date(2014, 9, 14),
    2018: date(2018, 9, 9),
    2022: date(2022, 9, 11),
    2026: date(2026, 9, 13),
}
ADVANCE_FILES = {
    2010: Path("data/raw/advance_voting/2010/mottagna_fortidsroster.skv"),
    2014: Path("data/raw/advance_voting/2014/mottagna_fortidsroster.skv"),
    2018: Path("data/raw/advance_voting/2018/mottagna_fortidsroster.skv"),
    2022: Path("data/raw/advance_voting/2022/fortidsroster.csv"),
}


@dataclass(frozen=True)
class FoldMetrics:
    target_year: int
    outcome: str
    baseline_weighted_mae: float
    candidate_weighted_mae: float
    relative_improvement: float
    baseline_unweighted_mae: float
    candidate_unweighted_mae: float
    observations: int
    weight_sum: float


def _as_int(value: str | None) -> int:
    text = (value or "").strip().replace("\xa0", "")
    return int(text) if text else 0


def _date_columns(fieldnames: Sequence[str]) -> dict[str, date]:
    columns: dict[str, date] = {}
    for field in fieldnames:
        try:
            columns[field] = date.fromisoformat(field[:10])
        except ValueError:
            continue
    return columns


def read_advance_vote_file(
    path: Path,
    *,
    election_year: int,
    cutoff_days: int = 2,
) -> pl.DataFrame:
    """Aggregate public receipt-location counts to reception municipality.

    These are counts by the municipality where the ballot was handed in. They
    are not counts by the voter's home municipality or home electoral district.
    """
    election_date = ELECTION_DATES[election_year]
    with path.open(encoding="cp1252", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header")
        dates = _date_columns(reader.fieldnames)
        if not dates:
            raise ValueError(f"{path} has no receipt-date columns")
        d2_columns = [
            column
            for column, receipt_date in dates.items()
            if receipt_date <= election_date - timedelta(days=cutoff_days)
        ]
        d7_columns = [
            column
            for column, receipt_date in dates.items()
            if receipt_date <= election_date - timedelta(days=7)
        ]
        if not d2_columns or not d7_columns:
            raise ValueError(f"{path} does not cover the registered cutoffs")

        records: list[dict[str, object]] = []
        for row in reader:
            county = (row.get("lan") or row.get("LÄNSKOD") or "").strip()
            municipality = (row.get("kom") or row.get("KOMMUNKOD") or "").strip()
            if not county.isdigit() or not municipality.isdigit():
                continue
            received_d2 = sum(_as_int(row.get(column)) for column in d2_columns)
            received_d7 = sum(_as_int(row.get(column)) for column in d7_columns)
            records.append(
                {
                    "election_year": election_year,
                    "municipality_id": f"{int(county):02d}{int(municipality):02d}",
                    "municipality_name": (row.get("kommun") or row.get("KOMMUN") or "").strip(),
                    "location_id": (row.get("lokalid") or row.get("LOKALID") or "").strip(),
                    "received_d2": received_d2,
                    "received_d7": received_d7,
                    "active_d2": int(received_d2 > 0),
                }
            )
    if not records:
        raise ValueError(f"{path} has no municipality rows")
    return (
        pl.DataFrame(records)
        .group_by("election_year", "municipality_id")
        .agg(
            pl.col("municipality_name").first(),
            pl.col("received_d2").sum(),
            pl.col("received_d7").sum(),
            pl.col("active_d2").sum().alias("active_locations_d2"),
            pl.len().alias("registered_locations"),
        )
        .sort("municipality_id")
    )


def municipality_outcomes(results: pl.DataFrame) -> pl.DataFrame:
    """Aggregate final election results to municipality without double-counting."""
    district_totals = (
        results.group_by("district_id")
        .agg(
            pl.col("election_year").first(),
            pl.col("municipality_id").first(),
            pl.col("valid_votes").first(),
            pl.col("invalid_votes").first(),
            pl.col("eligible_voters").first(),
        )
        .group_by("election_year", "municipality_id")
        .agg(
            pl.col("valid_votes").sum(),
            pl.col("invalid_votes").sum(),
            pl.col("eligible_voters").sum(),
        )
        .with_columns(
            ((pl.col("valid_votes") + pl.col("invalid_votes")) / pl.col("eligible_voters")).alias(
                "turnout"
            )
        )
    )
    party_votes = (
        results.group_by("election_year", "municipality_id", "canonical_party_code")
        .agg(pl.col("votes").sum())
        .pivot(
            on="canonical_party_code",
            index=["election_year", "municipality_id"],
            values="votes",
        )
    )
    missing = set(PARTIES) - set(party_votes.columns)
    party_votes = party_votes.with_columns(*(pl.lit(0).alias(party) for party in sorted(missing)))
    return district_totals.join(
        party_votes.select("election_year", "municipality_id", *PARTIES),
        on=["election_year", "municipality_id"],
        how="inner",
    ).with_columns(*(pl.col(party) / pl.col("valid_votes") for party in PARTIES))


def add_advance_features(
    advance: pl.DataFrame,
    outcomes: pl.DataFrame,
) -> pl.DataFrame:
    joined = advance.join(
        outcomes.select("municipality_id", "eligible_voters"),
        on="municipality_id",
        how="inner",
        validate="1:1",
    )
    return joined.with_columns(
        (pl.col("received_d2") / pl.col("eligible_voters")).alias("reception_rate_d2"),
        (pl.col("received_d7") / pl.col("received_d2")).fill_nan(0.0).alias("early_share_d7_of_d2"),
        (pl.col("active_locations_d2") * 10_000 / pl.col("eligible_voters")).alias(
            "active_locations_per_10000_d2"
        ),
    )


def _national_party_shares(frame: pl.DataFrame) -> np.ndarray:
    totals = frame.select(
        *((pl.col(party) * pl.col("valid_votes")).sum() for party in PARTIES)
    ).row(0)
    values = np.array([float(value) for value in totals], dtype=float)
    return np.asarray(values / values.sum(), dtype=float)


def _national_turnout(frame: pl.DataFrame) -> float:
    totals = frame.select(
        (pl.col("valid_votes") + pl.col("invalid_votes")).sum().alias("voters"),
        pl.col("eligible_voters").sum().alias("eligible"),
    ).row(0)
    return float(totals[0]) / float(totals[1])


def build_target_cycle(
    previous_advance: pl.DataFrame,
    current_advance: pl.DataFrame,
    previous_outcomes: pl.DataFrame,
    current_outcomes: pl.DataFrame,
) -> pl.DataFrame:
    """Build one target election with oracle-national spatial baselines."""
    target_year = int(current_outcomes["election_year"][0])
    previous_year = int(previous_outcomes["election_year"][0])
    previous_features = add_advance_features(previous_advance, previous_outcomes)
    current_features = add_advance_features(current_advance, current_outcomes)

    joined = (
        current_features.join(
            previous_features.select(
                "municipality_id",
                *(
                    pl.col(feature).alias(f"previous_{feature}")
                    for feature in (
                        "reception_rate_d2",
                        "early_share_d7_of_d2",
                        "active_locations_per_10000_d2",
                    )
                ),
            ),
            on="municipality_id",
            how="inner",
            validate="1:1",
        )
        .join(
            current_outcomes,
            on="municipality_id",
            how="inner",
            validate="1:1",
            suffix="_outcome",
        )
        .join(
            previous_outcomes.select(
                "municipality_id",
                pl.col("valid_votes").alias("previous_valid_votes"),
                pl.col("turnout").alias("previous_turnout"),
                *(pl.col(party).alias(f"previous_{party}") for party in PARTIES),
            ),
            on="municipality_id",
            how="inner",
            validate="1:1",
        )
        .with_columns(
            (pl.col("reception_rate_d2") - pl.col("previous_reception_rate_d2")).alias(
                "delta_reception_rate_d2"
            ),
            (pl.col("early_share_d7_of_d2") - pl.col("previous_early_share_d7_of_d2")).alias(
                "delta_early_share_d7_of_d2"
            ),
            (
                pl.col("active_locations_per_10000_d2")
                - pl.col("previous_active_locations_per_10000_d2")
            ).alias("delta_active_locations_per_10000_d2"),
        )
    )

    national_previous = _national_party_shares(previous_outcomes)
    national_current = _national_party_shares(current_outcomes)
    national_turnout_delta = _national_turnout(current_outcomes) - _national_turnout(
        previous_outcomes
    )
    records: list[dict[str, object]] = []
    for row in joined.iter_rows(named=True):
        previous_vector = np.array(
            [float(row[f"previous_{party}"]) for party in PARTIES], dtype=float
        )
        baseline = predict_proportional_swing(
            previous_vector,
            national_previous,
            national_current,
        )
        record: dict[str, object] = {
            "target_year": target_year,
            "previous_year": previous_year,
            "municipality_id": str(row["municipality_id"]),
            "municipality_name": str(row["municipality_name"]),
            "valid_votes": int(row["valid_votes"]),
            "previous_valid_votes": int(row["previous_valid_votes"]),
            "eligible_voters": int(row["eligible_voters_outcome"]),
            "received_d2": int(row["received_d2"]),
            "actual_turnout": float(row["turnout"]),
            "baseline_turnout": float(row["previous_turnout"]) + national_turnout_delta,
        }
        for feature in FEATURES:
            record[feature] = float(row[feature])
        for index, party in enumerate(PARTIES):
            record[f"previous_{party}"] = float(previous_vector[index])
            record[f"actual_{party}"] = float(row[party])
            record[f"baseline_{party}"] = float(baseline[index])
        records.append(record)
    frame = pl.DataFrame(records)
    return _standardize_within_cycle(frame)


def literal_geographic_reweighting(frame: pl.DataFrame) -> list[dict[str, object]]:
    """Diagnostic version of "weight old behavior by where early votes were cast".

    This is deliberately not a gate model. It neither knows current opinion nor
    solves the reception-place/home-address mismatch; it tests the literal
    intuition against leaving the previous national result unchanged.
    """
    diagnostics: list[dict[str, object]] = []
    for year in sorted(int(value) for value in frame["target_year"].unique()):
        cycle = frame.filter(pl.col("target_year") == year)
        previous_weights = cycle["previous_valid_votes"].to_numpy()
        advance_weights = cycle["received_d2"].to_numpy()
        actual_weights = cycle["valid_votes"].to_numpy()
        previous = cycle.select(*(f"previous_{party}" for party in PARTIES)).to_numpy()
        actual = cycle.select(*(f"actual_{party}" for party in PARTIES)).to_numpy()
        previous_national = np.average(previous, axis=0, weights=previous_weights)
        advance_weighted = np.average(previous, axis=0, weights=advance_weights)
        actual_national = np.average(actual, axis=0, weights=actual_weights)
        baseline_mae = float(
            np.abs(
                previous_national[: len(NAMED_PARTIES)] - actual_national[: len(NAMED_PARTIES)]
            ).mean()
        )
        candidate_mae = float(
            np.abs(
                advance_weighted[: len(NAMED_PARTIES)] - actual_national[: len(NAMED_PARTIES)]
            ).mean()
        )
        diagnostics.append(
            {
                "target_year": year,
                "baseline_previous_national_mae": baseline_mae,
                "advance_weighted_previous_geography_mae": candidate_mae,
                "relative_improvement": _relative_improvement(baseline_mae, candidate_mae),
                "previous_national": {
                    party: float(previous_national[index]) for index, party in enumerate(PARTIES)
                },
                "advance_weighted": {
                    party: float(advance_weighted[index]) for index, party in enumerate(PARTIES)
                },
                "actual_national": {
                    party: float(actual_national[index]) for index, party in enumerate(PARTIES)
                },
            }
        )
    return diagnostics


def _standardize_within_cycle(frame: pl.DataFrame) -> pl.DataFrame:
    expressions: list[pl.Expr] = []
    for feature in FEATURES:
        value = frame.select(pl.col(feature).std()).item()
        if not isinstance(value, (int, float)):
            raise ValueError(f"Cannot standardize {feature}")
        standard_deviation = float(value)
        if not np.isfinite(standard_deviation) or standard_deviation <= 0:
            raise ValueError(f"Cannot standardize {feature}")
        expressions.append(
            ((pl.col(feature) - pl.col(feature).mean()) / standard_deviation).alias(f"z_{feature}")
        )
    return frame.with_columns(*expressions)


def _mae(
    actual: np.ndarray,
    predicted: np.ndarray,
    weights: np.ndarray,
) -> tuple[float, float]:
    absolute = np.abs(actual - predicted)
    if absolute.ndim == 2:
        weighted = np.average(absolute.mean(axis=1), weights=weights)
    else:
        weighted = np.average(absolute, weights=weights)
    return float(weighted), float(absolute.mean())


def _relative_improvement(baseline: float, candidate: float) -> float:
    return (baseline - candidate) / baseline


def leave_one_election_out(
    cycles: pl.DataFrame,
    *,
    alpha: float,
) -> tuple[list[FoldMetrics], pl.DataFrame, dict[str, dict[str, float]]]:
    feature_columns = [f"z_{feature}" for feature in FEATURES]
    years = sorted(int(year) for year in cycles["target_year"].unique())
    folds: list[FoldMetrics] = []
    predictions: list[pl.DataFrame] = []
    coefficients: dict[str, dict[str, float]] = {}

    for target_year in years:
        train = cycles.filter(pl.col("target_year") != target_year)
        test = cycles.filter(pl.col("target_year") == target_year)
        x_train = train.select(feature_columns).to_numpy()
        x_test = test.select(feature_columns).to_numpy()

        actual_parties = test.select(*(f"actual_{party}" for party in PARTIES)).to_numpy()
        baseline_parties = test.select(*(f"baseline_{party}" for party in PARTIES)).to_numpy()
        candidate_parties = np.empty_like(baseline_parties)
        fold_coefficients: dict[str, float] = {}
        for index, party in enumerate(PARTIES):
            residual_train = (train[f"actual_{party}"] - train[f"baseline_{party}"]).to_numpy()
            model = Ridge(alpha=alpha)
            model.fit(x_train, residual_train)
            candidate_parties[:, index] = baseline_parties[:, index] + model.predict(x_test)
            for feature, coefficient in zip(FEATURES, model.coef_, strict=True):
                fold_coefficients[f"{party}:{feature}"] = float(coefficient)
        candidate_parties = np.vstack([project_simplex(row) for row in candidate_parties])
        coefficients[str(target_year)] = fold_coefficients

        named = slice(0, len(NAMED_PARTIES))
        vote_weights = test["valid_votes"].to_numpy()
        baseline_weighted, baseline_unweighted = _mae(
            actual_parties[:, named],
            baseline_parties[:, named],
            vote_weights,
        )
        candidate_weighted, candidate_unweighted = _mae(
            actual_parties[:, named],
            candidate_parties[:, named],
            vote_weights,
        )
        folds.append(
            FoldMetrics(
                target_year=target_year,
                outcome="named_party_share",
                baseline_weighted_mae=baseline_weighted,
                candidate_weighted_mae=candidate_weighted,
                relative_improvement=_relative_improvement(baseline_weighted, candidate_weighted),
                baseline_unweighted_mae=baseline_unweighted,
                candidate_unweighted_mae=candidate_unweighted,
                observations=test.height * len(NAMED_PARTIES),
                weight_sum=float(vote_weights.sum()),
            )
        )

        turnout_model = Ridge(alpha=alpha)
        turnout_model.fit(
            x_train,
            (train["actual_turnout"] - train["baseline_turnout"]).to_numpy(),
        )
        baseline_turnout = test["baseline_turnout"].to_numpy()
        candidate_turnout = np.clip(baseline_turnout + turnout_model.predict(x_test), 0.0, 1.0)
        actual_turnout = test["actual_turnout"].to_numpy()
        eligible_weights = test["eligible_voters"].to_numpy()
        baseline_weighted, baseline_unweighted = _mae(
            actual_turnout, baseline_turnout, eligible_weights
        )
        candidate_weighted, candidate_unweighted = _mae(
            actual_turnout, candidate_turnout, eligible_weights
        )
        folds.append(
            FoldMetrics(
                target_year=target_year,
                outcome="turnout",
                baseline_weighted_mae=baseline_weighted,
                candidate_weighted_mae=candidate_weighted,
                relative_improvement=_relative_improvement(baseline_weighted, candidate_weighted),
                baseline_unweighted_mae=baseline_unweighted,
                candidate_unweighted_mae=candidate_unweighted,
                observations=test.height,
                weight_sum=float(eligible_weights.sum()),
            )
        )

        prediction = test.select(
            "target_year",
            "municipality_id",
            "municipality_name",
            "valid_votes",
            "eligible_voters",
            *FEATURES,
        )
        prediction = prediction.with_columns(
            pl.Series("actual_turnout", actual_turnout),
            pl.Series("baseline_turnout", baseline_turnout),
            pl.Series("candidate_turnout", candidate_turnout),
            *(
                pl.Series(f"actual_{party}", actual_parties[:, index])
                for index, party in enumerate(PARTIES)
            ),
            *(
                pl.Series(f"baseline_{party}", baseline_parties[:, index])
                for index, party in enumerate(PARTIES)
            ),
            *(
                pl.Series(f"candidate_{party}", candidate_parties[:, index])
                for index, party in enumerate(PARTIES)
            ),
        )
        predictions.append(prediction)
    return folds, pl.concat(predictions), coefficients


def gate_result(
    folds: list[FoldMetrics],
    *,
    outcome: str,
    minimum_relative_improvement: float,
) -> dict[str, object]:
    selected = [fold for fold in folds if fold.outcome == outcome]
    baseline = sum(fold.baseline_weighted_mae * fold.weight_sum for fold in selected)
    candidate = sum(fold.candidate_weighted_mae * fold.weight_sum for fold in selected)
    weight_sum = sum(fold.weight_sum for fold in selected)
    pooled_baseline = baseline / weight_sum
    pooled_candidate = candidate / weight_sum
    pooled_improvement = _relative_improvement(pooled_baseline, pooled_candidate)
    every_fold = all(fold.candidate_weighted_mae < fold.baseline_weighted_mae for fold in selected)
    return {
        "passed": every_fold and pooled_improvement >= minimum_relative_improvement,
        "improved_every_fold": every_fold,
        "minimum_relative_improvement": minimum_relative_improvement,
        "pooled_baseline_weighted_mae": pooled_baseline,
        "pooled_candidate_weighted_mae": pooled_candidate,
        "pooled_relative_improvement": pooled_improvement,
    }


def verify_experimental_sources(root: Path) -> dict[int, Path]:
    manifest: dict[str, Any] = yaml.safe_load(
        (root / "config" / "sources_experimental.yaml").read_text(encoding="utf-8")
    )
    paths: dict[int, Path] = {}
    for source in manifest["sources"]:
        source_id = str(source["id"])
        if "advance_votes_received_" not in source_id or source["status"] != "verified_download":
            continue
        year = int(source_id.rsplit("_", 1)[1])
        path = root / str(source["raw_file"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"Checksum mismatch for {path}")
        paths[year] = path
    if set(paths) != set(ADVANCE_FILES):
        raise ValueError(f"Expected historical advance-vote files for {sorted(ADVANCE_FILES)}")
    return paths


def build_experiment_frame(root: Path, *, cutoff_days: int = 2) -> pl.DataFrame:
    paths = verify_experimental_sources(root)
    advances = {
        year: read_advance_vote_file(
            path,
            election_year=year,
            cutoff_days=cutoff_days,
        )
        for year, path in paths.items()
    }
    outcomes = {
        year: municipality_outcomes(
            pl.read_parquet(root / "data" / "processed" / f"election_results_{year}.parquet")
        )
        for year in paths
    }
    return pl.concat(
        [
            build_target_cycle(
                advances[previous],
                advances[current],
                outcomes[previous],
                outcomes[current],
            )
            for previous, current in ((2010, 2014), (2014, 2018), (2018, 2022))
        ],
        how="diagonal_relaxed",
    )


def write_advance_voting_report(summary: dict[str, Any], path: Path) -> Path:
    folds = summary["folds"]
    literal = summary["posthoc_literal_geographic_reweighting"]

    def pp(value: float) -> str:
        return f"{value * 100:.3f}".replace(".", ",")

    def relative(value: float) -> str:
        return f"{value * 100:+.1f} %".replace(".", ",")

    party_rows = [fold for fold in folds if fold["outcome"] == "named_party_share"]
    turnout_rows = [fold for fold in folds if fold["outcome"] == "turnout"]
    source_rows = "\n".join(
        f"| {row['year']} | {row['municipalities']} | {row['received_d2']:,} |".replace(",", " ")
        for row in summary["source_audit"]
    )
    party_table = "\n".join(
        f"| {row['target_year']} | {pp(row['baseline_weighted_mae'])} | "
        f"{pp(row['candidate_weighted_mae'])} | {relative(row['relative_improvement'])} |"
        for row in party_rows
    )
    turnout_table = "\n".join(
        f"| {row['target_year']} | {pp(row['baseline_weighted_mae'])} | "
        f"{pp(row['candidate_weighted_mae'])} | {relative(row['relative_improvement'])} |"
        for row in turnout_rows
    )
    literal_table = "\n".join(
        f"| {row['target_year']} | {pp(row['baseline_previous_national_mae'])} | "
        f"{pp(row['advance_weighted_previous_geography_mae'])} | "
        f"{relative(row['relative_improvement'])} |"
        for row in literal
    )
    party_gate = summary["gates"]["party_signal"]
    turnout_gate = summary["gates"]["turnout_signal"]
    party_worse = f"{abs(float(party_gate['pooled_relative_improvement'])) * 100:.1f}".replace(
        ".", ","
    )
    turnout_change = f"{float(turnout_gate['pooled_relative_improvement']) * 100:.1f}".replace(
        ".", ","
    )
    text = f"""# Förtidsröstning som prognossignal

## Slutsats

**Ingen av de två förregistrerade signalerna klarar gaten.** Offentlig statistik
över var förtidsröster tas emot ska därför inte påverka 2026-prognosen.

Partimodellen försämrar det röstantalsviktade felet i samtliga tre hållna val
och är {party_worse} procent
sämre totalt. Valdeltagandemodellen förbättrar 2014 och 2022, men blir tydligt
sämre 2018. Den lilla poolade förbättringen på
{turnout_change} procent är inte
stabil mellan val.

## Förregistrerad fråga och design

Designen låstes i git innan någon score räknades (`7a7869b`, med en ren
YAML-nestningsfix i `6a11a56`). Testet frågar om takten i mottagna
förtidsröster innehåller geografisk information efter att föregående
kommunresultat och målvalets nationella rörelse redan räknats bort.

- Officiella mottagningsdata för 2010, 2014, 2018 och 2022.
- 290 kommuner per målval; 870 kommun-val-observationer.
- Avskärning D−2, motsvarande fredagen före ett söndagsval.
- Sex förregistrerade egenskaper: nivå och förändring i mottagna röster per
  röstberättigad, andelen mottagen senast D−7 samt antal aktiva lokaler.
- Ridge-regression med fast `alpha=10`, tränad på två val och testad på det
  tredje.
- Baslinjen för partier är föregående kommunresultat med målvalets verkliga
  nationella proportionella svängning. Det är en stark oracle-baslinje som
  isolerar frågan om lokal signal.
- Gaten kräver förbättring i varje hållet val och minst 0,5 procent poolat.

## Källdata vid D−2

| Val | Kommuner | Mottagna röster |
|---:|---:|---:|
{source_rows}

Filerna är Valmyndighetens slutliga breda filer med en kolumn per mottagningsdag.
De gör det möjligt att återskapa antalet röster som mottogs före D−2, men inte
exakt den filversion en analytiker hade D−2: sena registreringar kan ha fyllts
bakåt. Detta gör testet något mer välvilligt mot signalen än en verklig
realtidskörning.

## Resultat: partier

MAE i procentenheter, åtta namngivna partier, viktat med giltiga röster.
Positiv förändring betyder att kandidaten förbättrar baslinjen.

| Hållet val | Baslinje | Med förtidsröster | Relativ förbättring |
|---:|---:|---:|---:|
{party_table}

Poolat: baslinje {pp(float(party_gate["pooled_baseline_weighted_mae"]))} pp,
kandidat {pp(float(party_gate["pooled_candidate_weighted_mae"]))} pp,
{relative(float(party_gate["pooled_relative_improvement"]))}. **Gate: FAIL.**

## Resultat: valdeltagande

MAE i procentenheter, viktat med röstberättigade.

| Hållet val | Baslinje | Med förtidsröster | Relativ förbättring |
|---:|---:|---:|---:|
{turnout_table}

Poolat: baslinje {pp(float(turnout_gate["pooled_baseline_weighted_mae"]))} pp,
kandidat {pp(float(turnout_gate["pooled_candidate_weighted_mae"]))} pp,
{relative(float(turnout_gate["pooled_relative_improvement"]))}. **Gate: FAIL.**

## Bokstavlig kontroll av idén

Som en efterhandsdiagnostik, utan möjlighet att ändra gaten, viktades föregående
valårs kommunresultat med var målvalets förtidsröster hade tagits emot. Det
jämfördes med att lämna föregående nationella resultat oförändrat.

| Målval | Föregående resultat | Förtidsvägd geografi | Relativ förbättring |
|---:|---:|---:|---:|
{literal_table}

Effekten är mycket liten och byter tecken: marginellt bättre 2014 och 2022,
marginellt sämre 2018. Det finns därför inget stabilt stöd för att
röstningslokalernas omgivande historik beskriver vilka som har röstat där.

## Varför signalen sannolikt faller

Den offentliga filen mäter **mottagningsplats**, inte väljarens hemadress.
Stationer, köpcentrum, sjukhus och arbetsplatser tar emot väljare från andra
kommuner och valdistrikt. Sambandet mellan lokalens omgivning och väljarnas
politiska sammansättning blir därför både svagt och olika från val till val.

Dessutom berättar antalet mottagna kuvert inget om parti. När modellen försöker
översätta en aktivitetsnivå till partiförändring lär den sig huvudsakligen
tillfälliga samband från två val, vilka inte håller i det tredje.

## 2026

Ingen 2026-känslighetsprognos produceras:

1. både parti- och valdeltagandegaten faller, och
2. den nuvarande officiella 2026-filen hämtades efter datastoppet
   11 september 22:29 CEST och kan innehålla efterregistreringar.

En bättre fortsättning kräver anonymiserade antal per **hemvaldistrikt** från
kommunernas Valid-export, helst även historiska publiceringsvintages. Det skulle
testa faktisk lokal förtidsröstning i stället för trafiken genom en
röstningslokal.
"""
    path.write_text(text, encoding="utf-8")
    return path


def run_advance_voting_backtest(root: Path) -> dict[str, Any]:
    config_path = root / "config" / "advance_voting.yaml"
    config_bytes = config_path.read_bytes()
    config: dict[str, Any] = yaml.safe_load(config_bytes)
    frame = build_experiment_frame(
        root,
        cutoff_days=int(config["cutoff"]["days_before_election"]),
    )
    source_audit = []
    for year, source_path in sorted(verify_experimental_sources(root).items()):
        source = read_advance_vote_file(
            source_path,
            election_year=year,
            cutoff_days=int(config["cutoff"]["days_before_election"]),
        )
        source_audit.append(
            {
                "year": year,
                "municipalities": source.height,
                "received_d2": int(source["received_d2"].sum()),
            }
        )
    folds, predictions, coefficients = leave_one_election_out(
        frame,
        alpha=float(config["models"]["ridge_alpha"]),
    )
    party_gate = gate_result(
        folds,
        outcome="named_party_share",
        minimum_relative_improvement=float(
            config["gate"]["party_signal"]["minimum_pooled_relative_mae_improvement"]
        ),
    )
    turnout_gate = gate_result(
        folds,
        outcome="turnout",
        minimum_relative_improvement=float(
            config["gate"]["turnout_signal"]["minimum_pooled_relative_mae_improvement"]
        ),
    )
    output = root / str(config["outputs"]["directory"])
    output.mkdir(parents=True, exist_ok=True)
    predictions.write_parquet(output / str(config["outputs"]["municipality_predictions"]))
    pl.DataFrame([asdict(fold) for fold in folds]).write_csv(
        output / str(config["outputs"]["fold_metrics"])
    )
    summary: dict[str, Any] = {
        "role": config["role"],
        "registered_before_backtest": config["registered_before_backtest"],
        "design_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "cutoff_days_before_election": config["cutoff"]["days_before_election"],
        "geography": config["geography"]["unit"],
        "n_municipality_cycles": frame.height,
        "target_years": sorted(int(year) for year in frame["target_year"].unique()),
        "source_audit": source_audit,
        "features": list(FEATURES),
        "ridge_alpha": config["models"]["ridge_alpha"],
        "folds": [asdict(fold) for fold in folds],
        "gates": {"party_signal": party_gate, "turnout_signal": turnout_gate},
        "posthoc_literal_geographic_reweighting": literal_geographic_reweighting(frame),
        "coefficients": coefficients,
        "forecast_2026": {
            "produced": False,
            "reason": (
                "No complete official 2026 advance-vote file vintage retrieved by "
                "the 2026-09-11 22:29 CEST production cutoff is preserved."
            ),
        },
    }
    (output / str(config["outputs"]["summary"])).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_advance_voting_report(
        summary,
        output / str(config["outputs"]["report"]),
    )
    return summary
