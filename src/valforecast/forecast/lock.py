from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from valforecast.forecast.aggregator import aggregate_polls
from valforecast.forecast.contract import ForecastContract, load_forecast_contract
from valforecast.forecast.polls import load_forecast_polls, parser_raw_files
from valforecast.forecast.snapshot import source_manifest_hash
from valforecast.ingest.fetch import sha256_file

LOCKED_CORE_FILES = {
    "scb_psu_transition_2026M05": "data/raw/scb/psu/transition_2026M05.json",
    "scb_psu_national_2026M05": "data/raw/scb/psu/national_poll_2026M05.json",
    "val2026_district_mapping_from_2022": (
        "data/raw/valmyndigheten/2026/valdistrikt_jamforelser_2022_2026.xlsx"
    ),
    "val2026_riksdag_eligible_voters": (
        "data/raw/valmyndigheten/2026/rostberattigade_riksdag_alder_kon_2026-08-14.xlsx"
    ),
}


def locked_raw_files(root: Path) -> dict[str, str]:
    files = dict(LOCKED_CORE_FILES)
    for source_id, relative in parser_raw_files(root).items():
        files[source_id] = relative
    return files


def _poll_checksums(polls: list[Any]) -> dict[str, str]:
    return {poll.poll_id: poll.sha256 for poll in polls}


def build_input_lock(root: Path, contract: ForecastContract | None = None) -> dict[str, object]:
    contract = contract or load_forecast_contract(root / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(root, contract)
    aggregated = aggregate_polls(polls, contract)
    checksums = {
        source_id: sha256_file(root / relative)
        for source_id, relative in locked_raw_files(root).items()
    }
    all_polls = [*aggregated.included, *aggregated.excluded]
    return {
        "schema_version": 1,
        "stage": "pre_snapshot_input_lock",
        "model_version": contract.model_version,
        "contract_sha256": hashlib.sha256(
            (root / "config" / "forecast_2026.yaml").read_bytes()
        ).hexdigest(),
        "source_manifest_hash": source_manifest_hash(root),
        "random_seed": contract.random_seed,
        "n_draws": contract.draws,
        "included_poll_ids": [poll.poll_id for poll in aggregated.included],
        "strict_sensitivity_poll_ids": list(contract.strict_hosted_poll_ids),
        "excluded_poll_ids": [
            {"poll_id": poll.poll_id, "reason": poll.exclusion_reason}
            for poll in aggregated.excluded
        ],
        "polls": [poll.as_dict() for poll in all_polls],
        "poll_sha256": _poll_checksums(all_polls),
        "raw_sha256": checksums,
        "forbidden_inputs_confirmed_absent": [
            "election_results_2026",
            "exit_polls",
            "valu",
            "regional_4c_conditioning",
            "milestone_3_local_structure",
        ],
    }


def write_input_lock(root: Path) -> Path:
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    document = build_input_lock(root, contract)
    path = root / contract.input_lock
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_input_lock(root: Path) -> dict[str, Any]:
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    path = root / contract.input_lock
    if not path.exists():
        raise ValueError("Forecast input lock is missing; run lock-forecast-2026-inputs")
    document = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    for poll in document.get("polls") or []:
        raw = Path(str(poll["raw_file"]))
        raw_path = raw if raw.is_absolute() else root / raw
        if raw_path.exists() and sha256_file(raw_path) != poll.get("sha256"):
            raise ValueError("Forecast input lock is stale: poll_sha256")
    current = build_input_lock(root, contract)
    comparable_keys = (
        "contract_sha256",
        "source_manifest_hash",
        "included_poll_ids",
        "strict_sensitivity_poll_ids",
        "poll_sha256",
        "raw_sha256",
    )
    for key in comparable_keys:
        if document.get(key) != current.get(key):
            raise ValueError(f"Forecast input lock is stale: {key}")
    return document
