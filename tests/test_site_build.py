from __future__ import annotations

import json
from pathlib import Path

import pytest

from valforecast.site.build import (
    END,
    LIVE_END,
    LIVE_START,
    START,
    build_site,
    load_live_data,
    load_seat_data,
)

PARTIES = ["V", "S", "MP", "C", "L", "M", "KD", "SD"]


def _document(draws: list[list[int]]) -> dict[str, object]:
    zeros = dict.fromkeys(PARTIES, 0)
    return {
        "schema_version": 1,
        "generated_from": "test",
        "n_draws": len(draws),
        "majority": 175,
        "total_seats": 349,
        "parties": PARTIES,
        "point_seats": dict(zip(PARTIES, draws[0], strict=True)),
        "median_seats": dict(zip(PARTIES, draws[0], strict=True)),
        "low_seats": zeros,
        "high_seats": zeros,
        "p_below_threshold": {party: 0.0 for party in PARTIES},
        "draws": draws,
    }


def _write(root: Path, document: dict[str, object]) -> None:
    (root / "site" / "data").mkdir(parents=True)
    (root / "site" / "data" / "seats.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    (root / "site" / "index.html").write_text(
        f"<html><body>\n{START}\n{END}\n{LIVE_START}\n{LIVE_END}\n"
        "<script>1</script></body></html>",
        encoding="utf-8",
    )


def test_build_site_inlines_the_draws_between_the_markers(tmp_path: Path) -> None:
    draws = [[26, 101, 24, 29, 18, 61, 24, 66], [24, 103, 25, 28, 0, 63, 25, 81]]
    _write(tmp_path, _document(draws))

    build_site(tmp_path)

    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "window.SEAT_DATA" in html
    payload = html.split("window.SEAT_DATA = ", 1)[1].split(";</script>", 1)[0]
    assert json.loads(payload)["draws"] == draws
    live_payload = html.split("window.VALU_LIVE_DATA = ", 1)[1].split(";\n</script>", 1)[0]
    assert json.loads(live_payload)["status"] == "unavailable"
    assert html.index(START) < html.index("<script>1</script>")


def test_build_site_is_idempotent(tmp_path: Path) -> None:
    _write(tmp_path, _document([[26, 101, 24, 29, 18, 61, 24, 66]]))

    build_site(tmp_path)
    once = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    build_site(tmp_path)

    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8") == once


def test_load_seat_data_rejects_a_draw_that_does_not_fill_the_chamber(tmp_path: Path) -> None:
    _write(tmp_path, _document([[26, 101, 24, 29, 18, 61, 24, 65]]))

    with pytest.raises(ValueError, match="sums to 348"):
        load_seat_data(tmp_path)


def test_load_seat_data_rejects_a_draw_with_the_wrong_party_count(tmp_path: Path) -> None:
    document = _document([[26, 101, 24, 29, 18, 61, 24, 66]])
    document["draws"] = [[26, 101, 24, 29, 18, 61, 24, 66, 0]]
    _write(tmp_path, document)

    with pytest.raises(ValueError, match="expected 8"):
        load_seat_data(tmp_path)


def test_the_published_page_carries_seat_data_for_every_party() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "site" / "index.html").read_text(encoding="utf-8")
    assert START in html and END in html
    block = html.split(START, 1)[1].split(END, 1)[0]
    if "window.SEAT_DATA" not in block:
        pytest.skip("seat data has not been built into the page yet")
    payload = json.loads(block.split("window.SEAT_DATA = ", 1)[1].rsplit(";</script>", 1)[0])
    assert payload["parties"] == PARTIES
    # The reader script runs at the end of the body, so the data has to come first.
    assert html.index(START) < html.rindex("<script>")
    for draw in payload["draws"]:
        assert sum(draw) == payload["total_seats"]


def _write_live_sources(root: Path) -> None:
    official = {
        "prediction": {
            "national": {
                party: {"point": 0.1, "low": 0.08, "high": 0.12} for party in PARTIES
            }
            | {"OTHER": {"point": 0.2, "low": 0.18, "high": 0.22}}
        }
    }
    summary = {
        "topline_gate": {
            "relative_improvement": 0.045,
            "cycles_won": 3,
            "cycles_scored": 3,
        },
        "combined_live_gate": {"cycles": [{"election_year": 2022}]},
    }
    (root / "forecast_snapshots").mkdir(parents=True)
    (root / "forecast_snapshots" / "official_forecast_2026.json").write_text(
        json.dumps(official), encoding="utf-8"
    )
    report = root / "reports" / "experiments" / "valu"
    report.mkdir(parents=True)
    (report / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def test_load_live_data_waits_without_inventing_valu(tmp_path: Path) -> None:
    _write_live_sources(tmp_path)

    document = load_live_data(tmp_path)

    assert document["status"] == "waiting_for_valu"
    assert document["live"] is None
    assert document["forecast"]["S"]["point"] == pytest.approx(0.1)


def test_load_live_data_exposes_a_verified_published_snapshot(tmp_path: Path) -> None:
    _write_live_sources(tmp_path)
    parties = [*PARTIES, "OTHER"]
    live = {
        "status": "valu_ready",
        "raw_valu": dict.fromkeys(parties, 0.1),
        "calibrated_valu": dict.fromkeys(parties, 0.1),
    }
    path = tmp_path / "reports" / "live"
    path.mkdir(parents=True)
    (path / "valu_2026.json").write_text(json.dumps(live), encoding="utf-8")

    document = load_live_data(tmp_path)

    assert document["status"] == "valu_ready"
    assert document["live"]["raw_valu"]["S"] == pytest.approx(0.1)


def test_load_live_data_rejects_an_incomplete_party_set(tmp_path: Path) -> None:
    _write_live_sources(tmp_path)
    live = {
        "status": "valu_ready",
        "raw_valu": {"S": 1.0},
        "calibrated_valu": {"S": 1.0},
    }
    path = tmp_path / "reports" / "live"
    path.mkdir(parents=True)
    (path / "valu_2026.json").write_text(json.dumps(live), encoding="utf-8")

    with pytest.raises(ValueError, match="party set"):
        load_live_data(tmp_path)
