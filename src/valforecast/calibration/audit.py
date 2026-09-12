from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import numpy as np

from valforecast.calibration.parse_wikipedia import extract_wikipedia_references
from valforecast.features.election_history import PARTIES

AUDIT_SEED = 20260912
AUDIT_N = 15
AUDIT_DIR = Path("data/raw/polls/history/audit")
KEY_PARTIES = ("S", "M", "SD", "L")
MATCH_TOLERANCE_PP = 0.25
INDEX_MARKERS = (
    "opinionen just nu",
    "valjarbarometer/2018-2",
    "dagliga valjarbarometrar",
)


def sample_level_c_rows(rows: list[dict[str, Any]], *, n: int = AUDIT_N) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["source_level"] == "C"]
    if len(candidates) < n:
        raise ValueError(f"Need at least {n} level-C rows to audit, found {len(candidates)}")
    rng = np.random.default_rng(AUDIT_SEED)
    indices = rng.choice(len(candidates), size=n, replace=False)
    return [candidates[int(index)] for index in sorted(indices.tolist())]


def _citation_numbers(text: str) -> list[int]:
    return [int(match) for match in re.findall(r"\[(\d+)\]", text)]


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Val2026Calibration/1.0 "
            "(historical poll audit; research@local)"
        )
    }


def _looks_paywalled(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "subscribe to continue",
        "prenumerera",
        "logga in för att",
        "log in to continue",
        "plus-artikel",
        "du har nått din artikelgräns",
        "please enable cookies",
    )
    if any(marker in lowered for marker in markers) and len(text) < 8000:
        return True
    return len(text) < 400


def _percent_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.findall(r"\b(\d{1,2}[.,]\d)\b", text):
        tokens.add(match.replace(",", "."))
    return tokens


def _expected_tokens(shares: dict[str, float]) -> dict[str, set[str]]:
    tokens: dict[str, set[str]] = {}
    for party in KEY_PARTIES:
        percent = shares[party] * 100.0
        tokens[party] = {
            f"{percent:.1f}",
            f"{round(percent):.1f}",
            str(int(round(percent))),
        }
    return tokens


def _match_count(text: str, shares: dict[str, float]) -> int:
    found = _percent_tokens(text) | set(re.findall(r"\b(\d{1,2})\s*%", text))
    expected = _expected_tokens(shares)
    return sum(1 for options in expected.values() if options & found)


def _slug(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "source").replace(".", "_")
    path = (parsed.path or "page").strip("/").replace("/", "_")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "", f"{host}_{path}")[:120]
    return safe or "cited_page"


def _pin_path(root: Path, url: str, suffix: str = ".html") -> Path:
    return root / AUDIT_DIR / f"{_slug(url)}{suffix}"


def _pdf_text(payload: bytes) -> str | None:
    if not payload.startswith(b"%PDF"):
        return None
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        document = pymupdf.open(stream=payload, filetype="pdf")  # type: ignore[no-untyped-call]
    except ValueError:
        return None
    texts: list[str] = []
    for index in range(int(document.page_count)):
        page = document.load_page(index)  # type: ignore[no-untyped-call]
        texts.append(str(page.get_text()))
    document.close()  # type: ignore[no-untyped-call]
    return "\n".join(texts) or None


def _citation_year_ok(url: str, cycle: int, fieldwork_end: str) -> bool | None:
    original = url
    archived = re.search(r"web\.archive\.org/web/\d+/(https?://.+)", url)
    if archived:
        original = archived.group(1)
    years = set(re.findall(r"(?<![0-9])20\d{2}(?![0-9])", unquote(original)))
    expected = {str(cycle), fieldwork_end[:4]}
    if not years:
        return None
    return bool(years & expected)


def _looks_index(url: str, text: str) -> bool:
    blob = f"{url} {text[:2000]}".lower()
    return any(marker in blob for marker in INDEX_MARKERS)


