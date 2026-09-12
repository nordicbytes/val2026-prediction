from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SEATS_JSON = Path("site/data/seats.json")
INDEX_HTML = Path("site/index.html")
START = "<!-- SEAT_DATA:START -->"
END = "<!-- SEAT_DATA:END -->"


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
    path.write_text(head + block + tail, encoding="utf-8")
    return path
