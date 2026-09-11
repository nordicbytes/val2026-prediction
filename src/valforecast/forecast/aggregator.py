from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import exp, log, sqrt

import numpy as np

from valforecast.features.election_history import PARTIES
from valforecast.forecast.contract import ForecastContract
from valforecast.forecast.polls import NationalPoll
from valforecast.polls.transition_posterior import sample_dirichlet_row


@dataclass(frozen=True)
class AggregatedPolls:
    included: tuple[NationalPoll, ...]
    excluded: tuple[NationalPoll, ...]
    weights: np.ndarray
    point: np.ndarray
    n_eff: float


def intervals_overlap(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    return left_start <= right_end and right_start <= left_end


PRODUCTION_SOURCE_CLASSES = frozenset(
    {
        "static_primary_document",
        "pollster_or_commissioner_original",
        "established_media_table_crosschecked",
    }
)


def recency_key(poll: NationalPoll) -> tuple[date, date, str]:
    return (poll.fieldwork_end, poll.publication_date, poll.poll_id)


def keep_latest_per_pollster(polls: list[NationalPoll]) -> list[NationalPoll]:
    latest: dict[str, NationalPoll] = {}
    for poll in polls:
        current = latest.get(poll.pollster)
        if current is None or recency_key(poll) > recency_key(current):
            latest[poll.pollster] = poll
    return sorted(latest.values(), key=lambda poll: (poll.fieldwork_end, poll.poll_id))


def collapse_overlapping_polls(polls: list[NationalPoll]) -> list[NationalPoll]:
    kept: list[NationalPoll] = []
    discarded: set[str] = set()
    ordered = sorted(
        polls,
        key=lambda poll: (poll.fieldwork_end, poll.publication_date, poll.poll_id),
        reverse=True,
    )
    for poll in ordered:
        if poll.poll_id in discarded:
            continue
        for other in ordered:
            if other.poll_id == poll.poll_id or other.pollster != poll.pollster:
                continue
            if intervals_overlap(
                poll.fieldwork_start,
                poll.fieldwork_end,
                other.fieldwork_start,
                other.fieldwork_end,
            ):
                discarded.add(other.poll_id)
        kept.append(poll)
    return sorted(kept, key=lambda poll: (poll.fieldwork_end, poll.poll_id))


def poll_weight(poll: NationalPoll, contract: ForecastContract) -> float:
    age_days = max((contract.cutoff.date() - poll.fieldwork_end).days, 0)
    decay = exp(-log(2.0) * age_days / contract.half_life_days)
    sample = poll.sample_size or contract.missing_sample_size_fallback
    return decay * sqrt(float(sample))


def _mark_excluded(poll: NationalPoll, reason: str) -> NationalPoll:
    return NationalPoll(
        **{
            **poll.__dict__,
            "included": False,
            "exclusion_reason": reason,
        }
    )


def _select_strict_hosted(
    polls: list[NationalPoll],
    contract: ForecastContract,
) -> tuple[list[NationalPoll], list[NationalPoll]]:
    wanted = list(contract.strict_hosted_poll_ids)
    by_id = {poll.poll_id: poll for poll in polls}
    missing = [poll_id for poll_id in wanted if poll_id not in by_id]
    if missing:
        raise ValueError(f"Strict-hosted sensitivity is missing polls: {missing}")
    excluded: list[NationalPoll] = []
    wanted_set = set(wanted)
    for poll in polls:
        if poll.poll_id not in wanted_set:
            if not poll.included:
                excluded.append(poll)
            else:
                excluded.append(_mark_excluded(poll, "not_in_strict_hosted_sensitivity"))
            continue
        if not poll.included:
            raise ValueError(f"Strict-hosted poll is excluded at parse time: {poll.poll_id}")
    included = [by_id[poll_id] for poll_id in wanted]
    return included, excluded


def select_included_polls(
    polls: list[NationalPoll],
    contract: ForecastContract,
    *,
    variant: str = "production",
    allow_secondary: bool = False,
) -> tuple[list[NationalPoll], list[NationalPoll]]:
    if contract.pollster_balance != "latest_fieldwork_end_per_pollster":
        raise ValueError(f"Unsupported pollster balance: {contract.pollster_balance}")
    if variant == "strict_hosted":
        return _select_strict_hosted(polls, contract)
    eligible: list[NationalPoll] = []
    excluded: list[NationalPoll] = []
    for poll in polls:
        if not poll.included:
            excluded.append(poll)
            continue
        if poll.source_class not in PRODUCTION_SOURCE_CLASSES and not allow_secondary:
            excluded.append(_mark_excluded(poll, poll.exclusion_reason or "secondary_source"))
            continue
        eligible.append(poll)
    collapsed = collapse_overlapping_polls(eligible)
    collapsed_ids = {poll.poll_id for poll in collapsed}
    balanced = keep_latest_per_pollster(collapsed)
    kept_ids = {poll.poll_id for poll in balanced}
    for poll in eligible:
        if poll.poll_id in kept_ids:
            continue
        reason = (
            "overlapping_same_pollster"
            if poll.poll_id not in collapsed_ids
            else "pollster_balance_not_latest"
        )
        excluded.append(_mark_excluded(poll, reason))
    return balanced, excluded


def aggregate_polls(
    polls: list[NationalPoll],
    contract: ForecastContract,
    *,
    variant: str = "production",
) -> AggregatedPolls:
    included, excluded = select_included_polls(polls, contract, variant=variant)
    if not included:
        raise ValueError("No polls remain after cutoff and overlap rules")
    weights = np.array([poll_weight(poll, contract) for poll in included], dtype=float)
    weights = weights / weights.sum()
    matrix = np.array([[poll.shares[party] for party in PARTIES] for poll in included], dtype=float)
    point = weights @ matrix
    point = point / point.sum()
    n_eff = float(
        sum(
            (poll.sample_size or contract.missing_sample_size_fallback) * weight
            for poll, weight in zip(included, weights, strict=True)
        )
    )
    return AggregatedPolls(
        included=tuple(included),
        excluded=tuple(excluded),
        weights=weights,
        point=point,
        n_eff=n_eff,
    )


def draw_poll_targets(
    aggregated: AggregatedPolls,
    *,
    n_draws: int,
    seed: int,
    bootstrap: bool = True,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n_polls = len(aggregated.included)
    if n_polls == 0:
        raise ValueError("Cannot draw poll targets without included polls")
    draws = np.empty((n_draws, len(PARTIES)), dtype=float)
    for draw_index in range(n_draws):
        if bootstrap:
            indices = rng.integers(0, n_polls, size=n_polls)
            polls = [aggregated.included[index] for index in indices]
            weights = aggregated.weights[indices]
            weights = weights / weights.sum()
        else:
            polls = list(aggregated.included)
            weights = aggregated.weights
        mix = np.zeros(len(PARTIES), dtype=float)
        for poll, weight in zip(polls, weights, strict=True):
            alpha = np.array(
                [
                    max(poll.shares[party] * float(poll.sample_size or 1000), 1e-6)
                    for party in PARTIES
                ],
                dtype=float,
            )
            mix += weight * sample_dirichlet_row(rng, alpha)
        draws[draw_index] = mix / mix.sum()
    return draws
