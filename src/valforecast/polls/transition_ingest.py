from __future__ import annotations

import json
import re
from itertools import product
from pathlib import Path
from typing import Any, cast

import polars as pl
import pymupdf

from valforecast.features.election_history import PARTIES
from valforecast.polls.schema import POLL_PREVIOUS_CATEGORIES, validate_transition_cells

SCB_PARTY_CODES = {
    "m": "M",
    "c": "C",
    "l": "L",
    "kd": "KD",
    "mp": "MP",
    "s": "S",
    "v": "V",
    "SD": "SD",
    "övr": "OTHER",
    "blankt": "BLANK",
    "vet ej": "DONT_KNOW",
}

SCB_2018_CURRENT_LABELS = {
    "Moderaterna": "M",
    "Centerpartiet": "C",
    "Liberalerna": "L",
    "Kristdemokraterna": "KD",
    "Miljöpartiet": "MP",
    "Socialdemokraterna": "S",
    "Vänsterpartiet": "V",
    "Sverigedemokraterna": "SD",
    "Övriga partier": "OTHER",
    "Blankt": "BLANK",
    "Vet ej": "DONT_KNOW",
    "Uppgift saknas": "MISSING",
}
SCB_2018_PREVIOUS_COLUMN_INDEX = {
    "M": 1,
    "C": 2,
    "L": 3,
    "KD": 4,
    "MP": 5,
    "S": 6,
    "V": 7,
    "SD": 8,
    "OTHER": 9,
}
SCB_PREVIOUS_CODES = {
    **SCB_PARTY_CODES,
    "ej röstat": "DID_NOT_VOTE",
    "ej röstberättigad": "NOT_ELIGIBLE",
    "uppgift saknas": "MISSING",
}
SCB_PDF_CURRENT_LABEL_ALIASES = {
    "M": ("Moderaterna",),
    "C": ("Centerpartiet",),
    "L": ("Liberalerna", "Folkpartiet"),
    "KD": ("Kristdemokraterna",),
    "MP": ("Miljöpartiet",),
    "S": ("Socialdemokraterna",),
    "V": ("Vänsterpartiet",),
    "SD": ("Sverigedemokraterna",),
    "OTHER": ("Övriga partier",),
    "BLANK": ("Blankt",),
    "DONT_KNOW": ("Vet ej",),
    "MISSING": ("Uppgift saknas",),
}
SCB_PDF_PREVIOUS_COLUMN_INDEX = {
    "MISSING": 0,
    "M": 1,
    "C": 2,
    "L": 3,
    "KD": 4,
    "MP": 5,
    "S": 6,
    "V": 7,
    "SD": 8,
    "OTHER": 9,
    "DID_NOT_VOTE": 10,
    "NOT_ELIGIBLE": 11,
}
SCB_PDF_TABLE21_COLUMNS = 13
SCB_PDF_UNCERTAINTY_MARKERS = frozenset({"ost", "±", "+"})
SCB_PDF_PREVIOUS_ELECTION = re.compile(
    r"valt parti vid (?P<year>20\d{2}) års riksdagsval",
    re.IGNORECASE,
)


def read_pxweb_jsonstat(path: Path) -> pl.DataFrame:
    document = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    dimension_ids = [str(value) for value in document["id"]]
    sizes = [int(value) for value in document["size"]]
    dimensions = cast(dict[str, Any], document["dimension"])
    dimension_values: list[list[str]] = []
    for dimension_id, expected_size in zip(dimension_ids, sizes, strict=True):
        category = dimensions[dimension_id]["category"]
        index = category["index"]
        if isinstance(index, dict):
            ordered = [value for value, _ in sorted(index.items(), key=lambda item: int(item[1]))]
        else:
            ordered = [str(value) for value in index]
        if len(ordered) != expected_size:
            raise ValueError(f"JSON-stat size mismatch for {dimension_id}")
        dimension_values.append(ordered)
    values = document["value"]
    expected_cells = 1
    for size in sizes:
        expected_cells *= size
    if len(values) != expected_cells:
        raise ValueError("JSON-stat value count does not match dimensions")
    rows = []
    for position, categories in enumerate(product(*dimension_values)):
        raw_value = values[position]
        rows.append(
            {
                **dict(zip(dimension_ids, categories, strict=True)),
                "value": float(raw_value) if raw_value not in {None, ".."} else None,
            }
        )
    return pl.DataFrame(rows)


