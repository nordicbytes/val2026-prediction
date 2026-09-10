import numpy as np
import polars as pl

from valforecast.models.baselines import (
    evaluate_predictions,
    predict_proportional_swing,
    predict_uniform_swing,
    project_simplex,
)


def test_simplex_projection_is_valid() -> None:
    projected = project_simplex(np.array([-0.2, 0.7, 0.8]))
    assert np.all(projected >= 0)
    assert np.isclose(projected.sum(), 1)


def test_uniform_swing_preserves_simplex() -> None:
    prediction = predict_uniform_swing(
        np.array([0.1, 0.4, 0.5]),
        np.array([0.05, -0.02, -0.03]),
    )
    np.testing.assert_allclose(prediction, [0.15, 0.38, 0.47])


def test_uniform_swing_clips_negative_share_then_closes() -> None:
    prediction = predict_uniform_swing(
        np.array([0.01, 0.49, 0.5]),
        np.array([-0.02, 0.01, 0.01]),
    )
    np.testing.assert_allclose(prediction, [0, 0.5 / 1.01, 0.51 / 1.01])


def test_proportional_swing_matches_national_case() -> None:
    previous = np.array([0.2, 0.3, 0.5])
    current = np.array([0.25, 0.25, 0.5])
    prediction = predict_proportional_swing(previous, previous, current)
    np.testing.assert_allclose(prediction, current)


def test_metrics_are_zero_for_perfect_predictions() -> None:
    frame = pl.DataFrame(
        {
            "district_id": ["A", "A", "B", "B"],
            "party": ["S", "M", "S", "M"],
            "actual_share": [0.6, 0.4, 0.2, 0.8],
            "predicted_share": [0.6, 0.4, 0.2, 0.8],
            "valid_votes": [100, 100, 200, 200],
        }
    )
    metrics = evaluate_predictions(frame, model="perfect")
    assert metrics.district_weighted_mae == 0
    assert metrics.national_mae == 0
    assert metrics.districts == 2

