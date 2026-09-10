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


def test_manifest_rejects_poll_published_before_fieldwork_ends(tmp_path: Path) -> None:
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(
        """
schema_version: 1
sources:
  - id: invalid_poll
    source: Test
    provider: Test
    dataset: Test
    url: https://example.com/poll.csv
    retrieved_at: null
    reference_period: "2022"
    geography_version: NATIONAL
    license: Test
    raw_file: data/raw/test/poll.csv
    sha256: null
    notes: Test
    poll:
      election_cycle: 2022
      pollster: Test
      publication_date: "2022-05-20"
      fieldwork_start: "2022-05-01"
      fieldwork_end: "2022-05-25"
      information_level: FULL_TRANSITION_TABLE
      population: Eligible voters
      sample_size: 100
      sample_size_basis: RESPONDENTS
      effective_n: null
      survey_weight_method: Test
      question_wording: Test
      extraction_method: API
      page_or_table: Test
      archive_status: LIVE
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="chronology"):
        load_sources(manifest)

