from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES
from valforecast.forecast.produce import ForecastResult, geography_aggregates


def source_manifest_hash(root: Path) -> str:
    return hashlib.sha256((root / "config" / "sources.yaml").read_bytes()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _party_records(point: np.ndarray, draws: np.ndarray, level: float) -> dict[str, Any]:
    tail = (1.0 - level) / 2.0
    low = np.quantile(draws, tail, axis=0)
    high = np.quantile(draws, 1.0 - tail, axis=0)
    return {
        party: {
            "point": round(float(point[index]), 6),
            "low": round(float(low[index]), 6),
            "high": round(float(high[index]), 6),
        }
        for index, party in enumerate(PARTIES)
    }


def build_snapshot_document(
    root: Path,
    result: ForecastResult,
    *,
    forecast_timestamp: datetime | None = None,
) -> dict[str, Any]:
    timestamp = forecast_timestamp or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    districts = result.universe.districts
    district_rows = []
    for index, record in enumerate(districts.iter_rows(named=True)):
        district_rows.append(
            {
                "district_id": record["district_id"],
                "municipality_id": record["municipality_id"],
                "county_id": record["county_id"],
                "constituency_id": record["constituency_id"],
                "eligible_voters": int(record["eligible_voters"]),
                "relation": record["relation"],
                "mapping_rule": record["mapping_rule"],
                "parties": {
                    party: {
                        "point": round(float(result.district_point[index, party_index]), 6),
                        "low": round(float(result.district_low[index, party_index]), 6),
                        "high": round(float(result.district_high[index, party_index]), 6),
                    }
                    for party_index, party in enumerate(PARTIES)
                },
            }
        )
    aggregates = {
        "municipality": geography_aggregates(result, "municipality_id").to_dicts(),
        "county": geography_aggregates(result, "county_id").to_dicts(),
        "constituency": geography_aggregates(result, "constituency_id").to_dicts(),
    }
    return {
        "forecast_timestamp": timestamp.isoformat(),
        "data_cutoff": result.contract.cutoff.isoformat(),
        "git_commit": git_commit(root),
        "source_manifest_hash": source_manifest_hash(root),
        "contract_sha256": file_sha256(root / "config" / "forecast_2026.yaml"),
        "input_lock_sha256": file_sha256(root / result.contract.input_lock),
        "model_version": result.contract.model_version,
        "random_seed": result.contract.random_seed,
        "n_draws": result.contract.draws,
        "weight": "eligible_voters_2026_qualification_day",
        "uncertainty": {
            "method": "pollster_bootstrap_then_dirichlet",
            "confidence_level": result.contract.confidence_level,
            "interpretation": "pollster_heterogeneity_approximation_not_election_result_interval",
        },
        "seats": None,
        "included_polls": [poll.poll_id for poll in result.aggregated.included],
        "strict_sensitivity_polls": [
            poll.poll_id for poll in result.sensitivity_aggregated.included
        ],
        "prediction": {
            "national": _party_records(
                result.national_point,
                result.national_draws,
                result.contract.confidence_level,
            ),
            "poll_target": {
                party: round(float(result.poll_target_point[index]), 6)
                for index, party in enumerate(PARTIES)
            },
            "strict_sensitivity": {
                "national": _party_records(
                    result.sensitivity_national_point,
                    result.sensitivity_national_draws,
                    result.contract.confidence_level,
                ),
                "poll_target": {
                    party: round(float(result.sensitivity_poll_target_point[index]), 6)
                    for index, party in enumerate(PARTIES)
                },
            },
            "districts": district_rows,
            "aggregates": aggregates,
        },
        "mapping_diagnostics": result.universe.mapping_diagnostics.to_dicts(),
    }


def snapshot_path(root: Path, document: dict[str, Any]) -> Path:
    cutoff = document["data_cutoff"].replace(":", "").replace("+", "p")
    stamp = document["forecast_timestamp"].replace(":", "").replace("+", "p")
    name = f"forecast_2026_{cutoff}_{stamp}.json"
    return root / "forecast_snapshots" / name


OFFICIAL_SNAPSHOT_NAME = "official_forecast_2026.json"


def official_snapshot_path(root: Path) -> Path:
    return root / "forecast_snapshots" / OFFICIAL_SNAPSHOT_NAME


def write_snapshot(root: Path, document: dict[str, Any], *, official: bool = False) -> Path:
    official_path = official_snapshot_path(root)
    path = official_path if official else snapshot_path(root, document)
    path.parent.mkdir(parents=True, exist_ok=True)
    if official_path.exists():
        raise ValueError("Official 2026 snapshot already exists and must not be overwritten")
    if path.exists():
        raise ValueError(f"Refusing to overwrite existing snapshot: {path}")
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def required_snapshot_fields() -> frozenset[str]:
    return frozenset(
        {
            "forecast_timestamp",
            "data_cutoff",
            "git_commit",
            "source_manifest_hash",
            "contract_sha256",
            "input_lock_sha256",
            "model_version",
            "random_seed",
            "uncertainty",
            "prediction",
        }
    )
