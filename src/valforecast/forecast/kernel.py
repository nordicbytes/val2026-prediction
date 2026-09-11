from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_ingest import (
    SCB_PARTY_CODES,
    extract_scb_national_poll,
    extract_scb_transition_cells,
    read_pxweb_jsonstat,
)
from valforecast.polls.transition_posterior import (
    RakedTransitionDraws,
    estimate_survey_transition,
)

SCB_2026_GOLD = {
    ("s", "s", "000001IX"): 75.8,
    ("m", "m", "000001IX"): 62.0,
    ("SD", "SD", "000001IX"): 74.9,
    ("hela väljarkåren", "s", "000001IX"): 26.8,
    ("hela väljarkåren", "blankt", "000001IX"): 7.8,
    ("hela väljarkåren", "vet ej", "000001IX"): 12.7,
}
VID10_2026_GOLD = {"S": 0.339, "L": 0.025, "OTHER": 0.020}


def validate_scb_2026_wave(frame: pl.DataFrame) -> None:
    expected_previous = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "ej röstat",
        "ej röstberättigad",
        "uppgift saknas",
        "hela väljarkåren",
    }
    expected_current = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "blankt",
        "vet ej",
    }
    expected_contents = {"000001IX", "000001IW", "000001IV"}
    if set(frame["Tid"].unique()) != {"2026M05"}:
        raise ValueError("SCB 2026 source must contain only 2026M05")
    if set(frame["PvalRV"].unique()) != expected_previous:
        raise ValueError("SCB 2026 previous-vote codes changed")
    if set(frame["Pvalnu"].unique()) != expected_current:
        raise ValueError("SCB 2026 current-vote codes changed")
    if set(frame["ContentsCode"].unique()) != expected_contents:
        raise ValueError("SCB 2026 content codes changed")
    bases = (
        frame.filter(pl.col("ContentsCode") == "000001IV")
        .group_by("PvalRV")
        .agg(pl.col("value").drop_nulls().first().alias("row_base"))
    )
    by_group = dict(bases.select("PvalRV", "row_base").iter_rows())
    partition = expected_previous - {"hela väljarkåren"}
    if int(sum(float(by_group[group]) for group in partition)) != 4542:
        raise ValueError("SCB 2026 previous-vote bases do not partition 4,542 respondents")
    if int(float(by_group["hela väljarkåren"])) != 4542:
        raise ValueError("SCB 2026 whole-electorate base is not 4,542")
    for (previous, current, contents), expected in SCB_2026_GOLD.items():
        value = frame.filter(
            (pl.col("PvalRV") == previous)
            & (pl.col("Pvalnu") == current)
            & (pl.col("ContentsCode") == contents)
        ).item(0, "value")
        if value != expected:
            raise ValueError(f"SCB 2026 gold cell changed: {previous}/{current}")


def extract_scb_2026_national_poll(frame: pl.DataFrame) -> np.ndarray:
    try:
        parsed = extract_scb_national_poll(frame, wave_id="scb_2026M05", time_value="2026M05")
        by_party = dict(parsed.select("party", "poll_share").iter_rows())
    except ValueError as error:
        if "sum to" not in str(error):
            raise
        selected = frame.filter(
            (pl.col("Tid") == "2026M05") & (pl.col("ContentsCode") == "ME0201B1")
        )
        by_party = {
            SCB_PARTY_CODES[str(record["Parti"])]: float(record["value"]) / 100.0
            for record in selected.iter_rows(named=True)
        }
        residual = 1.0 - sum(by_party.values())
        if abs(residual) > 0.005:
            raise
        by_party["OTHER"] = by_party.get("OTHER", 0.0) + residual
    vector = np.array([float(by_party[party]) for party in PARTIES], dtype=float)
    vector = vector / vector.sum()
    for party, expected in VID10_2026_GOLD.items():
        if abs(vector[PARTIES.index(party)] - expected) > 0.003:
            raise ValueError(f"Vid10 2026 gold cell changed: {party}")
    return np.asarray(vector, dtype=float)


def load_2026_transition_cells(root: Path) -> pl.DataFrame:
    frame = read_pxweb_jsonstat(root / "data/raw/scb/psu/transition_2026M05.json")
    validate_scb_2026_wave(frame)
    return extract_scb_transition_cells(frame, wave_id="scb_2026M05", time_value="2026M05")


def load_2026_vid10(root: Path) -> np.ndarray:
    frame = read_pxweb_jsonstat(root / "data/raw/scb/psu/national_poll_2026M05.json")
    return extract_scb_2026_national_poll(frame)


def estimate_2026_kernel(
    root: Path,
    previous_national: np.ndarray,
    *,
    n_draws: int,
    seed: int,
) -> RakedTransitionDraws:
    cells = load_2026_transition_cells(root)
    vid10 = load_2026_vid10(root)
    estimate = estimate_survey_transition(
        cells,
        previous_national=previous_national,
        target_national=vid10,
        n_draws=n_draws,
        seed=seed,
        estimator="T1_no_point_shrinkage_raked",
    )
    if estimate.raked is None:
        raise ValueError("2026 kernel was not raked to same-wave Vid10")
    return estimate.raked


def rake_kernel_to_targets(
    kernel: RakedTransitionDraws,
    previous_national: np.ndarray,
    targets: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> np.ndarray:
    if targets.shape[0] != kernel.n_draws:
        raise ValueError("Poll-target draws must match transition draws")
    raked = np.empty_like(kernel.raked_draws)
    for draw_index, (matrix, target) in enumerate(
        zip(kernel.raked_draws, targets, strict=True)
    ):
        result = calibrate_transition_matrix(
            matrix,
            previous_national,
            target,
            tolerance=tolerance,
        )
        if not result.converged:
            raise ValueError(f"Election-day raking failed for draw {draw_index}")
        raked[draw_index] = result.matrix
    return raked


