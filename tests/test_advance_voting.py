from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from valforecast.experiments.advance_voting import (
    FoldMetrics,
    build_experiment_frame,
    gate_result,
    leave_one_election_out,
    literal_geographic_reweighting,
    municipality_outcomes,
    read_advance_vote_file,
)


def test_receipt_file_is_cut_at_d2_and_aggregated_by_reception_municipality(
    tmp_path: Path,
) -> None:
    path = tmp_path / "received.csv"
    path.write_text(
        "lan;län;kom;kommun;lokalid;lokal;"
        "2014-09-07;2014-09-12;2014-09-13;2014-09-14;Totalt\n"
        "01;Stockholm;14;Upplands Väsby;1;A;10;20;30;40;100\n"
        "01;Stockholm;14;Upplands Väsby;2;B;5;0;0;0;5\n"
        "SUMMA;;;;;;15;20;30;40;105\n",
        encoding="cp1252",
    )

    result = read_advance_vote_file(path, election_year=2014, cutoff_days=2)

    assert result.height == 1
    assert result["municipality_id"][0] == "0114"
    assert result["received_d2"][0] == 35
    assert result["received_d7"][0] == 15
    assert result["active_locations_d2"][0] == 2
    assert result["registered_locations"][0] == 2


def test_municipality_outcomes_include_collection_votes_without_eligible_duplication() -> None:
    rows = []
    for district, kind, eligible, valid, invalid, s_votes, m_votes in (
        ("physical", "PHYSICAL", 100, 75, 5, 45, 30),
        ("collection", "COLLECTION", 0, 20, 0, 8, 12),
    ):
        for party, votes in (("S", s_votes), ("M", m_votes)):
            rows.append(
                {
                    "election_year": 2022,
                    "district_id": district,
                    "district_kind": kind,
                    "municipality_id": "0001",
                    "canonical_party_code": party,
                    "votes": votes,
                    "valid_votes": valid,
                    "invalid_votes": invalid,
                    "eligible_voters": eligible,
                }
            )

    result = municipality_outcomes(pl.DataFrame(rows))

    assert result.height == 1
    assert result["eligible_voters"][0] == 100
    assert result["valid_votes"][0] == 95
    assert result["turnout"][0] == pytest.approx(1.0)
    assert result["S"][0] == pytest.approx(53 / 95)
    assert result["M"][0] == pytest.approx(42 / 95)


def test_gate_requires_every_fold_and_the_registered_pooled_margin() -> None:
    folds = [
        FoldMetrics(2014, "turnout", 0.020, 0.019, 0.05, 0.02, 0.019, 290, 1.0),
        FoldMetrics(2018, "turnout", 0.020, 0.019, 0.05, 0.02, 0.019, 290, 1.0),
        FoldMetrics(2022, "turnout", 0.020, 0.021, -0.05, 0.02, 0.021, 290, 1.0),
    ]

    result = gate_result(folds, outcome="turnout", minimum_relative_improvement=0.005)

    assert result["passed"] is False
    assert result["improved_every_fold"] is False


def test_official_2022_file_reproduces_the_published_d2_total() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "data/raw/advance_voting/2022/fortidsroster.csv"
    if not path.exists():
        pytest.skip("experimental raw data has not been downloaded")

    result = read_advance_vote_file(path, election_year=2022, cutoff_days=2)

    assert result.height == 290
    assert result["received_d2"].sum() == 2_820_437


def test_the_historical_signal_fails_the_registered_stability_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "data/raw/advance_voting/2010/mottagna_fortidsroster.skv").exists():
        pytest.skip("experimental raw data has not been downloaded")

    frame = build_experiment_frame(root)
    folds, _predictions, _coefficients = leave_one_election_out(frame, alpha=10.0)

    assert frame.height == 870
    party = [fold for fold in folds if fold.outcome == "named_party_share"]
    turnout = [fold for fold in folds if fold.outcome == "turnout"]
    assert all(fold.candidate_weighted_mae > fold.baseline_weighted_mae for fold in party)
    assert turnout[0].candidate_weighted_mae < turnout[0].baseline_weighted_mae
    assert turnout[1].candidate_weighted_mae > turnout[1].baseline_weighted_mae
    assert turnout[2].candidate_weighted_mae < turnout[2].baseline_weighted_mae
    assert (
        gate_result(folds, outcome="named_party_share", minimum_relative_improvement=0.005)[
            "passed"
        ]
        is False
    )
    assert (
        gate_result(folds, outcome="turnout", minimum_relative_improvement=0.005)["passed"] is False
    )

    literal = literal_geographic_reweighting(frame)
    assert [row["relative_improvement"] > 0 for row in literal] == [True, False, True]
