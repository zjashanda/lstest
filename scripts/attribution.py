"""Heuristic causal graph and failure attribution over canonical facts.

The graph is built from data-driven :data:`DEFAULT_CAUSAL_RULES`; a project
may pass its own rule list through ``build_event_graph(..., rules=...)``.
Nothing here knows a project name, a command word, or a business value.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .assertions import STATUS_FAIL, coerce_facts, normalize_event_type

__all__ = [
    "GraphNode",
    "GraphEdge",
    "EventGraph",
    "DEFAULT_CAUSAL_RULES",
    "build_event_graph",
    "attribute",
    "render_markdown",
    "fingerprint",
]


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_types(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: Sequence[Any] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = tuple(value)
    else:
        items = (value,)
    return [normalize_event_type(item) for item in items if _as_text(item).strip()]


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    event_type: str
    label: str
    ts_ms: float | None
    source: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "event_type": self.event_type,
            "label": self.label,
            "ts_ms": self.ts_ms,
            "source": self.source,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class GraphEdge:
    src: str
    dst: str
    relation: str
    delta_ms: int | None

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "dst": self.dst,
            "relation": self.relation,
            "delta_ms": self.delta_ms,
        }


@dataclass(frozen=True)
class EventGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    roots: tuple[str, ...]
    terminal: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "schema": "lstest.event_graph.v1",
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "roots": list(self.roots),
            "terminal": list(self.terminal),
        }


#: Data-only causal overlay.  ``RESTART`` / ``DEVICE_EXCEPTION`` point at any
#: later event because they are failure antecedents rather than normal flow.
DEFAULT_CAUSAL_RULES: tuple[Mapping[str, Any], ...] = (
    {
        "from": "WAKE",
        "to": ["OFFLINE_ASR", "ONLINE_ASR"],
        "relation": "trigger",
        "within_ms": 3000,
        "require_order": True,
    },
    {
        "from": "REQUEST_ID",
        "to": ["RESPONSE_ID"],
        "relation": "request_response",
        "within_ms": 5000,
        "require_order": True,
    },
    {
        "from": "ONLINE_ASR",
        "to": ["COMMAND_ACK", "COMMAND_EVIDENCE"],
        "relation": "command",
        "within_ms": 5000,
        "require_order": True,
    },
    {
        "from": "PLAY_URL",
        "to": ["PLAYER"],
        "relation": "playback",
        "within_ms": 5000,
        "require_order": True,
    },
    {
        "from": "RESTART",
        "to": ["*"],
        "relation": "preceded_failure",
        "within_ms": 60000,
        "require_order": True,
    },
    {
        "from": "DEVICE_EXCEPTION",
        "to": ["*"],
        "relation": "preceded_failure",
        "within_ms": 60000,
        "require_order": True,
    },
)


def build_event_graph(
    facts: Iterable[Any], *, rules: Sequence[Mapping[str, Any]] | None = None
) -> EventGraph:
    """Build a causal graph; edges exist only when order and window hold."""
    items = coerce_facts(facts)
    nodes = tuple(
        GraphNode(
            node_id=f"n{index}",
            event_type=fact.event_type,
            label=fact.value or fact.event_type,
            ts_ms=fact.ts_ms,
            source=fact.source,
            evidence=tuple(fact.evidence),
        )
        for index, fact in enumerate(items)
    )
    active_rules = DEFAULT_CAUSAL_RULES if rules is None else tuple(rules)
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for rule in active_rules:
        if not isinstance(rule, Mapping):
            continue
        from_types = _as_types(rule.get("from"))
        raw_to = rule.get("to")
        wildcard = any(_as_text(item).strip() == "*" for item in _as_list(raw_to))
        to_types = [item for item in _as_types(raw_to) if item != "*"]
        if not from_types or (not to_types and not wildcard):
            continue
        relation = _as_text(rule.get("relation") or "causal")
        within_ms = rule.get("within_ms")
        try:
            within = None if within_ms is None else float(within_ms)
        except (TypeError, ValueError):
            within = None
        require_order = bool(rule.get("require_order", True))
        for dst_index, node in enumerate(nodes):
            if not wildcard and node.event_type not in to_types:
                continue
            if wildcard and node.event_type in from_types:
                continue
            if node.ts_ms is None:
                continue
            candidates: list[tuple[float, int]] = []
            for src_index, candidate in enumerate(nodes):
                if candidate.event_type not in from_types:
                    continue
                if candidate.ts_ms is None or src_index == dst_index:
                    continue
                delta = node.ts_ms - candidate.ts_ms
                if require_order and delta < 0:
                    continue
                if within is not None and delta > within:
                    continue
                if delta < 0:
                    continue
                candidates.append((delta, src_index))
            if not candidates:
                continue
            delta, src_index = min(candidates, key=lambda item: (item[0], item[1]))
            key = (nodes[src_index].node_id, node.node_id, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                GraphEdge(
                    src=nodes[src_index].node_id,
                    dst=node.node_id,
                    relation=relation,
                    delta_ms=int(round(delta)),
                )
            )
    incoming = {edge.dst for edge in edges}
    outgoing = {edge.src for edge in edges}
    roots = tuple(node.node_id for node in nodes if node.node_id not in incoming)
    terminal = tuple(node.node_id for node in nodes if node.node_id not in outgoing)
    return EventGraph(
        nodes=nodes,
        edges=tuple(edges),
        roots=roots,
        terminal=terminal,
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


_RELATED_KEYS = (
    "event_type",
    "from",
    "to",
    "before",
    "after",
    "start_event",
    "end_event",
    "request",
    "response",
    "window_from",
    "window_to",
    "steps",
    "missing_step",
    "missing",
)


def _outcome_status(outcome: Any) -> str:
    if isinstance(outcome, Mapping):
        return _as_text(outcome.get("status"))
    return _as_text(getattr(outcome, "status", ""))


def _outcome_actual(outcome: Any) -> Mapping[str, Any]:
    if isinstance(outcome, Mapping):
        actual = outcome.get("actual") or {}
    else:
        actual = getattr(outcome, "actual", {}) or {}
    return actual if isinstance(actual, Mapping) else {}


def _related_types(actual: Mapping[str, Any]) -> list[str]:
    related: list[str] = []
    for key in _RELATED_KEYS:
        for event_type in _as_types(actual.get(key)):
            if event_type and event_type not in related:
                related.append(event_type)
    return related


def _suspect(node_id: str, event_type: str, reason: str, confidence: float) -> dict:
    return {
        "node_id": node_id,
        "event_type": event_type,
        "reason": reason,
        "confidence": confidence,
    }


def attribute(
    outcomes: Sequence[Any],
    graph: EventGraph,
    *,
    facts: Iterable[Any] | None = None,
) -> dict:
    """Rank likely causes of a failing outcome against the causal graph."""
    notes: list[str] = []
    if not graph.nodes:
        return {
            "status": "NOT_APPLICABLE",
            "suspects": [],
            "missing": [],
            "notes": ["event graph is empty"],
        }
    failures = [outcome for outcome in outcomes if _outcome_status(outcome) == STATUS_FAIL]
    non_failures = [
        outcome
        for outcome in outcomes
        if _outcome_status(outcome) not in (STATUS_FAIL, "")
    ]
    notes.append(f"graph nodes={len(graph.nodes)} edges={len(graph.edges)}")
    notes.append(f"failures={len(failures)}")
    if not failures:
        if not non_failures:
            notes.append("no outcomes were evaluated")
        status = "NO_SUSPECT"
        return {"status": status, "suspects": [], "missing": [], "notes": notes}

    known_types = {node.event_type for node in graph.nodes}
    related: list[str] = []
    for outcome in failures:
        for event_type in _related_types(_outcome_actual(outcome)):
            if event_type not in related:
                related.append(event_type)

    missing = [event_type for event_type in related if event_type not in known_types]
    suspects: list[dict] = []
    for event_type in missing:
        suspects.append(_suspect("", event_type, "missing_related_event", 1.0))

    if facts is not None:
        observed = {fact.event_type for fact in coerce_facts(facts)}
        for event_type in related:
            if event_type in observed and event_type not in known_types:
                notes.append(f"{event_type} observed in facts but absent from graph")
    if missing:
        notes.append("missing related events: " + ", ".join(missing))

    incoming = {edge.dst for edge in graph.edges}
    for node in graph.nodes:
        if node.event_type in {"RESTART", "DEVICE_EXCEPTION"}:
            suspects.append(_suspect(node.node_id, node.event_type, "failure_antecedent", 0.9))

    for event_type in related:
        if event_type in missing:
            continue
        nodes = [node for node in graph.nodes if node.event_type == event_type]
        for node in nodes:
            if node.node_id not in incoming:
                suspects.append(
                    _suspect(node.node_id, node.event_type, "unattributed_related_event", 0.8)
                )
        if nodes:
            last = nodes[-1]
            suspects.append(
                _suspect(last.node_id, last.event_type, "related_event_observed", 0.5)
            )

    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for suspect in suspects:
        key = (suspect["node_id"], suspect["event_type"], suspect["reason"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suspect)
    deduped.sort(key=lambda item: (-item["confidence"], item["node_id"], item["event_type"]))
    if not related:
        notes.append("failing outcomes carry no related event_type")
    if not deduped:
        notes.append("no attributable node found")
    return {
        "status": "ATTRIBUTED" if deduped else "NO_SUSPECT",
        "suspects": deduped,
        "missing": missing,
        "notes": notes,
    }


def render_markdown(graph: EventGraph) -> str:
    """Render the graph as human-readable Chinese markdown."""
    lines = [
        "# 事件因果图",
        "",
        f"- 节点数：`{len(graph.nodes)}`",
        f"- 边数：`{len(graph.edges)}`",
        "",
        "## 节点",
        "",
    ]
    if not graph.nodes:
        lines.append("- 无")
    for node in graph.nodes:
        delta = "无时间戳" if node.ts_ms is None else f"{node.ts_ms:.0f}ms"
        lines.append(
            f"- `{node.node_id}` `{node.event_type}` {node.label}（{node.source or '-'}，{delta}）"
        )
    lines.extend(["", "## 因果边", ""])
    if not graph.edges:
        lines.append("- 无")
    for edge in graph.edges:
        delta = "" if edge.delta_ms is None else f"，Δ={edge.delta_ms}ms"
        lines.append(f"- `{edge.src}` -> `{edge.dst}` `{edge.relation}`{delta}")
    lines.extend(
        [
            "",
            "## 根节点",
            "",
            "- " + (", ".join(f"`{item}`" for item in graph.roots) if graph.roots else "无"),
            "",
            "## 终止节点",
            "",
            "- "
            + (", ".join(f"`{item}`" for item in graph.terminal) if graph.terminal else "无"),
            "",
        ]
    )
    return "\n".join(lines)


def fingerprint(facts: Iterable[Any]) -> str:
    """Stable 16-hex failure fingerprint over the normalized fact sequence."""
    items = coerce_facts(facts)
    payload = "\n".join(
        f"{fact.event_type}|{_as_text(fact.source).strip()}|{_as_text(fact.state_class).strip()}"
        for fact in items
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