def _fetch_text(url: str) -> str | None:
    try:
        response = httpx.get(
            url,
            headers=_headers(),
            timeout=20.0,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            return None
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.content.startswith(b"%PDF"):
            return _pdf_text(response.content)
        return response.text
    except httpx.HTTPError:
        return None


def _wayback_url(url: str, cycle: int) -> str:
    stamp = f"{cycle}0901"
    return f"https://web.archive.org/web/{stamp}/{url}"


def _read_pin(path: Path) -> str | None:
    payload = path.read_bytes()
    if payload.startswith(b"%PDF"):
        return _pdf_text(payload)
    return payload.decode("utf-8", errors="replace")


def _load_cited_page(root: Path, url: str, cycle: int) -> tuple[str | None, str, str]:
    for suffix in (".html", ".pdf", ".txt"):
        pin = _pin_path(root, url, suffix)
        if pin.exists():
            text = _read_pin(pin)
            if text:
                return text, str(pin), "pinned"
    text = _fetch_text(url)
    source = url
    origin = "live"
    if text is None or _looks_paywalled(text):
        archived = _fetch_text(_wayback_url(url, cycle))
        if archived:
            text = archived
            source = _wayback_url(url, cycle)
            origin = "wayback"
    if text:
        pin = _pin_path(root, url, ".txt")
        pin.parent.mkdir(parents=True, exist_ok=True)
        pin.write_text(text, encoding="utf-8")
        return text, source, origin
    return None, url, "missing"


def audit_level_c_rows(
    root: Path,
    sampled: list[dict[str, Any]],
) -> dict[str, Any]:
    html_by_file: dict[str, str] = {}
    refs_by_file: dict[str, dict[int, str]] = {}
    exact = 0
    deviant = 0
    unverified = 0
    deviations: list[float] = []
    details: list[dict[str, Any]] = []
    for row in sampled:
        raw = Path(str(row["raw_file"]))
        path = raw if raw.is_absolute() else root / raw
        if str(path) not in html_by_file:
            html_by_file[str(path)] = path.read_text(encoding="utf-8", errors="replace")
            refs_by_file[str(path)] = extract_wikipedia_references(html_by_file[str(path)])
        refs = refs_by_file[str(path)]
        citations = [int(item) for item in row.get("citations") or []]
        if not citations:
            citations = _citation_numbers(str(row["pollster"]))
        urls = list(row.get("citation_urls") or [])
        urls.extend(refs[number] for number in citations if number in refs)
        urls = list(dict.fromkeys(urls))
        urls.sort(
            key=lambda item: (
                0 if "web.archive.org" in item else 1,
                0 if ".pdf" in item.lower() else 1,
                0 if "t.co/" not in item else 2,
            )
        )
        shares = {party: float(row["shares"][party]) for party in PARTIES}
        status = "unverified"
        note = "No A/B page confirmed the parsed cells"
        used_url = ""
        origin = "missing"
        max_abs_pp: float | None = None
        cycle = int(row["election_cycle"])
        fieldwork_end = str(row["fieldwork_end"])
        for url in urls[:3]:
            year_ok = _citation_year_ok(url, cycle, fieldwork_end)
            if year_ok is False:
                note = f"Citation URL is from another year: {url}"
                continue
            fetched, used_url, origin = _load_cited_page(root, url, cycle)
            if fetched is None:
                continue
            if origin == "pinned":
                try:
                    used_url = str(Path(used_url).resolve().relative_to(root.resolve()))
                except ValueError:
                    used_url = str(Path(used_url).name)
            if year_ok is not True and str(cycle) in fetched:
                year_ok = True
            if year_ok is False:
                note = f"Citation URL is from another year: {url}"
                continue
            if _looks_index(url, fetched):
                note = (
                    f"Citation is an index or living dashboard, "
                    f"not one table ({origin}): {used_url}"
                )
                continue
            if _looks_paywalled(fetched):
                note = f"Citation page looks paywalled ({origin}): {used_url}"
                continue
            hits = _match_count(fetched, shares)
            expected = _expected_tokens(shares)
            found = _percent_tokens(fetched)
            gaps = []
            for party, options in expected.items():
                if options & found:
                    gaps.append(0.0)
                else:
                    target = shares[party] * 100.0
                    nearest = min((abs(float(item) - target) for item in found), default=99.0)
                    gaps.append(nearest)
            if gaps:
                max_abs_pp = float(max(gaps))
            if year_ok is True and (
                hits >= 3 or (max_abs_pp is not None and max_abs_pp <= MATCH_TOLERANCE_PP)
            ):
                status = "exact_ab"
                if hits >= 3:
                    max_abs_pp = 0.0
                note = f"Citation page reproduces the named shares ({origin}): {used_url}"
                break
            if year_ok is not True:
                note = f"Citation year could not be confirmed ({origin}): {used_url}"
                continue
            single_table = ".pdf" in url.lower()
            if (
                year_ok is True
                and single_table
                and len(found) >= 8
                and max_abs_pp is not None
            ):
                status = "deviant"
                note = (
                    f"Same-year citation fetched but named shares differ by up to "
                    f"{max_abs_pp:.1f} pp ({origin}): {used_url}"
                )
                break
            note = f"Citation page fetched without a usable share table ({origin}): {used_url}"
        details.append(
            {
                "poll_id": row["poll_id"],
                "cycle": row["election_cycle"],
                "family": row["institute_family"],
                "fieldwork_end": row["fieldwork_end"],
                "citations": citations,
                "citation_urls": urls,
                "checked_url": used_url,
                "origin": origin,
                "status": status,
                "max_abs_pp": max_abs_pp,
                "note": note,
            }
        )
        if status == "exact_ab":
            exact += 1
        elif status == "deviant":
            deviant += 1
            if max_abs_pp is not None:
                deviations.append(max_abs_pp)
        else:
            unverified += 1
    checked = exact + deviant
    if checked == 0:
        recommendation = (
            "Level C must not be production input. This revision could not confirm "
            "the sampled Wikipedia rows against A/B publications. Keep C for "
            "historical calibration only."
        )
    elif unverified >= len(sampled) / 2:
        recommendation = (
            "Level C must not be production input. More than half of the sampled "
            "rows could not be confirmed. Several Wikipedia hrefs point to the "
            "wrong year. Restrict C, especially 2014, before house effects are "
            "allowed to move a live forecast."
        )
    elif deviant / max(checked, 1) >= 0.3:
        recommendation = (
            "Level C must not be production input. The checked A/B pages deviate "
            "often enough that C should be restricted or dropped in a later "
            "revision before house effects are allowed to move a live forecast."
        )
    else:
        recommendation = (
            "Level C must not be production input. The checked A/B pages mostly "
            "reproduce the Wikipedia cells, so C is usable for historical "
            "calibration with that limitation stated."
        )
    return {
        "seed": AUDIT_SEED,
        "n_sampled": len(sampled),
        "exact_ab": exact,
        "deviant": deviant,
        "unverified": unverified,
        "mean_deviant_abs_pp": (
            float(np.mean(deviations)) if deviations else None
        ),
        "rows": details,
        "recommendation": recommendation,
    }
