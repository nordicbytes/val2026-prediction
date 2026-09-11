from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from valforecast.config import load_sources
from valforecast.evaluation.milestone_four_a import run_milestone_four_a
from valforecast.evaluation.milestone_four_b import (
    lock_survey_estimators,
    run_milestone_four_b,
)
from valforecast.evaluation.milestone_four_c import (
    lock_region_estimators,
    run_milestone_four_c,
)
from valforecast.evaluation.milestone_three import run_milestone_three
from valforecast.evaluation.milestone_two import run_milestone_two
from valforecast.forecast.contract import load_forecast_contract
from valforecast.forecast.lock import load_input_lock, write_input_lock
from valforecast.forecast.produce import run_forecast_2026
from valforecast.forecast.snapshot import build_snapshot_document, write_snapshot
from valforecast.ingest.fetch import fetch_source
from valforecast.pipeline import run_initial_milestone
from valforecast.polls.transition_corpus import build_survey_corpora

app = typer.Typer(no_args_is_help=True)
sources_app = typer.Typer(no_args_is_help=True)
app.add_typer(sources_app, name="sources")


def _root() -> Path:
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "config").exists():
            return candidate
    raise typer.BadParameter("Run the command inside the project repository")


@sources_app.command("validate")
def validate_sources() -> None:
    sources = load_sources(_root() / "config" / "sources.yaml")
    downloadable = sum(source.raw_file is not None for source in sources)
    typer.echo(f"Validated {len(sources)} sources ({downloadable} downloadable).")


@sources_app.command("fetch")
def fetch_sources(
    source_ids: Annotated[
        list[str] | None,
        typer.Option("--source", help="Source id; repeat to fetch multiple sources."),
    ] = None,
) -> None:
    root = _root()
    sources = load_sources(root / "config" / "sources.yaml")
    selected = [
        source
        for source in sources
        if source.raw_file is not None and (source_ids is None or source.id in source_ids)
    ]
    unknown = set(source_ids or ()) - {source.id for source in sources}
    if unknown:
        raise typer.BadParameter(f"Unknown source ids: {sorted(unknown)}")
    if not selected:
        raise typer.BadParameter("No downloadable sources selected")

    for source in selected:
        receipt = fetch_source(source, root)
        typer.echo(json.dumps(asdict(receipt), ensure_ascii=False))


@app.command("build-initial")
def build_initial() -> None:
    summary = run_initial_milestone(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("analyze-structure")
def analyze_structure() -> None:
    summary = run_milestone_two(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("validate-temporally")
def validate_temporally() -> None:
    summary = run_milestone_three(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("validate-transitions")
def validate_transitions() -> None:
    summary = run_milestone_four_a(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("ingest-survey-corpus")
def ingest_survey_corpus() -> None:
    summary = build_survey_corpora(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("lock-transitions-b-survey")
def lock_transitions_b_survey() -> None:
    summary = lock_survey_estimators(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("validate-transitions-b")
def validate_transitions_b() -> None:
    summary = run_milestone_four_b(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("lock-transitions-c-survey")
def lock_transitions_c_survey() -> None:
    summary = lock_region_estimators(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("validate-transitions-c")
def validate_transitions_c() -> None:
    summary = run_milestone_four_c(_root())
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("lock-forecast-2026-inputs")
def lock_forecast_2026_inputs() -> None:
    path = write_input_lock(_root())
    typer.echo(json.dumps({"lock_path": str(path)}, ensure_ascii=False, indent=2))


@app.command("forecast-2026")
def forecast_2026(
    official: Annotated[bool, typer.Option("--official")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    if official and dry_run:
        raise typer.BadParameter("Use either --official or --dry-run, not both")
    root = _root()
    contract = load_forecast_contract(root / "config" / "forecast_2026.yaml")
    lock = load_input_lock(root)
    result = run_forecast_2026(root, contract)
    document = build_snapshot_document(root, result)
    payload: dict[str, object] = {
        "included_polls": document["included_polls"],
        "national": document["prediction"]["national"],
        "poll_target": document["prediction"]["poll_target"],
        "strict_sensitivity": {
            "included_polls": document["strict_sensitivity_polls"],
            "national": document["prediction"]["strict_sensitivity"]["national"],
            "poll_target": document["prediction"]["strict_sensitivity"]["poll_target"],
        },
        "lock_stage": lock.get("stage"),
        "git_commit": document["git_commit"],
        "official": official,
        "dry_run": dry_run,
    }
    if dry_run:
        payload["snapshot"] = None
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    path = write_snapshot(root, document, official=official)
    payload["snapshot"] = str(path)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
