"""The house-effect tab hard-codes numbers, so guard them against drift.

``scripts/house_effect_sensitivity.py`` writes the sensitivity payload; the page
restates those figures as a JavaScript literal because it has to work with no
server. These tests fail if the two ever disagree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site" / "index.html"
PAYLOAD = ROOT / "reports" / "forecast_2026" / "house_effect_sensitivity.json"

# The page writes Övriga as "Ö"; the forecast calls the residual OTHER.
PAGE_KEY = {"OTHER": '"Ö"'}
SEAT_PARTIES = ("V", "S", "MP", "C", "L", "M", "KD", "SD")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def payload() -> dict[str, object]:
    if not PAYLOAD.exists():
        pytest.skip("the house-effect sensitivity has not been run")
    document: dict[str, object] = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    return document


def _block(page: str, start: str) -> str:
    """The text from `start` up to the line that closes its literal."""
    index = page.index(start)
    depth = 0
    for offset in range(index, len(page)):
        character = page[offset]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return page[index : offset + 1]
    raise AssertionError(f"Unbalanced braces after {start!r}")


def _number(block: str, party: str, field: str) -> float:
    key = re.escape(PAGE_KEY.get(party, party))
    match = re.search(rf"{key}:\s*\{{[^}}]*\b{field}:\s*(-?[\d.]+)", block)
    assert match, f"{party}.{field} is missing from the page"
    return float(match.group(1))


def test_the_adjusted_shares_on_the_page_match_the_computed_sensitivity(
    page: str, payload: dict
) -> None:
    block = _block(page, "const ADJUSTED_SHARES")
    for party, values in payload["adjusted"]["parties"].items():
        for field in ("point", "low", "high", "calLow", "calHigh"):
            assert _number(block, party, field) == pytest.approx(values[field], abs=5e-3), (
                f"{party}.{field} differs"
            )


def test_the_variant_seats_and_coalitions_on_the_page_match_the_computation(
    page: str, payload: dict
) -> None:
    for variant in ("official", "adjusted"):
        block = _block(page, f"      {variant}: {{")
        seats = re.search(r"seats: \{([^}]*)\}", block)
        assert seats, f"{variant} has no seat literal"
        parsed = dict(
            (key.strip(), int(value))
            for key, value in (item.split(":") for item in seats.group(1).split(","))
        )
        assert parsed == {party: payload[variant]["seats"][party] for party in SEAT_PARTIES}

        # The page shows a subset of the constellations the script computes.
        shown = re.search(r"coal: \{(.*?)\}", block, re.S)
        assert shown, f"{variant} has no coalition literal"
        pairs = re.findall(r'"([^"]+)":\s*([\d.]+)', shown.group(1))
        assert {name for name, _ in pairs} == {"S+V+MP+C", "M+KD+L+SD", "S+C+MP+L", "S+M"}
        for name, value in pairs:
            expected = payload[variant]["coalitionMajority"][name]
            assert float(value) == pytest.approx(expected, abs=1e-9), f"{variant} {name}"


def test_the_official_tab_repeats_the_published_threshold_probabilities(
    page: str, payload: dict
) -> None:
    """The official tab must agree with the seat data already inlined in the page."""
    block = _block(page, "      official: {")
    above = re.search(r"pAbove: \{([^}]*)\}", block)
    assert above
    parsed = dict(
        (key.strip(), float(value))
        for key, value in (item.split(":") for item in above.group(1).split(","))
    )
    seat_block = page.split("window.SEAT_DATA = ", 1)[1].rsplit(";</script>", 1)[0]
    inlined = json.loads(seat_block)["p_below_threshold"]
    for party, share in parsed.items():
        assert share == pytest.approx(1.0 - inlined[party], abs=1e-9), party
