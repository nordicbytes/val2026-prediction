from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from valforecast.config import load_sources
from valforecast.evaluation.milestone_two import run_milestone_two
from valforecast.ingest.fetch import fetch_source
from valforecast.pipeline import run_initial_milestone

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