def extract_scb_2018_pdf_transition(
    path: Path,
    *,
    wave_id: str = "scb_2018M05_original",
) -> pl.DataFrame:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        page_text = document[98].get_text()
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    row_bases = _parse_scb_2018_row_bases(lines)
    rows = []
    search_from = 0
    for label, current_party in SCB_2018_CURRENT_LABELS.items():
        start = lines.index(label, search_from)
        if lines[start + 1] != "%":
            raise ValueError(f"Unexpected Table 21 layout after {label}")
        estimates = [_swedish_number(value) / 100 for value in lines[start + 2 : start + 15]]
        if lines[start + 15] != "ost":
            raise ValueError(f"Missing uncertainty row after {label}")
        margins = [_swedish_number(value) / 100 for value in lines[start + 16 : start + 29]]
        search_from = start + 29
        for previous_party, column_index in SCB_2018_PREVIOUS_COLUMN_INDEX.items():
            rows.append(
                {
                    "wave_id": wave_id,
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "estimate": estimates[column_index],
                    "margin_error": margins[column_index],
                    "row_base": row_bases[column_index],
                    "cell_status": "PUBLISHED",
                }
            )
    result = pl.DataFrame(rows).sort("previous_party", "current_party")
    validate_transition_cells(result)
    gold_cells = {
        ("M", "M"): 0.532,
        ("S", "S"): 0.569,
        ("SD", "SD"): 0.726,
    }
    for (previous, current), expected in gold_cells.items():
        value = result.filter(
            (pl.col("previous_party") == previous) & (pl.col("current_party") == current)
        ).item(0, "estimate")
        if not np_isclose(float(value), expected, tolerance=1e-12):
            raise ValueError(f"SCB 2018 gold cell changed: {previous}/{current}")
    column_sums = result.group_by("previous_party").agg(pl.col("estimate").sum())
    if column_sums.filter((pl.col("estimate") < 0.995) | (pl.col("estimate") > 1.005)).height:
        raise ValueError("SCB 2018 Table 21 columns do not sum to one")
    return result


def extract_scb_2018_pdf_national_poll(
    path: Path,
    *,
    wave_id: str = "scb_2018M05_original",
) -> pl.DataFrame:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        page_text = document[4].get_text()
    labels = {
        "C": "C",
        "L": "L",
        "M": "M",
        "KD": "KD",
        "S": "S",
        "V": "V",
        "MP": "MP",
        "SD": "SD",
        "Övr": "OTHER",
    }
    rows = []
    for label, party in labels.items():
        match = re.search(
            rf"\n{re.escape(label)}\s*\n(\d+,\d+)\s+±",
            page_text,
        )
        if match is None:
            raise ValueError(f"Could not extract 2018 national estimate for {label}")
        rows.append(
            {
                "wave_id": wave_id,
                "party": party,
                "poll_share": _swedish_number(match.group(1)) / 100,
            }
        )
    result = pl.DataFrame(rows).sort("party")
    if not np_isclose(float(result["poll_share"].sum()), 1.0, tolerance=0.002):
        raise ValueError("Extracted 2018 national poll does not sum to one")
    gold = dict(result.select("party", "poll_share").iter_rows())
    if any(
        not np_isclose(float(gold[party]), expected, tolerance=1e-12)
        for party, expected in {"S": 0.283, "M": 0.226, "SD": 0.185}.items()
    ):
        raise ValueError("Extracted 2018 national poll gold cells changed")
    return result


def extract_scb_pdf_table21(
    path: Path,
    *,
    wave_id: str,
) -> tuple[pl.DataFrame, int, int]:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        page_index = _find_scb_table21_page(document)
        page_text = document[page_index].get_text()
        if "Antal i urvalet" not in page_text and page_index + 1 < document.page_count:
            page_text += "\n" + document[page_index + 1].get_text()
    match = SCB_PDF_PREVIOUS_ELECTION.search(page_text)
    if match is None:
        raise ValueError(f"Could not read previous-election reference in {wave_id}")
    previous_election = int(match.group("year"))
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    cells = parse_scb_pdf_table21_lines(lines, wave_id=wave_id)
    return cells, previous_election, _parse_scb_pdf_row_bases(lines)[12]


