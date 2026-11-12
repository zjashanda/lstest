"""结构化事件日志 v2 的回归测试。

覆盖：真实日志 golden、旧格式容错、三层产物、双成功率、gate 与最终判定分离、
run.log/summary 确定性、verify 漂移检测、任务运行时镜像。
全部测试不访问设备、网络或真实结果目录。
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import eventlog
from lstest.scripts.core import TaskArtifacts

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "legacy_flat_v1_smoke10.txt"
GOLDEN_META = FIXTURES / "legacy_flat_v1_smoke10.meta.json"


def meta() -> dict:
    return json.loads(GOLDEN_META.read_text(encoding="utf-8"))


def build_golden(target: Path) -> dict:
    return eventlog.import_legacy_log(GOLDEN, target, meta=meta())


def records_of(target: Path) -> list[eventlog.EventRecord]:
    return eventlog.read_events(Path(target) / "events.jsonl")


class GoldenLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "golden"
        self.summary = build_golden(self.run_dir)

    def test_counts_and_dual_rates(self) -> None:
        summary = self.summary
        self.assertEqual(summary["meta"]["events_count"], 55)
        self.assertEqual(summary["counts"], {"rounds_total": 10, "pass": 10, "fail": 0, "blocked": 0, "other": 0})
        self.assertEqual(summary["rates"]["round_success_rate"], 100.0)
        self.assertEqual(summary["rates"]["first_attempt_success_rate"], 0.0)
        self.assertEqual(summary["rates"]["first_attempt_gap_pct"], 100.0)
        self.assertEqual(summary["rates"]["attempt_distribution"]["counts"]["1"], 0)
        self.assertEqual(summary["rates"]["attempt_distribution"]["counts"]["2"], 10)
        self.assertEqual(summary["rates"]["avg_attempts"], 2.0)

    def test_gate_conflict_never_signs_pass(self) -> None:
        summary = self.summary
        self.assertEqual(summary["test_outcome"], "PASS")
        self.assertEqual(summary["delivery_status"], "DELIVERY_INCOMPLETE")
        self.assertEqual(summary["final_status"], "NEEDS_REVIEW")
        self.assertNotEqual(summary["final_status"], "PASS")
        self.assertEqual(summary["gate"]["failed_checks"], ["structured_artifacts_present"])
        self.assertEqual(summary["delivery_failed_checks"], ["structured_artifacts_present"])
        codes = [item["code"] for item in summary["conflicts"]]
        self.assertIn("TASK_END_STATUS_CONFLICT", codes)

    def test_first_attempt_anomaly_is_reported(self) -> None:
        anomalies = {item["code"]: item for item in self.summary["anomalies"]}
        self.assertEqual(anomalies["NO_WAKE"]["count"], 10)
        self.assertTrue(anomalies["NO_WAKE"]["recovered"])
        self.assertEqual(anomalies["NO_WAKE"]["rounds"], list(range(1, 11)))
        self.assertEqual(self.summary["responsibility"]["layer"], "tool")
        self.assertIsNone(self.summary["first_failure"])
        self.assertEqual(self.summary["worst_round"]["round"], 1)

    def test_run_log_is_compact_and_human_readable(self) -> None:
        text = (self.run_dir / "run.log").read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertLess(len(lines), 120)
        self.assertLess(len(lines), 1595 / 10)
        self.assertTrue(lines[0].startswith("# lstest run.log v2"))
        self.assertIn("first_attempt_success_rate=0%", text)
        self.assertIn("final=NEEDS_REVIEW", text)
        self.assertIn("R01 ", text)
        self.assertNotIn("- -", text)
        self.assertIn("SUMMARY 责任层=tool", text)
        self.assertIn("SUMMARY 下一步:", text)
        self.assertNotIn("D:\\", text)
        # 每个事件至少一行；异常另起一行缩进展开
        self.assertIn("└", text)

    def test_events_jsonl_schema_and_raw_externalization(self) -> None:
        records = records_of(self.run_dir)
        self.assertEqual([record.event_seq for record in records], list(range(1, 56)))
        for record in records:
            payload = record.to_dict()
            for field in eventlog.REQUIRED_FIELDS:
                self.assertIn(field, payload)
            self.assertEqual(payload["schema"], eventlog.EVENT_SCHEMA)
            self.assertNotIn("raw", payload)
        raw_records = [record for record in records if record.raw_ref]
        self.assertGreaterEqual(len(raw_records), 10)
        for record in raw_records:
            target = self.run_dir / record.raw_ref["path"]
            self.assertTrue(target.is_file(), record.raw_ref["path"])
            self.assertEqual(eventlog.sha256_file(target), record.raw_ref["sha256"])
        start = records[0]
        self.assertIsInstance(start.raw_ref, dict)
        self.assertEqual(start.details["environment"]["node"], "node-a")

    def test_summary_answers_required_questions(self) -> None:
        text = (self.run_dir / "summary.md").read_text(encoding="utf-8")
        for heading in (
            "一句话结论", "测了什么", "测试对象与环境", "最终结论与签发依据",
            "结果统计", "异常与趋势", "首败", "责任层", "下一步", "证据索引",
        ):
            self.assertIn(heading, text)
        self.assertIn("first_attempt_success_rate", text)
        self.assertIn("structured_artifacts_present", text)
        self.assertIn("node-a", text)

    def test_rerender_is_byte_identical(self) -> None:
        before_log = (self.run_dir / "run.log").read_bytes()
        before_md = (self.run_dir / "summary.md").read_bytes()
        summary = json.loads((self.run_dir / "summary.json").read_text(encoding="utf-8"))
        records = records_of(self.run_dir)
        eventlog.write_bundle(records, self.run_dir, meta={
            "title": summary["meta"]["title"],
            "purpose": summary["meta"]["purpose"],
            "scope": summary["meta"]["scope"],
        })
        self.assertEqual((self.run_dir / "run.log").read_bytes(), before_log)
        self.assertEqual((self.run_dir / "summary.md").read_bytes(), before_md)

    def test_verify_passes_and_detects_drift(self) -> None:
        findings = eventlog.verify_bundle(self.run_dir)
        errors = [item for item in findings if item["level"] == "ERROR"]
        self.assertEqual(errors, [])
        self.assertTrue(any(item["code"] == "SOURCE_STATUS_CONFLICT" for item in findings))

        log_path = self.run_dir / "run.log"
        original = log_path.read_text(encoding="utf-8")
        log_path.write_text(original + "手工追加的一行\n", encoding="utf-8")
        codes = {item["code"] for item in eventlog.verify_bundle(self.run_dir)}
        self.assertIn("RUNLOG_DRIFT", codes)
        log_path.write_text(original, encoding="utf-8")

        summary_path = self.run_dir / "summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        payload["rates"]["first_attempt_success_rate"] = 100.0
        summary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        codes = {item["code"] for item in eventlog.verify_bundle(self.run_dir)}
        self.assertIn("SUMMARY_DRIFT", codes)

    def test_check_files_reports_missing_evidence_as_warning(self) -> None:
        findings = eventlog.verify_bundle(self.run_dir, check_files=True)
        self.assertFalse([item for item in findings if item["level"] == "ERROR"])
        self.assertTrue(any(item["code"] == "MISSING_EVIDENCE" for item in findings))


class LegacyCompatibilityTests(unittest.TestCase):
    def test_crlf_bom_and_missing_blank_lines(self) -> None:
        block = (
            "time: 2026-11-12 10:00:00.000\nlevel: INFO\nevent: TASK_START\ntask_id: t1\n"
            "expected: {\"mode\": \"smoke\"}\nactual: -\nraw: -\nmessage: 中文消息\n"
        )
        text = "\ufeff" + (block + block.replace("TASK_START", "TASK_END").replace("10:00:00", "10:00:01")).replace("\n", "\r\n")
        payloads, warnings = eventlog.parse_legacy_flat(text)
        self.assertEqual([item["event"] for item in payloads], ["TASK_START", "TASK_END"])
        self.assertEqual(payloads[0]["message"], "中文消息")
        self.assertIsNone(payloads[0]["actual"])

    def test_multiline_json_and_unknown_fields_are_kept(self) -> None:
        text = (
            "time: 2026-11-12 10:00:00.000\nlevel: INFO\nevent: BROADCAST\n"
            "raw: {\n  \"wake\": true,\n  \"nested\": {\"a\": 1}\n}\n"
            "custom_field: keep-me\n"
        )
        payloads, _ = eventlog.parse_legacy_flat(text)
        self.assertEqual(payloads[0]["raw"], {"wake": True, "nested": {"a": 1}})
        record = eventlog.record_from_mapping(payloads[0], seq=1)
        self.assertEqual(record.details["legacy_fields"]["custom_field"], "keep-me")

    def test_missing_event_becomes_unknown_with_warning(self) -> None:
        text = "time: 2026-11-12 10:00:00.000\nlevel: INFO\nstatus: PASS\n"
        payloads, _ = eventlog.parse_legacy_flat(text)
        record = eventlog.record_from_mapping(payloads[0], seq=1)
        self.assertEqual(record.event, "UNKNOWN")
        self.assertEqual(record.time, "2026-11-12 10:00:00.000")

    def test_windows_paths_are_sanitized_and_chinese_kept(self) -> None:
        text = (
            "time: 2026-11-12 10:00:00.000\nevent: TASK_START\n"
            "details: {\"task_dir\": \"D:\\\\tasks\\\\NOAH-1\", "
            "\"result_dir\": \"D:\\\\tasks\\\\NOAH-1\\\\result\\\\r1\", "
            "\"wake_wav\": \"D:\\\\tasks\\\\NOAH-1\\\\assets\\\\w.wav\"}\n"
            "message: 任务目录 D:\\\\tasks\\\\NOAH-1 已创建\n"
            "evidence: [\"serial_logs\\\\serial_COM4_csk.log#000123\"]\n"
        )
        payloads, _ = eventlog.parse_legacy_flat(text)
        record = eventlog.record_from_mapping(payloads[0], seq=1)
        self.assertEqual(record.details["task_dir"], "NOAH-1")
        self.assertEqual(record.details["result_dir"], "r1")
        self.assertEqual(record.details["wake_wav"], "w.wav")
        self.assertEqual(record.evidence[0], {"path": "serial_logs/serial_COM4_csk.log", "line": 123})
        # 消息文本保留中文原文，不因为路径清洗而丢失
        self.assertIn("任务目录", record.message)

    def test_not_a_legacy_log_raises(self) -> None:
        with self.assertRaises(eventlog.LegacyFormatError):
            eventlog.parse_legacy_flat("hello\nworld\n")

    def test_huge_raw_is_externalized_without_inline_blob(self) -> None:
        blob = "x" * 50000
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            writer = eventlog.EventLogWriter(run_dir, "t1")
            writer.emit("EXTERNAL_TOOL_OUTPUT", raw={"output": blob}, status="PASS")
            writer.close()
            payload = (run_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertLess(len(payload.splitlines()[0]), 1200)
            self.assertNotIn(blob, payload)
            record = eventlog.read_events(run_dir / "events.jsonl")[0]
            self.assertGreater(record.raw_ref["bytes"], 50000)
            self.assertTrue((run_dir / record.raw_ref["path"]).is_file())


class VerdictRuleTests(unittest.TestCase):
    def _summary(self, events: list[dict], meta: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            writer = eventlog.EventLogWriter(run_dir, "t")
            for item in events:
                payload = dict(item)
                name = payload.pop("event")
                writer.emit(name, **payload)
            writer.close()
            return eventlog.write_bundle(eventlog.read_events(run_dir / "events.jsonl"), run_dir, meta=meta or {})

    def _round(self, number: int, status: str = "PASS", attempts: int = 1) -> dict:
        return {
            "event": "ROUND_RESULT", "round": number, "case_id": f"c{number}", "status": status,
            "actual": {"attempts_total": attempts, "first_success_attempt": 1 if attempts == 1 else None},
        }

    def test_full_pass_requires_gate_true(self) -> None:
        gate = {"checks": {"all_pass": True, "artifacts": True}, "gate_pass": True}
        summary = self._summary([
            {"event": "TASK_START", "expected": {"mode": "smoke", "target_rounds": 1}},
            self._round(1),
            {"event": "TASK_END", "status": "PASS", "actual": {"gate": gate}},
        ])
        self.assertEqual(summary["final_status"], "PASS")
        self.assertEqual(summary["delivery_status"], "COMPLETE")

    def test_missing_gate_is_needs_review(self) -> None:
        summary = self._summary([
            self._round(1),
            {"event": "TASK_END", "status": "PASS", "actual": {}},
        ])
        self.assertEqual(summary["gate"], None)
        self.assertEqual(summary["delivery_status"], "UNKNOWN")
        self.assertEqual(summary["final_status"], "NEEDS_REVIEW")

    def test_failed_round_is_fail_even_with_gate_true(self) -> None:
        summary = self._summary([
            self._round(1, status="FAIL", attempts=10),
            {"event": "TASK_END", "status": "FAIL", "actual": {"gate": {"gate_pass": True, "checks": {}}}},
        ])
        self.assertEqual(summary["test_outcome"], "FAIL")
        self.assertEqual(summary["final_status"], "FAIL")
        self.assertEqual(summary["counts"]["fail"], 1)

    def test_blocked_round_wins_over_pass(self) -> None:
        summary = self._summary([
            self._round(1),
            self._round(2, status="BLOCKED"),
            {"event": "TASK_END", "status": "BLOCKED", "actual": {"gate": {"gate_pass": False, "checks": {"device_available": False}}}},
        ])
        self.assertEqual(summary["test_outcome"], "BLOCKED")
        self.assertEqual(summary["delivery_status"], "BLOCKED")
        self.assertEqual(summary["final_status"], "BLOCKED")

    def test_interrupted_run_without_task_end(self) -> None:
        summary = self._summary([
            self._round(1, attempts=2),
            {"event": "BROADCAST", "round": 2, "status": "NO_WAKE", "attempt": 1, "max_attempts": 10},
        ])
        self.assertEqual(summary["test_outcome"], "INCOMPLETE")
        self.assertEqual(summary["final_status"], "NEEDS_REVIEW")
        self.assertTrue(any("TASK_END" in item for item in summary["missing"]))

    def test_first_attempt_rate_differs_from_round_rate(self) -> None:
        summary = self._summary([
            self._round(1, attempts=2), self._round(2, attempts=2),
            {"event": "TASK_END", "status": "PASS", "actual": {"gate": {"gate_pass": True, "checks": {}}}},
        ])
        self.assertEqual(summary["rates"]["round_success_rate"], 100.0)
        self.assertEqual(summary["rates"]["first_attempt_success_rate"], 0.0)
        self.assertEqual(summary["rates"]["attempt_distribution"]["counts"]["none"], 2)


class TaskArtifactsIntegrationTests(unittest.TestCase):
    def test_structured_events_are_mirrored_and_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "fixture", structured_events=True)
            artifacts.emit("TASK_STARTED", message="开始", expected={"mode": "smoke", "target_rounds": 1})
            artifacts.emit("ROUND_RESULT", message="R1", round=1, status="PASS", actual={"attempts_total": 1, "first_success_attempt": 1})
            artifacts.emit("TASK_END", message="结束", status="PASS", actual={"gate": {"gate_pass": True, "checks": {}}})
            artifacts.finalize("PASS", "fixture")
            run_dir = artifacts.run_dir
            self.assertTrue((run_dir / "events.jsonl").is_file())
            self.assertTrue((run_dir / "run.log").is_file())
            self.assertTrue((run_dir / "summary.md").is_file())
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["rates"]["round_success_rate"], 100.0)
            self.assertEqual(summary["final_status"], "PASS")
            self.assertEqual(eventlog.verify_bundle(run_dir), [])

    def test_structured_events_off_keeps_v1_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "fixture")
            artifacts.emit("TASK_STARTED", message="开始")
            artifacts.finalize("PASS", "fixture")
            names = {item.name for item in artifacts.run_dir.iterdir()}
            self.assertNotIn("events.jsonl", names)
            self.assertIn("tool.log", names)


class TimeContractLintTests(unittest.TestCase):
    """time lint：v2 产物只允许北京时间无时区毫秒格式。"""

    FORBIDDEN = (
        re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"),
        re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
        re.compile(r"[+-](?:0\d|1[0-4]):[0-5]\d\b"),
    )

    def test_event_times_match_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            build_golden(run_dir)
            for record in records_of(run_dir):
                self.assertRegex(record.time or "", eventlog.TIME_RE)

    def test_render_outputs_have_no_timezone_or_t_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            build_golden(run_dir)
            for name in ("run.log", "summary.md"):
                text = (run_dir / name).read_text(encoding="utf-8")
                for pattern in self.FORBIDDEN:
                    self.assertIsNone(pattern.search(text), f"{name} 命中 {pattern.pattern}")

    def test_module_source_has_no_forbidden_strftime(self) -> None:
        source = Path(eventlog.__file__).read_text(encoding="utf-8")
        self.assertNotIn("%Y-%m-%dT", source)
        self.assertNotIn("%H:%M:%SZ", source)
        self.assertNotIn("isoformat(", source)


class RedactionTests(unittest.TestCase):
    """secret/脱敏扫描：公开仓库里的样本与文档不得含内网、账号、用户目录、凭据。"""

    PATTERNS = {
        "internal_ip": re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        "node_name": re.compile(r"\bwin\d{3}\b"),
        "user_name": re.compile(r"\bAdministrator\b"),
        "user_dir": re.compile(r"D:\\\\zhsh\\\\"),
        "secret": re.compile(r"(?i)(access_key_secret|access_key_id|lstts_tts_token)\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{8,}"),
    }

    def test_fixtures_and_docs_are_redacted(self) -> None:
        root = Path(__file__).resolve().parents[1]
        targets = [
            FIXTURES / "legacy_flat_v1_smoke10.txt",
            FIXTURES / "PROVENANCE.md",
            FIXTURES / "legacy_flat_v1_smoke10.meta.json",
            root / "references" / "structured-event-log-v2.md",
            root / "scripts" / "eventlog.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for name, pattern in self.PATTERNS.items():
                match = pattern.search(text)
                self.assertIsNone(match, f"{path.name} 命中 {name}: {match.group(0) if match else ''}")


class CliTests(unittest.TestCase):
    def test_build_verify_and_schema(self) -> None:
        from lstest.scripts.lstest import main

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self.assertEqual(main(["eventlog", "build", "--legacy", str(GOLDEN), "--out-dir", str(out)]), 0)
            self.assertEqual(main(["eventlog", "verify", "--run-dir", str(out)]), 0)
            schema_file = Path(tmp) / "schema.json"
            self.assertEqual(main(["eventlog", "schema", "--output", str(schema_file)]), 0)
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
            self.assertEqual(schema["event_schema"], eventlog.EVENT_SCHEMA)
            self.assertEqual(schema["bundle_version"], 2)


if __name__ == "__main__":
    unittest.main()
