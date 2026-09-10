from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import POLL_CURRENT_CATEGORIES
from valforecast.polls.transition_posterior import (
    N_EFF_LABEL,
    approximate_cell_n_eff,
    build_survey_point_estimate,
    estimate_survey_transition,
    infer_row_effective_sample_sizes,
    rake_transition_draws,
    sample_transition_draws,
)


def _cell(
    previous_party: str,
    current_party: str,
    estimate: float | None,
    *,
    wave_id: str = "2016M05",
    margin_error: float | None = 0.04,
    row_base: int = 400,
    cell_status: str | None = None,
) -> dict[str, object]:
    suppressed = estimate is None
    return {
        "wave_id": wave_id,
        "previous_party": previous_party,
        "current_party": current_party,
        "estimate": estimate,
        "margin_error": None if suppressed else margin_error,
        "row_base": row_base,
        "cell_status": cell_status or ("SUPPRESSED" if suppressed else "PUBLISHED"),
    }


def _loyalty_estimates(previous_party: str, *, residual: float = 0.0) -> dict[str, float | None]:
    party_mass = 0.84 - residual
    estimates: dict[str, float | None] = {
        party: (0.60 if party == previous_party else 0.03) for party in PARTIES
    }
    estimates[previous_party] = 0.60
    off_mass = 0.03 * (len(PARTIES) - 1)
    estimates[previous_party] = party_mass - off_mass
    estimates["BLANK"] = 0.06
    estimates["DONT_KNOW"] = 0.08
    estimates["MISSING"] = 0.02
    return estimates


def _complete_wave(
    *,
    wave_id: str = "2016M05",
    row_base: int = 400,
    margin_error: float = 0.04,
    suppressed: dict[str, tuple[str, ...]] | None = None,
    estimates_by_previous: dict[str, dict[str, float | None]] | None = None,
    missing_margins: tuple[tuple[str, str], ...] = (),
    zero_cells: tuple[tuple[str, str], ...] = (),
) -> pl.DataFrame:
    suppressed = suppressed or {}
    rows: list[dict[str, object]] = []
    for previous_party in PARTIES:
        estimates = (
            estimates_by_previous[previous_party]
            if estimates_by_previous is not None
            else _loyalty_estimates(previous_party)
        )
        hidden = set(suppressed.get(previous_party, ()))
        for current_party in POLL_CURRENT_CATEGORIES:
            estimate = None if current_party in hidden else estimates.get(current_party, 0.0)
            margin = margin_error
            if (previous_party, current_party) in missing_margins:
                margin = None  # type: ignore[assignment]
            if (previous_party, current_party) in zero_cells:
                estimate = 0.0
                margin = 0.0
            rows.append(
                _cell(
                    previous_party,
                    current_party,
                    estimate,
                    wave_id=wave_id,
                    margin_error=margin,
                    row_base=row_base,
                )
            )
    return pl.DataFrame(rows)


def test_cell_n_eff_gold_matches_locked_formula() -> None:
    n_eff = approximate_cell_n_eff(0.5, 0.049)
    assert n_eff == pytest.approx(0.5 * 0.5 / (0.049 / 1.96) ** 2)
    assert n_eff == pytest.approx(400.0, abs=0.2)


def test_cell_n_eff_gold_scb_style_loyalty_cell() -> None:
    n_eff = approximate_cell_n_eff(0.751, 0.027)
    assert n_eff == pytest.approx(0.751 * (1 - 0.751) / (0.027 / 1.96) ** 2)
    assert n_eff == pytest.approx(985.423, abs=0.01)


def test_row_n_eff_is_median_clipped_and_not_kish() -> None:
    estimates = _loyalty_estimates("S")
    rows = []
    for current_party, estimate in estimates.items():
        if current_party == "S":
            margin = 0.02
        elif current_party == "M":
            margin = 0.08
        else:
            margin = 0.05
        rows.append(
            _cell(
                "S",
                current_party,
                estimate,
                margin_error=margin,
                row_base=200,
            )
        )
    for previous_party in PARTIES:
        if previous_party == "S":
            continue
        for current_party, estimate in _loyalty_estimates(previous_party).items():
            rows.append(_cell(previous_party, current_party, estimate, row_base=200))
    result = infer_row_effective_sample_sizes(pl.DataFrame(rows))
    valid = result.cells.filter(
        (pl.col("previous_party") == "S") & pl.col("included")
    )["cell_n_eff"].to_list()
    expected = float(np.median(np.asarray(valid, dtype=float)))
    expected = min(max(expected, 1.0), 200.0)
    s_row = result.rows.filter(pl.col("previous_party") == "S").row(0, named=True)
    assert s_row["n_eff"] == pytest.approx(expected)
    assert s_row["n_eff"] <= 200
    assert s_row["n_eff"] >= 1
    assert s_row["n_eff_label"] == N_EFF_LABEL
    assert s_row["is_kish_ess"] is False
    assert result.is_kish_ess is False
    assert result.n_eff_label == N_EFF_LABEL


