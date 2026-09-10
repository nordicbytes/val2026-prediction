from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from lightgbm import LGBMRegressor
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import ElasticNet, Ridge  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # type: ignore[import-untyped]

from valforecast.features.election_history import PARTIES
from valforecast.features.temporal import CORE_NUMERIC_FEATURES
from valforecast.models.district import project_rows_to_simplex, weighted_party_mae

SHARE_FEATURES = tuple(f"previous_share_{party}" for party in PARTIES)
STRUCTURE_FEATURES = (
    "previous_party_entropy",
    "previous_party_concentration",
    "previous_left_share",
    "previous_alliance_share",
    "previous_sd_share",
    "previous_bloc_margin",
    "previous_largest_party_margin",
)
TURNOUT_SIZE_FEATURES = ("previous_turnout", "log_previous_eligible_voters")
HISTORICAL_FEATURES = (
    *(f"historical_party_sensitivity_{party}" for party in PARTIES),
    *(f"historical_residual_std_{party}" for party in PARTIES),
    "historical_bloc_sensitivity",
)

ABLATION_NUMERIC_FEATURES = {
    "A_previous_shares": SHARE_FEATURES,
    "B_plus_structure": (*SHARE_FEATURES, *STRUCTURE_FEATURES),
    "C_plus_turnout_size": (
        *SHARE_FEATURES,
        *STRUCTURE_FEATURES,
        *TURNOUT_SIZE_FEATURES,
    ),
    "D_plus_historical": (
        *SHARE_FEATURES,
        *STRUCTURE_FEATURES,
        *TURNOUT_SIZE_FEATURES,
        *HISTORICAL_FEATURES,
    ),
    "E_plus_geography": (
        *SHARE_FEATURES,
        *STRUCTURE_FEATURES,
        *TURNOUT_SIZE_FEATURES,
        *HISTORICAL_FEATURES,
    ),
}


@dataclass(frozen=True)
class TemporalTest:
    test_id: str
    train_transitions: tuple[str, ...]
    test_transition: str


TEMPORAL_TESTS = (
    TemporalTest("B0", ("2014_2018",), "2018_2022"),
    TemporalTest("A", ("2010_2014",), "2014_2018"),
    TemporalTest("B", ("2010_2014", "2014_2018"), "2018_2022"),
)


def canonical_to_model_frame(canonical: pl.DataFrame) -> pl.DataFrame:
    district_columns = [
        "transition_id",
        "from_election",
        "to_election",
        "from_district_id",
        "to_district_id",
        "municipality_id",
        "county_id",
        "mapping_method",
        "mapping_quality",
        "comparison_weight",
        "valid_votes",
        "previous_valid_votes",
        "previous_largest_party",
        *CORE_NUMERIC_FEATURES,
        "historical_bloc_sensitivity",
    ]
    district = canonical.group_by("transition_id", "to_district_id").agg(
        *(
            pl.col(column).first()
            for column in district_columns
            if column not in {"transition_id", "to_district_id"}
        )
    )
    outcomes = canonical.pivot(
        on="party",
        index=["transition_id", "to_district_id"],
        values=[
            "current_vote_share",
            "baseline_no_change_share",
            "baseline_uniform_swing_share",
            "baseline_proportional_swing_share",
            "local_residual_swing",
            "historical_party_sensitivity",
            "historical_residual_swing_std",
        ],
        separator="_",
    )
    rename = {f"current_vote_share_{party}": f"actual_{party}" for party in PARTIES}
    rename.update({f"local_residual_swing_{party}": f"target_{party}" for party in PARTIES})
    rename.update(
        {
            f"historical_residual_swing_std_{party}": f"historical_residual_std_{party}"
            for party in PARTIES
        }
    )
    return district.join(
        outcomes.rename(rename),
        on=["transition_id", "to_district_id"],
    ).sort("transition_id", "to_district_id")


