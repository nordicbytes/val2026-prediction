from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from valforecast.features.election_history import PARTIES
from valforecast.geo.region_codebook import REGION_IDS, REGION_LABELS
from valforecast.polls.region_ingest import (
    NEWS_PARTY_ORDER,
    REGIONAL_WAVES,
    extract_scb_regional_news,
    extract_scb_vid12,
    validate_regional_gold_cells,
)
from valforecast.polls.schema import PollWave

TEMPLATE = """
<html><body>
<table class="defaulttable">
<thead>
<tr><th>Parti</th><th>C</th><th>L</th><th>M</th><th>KD</th><th>S</th><th>V</th><th>MP</th><th>SD</th><th>Övr</th></tr>
<tr><th></th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th><th>%</th><th>±</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</body></html>
"""


def _row(label: str, values: list[tuple[str, str]]) -> str:
    cells = "".join(
        f"<td>{estimate}</td><td>±{margin}</td>" for estimate, margin in values
    )
    return f"<tr><td class='forspalt'>{label}</td>{cells}</tr>"


def _write_2018(path: Path) -> Path:
    published = [
        ("8,5", "1,6"),
        ("5,2", "1,2"),
        ("22,2", "2,0"),
        ("1,5", "0,7"),
        ("27,6", "2,3"),
        ("4,5", "0,9"),
        ("5,3", "1,0"),
        ("22,1", "1,9"),
        ("3,2", "1,1"),
    ]
    rows = []
    for region_id, label in REGION_LABELS.items():
        values = list(published)
        if region_id == "0180":
            values[4] = ("23,6", "2,9")
        rows.append(_row(label, values))
    rows.append(
        _row(
            "Totalt",
            [
                ("8,7", "0,5"),
                ("4,4", "0,4"),
                ("22,6", "0,8"),
                ("2,9", "0,3"),
                ("28,3", "0,8"),
                ("7,4", "0,5"),
                ("4,3", "0,4"),
                ("18,5", "0,8"),
                ("2,9", "0,4"),
            ],
        )
    )
    path.write_text(TEMPLATE.format(rows="\n".join(rows)), encoding="utf-8")
    return path


def _write_2022(path: Path) -> Path:
    published = [
        ("6,0", "2,2"),
        ("3,2", "1,3"),
        ("22,8", "3,6"),
        ("3,2", "1,4"),
        ("32,9", "4,1"),
        ("7,5", "2,3"),
        ("2,6", "1,3"),
        ("19,6", "3,5"),
        ("2,3", "1,4"),
    ]
    rows = []
    for region_id, label in REGION_LABELS.items():
        values = list(published)
        if region_id == "SE09":
            values[8] = ("..", "..")
        rows.append(_row(label, values))
    total = list(published)
    total[4] = ("33,3", "1,3")
    rows.append(_row("Totalt", total))
    path.write_text(TEMPLATE.format(rows="\n".join(rows)), encoding="utf-8")
    return path


def test_news_parser_keeps_gold_cells_and_party_order(tmp_path: Path) -> None:
    cells = extract_scb_regional_news(_write_2018(tmp_path / "news.html"), election_cycle=2018)
    validate_regional_gold_cells(cells, 2018)
    assert set(cells["region_id"].unique()) == set((*REGION_IDS, "Z01"))
    assert set(cells["party"].unique()) == set(PARTIES)
    syd_c = cells.filter((pl.col("region_id") == "SE2") & (pl.col("party") == "C"))
    assert syd_c.item(0, "estimate") == pytest.approx(0.085)
    assert NEWS_PARTY_ORDER[0] == "C"


def test_suppressed_cells_remain_explicit_nulls(tmp_path: Path) -> None:
    cells = extract_scb_regional_news(_write_2022(tmp_path / "news.html"), election_cycle=2022)
    other = cells.filter((pl.col("region_id") == "SE09") & (pl.col("party") == "OTHER"))
    assert other.item(0, "estimate") is None
    assert other.item(0, "margin_error") is None
    assert other.item(0, "cell_status") == "SUPPRESSED"
    validate_regional_gold_cells(cells, 2022)


def test_vid12_rejects_revised_2018_wave() -> None:
    frame = pl.DataFrame(
        {
            "Region": ["SE2"],
            "Parti": ["s"],
            "ContentsCode": ["000001YF"],
            "Tid": ["2018M05"],
            "value": [27.6],
        }
    )
    with pytest.raises(ValueError, match="revised"):
        extract_scb_vid12(frame, wave_id="bad", time_value="2018M05")


def test_regional_waves_respect_forecast_cutoffs() -> None:
    for wave in REGIONAL_WAVES.values():
        wave.validate_strict_cutoff()
    late = PollWave(
        wave_id="late",
        election_cycle=2018,
        pollster="SCB",
        fieldwork_start=date(2018, 4, 27),
        fieldwork_end=date(2018, 5, 29),
        publication_date=date(2018, 6, 20),
        forecast_cutoff=date(2018, 6, 11),
        information_level="MARGINAL_ONLY",
        sample_size=1,
    )
    with pytest.raises(ValueError, match="after forecast cutoff"):
        late.validate_strict_cutoff()
