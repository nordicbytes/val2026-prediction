from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymupdf

from valforecast.config import Source, load_sources
from valforecast.features.election_history import PARTIES
from valforecast.forecast.contract import ForecastContract
from valforecast.ingest.fetch import sha256_file

STOCKHOLM = ZoneInfo("Europe/Stockholm")
SWEDISH_NUMBER = re.compile(r"(-?\d+(?:[.,]\d+)?)")
PARTY_LINE = {
    "S": "S",
    "V": "V",
    "C": "C",
    "MP": "MP",
    "SD": "SD",
    "M": "M",
    "KD": "KD",
    "L": "L",
    "Övriga": "OTHER",
}
TV4_LABELS = {
    "M": "M",
    "L": "L",
    "C": "C",
    "KD": "KD",
    "KD:": "KD",
    "S": "S",
    "V": "V",
    "MP": "MP",
    "SD": "SD",
    "ÖVRIGA": "OTHER",
}
OMNI_NAME_LABELS = {
    "Vänsterpartiet": "V",
    "Socialdemokraterna": "S",
    "Miljöpartiet": "MP",
    "Centerpartiet": "C",
    "Liberalerna": "L",
    "Moderaterna": "M",
    "Kristdemokraterna": "KD",
    "Sverigedemokraterna": "SD",
    "Övriga": "OTHER",
    "ÖVR": "OTHER",
}
DEMOSKOP_EARLY_CHART_SHA256 = "5bf1d6a851ed28589a64fe5dab6934fecac6f926aa5a4cee84293655ac587fb1"
DEMOSKOP_EARLY_SHARES = {
    "V": 0.079,
    "S": 0.272,
    "MP": 0.069,
    "C": 0.086,
    "L": 0.028,
    "M": 0.166,
    "KD": 0.079,
    "SD": 0.200,
    "OTHER": 0.021,
}
NAMED_PARTIES = PARTIES[:-1]
PRODUCTION_SOURCE_CLASSES = frozenset(
    {
        "static_primary_document",
        "pollster_or_commissioner_original",
        "established_media_table_crosschecked",
    }
)
SAME_DAY_WITHOUT_CLOCK = frozenset(
    {
        "static_primary_document",
        "pollster_or_commissioner_original",
    }
)
EXPRESSEN_INDIKATOR_LABELS = (
    ("S", "S"),
    ("V", "V"),
    ("MP", "MP"),
    ("C", "C"),
    ("L", "L"),
    ("M", "M"),
    ("KD", "KD"),
    ("SD", "SD"),
    ("Övriga", "OTHER"),
)
PLACERA_DEMOSKOP_FINAL_NAMED = (
    "V",
    "S",
    "MP",
    "C",
    "L",
    "KD",
    "M",
    "SD",
)
PLACERA_DEMOSKOP_FINAL_ROW = re.compile(
    r"September\s+7,5\s+29,7\s+5,6\s+7,7\s+4,7\s+7,6\s+16,7\s+19,0"
)


@dataclass(frozen=True)
class NationalPoll:
    poll_id: str
    pollster: str
    commissioner: str
    publication_date: date
    publication_datetime: datetime | None
    fieldwork_start: date
    fieldwork_end: date
    sample_size: int | None
    sample_size_basis: str
    method: str
    shares: dict[str, float]
    url: str
    retrieved_at: str
    raw_file: str
    sha256: str
    included: bool
    exclusion_reason: str | None
    source_class: str
    other_origin: str
    verification_tier: str | None

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["publication_date"] = self.publication_date.isoformat()
        payload["fieldwork_start"] = self.fieldwork_start.isoformat()
        payload["fieldwork_end"] = self.fieldwork_end.isoformat()
        payload["publication_datetime"] = (
            self.publication_datetime.isoformat() if self.publication_datetime else None
        )
        return payload


def parse_swedish_number(text: str) -> float:
    match = SWEDISH_NUMBER.search(text.replace("\xa0", " ").replace(" ", ""))
    if match is None:
        raise ValueError(f"No number in: {text}")
    return float(match.group(1).replace(",", "."))


