from __future__ import annotations

import numpy as np

from valforecast.calibration.probabilities import (
    BLOC_LEFT,
    BLOC_RIGHT,
    RIKSDAG_THRESHOLD,
    apply_overlay,
    monte_carlo_error,
    overlay_probabilities,
    recenter_on_point,
    summarize_draws,
)
from valforecast.features.election_history import PARTIES


def _draws(rows: list[dict[str, float]]) -> np.ndarray:
    matrix = np.zeros((len(rows), len(PARTIES)), dtype=float)
    for index, row in enumerate(rows):
        for party, value in row.items():
            matrix[index, PARTIES.index(party)] = value
        matrix[index, PARTIES.index("OTHER")] = 1.0 - matrix[index].sum()
    return matrix


def _row(left_total: float, l_share: float) -> dict[str, float]:
    per_left = left_total / len(BLOC_LEFT)
    remaining = 1.0 - left_total - l_share - 0.02
    per_right = remaining / (len(BLOC_RIGHT) - 1)
    row = {party: per_left for party in BLOC_LEFT}
    row.update({party: per_right for party in BLOC_RIGHT if party != "L"})
    row["L"] = l_share
    return row


def test_bloc_probability_counts_draws_where_left_is_larger() -> None:
    draws = _draws([_row(0.52, 0.05), _row(0.52, 0.05), _row(0.52, 0.05), _row(0.44, 0.05)])
    summary = summarize_draws(draws)
    assert summary["bloc"]["p_left_largest"] == 0.75
    assert summary["bloc"]["p_right_largest"] == 0.25
    assert summary["bloc"]["left"] == list(BLOC_LEFT)
    assert summary["bloc"]["right"] == list(BLOC_RIGHT)


def test_threshold_probability_uses_four_percent() -> None:
    draws = _draws([_row(0.50, 0.05), _row(0.50, 0.045), _row(0.50, 0.039), _row(0.50, 0.02)])
    summary = summarize_draws(draws)
    assert summary["threshold_level"] == RIKSDAG_THRESHOLD
    assert summary["threshold"]["L"]["p_above"] == 0.5
    assert "OTHER" not in summary["threshold"]


def test_monte_carlo_error_shrinks_with_more_draws() -> None:
    assert monte_carlo_error(0.5, 2_000) > monte_carlo_error(0.5, 20_000)
    assert abs(monte_carlo_error(0.5, 10_000) - 0.005) < 1e-12
    assert monte_carlo_error(1.0, 2_000) == 0.0


def test_recentering_puts_the_mean_on_the_point_and_keeps_a_simplex() -> None:
    draws = _draws([_row(0.52, 0.05), _row(0.46, 0.03), _row(0.50, 0.06)])
    point = draws.mean(axis=0) + 0.004
    point = point / point.sum()
    recentered = recenter_on_point(draws, point)
    assert np.allclose(recentered.sum(axis=1), 1.0)
    assert np.allclose(recentered.mean(axis=0), point, atol=1e-6)


def test_zero_covariance_overlay_only_replicates_the_base() -> None:
    draws = _draws([_row(0.52, 0.05), _row(0.46, 0.03)])
    point = draws.mean(axis=0)
    sigma = np.zeros((len(PARTIES), len(PARTIES)))
    overlaid = apply_overlay(draws, sigma, point, seed=1, replicates=3)
    assert overlaid.shape == (6, len(PARTIES))
    assert np.allclose(overlaid, np.repeat(recenter_on_point(draws, point), 3, axis=0), atol=1e-9)


def test_election_day_error_lowers_certainty_about_a_party_just_above_the_threshold() -> None:
    rng = np.random.default_rng(7)
    base = _draws([_row(0.505, 0.05 + 0.001 * float(rng.normal())) for _ in range(200)])
    point = base.mean(axis=0)
    small = np.eye(len(PARTIES)) * 1e-8
    large = np.eye(len(PARTIES)) * (0.01**2)
    result = overlay_probabilities(base, point, large, large, seed=11, replicates=5)
    official = result["sets"]["official"]["threshold"]["L"]["p_above"]
    decomposed = result["sets"]["decomposed"]["threshold"]["L"]["p_above"]
    assert official == 1.0
    assert decomposed < official
    assert result["sets"]["decomposed"]["n_draws"] == 1_000
    tight = overlay_probabilities(base, point, small, small, seed=11, replicates=5)
    assert tight["sets"]["decomposed"]["threshold"]["L"]["p_above"] == 1.0
