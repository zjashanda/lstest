from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from lstest.scripts.lstest import main

from lstest.scripts.capabilities import (
    CapabilityError,
    CapabilitySpec,
    load_capabilities,
    probe_capabilities,
)
from lstest.scripts.case_contract import (
    collect_case_contract_issues,
    normalize_case_contract,
)
from lstest.scripts.core import CaseResult, CaseSpec, TaskArtifacts
from lstest.scripts.result_validation import validate_run_dir
from lstest.scripts.verdict import (
    VerdictLayerResult,
    VerdictStatus,
    aggregate_verdict,
    validate_verdict_payload,
    verdict_from_status,
)


def _layer(layer_id: str, status: str, required: bool = True) -> VerdictLayerResult:
    return VerdictLayerResult(layer_id, VerdictStatus(status), required, "device", "")


def _case_contract() -> dict:
    return {
        "timeline": {},
        "lstest": {
            "requirement_ids": ["R-001"],
            "verdict_layers": [
                {"id": "functional", "required": True, "owner": "firmware"},
            ],
        },
    }


class CaseContractTests(unittest.TestCase):
    def test_legacy_timeline_only_payload_is_valid(self):
        payload = {"timeline": {"required_facts": ["WAKE_WORD"]}}
        self.assertEqual(
            collect_case_contract_issues(payload, known_fact_keys={"WAKE_WORD"}),
            [],
        )
        normalized = normalize_case_contract(payload, known_fact_keys={"WAKE_WORD"})
        self.assertEqual(normalized.timeline["required_facts"], ["WAKE_WORD"])
        self.assertEqual(normalized.verdict_layers[0].layer_id, "functional")

    def test_namespaced_contract_is_parsed(self):
        payload = {
            "timeline": {"required_facts": ["WAKE_WORD"]},
            "lstest": {
                "requirement_ids": ["R-001"],
                "depends_on": ["case-000"],
                "requires_capabilities": ["wake-audio"],
                "verdict_layers": [
                    {"id": "functional", "required": True, "owner": "firmware"},
                    {"id": "timing", "required": False, "owner": "device"},
                ],
                "timing": {
                    "id": "wake_latency",
                    "start_fact": "WAKE_WORD",
                    "end_fact": "PLAYER_START",
                    "tolerance_ms": 300,
                },
            },
        }
        normalized = normalize_case_contract(
            payload,
            case_id="case-001",
            known_fact_keys={"WAKE_WORD", "PLAYER_START"},
        )
        self.assertEqual(normalized.requirement_ids, ("R-001",))
        self.assertEqual(normalized.depends_on, ("case-000",))
        self.assertEqual(normalized.requires_capabilities, ("wake-audio",))
        self.assertEqual(normalized.verdict_layers[1].layer_id, "timing")
        self.assertEqual(normalized.timing.metric_id, "wake_latency")
        self.assertEqual(normalized.timing.tolerance_ms, 300)

    def test_invalid_nested_contract_is_rejected(self):
        payload = {
            "timeline": {"required_facts": ["MISSING"]},
            "lstest": {
                "unknown_field": True,
                "depends_on": ["case-001"],
                "requires_capabilities": ["missing-cap"],
                "verdict_layers": [
                    {"id": "functional"},
                    {"id": "functional"},
                ],
                "timing": {
                    "id": "bad",
                    "start_fact": "NOPE",
                    "end_fact": "NOPE",
                },
            },
        }
        issues = collect_case_contract_issues(
            payload,
            case_id="case-001",
            known_fact_keys={"WAKE_WORD"},
            capability_ids={"wake-audio"},
        )
        codes = {issue.code for issue in issues}
        self.assertIn("CASE_CONTRACT_UNKNOWN_FIELD", codes)
        self.assertIn("CASE_REQUIRED_FACT_UNKNOWN", codes)
        self.assertIn("CASE_DEPENDENCY_INVALID", codes)
        self.assertIn("CASE_CAPABILITY_UNKNOWN", codes)
        self.assertIn("CASE_VERDICT_LAYER_INVALID", codes)
        self.assertIn("CASE_TIMING_FACT_UNKNOWN", codes)
        self.assertIn("CASE_TIMING_INVALID", codes)


class VerdictTests(unittest.TestCase):
    def test_legacy_status_mapping(self):
        self.assertIs(verdict_from_status("ABORTED").status, VerdictStatus.ABORTED)
        self.assertIs(verdict_from_status("EXPECTED").status, VerdictStatus.PASS)

    def test_required_and_advisory_aggregation(self):
        pass_layer = _layer("functional", "PASS")
        self.assertIs(
            aggregate_verdict([pass_layer, _layer("timing", "PASS")]).status,
            VerdictStatus.PASS,
        )
        self.assertIs(
            aggregate_verdict([pass_layer, _layer("timing", "FAIL")]).status,
            VerdictStatus.FAIL,
        )
        self.assertIs(
            aggregate_verdict([pass_layer, _layer("timing", "AMBIGUOUS")]).status,
            VerdictStatus.AMBIGUOUS,
        )
        self.assertIs(
            aggregate_verdict([pass_layer, _layer("timing", "BLOCKED")]).status,
            VerdictStatus.BLOCKED,
        )
        self.assertIs(
            aggregate_verdict([pass_layer, _layer("timing", "FAIL", required=False)]).status,
            VerdictStatus.PARTIAL,
        )

    def test_verdict_payload_conflict_is_reported(self):
        issues = validate_verdict_payload({
            "status": "PASS",
            "layers": [{"id": "functional", "status": "FAIL", "required": True}],
        })
        self.assertTrue(any(issue.code == "VERDICT_STATUS_CONFLICT" for issue in issues))