def normalize_party_shares(
    raw_shares: dict[str, float],
    *,
    tolerance: float,
) -> dict[str, float]:
    shares = {party: float(raw_shares.get(party, float("nan"))) for party in PARTIES}
    missing_named = [party for party in PARTIES[:-1] if party not in raw_shares]
    if missing_named:
        raise ValueError(f"Missing named parties: {missing_named}")
    named_sum = sum(shares[party] for party in PARTIES[:-1])
    if "OTHER" not in raw_shares:
        residual = 1.0 - named_sum
        if abs(residual) > tolerance:
            raise ValueError("OTHER is missing and named shares do not sum to one")
        shares["OTHER"] = max(residual, 0.0)
    total = sum(shares[party] for party in PARTIES)
    if abs(total - 1.0) > tolerance:
        raise ValueError(f"Party shares sum to {total}")
    if abs(total - 1.0) <= tolerance and "OTHER" in raw_shares:
        shares["OTHER"] = max(shares["OTHER"] + (1.0 - total), 0.0)
        total = sum(shares[party] for party in PARTIES)
    return {party: shares[party] / total for party in PARTIES}


def derive_other_residual(
    named: dict[str, float],
    *,
    named_sum_min: float,
    named_sum_max: float,
) -> dict[str, float]:
    missing = [party for party in NAMED_PARTIES if party not in named]
    if missing:
        raise ValueError(f"Cannot derive OTHER residual; missing named parties: {missing}")
    if "OTHER" in named:
        raise ValueError("OTHER is already published; residual derivation is not allowed")
    named_sum = sum(float(named[party]) for party in NAMED_PARTIES)
    if named_sum < named_sum_min or named_sum > named_sum_max:
        raise ValueError(
            f"Named share sum {named_sum} is outside the DERIVED_RESIDUAL window "
            f"[{named_sum_min}, {named_sum_max}]"
        )
    residual = 1.0 - named_sum
    if residual < 0:
        raise ValueError("Derived OTHER residual is negative")
    return {party: float(named[party]) for party in NAMED_PARTIES} | {"OTHER": residual}


def require_pinned_needles(path: Path, needles: tuple[str, ...]) -> str:
    text = path.read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise ValueError(f"{path.name} is missing pinned needles: {missing}")
    return text


def published_before_cutoff(
    poll: NationalPoll,
    contract: ForecastContract,
) -> tuple[bool, str | None]:
    if poll.publication_datetime is not None:
        published = poll.publication_datetime
        if published.tzinfo is None:
            published = published.replace(tzinfo=STOCKHOLM)
        if published > contract.cutoff:
            return False, "published_after_cutoff"
        return True, None
    if poll.publication_date > contract.cutoff.date():
        return False, "published_after_cutoff"
    if (
        poll.publication_date == contract.cutoff.date()
        and poll.source_class not in SAME_DAY_WITHOUT_CLOCK
    ):
        return False, "same_day_unknown_clock"
    return True, None


def extract_verian_pdf_shares(path: Path) -> dict[str, float]:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        lines = [line.strip() for page in document for line in page.get_text().splitlines()]
    shares: dict[str, float] = {}
    for index, line in enumerate(lines):
        party = PARTY_LINE.get(line)
        if party is None or party in shares:
            continue
        for candidate in lines[index + 1 : index + 4]:
            if SWEDISH_NUMBER.fullmatch(candidate.replace(" ", "")):
                shares[party] = parse_swedish_number(candidate) / 100.0
                break
        else:
            raise ValueError(f"Verian PDF is missing a share after {line}")
    if set(shares) != set(PARTIES):
        raise ValueError(f"Verian PDF party set is incomplete: {sorted(shares)}")
    return shares


