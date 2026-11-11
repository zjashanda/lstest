"""Tests for the declarative assertion engine (scripts/assertions.py)."""

from __future__ import annotations

import unittest

from lstest.scripts import assertions
from lstest.scripts.assertions import (
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    Fact,
    Outcome,
    aggregate,
    coerce_fact,
    coerce_facts,
    evaluate,
    normalize_event_type,
)
from lstest.scripts.profile import ProfileFact
from lstest.scripts.timeline import FactOccurrence


def fact(event_type: str, ts_ms: float | None = None, **fields) -> Fact:
    return Fact(event_type=event_type, ts_ms=ts_ms, **fields)


def rule_outcome(rules, facts):
    result = evaluate(rules, facts)
    return result["status"], result["outcomes"][0]


class NormalizationTests(unittest.TestCase):
    def test_event_type_name_and_key_resolve_to_the_same_canonical_key(self):
        self.assertEqual(normalize_event_type("wake_result"), "WAKE")
        self.assertEqual(normalize_event_type("WAKE"), "WAKE")
        self.assertEqual(normalize_event_type("Wake_Result"), "WAKE")
        self.assertEqual(normalize_event_type("player_state"), "PLAYER")
        self.assertEqual(normalize_event_type("online_result"), "ONLINE_ASR")

    def test_unknown_event_type_is_upper_cased_not_dropped(self):
        self.assertEqual(normalize_event_type("vendor_marker"), "VENDOR_MARKER")
        self.assertEqual(normalize_event_type(""), "")

    def test_every_registry_event_type_maps_to_its_key(self):
        from lstest.scripts.profile import EVENT_REGISTRY

        for name, meta in EVENT_REGISTRY.items():
            with self.subTest(name=name):
                self.assertEqual(normalize_event_type(name), meta["key"])
                self.assertEqual(normalize_event_type(meta["key"]), meta["key"])


class CoercionTests(unittest.TestCase):
    def test_profile_fact_keeps_presentation_value_and_has_no_timestamp(self):
        source = ProfileFact(
            event_type="wake_result",
            key="wake_result",
            tag="DEVICE",
            presentation_value="hello wake",
            display_fields=(),
            captures={"keyword": "hello wake"},
            source="ap",
            port="COM1",
            role="ap",
            phase="wake",
            sequence=3,
            evidence=("line:1",),
        )
        coerced = coerce_fact(source)
        self.assertEqual(coerced.event_type, "WAKE")
        self.assertEqual(coerced.value, "hello wake")
        self.assertEqual(coerced.captures["keyword"], "hello wake")
        self.assertEqual(coerced.source, "ap")
        self.assertEqual(coerced.role, "ap")
        self.assertEqual(coerced.sequence, 3)
        self.assertIsNone(coerced.ts_ms)
        self.assertEqual(coerced.evidence, ("line:1",))

    def test_fact_occurrence_converts_monotonic_seconds_to_ms(self):
        occurrence = FactOccurrence(
            key="online_result",
            value="ok",
            tag="ONLINE",
            timestamp="2026-01-01T00:00:00.000",
            monotonic_seconds=1.25,
            case_id="case-1",
            epoch=0,
            role="asr",
            evidence=("evt",),
        )
        coerced = coerce_fact(occurrence)
        self.assertEqual(coerced.event_type, "ONLINE_ASR")
        self.assertEqual(coerced.ts_ms, 1250.0)
        self.assertEqual(coerced.case_id, "case-1")
        self.assertEqual(coerced.role, "asr")

    def test_mapping_input_is_normalized(self):
        coerced = coerce_fact(
            {
                "event_type": "player_state",
                "value": "playing",
                "ts_ms": "12.5",
                "state_class": "active",
                "captures": {"state": "playing"},
                "evidence": ["a", "b"],
            }
        )
        self.assertEqual(coerced.event_type, "PLAYER")
        self.assertEqual(coerced.ts_ms, 12.5)
        self.assertEqual(coerced.captures, {"state": "playing"})
        self.assertEqual(coerced.evidence, ("a", "b"))

    def test_coerce_facts_accepts_mixed_sources(self):
        items = coerce_facts([fact("WAKE"), {"event_type": "wake_result"}])
        self.assertEqual([item.event_type for item in items], ["WAKE", "WAKE"])

    def test_unsupported_input_raises(self):
        with self.assertRaises(TypeError):
            coerce_fact(object())