def run_temporal_validation(
    frame: pl.DataFrame,
    *,
    tests: Sequence[TemporalTest] = TEMPORAL_TESTS,
    random_seed: int = 20260910,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    metric_rows: list[dict[str, object]] = []
    prediction_frames: list[pl.DataFrame] = []
    for test in tests:
        train = frame.filter(pl.col("transition_id").is_in(test.train_transitions))
        future = frame.filter(pl.col("transition_id") == test.test_transition)
        if train.is_empty() or future.is_empty():
            continue
        metric_rows.extend(_baseline_metrics(test, future))
        for ablation, numeric_columns in ABLATION_NUMERIC_FEATURES.items():
            if (
                ablation in {"D_plus_historical", "E_plus_geography"}
                and train.select(
                    pl.any_horizontal(
                        pl.col("^historical_party_sensitivity_.*$").is_not_null()
                    ).any()
                ).item()
                is False
            ):
                continue
            categorical_columns = ["previous_largest_party"]
            if ablation == "E_plus_geography":
                categorical_columns.extend(["municipality_id", "county_id"])
            metrics, predictions = _fit_model_family(
                train,
                future,
                test,
                ablation,
                list(numeric_columns),
                categorical_columns,
                random_seed=random_seed,
            )
            metric_rows.extend(metrics)
            prediction_frames.append(predictions)
    return pl.DataFrame(metric_rows), pl.concat(prediction_frames)


def _fit_model_family(
    train: pl.DataFrame,
    future: pl.DataFrame,
    test: TemporalTest,
    ablation: str,
    numeric_columns: list[str],
    categorical_columns: list[str],
    *,
    random_seed: int,
) -> tuple[list[dict[str, object]], pl.DataFrame]:
    numeric_pipeline = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
    )
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    x_train = np.hstack(
        [
            numeric_pipeline.fit_transform(train.select(numeric_columns).to_numpy()),
            encoder.fit_transform(train.select(categorical_columns).to_numpy()),
        ]
    )
    x_test = np.hstack(
        [
            numeric_pipeline.transform(future.select(numeric_columns).to_numpy()),
            encoder.transform(future.select(categorical_columns).to_numpy()),
        ]
    )
    targets = [f"target_{party}" for party in PARTIES[:-1]]
    y_train = train.select(targets).to_numpy()
    fit_weights = train["previous_valid_votes"].to_numpy()
    evaluation_weights = future["valid_votes"].to_numpy()
    actual = future.select([f"actual_{party}" for party in PARTIES]).to_numpy()
    baseline = future.select(
        [f"baseline_proportional_swing_share_{party}" for party in PARTIES]
    ).to_numpy()
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
    }
    metrics: list[dict[str, object]] = []
    prediction_frames: list[pl.DataFrame] = []
    baseline_mae = weighted_party_mae(actual, baseline, evaluation_weights)
    for model_name, factory in factories.items():
        free_residual = np.zeros((future.height, len(PARTIES) - 1))
        if model_name == "ridge":
            model = factory(random_seed)
            model.fit(x_train, y_train, sample_weight=fit_weights)
            free_residual = model.predict(x_test)
        else:
            for party_index in range(len(PARTIES) - 1):
                model = factory(random_seed + party_index)
                model.fit(
                    x_train,
                    y_train[:, party_index],
                    sample_weight=fit_weights,
                )
                free_residual[:, party_index] = model.predict(x_test)
        residual = np.column_stack([free_residual, -free_residual.sum(axis=1)])
        predicted = project_rows_to_simplex(baseline + residual)
        mae = weighted_party_mae(actual, predicted, evaluation_weights)
        metrics.append(
            {
                "test_id": test.test_id,
                "train": " + ".join(test.train_transitions),
                "test": test.test_transition,
                "ablation": ablation,
                "model": model_name,
                "weighted_mae": mae,
                "baseline_mae": baseline_mae,
                "delta_mae": mae - baseline_mae,
                "relative_improvement": (baseline_mae - mae) / baseline_mae,
                "districts": future.height,
            }
        )
        prediction_frames.append(
            _prediction_long(
                future,
                actual,
                baseline,
                predicted,
                test,
                ablation,
                model_name,
            )
        )
    return metrics, pl.concat(prediction_frames)


def _baseline_metrics(
    test: TemporalTest,
    future: pl.DataFrame,
) -> list[dict[str, object]]:
    actual = future.select([f"actual_{party}" for party in PARTIES]).to_numpy()
    weights = future["valid_votes"].to_numpy()
    rows = []
    for model, prefix in (
        ("B0_no_change", "baseline_no_change_share"),
        ("B1_uniform_swing", "baseline_uniform_swing_share"),
        ("B2_proportional_swing", "baseline_proportional_swing_share"),
    ):
        predicted = future.select([f"{prefix}_{party}" for party in PARTIES]).to_numpy()
        mae = weighted_party_mae(actual, predicted, weights)
        rows.append(
            {
                "test_id": test.test_id,
                "train": " + ".join(test.train_transitions),
                "test": test.test_transition,
                "ablation": "baseline",
                "model": model,
                "weighted_mae": mae,
                "baseline_mae": mae if model == "B2_proportional_swing" else None,
                "delta_mae": 0.0 if model == "B2_proportional_swing" else None,
                "relative_improvement": 0.0 if model == "B2_proportional_swing" else None,
                "districts": future.height,
            }
        )
    return rows


def _prediction_long(
    future: pl.DataFrame,
    actual: np.ndarray,
    baseline: np.ndarray,
    predicted: np.ndarray,
    test: TemporalTest,
    ablation: str,
    model: str,
) -> pl.DataFrame:
    records: list[dict[str, object]] = []
    for row_index, district_id in enumerate(future["to_district_id"]):
        for party_index, party in enumerate(PARTIES):
            records.append(
                {
                    "test_id": test.test_id,
                    "train": " + ".join(test.train_transitions),
                    "test": test.test_transition,
                    "ablation": ablation,
                    "model": model,
                    "district_id": district_id,
                    "municipality_id": future["municipality_id"][row_index],
                    "county_id": future["county_id"][row_index],
                    "party": party,
                    "valid_votes": future["valid_votes"][row_index],
                    "actual_share": actual[row_index, party_index],
                    "baseline_share": baseline[row_index, party_index],
                    "predicted_share": predicted[row_index, party_index],
                }
            )
    return pl.DataFrame(records)