def extract_novus_monthly_pdf_shares(path: Path) -> dict[str, float]:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        text = "\n".join(page.get_text() for page in document)
    patterns = {
        "M": r"Moderaterna har ett väljarstöd på ([\d,]+) procent",
        "L": r"Liberalerna har ett väljarstöd på ([\d,]+) procent",
        "C": r"Centerpartiet har ett väljarstöd på ([\d,]+) procent",
        "S": r"Socialdemokraterna minskar med .* till ([\d,]+) procent",
        "V": r"Vänsterpartiet har ett väljarstöd på ([\d,]+) procent",
        "MP": r"Miljöpartiet har ett väljarstöd på ([\d,]+) procent",
        "SD": r"Sverigedemokraterna har ett väljarstöd på ([\d,]+) procent",
        "OTHER": r"rösta på annat parti är ([\d,]+) procent",
    }
    shares = {party: parse_swedish_number(match.group(1)) / 100.0 for party, match in (
        (party, re.search(pattern, text)) for party, pattern in patterns.items()
    ) if match is not None}
    kd_match = re.search(
        r"Kristdemokraterna har ett väljarstöd på ([\d,]+)",
        text,
    )
    if kd_match is None:
        kd_match = re.search(r"Kristdemokraterna har ett väljarstöd på\s+([\d,]+)", text)
    if kd_match is None:
        raise ValueError("Novus monthly PDF is missing KD")
    shares["KD"] = parse_swedish_number(kd_match.group(1)) / 100.0
    if "S" not in shares:
        s_match = re.search(r"från 30,1 till ([\d,]+) procent", text)
        if s_match is None:
            raise ValueError("Novus monthly PDF is missing S")
        shares["S"] = parse_swedish_number(s_match.group(1)) / 100.0
    if set(shares) != set(PARTIES):
        raise ValueError(f"Novus monthly PDF party set is incomplete: {sorted(shares)}")
    return shares


