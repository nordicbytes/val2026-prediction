import numpy as np
import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.models.transition_matrix import (
    build_poll_state_proportional_predictions,
    build_transition_predictions,
)


def _canonical_fixture() -> pl.DataFrame:
    rows = []
    previous = np.arange(1, len(PARTIES) + 1, dtype=float)
    previous /= previous.sum()
    current = previous[::-1]
    for party_index, party in enumerate(PARTIES):
        rows.append(
            {
                "transition_id": "2018_2022",
                "to_district_id": "01010101",
                "municipality_id": "0101",
                "county_id": "01",
                "party": party,
                "previous_vote_share": previous[party_index],
                "current_vote_share": current[party_index],
                "valid_votes": 1000,
                "previous_valid_votes": 900,
            }
        )
    return pl.DataFrame(rows)


def test_identity_transition_returns_previous_district_composition() -> None:
    canonical = _canonical_fixture()
    predictions = build_transition_predictions(
        canonical,
        np.eye(len(PARTIES)),
        model="T0_identity",
    )
    assert np.allclose(predictions["predicted_share"], predictions["previous_share"])
    assert np.isclose(predictions["predicted_share"].sum(), 1)


def test_b2_and_t0_accept_the_same_national_poll_state() -> None:
    canonical = _canonical_fixture()
    previous_national = np.repeat(1 / len(PARTIES), len(PARTIES))
    poll_national = np.arange(1, len(PARTIES) + 1, dtype=float)
    poll_national /= poll_national.sum()
    transition = np.tile(poll_national, (len(PARTIES), 1))
    t0 = build_transition_predictions(canonical, transition, model="T0")
    b2 = build_poll_state_proportional_predictions(
        canonical,
        previous_national,
        poll_national,
    )
    assert np.allclose(
        t0.group_by("district_id").agg(pl.col("predicted_share").sum())[
            "predicted_share"
        ],
        1,
    )
    assert np.allclose(
        b2.group_by("district_id").agg(pl.col("predicted_share").sum())[
            "predicted_share"
        ],
        1,
    )
    assert np.allclose(t0["predicted_share"], poll_national)
