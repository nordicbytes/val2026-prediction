from __future__ import annotations

import re
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, cast

import polars as pl

from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS, REGION_LABELS
from valforecast.polls.schema import PollWave
from valforecast.polls.transition_ingest import read_pxweb_jsonstat

NEWS_PARTY_ORDER = ("C", "L", "M", "KD", "S", "V", "MP", "SD", "OTHER")
SCB_PARTY_CODES = {
    "c": "C",
    "l": "L",
    "m": "M",
    "kd": "KD",
    "s": "S",
    "v": "V",
    "mp": "MP",
    "SD": "SD",
    "övr": "OTHER",
}
REGION_LABEL_TO_ID = {label: region_id for region_id, label in REGION_LABELS.items()}
REGION_LABEL_TO_ID["Totalt"] = "Z01"
SUPPRESSED_MARKERS = frozenset({"..", ".", "…", "…."})
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
NUMBER_RE = re.compile(r"^-?\d+(?:,\d+)?$")

GoldCell = tuple[float | None, float | None]
GOLD_CELLS: dict[int, dict[tuple[str, str], GoldCell]] = {
    2018: {
        ("SE2", "C"): (8.5, 1.6),
        ("0180", "S"): (23.6, 2.9),
        ("Z01", "S"): (28.3, 0.8),
    },
    2022: {
        ("SE2", "S"): (32.9, 4.1),
        ("SE09", "OTHER"): (None, None),
        ("Z01", "S"): (33.3, 1.3),
    },
}

REGIONAL_WAVES = {
    2018: PollWave(
        wave_id="scb_2018M05_regional_original",
        election_cycle=2018,
        pollster="SCB",
        fieldwork_start=date(2018, 4, 27),
        fieldwork_end=date(2018, 5, 29),
        publication_date=date(2018, 6, 5),
        forecast_cutoff=date(2018, 6, 11),
        information_level="MARGINAL_ONLY",
        sample_size=8951,
    ),
    2022: PollWave(
        wave_id="scb_2022M05_regional_original",
        election_cycle=2022,
        pollster="SCB",
        fieldwork_start=date(2022, 4, 28),
        fieldwork_end=date(2022, 5, 25),
        publication_date=date(2022, 6, 2),
        forecast_cutoff=date(2022, 6, 8),
        information_level="MARGINAL_ONLY",
        sample_size=4274,
    ),
}


def extract_scb_regional_news(
    path: Path,
    *,
    election_cycle: int,
) -> pl.DataFrame:
    wave = REGIONAL_WAVES[election_cycle]
    wave.validate_strict_cutoff()
    html = unescape(path.read_text(encoding="utf-8"))
    cells = _parse_regional_news_html(html, wave_id=wave.wave_id)
    validate_regional_gold_cells(cells, election_cycle)
    validate_regional_cells(cells)
    return cells


