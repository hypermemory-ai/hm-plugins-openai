"""Validate the exact turn contract before it can reach a remote writer."""

import math

ACTIVITY_CATEGORIES = frozenset(
    {
        "reasoning",
        "memory",
        "context",
        "doc_processing",
        "automation",
        "personal",
        "chatting",
        "research",
        "design",
        "calculations",
        "coding",
        "planning",
        "productivity",
        "writing",
        "unmatched",
    }
)


def validate_segments(segments):
    if not isinstance(segments, list) or not segments:
        raise ValueError("activity_segments must contain the turn's actual activities")
    seen = set()
    total = 0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("each activity segment must be an object")
        category = segment.get("category")
        weight = segment.get("weight")
        if not isinstance(category, str) or category not in ACTIVITY_CATEGORIES or category in seen:
            raise ValueError(f"unsupported or repeated activity category: {category!r}")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or not 0 < weight <= 100
        ):
            raise ValueError("activity weights must be finite numbers greater than 0 and at most 100")
        seen.add(category)
        total += weight
    if not math.isclose(total, 100, rel_tol=0, abs_tol=0.000001):
        raise ValueError("activity weights must total 100")
    return segments


def validate_contract(contract):
    summary = contract.get("timeline_summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
        raise ValueError("timeline_summary must contain 1–2000 characters")
    if not isinstance(contract.get("durable_candidates"), list):
        raise ValueError("durable_candidates must be an explicit list")
    if any(not isinstance(candidate, dict) or not candidate for candidate in contract["durable_candidates"]):
        raise ValueError("each durable candidate must be a nonempty object")
    validate_segments(contract.get("activity_segments"))
