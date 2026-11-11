"""Tests for causal attribution (scripts/attribution.py)."""

from __future__ import annotations

import unittest

from lstest.scripts.attribution import (
    DEFAULT_CAUSAL_RULES,
    EventGraph,
    attribute,
    build_event_graph,
    fingerprint,
    render_markdown,
)
from lstest.scripts.assertions import STATUS_FAIL, STATUS_PASS, Fact, Outcome


def fact(event_type: str, ts_ms: float | None = None, **fields) -> Fact:
    return Fact(event_type=event_type, ts_ms=ts_ms, **fields)


def outcome(name: str, status: str, reason: str = "", **actual) -> Outcome:
    return Outcome(name=name, status=status, reason=reason, actual=actual)


class GraphBuildTests(unittest.TestCase):
    def test_default_rules_are_data_and_cover_required_relations(self):
        pairs = {(rule["from"], tuple(rule["to"])) for rule in DEFAULT_CAUSAL_RULES}
        self.assertIn(("WAKE", ("OFFLINE_ASR", "ONLINE_ASR")), pairs)
        self.assertIn(("REQUEST_ID", ("RESPONSE_ID",)), pairs)
        self.assertIn(("ONLINE_ASR", ("COMMAND_ACK", "COMMAND_EVIDENCE")), pairs)
        self.assertIn(("PLAY_URL", ("PLAYER",)), pairs)
        self.assertIn(("RESTART", ("*",)), pairs)
        self.assertIn(("DEVICE_EXCEPTION", ("*",)), pairs)

    def test_wake_to_asr_edge_when_order_and_window_hold(self):
        graph = build_event_graph([fact("WAKE", 1000.0), fact("ONLINE_ASR", 1500.0)])
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)
        edge = graph.edges[0]
        self.assertEqual((edge.src, edge.dst, edge.relation), ("n0", "n1", "trigger"))
        self.assertEqual(edge.delta_ms, 500)
        self.assertEqual(graph.roots, ("n0",))
        self.assertEqual(graph.terminal, ("n1",))

    def test_no_edge_when_order_is_reversed(self):
        graph = build_event_graph([fact("ONLINE_ASR", 900.0), fact("WAKE", 1000.0)])
        self.assertEqual(graph.edges, ())

    def test_no_edge_when_window_is_exceeded(self):
        graph = build_event_graph([fact("WAKE", 0.0), fact("ONLINE_ASR", 9000.0)])
        self.assertEqual(graph.edges, ())

    def test_no_edge_without_timestamps(self):
        graph = build_event_graph([fact("WAKE"), fact("ONLINE_ASR")])
        self.assertEqual(graph.edges, ())

    def test_project_rules_can_be_injected(self):
        graph = build_event_graph(
            [fact("PLAY_URL", 100.0), fact("PLAYER", 150.0)],
            rules=[{"from": "PLAY_URL", "to": ["PLAYER"], "relation": "playback_custom", "within_ms": 100}],
        )
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].relation, "playback_custom")

    def test_latest_source_is_chosen(self):
        graph = build_event_graph(
            [
                fact("WAKE", 1000.0),
                fact("WAKE", 1400.0),
                fact("ONLINE_ASR", 1500.0),
            ]
        )
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].src, "n1")
        self.assertEqual(graph.edges[0].delta_ms, 100)

    def test_schema_dict(self):
        graph = build_event_graph([fact("WAKE", 1000.0)])
        payload = graph.to_dict()
        self.assertEqual(payload["schema"], "lstest.event_graph.v1")
        self.assertEqual(payload["node_count"], 1)
        self.assertEqual(payload["edge_count"], 0)
        self.assertEqual(payload["roots"], ["n0"])
        self.assertEqual(payload["terminal"], ["n0"])


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_sensitive_to_order(self):
        first = [fact("WAKE", source="ap"), fact("ONLINE_ASR", source="asr")]
        second = [fact("WAKE", source="ap"), fact("ONLINE_ASR", source="asr")]
        reversed_facts = [fact("ONLINE_ASR", source="asr"), fact("WAKE", source="ap")]
        digest = fingerprint(first)
        self.assertEqual(digest, fingerprint(second))
        self.assertEqual(len(digest), 16)
        int(digest, 16)  # hex
        self.assertNotEqual(digest, fingerprint(reversed_facts))

    def test_fingerprint_ignores_presentation_values(self):
        a = [fact("WAKE", value="hello", source="ap")]
        b = [fact("WAKE", value="different text", source="ap")]
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_fingerprint_uses_state_class(self):
        a = [fact("PLAYER", state_class="active")]
        b = [fact("PLAYER", state_class="terminal")]
        self.assertNotEqual(fingerprint(a), fingerprint(b))


