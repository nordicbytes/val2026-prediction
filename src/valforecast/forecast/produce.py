from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.forecast.aggregator import AggregatedPolls, aggregate_polls, draw_poll_targets
from valforecast.forecast.contract import ForecastContract, load_forecast_contract
from valforecast.forecast.kernel import estimate_2026_kernel, rake_kernel_to_targets
from valforecast.forecast.polls import load_forecast_polls
from valforecast.forecast.universe import ForecastUniverse, build_forecast_universe
from valforecast.polls.transition_calibrate import calibrate_transition_matrix
from valforecast.polls.transition_posterior import RakedTransitionDraws


@dataclass(frozen=True)
class ForecastResult:
    contract: ForecastContract
    aggregated: AggregatedPolls
    sensitivity_aggregated: AggregatedPolls
    universe: ForecastUniverse
    national_point: np.ndarray
    national_draws: np.ndarray
    district_point: np.ndarray
    district_low: np.ndarray
    district_high: np.ndarray
    poll_target_point: np.ndarray
    sensitivity_national_point: np.ndarray
    sensitivity_national_draws: np.ndarray
    sensitivity_poll_target_point: np.ndarray


def _quantile_interval(draws: np.ndarray, level: float) -> tuple[np.ndarray, np.ndarray]:
    tail = (1.0 - level) / 2.0
    return (
        np.quantile(draws, tail, axis=0),
        np.quantile(draws, 1.0 - tail, axis=0),
    )


def apply_and_reconcile(
    previous: np.ndarray,
    weights: np.ndarray,
    matrices: np.ndarray,
    targets: np.ndarray,
    *,
    tolerance: float = 1e-10,
) -> np.ndarray:
    n_draws = matrices.shape[0]
    n_districts = previous.shape[0]
    reconciled = np.empty((n_draws, n_districts, len(PARTIES)), dtype=np.float64)
    weight_share = weights / weights.sum()
    for draw_index in range(n_draws):
        predicted = previous @ matrices[draw_index]
        predicted = predicted / predicted.sum(axis=1, keepdims=True)
        result = calibrate_transition_matrix(
            predicted,
            weight_share,
            targets[draw_index],
            tolerance=tolerance,
        )
        if not result.converged:
            raise ValueError(f"District reconciliation failed for draw {draw_index}")
        reconciled[draw_index] = result.matrix
    return reconciled


def summarize_forecast(
    reconciled: np.ndarray,
    weights: np.ndarray,
    *,
    confidence_level: float,
    point_target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    weight_share = weights / weights.sum()
    national_draws = np.einsum("d,ndp->np", weight_share, reconciled)
    raw_district_point = reconciled.mean(axis=0)
    point_calibration = calibrate_transition_matrix(
        raw_district_point,
        weight_share,
        point_target,
        tolerance=1e-12,
    )
    if not point_calibration.converged:
        raise ValueError("District point reconciliation failed")
    district_point = point_calibration.matrix
    national_point = weight_share @ district_point
    district_low, district_high = _quantile_interval(reconciled, confidence_level)
    return national_point, national_draws, district_point, district_low, district_high


def _national_from_polls(
    universe: ForecastUniverse,
    kernel: RakedTransitionDraws,
    aggregated: AggregatedPolls,
    contract: ForecastContract,
    *,
    seed_offset: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    poll_draws = draw_poll_targets(
        aggregated,
        n_draws=contract.draws,
        seed=contract.random_seed + seed_offset,
        bootstrap=contract.pollster_bootstrap,
    )
    election_day = rake_kernel_to_targets(
        kernel,
        universe.national_previous,
        poll_draws,
    )
    reconciled = apply_and_reconcile(
        universe.previous_matrix,
        universe.eligible_weights,
        election_day,
        poll_draws,
    )
    return summarize_forecast(
        reconciled,
        universe.eligible_weights,
        confidence_level=contract.confidence_level,
        point_target=aggregated.point,
    )


def run_forecast_2026(root: Path, contract: ForecastContract | None = None) -> ForecastResult:
    contract = contract or load_forecast_contract(root / "config" / "forecast_2026.yaml")
    polls = load_forecast_polls(root, contract)
    aggregated = aggregate_polls(polls, contract, variant="production")
    sensitivity = aggregate_polls(polls, contract, variant="strict_hosted")
    universe = build_forecast_universe(root)
    kernel = estimate_2026_kernel(
        root,
        universe.national_previous,
        n_draws=contract.draws,
        seed=contract.random_seed,
    )
    summarized = _national_from_polls(
        universe,
        kernel,
        aggregated,
        contract,
        seed_offset=17,
    )
    sensitivity_summarized = _national_from_polls(
        universe,
        kernel,
        sensitivity,
        contract,
        seed_offset=17,
    )
    national_point, national_draws, district_point, district_low, district_high = summarized
    sensitivity_point, sensitivity_draws, _, _, _ = sensitivity_summarized
    return ForecastResult(
        contract=contract,
        aggregated=aggregated,
        sensitivity_aggregated=sensitivity,
        universe=universe,
        national_point=national_point,
        national_draws=national_draws,
        district_point=district_point,
        district_low=district_low,
        district_high=district_high,
        poll_target_point=aggregated.point,
        sensitivity_national_point=sensitivity_point,
        sensitivity_national_draws=sensitivity_draws,
        sensitivity_poll_target_point=sensitivity.point,
    )


def geography_aggregates(
    result: ForecastResult,
    group_column: str,
) -> pl.DataFrame:
    districts = result.universe.districts.with_row_index("row_index")
    rows: list[dict[str, object]] = []
    for group, members in districts.group_by(group_column):
        indices = members["row_index"].to_list()
        weights = result.universe.eligible_weights[indices]
        weight_share = weights / weights.sum()
        point = weight_share @ result.district_point[indices]
        for party_index, party in enumerate(PARTIES):
            rows.append(
                {
                    "geography": group_column,
                    "unit_id": group[0] if isinstance(group, tuple) else group,
                    "party": party,
                    "point": float(point[party_index]),
                    "eligible_voters": int(weights.sum()),
                }
            )
    return pl.DataFrame(rows).sort("unit_id", "party")