def test_n_eff_excludes_zero_and_missing_cells() -> None:
    rows = []
    for previous_party in PARTIES:
        for current_party in POLL_CURRENT_CATEGORIES:
            estimate: float | None = 0.08 if current_party in PARTIES else 0.07
            if previous_party == "C" and current_party == "C":
                estimate = 0.40
            margin: float | None = 0.04
            if previous_party == "C" and current_party == "V":
                estimate = 0.0
                margin = 0.0
            if previous_party == "C" and current_party == "MP":
                margin = None
            if previous_party == "C" and current_party == "OTHER":
                estimate = None
                margin = None
            rows.append(
                _cell(
                    previous_party,
                    current_party,
                    estimate,
                    margin_error=margin,
                    row_base=180,
                )
            )
    result = infer_row_effective_sample_sizes(pl.DataFrame(rows))
    reasons = dict(
        result.cells.filter(pl.col("previous_party") == "C")
        .select("current_party", "exclusion_reason")
        .iter_rows()
    )
    assert reasons["V"] == "zero_estimate"
    assert reasons["MP"] == "missing_margin"
    assert reasons["OTHER"] == "missing_estimate"
    assert reasons["C"] is None


def test_missing_margin_row_uses_wave_median_design_effect() -> None:
    cells = _complete_wave(row_base=400, margin_error=0.04)
    observed = infer_row_effective_sample_sizes(cells)
    wave_deff = observed.wave_median_design_effect
    assert wave_deff is not None

    fallback_rows = []
    for record in cells.iter_rows(named=True):
        margin = None if record["previous_party"] == "L" else record["margin_error"]
        fallback_rows.append({**record, "margin_error": margin})
    result = infer_row_effective_sample_sizes(pl.DataFrame(fallback_rows))
    liberal = result.rows.filter(pl.col("previous_party") == "L").row(0, named=True)
    assert liberal["used_fallback"] is True
    assert liberal["valid_cells"] == 0
    assert liberal["n_eff"] == pytest.approx(min(max(400 / wave_deff, 1.0), 400.0))


def test_n_eff_clips_to_public_row_base() -> None:
    rows = []
    for previous_party in PARTIES:
        for current_party in POLL_CURRENT_CATEGORIES:
            estimate = 0.5 if current_party == previous_party else 0.04
            if current_party not in PARTIES:
                estimate = 0.04
            rows.append(
                _cell(
                    previous_party,
                    current_party,
                    estimate,
                    margin_error=0.001,
                    row_base=50,
                )
            )
    result = infer_row_effective_sample_sizes(pl.DataFrame(rows))
    assert result.rows["n_eff"].max() == 50
    assert bool(result.rows["clip_applied"].all())


def test_published_cells_stay_unchanged_and_residual_is_flat() -> None:
    suppressed = {"S": ("C", "L")}
    custom = {party: _loyalty_estimates(party) for party in PARTIES}
    custom["S"]["C"] = 0.03
    custom["S"]["L"] = 0.03
    custom["S"]["S"] = 0.54
    cells = _complete_wave(suppressed=suppressed, estimates_by_previous=custom)
    published_s_to_s = cells.filter(
        (pl.col("previous_party") == "S") & (pl.col("current_party") == "S")
    ).item(0, "estimate")
    published_mass = float(
        cells.filter((pl.col("previous_party") == "S") & pl.col("estimate").is_not_null())[
            "estimate"
        ].sum()
    )
    residual = 1.0 - published_mass
    point = build_survey_point_estimate(cells)
    s_index = PARTIES.index("S")
    c_index = POLL_CURRENT_CATEGORIES.index("C")
    l_index = POLL_CURRENT_CATEGORIES.index("L")
    s_full_index = POLL_CURRENT_CATEGORIES.index("S")
    assert point.full_category_point[s_index, s_full_index] == pytest.approx(published_s_to_s)
    assert point.full_category_point[s_index, c_index] == pytest.approx(residual / 2)
    assert point.full_category_point[s_index, l_index] == pytest.approx(residual / 2)
    assert point.stated_party_point[s_index].sum() == pytest.approx(1.0)
    assert np.allclose(point.stated_party_point.sum(axis=1), 1.0)


