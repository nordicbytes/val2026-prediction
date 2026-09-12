from __future__ import annotations

from typing import Any

import numpy as np

from valforecast.features.election_history import PARTIES

HOUSE_PRIOR_STRENGTH = 3.0


def last_poll_errors(
    last_rows: list[dict[str, Any]],
    results: dict[int, dict[str, float]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for row in last_rows:
        cycle = int(row["election_cycle"])
        result = results[cycle]
        shares = row["shares"]
        residual = {party: float(shares[party]) - float(result[party]) for party in PARTIES}
        errors.append({**row, "error": residual})
    return errors


def estimate_house_effects(
    errors: list[dict[str, Any]],
    *,
    prior_strength: float = HOUSE_PRIOR_STRENGTH,
    max_cycle: int | None = None,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in errors:
        cycle = int(row["election_cycle"])
        if max_cycle is not None and cycle >= max_cycle:
            continue
        grouped.setdefault(str(row["institute_family"]), []).append(row)
    estimates: dict[str, dict[str, Any]] = {}
    for family, rows in grouped.items():
        n_cycles = len({int(row["election_cycle"]) for row in rows})
        shrink = n_cycles / (n_cycles + prior_strength)
        raw = {party: 0.0 for party in PARTIES}
        usable = {party: 0 for party in PARTIES}
        for row in rows:
            cycle = int(row["election_cycle"])
            for party in PARTIES:
                if party == "SD" and cycle <= 2006:
                    continue
                raw[party] += float(row["error"][party])
                usable[party] += 1
        mean = {
            party: (raw[party] / usable[party] if usable[party] else 0.0) for party in PARTIES
        }
        estimates[family] = {
            "n_cycles": n_cycles,
            "shrinkage": shrink,
            "prior_strength": prior_strength,
            "raw_mean": mean,
            "shrunk": {party: shrink * mean[party] for party in PARTIES},
        }
    return estimates


def apply_house_effects(
    rows: list[dict[str, Any]],
    houses: dict[str, dict[str, Any]],
) -> list[dict[str, float]]:
    adjusted: list[dict[str, float]] = []
    for row in rows:
        family = str(row["institute_family"])
        effect = houses.get(family, {}).get("shrunk", {party: 0.0 for party in PARTIES})
        shares = {
            party: max(float(row["shares"][party]) - float(effect[party]), 0.0)
            for party in PARTIES
        }
        total = sum(shares.values())
        adjusted.append({party: shares[party] / total for party in PARTIES})
    return adjusted


def mean_shares(rows: list[dict[str, float]] | list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        raise ValueError("Cannot average an empty poll set")
    values = []
    for row in rows:
        raw = row.get("shares")
        shares: dict[str, Any] = raw if isinstance(raw, dict) else row
        values.append([float(shares[party]) for party in PARTIES])
    mean = np.mean(np.array(values, dtype=float), axis=0)
    mean = mean / mean.sum()
    return {party: float(mean[index]) for index, party in enumerate(PARTIES)}


def l1_error(forecast: dict[str, float], result: dict[str, float]) -> float:
    return float(sum(abs(forecast[party] - result[party]) for party in PARTIES))


def error_covariance(errors: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = np.array(
        [[float(row["error"][party]) for party in PARTIES] for row in errors],
        dtype=float,
    )
    n_obs, n_parties = matrix.shape
    if n_obs < 2:
        raise ValueError("Need at least two last-poll residuals for covariance")
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    sample = (centered.T @ centered) / (n_obs - 1)
    shrink = n_obs / (n_obs + n_parties)
    target = float(np.mean(np.diag(sample))) * np.eye(n_parties)
    sigma = shrink * sample + (1.0 - shrink) * target
    sd_pp = {
        party: float(np.sqrt(max(sigma[index, index], 0.0)) * 100.0)
        for index, party in enumerate(PARTIES)
    }
    return {
        "n_obs": n_obs,
        "shrinkage": shrink,
        "sigma": sigma,
        "sd_percentage_points": sd_pp,
        "mean_error": {
            party: float(matrix[:, index].mean()) for index, party in enumerate(PARTIES)
        },
    }


def recency_curve(
    rows: list[dict[str, Any]],
    results: dict[int, dict[str, float]],
) -> list[dict[str, Any]]:
    buckets = ((1, 7), (8, 14), (15, 21), (22, 30))
    curve: list[dict[str, Any]] = []
    for low, high in buckets:
        selected = [
            row
            for row in rows
            if low <= int(row["days_to_election"]) <= high
        ]
        if not selected:
            curve.append({"days": f"{low}-{high}", "n": 0, "mean_l1": None})
            continue
        l1s = [
            l1_error(row["shares"], results[int(row["election_cycle"])])
            for row in selected
        ]
        curve.append(
            {
                "days": f"{low}-{high}",
                "n": len(selected),
                "mean_l1": float(np.mean(l1s)),
            }
        )
    return curve
