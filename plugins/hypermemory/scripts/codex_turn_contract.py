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
ACTION_HINTS = frozenset({"store", "update", "forget", "supersede"})
OUTCOME_STATUSES = frozenset({"completed", "partial", "blocked", "informational"})
SOURCE_BASES = frozenset({"user_confirmed", "completed_work", "authoritative_evidence"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


def _required_text(value, field, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must contain 1–{maximum} characters")
    return value


def _optional_text(value, field):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")


def _string_list(value, field):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an explicit list of strings")


def _relationship_changes(value, candidate_index):
    if not isinstance(value, dict) or set(value) != {"add", "remove_or_replace"}:
        raise ValueError(f"durable_candidates[{candidate_index}].relationship_changes has the wrong shape")
    if not isinstance(value["add"], list) or not isinstance(value["remove_or_replace"], list):
        raise ValueError(f"durable_candidates[{candidate_index}].relationship_changes entries must be lists")
    for index, change in enumerate(value["add"]):
        if not isinstance(change, dict):
            field = f"durable_candidates[{candidate_index}].relationship_changes.add[{index}]"
            raise ValueError(f"{field} must be an object")
        _required_text(change.get("target_key"), "relationship target_key", 300)
        _required_text(change.get("meaning"), "relationship meaning", 1000)
    for index, change in enumerate(value["remove_or_replace"]):
        if not isinstance(change, dict):
            field = f"durable_candidates[{candidate_index}].relationship_changes.remove_or_replace[{index}]"
            raise ValueError(f"{field} must be an object")
        _required_text(change.get("target_key"), "relationship target_key", 300)
        _required_text(change.get("current_meaning"), "relationship current_meaning", 1000)
        _required_text(change.get("reason"), "relationship reason", 1000)


def _candidate(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"durable_candidates[{index}] must be an object")
    if value.get("action_hint") not in ACTION_HINTS:
        raise ValueError(f"durable_candidates[{index}].action_hint is invalid")
    _optional_text(value.get("key_hint"), f"durable_candidates[{index}].key_hint")
    _required_text(value.get("node_type"), f"durable_candidates[{index}].node_type", 100)
    _required_text(value.get("description_draft"), f"durable_candidates[{index}].description_draft", 1000)
    if not isinstance(value.get("facts"), dict):
        raise ValueError(f"durable_candidates[{index}].facts must be an object")
    _relationship_changes(value.get("relationship_changes"), index)
    _required_text(value.get("durability_reason"), f"durable_candidates[{index}].durability_reason", 1000)
    if value.get("source_basis") not in SOURCE_BASES:
        raise ValueError(f"durable_candidates[{index}].source_basis is invalid")
    if value.get("confidence") not in CONFIDENCE_LEVELS:
        raise ValueError(f"durable_candidates[{index}].confidence is invalid")


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
    if not isinstance(contract, dict) or contract.get("schema_version") != "2.10.0":
        raise ValueError("contract must use schema_version 2.10.0")
    _required_text(contract.get("turn_id"), "turn_id", 160)
    _optional_text(contract.get("occurred_at"), "occurred_at")
    scope = contract.get("active_scope")
    if not isinstance(scope, dict):
        raise ValueError("active_scope must be an object")
    _optional_text(scope.get("project_key"), "active_scope.project_key")
    _optional_text(scope.get("project_name"), "active_scope.project_name")
    _string_list(scope.get("other_anchor_keys"), "active_scope.other_anchor_keys")
    request = contract.get("request")
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    _required_text(request.get("intent"), "request.intent", 1000)
    _optional_text(request.get("explicit_memory_instruction"), "request.explicit_memory_instruction")
    outcome = contract.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") not in OUTCOME_STATUSES:
        raise ValueError("outcome.status is invalid")
    _required_text(outcome.get("summary"), "outcome.summary", 2000)
    if not isinstance(outcome.get("durable_artifacts"), list):
        raise ValueError("outcome.durable_artifacts must be an explicit list")
    candidates = contract.get("durable_candidates")
    if not isinstance(candidates, list):
        raise ValueError("durable_candidates must be an explicit list")
    for index, candidate in enumerate(candidates):
        _candidate(candidate, index)
    _required_text(contract.get("timeline_summary"), "timeline_summary", 2000)
    validate_segments(contract.get("activity_segments"))
    _string_list(contract.get("timeline_only"), "timeline_only")
    _string_list(contract.get("excluded"), "excluded")
    token_listener = contract.get("token_listener")
    if not isinstance(token_listener, dict) or set(token_listener) != {"listener_path", "job_path"}:
        raise ValueError("token_listener must contain listener_path and job_path")
    _optional_text(token_listener.get("listener_path"), "token_listener.listener_path")
    _optional_text(token_listener.get("job_path"), "token_listener.job_path")
    return contract
