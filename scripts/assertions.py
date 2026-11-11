"""Declarative temporal assertions over project-neutral facts.

This module is a pure engine: it reads ``ProfileFact`` / ``FactOccurrence``
records produced elsewhere and never touches hardware, files, or the network.
Expectations live in ``cases/`` data; this file only knows rule shapes.

Every rule produces exactly one :class:`Outcome`.  A rule that cannot be
judged from the available evidence returns ``SKIP`` or ``BLOCKED`` with a
reason; missing evidence is never converted into ``PASS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .profile import EVENT_REGISTRY

__all__ = [
    "STATUS_PASS",
    "STATUS_FAIL",
    "STATUS_SKIP",
    "STATUS_BLOCKED",
    "STATUS_AMBIGUOUS",
    "Fact",
    "Outcome",
    "normalize_event_type",
    "coerce_fact",
    "coerce_facts",
    "evaluate",
    "aggregate",
]

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
STATUS_BLOCKED = "BLOCKED"
STATUS_AMBIGUOUS = "AMBIGUOUS"

# ``FAIL > BLOCKED > AMBIGUOUS > SKIP > PASS``.  Unknown statuses degrade to
# SKIP severity so a malformed outcome can never be promoted to PASS.
_SEVERITY = {
    STATUS_FAIL: 5,
    STATUS_BLOCKED: 4,
    STATUS_AMBIGUOUS: 3,
    STATUS_SKIP: 2,
    STATUS_PASS: 1,
}

# Canonical key -> canonical key, plus registry event_type name -> canonical
# key.  Derived from the single source of truth so the table is never copied.
_BY_NAME: dict[str, str] = {}
_KEYS: set[str] = set()
for _name, _meta in EVENT_REGISTRY.items():
    _key = str(_meta["key"]).upper()
    _KEYS.add(_key)
    _BY_NAME[_name.strip().lower()] = _key


def normalize_event_type(name: str) -> str:
    """Resolve ``wake_result`` / ``WAKE`` / ``Wake_Result`` to ``WAKE``.

    Unknown names are upper-cased and kept as-is so a new adapter event cannot
    be silently dropped by the engine.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    mapped = _BY_NAME.get(lowered)
    if mapped:
        return mapped
    upper = text.upper()
    if upper in _KEYS:
        return upper
    return upper


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class Fact:
    """One canonical observation derived from any source structure."""

    event_type: str
    key: str = ""
    value: str = ""
    source: str = ""
    port: str = ""
    role: str = ""
    sequence: int = 0
    ts_ms: float | None = None
    captures: Mapping[str, str] = field(default_factory=dict)
    state_class: str = ""
    evidence: tuple[str, ...] = ()
    case_id: str = ""


@dataclass(frozen=True)
class Outcome:
    """The single result a rule contributes to an evaluation."""

    name: str
    status: str
    reason: str = ""
    actual: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "actual": _jsonable(self.actual),
        }


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_as_text(item) for item in value)
    return (_as_text(value),)


def _fact_from_mapping(value: Mapping[str, Any]) -> Fact:
    raw_type = value.get("event_type", value.get("key", ""))
    captures = value.get("captures") or {}
    if not isinstance(captures, Mapping):
        captures = {}
    ts = value.get("ts_ms", None)
    if ts is None:
        ts = value.get("timestamp_ms", None)
    try:
        ts_ms = None if ts is None else float(ts)
    except (TypeError, ValueError):
        ts_ms = None
    try:
        sequence = int(value.get("sequence", 0) or 0)
    except (TypeError, ValueError):
        sequence = 0
    event_type = normalize_event_type(raw_type)
    return Fact(
        event_type=event_type,
        key=_as_text(value.get("key") or raw_type),
        value=_as_text(value.get("value", value.get("presentation_value", ""))),
        source=_as_text(value.get("source")),
        port=_as_text(value.get("port")),
        role=_as_text(value.get("role")),
        sequence=sequence,
        ts_ms=ts_ms,
        captures={str(key): _as_text(item) for key, item in captures.items()},
        state_class=_as_text(value.get("state_class")),
        evidence=tuple(_as_text(item) for item in _as_sequence(value.get("evidence"))),
        case_id=_as_text(value.get("case_id")),
    )


def _fact_from_profile(value: Any) -> Fact:
    event_type = normalize_event_type(getattr(value, "event_type", ""))
    captures = getattr(value, "captures", {}) or {}
    if not isinstance(captures, Mapping):
        captures = {}
    try:
        sequence = int(getattr(value, "sequence", 0) or 0)
    except (TypeError, ValueError):
        sequence = 0
    return Fact(
        event_type=event_type,
        key=_as_text(getattr(value, "key", "") or event_type),
        value=_as_text(getattr(value, "presentation_value", "")),
        source=_as_text(getattr(value, "source", "")),
        port=_as_text(getattr(value, "port", "")),
        role=_as_text(getattr(value, "role", "")),
        sequence=sequence,
        ts_ms=None,
        captures={str(key): _as_text(item) for key, item in captures.items()},
        state_class=_as_text(getattr(value, "state_class", "")),
        evidence=tuple(_as_text(item) for item in _as_sequence(getattr(value, "evidence", ()))),
        case_id=_as_text(getattr(value, "case_id", "")),
    )


def _fact_from_occurrence(value: Any) -> Fact:
    raw_type = getattr(value, "key", "")
    seconds = getattr(value, "monotonic_seconds", None)
    try:
        ts_ms = None if seconds is None else float(seconds) * 1000.0
    except (TypeError, ValueError):
        ts_ms = None
    return Fact(
        event_type=normalize_event_type(raw_type),
        key=_as_text(raw_type),
        value=_as_text(getattr(value, "value", "")),
        source=_as_text(getattr(value, "source", "")),
        port=_as_text(getattr(value, "port", "")),
        role=_as_text(getattr(value, "role", "")),
        sequence=0,
        ts_ms=ts_ms,
        captures={},
        state_class="",
        evidence=tuple(_as_text(item) for item in _as_sequence(getattr(value, "evidence", ()))),
        case_id=_as_text(getattr(value, "case_id", "")),
    )


def coerce_fact(value: Any) -> Fact:
    """Normalize any supported fact-like object into :class:`Fact`."""
    if isinstance(value, Fact):
        event_type = normalize_event_type(value.event_type)
        if event_type == value.event_type:
            return value
        return Fact(
            event_type=event_type,
            key=value.key or value.event_type,
            value=value.value,
            source=value.source,
            port=value.port,
            role=value.role,
            sequence=value.sequence,
            ts_ms=value.ts_ms,
            captures=value.captures,
            state_class=value.state_class,
            evidence=value.evidence,
            case_id=value.case_id,
        )
    if isinstance(value, Mapping):
        return _fact_from_mapping(value)
    # ``timeline.FactOccurrence`` carries monotonic seconds rather than ms.
    if hasattr(value, "monotonic_seconds"):
        return _fact_from_occurrence(value)
    # ``profile.ProfileFact`` carries a presentation value and no timestamp.
    if hasattr(value, "presentation_value"):
        return _fact_from_profile(value)
    if hasattr(value, "event_type"):
        return _fact_from_profile(value)
    raise TypeError(f"cannot coerce {type(value).__name__} into Fact")


def coerce_facts(values: Iterable[Any]) -> list[Fact]:
    return [coerce_fact(value) for value in values]


def aggregate(outcomes: Sequence[Outcome]) -> str:
    """Combine outcomes; an empty rule list is ``SKIP``, never ``PASS``."""
    items = list(outcomes)
    if not items:
        return STATUS_SKIP
    severity = max(_SEVERITY.get(item.status, _SEVERITY[STATUS_SKIP]) for item in items)
    for status, rank in _SEVERITY.items():
        if rank == severity:
            return status
    return STATUS_SKIP


def _normalized_types(value: Any) -> list[str]:
    return [normalize_event_type(item) for item in _as_sequence(value) if _as_text(item).strip()]


def _select(facts: Sequence[Fact], event_types: Sequence[str], source: str = "") -> list[Fact]:
    wanted = set(event_types)
    return [
        fact
        for fact in facts
        if fact.event_type in wanted and (not source or fact.source == source)
    ]


def _missing_types(facts: Sequence[Fact], event_types: Sequence[str]) -> list[str]:
    present = {fact.event_type for fact in facts}
    return [event_type for event_type in event_types if event_type not in present]


def _result(name: str, status: str, reason: str = "", **actual: Any) -> Outcome:
    return Outcome(name=name, status=status, reason=reason, actual=actual)


def _rule_name(rule: Mapping[str, Any], kind: str, index: int) -> str:
    name = _as_text(rule.get("name")).strip()
    if name:
        return name
    if kind == "no_reboot":
        return "no_runtime_reboot"
    return f"{kind}#{index}"


def _int_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# rule handlers
# --------------------------------------------------------------------------


def _rule_event_exists(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    event_types = _normalized_types(rule.get("event_type"))
    if not event_types:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["event_type"])
    min_count = _int_or_none(rule.get("min_count", 1))
    min_count = 1 if min_count is None else min_count
    source = _as_text(rule.get("source"))
    matched = _select(facts, event_types, source)
    count = len(matched)
    if count < min_count:
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            event_type=event_types[0],
            count=count,
            min_count=int(min_count),
            source=source,
        )
    return _result(
        name,
        STATUS_PASS,
        event_type=event_types[0],
        count=count,
        min_count=int(min_count),
        source=source,
    )


def _rule_event_absent(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    event_types = _normalized_types(rule.get("event_type"))
    if not event_types:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["event_type"])
    matched = _select(facts, event_types)
    count = len(matched)
    if count:
        return _result(
            name,
            STATUS_FAIL,
            "unexpected_event",
            event_type=event_types[0],
            count=count,
        )
    return _result(name, STATUS_PASS, event_type=event_types[0], count=0)


def _rule_event_order(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    before_types = _normalized_types(rule.get("before"))
    after_types = _normalized_types(rule.get("after"))
    if not before_types or not after_types:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["before", "after"])
    before_facts = _select(facts, before_types)
    after_facts = _select(facts, after_types)
    missing = _missing_types(facts, before_types + after_types)
    if not before_facts or not after_facts:
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            missing=missing,
            before=before_types[0],
            after=after_types[0],
        )
    strict = bool(rule.get("strict", True))
    positions = {id(fact): index for index, fact in enumerate(facts)}
    first_before = min(before_facts, key=lambda fact: positions.get(id(fact), 0))
    first_after = min(after_facts, key=lambda fact: positions.get(id(fact), 0))
    if first_before.ts_ms is not None and first_after.ts_ms is not None:
        ordered = first_before.ts_ms < first_after.ts_ms
        if not strict:
            ordered = first_before.ts_ms <= first_after.ts_ms
        source = "ts_ms"
    elif first_before.sequence or first_after.sequence:
        ordered = first_before.sequence < first_after.sequence
        if not strict:
            ordered = first_before.sequence <= first_after.sequence
        source = "sequence"
    else:
        # Arrival order is still observed evidence: facts are an ordered stream.
        ordered = positions.get(id(first_before), 0) < positions.get(id(first_after), 0)
        if not strict:
            ordered = positions.get(id(first_before), 0) <= positions.get(id(first_after), 0)
        source = "position"
    actual = {
        "before": before_types[0],
        "after": after_types[0],
        "ordered_by": source,
        "before_ts_ms": first_before.ts_ms,
        "after_ts_ms": first_after.ts_ms,
        "before_sequence": first_before.sequence,
        "after_sequence": first_after.sequence,
    }
    if not ordered:
        return _result(name, STATUS_FAIL, "out_of_order", **actual)
    return _result(name, STATUS_PASS, **actual)


def _window_timing(
    rule: Mapping[str, Any],
    facts: Sequence[Fact],
    name: str,
    *,
    slow_reason: str,
    fast_reason: str,
    from_field: str,
    to_field: str,
) -> Outcome:
    from_types = _normalized_types(rule.get(from_field))
    to_types = _normalized_types(rule.get(to_field))
    max_ms = _int_or_none(rule.get("max_ms"))
    min_ms = _int_or_none(rule.get("min_ms", 0)) or 0.0
    if not from_types or not to_types or max_ms is None:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=[from_field, to_field, "max_ms"])
    from_facts = _select(facts, from_types)
    to_facts = _select(facts, to_types)
    missing = _missing_types(facts, from_types + to_types)
    if not from_facts or not to_facts:
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            missing=missing,
            **{from_field: from_types[0], to_field: to_types[0]},
        )
    if all(fact.ts_ms is None for fact in from_facts) or all(fact.ts_ms is None for fact in to_facts):
        return _result(
            name,
            STATUS_SKIP,
            "no_timestamp",
            **{from_field: from_types[0], to_field: to_types[0]},
        )
    pairs: list[tuple[float, Fact]] = []
    for after in to_facts:
        if after.ts_ms is None:
            continue
        candidates = [
            before
            for before in from_facts
            if before.ts_ms is not None and before.ts_ms <= after.ts_ms
        ]
        if candidates:
            delta = min(after.ts_ms - before.ts_ms for before in candidates)
            pairs.append((delta, after))
    if not pairs:
        untimed = any(fact.ts_ms is None for fact in from_facts) or any(
            fact.ts_ms is None for fact in to_facts
        )
        if untimed:
            return _result(
                name,
                STATUS_SKIP,
                "no_timestamp",
                **{from_field: from_types[0], to_field: to_types[0]},
            )
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            missing=missing or from_types + to_types,
            detail="no_pair",
            **{from_field: from_types[0], to_field: to_types[0]},
        )
    delta_ms, _ = min(pairs, key=lambda item: item[0])
    actual = {
        from_field: from_types[0],
        to_field: to_types[0],
        "delta_ms": round(delta_ms, 3),
        "max_ms": max_ms,
        "min_ms": min_ms,
    }
    if delta_ms > max_ms:
        return _result(name, STATUS_FAIL, slow_reason, **actual)
    if delta_ms < min_ms:
        return _result(name, STATUS_FAIL, fast_reason, **actual)
    return _result(name, STATUS_PASS, **actual)


def _rule_event_within_ms(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    return _window_timing(
        rule,
        facts,
        name,
        slow_reason="too_slow",
        fast_reason="too_fast",
        from_field="from",
        to_field="to",
    )


def _rule_duration(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    return _window_timing(
        rule,
        facts,
        name,
        slow_reason="too_long",
        fast_reason="too_short",
        from_field="start_event",
        to_field="end_event",
    )


def _rule_event_not_during(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    event_types = _normalized_types(rule.get("event_type"))
    window_from = _normalized_types(rule.get("window_from"))
    window_to = _normalized_types(rule.get("window_to"))
    if not event_types or not window_from or not window_to:
        return _result(
            name,
            STATUS_BLOCKED,
            "invalid_rule",
            missing=["event_type", "window_from", "window_to"],
        )
    starts = _select(facts, window_from)
    ends = _select(facts, window_to)
    targets = _select(facts, event_types)
    if not starts or not ends:
        return _result(
            name,
            STATUS_SKIP,
            "window_missing",
            missing=_missing_types(facts, window_from + window_to),
            window_from=window_from[0],
            window_to=window_to[0],
        )
    if any(fact.ts_ms is None for fact in starts + ends):
        return _result(name, STATUS_SKIP, "no_timestamp", window_from=window_from[0], window_to=window_to[0])
    start_ms = min(fact.ts_ms for fact in starts)
    later_ends = [fact.ts_ms for fact in ends if fact.ts_ms >= start_ms]
    if not later_ends:
        return _result(name, STATUS_SKIP, "window_missing", window_from=window_from[0], window_to=window_to[0])
    end_ms = min(later_ends)
    if any(fact.ts_ms is None for fact in targets):
        return _result(name, STATUS_SKIP, "no_timestamp", event_type=event_types[0])
    hits = [fact for fact in targets if start_ms <= fact.ts_ms <= end_ms]
    actual = {
        "event_type": event_types[0],
        "window_from": window_from[0],
        "window_to": window_to[0],
        "window_start_ms": start_ms,
        "window_end_ms": end_ms,
        "count": len(hits),
    }
    if hits:
        return _result(name, STATUS_FAIL, "forbidden_event", **actual)
    return _result(name, STATUS_PASS, **actual)


def _rule_sequence(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    steps = _normalized_types(rule.get("steps"))
    if not steps:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["steps"])
    allow_gaps = bool(rule.get("allow_gaps", True))
    matched: list[Fact] = []
    cursor = 0
    missing_step = ""
    for step in steps:
        found_index = -1
        if cursor >= len(facts):
            missing_step = step
            break
        if allow_gaps:
            for index in range(cursor, len(facts)):
                if facts[index].event_type == step:
                    found_index = index
                    break
        elif facts[cursor].event_type == step:
            found_index = cursor
        if found_index < 0:
            missing_step = step
            break
        matched.append(facts[found_index])
        cursor = found_index + 1
    actual: dict[str, Any] = {
        "steps": steps,
        "matched": [fact.event_type for fact in matched],
        "missing_step": missing_step,
        "allow_gaps": allow_gaps,
    }
    if missing_step:
        return _result(name, STATUS_FAIL, "sequence_broken", **actual)
    max_span_ms = _int_or_none(rule.get("max_span_ms"))
    if max_span_ms is not None:
        first, last = matched[0], matched[-1]
        if first.ts_ms is None or last.ts_ms is None:
            return _result(name, STATUS_SKIP, "no_timestamp", **actual)
        span_ms = last.ts_ms - first.ts_ms
        actual["span_ms"] = round(span_ms, 3)
        actual["max_span_ms"] = max_span_ms
        if span_ms > max_span_ms:
            return _result(name, STATUS_FAIL, "sequence_too_long", **actual)
    return _result(name, STATUS_PASS, **actual)


def _rule_response(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    request_types = _normalized_types(rule.get("request"))
    response_types = _normalized_types(rule.get("response"))
    if not request_types or not response_types:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["request", "response"])
    requests = _select(facts, request_types)
    responses = _select(facts, response_types)
    missing = _missing_types(facts, request_types + response_types)
    if not requests or not responses:
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            missing=missing,
            request=request_types[0],
            response=response_types[0],
        )
    capture = _as_text(rule.get("match_capture")).strip()
    max_ms = _int_or_none(rule.get("max_ms"))

    pairs: list[tuple[Fact, Fact]] = []
    for response in responses:
        candidates = [
            request
            for request in requests
            if request.ts_ms is None
            or response.ts_ms is None
            or request.ts_ms <= response.ts_ms
        ]
        for request in candidates:
            pairs.append((request, response))
    if not pairs:  # defensive: at least one pairing always survives the filter
        return _result(
            name,
            STATUS_FAIL,
            "missing_event",
            missing=missing,
            detail="no_pair",
            request=request_types[0],
            response=response_types[0],
        )

    actual: dict[str, Any] = {
        "request": request_types[0],
        "response": response_types[0],
        "capture": capture,
        "pairs": len(pairs),
    }
    if capture:
        correlated = [
            (request, response)
            for request, response in pairs
            if _as_text(request.captures.get(capture)).strip()
            and _as_text(request.captures.get(capture)) == _as_text(response.captures.get(capture))
        ]
        if not correlated:
            return _result(
                name,
                STATUS_FAIL,
                "correlation_mismatch",
                request_values=sorted(
                    {_as_text(request.captures.get(capture)) for request in requests}
                ),
                response_values=sorted(
                    {_as_text(response.captures.get(capture)) for response in responses}
                ),
                **actual,
            )
        pairs = correlated
        actual["correlation"] = correlated[0][0].captures.get(capture)
        actual["pairs"] = len(pairs)

    def _delta(pair: tuple[Fact, Fact]) -> float:
        request, response = pair
        if request.ts_ms is None or response.ts_ms is None:
            return float("inf")
        return response.ts_ms - request.ts_ms

    request, response = min(pairs, key=_delta)
    delta_ms = _delta((request, response))
    if max_ms is not None:
        if request.ts_ms is None or response.ts_ms is None:
            return _result(name, STATUS_SKIP, "no_timestamp", **actual)
        actual["delta_ms"] = round(delta_ms, 3)
        actual["max_ms"] = max_ms
        if delta_ms > max_ms:
            return _result(name, STATUS_FAIL, "too_slow", **actual)
    elif delta_ms != float("inf"):
        actual["delta_ms"] = round(delta_ms, 3)
    return _result(name, STATUS_PASS, **actual)


def _rule_count_range(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    event_types = _normalized_types(rule.get("event_type"))
    if not event_types:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["event_type"])
    low = _int_or_none(rule.get("min", 0)) or 0
    high = _int_or_none(rule.get("max"))
    count = len(_select(facts, event_types))
    actual = {
        "event_type": event_types[0],
        "count": count,
        "min": int(low),
        "max": None if high is None else int(high),
    }
    if count < low or (high is not None and count > high):
        return _result(name, STATUS_FAIL, "count_out_of_range", **actual)
    return _result(name, STATUS_PASS, **actual)


def _rule_state_required(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    state_class = _as_text(rule.get("state_class")).strip()
    event_types = _normalized_types(rule.get("event_type", "PLAYER")) or ["PLAYER"]
    if not state_class:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["state_class"])
    matched = _select(facts, event_types)
    observed = sorted({fact.state_class for fact in matched if fact.state_class})
    actual = {
        "event_type": event_types[0],
        "state_class": state_class,
        "observed": observed,
        "count": sum(1 for fact in matched if fact.state_class == state_class),
    }
    if state_class not in observed:
        return _result(name, STATUS_FAIL, "state_missing", **actual)
    return _result(name, STATUS_PASS, **actual)


def _rule_no_reboot(rule: Mapping[str, Any], facts: Sequence[Fact], name: str) -> Outcome:
    expanded = dict(rule)
    expanded["event_type"] = "RESTART"
    return _rule_event_absent(expanded, facts, name)


_HANDLERS = {
    "event_exists": _rule_event_exists,
    "event_absent": _rule_event_absent,
    "event_order": _rule_event_order,
    "event_within_ms": _rule_event_within_ms,
    "event_not_during": _rule_event_not_during,
    "sequence": _rule_sequence,
    "duration": _rule_duration,
    "response": _rule_response,
    "count_range": _rule_count_range,
    "state_required": _rule_state_required,
    "no_reboot": _rule_no_reboot,
}


def _evaluate_rule(rule: Any, facts: Sequence[Fact], index: int) -> Outcome:
    if not isinstance(rule, Mapping):
        return _result(f"rule#{index}", STATUS_SKIP, "invalid_rule", detail="rule is not a mapping")
    kind = _as_text(rule.get("kind")).strip()
    name = _rule_name(rule, kind, index) if kind else f"rule#{index}"
    if not kind:
        return _result(name, STATUS_BLOCKED, "invalid_rule", missing=["kind"])
    handler = _HANDLERS.get(kind)
    if handler is None:
        return _result(name, STATUS_SKIP, "unknown_rule_kind", kind=kind)
    return handler(rule, facts, name)


def evaluate(rules: Sequence[Mapping[str, Any]], facts: Iterable[Any]) -> dict:
    """Evaluate every rule against ``facts``; one outcome per rule."""
    items = coerce_facts(facts)
    outcomes = [
        _evaluate_rule(rule, items, index) for index, rule in enumerate(rules or ())
    ]
    counts = {
        STATUS_PASS: 0,
        STATUS_FAIL: 0,
        STATUS_SKIP: 0,
        STATUS_BLOCKED: 0,
        STATUS_AMBIGUOUS: 0,
    }
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return {"status": aggregate(outcomes), "outcomes": outcomes, "counts": counts}