class RuleKindPositiveTests(unittest.TestCase):
    """Every one of the 11 rule kinds must be able to produce PASS."""

    def test_event_exists(self):
        status, outcome = rule_outcome(
            [{"kind": "event_exists", "event_type": "wake_result", "min_count": 2}],
            [fact("WAKE"), fact("wake_result")],
        )
        self.assertEqual((status, outcome.status, outcome.reason), (STATUS_PASS, STATUS_PASS, ""))
        self.assertEqual(outcome.actual["count"], 2)

    def test_event_absent(self):
        status, outcome = rule_outcome(
            [{"kind": "event_absent", "event_type": "RESTART"}], [fact("WAKE")]
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))

    def test_event_order(self):
        status, outcome = rule_outcome(
            [{"kind": "event_order", "before": "wake_result", "after": "online_result"}],
            [fact("WAKE", 100.0), fact("ONLINE_ASR", 900.0)],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.actual["ordered_by"], "ts_ms")

    def test_event_within_ms(self):
        status, outcome = rule_outcome(
            [{"kind": "event_within_ms", "from": "WAKE", "to": "ONLINE_ASR", "max_ms": 1000}],
            [fact("WAKE", 1000.0), fact("ONLINE_ASR", 1300.0)],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.actual["delta_ms"], 300.0)

    def test_event_not_during(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "event_not_during",
                    "event_type": "COMMAND_ACK",
                    "window_from": "REQUEST_ID",
                    "window_to": "RESPONSE_ID",
                }
            ],
            [
                fact("REQUEST_ID", 1000.0),
                fact("RESPONSE_ID", 3000.0),
                fact("COMMAND_ACK", 5000.0),
            ],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))

    def test_sequence(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "sequence",
                    "steps": ["wake_result", "online_result", "command_ack"],
                    "max_span_ms": 5000,
                }
            ],
            [
                fact("WAKE", 100.0),
                fact("REQUEST_ID", 200.0),
                fact("ONLINE_ASR", 300.0),
                fact("COMMAND_ACK", 400.0),
            ],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.actual["missing_step"], "")

    def test_duration(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "duration",
                    "start_event": "PLAY_URL",
                    "end_event": "PLAYER",
                    "max_ms": 2000,
                }
            ],
            [fact("PLAY_URL", 1000.0), fact("PLAYER", 2500.0)],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.actual["delta_ms"], 1500.0)

    def test_response(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "response",
                    "request": "REQUEST_ID",
                    "response": "RESPONSE_ID",
                    "max_ms": 1000,
                    "match_capture": "request_id",
                }
            ],
            [
                fact("REQUEST_ID", 1000.0, captures={"request_id": "7"}),
                fact("RESPONSE_ID", 1200.0, captures={"request_id": "7"}),
            ],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.actual["correlation"], "7")

    def test_count_range(self):
        status, outcome = rule_outcome(
            [{"kind": "count_range", "event_type": "WAKE", "min": 1, "max": 2}],
            [fact("WAKE"), fact("WAKE")],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))

    def test_state_required(self):
        status, outcome = rule_outcome(
            [{"kind": "state_required", "state_class": "active"}],
            [
                fact("PLAYER", state_class="preparation"),
                fact("PLAYER", state_class="active"),
            ],
        )
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))

    def test_no_reboot(self):
        status, outcome = rule_outcome([{"kind": "no_reboot"}], [fact("WAKE")])
        self.assertEqual((status, outcome.status), (STATUS_PASS, STATUS_PASS))
        self.assertEqual(outcome.name, "no_runtime_reboot")


