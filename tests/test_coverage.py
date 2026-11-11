"""Tests for capability matrix and coverage policy (scripts/coverage.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lstest.scripts.assertions import Fact
from lstest.scripts.coverage import capability_matrix, evaluate_policy, state_coverage


def fact(event_type: str, role: str = "", state_class: str = "") -> Fact:
    return Fact(event_type=event_type, role=role, state_class=state_class)


PROFILE = {
    "schema_version": 2,
    "profile_id": "demo-device",
    "ports": [
        {
            "role": "ap",
            "port": "COM1",
            "capabilities": ["serial_log", "wake_marker"],
        },
        {
            "role": "upper",
            "port": "COM2",
            "capabilities": ["serial_log", "cloud_reply"],
        },
        {
            "role": "control",
            "port": "COM3",
            "capabilities": [],
        },
    ],
    "required_roles": ["ap", "upper", "asr"],
    "required_capabilities": ["serial_log", "media_oracle"],
}


class CapabilityMatrixTests(unittest.TestCase):
    def test_reads_port_capabilities_explicitly(self):
        matrix = capability_matrix(PROFILE)
        self.assertEqual(matrix["profile_id"], "demo-device")
        self.assertEqual(sorted(matrix["ports"]), ["ap", "control", "upper"])
        self.assertEqual(matrix["ports"]["ap"]["capabilities"], ["serial_log", "wake_marker"])
        self.assertTrue(matrix["capabilities"]["serial_log"]["supported"])
        self.assertEqual(matrix["capabilities"]["serial_log"]["roles"], ["ap", "upper"])
        self.assertEqual(matrix["capabilities"]["serial_log"]["source"], "ports[].capabilities")

    def test_reports_degraded_roles_and_capabilities(self):
        matrix = capability_matrix(PROFILE)
        self.assertIn("role:asr", matrix["degraded"])
        self.assertIn("role:control", matrix["degraded"])
        self.assertIn("capability:media_oracle", matrix["degraded"])
        self.assertEqual(matrix["degraded_roles"], ["asr", "control"])
        self.assertEqual(matrix["degraded_capabilities"], ["media_oracle"])
        self.assertFalse(matrix["capabilities"]["media_oracle"]["supported"])

    def test_loads_from_path_with_tempfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(PROFILE), encoding="utf-8")
            from_path = capability_matrix(path)
        from_mapping = capability_matrix(PROFILE)
        self.assertEqual(from_path["profile_id"], from_mapping["profile_id"])
        self.assertEqual(from_path["ports"], from_mapping["ports"])
        self.assertEqual(from_path["degraded"], from_mapping["degraded"])

    def test_accepts_objects_with_payload(self):
        device = SimpleNamespace(profile_id="demo-device", payload=PROFILE)
        matrix = capability_matrix(device)
        self.assertEqual(matrix["profile_id"], "demo-device")
        self.assertIn("ap", matrix["ports"])

    def test_no_project_name_inference(self):
        # Polaris inferred capabilities from a project name; lstest must not.
        matrix = capability_matrix({"profile_id": "venusws63", "ports": []})
        self.assertEqual(matrix["capabilities"], {})
        self.assertEqual(matrix["degraded"], [])
        self.assertNotIn("asr_log", matrix["capabilities"])
        self.assertNotIn("cp_log", matrix["capabilities"])

    def test_missing_capability_field_is_not_guessed(self):
        matrix = capability_matrix(
            {"profile_id": "p", "ports": [{"role": "ap", "port": "COM1"}]}
        )
        self.assertEqual(matrix["ports"]["ap"]["capabilities"], [])
        self.assertIn("role:ap", matrix["degraded"])

    def test_duplicate_role_capabilities_merge(self):
        matrix = capability_matrix(
            {
                "profile_id": "p",
                "ports": [
                    {"role": "upper", "capabilities": ["serial_log"]},
                    {"role": "upper", "capabilities": ["serial_log", "cloud_reply"]},
                ],
            }
        )
        self.assertEqual(matrix["ports"]["upper"]["capabilities"], ["serial_log", "cloud_reply"])


class StateCoverageTests(unittest.TestCase):
    def test_all_required_states_covered(self):
        result = state_coverage(
            [
                fact("PLAYER", state_class="preparation"),
                fact("PLAYER", state_class="active"),
                fact("PLAYER", state_class="terminal"),
            ]
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["covered"], ["preparation", "active", "terminal"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(
            result["counts"], {"active": 1, "preparation": 1, "terminal": 1}
        )

    def test_missing_state_degrades_coverage(self):
        result = state_coverage([fact("PLAYER", state_class="active")])
        self.assertEqual(result["status"], "COVERAGE_DEGRADED")
        self.assertEqual(result["missing"], ["preparation", "terminal"])
        self.assertEqual(result["covered"], ["active"])

    def test_no_facts_is_never_pass(self):
        result = state_coverage([fact("WAKE")])
        self.assertEqual(result["status"], "COVERAGE_DEGRADED")
        self.assertEqual(result["missing"], ["preparation", "active", "terminal"])
        self.assertEqual(result["counts"], {})

    def test_policy_required_states_override(self):
        result = state_coverage(
            [fact("PLAYER", state_class="active")],
            policy={"required_states": ["active"]},
        )
        self.assertEqual(result["status"], "PASS")

    def test_empty_requirement_skips(self):
        result = state_coverage([fact("PLAYER", state_class="active")], required=[])
        self.assertEqual(result["status"], "SKIP")


class EvaluatePolicyTests(unittest.TestCase):
    def policy(self, **overrides):
        payload = {
            "schema": "lstest.coverage_policy.v1",
            "roles": {"full": ["ap", "cp", "asr"], "minimum": ["ap"]},
            "required_states": ["preparation", "active", "terminal"],
            "missing_minimum": "BLOCKED",
            "missing_non_minimum": "COVERAGE_DEGRADED",
        }
        payload.update(overrides)
        return payload

    def test_no_policy_skips(self):
        for empty in (None, {}):
            with self.subTest(policy=empty):
                result = evaluate_policy([fact("WAKE", role="ap")], empty)
                self.assertEqual(result["status"], "SKIP")
                self.assertEqual(result["reason"], "no_policy")

    def test_missing_minimum_role_blocks(self):
        result = evaluate_policy(
            [fact("WAKE", role="upper"), fact("PLAYER", role="upper", state_class="preparation")],
            self.policy(),
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["missing_minimum"], ["ap"])

    def test_missing_non_minimum_role_degrades(self):
        facts = [
            fact("WAKE", role="ap"),
            fact("PLAYER", role="ap", state_class="preparation"),
            fact("PLAYER", role="ap", state_class="active"),
            fact("PLAYER", role="ap", state_class="terminal"),
        ]
        result = evaluate_policy(facts, self.policy())
        self.assertEqual(result["status"], "COVERAGE_DEGRADED")
        self.assertEqual(result["missing_non_minimum"], ["cp", "asr"])
        self.assertEqual(result["missing_minimum"], [])
        self.assertEqual(result["states"]["missing"], [])

    def test_full_coverage_passes(self):
        facts = [
            fact("WAKE", role="ap"),
            fact("REQUEST_ID", role="cp"),
            fact("ONLINE_ASR", role="asr"),
            fact("PLAYER", role="ap", state_class="preparation"),
            fact("PLAYER", role="ap", state_class="active"),
            fact("PLAYER", role="ap", state_class="terminal"),
        ]
        result = evaluate_policy(facts, self.policy())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["observed_roles"], ["ap", "asr", "cp"])
        self.assertEqual(result["role_counts"]["ap"], 4)
        self.assertEqual(result["states"]["covered"], ["preparation", "active", "terminal"])

    def test_missing_required_state_degrades_even_with_all_roles(self):
        facts = [
            fact("WAKE", role="ap"),
            fact("REQUEST_ID", role="cp"),
            fact("ONLINE_ASR", role="asr"),
        ]
        result = evaluate_policy(facts, self.policy())
        self.assertEqual(result["status"], "COVERAGE_DEGRADED")
        self.assertEqual(result["states"]["missing"], ["preparation", "active", "terminal"])

    def test_unsupported_schema_is_blocked(self):
        result = evaluate_policy([fact("WAKE", role="ap")], self.policy(schema="other.v9"))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "unsupported_schema")

    def test_policy_values_are_honoured_verbatim(self):
        result = evaluate_policy(
            [fact("WAKE", role="upper")],
            self.policy(missing_minimum="BLOCKED"),
        )
        self.assertEqual(result["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
