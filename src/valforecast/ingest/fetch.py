from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from valforecast.config import Source


@dataclass(frozen=True)
class FetchReceipt:
    source_id: str
    url: str
    path: str
    sha256: str
    bytes: int
    retrieved_at: str
    reused_cache: bool


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_source(
    source: Source,
    project_root: Path,
    *,
    timeout_seconds: float = 120.0,
) -> FetchReceipt:
    if source.raw_file is None:
        raise ValueError(f"Source {source.id} is a catalogue, not a downloadable raw file")

    raw_root = (project_root / "data" / "raw").resolve()
    destination = (project_root / source.raw_file).resolve()
    if not destination.is_relative_to(raw_root):
        raise ValueError(f"Raw file must be inside data/raw: {source.raw_file}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        checksum = sha256_file(destination)
        if source.sha256 is not None and checksum != source.sha256:
            raise ValueError(
                f"Cached file checksum differs from manifest for {source.id}: {checksum}"
            )
        return _write_receipt(source, destination, checksum, reused_cache=True)

    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.part")
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=timeout_seconds) as client,
            client.stream("GET", source.url) as response,
            temporary.open("wb") as handle,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes():
                handle.write(chunk)

        checksum = sha256_file(temporary)
        if source.sha256 is not None and checksum != source.sha256:
            raise ValueError(
                f"Downloaded checksum differs from manifest for {source.id}: {checksum}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return _write_receipt(source, destination, checksum, reused_cache=False)


def _write_receipt(
    source: Source,
    destination: Path,
    checksum: str,
    *,
    reused_cache: bool,
) -> FetchReceipt:
    receipt = FetchReceipt(
        source_id=source.id,
        url=source.url,
        path=str(destination),
        sha256=checksum,
        bytes=destination.stat().st_size,
        retrieved_at=datetime.now(UTC).isoformat(),
        reused_cache=reused_cache,
    )
    receipt_path = destination.with_suffix(destination.suffix + ".receipt.json")
    receipt_path.write_text(
        json.dumps(asdict(receipt), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt

