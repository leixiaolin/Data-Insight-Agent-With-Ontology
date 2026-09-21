"""Shared checks for tool-grounded ontology evidence (not model summaries)."""

from __future__ import annotations

import math
from typing import Any


def usable_business_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return False
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return False
    def identified(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return any(identified(value.get(key)) for key in ("name", "iri", "entity", "relation"))
        return False
    return identified(data.get("root_entity")) or any(
        identified(item)
        for key in ("semantic_properties", "semantic_relationships")
        for item in (data.get(key) or [])
    )


def evidence_confidence(payload: dict[str, Any]) -> float:
    try:
        value = float(payload.get("confidence", 0))
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def select_business_evidence(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        (evidence_confidence(item["result"]), index, item)
        for index, item in enumerate(results)
        if item.get("tool") == "get_business_context"
        and not item.get("error")
        and usable_business_evidence(item.get("result"))
    ]
    return max(candidates, key=lambda item: item[:2])[2] if candidates else None


def usable_ontology_context(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and usable_business_evidence(payload.get("primary_business_context"))
    )
