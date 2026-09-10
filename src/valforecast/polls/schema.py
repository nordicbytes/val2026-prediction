from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from valforecast.features.election_history import PARTIES

POLL_CURRENT_CATEGORIES = (*PARTIES, "BLANK", "DONT_KNOW", "MISSING")
POLL_PREVIOUS_NONPARTY = ("DID_NOT_VOTE", "NOT_ELIGIBLE", "MISSING")
POLL_PREVIOUS_CATEGORIES = (*PARTIES, *POLL_PREVIOUS_NONPARTY)
INFORMATION_LEVELS = frozenset(
    {
        "MICRODATA",
        "FULL_TRANSITION_TABLE",
        "PARTIAL_TRANSITION_TABLE",
        "MARGINAL_ONLY",
    }
)


@dataclass(frozen=True)
class PollWave:
    wave_id: str
    election_cycle: int
    pollster: str
    fieldwork_start: date
    fieldwork_end: date
    publication_date: date
    forecast_cutoff: date
    information_level: str
    sample_size: int | None

    def validate_strict_cutoff(self) -> None:
        if self.information_level not in INFORMATION_LEVELS:
            raise ValueError(f"Unknown information level: {self.information_level}")
        if self.fieldwork_start > self.fieldwork_end:
            raise ValueError("Fieldwork starts after it ends")
        if self.fieldwork_end > self.forecast_cutoff:
            raise ValueError(f"{self.wave_id} fieldwork ends after forecast cutoff")
        if self.publication_date > self.forecast_cutoff:
            raise ValueError(f"{self.wave_id} was published after forecast cutoff")


def validate_transition_cells(frame: pl.DataFrame) -> None:
    required = {
        "wave_id",
        "previous_party",
        "current_party",
        "estimate",
        "margin_error",
        "row_base",
        "cell_status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing transition columns: {sorted(missing)}")
    invalid_previous = set(frame["previous_party"].drop_nulls().unique()) - set(
        POLL_PREVIOUS_CATEGORIES
    )
    invalid_current = set(frame["current_party"].drop_nulls().unique()) - set(
        POLL_CURRENT_CATEGORIES
    )
    if invalid_previous or invalid_current:
        raise ValueError(
            f"Unknown parties: previous={sorted(invalid_previous)}, "
            f"current={sorted(invalid_current)}"
        )
    estimates = frame["estimate"].drop_nulls()
    if estimates.len() and ((estimates < 0).any() or (estimates > 1).any()):
        raise ValueError("Transition estimates must be probabilities")
