from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from catboost import CatBoostRegressor  # type: ignore[import-untyped]
from lightgbm import LGBMRegressor
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import ElasticNet, Ridge  # type: ignore[import-untyped]
from sklearn.model_selection import GroupKFold  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[import-untyped]

from valforecast.features.election_history import (
    PARTIES,
    numeric_feature_columns,
    target_columns,
)


@dataclass(frozen=True)
class DistrictModelMetric:
    model: str
    grouping: str
    weighted_mae: float
    improvement_vs_proportional: float
    relative_improvement: float
    folds: int
    districts: int


def run_grouped_oof_models(
    frame: pl.DataFrame,
    *,
    random_seed: int = 20260910,
    folds: int = 5,
    group_column: str = "municipality_id",
    include_geography: bool = False,
) -> tuple[pl.DataFrame, list[DistrictModelMetric]]:
    numeric = numeric_feature_columns()
    x_numeric = frame.select(numeric).to_numpy()
    x_categorical = frame.select(["municipality_id", "county_id"]).to_numpy()
    y_full = frame.select(target_columns()).to_numpy()
    y = y_full[:, :-1]
    baseline = frame.select([f"predicted_share_{party}" for party in PARTIES]).to_numpy()
    actual = frame.select([f"actual_share_{party}" for party in PARTIES]).to_numpy()
    fit_weights = frame["valid_votes_2018"].to_numpy()
    evaluation_weights = frame["valid_votes_2022"].to_numpy()
    groups = frame[group_column].to_numpy()

    factories: dict[str, Callable[[int], Any]] = {
        "ridge": lambda seed: Ridge(alpha=10.0),
        "elastic_net": lambda seed: ElasticNet(
            alpha=0.0005,
            l1_ratio=0.2,
            max_iter=10_000,
            random_state=seed,
        ),
        "lightgbm": lambda seed: LGBMRegressor(
            n_estimators=250,
            learning_rate=0.03,
            num_leaves=15,
            max_depth=5,
            min_child_samples=40,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=seed,
            verbosity=-1,
            n_jobs=1,
        ),
        "catboost": lambda seed: CatBoostRegressor(
            iterations=250,
            depth=5,
            learning_rate=0.03,
            loss_function="RMSE",
            l2_leaf_reg=5.0,
            random_seed=seed,
            verbose=False,
            thread_count=1,
            allow_writing_files=False,
        ),
    }
    splitter = GroupKFold(n_splits=folds, shuffle=True, random_state=random_seed)
    residual_predictions = {model: np.zeros_like(y, dtype=float) for model in factories}
    fold_ids = np.full(frame.height, -1, dtype=int)

    for fold, (train, test) in enumerate(splitter.split(x_numeric, y, groups)):
        fold_ids[test] = fold
        numeric_pipeline = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
        )
        train_parts = [numeric_pipeline.fit_transform(x_numeric[train])]
        test_parts = [numeric_pipeline.transform(x_numeric[test])]
        if include_geography:
            encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
            train_parts.append(encoder.fit_transform(x_categorical[train]))
            test_parts.append(encoder.transform(x_categorical[test]))
        x_train = np.hstack(train_parts)
        x_test = np.hstack(test_parts)

        ridge = factories["ridge"](random_seed + fold)
        assert isinstance(ridge, Ridge)
        ridge.fit(x_train, y[train], sample_weight=fit_weights[train])
        residual_predictions["ridge"][test] = ridge.predict(x_test)

        for model_name in ("elastic_net", "lightgbm", "catboost"):
            for party_index in range(len(PARTIES) - 1):
                model = factories[model_name](random_seed + fold * 100 + party_index)
                model.fit(
                    x_train,
                    y[train, party_index],
                    sample_weight=fit_weights[train],
                )
                residual_predictions[model_name][test, party_index] = model.predict(x_test)

    baseline_mae = weighted_party_mae(actual, baseline, evaluation_weights)
    records: list[dict[str, object]] = []
    metrics: list[DistrictModelMetric] = [
        DistrictModelMetric(
            model="proportional_swing",
            grouping=group_column,
            weighted_mae=baseline_mae,
            improvement_vs_proportional=0.0,
            relative_improvement=0.0,
            folds=folds,
            districts=frame.height,
        )
    ]
    for model_name, free_residual_prediction in residual_predictions.items():
        residual_prediction = np.column_stack(
            [free_residual_prediction, -free_residual_prediction.sum(axis=1)]
        )
        shares = project_rows_to_simplex(baseline + residual_prediction)
        mae = weighted_party_mae(actual, shares, evaluation_weights)
        improvement = baseline_mae - mae
        metrics.append(
            DistrictModelMetric(
                model=model_name,
                grouping=group_column,
                weighted_mae=mae,
                improvement_vs_proportional=improvement,
                relative_improvement=improvement / baseline_mae,
                folds=folds,
                districts=frame.height,
            )
        )
        for row_index, district_id in enumerate(frame["to_district_id"]):
            for party_index, party in enumerate(PARTIES):
                records.append(
                    {
                        "district_id": district_id,
                        "municipality_id": frame["municipality_id"][row_index],
                        "county_id": frame["county_id"][row_index],
                        "fold": int(fold_ids[row_index]),
                        "grouping": group_column,
                        "model": model_name,
                        "party": party,
                        "valid_votes": int(evaluation_weights[row_index]),
                        "actual_share": float(actual[row_index, party_index]),
                        "baseline_share": float(baseline[row_index, party_index]),
                        "predicted_share": float(shares[row_index, party_index]),
                        "actual_residual": float(y_full[row_index, party_index]),
                        "predicted_residual": float(residual_prediction[row_index, party_index]),
                    }
                )
    return pl.DataFrame(records), metrics


