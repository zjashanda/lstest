"""Offline parity harness: lstest engines vs the real Polaris primitives.

Design rules
------------
* Polaris is **loaded**, never re-implemented: the real
  ``runtime/assertion_engine.py``, ``runtime/assertion_dsl.py``,
  ``runtime/state_assertion_dsl.py``, ``runtime/state_coverage_policy.py`` and
  ``runtime/event_graph.py`` are imported from the checkout through a synthetic
  package, so their relative imports resolve without copying a line of Polaris.
* Nothing here runs at lstest runtime: only ``tests/`` and
  ``python -m tools.parity.harness`` import this module.
* A Polaris result is never synthesized.  If the checkout is missing the
  harness raises :class:`PolarisUnavailable`; if a module fails to import the
  exact exception is recorded in the report and the dependent checks fail
  loudly instead of silently passing.

Comparison modes
----------------
``status``
    Both engines return a verdict.  The Polaris verdict is translated through
    :data:`STATUS_MAP` and must equal the lstest verdict.
``set``
    Both engines return an observation set (causal graph edges, missing
    coverage states).  The check agrees when the two sets are identical.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
from pathlib import Path
from typing import Any, Mapping, Sequence

from lstest.scripts import assertions, attribution, coverage

__all__ = [
    "CONTRACT_KINDS",
    "SET_KINDS",
    "STATUS_MAP",
    "DEFAULT_POLARIS_EVENT_TYPES",
    "PolarisUnavailable",
    "PolarisModuleUnavailable",
    "ParityError",
    "PolarisPrimitives",
    "DEFAULT_FIXTURES",
    "DEFAULT_REPORT",
    "resolve_polaris_runtime",
    "load_polaris",
    "map_polaris_status",
    "load_scenarios",
    "run_parity",
    "render_table",
    "render_report",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = REPO_ROOT / "tools" / "parity" / "fixtures"
DEFAULT_REPORT = REPO_ROOT / "docs" / "migration" / "parity-report.md"

POLARIS_ROOT_ENV = "POLARIS_ROOT"
DEFAULT_POLARIS_ROOT = REPO_ROOT.parent / "Polaris"
POLARIS_RUNTIME_RELATIVE = Path("satellite") / "cucumber-agent-testing" / "runtime"

#: Import order matters only for report readability; dependencies resolve
#: themselves because the synthetic package is already in ``sys.modules``.
POLARIS_MODULES = (
    "events",
    "timeline",
    "assertion_engine",
    "assertion_dsl",
    "state_assertion_dsl",
    "state_coverage_policy",
    "event_graph",
)

_POLARIS_PACKAGE = "_lstest_parity_polaris_runtime"

#: The 11 rule kinds frozen in ``artifacts/absorption-api-contract.md``.
CONTRACT_KINDS = (
    "event_exists",
    "event_absent",
    "event_order",
    "event_within_ms",
    "event_not_during",
    "sequence",
    "duration",
    "response",
    "count_range",
    "state_required",
    "no_reboot",
)

#: Kinds compared as observation sets rather than verdict strings.
SET_KINDS = ("attribution_graph", "coverage_states")

#: Polaris verdict -> lstest verdict.  Unknown Polaris verdicts degrade to
#: ``AMBIGUOUS``: a new Polaris status must never be silently promoted to PASS.
STATUS_MAP: Mapping[str, str] = {
    "PASS": "PASS",
    "FAIL": "FAIL",
    "BLOCKED": "BLOCKED",
    "SKIP": "SKIP",
    "SKIPPED": "SKIP",
    "TIMING_AMBIGUOUS": "AMBIGUOUS",
    "WARN": "AMBIGUOUS",
    "PASS_WITH_SKIPPED_TIMING": "AMBIGUOUS",
}
UNMAPPED_POLARIS_STATUS = "AMBIGUOUS"

#: Default Polaris event type per lstest canonical key.  Fixtures may override
#: per fact with ``polaris_event_type``; the table is data, not a verdict.
DEFAULT_POLARIS_EVENT_TYPES: Mapping[str, str] = {
    "WAKE": "WakeDetected",
    "OFFLINE_ASR": "ASRDetected",
    "ONLINE_ASR": "CloudASRDetected",
    "COMMAND_ACK": "CommandDetected",
    "COMMAND_EVIDENCE": "CommandEvidenceDetected",
    "PLAY_URL": "MediaUrlIssued",
    "PLAYER": "MediaStarted",
    "RESTART": "RebootDetected",
    "DEVICE_EXCEPTION": "DeviceException",
    "REQUEST_ID": "RequestIssued",
    "RESPONSE_ID": "ResponseIssued",
}

REQUIRED_STATES_DEFAULT = ("preparation", "active", "terminal")


class ParityError(RuntimeError):
    """A fixture or harness invariant is broken (not a verdict mismatch)."""


class PolarisUnavailable(ParityError):
    """The Polaris checkout cannot be located or imported."""


class PolarisModuleUnavailable(ParityError):
    """One Polaris module failed to import; its checks must be reported, not faked."""


# ---------------------------------------------------------------------------
# Polaris loading
# ---------------------------------------------------------------------------


def resolve_polaris_runtime(root: str | Path | None = None) -> Path:
    """Return the Polaris ``runtime/`` directory, or raise."""
    base = Path(root or os.environ.get(POLARIS_ROOT_ENV) or DEFAULT_POLARIS_ROOT)
    candidates = (base, base / POLARIS_RUNTIME_RELATIVE)
    for candidate in candidates:
        if (candidate / "assertion_engine.py").is_file():
            return candidate.resolve()
    raise PolarisUnavailable(
        "polaris runtime not found; looked for runtime/assertion_engine.py under "
        f"{base} (set {POLARIS_ROOT_ENV} to override)"
    )


class _StubAttribute:
    """Permissive placeholder for an attribute of an unavailable package."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> "_StubAttribute":
        return self

    def __getattr__(self, item: str) -> "_StubAttribute":
        return _StubAttribute(f"{self._name}.{item}")

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<stub {self._name}>"