class RuleKindNegativeTests(unittest.TestCase):
    """Each negative case proves the engine can really FAIL."""

    def test_event_exists_missing_event(self):
        status, outcome = rule_outcome(
            [{"kind": "event_exists", "event_type": "WAKE"}], [fact("PLAYER")]
        )
        self.assertEqual((status, outcome.status, outcome.reason), (STATUS_FAIL, STATUS_FAIL, "missing_event"))

    def test_event_exists_honours_source_filter(self):
        status, outcome = rule_outcome(
            [{"kind": "event_exists", "event_type": "WAKE", "source": "upper"}],
            [fact("WAKE", source="ap")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "missing_event"))

    def test_event_absent_unexpected_event(self):
        status, outcome = rule_outcome(
            [{"kind": "event_absent", "event_type": "RESTART"}], [fact("RESTART")]
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "unexpected_event"))

    def test_event_order_out_of_order(self):
        status, outcome = rule_outcome(
            [{"kind": "event_order", "before": "WAKE", "after": "ONLINE_ASR"}],
            [fact("ONLINE_ASR", 900.0), fact("WAKE", 1000.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "out_of_order"))

    def test_event_order_missing_side(self):
        status, outcome = rule_outcome(
            [{"kind": "event_order", "before": "WAKE", "after": "ONLINE_ASR"}],
            [fact("WAKE", 1000.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "missing_event"))
        self.assertEqual(outcome.actual["missing"], ["ONLINE_ASR"])

    def test_event_within_ms_too_slow(self):
        status, outcome = rule_outcome(
            [{"kind": "event_within_ms", "from": "WAKE", "to": "ONLINE_ASR", "max_ms": 500}],
            [fact("WAKE", 1000.0), fact("ONLINE_ASR", 1800.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "too_slow"))
        self.assertEqual(outcome.actual["delta_ms"], 800.0)

    def test_event_within_ms_too_fast(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "event_within_ms",
                    "from": "WAKE",
                    "to": "ONLINE_ASR",
                    "max_ms": 5000,
                    "min_ms": 100,
                }
            ],
            [fact("WAKE", 1000.0), fact("ONLINE_ASR", 1010.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "too_fast"))

    def test_event_within_ms_without_timestamp_skips(self):
        status, outcome = rule_outcome(
            [{"kind": "event_within_ms", "from": "WAKE", "to": "ONLINE_ASR", "max_ms": 500}],
            [fact("WAKE"), fact("ONLINE_ASR")],
        )
        self.assertEqual((status, outcome.status, outcome.reason), (STATUS_SKIP, STATUS_SKIP, "no_timestamp"))

    def test_event_not_during_forbidden_event(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "event_not_during",
                    "event_type": "COMMAND_ACK",
                    "window_from": "REQUEST_ID",
                    "window_to": "RESPONSE_ID",
                }
            ],
            [
                fact("REQUEST_ID", 1000.0),
                fact("COMMAND_ACK", 2000.0),
                fact("RESPONSE_ID", 3000.0),
            ],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "forbidden_event"))
        self.assertEqual(outcome.actual["count"], 1)

    def test_event_not_during_without_window_skips(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "event_not_during",
                    "event_type": "COMMAND_ACK",
                    "window_from": "REQUEST_ID",
                    "window_to": "RESPONSE_ID",
                }
            ],
            [fact("COMMAND_ACK", 2000.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_SKIP, "window_missing"))

    def test_sequence_broken_reports_missing_step(self):
        status, outcome = rule_outcome(
            [{"kind": "sequence", "steps": ["WAKE", "ONLINE_ASR", "COMMAND_ACK"]}],
            [fact("WAKE", 100.0), fact("ONLINE_ASR", 200.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "sequence_broken"))
        self.assertEqual(outcome.actual["missing_step"], "COMMAND_ACK")

    def test_sequence_span_without_timestamp_skips(self):
        status, outcome = rule_outcome(
            [{"kind": "sequence", "steps": ["WAKE", "ONLINE_ASR"], "max_span_ms": 1000}],
            [fact("WAKE"), fact("ONLINE_ASR")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_SKIP, "no_timestamp"))

    def test_sequence_span_over_limit_fails(self):
        status, outcome = rule_outcome(
            [{"kind": "sequence", "steps": ["WAKE", "ONLINE_ASR"], "max_span_ms": 1000}],
            [fact("WAKE", 100.0), fact("ONLINE_ASR", 5000.0)],
        )
        self.assertEqual(outcome.status, STATUS_FAIL)
        self.assertEqual(outcome.reason, "sequence_too_long")

    def test_duration_too_long(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "duration",
                    "start_event": "PLAY_URL",
                    "end_event": "PLAYER",
                    "max_ms": 1000,
                }
            ],
            [fact("PLAY_URL", 1000.0), fact("PLAYER", 3000.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "too_long"))

    def test_duration_without_timestamp_skips(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "duration",
                    "start_event": "PLAY_URL",
                    "end_event": "PLAYER",
                    "max_ms": 1000,
                }
            ],
            [fact("PLAY_URL"), fact("PLAYER")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_SKIP, "no_timestamp"))

    def test_response_capture_mismatch(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "response",
                    "request": "REQUEST_ID",
                    "response": "RESPONSE_ID",
                    "match_capture": "request_id",
                }
            ],
            [
                fact("REQUEST_ID", 1000.0, captures={"request_id": "7"}),
                fact("RESPONSE_ID", 1200.0, captures={"request_id": "8"}),
            ],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "correlation_mismatch"))
        self.assertEqual(outcome.actual["request_values"], ["7"])
        self.assertEqual(outcome.actual["response_values"], ["8"])

    def test_response_without_capture_evidence_is_a_mismatch_not_a_pass(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "response",
                    "request": "REQUEST_ID",
                    "response": "RESPONSE_ID",
                    "match_capture": "request_id",
                }
            ],
            [fact("REQUEST_ID"), fact("RESPONSE_ID")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "correlation_mismatch"))

    def test_response_too_slow(self):
        status, outcome = rule_outcome(
            [
                {
                    "kind": "response",
                    "request": "REQUEST_ID",
                    "response": "RESPONSE_ID",
                    "max_ms": 100,
                }
            ],
            [fact("REQUEST_ID", 1000.0), fact("RESPONSE_ID", 2000.0)],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "too_slow"))

    def test_count_range_out_of_range(self):
        status, outcome = rule_outcome(
            [{"kind": "count_range", "event_type": "WAKE", "min": 1, "max": 1}],
            [fact("WAKE"), fact("WAKE")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "count_out_of_range"))
        self.assertEqual(outcome.actual["count"], 2)

    def test_state_required_missing(self):
        status, outcome = rule_outcome(
            [{"kind": "state_required", "state_class": "terminal"}],
            [fact("PLAYER", state_class="active")],
        )
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "state_missing"))
        self.assertEqual(outcome.actual["observed"], ["active"])

    def test_no_reboot_fails_when_restart_observed(self):
        status, outcome = rule_outcome([{"kind": "no_reboot"}], [fact("RESTART", 10.0)])
        self.assertEqual((outcome.status, outcome.reason), (STATUS_FAIL, "unexpected_event"))