def parse_scb_pdf_table21_lines(
    lines: list[str],
    *,
    wave_id: str,
) -> pl.DataFrame:
    row_bases = _parse_scb_pdf_row_bases(lines)
    rows = []
    search_from = 0
    for current_party, labels in SCB_PDF_CURRENT_LABEL_ALIASES.items():
        start, _label = _find_current_label(lines, labels, search_from, current_party)
        if lines[start + 1] != "%":
            raise ValueError(f"Unexpected Table 21 layout after {current_party}")
        estimates = [
            _swedish_number(value) / 100
            for value in lines[start + 2 : start + 2 + SCB_PDF_TABLE21_COLUMNS]
        ]
        if lines[start + 2 + SCB_PDF_TABLE21_COLUMNS] not in SCB_PDF_UNCERTAINTY_MARKERS:
            raise ValueError(f"Missing uncertainty row after {current_party}")
        margins = [
            _swedish_number(value) / 100
            for value in lines[
                start + 3 + SCB_PDF_TABLE21_COLUMNS : start + 3 + 2 * SCB_PDF_TABLE21_COLUMNS
            ]
        ]
        search_from = start + 3 + 2 * SCB_PDF_TABLE21_COLUMNS
        for previous_party, column_index in SCB_PDF_PREVIOUS_COLUMN_INDEX.items():
            rows.append(
                {
                    "wave_id": wave_id,
                    "previous_party": previous_party,
                    "current_party": current_party,
                    "estimate": estimates[column_index],
                    "margin_error": margins[column_index],
                    "row_base": row_bases[column_index],
                    "cell_status": "PUBLISHED",
                }
            )
    result = pl.DataFrame(rows).sort("previous_party", "current_party")
    validate_transition_cells(result)
    _validate_pdf_column_totals(result)
    return result


def _find_scb_table21_page(document: Any) -> int:
    for index, page in enumerate(document):
        text = page.get_text()
        if re.search(r"Tabell\s+21\b", text) and (
            "Väljarkåren fördelad" in text or "riksdagsval" in text.lower()
        ):
            return int(index)
    raise ValueError("Could not locate SCB Table 21")


def _find_current_label(
    lines: list[str],
    labels: tuple[str, ...],
    search_from: int,
    current_party: str,
) -> tuple[int, str]:
    for label in labels:
        try:
            return lines.index(label, search_from), label
        except ValueError:
            continue
    raise ValueError(f"Missing Table 21 current-intention row {current_party}")


def _parse_scb_pdf_row_bases(lines: list[str]) -> list[int]:
    start = lines.index("Antal i urvalet") + 1
    values: list[int] = []
    for value in lines[start:]:
        cleaned = value.replace(" ", "").replace("\xa0", "")
        if not cleaned.isdigit():
            break
        values.append(int(cleaned))
    if len(values) != SCB_PDF_TABLE21_COLUMNS:
        raise ValueError(
            f"Expected {SCB_PDF_TABLE21_COLUMNS} Table 21 row bases, got {len(values)}"
        )
    if sum(values[:12]) != values[12]:
        raise ValueError("Table 21 row bases do not partition the gross sample")
    return values


def _validate_pdf_column_totals(frame: pl.DataFrame) -> None:
    totals = frame.group_by("previous_party").agg(
        pl.col("estimate").sum().alias("estimate"),
        pl.col("row_base").max().alias("row_base"),
    )
    empty = totals.filter((pl.col("row_base") == 0) & (pl.col("estimate") != 0))
    if empty.height:
        raise ValueError("Empty Table 21 previous-vote columns must be zero")
    nonempty = totals.filter(pl.col("row_base") > 0)
    if nonempty.filter((pl.col("estimate") < 0.995) | (pl.col("estimate") > 1.005)).height:
        raise ValueError("SCB Table 21 columns do not sum to one")


def _parse_scb_2018_row_bases(lines: list[str]) -> list[int]:
    start = lines.index("Antal i urvalet") + 1
    values = [int(value.replace(" ", "")) for value in lines[start : start + 13]]
    if len(values) != 13 or values[-1] != 8951:
        raise ValueError("Could not validate SCB 2018 Table 21 row bases")
    return values


