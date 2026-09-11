from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import yaml

CONTRACT_RELATIVE = Path("config") / "forecast_2026.yaml"
FORBIDDEN_INPUT_MARKERS = (
    "election_results_2026",
    "exit_polls",
    "valu",
    "post_cutoff_material",
    "regional_4c_conditioning",
    "milestone_3_local_structure",
    "post_hoc_adjustment",
)


@dataclass(frozen=True)
class ForecastContract:
    schema_version: int
    model_version: str
    cutoff: datetime
    timezone: str
    election_date: date
    primary_model: str
    draws: int
    random_seed: int
    confidence_level: float
    half_life_days: float
    earliest_fieldwork_end: date
    rounding_tolerance: float
    missing_sample_size_fallback: int
    pollster_balance: str
    derived_other_named_sum_min: float
    derived_other_named_sum_max: float
    strict_hosted_poll_ids: tuple[str, ...]
    pollster_bootstrap: bool
    input_lock: Path
    raw: dict[str, Any]

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()


def load_forecast_contract(path: Path) -> ForecastContract:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Forecast contract must be a mapping with schema_version: 1")
    cutoff_raw = document["cutoff"]
    model = document["model"]
    polls = document["poll_aggregation"]
    uncertainty = document["uncertainty"]
    if model["primary"] != "T1_no_point_shrinkage_raked":
        raise ValueError("Production contract must lock the 4B primary estimator")
    if model.get("regional_4c") != "forbidden":
        raise ValueError("Production contract must forbid 4C regional conditioning")
    if model.get("milestone_3_local_structure") != "forbidden":
        raise ValueError("Production contract must forbid milestone 3 structure")
    if polls.get("house_effects") != "none":
        raise ValueError("House effects are not locked for 2026")
    pollster_balance = str(polls.get("pollster_balance", ""))
    if pollster_balance != "latest_fieldwork_end_per_pollster":
        raise ValueError("Pollster balance must be latest_fieldwork_end_per_pollster")
    residual = polls["other_and_residual"]
    derived = residual.get("derived_residual_other") or {}
    if derived.get("allow") is not True:
        raise ValueError("Contract must allow documented DERIVED_RESIDUAL for OTHER")
    strict_ids = polls.get("sensitivity", {}).get("strict_primary_hosted")
    if not isinstance(strict_ids, list) or not strict_ids:
        raise ValueError("Contract must lock the strict-hosted sensitivity set")
    if uncertainty.get("poll_uncertainty") != "pollster_bootstrap_then_dirichlet":
        raise ValueError("Contract must lock pollster bootstrap uncertainty")
    if uncertainty["draws"] < 2000:
        raise ValueError("Contract must lock at least 2000 draws")
    timezone = str(cutoff_raw["timezone"])
    cutoff = datetime.fromisoformat(str(cutoff_raw["datetime"]))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=ZoneInfo(timezone))
    forbidden = set(document.get("forbidden_inputs") or [])
    missing_forbidden = set(FORBIDDEN_INPUT_MARKERS) - forbidden
    if missing_forbidden:
        raise ValueError(f"Contract is missing forbidden markers: {sorted(missing_forbidden)}")
    return ForecastContract(
        schema_version=int(document["schema_version"]),
        model_version=str(document["model_version"]),
        cutoff=cutoff,
        timezone=timezone,
        election_date=date.fromisoformat(str(cutoff_raw["election_date"])),
        primary_model=str(model["primary"]),
        draws=int(uncertainty["draws"]),
        random_seed=int(uncertainty["random_seed"]),
        confidence_level=float(uncertainty["confidence_level"]),
        half_life_days=float(polls["half_life_days"]),
        earliest_fieldwork_end=date.fromisoformat(str(polls["inclusion"]["earliest_fieldwork_end"])),
        rounding_tolerance=float(residual["rounding_residual_to_other_if_abs_at_most"]),
        missing_sample_size_fallback=int(polls["missing_sample_size_fallback"]),
        pollster_balance=pollster_balance,
        derived_other_named_sum_min=float(derived["named_sum_min"]),
        derived_other_named_sum_max=float(derived["named_sum_max"]),
        strict_hosted_poll_ids=tuple(str(item) for item in strict_ids),
        pollster_bootstrap=True,
        input_lock=Path(str(document["lock"]["input_lock"])),
        raw=cast(dict[str, Any], document),
    )
