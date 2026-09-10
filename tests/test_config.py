from pathlib import Path

import pytest

from valforecast.config import load_sources


def test_project_source_manifest_is_valid() -> None:
    root = Path(__file__).parents[1]
    sources = load_sources(root / "config" / "sources.yaml")
    assert sources
    assert len({source.id for source in sources}) == len(sources)


def test_manifest_rejects_missing_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "sources.yaml"
    manifest.write_text("schema_version: 1\nsources:\n  - id: incomplete\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_sources(manifest)