def extract_scb_vid12(
    frame: pl.DataFrame,
    *,
    wave_id: str,
    time_value: str,
) -> pl.DataFrame:
    if time_value == "2018M05":
        raise ValueError("Live Vid12 2018M05 is the revised series and is forbidden")
    required = {"Region", "Parti", "ContentsCode", "Tid", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing Vid12 dimensions: {sorted(missing)}")
    selected = frame.filter(pl.col("Tid") == time_value)
    if selected.is_empty():
        raise ValueError(f"Vid12 extract does not contain {time_value}")
    rows: list[dict[str, object]] = []
    for region_id in (*REGION_IDS, "Z01"):
        region = selected.filter(pl.col("Region") == region_id)
        if region.is_empty():
            raise ValueError(f"Vid12 extract is missing region {region_id}")
        for party_code, party in SCB_PARTY_CODES.items():
            percent = _vid12_value(region, party_code, "000001YF")
            margin = _vid12_value(region, party_code, "000001YE")
            rows.append(
                {
                    "wave_id": wave_id,
                    "region_id": region_id,
                    "party": party,
                    "estimate": None if percent is None else percent / 100,
                    "margin_error": None if margin is None else margin / 100,
                    "row_base": None,
                    "cell_status": "SUPPRESSED" if percent is None else "PUBLISHED",
                }
            )
    result = pl.DataFrame(rows).sort("region_id", "party")
    validate_regional_cells(result)
    return result


def validate_regional_gold_cells(cells: pl.DataFrame, election_cycle: int) -> None:
    expected = GOLD_CELLS[election_cycle]
    by_cell = {
        (str(region_id), str(party)): (estimate, margin)
        for region_id, party, estimate, margin in cells.select(
            "region_id", "party", "estimate", "margin_error"
        ).iter_rows()
    }
    for key, (estimate, margin) in expected.items():
        if key not in by_cell:
            raise ValueError(f"Regional gold cell missing: {key}")
        observed_estimate, observed_margin = by_cell[key]
        if estimate is None:
            if observed_estimate is not None or observed_margin is not None:
                raise ValueError(f"Regional gold cell should be suppressed: {key}")
            continue
        if observed_estimate is None or observed_margin is None or margin is None:
            raise ValueError(f"Regional gold cell is unexpectedly suppressed: {key}")
        if not (
            abs(float(observed_estimate) * 100 - float(estimate)) < 1e-9
            and abs(float(observed_margin) * 100 - float(margin)) < 1e-9
        ):
            raise ValueError(f"Regional gold cell changed: {key}")


def validate_regional_cells(cells: pl.DataFrame) -> None:
    required = {
        "wave_id",
        "region_id",
        "party",
        "estimate",
        "margin_error",
        "row_base",
        "cell_status",
    }
    missing = required - set(cells.columns)
    if missing:
        raise ValueError(f"Missing regional poll columns: {sorted(missing)}")
    unknown_regions = set(cells["region_id"].unique()) - set((*REGION_IDS, "Z01"))
    if unknown_regions:
        raise ValueError(f"Unexpected regional codes: {sorted(unknown_regions)}")
    unknown_parties = set(cells["party"].unique()) - set(PARTIES)
    if unknown_parties:
        raise ValueError(f"Unexpected regional parties: {sorted(unknown_parties)}")
    estimates = cells["estimate"].drop_nulls()
    if estimates.len() and ((estimates < 0).any() or (estimates > 1).any()):
        raise ValueError("Regional estimates must be probabilities")
    if cells.filter(
        (pl.col("cell_status") == "SUPPRESSED") & pl.col("estimate").is_not_null()
    ).height:
        raise ValueError("Suppressed regional cells must keep a null estimate")


def assert_news_matches_vid12(news: pl.DataFrame, vid12: pl.DataFrame) -> None:
    left = news.select("region_id", "party", "estimate", "margin_error", "cell_status")
    right = vid12.select("region_id", "party", "estimate", "margin_error", "cell_status")
    if not left.sort("region_id", "party").equals(right.sort("region_id", "party")):
        raise ValueError("Pinned Vid12 extract does not match the original 2022 news table")


def load_target_regional_inputs(root: Path) -> dict[int, pl.DataFrame]:
    psu = root / "data" / "raw" / "scb" / "psu"
    news_2018 = extract_scb_regional_news(
        psu / "regional_val_idag_2018_06_05.html",
        election_cycle=2018,
    )
    news_2022 = extract_scb_regional_news(
        psu / "regional_val_idag_2022_06_02.html",
        election_cycle=2022,
    )
    vid12 = extract_scb_vid12(
        read_pxweb_jsonstat(psu / "regional_poll_2022M05_vid12.json"),
        wave_id="scb_2022M05_regional_vid12",
        time_value="2022M05",
    )
    assert_news_matches_vid12(
        news_2022,
        vid12.with_columns(pl.lit(news_2022["wave_id"][0]).alias("wave_id")),
    )
    return {2018: news_2018, 2022: news_2022}


def _parse_regional_news_html(html: str, *, wave_id: str) -> pl.DataFrame:
    table = _regional_table_html(html)
    rows: list[dict[str, object]] = []
    for row_html in ROW_RE.findall(table):
        values = [_cell_text(cell) for cell in CELL_RE.findall(row_html)]
        if not values or values[0] not in REGION_LABEL_TO_ID:
            continue
        if len(values) != 1 + 2 * len(NEWS_PARTY_ORDER):
            raise ValueError(f"Unexpected regional table width for {values[0]}")
        region_id = REGION_LABEL_TO_ID[values[0]]
        for party_index, party in enumerate(NEWS_PARTY_ORDER):
            estimate = _parse_percent(values[1 + 2 * party_index])
            margin = _parse_percent(values[2 + 2 * party_index])
            suppressed = estimate is None or margin is None
            if suppressed and estimate is not None:
                raise ValueError(f"Region {region_id} has a published estimate without a margin")
            rows.append(
                {
                    "wave_id": wave_id,
                    "region_id": region_id,
                    "party": party,
                    "estimate": estimate,
                    "margin_error": None if suppressed else margin,
                    "row_base": None,
                    "cell_status": "SUPPRESSED" if suppressed else "PUBLISHED",
                }
            )
    result = pl.DataFrame(rows)
    expected_regions = set((*REGION_IDS, "Z01"))
    if set(result["region_id"].unique()) != expected_regions:
        raise ValueError("Regional news table does not contain the locked eight groups plus total")
    return result.sort("region_id", "party")


def _regional_table_html(html: str) -> str:
    start = html.find("Sydsverige")
    if start < 0:
        raise ValueError("Regional news page is missing the Sydsverige row")
    table_start = html.rfind("<table", 0, start)
    table_end = html.find("</table>", start)
    if table_start < 0 or table_end < 0:
        raise ValueError("Could not isolate the regional val-idag table")
    return html[table_start : table_end + len("</table>")]


def _cell_text(html: str) -> str:
    return TAG_RE.sub("", html).replace("\xa0", " ").replace("±", "").strip()


def _parse_percent(text: str) -> float | None:
    cleaned = text.strip()
    if cleaned in SUPPRESSED_MARKERS or cleaned == "":
        return None
    if not NUMBER_RE.match(cleaned):
        raise ValueError(f"Unreadable regional percent: {text}")
    return float(cleaned.replace(",", ".")) / 100


def _vid12_value(region: pl.DataFrame, party_code: str, contents: str) -> float | None:
    matched = region.filter(
        (pl.col("Parti") == party_code) & (pl.col("ContentsCode") == contents)
    )
    if matched.height != 1:
        raise ValueError(f"Vid12 cell is not unique: {party_code}/{contents}")
    value = matched.item(0, "value")
    return None if value is None else float(cast(Any, value))