class AttributionTests(unittest.TestCase):
    def test_empty_graph_is_not_applicable(self):
        graph = build_event_graph([])
        result = attribute([outcome("wake", STATUS_FAIL, "missing_event", event_type="WAKE")], graph)
        self.assertEqual(result["status"], "NOT_APPLICABLE")
        self.assertEqual(result["suspects"], [])
        self.assertEqual(result["missing"], [])

    def test_all_pass_has_no_suspect(self):
        graph = build_event_graph([fact("WAKE", 1000.0), fact("ONLINE_ASR", 1500.0)])
        result = attribute([outcome("wake", STATUS_PASS), outcome("asr", STATUS_PASS)], graph)
        self.assertEqual(result["status"], "NO_SUSPECT")
        self.assertEqual(result["suspects"], [])

    def test_failure_attributes_missing_related_event_first(self):
        graph = build_event_graph([fact("WAKE", 1000.0)])
        failed = outcome(
            "player",
            STATUS_FAIL,
            "state_missing",
            event_type="PLAYER",
            state_class="active",
        )
        result = attribute([failed], graph)
        self.assertEqual(result["status"], "ATTRIBUTED")
        self.assertEqual(result["missing"], ["PLAYER"])
        top = result["suspects"][0]
        self.assertEqual(top["event_type"], "PLAYER")
        self.assertEqual(top["reason"], "missing_related_event")
        self.assertEqual(top["confidence"], 1.0)
        self.assertEqual(top["node_id"], "")

    def test_failure_attributes_observed_related_node(self):
        graph = build_event_graph([fact("WAKE", 1000.0), fact("PLAYER", 1200.0)])
        result = attribute(
            [outcome("player", STATUS_FAIL, "state_missing", event_type="PLAYER")], graph
        )
        self.assertEqual(result["status"], "ATTRIBUTED")
        self.assertEqual(result["missing"], [])
        reasons = {suspect["reason"] for suspect in result["suspects"]}
        self.assertIn("unattributed_related_event", reasons)

    def test_failure_antecedent_ranks_high(self):
        graph = build_event_graph([fact("DEVICE_EXCEPTION", 500.0), fact("PLAYER", 1000.0)])
        result = attribute(
            [outcome("player", STATUS_FAIL, "state_missing", event_type="PLAYER")],
            graph,
        )
        self.assertEqual(result["status"], "ATTRIBUTED")
        top = result["suspects"][0]
        self.assertEqual(top["event_type"], "DEVICE_EXCEPTION")
        self.assertEqual(top["reason"], "failure_antecedent")
        self.assertGreaterEqual(top["confidence"], 0.9)

    def test_sequence_break_is_attributed_from_actual(self):
        graph = build_event_graph([fact("WAKE", 1000.0)])
        failed = outcome(
            "sequence",
            STATUS_FAIL,
            "sequence_broken",
            steps=["WAKE", "ONLINE_ASR", "COMMAND_ACK"],
            missing_step="ONLINE_ASR",
        )
        result = attribute([failed], graph)
        self.assertEqual(result["missing"], ["ONLINE_ASR", "COMMAND_ACK"])
        self.assertEqual(result["suspects"][0]["confidence"], 1.0)

    def test_suspects_are_sorted_by_confidence(self):
        graph = build_event_graph(
            [fact("DEVICE_EXCEPTION", 500.0), fact("WAKE", 900.0), fact("PLAYER", 1000.0)]
        )
        result = attribute(
            [
                outcome("player", STATUS_FAIL, "state_missing", event_type="PLAYER"),
                outcome("ack", STATUS_FAIL, "missing_event", event_type="COMMAND_ACK"),
            ],
            graph,
        )
        confidences = [suspect["confidence"] for suspect in result["suspects"]]
        self.assertEqual(confidences, sorted(confidences, reverse=True))


class RenderTests(unittest.TestCase):
    def test_render_markdown_mentions_nodes_and_edges(self):
        graph = build_event_graph([fact("WAKE", 1000.0), fact("ONLINE_ASR", 1500.0)])
        text = render_markdown(graph)
        self.assertIn("事件因果图", text)
        self.assertIn("`n0`", text)
        self.assertIn("`trigger`", text)

    def test_render_markdown_handles_empty_graph(self):
        text = render_markdown(EventGraph(nodes=(), edges=(), roots=(), terminal=()))
        self.assertIn("节点数：`0`", text)
        self.assertIn("无", text)


if __name__ == "__main__":
    unittest.main()