class CapabilityTests(unittest.TestCase):
    def test_file_capability_and_required_block(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "firmware.bin"
            marker.write_bytes(b"fixture")
            config = Path(directory) / "capabilities.json"
            config.write_text(json.dumps({
                "schema_version": 1,
                "capabilities": [{
                    "id": "firmware",
                    "required": True,
                    "check": "file",
                    "params": {"path": str(marker)},
                }],
            }), encoding="utf-8")
            specs = load_capabilities(config)
            self.assertEqual(probe_capabilities(specs)["status"], "PASS")

        missing = CapabilitySpec("missing", True, "file", {"path": "Z:/not-present.bin"})
        self.assertEqual(probe_capabilities([missing])["status"], "BLOCKED")
        optional = CapabilitySpec("optional", False, "file", {"path": "Z:/not-present.bin"})
        self.assertEqual(probe_capabilities([optional])["status"], "WARN")

    def test_invalid_capability_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capabilities.json"
            path.write_text(json.dumps({"schema_version": 2, "capabilities": []}), encoding="utf-8")
            with self.assertRaises(CapabilityError):
                load_capabilities(path)


class ResultValidationTests(unittest.TestCase):
    def _valid_run(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        artifacts = TaskArtifacts(Path(temporary.name), "p0-valid")
        artifacts.freeze_cases([
            CaseSpec("case-001", "fixture", metadata={"case_contract": _case_contract()}),
        ])
        artifacts.record_case(CaseResult("case-001", "PASS", reason="fixture complete"))
        artifacts.finalize("PASS", "fixture complete")
        return temporary, artifacts.run_dir

    def test_valid_result_directory_passes(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            payload = validate_run_dir(run_dir)
            self.assertEqual(payload["status"], "PASS", payload)
            self.assertEqual(payload["cases"], 1)
            self.assertEqual(payload["results"], 1)

    def test_extra_artifact_blocks_validation(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            (run_dir / "notes.txt").write_text("extra", encoding="utf-8")
            payload = validate_run_dir(run_dir)
            self.assertEqual(payload["status"], "BLOCKED")
            self.assertTrue(any(item["code"] == "RESULT_ARTIFACT_EXTRA" for item in payload["issues"]))

    def test_missing_result_artifact_blocks_validation(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            (run_dir / "tool.log").unlink()
            payload = validate_run_dir(run_dir)
            self.assertEqual(payload["status"], "BLOCKED")
            self.assertTrue(any(item["code"] == "RESULT_ARTIFACT_MISSING" for item in payload["issues"]))

    def test_verdict_conflict_fails_validation(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            path = run_dir / "results.csv"
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
                fieldnames = list(rows[0])
            facts = json.loads(rows[0]["facts_json"])
            facts["_lstest"]["verdict"]["status"] = "FAIL"
            rows[0]["facts_json"] = json.dumps(facts, ensure_ascii=False)
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            payload = validate_run_dir(run_dir)
            self.assertEqual(payload["status"], "FAIL")
            self.assertTrue(any(item["code"] == "RESULT_VERDICT_CONFLICT" for item in payload["issues"]))

    def test_legacy_timeline_is_serialized_without_namespace_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "legacy-contract")
            artifacts.freeze_cases([
                CaseSpec("case-001", "fixture", metadata={"timeline": {"required_facts": []}}),
            ])
            with (artifacts.run_dir / "cases.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(json.loads(row["case_contract"]), {"required_facts": []})
            artifacts.finalize("PASS", "fixture complete")

    def test_cli_result_validate_dispatches(self):
        temporary, run_dir = self._valid_run()
        output = StringIO()
        with temporary, redirect_stdout(output):
            code = main(["result", "validate", str(run_dir), "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"status": "PASS"', output.getvalue())

    def test_cli_allow_blocked_changes_exit_code(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            (run_dir / "notes.txt").write_text("extra", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["result", "validate", str(run_dir), "--allow-blocked"])
            self.assertEqual(code, 0)
            self.assertIn("result: BLOCKED", output.getvalue())

    def test_namespaced_contract_and_verdict_are_persisted(self):
        temporary, run_dir = self._valid_run()
        with temporary:
            with (run_dir / "cases.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                case_row = next(csv.DictReader(handle))
            contract = json.loads(case_row["case_contract"])
            self.assertEqual(contract["lstest"]["requirement_ids"], ["R-001"])

            with (run_dir / "results.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                result_row = next(csv.DictReader(handle))
            facts = json.loads(result_row["facts_json"])
            self.assertEqual(facts["_lstest"]["schema_version"], 1)
            self.assertEqual(facts["_lstest"]["verdict"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
