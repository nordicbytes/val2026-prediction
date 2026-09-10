from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import POLL_CURRENT_CATEGORIES
from valforecast.polls.transition_hierarchy import (
    CONCENTRATION_GRID,
    fit_previous_party_hierarchy,
    hierarchical_dirichlet_parameters,
    hierarchical_posterior_mean,
    parse_calendar_year,
    previous_party_mean,
)
from valforecast.polls.transition_posterior import (
    build_survey_point_estimate,
    estimate_survey_transition,
    infer_row_effective_sample_sizes,
)


def _estimates(previous_party: str, stay: float) -> dict[str, float]:
    leftover = 0.84 - stay
    leak = leftover / (len(PARTIES) - 1)
    estimates = {party: leak for party in PARTIES}
    estimates[previous_party] = stay
    estimates["BLANK"] = 0.06
    estimates["DONT_KNOW"] = 0.08
    estimates["MISSING"] = 0.02
    return estimates


def _wave(
    wave_id: str,
    *,
    previous_election: int,
    stay: float = 0.60,
    stay_by_party: dict[str, float] | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for previous_party in PARTIES:
        party_stay = stay_by_party.get(previous_party, stay) if stay_by_party else stay
        estimates = _estimates(previous_party, party_stay)
        for current_party, estimate in estimates.items():
            rows.append(
                {
                    "wave_id": wave_id,
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "estimate": estimate,
                    "margin_error": 0.04,
                    "row_base": 400,
                    "cell_status": "PUBLISHED",
                    "previous_election": previous_election,
                }
            )
        for current_party in POLL_CURRENT_CATEGORIES:
            if current_party in estimates:
                continue
            rows.append(
                {
                    "wave_id": wave_id,
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "estimate": 0.0,
                    "margin_error": 0.0,
                    "row_base": 400,
                    "cell_status": "PUBLISHED",
                    "previous_election": previous_election,
                }
            )
    return pl.DataFrame(rows)


def _training_corpus() -> pl.DataFrame:
    return pl.concat(
        [
            _wave("2015M11", previous_election=2014, stay=0.62),
            _wave("2016M05", previous_election=2014, stay=0.60),
            _wave("2016M11", previous_election=2014, stay=0.61),
            _wave("2017M05", previous_election=2014, stay=0.59),
            _wave("2017M11", previous_election=2014, stay=0.20),
        ]
    )


def test_parse_calendar_year_from_wave_ids() -> None:
    assert parse_calendar_year("2015M11") == 2015
    assert parse_calendar_year("scb_2018M05_original") == 2018


def test_previous_party_mean_is_equal_weight_same_party_only() -> None:
    s_rows = [
        np.array([0.8 if party == "S" else 0.025 for party in PARTIES]),
        np.array([0.6 if party == "S" else 0.05 for party in PARTIES]),
    ]
    m_rows = [np.array([0.9 if party == "M" else 0.0125 for party in PARTIES])]
    s_mean = previous_party_mean(s_rows, zero_smoothing=0.0)
    m_mean = previous_party_mean(m_rows, zero_smoothing=0.0)
    assert s_mean[PARTIES.index("S")] == pytest.approx(0.7)
    assert m_mean[PARTIES.index("M")] == pytest.approx(0.9)
    assert s_mean[PARTIES.index("M")] == pytest.approx(0.0375)


def test_hierarchy_mean_uses_same_previous_party_rows_only() -> None:
    cells = pl.concat(
        [
            _wave(
                "2015M11",
                previous_election=2014,
                stay_by_party={"S": 0.72, "M": 0.20},
            ),
            _wave(
                "2016M05",
                previous_election=2014,
                stay_by_party={"S": 0.70, "M": 0.18},
            ),
            _wave(
                "2017M05",
                previous_election=2014,
                stay_by_party={"S": 0.68, "M": 0.16},
            ),
        ]
    )
    fit = fit_previous_party_hierarchy(cells, target_wave_id="2018M05")
    s_mean = fit.means["S"]
    m_mean = fit.means["M"]
    assert s_mean[PARTIES.index("S")] > 0.6
    assert s_mean[PARTIES.index("M")] < 0.15
    assert m_mean[PARTIES.index("M")] < 0.35
    assert m_mean[PARTIES.index("S")] < 0.15
    assert s_mean[PARTIES.index("S")] > m_mean[PARTIES.index("S")]


def test_hierarchy_rejects_mixed_previous_election_reference() -> None:
    cells = pl.concat(
        [
            _wave("2015M11", previous_election=2014),
            _wave("2019M05", previous_election=2018),
        ]
    )
    with pytest.raises(ValueError, match="single previous-election"):
        fit_previous_party_hierarchy(cells, target_wave_id="2022M05")


def test_target_may_wave_cannot_enter_prior() -> None:
    cells = pl.concat(
        [
            _wave("2015M11", previous_election=2014),
            _wave("2016M05", previous_election=2014),
            _wave("2018M05", previous_election=2014, stay=0.10),
        ]
    )
    with pytest.raises(ValueError, match="Target May wave"):
        fit_previous_party_hierarchy(cells, target_wave_id="2018M05")


def test_target_may_calendar_wave_cannot_enter_prior() -> None:
    cells = pl.concat(
        [
            _wave("scb_2015M11_original", previous_election=2014).with_columns(
                pl.lit("2015M11").alias("calendar_wave")
            ),
            _wave("scb_2016M05_original", previous_election=2014).with_columns(
                pl.lit("2016M05").alias("calendar_wave")
            ),
            _wave("scb_2018M05_original", previous_election=2014, stay=0.10).with_columns(
                pl.lit("2018M05").alias("calendar_wave")
            ),
        ]
    )
    with pytest.raises(ValueError, match="Target May wave"):
        fit_previous_party_hierarchy(cells, target_wave_id="2018M05")


def test_untouched_validation_wave_is_not_used_for_tuning() -> None:
    cells = _training_corpus()
    fit = fit_previous_party_hierarchy(
        cells,
        target_wave_id="2018M05",
        untouched_validation_wave_ids=("2017M11",),
    )
    assert "2017M11" not in fit.training_wave_ids
    assert "2017M11" in fit.excluded_wave_ids
    if fit.fold_scores.height:
        assert "2017M11" not in fit.fold_scores["wave_id"].to_list()
    assert fit.means["S"][PARTIES.index("S")] > 0.5


def test_leave_one_calendar_year_out_holds_entire_year() -> None:
    cells = _training_corpus()
    fit = fit_previous_party_hierarchy(
        cells,
        target_wave_id="2018M05",
        untouched_validation_wave_ids=("2017M11",),
    )
    years_by_wave = {
        "2015M11": 2015,
        "2016M05": 2016,
        "2016M11": 2016,
        "2017M05": 2017,
    }
    for record in fit.fold_scores.iter_rows(named=True):
        assert years_by_wave[str(record["wave_id"])] == record["held_out_year"]
    held_out_2016 = fit.fold_scores.filter(pl.col("held_out_year") == 2016)
    assert set(held_out_2016["wave_id"].unique()) == {"2016M05", "2016M11"}


def test_fixed_grid_selection_and_underidentified_fallback() -> None:
    identified = fit_previous_party_hierarchy(
        _training_corpus().filter(pl.col("wave_id") != "2017M11"),
        target_wave_id="2018M05",
    )
    assert identified.concentration_grid == CONCENTRATION_GRID
    assert set(identified.fold_scores["kappa"].unique()) == set(CONCENTRATION_GRID)
    assert set(identified.selected["kappa"].to_list()).issubset(set(CONCENTRATION_GRID))
    assert bool((identified.selected["underidentified"] == False).all())  # noqa: E712

    thin = fit_previous_party_hierarchy(
        _wave("2016M05", previous_election=2014),
        target_wave_id="2018M05",
    )
    assert thin.selected["kappa"].to_list() == [0.0] * len(PARTIES)
    assert bool(thin.selected["used_fallback"].all())
    assert bool(thin.selected["underidentified"].all())


def test_stable_history_prefers_positive_concentration() -> None:
    cells = pl.concat(
        [
            _wave("2015M11", previous_election=2014, stay=0.70),
            _wave("2016M05", previous_election=2014, stay=0.70),
            _wave("2017M05", previous_election=2014, stay=0.70),
        ]
    )
    fit = fit_previous_party_hierarchy(cells, target_wave_id="2018M05")
    assert float(fit.selected["kappa"].min()) > 0


def test_election_outcome_columns_are_rejected() -> None:
    cells = _wave("2015M11", previous_election=2014).with_columns(
        pl.lit(0.3).alias("election_result")
    )
    with pytest.raises(ValueError, match="Election outcomes"):
        fit_previous_party_hierarchy(cells, target_wave_id="2018M05")


def test_hierarchy_module_has_no_election_outcome_imports() -> None:
    imports = [
        line
        for line in Path("src/valforecast/polls/transition_hierarchy.py")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    joined = "\n".join(imports)
    for token in (
        "valforecast.ingest",
        "valforecast.evaluation",
        "valforecast.models",
        "elections",
    ):
        assert token not in joined


def test_hierarchical_posterior_uses_same_row_party_prior_only() -> None:
    cells = pl.concat(
        [
            _wave("2015M11", previous_election=2014, stay_by_party={"S": 0.75, "M": 0.20}),
            _wave("2016M05", previous_election=2014, stay_by_party={"S": 0.73, "M": 0.18}),
            _wave("2017M05", previous_election=2014, stay_by_party={"S": 0.71, "M": 0.16}),
        ]
    )
    fit = fit_previous_party_hierarchy(cells, target_wave_id="2018M05")
    target = _wave("2018M05", previous_election=2014, stay=0.40)
    point = build_survey_point_estimate(target.drop("previous_election"))
    n_eff = infer_row_effective_sample_sizes(target.drop("previous_election")).vector()
    alpha = hierarchical_dirichlet_parameters(fit, point, n_eff)
    posterior = hierarchical_posterior_mean(fit, point, n_eff)
    s_index = PARTIES.index("S")
    m_index = PARTIES.index("M")
    assert posterior[s_index, s_index] > point.stated_party_point[s_index, s_index]
    assert posterior[s_index, m_index] < 0.15
    assert alpha[s_index, s_index] > alpha[s_index, m_index]
    assert np.allclose(posterior.sum(axis=1), 1.0)


def test_secondary_draws_are_deterministic_and_rakeable() -> None:
    cells = pl.concat(
        [
            _wave("2015M11", previous_election=2014, stay=0.66),
            _wave("2016M05", previous_election=2014, stay=0.64),
            _wave("2017M05", previous_election=2014, stay=0.65),
        ]
    )
    fit = fit_previous_party_hierarchy(cells, target_wave_id="2018M05")
    target = _wave("2018M05", previous_election=2014, stay=0.50)
    survey = target.drop("previous_election")
    n_eff = infer_row_effective_sample_sizes(survey)
    point = build_survey_point_estimate(survey)
    alpha = hierarchical_dirichlet_parameters(fit, point, n_eff.vector())
    first = estimate_survey_transition(
        survey,
        previous_national=np.repeat(1.0, len(PARTIES)),
        target_national=np.linspace(0.2, 0.04, len(PARTIES)),
        n_draws=5,
        seed=20260911,
        dirichlet_alpha=alpha,
        estimator="T1_previous_party_hierarchical_raked",
    )
    second = estimate_survey_transition(
        survey,
        previous_national=np.repeat(1.0, len(PARTIES)),
        target_national=np.linspace(0.2, 0.04, len(PARTIES)),
        n_draws=5,
        seed=20260911,
        dirichlet_alpha=alpha,
        estimator="T1_previous_party_hierarchical_raked",
    )
    assert first.raked is not None
    assert second.raked is not None
    assert np.array_equal(first.raked.raked_draws, second.raked.raked_draws)
    assert first.raked.row_simplex_ok
    assert first.raked.national_reconciliation_ok
