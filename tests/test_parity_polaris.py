"""Parity of the ported lstest engines against the real Polaris primitives.

Skipped (with the reason) when the Polaris checkout is not present: Polaris is
never a runtime dependency of lstest, only a parity oracle for this test.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from lstest.tools.parity import harness

FIXTURES = Path(__file__).resolve().parents[1] / "tools" / "parity" / "fixtures"


class ParityPolarisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.runtime = harness.resolve_polaris_runtime()
        except harness.PolarisUnavailable as error:
            raise unittest.SkipTest(f"Polaris checkout unavailable: {error}") from error
        cls.result = harness.run_parity(FIXTURES)
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)

    def test_every_check_agrees(self):
        counts = self.result["counts"]
        disagreements = [
            (item["scenario"], item["check"], item["lstest_status"], item["polaris_status"])
            for item in self.result["checks"]
            if not item["agreed"]
        ]
        self.assertEqual(disagreements, [], "lstest and Polaris disagree on these checks")
        self.assertEqual(counts["agreed"], counts["total"])
        self.assertEqual(counts["mismatched"], 0)

    def test_contract_coverage_and_volume(self):
        counts = self.result["counts"]
        self.assertEqual(counts["contract_kinds_missing"], [])
        self.assertEqual(list(counts["contract_kinds_covered"]), list(harness.CONTRACT_KINDS))
        self.assertGreaterEqual(counts["total"], 22, "all 11 kinds need PASS and FAIL evidence")
        self.assertGreaterEqual(counts["scenarios"], 2)

    def test_each_contract_kind_has_pass_and_fail_evidence(self):
        seen: dict[str, set[str]] = {}
        for item in self.result["checks"]:
            if item["kind"] in harness.CONTRACT_KINDS:
                seen.setdefault(item["kind"], set()).add(item["lstest_status"])
        for kind in harness.CONTRACT_KINDS:
            with self.subTest(kind=kind):
                self.assertLessEqual(
                    {"PASS", "FAIL"},
                    seen.get(kind, set()),
                    f"{kind} needs both a PASS and a FAIL fixture",
                )

    def test_real_polaris_modules_were_imported(self):
        modules = set(self.result["polaris"]["modules"])
        for name in ("assertion_engine", "assertion_dsl", "state_coverage_policy", "event_graph"):
            self.assertIn(name, modules)
        self.assertEqual(self.result["polaris"]["import_errors"], {})
        self.assertEqual(str(self.result["polaris"]["runtime"]), str(self.runtime))

    def test_detector_flags_the_injected_mismatch(self):
        controlled = harness.run_parity(FIXTURES, control=True)
        control_rows = [row for row in controlled["rows"] if row[0] == "control_expected_mismatch"]
        self.assertEqual(len(control_rows), 1)
        self.assertFalse(control_rows[0][4], "the injected control must be reported as a mismatch")
        self.assertEqual(controlled["counts"]["mismatched"], 1)
        # The shipped fixtures themselves stay consistent.
        self.assertEqual(
            controlled["counts"]["total"] - 1,
            self.result["counts"]["total"],
        )

    def test_cli_exit_codes(self):
        report = Path(self._tmp.name) / "parity.md"
        control_report = Path(self._tmp.name) / "parity-control.md"
        with contextlib.redirect_stdout(io.StringIO()):
            clean = harness.main(["--fixtures", str(FIXTURES), "--report", str(report)])
            controlled = harness.main(
                [
                    "--fixtures",
                    str(FIXTURES),
                    "--report",
                    str(control_report),
                    "--expect-mismatch",
                ]
            )
        self.assertEqual(clean, 0, "a clean run must exit 0")
        body = report.read_text(encoding="utf-8")
        self.assertIn("状态映射", body)
        self.assertIn(f"| 对照项总数 | {self.result['counts']['total']} |", body)
        self.assertNotIn("control_expected_mismatch", body)
        self.assertEqual(
            controlled, 1, "the expected-mismatch control must make the harness exit non-zero"
        )
        self.assertIn("control_expected_mismatch", control_report.read_text(encoding="utf-8"))

    def test_status_mapping_is_explicit(self):
        self.assertEqual(harness.map_polaris_status("PASS"), "PASS")
        self.assertEqual(harness.map_polaris_status("FAIL"), "FAIL")
        self.assertEqual(harness.map_polaris_status("BLOCKED"), "BLOCKED")
        self.assertEqual(harness.map_polaris_status("SKIP"), "SKIP")
        self.assertEqual(harness.map_polaris_status("TIMING_AMBIGUOUS"), "AMBIGUOUS")
        self.assertEqual(harness.map_polaris_status("WARN"), "AMBIGUOUS")
        self.assertEqual(harness.map_polaris_status("SOMETHING_NEW"), "AMBIGUOUS")
        self.assertNotEqual(harness.map_polaris_status("SOMETHING_NEW"), "PASS")


class ParityFixtureInvariantTests(unittest.TestCase):
    """Fixture validation must reject hand-waved Polaris expectations."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, payload: dict) -> Path:
        path = self.dir / "scenario.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_missing_polaris_expectation_is_rejected(self):
        self._write(
            {
                "name": "bad",
                "facts": [{"event_type": "WAKE", "ts_ms": 0}],
                "checks": [{"name": "wake", "kind": "event_exists", "event_type": "WAKE"}],
            }
        )
        with self.assertRaises(harness.ParityError):
            harness.load_scenarios(self.dir)

    def test_wrong_polaris_event_type_is_rejected(self):
        self._write(
            {
                "name": "bad",
                "facts": [{"event_type": "WAKE", "ts_ms": 0}],
                "checks": [
                    {
                        "name": "wake",
                        "kind": "event_exists",
                        "event_type": "WAKE",
                        "polaris": {"primitive": "assert_event_exists", "args": {"event_type": "RebootDetected"}},
                    }
                ],
            }
        )
        with self.assertRaises(harness.ParityError):
            harness.load_scenarios(self.dir)

    def test_non_chronological_facts_are_rejected(self):
        self._write(
            {
                "name": "bad",
                "facts": [
                    {"event_type": "WAKE", "ts_ms": 500},
                    {"event_type": "OFFLINE_ASR", "ts_ms": 100},
                ],
                "checks": [
                    {
                        "name": "wake",
                        "kind": "event_exists",
                        "event_type": "WAKE",
                        "polaris": {"primitive": "assert_event_exists", "args": {"event_type": "WakeDetected"}},
                    }
                ],
            }
        )
        with self.assertRaises(harness.ParityError):
            harness.load_scenarios(self.dir)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
