"""结构化事件日志 v2 的回归测试。

覆盖：真实日志 golden（TOOL + DEVICE 单行时序）、旧格式容错、三层产物、
双成功率、gate 与最终判定分离、tool.log 单行/毫秒/排序/去重/未知行、
serial host receive time、确定性重算与漂移检测、任务运行时镜像。
全部测试不访问设备、网络或真实结果目录。
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from lstest.scripts import eventlog
from lstest.scripts.core import ConnectionSpec, TaskArtifacts
from lstest.scripts.serial_capture import SerialManager

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "legacy_flat_v1_smoke10.txt"
GOLDEN_META = FIXTURES / "legacy_flat_v1_smoke10.meta.json"
GOLDEN_MARKERS = FIXTURES / "legacy_flat_v1_smoke10.markers.json"

LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} "
    r"\[[^\]]+\]\[[A-Z_]+\]\[[A-Z_]+\](?:\[[A-Z_]+\])? .+$"
)


def meta() -> dict:
    return json.loads(GOLDEN_META.read_text(encoding="utf-8"))


def build_golden(target: Path) -> dict:
    return eventlog.import_legacy_log(
        GOLDEN, target, meta=meta(), markers=GOLDEN_MARKERS,
    )


def records_of(target: Path) -> list[eventlog.EventRecord]:
    return eventlog.read_events(Path(target) / "events.jsonl")


def tool_lines(target: Path) -> list[str]:
    return (Path(target) / "tool.log").read_text(encoding="utf-8").splitlines()


class GoldenLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "golden"
        self.summary = build_golden(self.run_dir)

    def test_counts_and_dual_rates(self) -> None:
        summary = self.summary
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
        self.assertEqual(summary["gate"]["failed_checks"], ["structured_artifacts_present"])
        codes = [item["code"] for item in summary["conflicts"]]
        self.assertIn("TASK_END_STATUS_CONFLICT", codes)

    def test_tool_log_is_one_line_per_event(self) -> None:
        lines = tool_lines(self.run_dir)
        events = records_of(self.run_dir)
        self.assertEqual(len(lines), len(events))
        self.assertEqual(len(events), 105)  # 55 TOOL + 50 DEVICE
        self.assertEqual(len([r for r in events if (r.details or {}).get("device")]), 50)
        for number, line in enumerate(lines, start=1):
            self.assertRegex(line, LINE_RE, f"第 {number} 行不是单行时序")
            self.assertLess(len(line), 600, f"第 {number} 行过长（{len(line)}）")
        self.assertEqual(eventlog.validate_tool_log_text("\n".join(lines)), [])
        # 严格按 host 毫秒升序
        stamps = [line[:23] for line in lines]
        self.assertEqual(stamps, sorted(stamps))
        # 不允许续行/空行/段落汇总
        self.assertNotIn("", lines)
        self.assertFalse([line for line in lines if line.startswith((" ", "\t", "SUMMARY", "#"))])

    def test_tool_log_contains_tool_and_device_categories(self) -> None:
        text = "\n".join(tool_lines(self.run_dir))
        for category in ("[RUN]", "[ENV]", "[SERIAL]", "[PDU]", "[WAKE]", "[REC]", "[RESULT]", "[DEVICE]"):
            self.assertIn(category, text)
        self.assertEqual(text.count("[DEVICE][POWER_OFF]"), 10)
        self.assertEqual(text.count("[DEVICE][POWER_ON]"), 10)
        self.assertEqual(text.count("[DEVICE][PRE_WAKEUP]"), 10)
        self.assertEqual(text.count("[DEVICE][PLAYER_START]"), 10)
        self.assertEqual(text.count("[DEVICE][PLAYER_COMPLETE]"), 10)
        self.assertIn("state=ON→OFF", text)
        self.assertIn("state=OFF→ON", text)
        self.assertEqual(text.count("[WAKE][NO_WAKE]"), 10)
        self.assertIn("device_ts=", text)
        self.assertIn("ncm=", text)
        self.assertIn("threshold=", text)

    def test_judgement_and_verdict_are_explicit(self) -> None:
        text = "\n".join(tool_lines(self.run_dir))
        no_wake = [line for line in tool_lines(self.run_dir) if "[WAKE][NO_WAKE]" in line]
        self.assertEqual(len(no_wake), 10)
        self.assertIn("expected=", no_wake[0])
        self.assertIn("actual=", no_wake[0])
        self.assertIn("hit=none", no_wake[0])
        self.assertIn("next=RETRY", no_wake[0])
        self.assertIn("window_s=1", no_wake[0])
        end_lines = [line for line in tool_lines(self.run_dir) if "[TASK][RUN][END]" in line]
        self.assertEqual(len(end_lines), 1)
        end = end_lines[0]
        self.assertIn("[END][NEEDS_REVIEW]", end)
        self.assertIn("recorded=PASS", end)
        self.assertIn("test_outcome=PASS", end)
        self.assertIn("delivery=DELIVERY_INCOMPLETE", end)
        self.assertIn("gate=FAIL(structured_artifacts_present)", end)
        self.assertIn("rounds=10/10", end)
        self.assertNotIn("final=PASS", text)

    def test_tool_log_hides_bulk_raw_and_absolute_paths(self) -> None:
        text = "\n".join(tool_lines(self.run_dir))
        for forbidden in ("stdout_stderr", "Refresh PA to OFF", "D:\\", "10.11."):
            self.assertNotIn(forbidden, text)
        self.assertIn("evidence=", text)

    def test_events_jsonl_schema_and_raw_externalization(self) -> None:
        records = records_of(self.run_dir)
        self.assertEqual([record.event_seq for record in records], list(range(1, len(records) + 1)))
        for record in records:
            payload = record.to_dict()
            for field in eventlog.REQUIRED_FIELDS:
                self.assertIn(field, payload)
            self.assertEqual(payload["schema"], eventlog.EVENT_SCHEMA)
            self.assertNotIn("raw", payload)
            self.assertRegex(record.time or "", eventlog.TIME_RE)
        raw_records = [record for record in records if record.raw_ref]
        self.assertGreaterEqual(len(raw_records), 40)
        for record in raw_records:
            target = self.run_dir / record.raw_ref["path"]
            self.assertTrue(target.is_file(), record.raw_ref["path"])
            self.assertEqual(eventlog.sha256_file(target), record.raw_ref["sha256"])

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
        self.assertIn("首轮稳定性缺口 100.0 个百分点", text)

    def test_rerender_is_byte_identical(self) -> None:
        before_log = (self.run_dir / "tool.log").read_bytes()
        before_md = (self.run_dir / "summary.md").read_bytes()
        summary = json.loads((self.run_dir / "summary.json").read_text(encoding="utf-8"))
        records = records_of(self.run_dir)
        eventlog.write_bundle(records, self.run_dir, meta={
            "title": summary["meta"]["title"],
            "purpose": summary["meta"]["purpose"],
            "scope": summary["meta"]["scope"],
        })
        self.assertEqual((self.run_dir / "tool.log").read_bytes(), before_log)
        self.assertEqual((self.run_dir / "summary.md").read_bytes(), before_md)

    def test_verify_passes_and_detects_drift(self) -> None:
        findings = eventlog.verify_bundle(self.run_dir)
        self.assertEqual([item for item in findings if item["level"] == "ERROR"], [])
        self.assertTrue(any(item["code"] == "SOURCE_STATUS_CONFLICT" for item in findings))

        log_path = self.run_dir / "tool.log"
        original = log_path.read_text(encoding="utf-8")
        log_path.write_text(original + "2026-11-12 17:07:10.000 [TASK][RUN][EVENT] 手工追加\n", encoding="utf-8")
        codes = {item["code"] for item in eventlog.verify_bundle(self.run_dir)}
        self.assertIn("TOOL_LOG_DRIFT", codes)
        log_path.write_text(original + "续行没有时间戳\n", encoding="utf-8")
        codes = {item["code"] for item in eventlog.verify_bundle(self.run_dir)}
        self.assertTrue({"TOOL_LOG_DRIFT", "TOOL_LOG_FORMAT"} & codes)
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


class ToolLogUnitTests(unittest.TestCase):
    def test_ensure_ms_normalizes_all_supported_forms(self) -> None:
        cases = {
            "2026-11-12 17:02:22.527": "2026-11-12 17:02:22.527",
            "2026-11-12 17:02:22": "2026-11-12 17:02:22.000",
            "2026-11-12T17:02:22.5+08:00": "2026-11-12 17:02:22.500",
            "2026-11-12T09:02:22.527Z": "2026-11-12 09:02:22.527",
            "2026-11-12 17:02:22.527123": "2026-11-12 17:02:22.527",
        }
        for raw, expected in cases.items():
            self.assertEqual(eventlog.ensure_ms(raw), expected)

    def test_one_line_escapes_newlines_and_control_chars(self) -> None:
        text = eventlog.one_line("第一行\n第二行\r\n第三行\t带制表符\x07")
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("\x07", text)
        self.assertIn("\\n", text)

    def test_render_sorts_by_host_time_and_keeps_one_line(self) -> None:
        records = [
            eventlog.EventRecord(event="TASK_END", time="2026-11-12 10:00:03.000", status="PASS",
                                 message="结束", actual={"gate": {"gate_pass": True, "checks": {}}}),
            eventlog.EventRecord(event="BROADCAST", time="2026-11-12 10:00:01.000", round=1,
                                 case_id="c1", attempt=1, status="NO_WAKE", message="未唤醒",
                                 expected={"wake": True}, actual={"wake_seen": False}, handling="continue"),
            eventlog.EventRecord(event="TASK_START", time="2026-11-12 10:00:00.000", expected={"mode": "smoke"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            eventlog.write_event_records(records, run_dir / "events.jsonl")
            summary = eventlog.write_bundle(eventlog.read_events(run_dir / "events.jsonl"), run_dir)
            lines = (run_dir / "tool.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual([line[:23] for line in lines],
                         ["2026-11-12 10:00:00.000", "2026-11-12 10:00:01.000", "2026-11-12 10:00:03.000"])
        self.assertIn("[R01/A01][WAKE][NO_WAKE]", lines[1])
        self.assertIn("next=RETRY", lines[1])
        self.assertIn("final=NEEDS_REVIEW", lines[2])  # 只有 NO_WAKE 事件、没有 ROUND_RESULT
        self.assertEqual(summary["test_outcome"], "UNKNOWN")

    def test_repeated_device_states_are_collapsed_with_repeat_count(self) -> None:
        events = [
            eventlog.EventRecord(
                event="BOOT_STAGE", time=f"2026-11-12 10:00:0{index}.000", round=1,
                source="device", message="启动阶段",
                details={"device": True, "audit_category": "DEVICE", "audit_kind": "STATE",
                         "state": "BOOT", "parameters": {"stage": 2}},
            )
            for index in range(3)
        ]
        events.append(eventlog.EventRecord(
            event="READY", time="2026-11-12 10:00:09.000", round=1, source="device",
            message="就绪", details={"device": True, "audit_category": "DEVICE", "audit_kind": "STATE",
                                    "state": "READY"},
        ))
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            eventlog.write_event_records(events, run_dir / "events.jsonl")
            eventlog.write_bundle(eventlog.read_events(run_dir / "events.jsonl"), run_dir)
            lines = tool_lines(run_dir)
        self.assertEqual(len(lines), 2)
        self.assertIn("[DEVICE][BOOT_STAGE]", lines[0])
        self.assertIn("repeat=3", lines[0])
        self.assertIn("last_hit=2026-11-12 10:00:02.000", lines[0])
        self.assertIn("就绪", lines[1])

    def test_device_unparsed_keeps_hash_and_evidence(self) -> None:
        line = "totally unknown native line 中文"
        record = eventlog.device_unparsed_event(line, evidence=[{"path": "serial_logs/x.log", "line": 7}])
        self.assertEqual(record.event, "DEVICE_UNPARSED")
        self.assertEqual(record.details["raw_sha256"], eventlog.sha256_bytes(line.encode("utf-8")))
        self.assertIn("未识别设备行", record.message)
        self.assertEqual(record.evidence, [{"path": "serial_logs/x.log", "line": 7}])
        rendered = eventlog.render_tool_log({"tooling": {}}, [record])
        self.assertIn("[DEVICE][UNPARSED]", rendered)
        self.assertIn("未识别设备行", rendered)
        self.assertEqual(len(rendered.splitlines()), 1)

    def test_marker_config_errors_are_explicit(self) -> None:
        with self.assertRaises(eventlog.MarkerConfigError):
            eventlog.load_markers({"markers": [{"source": "raw"}]})
        with self.assertRaises(eventlog.MarkerConfigError):
            eventlog.load_markers({"markers": [{"source": "raw", "code": "X", "pattern": "("}]})
        markers, _ = eventlog.load_markers({"markers": [
            {"source": "raw", "code": "x", "pattern": "hit", "state": "A→B"},
        ]})
        self.assertEqual(markers[0].code, "X")
        self.assertEqual(markers[0].state, "A→B")


class LegacyCompatibilityTests(unittest.TestCase):
    def test_crlf_bom_and_missing_blank_lines(self) -> None:
        block = (
            "time: 2026-11-12 10:00:00.000\nlevel: INFO\nevent: TASK_START\ntask_id: t1\n"
            "expected: {\"mode\": \"smoke\"}\nactual: -\nraw: -\nmessage: 中文消息\n"
        )
        merged = block + block.replace("TASK_START", "TASK_END").replace("10:00:00", "10:00:01")
        payloads, warnings = eventlog.parse_legacy_flat("\ufeff" + merged.replace("\n", "\r\n"))
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

    def test_seconds_only_time_is_normalized_to_ms(self) -> None:
        text = "time: 2026-11-12 10:00:00\nevent: BROADCAST\n"
        payloads, _ = eventlog.parse_legacy_flat(text)
        self.assertEqual(eventlog.record_from_mapping(payloads[0]).time, "2026-11-12 10:00:00.000")

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
            {"event": "TASK_END", "status": "BLOCKED",
             "actual": {"gate": {"gate_pass": False, "checks": {"device_available": False}}}},
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


class FakeSerial:
    def __init__(self, *_args, **_kwargs):
        self.closed = False
        self.lines = [b"[2026-11-12 17:00:01.100][D][evs_event] wake result: bMain=1\r\n"]

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


class SerialTimestampTests(unittest.TestCase):
    def test_serial_log_prefix_is_host_receive_ms_and_raw_text_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "serial")
            artifacts.freeze_cases([{"case_id": "c1", "scenario": "fixture"}])
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM1", "baudrate": 115200, "role": "csk"}]})
            manager = SerialManager(spec.ports, artifacts, factory=FakeSerial)
            manager.start()
            time.sleep(0.05)
            manager.stop()
            artifacts.finalize("PASS", "fixture complete")
            log = artifacts.run_dir / "serial_logs" / "serial_COM1_csk.log"
            content = log.read_text(encoding="utf-8")
            match = re.search(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] COM1 csk #1 ", content, re.M)
            self.assertIsNotNone(match, content)
            self.assertIn("wake result: bMain=1", content)


class TaskArtifactsIntegrationTests(unittest.TestCase):
    def test_structured_events_are_mirrored_and_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "fixture", structured_events=True)
            artifacts.emit("TASK_STARTED", message="开始", expected={"mode": "smoke", "target_rounds": 1})
            artifacts.emit("ROUND_RESULT", message="R1", round=1, status="PASS",
                           actual={"attempts_total": 1, "first_success_attempt": 1})
            artifacts.emit("TASK_END", message="结束", status="PASS",
                           actual={"gate": {"gate_pass": True, "checks": {}}})
            artifacts.finalize("PASS", "fixture")
            run_dir = artifacts.run_dir
            self.assertTrue((run_dir / "events.jsonl").is_file())
            self.assertTrue((run_dir / "tool.log").is_file())
            self.assertTrue((run_dir / "tool.live.log").is_file())
            self.assertTrue((run_dir / "summary.md").is_file())
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["rates"]["round_success_rate"], 100.0)
            self.assertEqual(summary["final_status"], "PASS")
            self.assertEqual(eventlog.verify_bundle(run_dir), [])
            lines = tool_lines(run_dir)
            self.assertEqual(len(lines), json.loads((run_dir / "summary.json").read_text())["meta"]["events_count"])

    def test_structured_events_off_keeps_v1_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "fixture")
            artifacts.emit("TASK_STARTED", message="开始")
            artifacts.finalize("PASS", "fixture")
            names = {item.name for item in artifacts.run_dir.iterdir()}
            self.assertNotIn("events.jsonl", names)
            self.assertNotIn("tool.live.log", names)
            self.assertIn("tool.log", names)


class TimeContractLintTests(unittest.TestCase):
    """time lint：所有产物行必须是北京时间毫秒，禁止 T/Z/偏移。"""

    FORBIDDEN = (
        re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"),
        re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
        re.compile(r"[+-](?:0\d|1[0-4]):[0-5]\d\b"),
    )

    def test_every_event_time_has_milliseconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "golden"
            build_golden(run_dir)
            for record in records_of(run_dir):
                self.assertRegex(record.time or "", eventlog.TIME_RE)

    def test_tool_log_lines_all_start_with_ms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "golden"
            build_golden(run_dir)
            for line in tool_lines(run_dir):
                self.assertRegex(line[:23], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
                for pattern in self.FORBIDDEN:
                    self.assertIsNone(pattern.search(line), f"命中 {pattern.pattern}: {line[:120]}")

    def test_module_source_has_no_forbidden_strftime(self) -> None:
        source = Path(eventlog.__file__).read_text(encoding="utf-8")
        self.assertNotIn("%Y-%m-%dT", source)
        self.assertNotIn("%H:%M:%SZ", source)
        self.assertNotIn("isoformat(", source)


class RedactionTests(unittest.TestCase):
    """secret/脱敏扫描：公开仓库样本与文档不得含内网、账号、用户目录、凭据。"""

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
            FIXTURES / "legacy_flat_v1_smoke10.markers.json",
            root / "references" / "structured-event-log-v2.md",
            root / "scripts" / "eventlog.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for name, pattern in self.PATTERNS.items():
                match = pattern.search(text)
                self.assertIsNone(match, f"{path.name} 命中 {name}: {match.group(0) if match else ''}")


class DeviceCoverageTests(unittest.TestCase):
    """覆盖启动/串口/唤醒/识别/TTS/播放/录音/业务/异常恢复的设备状态渲染。"""

    def _device(self, event_code: str, moment: str, state: str, message: str, **parameters) -> eventlog.EventRecord:
        return eventlog.EventRecord(
            event=event_code, time=moment, round=1, phase="coverage", source="device", message=message,
            details={"device": True, "state": state, "parameters": parameters},
        )

    def test_all_device_categories_render_one_line_each(self) -> None:
        records = [
            eventlog.EventRecord(event="TASK_START", time="2026-11-12 10:00:00.000",
                                 expected={"mode": "smoke"}),
            self._device("BOOT_START", "2026-11-12 10:00:01.000", "OFF→BOOTING", "设备开始启动", firmware="fw1"),
            self._device("READY", "2026-11-12 10:00:03.000", "BOOTING→READY", "语音算法就绪"),
            self._device("SERIAL_UP", "2026-11-12 10:00:03.100", "DOWN→UP", "串口链路建立", port="COM4"),
            self._device("WAKEUP", "2026-11-12 10:00:05.000", "IDLE→WAKE", "检测到主唤醒",
                         keyword="kw", ncm="871", threshold="800"),
            self._device("ASR_START", "2026-11-12 10:00:05.200", "WAKE→ASR", "开始离线识别", route="offline"),
            self._device("ASR_RESULT", "2026-11-12 10:00:05.600", "ASR→DONE", "离线识别完成",
                         text_sha="abc123", intent="open"),
            self._device("TTS_START", "2026-11-12 10:00:06.000", "IDLE→TTS", "开始响应 TTS",
                         mode="online", text_sha="def456"),
            self._device("TTS_END", "2026-11-12 10:00:06.500", "TTS→DONE", "TTS 合成结束", elapsed="520ms"),
            self._device("PLAYER_START", "2026-11-12 10:00:06.600", "IDLE→PLAYING", "响应播报开始", volume="70"),
            self._device("PLAYER_COMPLETE", "2026-11-12 10:00:07.300", "PLAYING→COMPLETE", "响应播报结束"),
            self._device("VAD_START", "2026-11-12 10:00:07.400", "SILENCE→VOICE", "检测到人声"),
            self._device("VAD_END", "2026-11-12 10:00:08.000", "VOICE→SILENCE", "人声结束"),
            self._device("INTENT", "2026-11-12 10:00:08.100", "ASR→INTENT", "设备收到命令", command="switch1"),
            self._device("ERROR", "2026-11-12 10:00:09.000", "READY→FAULT", "设备上报错误", code="E42"),
            self._device("RECOVERED", "2026-11-12 10:00:10.000", "FAULT→READY", "设备恢复就绪"),
            eventlog.device_unparsed_event("unknown native line", host_time="2026-11-12 10:00:11.000"),
            eventlog.EventRecord(event="TASK_END", time="2026-11-12 10:00:12.000", status="PASS",
                                 actual={"gate": {"gate_pass": True, "checks": {}}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            eventlog.write_event_records(records, run_dir / "events.jsonl")
            summary = eventlog.write_bundle(eventlog.read_events(run_dir / "events.jsonl"), run_dir)
            lines = tool_lines(run_dir)
            findings = eventlog.verify_bundle(run_dir)
        self.assertEqual(len(lines), len(records))
        text = "\n".join(lines)
        for code in (
            "BOOT_START", "READY", "SERIAL_UP", "WAKEUP", "ASR_START", "ASR_RESULT",
            "TTS_START", "TTS_END", "PLAYER_START", "PLAYER_COMPLETE", "VAD_START",
            "VAD_END", "INTENT", "ERROR", "RECOVERED", "UNPARSED",
        ):
            self.assertIn(f"[DEVICE][{code}]", text, code)
        for line in lines:
            self.assertRegex(line, LINE_RE)
            self.assertNotIn("\n", line)
        self.assertIn("state=FAULT→READY", text)
        self.assertIn("text_sha=abc123", text)
        self.assertEqual(summary["final_status"], "NEEDS_REVIEW")  # 无 ROUND_RESULT
        self.assertEqual([item for item in findings if item["level"] == "ERROR"], [])

    def test_coverage_lines_are_sorted_by_host_time(self) -> None:
        records = [
            self._device("READY", "2026-11-12 10:00:05.000", "BOOTING→READY", "就绪"),
            self._device("BOOT_START", "2026-11-12 10:00:01.000", "OFF→BOOTING", "启动"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            eventlog.write_event_records(records, run_dir / "events.jsonl")
            eventlog.write_bundle(eventlog.read_events(run_dir / "events.jsonl"), run_dir)
            lines = tool_lines(run_dir)
        self.assertIn("[DEVICE][BOOT_START]", lines[0])
        self.assertIn("[DEVICE][READY]", lines[1])


class CliTests(unittest.TestCase):
    def test_build_verify_and_schema(self) -> None:
        from lstest.scripts.lstest import main

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self.assertEqual(main([
                "eventlog", "build", "--legacy", str(GOLDEN), "--out-dir", str(out),
                "--markers", str(GOLDEN_MARKERS),
            ]), 0)
            self.assertEqual(main(["eventlog", "verify", "--run-dir", str(out)]), 0)
            schema_file = Path(tmp) / "schema.json"
            self.assertEqual(main(["eventlog", "schema", "--output", str(schema_file)]), 0)
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
            self.assertEqual(schema["event_schema"], eventlog.EVENT_SCHEMA)
            self.assertEqual(schema["bundle_version"], 2)
            self.assertIn("tool_log_rule", schema)


if __name__ == "__main__":
    unittest.main()