def close_rows(values: np.ndarray) -> np.ndarray:
    totals = values.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise ValueError("Cannot close rows with non-positive totals")
    return np.asarray(values / totals, dtype=float)


def project_rows_to_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection of each row onto the probability simplex."""
    projected = np.empty_like(values, dtype=float)
    for row_index, row in enumerate(values):
        ordered = np.sort(row)[::-1]
        cumulative = np.cumsum(ordered) - 1
        support = np.flatnonzero(ordered - cumulative / np.arange(1, len(row) + 1) > 0)
        theta = cumulative[support[-1]] / (support[-1] + 1)
        projected[row_index] = np.maximum(row - theta, 0)
    return projected


def weighted_party_mae(
    actual: np.ndarray,
    predicted: np.ndarray,
    weights: np.ndarray,
) -> float:
    party_mae = np.average(np.abs(actual - predicted), weights=weights, axis=0)
    return float(np.mean(party_mae))


def run_ridge_feature_ablation(
    frame: pl.DataFrame,
    *,
    random_seed: int = 20260910,
    folds: int = 5,
) -> pl.DataFrame:
    feature_sets = {
        "previous_vote_shares": [f"previous_{party}" for party in PARTIES],
        "previous_shares_plus_turnout_entropy_size": numeric_feature_columns(),
        "previous_shares_plus_context_and_geography": numeric_feature_columns(),
    }
    y = frame.select(target_columns()).to_numpy()[:, :-1]
    baseline = frame.select([f"predicted_share_{party}" for party in PARTIES]).to_numpy()
    actual = frame.select([f"actual_share_{party}" for party in PARTIES]).to_numpy()
    fit_weights = frame["valid_votes_2018"].to_numpy()
    evaluation_weights = frame["valid_votes_2022"].to_numpy()
    baseline_mae = weighted_party_mae(actual, baseline, evaluation_weights)
    rows = []
    for grouping in ("municipality_id", "county_id"):
        groups = frame[grouping].to_numpy()
        grouping_folds = len(np.unique(groups)) if grouping == "county_id" else folds
        splitter = GroupKFold(n_splits=grouping_folds, shuffle=True, random_state=random_seed)
        for feature_set, numeric in feature_sets.items():
            include_geography = feature_set.endswith("and_geography")
            x_numeric = frame.select(numeric).to_numpy()
            x_categorical = frame.select(["municipality_id", "county_id"]).to_numpy()
            residual_prediction = np.zeros_like(y)
            for train, test in splitter.split(x_numeric, y, groups):
                numeric_pipeline = make_pipeline(
                    SimpleImputer(strategy="median"),
                    StandardScaler(),
                )
                train_parts = [numeric_pipeline.fit_transform(x_numeric[train])]
                test_parts = [numeric_pipeline.transform(x_numeric[test])]
                if include_geography:
                    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
                    train_parts.append(encoder.fit_transform(x_categorical[train]))
                    test_parts.append(encoder.transform(x_categorical[test]))
                model = Ridge(alpha=10.0)
                model.fit(
                    np.hstack(train_parts),
                    y[train],
                    sample_weight=fit_weights[train],
                )
                residual_prediction[test] = model.predict(np.hstack(test_parts))
            full_residual_prediction = np.column_stack(
                [residual_prediction, -residual_prediction.sum(axis=1)]
            )
            prediction = project_rows_to_simplex(baseline + full_residual_prediction)
            mae = weighted_party_mae(actual, prediction, evaluation_weights)
            rows.append(
                {
                    "model": "ridge",
                    "grouping": grouping,
                    "feature_set": feature_set,
                    "weighted_mae": mae,
                    "improvement_vs_proportional": baseline_mae - mae,
                    "relative_improvement": (baseline_mae - mae) / baseline_mae,
                }
            )
    return pl.DataFrame(rows)