def _swedish_number(value: str) -> float:
    return float(value.replace(" ", "").replace(",", "."))


def np_isclose(left: float, right: float, *, tolerance: float) -> bool:
    return abs(left - right) <= tolerance


def extract_scb_transition_cells(
    frame: pl.DataFrame,
    *,
    wave_id: str,
    time_value: str,
) -> pl.DataFrame:
    required = {"PvalRV", "Pvalnu", "ContentsCode", "Tid", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing SCB transition dimensions: {sorted(missing)}")
    selected = (
        frame.filter(pl.col("Tid") == time_value)
        .with_columns(
            pl.col("PvalRV").replace_strict(SCB_PARTY_CODES, default=None).alias("previous_party"),
            pl.col("Pvalnu").replace_strict(SCB_PARTY_CODES, default=None).alias("current_party"),
        )
        .filter(pl.col("previous_party").is_in(PARTIES) & pl.col("current_party").is_not_null())
    )
    wide = selected.pivot(
        on="ContentsCode",
        index=["previous_party", "current_party"],
        values="value",
    )
    for column in ("000001IX", "000001IW", "000001IV"):
        if column not in wide.columns:
            wide = wide.with_columns(pl.lit(None).cast(pl.Float64).alias(column))
    result = wide.select(
        pl.lit(wave_id).alias("wave_id"),
        "previous_party",
        "current_party",
        (pl.col("000001IX") / 100).alias("estimate"),
        (pl.col("000001IW") / 100).alias("margin_error"),
        pl.col("000001IV").cast(pl.Int64, strict=False).alias("row_base"),
        pl.when(pl.col("000001IX").is_null())
        .then(pl.lit("SUPPRESSED"))
        .otherwise(pl.lit("PUBLISHED"))
        .alias("cell_status"),
    ).sort("previous_party", "current_party")
    validate_transition_cells(result)
    return result


def extract_scb_corpus_transition_cells(
    frame: pl.DataFrame,
    *,
    wave_id: str,
    time_value: str,
) -> pl.DataFrame:
    required = {"PvalRV", "Pvalnu", "ContentsCode", "Tid", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing SCB transition dimensions: {sorted(missing)}")
    selected = (
        frame.filter(pl.col("Tid") == time_value)
        .with_columns(
            pl.col("PvalRV")
            .replace_strict(SCB_PREVIOUS_CODES, default=None)
            .alias("previous_party"),
            pl.col("Pvalnu").replace_strict(SCB_PARTY_CODES, default=None).alias("current_party"),
        )
        .filter(
            pl.col("previous_party").is_in(POLL_PREVIOUS_CATEGORIES)
            & pl.col("current_party").is_not_null()
        )
    )
    wide = selected.pivot(
        on="ContentsCode",
        index=["previous_party", "current_party"],
        values="value",
    )
    for column in ("000001IX", "000001IW", "000001IV"):
        if column not in wide.columns:
            wide = wide.with_columns(pl.lit(None).cast(pl.Float64).alias(column))
    result = wide.select(
        pl.lit(wave_id).alias("wave_id"),
        "previous_party",
        "current_party",
        (pl.col("000001IX") / 100).alias("estimate"),
        (pl.col("000001IW") / 100).alias("margin_error"),
        pl.col("000001IV").cast(pl.Int64, strict=False).alias("row_base"),
        pl.when(pl.col("000001IX").is_null())
        .then(pl.lit("SUPPRESSED"))
        .otherwise(pl.lit("PUBLISHED"))
        .alias("cell_status"),
    ).sort("previous_party", "current_party")
    validate_transition_cells(result)
    return result


def validate_scb_historical_pxweb(
    frame: pl.DataFrame,
    *,
    expected_times: set[str],
) -> None:
    expected_previous = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "ej röstat",
        "ej röstberättigad",
        "uppgift saknas",
        "hela väljarkåren",
    }
    expected_current = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "blankt",
        "vet ej",
    }
    expected_contents = {"000001IX", "000001IW", "000001IV"}
    if set(frame["Tid"].unique()) != expected_times:
        raise ValueError("SCB historical source does not contain the locked 2018M11–2021M11 waves")
    if set(frame["PvalRV"].unique()) != expected_previous:
        raise ValueError("SCB source has an unexpected previous-vote code set")
    if set(frame["Pvalnu"].unique()) != expected_current:
        raise ValueError("SCB source has an unexpected current-vote code set")
    if set(frame["ContentsCode"].unique()) != expected_contents:
        raise ValueError("SCB source has an unexpected content code set")


