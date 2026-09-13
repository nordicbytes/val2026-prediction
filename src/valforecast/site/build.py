from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SEATS_JSON = Path("site/data/seats.json")
INDEX_HTML = Path("site/index.html")
START = "<!-- SEAT_DATA:START -->"
END = "<!-- SEAT_DATA:END -->"
LIVE_START = "<!-- LIVE_DATA:START -->"
LIVE_END = "<!-- LIVE_DATA:END -->"


def load_seat_data(root: Path) -> dict[str, Any]:
    path = root / SEATS_JSON
    if not path.exists():
        raise FileNotFoundError(f"Missing {SEATS_JSON}; run the seat simulation first")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    parties = document["parties"]
    draws = document["draws"]
    total = int(document["total_seats"])
    if len(draws) != int(document["n_draws"]):
        raise ValueError("n_draws does not match the number of rows in draws")
    for index, draw in enumerate(draws):
        if len(draw) != len(parties):
            raise ValueError(f"Draw {index} has {len(draw)} entries, expected {len(parties)}")
        if sum(draw) != total:
            raise ValueError(f"Draw {index} sums to {sum(draw)}, expected {total}")
    return document


def load_live_data(root: Path) -> dict[str, Any]:
    official_path = root / "forecast_snapshots" / "official_forecast_2026.json"
    valu_summary_path = root / "reports" / "experiments" / "valu" / "summary.json"
    live_path = root / "reports" / "live" / "valu_2026.json"
    if not official_path.exists() or not valu_summary_path.exists():
        return {
            "schema_version": 1,
            "status": "unavailable",
            "forecast": {},
            "live": None,
            "backtest": None,
        }

    official: dict[str, Any] = json.loads(official_path.read_text(encoding="utf-8"))
    valu_summary: dict[str, Any] = json.loads(
        valu_summary_path.read_text(encoding="utf-8")
    )
    national = official["prediction"]["national"]
    forecast = {
        party: {
            "point": float(values["point"]),
            "low": float(values["low"]),
            "high": float(values["high"]),
        }
        for party, values in national.items()
    }
    live: dict[str, Any] | None = None
    status = "waiting_for_valu"
    if live_path.exists():
        live = json.loads(live_path.read_text(encoding="utf-8"))
        if live.get("status") != "valu_ready":
            raise ValueError("Published live VALU file has an unexpected status")
        for field in ("raw_valu", "calibrated_valu"):
            if set(live[field]) != set(forecast):
                raise ValueError(
                    f"Live VALU party set in {field} differs from the frozen forecast"
                )
        status = "valu_ready"

    return {
        "schema_version": 1,
        "status": status,
        "forecast": forecast,
        "live": live,
        "backtest": {
            "topline_relative_improvement": float(
                valu_summary["topline_gate"]["relative_improvement"]
            ),
            "topline_cycles_won": int(valu_summary["topline_gate"]["cycles_won"]),
            "topline_cycles_scored": int(
                valu_summary["topline_gate"]["cycles_scored"]
            ),
            "combined_cycles": valu_summary["combined_live_gate"]["cycles"],
        },
    }


def build_site(root: Path) -> Path:
    """Inline the seat draws into the page so it works straight off the file system."""
    document = load_seat_data(root)
    path = root / INDEX_HTML
    html = path.read_text(encoding="utf-8")
    if START not in html or END not in html:
        raise ValueError(f"{INDEX_HTML} is missing the {START} / {END} markers")
    head, rest = html.split(START, 1)
    _, tail = rest.split(END, 1)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    block = f"{START}\n<script>window.SEAT_DATA = {payload};</script>\n{END}"
    html = head + block + tail
    if LIVE_START in html and LIVE_END in html:
        live = load_live_data(root)
        live_payload = json.dumps(live, ensure_ascii=False, separators=(",", ":"))
        live_head, live_rest = html.split(LIVE_START, 1)
        _, live_tail = live_rest.split(LIVE_END, 1)
        live_block = (
            f"{LIVE_START}\n"
            f"<script>window.VALU_LIVE_DATA = {live_payload};\n</script>\n"
            f"{LIVE_END}"
        )
        html = live_head + live_block + live_tail
    path.write_text(html, encoding="utf-8")
    return path
