from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS
from valforecast.polls.region_condition import (
    build_regional_point_margins,
    infer_region_effective_sample_sizes,
    reconcile_regional_draws,
    reconcile_regional_margins,
    sample_regional_margin_draws,
)
from valforecast.polls.transition_posterior import approximate_cell_n_eff


def _cells(*, suppress_other: bool = False) -> pl.DataFrame:
    rows = []
    for region_id in (*REGION_IDS, "Z01"):
        for party in PARTIES:
            suppressed = suppress_other and party == "OTHER" and region_id == "SE09"
            estimate = None if suppressed else 1.0 / len(PARTIES)
            margin = None if suppressed else 0.02
            rows.append(
                {
                    "wave_id": "test",
                    "region_id": region_id,
                    "party": party,
                    "estimate": estimate,
                    "margin_error": margin,
                    "row_base": None,
                    "cell_status": "SUPPRESSED" if suppressed else "PUBLISHED",
                }
            )
    return pl.DataFrame(rows)


def test_suppressed_residual_is_allocated_flat_without_national_shrinkage() -> None:
    cells = _cells(suppress_other=True)
    point = build_regional_point_margins(cells)
    published_mass = float(
        cells.filter((pl.col("region_id") == "SE09") & (pl.col("party") != "OTHER"))[
            "estimate"
        ].sum()
    )
    residual = 1.0 - published_mass
    assert point.margins[REGION_IDS.index("SE09"), PARTIES.index("OTHER")] == pytest.approx(
        residual
    )
    assert point.margins[REGION_IDS.index("SE2"), PARTIES.index("S")] == pytest.approx(
        1.0 / len(PARTIES)
    )
    assert np.allclose(point.margins.sum(axis=1), 1.0)


def test_zero_renormalize_drops_suppressed_mass() -> None:
    point = build_regional_point_margins(
        _cells(suppress_other=True),
        suppression="zero_renormalize",
    )
    smaland = point.margins[REGION_IDS.index("SE09")]
    assert smaland[PARTIES.index("OTHER")] == pytest.approx(0.0)
    assert smaland.sum() == pytest.approx(1.0)


def test_region_ess_uses_median_and_does_not_cap_on_missing_base() -> None:
    cells = _cells()
    n_eff = infer_region_effective_sample_sizes(cells)
    expected = approximate_cell_n_eff(1.0 / 9.0, 0.02)
    assert n_eff.values[0] == pytest.approx(expected)
    assert not n_eff.is_kish_ess
    assert n_eff.rows["base_capped"].to_list() == [False] * len(REGION_IDS)
    assert n_eff.rows["clip_applied"].to_list() == [False] * len(REGION_IDS)
    assert (n_eff.values > 900).all()


def test_reconciliation_hits_national_target_and_keeps_simplex() -> None:
    point = build_regional_point_margins(_cells())
    weights = np.full(len(REGION_IDS), 1.0 / len(REGION_IDS))
    target = np.array([0.12, 0.28, 0.05, 0.08, 0.06, 0.18, 0.05, 0.15, 0.03])
    target = target / target.sum()
    reconciled = reconcile_regional_margins(point.margins, weights, target)
    assert np.allclose(reconciled.sum(axis=1), 1.0)
    assert np.allclose(weights @ reconciled, target, atol=1e-10)


def test_draws_are_deterministic_and_reconcile_draw_wise() -> None:
    point = build_regional_point_margins(_cells(suppress_other=True))
    n_eff = infer_region_effective_sample_sizes(_cells(suppress_other=True))
    first = sample_regional_margin_draws(point, n_eff, n_draws=8, seed=20260911)
    second = sample_regional_margin_draws(point, n_eff, n_draws=8, seed=20260911)
    assert np.array_equal(first.draws, second.draws)
    weights = np.linspace(0.2, 0.05, len(REGION_IDS))
    weights = weights / weights.sum()
    target = np.full(len(PARTIES), 1.0 / len(PARTIES))
    reconciled = reconcile_regional_draws(first, weights, target)
    assert reconciled.row_simplex_ok
    assert reconciled.national_reconciliation_ok
    assert reconciled.draws is not None
    assert np.allclose(reconciled.draws.sum(axis=2), 1.0)
