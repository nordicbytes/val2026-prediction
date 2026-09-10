import numpy as np

from valforecast.models.district import (
    close_rows,
    project_rows_to_simplex,
    weighted_party_mae,
)


def test_close_rows_returns_probability_simplex() -> None:
    values = np.array([[0.2, 0.3, 0.6], [0.0, 2.0, 1.0]])
    closed = close_rows(values)
    np.testing.assert_allclose(closed.sum(axis=1), 1)
    assert np.all(closed >= 0)


def test_weighted_party_mae_weights_districts_not_parties() -> None:
    actual = np.array([[0.5, 0.5], [0.2, 0.8]])
    predicted = np.array([[0.4, 0.6], [0.2, 0.8]])
    result = weighted_party_mae(actual, predicted, np.array([3.0, 1.0]))
    assert np.isclose(result, 0.075)


def test_project_rows_to_simplex_preserves_valid_rows_and_repairs_invalid_rows() -> None:
    values = np.array([[0.2, 0.3, 0.5], [-0.2, 0.3, 1.1]])
    projected = project_rows_to_simplex(values)
    np.testing.assert_allclose(projected[0], values[0])
    np.testing.assert_allclose(projected.sum(axis=1), 1)
    assert np.all(projected >= 0)