def _walk_next_data(node: object, rows: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        node_type = node.get("nodeType")
        content = node.get("content")
        if node_type in {"Bold", "Text"} and isinstance(content, str):
            rows.append((str(node_type), content))
        for value in node.values():
            _walk_next_data(value, rows)
    elif isinstance(node, list):
        for value in node:
            _walk_next_data(value, rows)


def extract_tv4_novus_shares(path: Path, *, expected_s: float) -> tuple[dict[str, float], int]:
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if match is None:
        raise ValueError(f"TV4 page is missing NEXT_DATA: {path}")
    rows: list[tuple[str, str]] = []
    _walk_next_data(json.loads(match.group(1)), rows)
    blocks: list[dict[str, float]] = []
    current: dict[str, float] = {}
    sample_size = None
    for index, (kind, content) in enumerate(rows):
        if "Antal intervjuer" in content or "Totalt antal intervjuer" in content:
            sample_match = re.search(r"(\d[\d\s]*)", content.split("intervjuer")[-1])
            if sample_match is not None:
                sample_size = int(sample_match.group(1).replace(" ", ""))
        if kind != "Bold":
            continue
        label = content.strip().strip(":").upper()
        party = TV4_LABELS.get(label)
        if party is None or index + 1 >= len(rows):
            continue
        value = parse_swedish_number(rows[index + 1][1]) / 100.0
        if party in current and len(current) >= 8:
            blocks.append(current)
            current = {}
        if party not in current:
            current[party] = value
        if set(current) == set(PARTIES):
            blocks.append(current)
            current = {}
    for block in blocks:
        if abs(block.get("S", -1.0) - expected_s) < 1e-9:
            if sample_size is None:
                raise ValueError(f"TV4 page is missing sample size: {path}")
            return block, sample_size
    raise ValueError(f"TV4 page has no block with S={expected_s}")


def extract_demoskop_early_chart_shares(path: Path) -> dict[str, float]:
    digest = sha256_file(path)
    if digest != DEMOSKOP_EARLY_CHART_SHA256:
        raise ValueError("Demoskop early chart image checksum changed")
    return dict(DEMOSKOP_EARLY_SHARES)


def extract_omni_party_shares(path: Path, *, short_labels: bool) -> dict[str, float]:
    text = path.read_text(encoding="utf-8")
    shares: dict[str, float] = {}
    if short_labels:
        patterns = {
            "V": r">V:\s*([\d,]+)\s*procent",
            "S": r">S:\s*([\d,]+)\s*procent",
            "MP": r">MP:\s*([\d,]+)\s*procent",
            "C": r">C:\s*([\d,]+)\s*procent",
            "L": r">L:\s*([\d,]+)\s*procent",
            "M": r">M:\s*([\d,]+)\s*procent",
            "KD": r">KD:\s*([\d,]+)\s*procent",
            "SD": r">SD:\s*([\d,]+)\s*procent",
            "OTHER": r">ÖVR:\s*([\d,]+)\s*procent",
        }
    else:
        patterns = {
            party: rf">{re.escape(label)}:\s*([\d,]+)\s*procent"
            for label, party in OMNI_NAME_LABELS.items()
            if label not in {"ÖVR"}
        }
    for party, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            continue
        if party not in shares:
            shares[party] = parse_swedish_number(match.group(1)) / 100.0
    return shares


def extract_expressen_indikator_shares(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8")
    shares: dict[str, float] = {}
    for label, party in EXPRESSEN_INDIKATOR_LABELS:
        match = re.search(rf">{re.escape(label)}:\s*([\d,]+)</p>", text)
        if match is None:
            raise ValueError(f"Expressen Indikator table is missing {label}")
        shares[party] = parse_swedish_number(match.group(1)) / 100.0
    return shares


def extract_placera_demoskop_final_named(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8")
    match = PLACERA_DEMOSKOP_FINAL_ROW.search(text)
    if match is None:
        raise ValueError("Placera Demoskop final row is missing")
    numbers = re.findall(r"\d+,\d+", match.group(0))
    if len(numbers) != 8:
        raise ValueError("Placera Demoskop final row does not contain eight named shares")
    return {
        party: parse_swedish_number(number) / 100.0
        for party, number in zip(PLACERA_DEMOSKOP_FINAL_NAMED, numbers, strict=True)
    }


def parser_raw_files(root: Path) -> dict[str, str]:
    from valforecast.forecast.contract import load_forecast_contract

    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    files: dict[str, str] = {}
    for poll in load_forecast_polls(root, contract):
        relative = Path(poll.raw_file)
        files[f"poll:{poll.poll_id}"] = (
            relative.relative_to(root).as_posix() if relative.is_absolute() else relative.as_posix()
        )
    extras = {
        "poll_support:demoskop_2026_sep_early_html": (
            "data/raw/polls/demoskop_valjarbarometer_september_2026.html"
        ),
        "poll_support:indikator_sr_primary_html": (
            "data/raw/polls/indikator_opinion_sr_11-september-2026.html"
        ),
        "poll_support:dn_ipsos_primary_html": "data/raw/polls/dn_ipsos_11-september-2026.html",
        "poll_support:svd_demoskop_primary_html": (
            "data/raw/polls/svd_demoskop_11-september-2026.html"
        ),
        "poll_support:aftonbladet_demoskop_column_html": (
            "data/raw/polls/aftonbladet_demoskop_11-september-2026.html"
        ),
        "poll_support:placera_demoskop_telegram_html": (
            "data/raw/polls/placera_demoskop_11-september-2026.html"
        ),
        "poll_support:expressen_dn_ipsos_html": (
            "data/raw/polls/expressen_dn_ipsos_11-september-2026.html"
        ),
        "poll_support:expressen_indikator_html": (
            "data/raw/polls/expressen_indikator_11-september-2026.html"
        ),
        "poll_support:omni_dn_ipsos_html": "data/raw/polls/omni_dn_ipsos_11-september-2026.html",
        "poll_support:omni_demoskop_html": "data/raw/polls/omni_demoskop_11-september-2026.html",
    }
    files.update(extras)
    return files


def load_forecast_polls(root: Path, contract: ForecastContract) -> list[NationalPoll]:
    catalog = {
        source.raw_file: source
        for source in load_sources(root / "config" / "sources.yaml")
        if source.raw_file
    }
    return [
        _verian_monthly(root, contract, catalog),
        _verian_final(root, contract, catalog),
        _novus_monthly(root, contract, catalog),
        _novus_tv4_early(root, contract, catalog),
        _novus_tv4_late(root, contract, catalog),
        _demoskop_early(root, contract, catalog),
        _demoskop_final(root, contract, catalog),
        _dn_ipsos_final(root, contract, catalog),
        _indikator_final(root, contract, catalog),
        _omni_indikator(root, contract, catalog),
    ]


def _source_for(catalog: dict[str, Source], raw: Path, root: Path) -> Source:
    relative = raw.relative_to(root).as_posix()
    source = catalog.get(relative)
    if source is None or source.retrieved_at is None:
        raise ValueError(f"Missing per-file source metadata for {relative}")
    return source


def _verian_monthly(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/verian_valjarbarometer_september_2026.pdf"
    extracted = extract_verian_pdf_shares(raw)
    _assert_gold(extracted, {"S": 0.292, "L": 0.030, "OTHER": 0.028})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="verian_svt_2026_sep_monthly",
        pollster="Verian",
        commissioner="SVT",
        publication_date=date(2026, 9, 3),
        publication_datetime=datetime(2026, 9, 3, 0, 0, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 8, 20),
        fieldwork_end=date(2026, 9, 2),
        sample_size=3069,
        method="Webbpanel och telefon, vägt på kön, ålder, utbildning och partival 2022",
        shares=shares,
        url="https://www.veriangroup.com/hubfs/SE/Valjarbarometern/Verian-Valjarbarometer_September-2026.pdf",
        raw_file=raw,
        source_class="static_primary_document",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _verian_final(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/verian_valjarbarometer_4-10-september-2026.pdf"
    extracted = extract_verian_pdf_shares(raw)
    _assert_gold(extracted, {"S": 0.276, "L": 0.055, "OTHER": 0.027})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="verian_svt_2026_sep_final",
        pollster="Verian",
        commissioner="SVT",
        publication_date=date(2026, 9, 11),
        publication_datetime=None,
        fieldwork_start=date(2026, 9, 4),
        fieldwork_end=date(2026, 9, 10),
        sample_size=3063,
        method="Webbpanel och telefon, vägt på kön, ålder, utbildning och partival 2022",
        shares=shares,
        url="https://www.veriangroup.com/hubfs/SE/Valjarbarometern/Verian-Valjarbarometer_4-10-september-2026.pdf",
        raw_file=raw,
        source_class="static_primary_document",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _novus_monthly(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/novus_valjarbarometer_september_2026.pdf"
    extracted = extract_novus_monthly_pdf_shares(raw)
    _assert_gold(extracted, {"S": 0.258, "L": 0.033, "OTHER": 0.020})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="novus_monthly_2026_sep",
        pollster="Novus",
        commissioner="Novus",
        publication_date=date(2026, 9, 2),
        publication_datetime=datetime(2026, 9, 2, 0, 0, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 8, 24),
        fieldwork_end=date(2026, 8, 30),
        sample_size=2984,
        method="Slumpmässigt individurval, efterstratifierat på kön, ålder, utbildning och region",
        shares=shares,
        url="https://novus.se/wp-content/uploads/2026/09/novusvaljarbarometerseptember2026h3q8v5.pdf",
        raw_file=raw,
        source_class="static_primary_document",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _novus_tv4_early(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/tv4_novus_6-8-september-2026.html"
    shares_raw, sample_size = extract_tv4_novus_shares(raw, expected_s=0.266)
    _assert_gold(shares_raw, {"S": 0.266, "L": 0.058, "OTHER": 0.012})
    shares = normalize_party_shares(shares_raw, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="novus_tv4_2026_09_09",
        pollster="Novus",
        commissioner="TV4",
        publication_date=date(2026, 9, 9),
        publication_datetime=datetime(2026, 9, 9, 15, 0, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 6),
        fieldwork_end=date(2026, 9, 8),
        sample_size=sample_size,
        method="Novus/TV4, slumpmässigt individurval",
        shares=shares,
        url="https://www.tv4.se/artikel/6uiXoaoC9Mtryr1GbHjkgP/l-oever-spaerren-i-ny-novusmaetning-statistiskt-saekerstaellt",
        raw_file=raw,
        source_class="static_primary_document",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _novus_tv4_late(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/tv4_novus_7-10-september-2026.html"
    shares_raw, sample_size = extract_tv4_novus_shares(raw, expected_s=0.286)
    _assert_gold(shares_raw, {"S": 0.286, "L": 0.047, "OTHER": 0.014})
    shares = normalize_party_shares(shares_raw, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="novus_tv4_2026_09_11",
        pollster="Novus",
        commissioner="TV4",
        publication_date=date(2026, 9, 11),
        publication_datetime=datetime(2026, 9, 11, 14, 59, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 7),
        fieldwork_end=date(2026, 9, 10),
        sample_size=sample_size,
        method="Novus/TV4, slumpmässigt individurval",
        shares=shares,
        url="https://www.tv4.se/artikel/4lEtVYL5pNdnv2SXu42Fk9/efter-bensinbeskedet-mp-rasar-i-opinionen",
        raw_file=raw,
        source_class="static_primary_document",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _demoskop_early(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/images/demoskop_sep_early_bild1.jpg"
    extracted = extract_demoskop_early_chart_shares(raw)
    _assert_gold(extracted, {"S": 0.272, "L": 0.028, "OTHER": 0.021})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="demoskop_2026_sep_early",
        pollster="Demoskop",
        commissioner="Aftonbladet/SvD",
        publication_date=date(2026, 9, 3),
        publication_datetime=datetime(2026, 9, 3, 0, 0, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 8, 25),
        fieldwork_end=date(2026, 9, 1),
        sample_size=1998,
        method="Webbundersökning i Iniziopanelen, vägd på kön, ålder och region",
        shares=shares,
        url="https://demoskop.se/wp-content/uploads/2026/09/Bild1.jpg",
        raw_file=raw,
        source_class="static_primary_document",
        other_origin="published",
        verification_tier="a",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _dn_ipsos_final(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    table = root / "data/raw/polls/omni_dn_ipsos_11-september-2026.html"
    require_pinned_needles(
        root / "data/raw/polls/dn_ipsos_11-september-2026.html",
        ("DN/Ipsos", "Ipsos"),
    )
    require_pinned_needles(
        root / "data/raw/polls/expressen_dn_ipsos_11-september-2026.html",
        (
            "1 800",
            "7 och 10 september",
            "49,0",
            "49,3",
            "DN/Ipsos samlar partiet 5 procent",
            "Publicerad 11 sep 2026 kl 15.38",
        ),
    )
    named = extract_omni_party_shares(table, short_labels=False)
    _assert_gold(named, {"S": 0.274, "L": 0.050, "M": 0.183, "SD": 0.188, "V": 0.076})
    extracted = derive_other_residual(
        named,
        named_sum_min=contract.derived_other_named_sum_min,
        named_sum_max=contract.derived_other_named_sum_max,
    )
    _assert_gold(extracted, {"OTHER": 0.016})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="dn_ipsos_2026_09_11",
        pollster="Ipsos",
        commissioner="DN",
        publication_date=date(2026, 9, 11),
        publication_datetime=datetime(2026, 9, 11, 15, 38, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 7),
        fieldwork_end=date(2026, 9, 10),
        sample_size=1800,
        method="DN/Ipsos, 1 800 intervjuer 7–10 september. OTHER är DERIVED_RESIDUAL.",
        shares=shares,
        url="https://omni.se/dn-ipsos-dott-lopp-mellan-blocken-tva-dagar-innan-valet/a/Wv12vK",
        raw_file=table,
        source_class="established_media_table_crosschecked",
        other_origin="derived_residual",
        verification_tier="b",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _indikator_final(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    table = root / "data/raw/polls/expressen_indikator_11-september-2026.html"
    require_pinned_needles(
        root / "data/raw/polls/indikator_opinion_sr_11-september-2026.html",
        ("2189", "2026-09-02", "2026-09-10"),
    )
    require_pinned_needles(
        table,
        ("2 189", "2 september och 10 september", "S: 28,6", "Övriga: 1,6"),
    )
    extracted = extract_expressen_indikator_shares(table)
    _assert_gold(extracted, {"S": 0.286, "L": 0.054, "V": 0.078, "OTHER": 0.016})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="indikator_ekot_2026_09_11",
        pollster="Indikator Opinion",
        commissioner="Sveriges Radio Ekot",
        publication_date=date(2026, 9, 11),
        publication_datetime=datetime(2026, 9, 11, 8, 20, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 2),
        fieldwork_end=date(2026, 9, 10),
        sample_size=2189,
        method="Slumpmässigt urval, postal enkät, vägt på kön, ålder och partival 2022",
        shares=shares,
        url="https://www.expressen.se/nyheter/politik/l-klart-over-sparren-i-ny-matning-gapet-minskar-/",
        raw_file=table,
        source_class="established_media_table_crosschecked",
        other_origin="published",
        verification_tier="b",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _demoskop_final(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    table = root / "data/raw/polls/placera_demoskop_11-september-2026.html"
    require_pinned_needles(
        table,
        ("50,5", "48,0", "7-11 september"),
    )
    require_pinned_needles(
        root / "data/raw/polls/aftonbladet_demoskop_11-september-2026.html",
        ("29,7", "4,7 procent", "50,5"),
    )
    require_pinned_needles(
        root / "data/raw/polls/omni_demoskop_11-september-2026.html",
        ("1 419", "7 – 11 september"),
    )
    named = extract_placera_demoskop_final_named(table)
    _assert_gold(named, {"S": 0.297, "L": 0.047, "V": 0.075, "MP": 0.056, "SD": 0.190})
    extracted = derive_other_residual(
        named,
        named_sum_min=contract.derived_other_named_sum_min,
        named_sum_max=contract.derived_other_named_sum_max,
    )
    _assert_gold(extracted, {"OTHER": 0.015})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="demoskop_2026_09_11",
        pollster="Demoskop",
        commissioner="Aftonbladet/SvD",
        publication_date=date(2026, 9, 11),
        publication_datetime=datetime(2026, 9, 11, 15, 13, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 7),
        fieldwork_end=date(2026, 9, 11),
        sample_size=1419,
        method="Webbundersökning i Iniziopanelen. OTHER är DERIVED_RESIDUAL.",
        shares=shares,
        url="https://www.placera.se/telegram/valbarometer-gapet-krymper-s-vander-upp-mp-ned--demoskop-20260911",
        raw_file=table,
        source_class="established_media_table_crosschecked",
        other_origin="derived_residual",
        verification_tier="b",
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _omni_indikator(
    root: Path, contract: ForecastContract, catalog: dict[str, Source]
) -> NationalPoll:
    raw = root / "data/raw/polls/omni_indikator_11-september-2026.html"
    extracted = extract_omni_party_shares(raw, short_labels=True)
    _assert_gold(extracted, {"S": 0.286, "L": 0.054, "OTHER": 0.016, "V": 0.078})
    shares = normalize_party_shares(extracted, tolerance=contract.rounding_tolerance)
    return _poll(
        poll_id="indikator_ekot_2026_09_11_omni",
        pollster="Indikator Opinion",
        commissioner="Sveriges Radio Ekot",
        publication_date=date(2026, 9, 11),
        publication_datetime=datetime(2026, 9, 11, 8, 35, 51, tzinfo=STOCKHOLM),
        fieldwork_start=date(2026, 9, 2),
        fieldwork_end=date(2026, 9, 10),
        sample_size=2189,
        method="Slumpmässigt urval, postal enkät, vägt på kön, ålder och partival 2022",
        shares=shares,
        url="https://omni.se/l-ledaren-om-upphamtningen-ska-spurta-in-i-riksdagen/a/oEQWLB",
        raw_file=raw,
        source_class="secondary_news_table",
        other_origin="published",
        verification_tier=None,
        contract=contract,
        catalog=catalog,
        root=root,
    )


def _poll(
    *,
    poll_id: str,
    pollster: str,
    commissioner: str,
    publication_date: date,
    publication_datetime: datetime | None,
    fieldwork_start: date,
    fieldwork_end: date,
    sample_size: int | None,
    method: str,
    shares: dict[str, float],
    url: str,
    raw_file: Path,
    source_class: str,
    contract: ForecastContract,
    catalog: dict[str, Source],
    root: Path,
    other_origin: str = "published",
    verification_tier: str | None = "a",
) -> NationalPoll:
    source = _source_for(catalog, raw_file, root)
    if source.sha256 is not None and sha256_file(raw_file) != source.sha256:
        raise ValueError(f"Raw poll checksum does not match the source manifest: {poll_id}")
    poll = NationalPoll(
        poll_id=poll_id,
        pollster=pollster,
        commissioner=commissioner,
        publication_date=publication_date,
        publication_datetime=publication_datetime,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        sample_size=sample_size,
        sample_size_basis="RESPONDENTS",
        method=method,
        shares=shares,
        url=url,
        retrieved_at=source.retrieved_at or "",
        raw_file=str(raw_file),
        sha256=sha256_file(raw_file),
        included=True,
        exclusion_reason=None,
        source_class=source_class,
        other_origin=other_origin,
        verification_tier=verification_tier,
    )
    if source_class not in PRODUCTION_SOURCE_CLASSES:
        return NationalPoll(
            **{**asdict(poll), "included": False, "exclusion_reason": "secondary_source"}
        )
    allowed, reason = published_before_cutoff(poll, contract)
    if not allowed:
        return NationalPoll(**{**asdict(poll), "included": False, "exclusion_reason": reason})
    if poll.fieldwork_end < contract.earliest_fieldwork_end:
        return NationalPoll(
            **{**asdict(poll), "included": False, "exclusion_reason": "fieldwork_before_window"}
        )
    return poll


def _assert_gold(shares: dict[str, float], gold: dict[str, float]) -> None:
    for party, expected in gold.items():
        if abs(shares[party] - expected) > 5e-4:
            raise ValueError(f"Gold cell failed for {party}: {shares[party]} != {expected}")


def inventory_rows() -> list[dict[str, Any]]:
    """Static research notes used by the inventory report; not production shares."""
    return [
        {
            "pollster": "Demoskop",
            "commissioner": "Aftonbladet/SvD",
            "status": "primary_early_chart_complete",
            "note": (
                "Tidigt primärdiagram 3 september. "
                "Slutmätning 7–11 sep används i productionstarget."
            ),
        },
        {
            "pollster": "Ipsos",
            "commissioner": "DN",
            "status": "media_table_crosschecked",
            "note": (
                "DN är paywallad. Omni har åtta namngivna partier. "
                "OTHER 1,6 är DERIVED_RESIDUAL. Expressen korskontrollerar n, fält, L och block."
            ),
        },
        {
            "pollster": "Indikator Opinion",
            "commissioner": "Sveriges Radio Ekot",
            "status": "media_table_crosschecked",
            "note": (
                "Expressen publicerar komplett vektor inklusive Övriga. "
                "Indikator.org låser metod, n och fältperiod."
            ),
        },
        {
            "pollster": "Sentio",
            "commissioner": None,
            "status": "no_primary",
            "note": "Ingen verifierbar primärkälla för en sen 2026-mätning hittades på sentio.no.",
        },
        {
            "pollster": "SCB",
            "commissioner": "SCB",
            "status": "kernel_only",
            "note": "Vid10 2026M05 används bara till same-wave-raking.",
        },
    ]