def test_historical_origin_split_uses_same_row_party_mean() -> None:
    cells = _complete_wave(suppressed={"S": ("C", "L")})
    historical = np.zeros(len(POLL_CURRENT_CATEGORIES), dtype=float)
    historical[POLL_CURRENT_CATEGORIES.index("C")] = 0.8
    historical[POLL_CURRENT_CATEGORIES.index("L")] = 0.2
    point = build_survey_point_estimate(
        cells,
        suppression="historical_origin",
        historical_origin_means={"S": historical},
    )
    residual = 1.0 - float(
        cells.filter((pl.col("previous_party") == "S") & pl.col("estimate").is_not_null())[
            "estimate"
        ].sum()
    )
    s_index = PARTIES.index("S")
    assert point.full_category_point[s_index, POLL_CURRENT_CATEGORIES.index("C")] == pytest.approx(
        0.8 * residual
    )
    assert point.full_category_point[s_index, POLL_CURRENT_CATEGORIES.index("L")] == pytest.approx(
        0.2 * residual
    )


def test_zero_renormalize_drops_suppressed_support() -> None:
    cells = _complete_wave(suppressed={"S": ("C", "L")})
    point = build_survey_point_estimate(cells, suppression="zero_renormalize")
    s_index = PARTIES.index("S")
    assert point.full_category_point[s_index, POLL_CURRENT_CATEGORIES.index("C")] == 0
    assert point.full_category_point[s_index, POLL_CURRENT_CATEGORIES.index("L")] == 0
    assert point.full_category_point[s_index].sum() == pytest.approx(1.0)
    assert point.stated_party_point[s_index].sum() == pytest.approx(1.0)


def test_primary_builder_does_not_accept_national_poll_prior() -> None:
    assert "national_poll" not in build_survey_point_estimate.__code__.co_varnames
    assert "poll" not in build_survey_point_estimate.__code__.co_varnames


def test_draws_are_deterministic_simplex_and_keep_exact_zeros() -> None:
    estimates = {
        previous: _loyalty_estimates(previous) for previous in PARTIES
    }
    estimates["KD"]["V"] = 0.0
    cells = _complete_wave(estimates_by_previous=estimates)
    n_eff = infer_row_effective_sample_sizes(cells)
    point = build_survey_point_estimate(cells)
    first = sample_transition_draws(point, n_eff.vector(), n_draws=12, seed=20260911)
    second = sample_transition_draws(point, n_eff.vector(), n_draws=12, seed=20260911)
    third = sample_transition_draws(point, n_eff.vector(), n_draws=12, seed=7)
    assert np.array_equal(first.stated_party_draws, second.stated_party_draws)
    assert not np.array_equal(first.stated_party_draws, third.stated_party_draws)
    assert np.allclose(first.stated_party_draws.sum(axis=2), 1.0)
    assert np.allclose(first.full_category_draws.sum(axis=2), 1.0)
    kd_index = PARTIES.index("KD")
    v_index = POLL_CURRENT_CATEGORIES.index("V")
    assert np.allclose(first.full_category_draws[:, kd_index, v_index], 0.0)
    assert first.stated_party_draws.shape[2] == len(PARTIES)


def test_draw_wise_raking_reconciles_to_same_wave_poll() -> None:
    cells = _complete_wave()
    previous = np.arange(1, len(PARTIES) + 1, dtype=float)
    target = np.arange(len(PARTIES), 0, -1, dtype=float)
    estimate = estimate_survey_transition(
        cells,
        previous_national=previous,
        target_national=target,
        n_draws=8,
        seed=20260911,
    )
    assert estimate.raked is not None
    assert estimate.raked.row_simplex_ok
    assert estimate.raked.national_reconciliation_ok
    assert np.allclose(estimate.raked.raked_draws.sum(axis=2), 1.0)
    previous_share = previous / previous.sum()
    target_share = target / target.sum()
    for matrix in estimate.raked.raked_draws:
        assert np.allclose(previous_share @ matrix, target_share, atol=1e-10)
    assert np.allclose(previous_share @ estimate.raked.raked_point_matrix, target_share, atol=1e-10)
    unraked = estimate.point.stated_party_point
    assert not np.allclose(previous_share @ unraked, target_share, atol=1e-3)


def test_raking_is_applied_per_draw_not_to_a_single_mean_matrix() -> None:
    cells = _complete_wave()
    n_eff = infer_row_effective_sample_sizes(cells)
    point = build_survey_point_estimate(cells)
    draws = sample_transition_draws(point, n_eff.vector(), n_draws=6, seed=20260911)
    previous = np.linspace(0.2, 0.04, len(PARTIES))
    target = np.linspace(0.04, 0.2, len(PARTIES))
    raked = rake_transition_draws(draws, previous, target)
    mean_then_rake = raked.raked_point_matrix
    assert raked.raked_draws.shape[0] == 6
    assert not np.allclose(raked.mean_raked_draws, mean_then_rake)
    assert int(raked.calibration_diagnostics.height) == 6