def _install_stub(name: str) -> None:
    module = types.ModuleType(name)
    module.__getattr__ = lambda item: _StubAttribute(f"{name}.{item}")  # type: ignore[attr-defined]
    sys.modules[name] = module


def _import_with_stubs(full_name: str, stubs: list[str]) -> Any:
    """Import a Polaris module, stubbing unavailable top-level packages once."""
    try:
        return importlib.import_module(full_name)
    except ModuleNotFoundError as error:
        root = (error.name or "").split(".")[0]
        if not root or root == _POLARIS_PACKAGE or root == full_name.split(".")[0]:
            raise
        if root in sys.modules:
            raise
        _install_stub(root)
        stubs.append(root)
        return importlib.import_module(full_name)


@dataclass(frozen=True)
class PolarisPrimitives:
    """The real Polaris runtime modules plus a timeline adapter."""

    root: Path
    modules: Mapping[str, Any]
    import_errors: Mapping[str, str] = field(default_factory=dict)
    stubs: tuple[str, ...] = ()

    # -- helpers ---------------------------------------------------------
    def require(self, module: str) -> Any:
        if module not in self.modules:
            detail = self.import_errors.get(module, "module was not imported")
            raise PolarisModuleUnavailable(f"polaris module {module!r} unavailable: {detail}")
        return self.modules[module]

    def timeline(self, facts: Sequence[Mapping[str, Any]]) -> Any:
        """Build a Polaris ``Timeline`` from fixture facts, preserving ts_ms."""
        events_module = self.require("events")
        timeline_module = self.require("timeline")
        events = []
        for index, fact in enumerate(facts):
            ts_ms = fact.get("ts_ms")
            event_type = fact_polaris_type(fact)
            events.append(
                events_module.make_event(
                    path=Path("parity.log"),
                    line_no=index + 1,
                    raw=f"{event_type}@{ts_ms}#{index}",
                    source=str(fact.get("source") or fact.get("role") or ""),
                    event_type=event_type,
                    payload=dict(fact.get("captures") or {}),
                    timestamp_monotonic_ms=ts_ms,
                )
            )
        return timeline_module.Timeline.from_events(events)

    def timeline_from_events(self, events: Sequence[Any]) -> Any:
        return self.require("timeline").Timeline(list(events))

    # -- dispatch --------------------------------------------------------
    def run(self, primitive: str, args: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> tuple[str, dict]:
        handler = _POLARIS_PRIMITIVES.get(primitive)
        if handler is None:
            raise ParityError(f"unknown polaris primitive {primitive!r}")
        timeline = self.timeline(facts)
        return handler(self, timeline, dict(args), facts)

    def describe(self) -> dict:
        return {
            "runtime": str(self.root),
            "modules": sorted(self.modules),
            "import_errors": dict(self.import_errors),
            "stubs": list(self.stubs),
        }


def load_polaris(root: str | Path | None = None) -> PolarisPrimitives:
    """Load the real Polaris runtime modules through a synthetic package."""
    runtime_dir = resolve_polaris_runtime(root)
    package = sys.modules.get(_POLARIS_PACKAGE)
    if package is None or not getattr(package, "__path__", None):
        package = types.ModuleType(_POLARIS_PACKAGE)
        package.__path__ = [str(runtime_dir)]  # type: ignore[attr-defined]
        package.__package__ = _POLARIS_PACKAGE
        sys.modules[_POLARIS_PACKAGE] = package
    modules: dict[str, Any] = {}
    errors: dict[str, str] = {}
    stubs: list[str] = []
    for name in POLARIS_MODULES:
        try:
            modules[name] = _import_with_stubs(f"{_POLARIS_PACKAGE}.{name}", stubs)
        except Exception as error:  # noqa: BLE001 - the exact error is reported
            errors[name] = f"{type(error).__name__}: {error}"
    if "assertion_engine" not in modules:
        raise PolarisUnavailable(
            "polaris runtime/assertion_engine.py could not be imported: "
            f"{errors.get('assertion_engine', 'unknown error')}"
        )
    return PolarisPrimitives(
        root=runtime_dir,
        modules=modules,
        import_errors=errors,
        stubs=tuple(dict.fromkeys(stubs)),
    )


# ---------------------------------------------------------------------------
# fact helpers
# ---------------------------------------------------------------------------


def normalized(fact: Mapping[str, Any]) -> str:
    return assertions.normalize_event_type(str(fact.get("event_type") or fact.get("key") or ""))


def fact_polaris_type(fact: Mapping[str, Any]) -> str:
    explicit = str(fact.get("polaris_event_type") or "").strip()
    if explicit:
        return explicit
    canonical = normalized(fact)
    return DEFAULT_POLARIS_EVENT_TYPES.get(canonical, canonical)


def mapped_type(event_type: str, facts: Sequence[Mapping[str, Any]]) -> str:
    """Polaris type for ``event_type``: from facts when observed, else defaults."""
    canonical = assertions.normalize_event_type(str(event_type))
    for fact in facts:
        if normalized(fact) == canonical:
            return fact_polaris_type(fact)
    return DEFAULT_POLARIS_EVENT_TYPES.get(canonical, canonical)


def state_token(event_type: str, state_class: str) -> str:
    canonical = assertions.normalize_event_type(str(event_type or "PLAYER")) or "PLAYER"
    return f"{canonical}_{str(state_class or '').strip()}".upper()


def state_tokens(facts: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        {
            state_token(normalized(fact), str(fact.get("state_class") or ""))
            for fact in facts
            if str(fact.get("state_class") or "").strip()
        }
    )


def map_polaris_status(status: str) -> str:
    """Translate a Polaris verdict into the lstest vocabulary."""
    return STATUS_MAP.get(str(status or "").strip().upper(), UNMAPPED_POLARIS_STATUS)


def _ts(fact: Mapping[str, Any]) -> float | None:
    value = fact.get("ts_ms")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Polaris primitives
# ---------------------------------------------------------------------------


def _prim_assert_event_exists(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_event_exists(
        timeline,
        args["event_type"],
        min_count=int(args.get("min_count", 1)),
        source=str(args.get("source") or ""),
    )
    return result.result, dict(result.actual)


def _prim_assert_no_event_exists(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_no_event_exists(timeline, args["event_type"], name=str(args.get("name") or ""))
    return result.result, dict(result.actual)


def _prim_assert_event_order(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_event_order(timeline, args["before"], args["after"])
    return result.result, dict(result.actual)


def _prim_assert_event_within_ms(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_event_within_ms(
        timeline,
        args["event_type"],
        within_ms=int(args["within_ms"]),
        anchor_event_type=str(args.get("anchor_event_type") or "AudioInjected"),
    )
    return result.result, dict(result.actual)


def _prim_assert_no_event_during(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_no_event_during(
        timeline,
        args["event_type"],
        duration_ms=int(args["duration_ms"]),
        anchor_event_type=args.get("anchor_event_type"),
    )
    return result.result, dict(result.actual)


def _prim_assert_no_runtime_reboot(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    engine = rt.require("assertion_engine")
    result = engine.assert_no_runtime_reboot(timeline)
    return result.result, dict(result.actual)


def _prim_assert_sequence(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    dsl = rt.require("assertion_dsl")
    result = dsl._assert_sequence(timeline, str(args["chain"]), int(args.get("within_ms", 10**9)))
    return str(result["result"]), dict(result.get("actual") or {})


def _prim_assert_duration_min(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    dsl = rt.require("assertion_dsl")
    result = dsl._assert_duration(timeline, str(args["start"]), str(args["end"]), int(args["minimum_ms"]))
    return str(result["result"]), dict(result.get("actual") or {})


def _prim_assert_response(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    dsl = rt.require("assertion_dsl")
    result = dsl._assert_response(
        timeline,
        str(args["response"]),
        str(args["anchor"]),
        int(args["within_ms"]),
    )
    return str(result["result"]), dict(result.get("actual") or {})


def _prim_assert_count_range(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    """Compose two Polaris primitives; the verdict comes from Polaris itself."""
    engine = rt.require("assertion_engine")
    event_type = args["event_type"]
    low = int(args.get("min", 0))
    high = args.get("max")
    parts = [engine.assert_event_exists(timeline, event_type, min_count=low)]
    if high is not None:
        matches = timeline.find(event_type)
        extra = matches[int(high):]
        parts.append(
            engine.assert_no_event_exists(
                rt.timeline_from_events(extra),
                event_type,
                name=f"count_max_{event_type}",
            )
        )
    status = engine.aggregate_result(parts)
    return status, {
        "count": len(timeline.find(event_type)),
        "min": low,
        "max": high,
        "parts": [item.result for item in parts],
    }


def _prim_state_expected(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    state_dsl = rt.require("state_assertion_dsl")
    token = str(args["token"])
    history = [{"event_type": item} for item in state_tokens(facts)]
    result = state_dsl.evaluate_state_dsl({"history": history}, f"EXPECT_HISTORY {token}")
    return str(result["result"]), {
        "token": token,
        "observed": history,
        "assertions": [item["name"] for item in result.get("assertions", [])],
    }


def _prim_build_event_graph(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    graph_module = rt.require("event_graph")
    rules = list(args.get("rules") or [])
    overlay = {"name": "lstest-parity", "rules": rules}
    graph = graph_module.build_event_graph(timeline, overlay)
    rule_ids = {str(rule.get("id") or "") for rule in rules}
    types_by_id = {node.event_id: node.event_type for node in graph.nodes}
    edges = sorted(
        {
            f"{types_by_id[edge.src]}>{types_by_id[edge.dst]}:{edge.relation}"
            for edge in graph.edges
            if edge.reason in rule_ids and edge.src in types_by_id and edge.dst in types_by_id
        }
    )
    return f"SET:{len(edges)}", {"edges": edges}


def _prim_coverage_policy(rt: PolarisPrimitives, timeline: Any, args: dict, facts: Sequence[Mapping]) -> tuple[str, dict]:
    policy_module = rt.require("state_coverage_policy")
    required = list(args.get("required_states") or [])
    visited = sorted({str(fact.get("state_class") or "").strip() for fact in facts if str(fact.get("state_class") or "").strip()})
    runtime_state = {"coverage": {"visited_states": visited}}
    policy = {"coverage": {"common": {"required_states": required}}}
    result = policy_module.evaluate_state_coverage_policy(runtime_state, "lstest-parity", policy)
    missing = sorted(
        str(item["name"]).split(":", 1)[1]
        for item in result.get("checks", [])
        if str(item["name"]).startswith("required_state:") and item["result"] == "WARN"
    )
    return str(result["result"]), {"missing": missing, "visited": visited}


_POLARIS_PRIMITIVES = {
    "assert_event_exists": _prim_assert_event_exists,
    "assert_no_event_exists": _prim_assert_no_event_exists,
    "assert_event_order": _prim_assert_event_order,
    "assert_event_within_ms": _prim_assert_event_within_ms,
    "assert_no_event_during": _prim_assert_no_event_during,
    "assert_no_runtime_reboot": _prim_assert_no_runtime_reboot,
    "assert_sequence": _prim_assert_sequence,
    "assert_duration_min": _prim_assert_duration_min,
    "assert_response": _prim_assert_response,
    "assert_count_range": _prim_assert_count_range,
    "state_expected": _prim_state_expected,
    "build_event_graph": _prim_build_event_graph,
    "coverage_policy": _prim_coverage_policy,
}


# ---------------------------------------------------------------------------
# lstest side
# ---------------------------------------------------------------------------


def _lstest_edges(facts: Sequence[Mapping], check: Mapping[str, Any]) -> list[str]:
    """lstest causal edges rendered in the Polaris event-type vocabulary.

    The two engines keep different vocabularies (``WAKE`` vs ``WakeDetected``);
    edges are compared after mapping through the fixture's own type
    declarations, so a renamed event shows up as a mismatch instead of hiding.
    """
    graph = attribution.build_event_graph(facts, rules=check.get("rules"))
    types_by_id = {node.node_id: node.event_type for node in graph.nodes}
    return sorted(
        {
            f"{mapped_type(types_by_id[edge.src], facts)}>"
            f"{mapped_type(types_by_id[edge.dst], facts)}:{edge.relation}"
            for edge in graph.edges
        }
    )


def _lstest_missing_states(facts: Sequence[Mapping], check: Mapping[str, Any]) -> list[str]:
    required = check.get("required_states") or REQUIRED_STATES_DEFAULT
    return sorted(coverage.state_coverage(facts, required=required)["missing"])


def _lstest_verdict(check: Mapping[str, Any], facts: Sequence[Mapping]) -> tuple[str, dict]:
    rule = {key: value for key, value in check.items() if key not in {"polaris", "compare", "description"}}
    result = assertions.evaluate([rule], facts)
    outcome = result["outcomes"][0]
    return result["status"], outcome.to_dict()


_LSTEST_SET_EXTRACTORS = {
    "attribution_graph": _lstest_edges,
    "coverage_states": _lstest_missing_states,
}


def _lstest_observation(check: Mapping[str, Any], facts: Sequence[Mapping]) -> tuple[list[str], str]:
    kind = str(check.get("kind") or "")
    extractor = _LSTEST_SET_EXTRACTORS[kind]
    values = extractor(facts, check)
    return values, f"SET:{len(values)}"


# ---------------------------------------------------------------------------
# fixture validation
# ---------------------------------------------------------------------------


def _guard(condition: bool, message: str) -> None:
    if not condition:
        raise ParityError(message)


def _validate_facts(scenario: str, facts: Sequence[Mapping[str, Any]]) -> None:
    _guard(bool(facts), f"{scenario}: facts must not be empty")
    previous: float | None = None
    for index, fact in enumerate(facts):
        _guard(isinstance(fact, Mapping), f"{scenario}: fact #{index} must be an object")
        _guard(bool(str(fact.get("event_type") or "")), f"{scenario}: fact #{index} needs event_type")
        ts = _ts(fact)
        if ts is not None and previous is not None:
            _guard(
                ts >= previous,
                f"{scenario}: fact #{index} ts_ms {ts} is before the previous fact; "
                "both engines sort on timestamps, so fixture order must be chronological",
            )
        if ts is not None:
            previous = ts


def _validate_check(scenario: str, check: Mapping[str, Any], facts: Sequence[Mapping]) -> None:
    kind = str(check.get("kind") or "")
    name = str(check.get("name") or "?")
    where = f"{scenario}: check {name!r}"
    polaris = check.get("polaris")
    _guard(isinstance(polaris, Mapping), f"{where} must declare a polaris expectation")
    primitive = str(polaris.get("primitive") or "")
    _guard(bool(primitive), f"{where} must declare polaris.primitive")
    args = dict(polaris.get("args") or {})
    _guard(primitive in _POLARIS_PRIMITIVES, f"{where}: unknown polaris primitive {primitive!r}")

    def candidate_types(lstest_type: str) -> set[str]:
        """Polaris event types the fixture maps ``lstest_type`` onto."""
        canonical = assertions.normalize_event_type(str(lstest_type))
        found = {
            fact_polaris_type(fact)
            for fact in facts
            if normalized(fact) == canonical
        }
        return found or {DEFAULT_POLARIS_EVENT_TYPES.get(canonical, canonical)}

    def expect_type(field_name: str, lstest_type: str) -> None:
        allowed = candidate_types(lstest_type)
        _guard(
            str(args.get(field_name) or "") in allowed,
            f"{where}: polaris {field_name}={args.get(field_name)!r} is not one of the "
            f"polaris event types the fixture maps {lstest_type!r} onto ({sorted(allowed)})",
        )

    if kind == "event_exists":
        expect_type("event_type", str(check.get("event_type") or ""))
        _guard(
            int(args.get("min_count", 1)) == int(check.get("min_count", 1)),
            f"{where}: min_count differs between engines",
        )
        _guard(
            str(args.get("source") or "") == str(check.get("source") or ""),
            f"{where}: source filter differs between engines",
        )
    elif kind == "event_absent":
        expect_type("event_type", str(check.get("event_type") or ""))
    elif kind == "event_order":
        expect_type("before", str(check.get("before") or ""))
        expect_type("after", str(check.get("after") or ""))
    elif kind == "event_within_ms":
        expect_type("event_type", str(check.get("to") or ""))
        expect_type("anchor_event_type", str(check.get("from") or ""))
        _guard(
            int(args.get("within_ms", -1)) == int(check.get("max_ms", -2)),
            f"{where}: within_ms differs from max_ms",
        )
    elif kind == "duration":
        expect_type("event_type", str(check.get("end_event") or ""))
        expect_type("anchor_event_type", str(check.get("start_event") or ""))
        _guard(
            int(args.get("within_ms", -1)) == int(check.get("max_ms", -2)),
            f"{where}: within_ms differs from max_ms",
        )
    elif kind == "event_not_during":
        expect_type("event_type", str(check.get("event_type") or ""))
        expect_type("anchor_event_type", str(check.get("window_from") or ""))
        window = _window_ms(facts, str(check.get("window_from") or ""), str(check.get("window_to") or ""))
        _guard(window is not None, f"{where}: window events are not both timestamped")
        _guard(
            int(args.get("duration_ms", -1)) == int(window),
            f"{where}: duration_ms={args.get('duration_ms')!r} does not match the "
            f"fact window ({window}ms)",
        )
    elif kind == "sequence":
        steps = [str(step) for step in check.get("steps") or []]
        parts = [part.strip() for part in str(args.get("chain") or "").split("->")]
        _guard(len(parts) == len(steps), f"{where}: chain length differs from steps length")
        for step, part in zip(steps, parts):
            allowed = candidate_types(step)
            _guard(
                part in allowed,
                f"{where}: chain step {part!r} is not one of the polaris types for {step!r} ({sorted(allowed)})",
            )
    elif kind == "response":
        expect_type("anchor", str(check.get("request") or ""))
        expect_type("response", str(check.get("response") or ""))
        _guard(
            int(args.get("within_ms", -1)) == int(check.get("max_ms", -2)),
            f"{where}: within_ms differs from max_ms",
        )
    elif kind == "count_range":
        expect_type("event_type", str(check.get("event_type") or ""))
        _guard(int(args.get("min", 0)) == int(check.get("min", 0)), f"{where}: min differs")
        _guard(args.get("max") == check.get("max"), f"{where}: max differs")
    elif kind == "state_required":
        expected = state_token(str(check.get("event_type") or "PLAYER"), str(check.get("state_class") or ""))
        _guard(str(args.get("token") or "") == expected, f"{where}: token {args.get('token')!r} != {expected!r}")
    elif kind == "no_reboot":
        _guard(primitive == "assert_no_runtime_reboot", f"{where}: no_reboot must use assert_no_runtime_reboot")
    elif kind == "attribution_graph":
        rules = list(check.get("rules") or [])
        overlay_rules = list(args.get("rules") or [])
        _guard(len(rules) == len(overlay_rules), f"{where}: rule counts differ between engines")
        for rule, overlay in zip(rules, overlay_rules):
            _guard(
                str(rule.get("relation")) == str(overlay.get("relation")),
                f"{where}: relation differs between engines",
            )
            sources = _as_list(rule.get("from"))
            _guard(bool(sources), f"{where}: rule needs at least one 'from' event type")
            expected_src = set().union(*[candidate_types(item) for item in sources])
            _guard(
                set(_as_list((overlay.get("src") or {}).get("event_types"))) == expected_src,
                f"{where}: rule source event types differ between engines "
                f"({sorted(expected_src)} vs {_as_list((overlay.get('src') or {}).get('event_types'))})",
            )
            targets = [item for item in _as_list(rule.get("to")) if str(item) != "*"]
            _guard(
                bool(targets),
                f"{where}: wildcard causal rules are out of parity scope; list explicit targets",
            )
            expected_dst = set().union(*[candidate_types(item) for item in targets])
            _guard(
                set(_as_list((overlay.get("dst") or {}).get("event_types"))) == expected_dst,
                f"{where}: rule destination event types differ between engines "
                f"({sorted(expected_dst)} vs {_as_list((overlay.get('dst') or {}).get('event_types'))})",
            )
    elif kind == "coverage_states":
        _guard(
            list(args.get("required_states") or []) == list(check.get("required_states") or []),
            f"{where}: required_states differ between engines",
        )
    else:
        raise ParityError(f"{where}: unsupported check kind {kind!r}")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _window_ms(facts: Sequence[Mapping], window_from: str, window_to: str) -> float | None:
    starts = [ts for ts in (_ts(fact) for fact in facts if normalized(fact) == assertions.normalize_event_type(window_from)) if ts is not None]
    ends = [ts for ts in (_ts(fact) for fact in facts if normalized(fact) == assertions.normalize_event_type(window_to)) if ts is not None]
    if not starts or not ends:
        return None
    start = min(starts)
    later = [item for item in ends if item >= start]
    if not later:
        return None
    return min(later) - start


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


def load_scenarios(fixtures_dir: str | Path) -> list[dict]:
    """Load and validate every ``*.json`` scenario in ``fixtures_dir``."""
    directory = Path(fixtures_dir)
    if not directory.is_dir():
        raise ParityError(f"fixtures directory not found: {directory}")
    scenarios: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        _guard(isinstance(payload, Mapping), f"{path.name}: scenario must be an object")
        name = str(payload.get("name") or path.stem)
        facts = list(payload.get("facts") or [])
        checks = list(payload.get("checks") or [])
        _guard(bool(checks), f"{name}: scenario has no checks")
        _validate_facts(name, facts)
        for check in checks:
            _validate_check(name, check, facts)
        payload = dict(payload)
        payload["name"] = name
        payload["facts"] = facts
        payload["checks"] = checks
        payload["__path__"] = str(path)
        scenarios.append(payload)
    _guard(bool(scenarios), f"no fixture scenarios found in {directory}")
    return scenarios


def _control_scenario() -> dict:
    """A deliberately wrong expectation used to prove the detector works.

    Never shipped as a fixture file: it is injected only with
    ``--expect-mismatch``, and it must always disagree.
    """
    return {
        "name": "control_expected_mismatch",
        "description": "Injected control: asserts 9 wakes where the facts carry 1.",
        "facts": [
            {"event_type": "WAKE", "source": "ap", "role": "ap", "port": "COM14", "sequence": 1, "ts_ms": 0},
        ],
        "checks": [
            {
                "name": "control_wake_count",
                "kind": "event_exists",
                "event_type": "WAKE",
                "min_count": 9,
                "polaris": {"primitive": "assert_event_exists", "args": {"event_type": "WakeDetected", "min_count": 1}},
            }
        ],
        "__path__": "<injected control>",
        "control": True,
    }


def run_parity(
    fixtures_dir: str | Path = DEFAULT_FIXTURES,
    *,
    polaris_root: str | Path | None = None,
    control: bool = False,
) -> dict:
    """Compare lstest verdicts against the real Polaris primitives.

    Returns per-check tuples under ``rows`` plus the detailed records under
    ``checks`` and aggregate ``counts``.
    """
    primitives = load_polaris(polaris_root)
    scenarios = load_scenarios(fixtures_dir)
    if control:
        scenarios = scenarios + [_control_scenario()]

    checks: list[dict] = []
    rows: list[tuple[str, str, str, str, bool]] = []
    status_counts: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}

    for scenario in scenarios:
        name = str(scenario["name"])
        facts = scenario["facts"]
        for check in scenario["checks"]:
            kind = str(check.get("kind") or "")
            check_name = str(check.get("name") or "?")
            polaris_block = dict(check.get("polaris") or {})
            primitive = str(polaris_block.get("primitive") or "")
            args = dict(polaris_block.get("args") or {})
            mode = "set" if kind in SET_KINDS else "status"

            lstest_detail: dict = {}
            if mode == "set":
                lstest_values, lstest_status = _lstest_observation(check, facts)
            else:
                lstest_status, lstest_detail = _lstest_verdict(check, facts)

            try:
                polaris_raw, polaris_detail = primitives.run(primitive, args, facts)
            except PolarisModuleUnavailable as error:
                # A module that cannot be imported is reported per check and in
                # the report; the remaining primitives still get covered.
                polaris_raw = "IMPORT_ERROR"
                polaris_detail = {"error": str(error)}
            except ParityError:
                raise
            except Exception as error:  # noqa: BLE001 - fixture/harness defect
                raise ParityError(
                    f"{name}: check {check_name!r} polaris primitive {primitive!r} raised "
                    f"{type(error).__name__}: {error}"
                ) from error

            if polaris_raw == "IMPORT_ERROR":
                polaris_status = "IMPORT_ERROR"
                agreed = False
                detail = {"error": polaris_detail}
            elif mode == "set":
                polaris_values = list(
                    polaris_detail.get("edges") or polaris_detail.get("missing") or []
                )
                agreed = lstest_values == polaris_values
                polaris_status = f"SET:{len(polaris_values)}"
                detail = {
                    "lstest_observation": lstest_values,
                    "polaris_observation": polaris_detail,
                }
            else:
                polaris_status = map_polaris_status(polaris_raw)
                agreed = lstest_status == polaris_status
                detail = {"lstest": lstest_detail, "polaris": polaris_detail}

            record = {
                "scenario": name,
                "check": check_name,
                "kind": kind,
                "mode": mode,
                "primitive": primitive,
                "lstest_status": lstest_status,
                "polaris_raw": polaris_raw,
                "polaris_status": polaris_status,
                "agreed": bool(agreed),
                "detail": detail,
            }
            checks.append(record)
            rows.append((name, check_name, lstest_status, polaris_status, bool(agreed)))
            status_counts[lstest_status] = status_counts.get(lstest_status, 0) + 1
            bucket = by_kind.setdefault(kind, {"checks": 0, "agreed": 0})
            bucket["checks"] += 1
            bucket["agreed"] += 1 if agreed else 0

    covered = [kind for kind in CONTRACT_KINDS if kind in by_kind]
    counts = {
        "scenarios": len(scenarios),
        "total": len(checks),
        "agreed": sum(1 for item in checks if item["agreed"]),
        "mismatched": sum(1 for item in checks if not item["agreed"]),
        "kinds": len(by_kind),
        "contract_kinds_covered": covered,
        "contract_kinds_missing": [kind for kind in CONTRACT_KINDS if kind not in by_kind],
        "by_kind": by_kind,
        "lstest_status_counts": dict(sorted(status_counts.items())),
    }
    return {
        "schema": "lstest.parity_result.v1",
        "generated_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "fixtures": str(Path(fixtures_dir)),
        "control": bool(control),
        "polaris": primitives.describe(),
        "counts": counts,
        "checks": checks,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_table(result: Mapping[str, Any]) -> str:
    counts = result["counts"]
    lines = [
        f"scenarios={counts['scenarios']} checks={counts['total']} "
        f"agreed={counts['agreed']} mismatched={counts['mismatched']}",
        f"kinds covered={len(counts['contract_kinds_covered'])}/{len(CONTRACT_KINDS)} "
        f"missing={counts['contract_kinds_missing'] or 'none'}",
        "",
        f"{'scenario':<28} {'check':<30} {'lstest':<14} {'polaris':<14} ok",
        "-" * 94,
    ]
    for scenario, check, lstest_status, polaris_status, agreed in result["rows"]:
        lines.append(
            f"{scenario[:28]:<28} {check[:30]:<30} {lstest_status[:14]:<14} "
            f"{polaris_status[:14]:<14} {'yes' if agreed else 'NO'}"
        )
    return "\n".join(lines)


def _mapping_rows() -> list[tuple[str, str]]:
    order = ("PASS", "FAIL", "BLOCKED", "SKIP", "SKIPPED", "TIMING_AMBIGUOUS", "WARN", "PASS_WITH_SKIPPED_TIMING")
    return [(polaris, STATUS_MAP[polaris]) for polaris in order]


_KNOWN_DIVERGENCES = (
    (
        "缺少锚点事件",
        "lstest `event_within_ms`/`duration` 在 from/start 缺失时判 FAIL(`missing_event`)；"
        "Polaris `assert_event_within_ms` 在锚点缺失时返回 SKIP。两者语义不同，"
        "shipped fixtures 一律提供双侧事件，因此该差异不参与本次对照。",
    ),
    (
        "`response.match_capture` 关联",
        "Polaris 没有按 capture 关联请求/响应的原语，只有 `_assert_response` 的时间分量。"
        "本次只对照时间判定；关联相等（correlation）是 lstest 独有语义，"
        "仅在关联成立的 PASS 用例上与 Polaris 结论一致，关联不符的 FAIL 用例不在对照范围内。",
    ),
    (
        "通配因果规则",
        "lstest `DEFAULT_CAUSAL_RULES` 的 `RESTART|DEVICE_EXCEPTION -> *` 通配规则需要显式目标集合才能落到 "
        "Polaris 的 rule overlay；通配边不参与本次图对照，只对照显式 from/to 规则产生的边。",
    ),
    (
        "`coverage.py` 的角色/能力维度",
        "Polaris 没有 role 概念，也没有 capability matrix 原语（它从 env 推导 capability，语义不同），"
        "因此覆盖对照只比较状态维度（required states）的缺失集合。",
    ),
    (
        "缺少时间戳",
        "Polaris `Timeline._normalize_monotonic_clock` 会给无时间戳事件补一个序号时钟，"
        "而 lstest 的时序规则在缺 ts 时判 SKIP(`no_timestamp`)。两者不可比，fixtures 全部带 ts_ms。",
    ),
    (
        "状态映射",
        "`WARN`/`TIMING_AMBIGUOUS` 统一映射为 lstest `AMBIGUOUS`：它们都表示“证据不足以判定”"
        "而不是通过；未登记的新 Polaris 状态同样降级为 `AMBIGUOUS`，绝不静默转成 PASS。",
    ),
)


def render_report(result: Mapping[str, Any], command: str) -> str:
    counts = result["counts"]
    polaris = result["polaris"]
    lines: list[str] = []
    lines.append("<!-- 由 tools/parity/harness.py 生成；数字来自本次真实运行，禁止手改 -->")
    lines.append("")
    lines.append("# lstest ↔ Polaris 判定一致性报告")
    lines.append("")
    lines.append("本报告由 parity harness 在离线环境直接加载 **真实 Polaris 运行时原语**后生成。")
    lines.append("lstest 侧调用 `scripts/assertions.py` / `scripts/attribution.py` / `scripts/coverage.py`，")
    lines.append("Polaris 侧调用其 `runtime/` 下的同名能力，两侧输入同一事件序列。")
    lines.append("")
    lines.append("## 运行信息")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 生成时间（北京时间，UTC+8，YYYY-MM-DD HH:MM:SS） | {result['generated_at']} |")
    lines.append(f"| 命令 | `{command}` |")
    lines.append(f"| fixtures | `{result['fixtures']}` |")
    lines.append(f"| Polaris runtime | `{polaris['runtime']}` |")
    lines.append(f"| 成功导入的 Polaris 模块 | {', '.join(polaris['modules'])} |")
    lines.append(f"| 注入的 stub 包 | {', '.join(polaris['stubs']) or '无'} |")
    lines.append(
        "| 导入失败 | "
        + (", ".join(f"`{name}`: {error}" for name, error in polaris["import_errors"].items()) or "无")
        + " |"
    )
    lines.append(f"| 是否含注入对照用例 | {'是（--expect-mismatch）' if result['control'] else '否'} |")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 场景数 | {counts['scenarios']} |")
    lines.append(f"| 对照项总数 | {counts['total']} |")
    lines.append(f"| 一致 | {counts['agreed']} |")
    lines.append(f"| 不一致 | {counts['mismatched']} |")
    lines.append(f"| 覆盖的合同 kind | {len(counts['contract_kinds_covered'])}/{len(CONTRACT_KINDS)} |")
    lines.append(f"| 缺失的合同 kind | {', '.join(counts['contract_kinds_missing']) or '无'} |")
    lines.append(
        "| lstest 结论分布 | "
        + "，".join(f"{status}={value}" for status, value in counts["lstest_status_counts"].items())
        + " |"
    )
    lines.append("")
    lines.append("## 状态映射（显式）")
    lines.append("")
    lines.append("| Polaris | lstest | 依据 |")
    lines.append("| --- | --- | --- |")
    justifications = {
        "PASS": "同名同义：证据满足期望。",
        "FAIL": "同名同义：证据违反期望。",
        "BLOCKED": "同名同义：缺少执行前提，不能判定。",
        "SKIP": "同名同义：证据不足但不构成失败。",
        "SKIPPED": "Polaris 覆盖策略无任何检查项时返回 SKIPPED，等价于 lstest SKIP。",
        "TIMING_AMBIGUOUS": "Polaris 表示时序无法确认；lstest 用 AMBIGUOUS 表达同一含义。",
        "WARN": "Polaris 的非致命偏离信号；lstest 用 AMBIGUOUS 保留“不可判 PASS”的语义。",
        "PASS_WITH_SKIPPED_TIMING": "有时序项被跳过，不能算干净的 PASS，降级为 AMBIGUOUS。",
    }
    for polaris_status, lstest_status in _mapping_rows():
        lines.append(f"| `{polaris_status}` | `{lstest_status}` | {justifications[polaris_status]} |")
    lines.append("")
    lines.append("未登记的新 Polaris 状态一律映射为 `AMBIGUOUS`，不会静默变成 `PASS`。")
    lines.append("")
    lines.append("## 逐 kind 结果")
    lines.append("")
    lines.append("| kind | 对照项 | 一致 |")
    lines.append("| --- | --- | --- |")
    for kind in sorted(counts["by_kind"]):
        bucket = counts["by_kind"][kind]
        lines.append(f"| `{kind}` | {bucket['checks']} | {bucket['agreed']} |")
    lines.append("")
    lines.append("## 逐项结果")
    lines.append("")
    lines.append("| 场景 | 对照项 | kind | 模式 | lstest | polaris | 一致 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in result["checks"]:
        lines.append(
            f"| {item['scenario']} | {item['check']} | `{item['kind']}` | {item['mode']} | "
            f"`{item['lstest_status']}` | `{item['polaris_status']}` | "
            f"{'是' if item['agreed'] else '**否**'} |"
        )
    lines.append("")
    lines.append("## 已知差异（未参与对照的原因）")
    lines.append("")
    for title, body in _KNOWN_DIVERGENCES:
        lines.append(f"- **{title}**：{body}")
    lines.append("")
    if result["control"]:
        lines.append("## 检测器对照实验（--expect-mismatch）")
        lines.append("")
        control_checks = [item for item in result["checks"] if item["scenario"] == "control_expected_mismatch"]
        detected = [item for item in control_checks if not item["agreed"]]
        lines.append(
            f"注入 1 个故意写错的对照组（`control_expected_mismatch`），"
            f"共 {len(control_checks)} 项，检出不一致 {len(detected)} 项。"
        )
        lines.append("")
        lines.append("| 对照项 | lstest | polaris | 一致 |")
        lines.append("| --- | --- | --- | --- |")
        for item in control_checks:
            lines.append(
                f"| {item['check']} | `{item['lstest_status']}` | `{item['polaris_status']}` | "
                f"{'是' if item['agreed'] else '**否**'} |"
            )
        lines.append("")
        lines.append("该对照组只存在于带 `--expect-mismatch` 的运行中，仓库里的 fixtures 不含故意失败的用例。")
        lines.append("")
    lines.append("## 检测器自检")
    lines.append("")
    lines.append("带 `--expect-mismatch` 运行会临时注入一个故意写错的对照场景（不落盘为 fixture），")
    lines.append("harness 必须报告该项不一致并以退出码 1 结束；`tests/test_parity_polaris.py` 会断言这一点。")
    lines.append("")
    lines.append("```bash")
    lines.append("<venv>/bin/python -m tools.parity.harness --expect-mismatch   # 期望 exit 1")
    lines.append("```")
    lines.append("")
    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append("# 在 lstest 仓库根目录（推荐）")
    lines.append("cd <repos>/lstest && PYTHONPATH=<repos> <venv>/bin/python -m tools.parity.harness --fixtures tools/parity/fixtures")
    lines.append("# 或从 repos 目录用完整包名")
    lines.append("cd <repos> && PYTHONPATH=<repos> <venv>/bin/python -m lstest.tools.parity.harness --fixtures lstest/tools/parity/fixtures")
    lines.append("cd <repos> && PYTHONPATH=<repos> <venv>/bin/python -m unittest discover -s lstest/tests -t .")
    lines.append("```")
    lines.append("")
    lines.append("Polaris checkout 缺失时 harness 以退出码 2 结束并说明原因，不会伪造 Polaris 结论。")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.parity.harness",
        description="Compare lstest verdicts with the real Polaris runtime primitives.",
    )
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES), help="fixtures directory")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="markdown report path")
    parser.add_argument("--polaris-root", default=None, help="Polaris checkout (default: $POLARIS_ROOT)")
    parser.add_argument(
        "--expect-mismatch",
        action="store_true",
        help=(
            "inject the control scenario that must disagree with Polaris and exit 1; "
            "used to prove the detector works without shipping a broken fixture"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the raw result JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    invoked = list(sys.argv[1:] if argv is None else argv)
    command = " ".join(["python", "-m", "tools.parity.harness", *invoked])
    try:
        result = run_parity(args.fixtures, polaris_root=args.polaris_root, control=args.expect_mismatch)
    except PolarisUnavailable as error:
        print(f"POLARIS UNAVAILABLE: {error}", file=sys.stderr)
        return 2
    except ParityError as error:
        print(f"PARITY ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_table(result))

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result, command), encoding="utf-8")

    counts = result["counts"]
    print(
        f"report written: {report_path} "
        f"(scenarios={counts['scenarios']} checks={counts['total']} "
        f"agreed={counts['agreed']} mismatched={counts['mismatched']})"
    )
    if args.expect_mismatch:
        control = [item for item in result["checks"] if item["scenario"] == "control_expected_mismatch"]
        detected = [item for item in control if not item["agreed"]]
        if detected:
            print(f"CONTROL: expected mismatch detected ({len(detected)}/{len(control)}) -> exit 1")
            return 1
        print("CONTROL: the injected mismatch was NOT detected; the detector is broken -> exit 1")
        return 1
    return 0 if counts["mismatched"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
