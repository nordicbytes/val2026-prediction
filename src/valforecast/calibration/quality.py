from __future__ import annotations

from typing import Any


def _verified_ids(audit: dict[str, Any]) -> set[str]:
    return {
        str(row["poll_id"])
        for row in audit.get("rows") or []
        if row.get("status") == "exact_ab"
    }


def is_ab_verified(row: dict[str, Any], verified_ids: set[str]) -> bool:
    level = str(row["source_level"])
    if level in {"A", "B"}:
        return True
    return level == "C" and str(row["poll_id"]) in verified_ids


def house_effect_source_quality(
    last_rows: list[dict[str, Any]],
    houses: dict[str, dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    verified_ids = _verified_ids(audit)
    quality: dict[str, dict[str, Any]] = {}
    for family in sorted(houses):
        rows = [
            row for row in last_rows if str(row["institute_family"]) == family
        ]
        n_ab = sum(1 for row in rows if str(row["source_level"]) in {"A", "B"})
        n_c = sum(1 for row in rows if str(row["source_level"]) == "C")
        n_c_verified = sum(
            1
            for row in rows
            if str(row["source_level"]) == "C" and str(row["poll_id"]) in verified_ids
        )
        n_c_unverified = n_c - n_c_verified
        quality[family] = {
            "n_last_polls": len(rows),
            "n_ab": n_ab,
            "n_c_verified": n_c_verified,
            "n_c_unverified": n_c_unverified,
            "rests_only_on_unverified_c": n_ab == 0 and n_c_verified == 0,
            "cycles": sorted({int(row["election_cycle"]) for row in rows}),
        }
    return quality


def unverified_share(rows: list[dict[str, Any]], audit: dict[str, Any]) -> dict[str, Any]:
    verified_ids = _verified_ids(audit)
    n = len(rows)
    n_unverified = sum(1 for row in rows if not is_ab_verified(row, verified_ids))
    return {
        "n": n,
        "n_unverified": n_unverified,
        "share_unverified": (n_unverified / n) if n else None,
    }