class AggregationTests(unittest.TestCase):
    def test_aggregate_precedence(self):
        fail = Outcome("f", STATUS_FAIL)
        blocked = Outcome("b", STATUS_BLOCKED)
        ambiguous = Outcome("a", "AMBIGUOUS")
        skip = Outcome("s", STATUS_SKIP)
        passed = Outcome("p", STATUS_PASS)
        self.assertEqual(aggregate([]), STATUS_SKIP)
        self.assertEqual(aggregate([passed]), STATUS_PASS)
        self.assertEqual(aggregate([passed, skip]), STATUS_SKIP)
        self.assertEqual(aggregate([skip, ambiguous, passed]), "AMBIGUOUS")
        self.assertEqual(aggregate([ambiguous, blocked, passed]), STATUS_BLOCKED)
        self.assertEqual(aggregate([blocked, fail, skip]), STATUS_FAIL)

    def test_fail_dominates_skip_in_evaluate(self):
        result = evaluate(
            [
                {"kind": "event_exists", "event_type": "WAKE"},
                {"kind": "event_exists", "event_type": "COMMAND_ACK"},
                {"kind": "event_within_ms", "from": "WAKE", "to": "ONLINE_ASR", "max_ms": 10},
            ],
            [fact("WAKE"), fact("ONLINE_ASR")],
        )
        self.assertEqual(result["status"], STATUS_FAIL)
        self.assertEqual(
            result["counts"],
            {"PASS": 1, "FAIL": 1, "SKIP": 1, "BLOCKED": 0, "AMBIGUOUS": 0},
        )

    def test_empty_rule_list_is_skip(self):
        result = evaluate([], [fact("WAKE")])
        self.assertEqual(result["status"], STATUS_SKIP)
        self.assertEqual(result["outcomes"], [])
        self.assertEqual(
            result["counts"],
            {"PASS": 0, "FAIL": 0, "SKIP": 0, "BLOCKED": 0, "AMBIGUOUS": 0},
        )

    def test_every_rule_produces_one_outcome(self):
        rules = [
            {"kind": "event_exists", "event_type": "WAKE"},
            {"kind": "totally_unknown"},
            "not-a-mapping",
            {"kind": "no_reboot"},
        ]
        result = evaluate(rules, [fact("WAKE")])
        self.assertEqual(len(result["outcomes"]), len(rules))
        self.assertEqual(result["outcomes"][1].status, STATUS_SKIP)
        self.assertEqual(result["outcomes"][1].reason, "unknown_rule_kind")
        self.assertEqual(result["outcomes"][2].status, STATUS_SKIP)
        self.assertEqual(result["outcomes"][2].reason, "invalid_rule")

    def test_missing_required_rule_field_is_blocked(self):
        status, outcome = rule_outcome([{"kind": "event_exists"}], [fact("WAKE")])
        self.assertEqual((outcome.status, outcome.reason), (STATUS_BLOCKED, "invalid_rule"))

    def test_outcome_to_dict_is_jsonable(self):
        outcome = Outcome("n", STATUS_FAIL, "missing_event", {"missing": ("WAKE",)})
        payload = outcome.to_dict()
        self.assertEqual(payload["actual"]["missing"], ["WAKE"])
        self.assertEqual(set(payload), {"name", "status", "reason", "actual"})

    def test_module_exposes_contract_constants(self):
        self.assertEqual(assertions.STATUS_PASS, "PASS")
        self.assertEqual(assertions.STATUS_AMBIGUOUS, "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