def validate_scb_2022_wave(frame: pl.DataFrame) -> None:
    expected_previous = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "ej röstat",
        "ej röstberättigad",
        "uppgift saknas",
        "hela väljarkåren",
    }
    expected_current = {
        "m",
        "c",
        "l",
        "kd",
        "mp",
        "s",
        "v",
        "SD",
        "övr",
        "blankt",
        "vet ej",
    }
    expected_contents = {"000001IX", "000001IW", "000001IV"}
    if set(frame["Tid"].unique()) != {"2022M05"}:
        raise ValueError("SCB source must contain only the strict 2022M05 wave")
    if set(frame["PvalRV"].unique()) != expected_previous:
        raise ValueError("SCB source has an unexpected previous-vote code set")
    if set(frame["Pvalnu"].unique()) != expected_current:
        raise ValueError("SCB source has an unexpected current-vote code set")
    if set(frame["ContentsCode"].unique()) != expected_contents:
        raise ValueError("SCB source has an unexpected content code set")

    bases = (
        frame.filter(pl.col("ContentsCode") == "000001IV")
        .group_by("PvalRV")
        .agg(
            pl.col("value").drop_nulls().n_unique().alias("unique_bases"),
            pl.col("value").drop_nulls().first().alias("row_base"),
        )
    )
    if bases.filter(pl.col("unique_bases") != 1).height:
        raise ValueError("SCB bastal is not constant within previous-vote row")
    by_group = dict(bases.select("PvalRV", "row_base").iter_rows())
    partition = expected_previous - {"hela väljarkåren"}
    if int(sum(float(by_group[group]) for group in partition)) != 4274:
        raise ValueError("SCB previous-vote row bases do not partition respondents")
    if int(float(by_group["hela väljarkåren"])) != 4274:
        raise ValueError("SCB whole-electorate base is not 4,274")

    gold_cells = {
        ("s", "s", "000001IX"): 75.1,
        ("hela väljarkåren", "s", "000001IX"): 26.8,
        ("hela väljarkåren", "blankt", "000001IX"): 5.5,
        ("hela väljarkåren", "vet ej", "000001IX"): 14.6,
    }
    for (previous, current, contents), expected in gold_cells.items():
        value = frame.filter(
            (pl.col("PvalRV") == previous)
            & (pl.col("Pvalnu") == current)
            & (pl.col("ContentsCode") == contents)
        ).item(0, "value")
        if value != expected:
            raise ValueError(f"SCB gold cell changed: {previous}/{current}")


def extract_scb_national_poll(
    frame: pl.DataFrame,
    *,
    wave_id: str,
    time_value: str,
) -> pl.DataFrame:
    required = {"Parti", "ContentsCode", "Tid", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing SCB national poll dimensions: {sorted(missing)}")
    result = (
        frame.filter((pl.col("Tid") == time_value) & (pl.col("ContentsCode") == "ME0201B1"))
        .with_columns(pl.col("Parti").replace_strict(SCB_PARTY_CODES, default=None).alias("party"))
        .filter(pl.col("party").is_in(PARTIES))
        .select(
            pl.lit(wave_id).alias("wave_id"),
            "party",
            (pl.col("value") / 100).alias("poll_share"),
        )
        .sort("party")
    )
    if result["party"].n_unique() != len(PARTIES):
        missing_parties = set(PARTIES) - set(result["party"])
        raise ValueError(f"National poll is missing parties: {sorted(missing_parties)}")
    total = float(result["poll_share"].sum())
    if not 0.999 <= total <= 1.001:
        raise ValueError(f"National poll shares sum to {total}")
    return result


def read_extracted_transition_csv(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path, try_parse_dates=True).with_columns(
        pl.col("estimate").cast(pl.Float64),
        pl.col("margin_error").cast(pl.Float64),
        pl.col("row_base").cast(pl.Int64),
    )
    validate_transition_cells(frame)
    return frame
