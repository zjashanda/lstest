"""设备状态目录（lstest.state-catalog.v1）回归测试。

覆盖：目录自洽、项目扩展校验（P_ 前缀/别名/命名空间/gate）、
未登记码降级 DEVICE_UNPARSED、别名保留 raw_code、重复/迟到/恢复分析、
状态 gate 与最终判定、verify 的 STATE_* 检查、CLI 与覆盖率。
全部测试不访问设备、网络或真实结果目录。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import eventlog, result_layout, profile, states

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE = FIXTURES / "state_catalog_smoke.txt"
SMOKE_MARKERS = FIXTURES / "state_catalog_smoke.markers.json"
SMOKE_EXTENSION = FIXTURES / "state_catalog_smoke.extension.json"
SMOKE_META = FIXTURES / "state_catalog_smoke.meta.json"


def load_extension() -> states.StateExtension:
    value = states.validate_extension(SMOKE_EXTENSION)
    assert value is not None
    return value


def device(
    code: str,
    moment: str,
    *,
    state: str = "",
    device_ts: str | None = None,
    round_value: int | None = 1,
    parameters: dict | None = None,
) -> eventlog.EventRecord:
    details = {"device": True, "audit_kind": "STATE"}
    if state:
        details["state"] = state
    if device_ts:
        details["device_ts"] = device_ts
    if parameters:
        details["parameters"] = parameters
    return eventlog.EventRecord(
        event=code, time=moment, round=round_value, source="device",
        details=details, message=code,
    )


class CatalogIntegrityTests(unittest.TestCase):
    def test_every_common_state_is_well_formed(self) -> None:
        self.assertGreaterEqual(len(states.CATALOG), 100)
        for code, state in states.CATALOG.items():
            with self.subTest(code=code):
                self.assertRegex(code, states.COMMON_CODE_RE)
                self.assertIn(state.category, states.CATEGORIES)
                self.assertIn(state.kind, states.KINDS)
                self.assertIn(state.pass_role, states.PASS_ROLES)
                self.assertIn(state.duplicate_policy, states.DUPLICATE_POLICIES)
                self.assertTrue(state.label_zh)
        self.assertEqual(states.CATALOG["DEVICE_UNPARSED"].kind, "framework")

    def test_prerequisites_and_successors_reference_known_states(self) -> None:
        for code, state in states.CATALOG.items():
            for ref in (*state.prerequisites, *state.successors):
                with self.subTest(code=code, ref=ref):
                    self.assertIn(ref, states.CATALOG)

    def test_schema_document_describes_extension(self) -> None:
        document = states.schema_document()
        self.assertEqual(document["$schema"], states.CATALOG_SCHEMA)
        self.assertIn("P_", document["extension"]["project_code_pattern"])
        self.assertIn("required_states", document["extension"]["gate"])


class ExtensionValidationTests(unittest.TestCase):
    def base(self) -> dict:
        return {
            "schema": states.EXTENSION_SCHEMA,
            "profile_id": "demo-profile",
            "states": [],
            "aliases": [],
            "gate": {},
        }

    def test_valid_extension_loads(self) -> None:
        extension = load_extension()
        self.assertEqual(extension.profile_id, "state-catalog-demo")
        self.assertIn("P_TCL_SETLOGLEV_OK", extension.states)
        self.assertEqual(extension.aliases["WAKE_HIT"], "WAKEUP")
        self.assertEqual(extension.gate["required_states"][0], "READY")

    def test_project_code_must_be_prefixed(self) -> None:
        payload = self.base()
        payload["states"] = [{"code": "MY_STATE", "category": "mode", "kind": "device",
                              "label_zh": "x"}]
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)

    def test_common_state_cannot_be_overridden(self) -> None:
        payload = self.base()
        payload["states"] = [{"code": "P_POWER_OFF", "category": "power", "kind": "device",
                              "label_zh": "x"}]
        payload["aliases"] = [{"raw_code": "POWER_ON", "maps_to": "WAKEUP"}]
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)

    def test_alias_target_must_exist(self) -> None:
        payload = self.base()
        payload["aliases"] = [{"raw_code": "NOPE", "maps_to": "P_MISSING"}]
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)

    def test_gate_requires_known_states(self) -> None:
        payload = self.base()
        payload["gate"] = {"required_states": ["NOT_A_STATE"]}
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)

    def test_extension_references_must_be_registered(self) -> None:
        payload = self.base()
        payload["states"] = [{"code": "P_X", "category": "mode", "kind": "device",
                              "label_zh": "x", "prerequisites": ["P_TYPO"]}]
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)

    def test_bad_profile_id_is_rejected(self) -> None:
        payload = self.base()
        payload["profile_id"] = "Bad Profile"
        with self.assertRaises(states.StateCatalogError):
            states.validate_extension(payload)


class ResolveTests(unittest.TestCase):
    def test_common_project_and_unknown_codes(self) -> None:
        extension = load_extension()
        code, definition, status = states.resolve_device_code("WAKEUP", extension)
        self.assertEqual((code, status), ("WAKEUP", "common"))
        code, definition, status = states.resolve_device_code("P_TCL_SETLOGLEV_OK", extension)
        self.assertEqual((code, status), ("P_TCL_SETLOGLEV_OK", "project"))
        code, definition, status = states.resolve_device_code("BOGUS_STATE", extension)
        self.assertEqual(code, states.UNPARSED_CODE)
        self.assertEqual(status, "unregistered")
        self.assertEqual(definition.code, states.UNPARSED_CODE)

    def test_alias_maps_to_common_and_keeps_raw_code_at_call_site(self) -> None:
        extension = load_extension()
        code, _definition, status = states.resolve_device_code("WAKE_HIT", extension)
        self.assertEqual((code, status), ("WAKEUP", "common"))

    def test_judgement_codes_cannot_be_device_states(self) -> None:
        for code in ("PASS", "FAIL", "NEEDS_REVIEW", "POWER_PASS"):
            with self.subTest(code=code):
                resolved, _definition, status = states.resolve_device_code(code)
                self.assertEqual(resolved, states.UNPARSED_CODE)
                self.assertEqual(status, "unregistered")


class DeriveAndWriteTests(unittest.TestCase):
    def test_golden_build_registers_alias_project_and_unparsed(self) -> None:
        extension = load_extension()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            summary = eventlog.import_legacy_log(
                SMOKE, out, meta=json.loads(SMOKE_META.read_text(encoding="utf-8")),
                markers=SMOKE_MARKERS, extension=extension,
            )
            records = eventlog.read_events(result_layout.resolve_required(out, "events"))
            by_code = {}
            for record in records:
                by_code.setdefault(record.event, []).append(record)
            self.assertIn("WAKEUP", by_code)
            wake = by_code["WAKEUP"][0]
            self.assertEqual(wake.details["raw_code"], "WAKE_HIT")
            self.assertEqual(wake.details["catalog_status"], "common")
            self.assertIn("P_TCL_SETLOGLEV_OK", by_code)
            self.assertIn("DEVICE_UNPARSED", by_code)
            unparsed = by_code["DEVICE_UNPARSED"][0]
            self.assertEqual(unparsed.details["raw_code"], "BOGUS_STATE")
            self.assertEqual(unparsed.level, "WARN")
            self.assertTrue(unparsed.details["raw_sha256"])
            self.assertEqual(summary["state_report"]["catalog_status"]["unregistered"], 1)
            self.assertEqual(summary["state_report"]["catalog_status"]["project"], 1)
            self.assertFalse(summary["gate"]["gate_pass"])
            self.assertIn("no_unrecovered_blocking", summary["gate"]["failed_checks"])
            self.assertNotEqual(summary["final_status"], "PASS")
            lines = result_layout.resolve_required(out, "tool_log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), len(records))
            self.assertIn("[DEVICE] 未识别设备状态：", "\n".join(lines))
            for forbidden in ("raw_marker=", "catalog_status=", "raw_code=", "rule="):
                self.assertNotIn(forbidden, "\n".join(lines))

    def test_write_device_event_registered_and_unregistered(self) -> None:
        extension = load_extension()
        with tempfile.TemporaryDirectory() as tmp:
            writer = eventlog.EventLogWriter(Path(tmp), "t1")
            try:
                payload = eventlog.write_device_event(
                    writer, "start player", code="PLAYER_START", state="IDLE→PLAYING",
                    host_time="2026-11-12 10:00:01.000", extension=extension,
                )
                payload2 = eventlog.write_device_event(
                    writer, "vendor magic", code="NOT_REGISTERED",
                    host_time="2026-11-12 10:00:02.000", extension=extension,
                )
            finally:
                writer.close()
            self.assertEqual(payload["event"], "PLAYER_START")
            self.assertEqual(payload2["event"], "DEVICE_UNPARSED")
            self.assertEqual(payload2["level"], "WARN")
            self.assertEqual(payload2["details"]["raw_code"], "NOT_REGISTERED")


class AnalyzeTests(unittest.TestCase):
    def test_duplicate_collapse_late_and_recovery(self) -> None:
        records = [
            device("READY", "2026-11-12 10:00:00.000", state="BOOTING→READY",
                   device_ts="2026-11-12 10:00:00.000"),
            device("PLAYER_START", "2026-11-12 10:00:01.000", state="IDLE→PLAYING",
                   device_ts="2026-11-12 10:00:01.000"),
            device("PLAYER_START", "2026-11-12 10:00:01.500", state="IDLE→PLAYING",
                   device_ts="2026-11-12 10:00:01.500"),
            device("ERROR", "2026-11-12 10:00:02.000", state="PLAYING→FAULT",
                   device_ts="2026-11-12 10:00:00.500"),
            device("RECOVERED", "2026-11-12 10:00:03.000", state="FAULT→READY",
                   device_ts="2026-11-12 10:00:03.000"),
        ]
        report = states.analyze(records)
        self.assertEqual(report["duplicates"]["collapsed_events"], 1)
        self.assertEqual(len(report["late"]), 1)
        self.assertEqual(report["late"][0]["code"], "ERROR")
        self.assertEqual(report["blocking"]["count"], 1)
        self.assertEqual(report["blocking"]["recovered"], 1)
        self.assertEqual(report["blocking"]["unrecovered"], [])

    def test_unrecovered_blocking_sets_gate_false(self) -> None:
        extension = load_extension()
        records = [
            device("READY", "2026-11-12 10:00:00.000", state="BOOTING→READY"),
            device("ERROR", "2026-11-12 10:00:05.000", state="READY→FAULT"),
        ]
        report = states.analyze(records, extension)
        self.assertEqual(len(report["blocking"]["unrecovered"]), 1)
        self.assertFalse(report["gate_checks"]["no_unrecovered_blocking"])

    def test_required_states_gate(self) -> None:
        extension = load_extension()
        records = [
            device("READY", "2026-11-12 10:00:00.000", state="BOOTING→READY"),
            device("WAKEUP", "2026-11-12 10:00:01.000", state="IDLE→WAKEUP"),
        ]
        report = states.analyze(records, extension)
        self.assertFalse(report["gate_checks"]["required_states_present"])
        self.assertEqual(
            set(report["required_states"]["missing"]),
            {"PLAYER_COMPLETE", "P_TCL_SETLOGLEV_OK"},
        )

    def test_correlation_groups_are_reported(self) -> None:
        first = device("READY", "2026-11-12 10:00:00.000", state="BOOTING→READY")
        second = device("WAKEUP", "2026-11-12 10:00:01.000", state="IDLE→WAKEUP")
        object.__setattr__(first, "correlation_id", "R1:case-a")
        object.__setattr__(second, "correlation_id", "R1:case-a")
        report = states.analyze([first, second])
        self.assertEqual(report["correlation"]["with_id"], 2)
        self.assertEqual(report["correlation"]["without_id"], 0)
        self.assertEqual(report["correlation"]["groups"]["R1:case-a"], ["READY", "WAKEUP"])

    def test_prerequisite_missing_only_reported_when_strict(self) -> None:
        extension = load_extension()
        records = [device("PLAYER_START", "2026-11-12 10:00:01.000", state="IDLE→PLAYING")]
        report = states.analyze(records, extension)
        self.assertFalse(report["prerequisites_strict"])
        self.assertTrue(report["prerequisite_missing"])
        strict = states.validate_extension({
            "schema": states.EXTENSION_SCHEMA,
            "profile_id": "state-catalog-demo",
            "gate": {"required_states": ["PLAYER_START"], "strict_prerequisites": True},
        })
        report = states.analyze(records, strict)
        self.assertTrue(report["prerequisites_strict"])


class VerifyAndSummaryTests(unittest.TestCase):
    def test_verify_reports_state_findings_and_strict_promotes(self) -> None:
        extension = load_extension()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            eventlog.import_legacy_log(
                SMOKE, out, meta=json.loads(SMOKE_META.read_text(encoding="utf-8")),
                markers=SMOKE_MARKERS, extension=extension,
            )
            findings = eventlog.verify_bundle(out, extension=extension)
            codes = {item["code"]: item["level"] for item in findings
                     if item["code"].startswith("STATE_")}
            self.assertEqual(codes.get("STATE_UNREGISTERED"), "WARN")
            self.assertIn("STATE_UNRECOVERED", codes)
            strict = eventlog.verify_bundle(out, extension=extension, strict_states=True)
            strict_codes = {item["code"]: item["level"] for item in strict
                            if item["code"].startswith("STATE_")}
            self.assertEqual(strict_codes.get("STATE_UNREGISTERED"), "ERROR")
            self.assertFalse([item for item in findings if item["level"] == "ERROR"])

    def test_summary_state_gate_blocks_pass_without_touching_outcome(self) -> None:
        extension = load_extension()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            summary = eventlog.import_legacy_log(
                SMOKE, out, meta=json.loads(SMOKE_META.read_text(encoding="utf-8")),
                markers=SMOKE_MARKERS, extension=extension,
            )
            summary_md = (out / "summary.md").read_text(encoding="utf-8")
        self.assertEqual(summary["test_outcome"], "INCOMPLETE")
        self.assertEqual(summary["delivery_status"], "BLOCKED")
        self.assertEqual(summary["final_status"], "BLOCKED")
        self.assertIn("设备状态目录", summary_md)

    def test_without_extension_keeps_old_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            summary = eventlog.import_legacy_log(
                SMOKE, out, meta=json.loads(SMOKE_META.read_text(encoding="utf-8")),
                markers=None,
            )
            self.assertEqual(summary["state_report"]["gate_checks"], {})
            findings = eventlog.verify_bundle(out)
            self.assertFalse([item for item in findings if item["level"] == "ERROR"])


class ProfileIntegrationTests(unittest.TestCase):
    def test_profile_declares_valid_state_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps({
                "profile_id": "state-catalog-demo",
                "schema_version": 2,
                "ports": [],
                "state_catalog": json.loads(SMOKE_EXTENSION.read_text(encoding="utf-8")),
            }), encoding="utf-8")
            loaded = profile.DeviceProfile.load(path)
            extension = loaded.state_extension()
            self.assertEqual(extension.profile_id, "state-catalog-demo")

    def test_profile_rejects_bad_state_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps({
                "profile_id": "state-catalog-demo",
                "schema_version": 2,
                "ports": [],
                "state_catalog": {"states": [{"code": "NO_PREFIX", "category": "mode",
                                              "kind": "device", "label_zh": "x"}]},
            }), encoding="utf-8")
            with self.assertRaises(profile.ProfileError):
                profile.DeviceProfile.load(path)


class TwoProfileDisplayTests(unittest.TestCase):
    """用户验收：项目 A 只有部分 marker、项目 B 另一套 marker，均不补假状态。"""

    def build(self, name: str):
        extension = states.validate_extension(FIXTURES / f"{name}.extension.json")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            summary = eventlog.import_legacy_log(
                FIXTURES / f"{name}.txt", out, meta={},
                markers=FIXTURES / f"{name}.markers.json", extension=extension,
            )
            records = eventlog.read_events(result_layout.resolve_required(out, "events"))
            tool_log = result_layout.resolve_required(out, "tool_log").read_text(encoding="utf-8")
            findings = eventlog.verify_bundle(out, extension=extension)
        codes = [record.event for record in records]
        return extension, codes, tool_log, findings, summary

    def test_offline_profile_has_no_online_states(self) -> None:
        extension, codes, tool_log, findings, summary = self.build("profile_a_offline")
        self.assertEqual([code for code in codes if code != "TASK_START"],
                         ["WAKE_KEYWORD", "ASR_KEYWORD", "TTS_PLAY_ID"])
        self.assertFalse([code for code in codes if code.startswith("ONLINE_")])
        self.assertIn("唤醒：keyword=ni3 hao3", tool_log)
        self.assertIn("asr：da3 kai1 kong1 tiao2", tool_log)
        self.assertIn("tts：tts_id=1，play", tool_log)
        coverage = states.profile_coverage(extension, codes)
        for code in ("ONLINE_ASR_TEXT", "ONLINE_TTS_TEXT", "ONLINE_SKILL_TEXT"):
            self.assertIn(code, coverage["not_applicable"])
            self.assertNotIn(code, coverage["not_emitted"])
        self.assertEqual(coverage["counts"]["UNPARSED"], 0)
        self.assertFalse([item for item in findings if item["level"] == "ERROR"])

    def test_online_profile_emits_online_display_classes(self) -> None:
        extension, codes, tool_log, findings, summary = self.build("profile_b_online")
        for code in ("WAKE_TTS_CALLBACK", "ONLINE_ASR_TEXT", "ONLINE_TTS_TEXT", "ONLINE_SKILL_TEXT"):
            self.assertIn(code, codes)
        self.assertIn("tts：tts_id=1，wakeup_tts", tool_log)
        self.assertIn("online_asr：da3 kai1 kong1 tiao2", tool_log)
        self.assertIn("online_tts：tts_id=2，play", tool_log)
        self.assertIn("online_skill：open_ac", tool_log)
        coverage = states.profile_coverage(extension, codes)
        for code in ("ONLINE_ASR_TEXT", "ONLINE_TTS_TEXT", "ONLINE_SKILL_TEXT"):
            self.assertIn(code, coverage["emitted"])
            self.assertNotIn(code, coverage["not_applicable"])
        self.assertFalse([item for item in findings if item["level"] == "ERROR"])

    def test_both_profiles_hide_internal_tokens(self) -> None:
        for name in ("profile_a_offline", "profile_b_online"):
            _extension, _codes, tool_log, _findings, _summary = self.build(name)
            with self.subTest(profile=name):
                for token in ("raw_marker=", "catalog_status=", "raw_code=", "rule=",
                              "WAKE_KEYWORD", "ASR_KEYWORD", "ONLINE_ASR_TEXT",
                              "wakeup_callback", "online_asr_callbak", "match.group"):
                    self.assertNotIn(token, tool_log)


class TaskArtifactsIntegrationTests(unittest.TestCase):
    def test_task_artifacts_uses_extension_and_writes_device_events(self) -> None:
        from lstest.scripts.core import TaskArtifacts

        extension = load_extension()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "state-artifacts", structured_events=True,
                                      state_extension=extension)
            try:
                artifacts.write_device_event(
                    "start player", code="PLAYER_START", state="IDLE→PLAYING",
                    host_time="2026-11-12 10:00:01.000",
                )
                artifacts.write_device_event(
                    "vendor magic", code="BOGUS", host_time="2026-11-12 10:00:02.000",
                )
                artifacts.finalize("PASS", "unit test")
                summary = artifacts.structured_summary
            finally:
                pass
            self.assertIsNotNone(summary)
            self.assertEqual(summary["state_report"]["catalog_status"]["common"], 1)
            self.assertEqual(summary["state_report"]["catalog_status"]["unregistered"], 1)
            self.assertEqual(summary["state_report"]["catalog"]["profile_id"], "state-catalog-demo")


class CoverageAndCliTests(unittest.TestCase):
    def inventory(self) -> dict:
        return {
            "schema": "noahsark.state-inventory.v1",
            "states": [
                {"code": "WAKEUP", "category": "wake", "scope": "common",
                 "lstest_status": "emitted", "label_zh": "唤醒"},
                {"code": "P_TCL_SETLOGLEV_OK", "category": "mode", "scope": "tcl",
                 "lstest_status": "absent", "label_zh": "日志级别"},
                {"code": "SERIAL_LINE", "category": "serial", "scope": "common",
                 "lstest_status": "documented", "label_zh": "串口原始行"},
            ],
        }

    def test_coverage_report(self) -> None:
        report = states.coverage_report(self.inventory())
        self.assertEqual(report["inventory_states"], 3)
        self.assertIn("WAKEUP", report["supported"])
        self.assertIn("P_TCL_SETLOGLEV_OK", report["project_only"])
        self.assertEqual([item["code"] for item in report["missing"]], ["SERIAL_LINE"])

    def test_cli_list_check_and_coverage(self) -> None:
        python = sys.executable
        root = Path(__file__).resolve().parents[1]
        env = {"PYTHONPATH": str(root)}
        listed = subprocess.run(
            [python, "-m", "lstest.scripts.states", "list", "--category", "wake", "--json"],
            capture_output=True, text=True, env={**__import__("os").environ, **env}, check=False,
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        payload = json.loads(listed.stdout)
        self.assertTrue(all(item["category"] == "wake" for item in payload))

        checked = subprocess.run(
            [python, "-m", "lstest.scripts.states", "check", "--extension", str(SMOKE_EXTENSION)],
            capture_output=True, text=True, env={**__import__("os").environ, **env}, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["status"], "PASS")

        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "inventory.json"
            inventory.write_text(json.dumps(self.inventory()), encoding="utf-8")
            covered = subprocess.run(
                [python, "-m", "lstest.scripts.states", "coverage", "--inventory", str(inventory)],
                capture_output=True, text=True, env={**__import__("os").environ, **env}, check=False,
            )
            self.assertEqual(covered.returncode, 0, covered.stderr)
            self.assertEqual(json.loads(covered.stdout)["schema"], states.COVERAGE_SCHEMA)

            events = Path(tmp) / "events.jsonl"
            events.write_text(
                json.dumps({"event": "WAKE_KEYWORD", "details": {"device": True}}) + "\n"
                + json.dumps({"event": "DEVICE_UNPARSED",
                              "details": {"device": True, "raw_code": "MYSTERY_MARKER"}}) + "\n",
                encoding="utf-8",
            )
            profile_cov = subprocess.run(
                [python, "-m", "lstest.scripts.states", "profile-coverage",
                 "--extension", str(FIXTURES / "profile_a_offline.extension.json"),
                 "--events", str(events)],
                capture_output=True, text=True, env={**__import__("os").environ, **env}, check=False,
            )
            self.assertEqual(profile_cov.returncode, 0, profile_cov.stderr)
            payload = json.loads(profile_cov.stdout)
            self.assertEqual(payload["schema"], "lstest.state-profile-coverage.v1")
            self.assertIn("WAKE_KEYWORD", payload["emitted"])
            self.assertEqual(payload["unparsed"], ["MYSTERY_MARKER"])


if __name__ == "__main__":
    unittest.main()
