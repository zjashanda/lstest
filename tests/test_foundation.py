from __future__ import annotations

import csv
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lstest.scripts.audio_synthesis import load_text_manifest, safe_audio_filename
from lstest.scripts.core import CaseResult, CaseSpec, ConnectionSpec, StopSupervisor, TaskArtifacts
from lstest.scripts.observations import RawTag, ToolJudgement, judge_command, judge_online, judge_wakeup
from lstest.scripts.playback import CaptureBackend, PlayerEventNormalizer, PlaybackBackend
from lstest.scripts.profile import DeviceProfile, ProfileError
from lstest.scripts.runtime import ScenarioRuntime
from lstest.scripts.serial_capture import SerialManager
from lstest.scripts.shell import ProfileCommandSender, ProfileRecoveryStateMachine, ProfileRestartRecoveryMonitor
from lstest.scripts.smoke import collect_initialization_evidence
from lstest.scripts.timing import EventClock, correlate
from lstest.scripts.timeline import ToolLogValidator


PROFILE = {
    "schema_version": 1,
    "profile_id": "fixture-device",
    "ports": [{"role": "csk", "capabilities": ["offline_wake"]}],
    "commands": [{"command": "log.level 4", "roles": ["csk"], "success_patterns": [r"level=4"], "ack_strength": "strong"}],
}


class FakeSerial:
    def __init__(self, *_args, **_kwargs):
        self.closed = False
        self.lines = [b"algo ready\n"]

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


class RecoveryEvent:
    def __init__(self, port: str, role: str, cursor: int, line: str):
        self.port = port
        self.role = role
        self.cursor = cursor
        self.line = line


class RecoveryManager:
    """Deterministic serial-event manager for recovery state-machine tests."""

    def __init__(self, ports, event_batches=None, writes=None):
        self.ports = ports
        self.handles = {spec.port: object() for spec in ports}
        self.stop_event = __import__("threading").Event()
        self.events = []
        self.event_batches = list(event_batches or [])
        self.writes = writes if writes is not None else []
        self.wait_calls = []

    def snapshot(self, port=None):
        if port:
            return {port: max((event.cursor for event in self.events if event.port == port), default=0)}
        return {spec.port: max((event.cursor for event in self.events if event.port == spec.port), default=0) for spec in self.ports}

    def since(self, cursors):
        return [event for event in self.events if event.cursor > cursors.get(event.port, 0)]

    def wait_for(self, predicate, _timeout_s, *, cursors=None):
        self.wait_calls.append(dict(cursors or {}))
        if self.event_batches:
            self.events.extend(self.event_batches.pop(0))
        values = self.since(cursors or self.snapshot())
        return values if predicate(values) else values

    def write(self, command, port):
        self.writes.append((command, port))
        return ""


class FoundationTests(unittest.TestCase):

    def test_terminal_mirror_failure_keeps_tool_log_and_case_finalization(self):
        class BrokenTerminal:
            def write(self, _value):
                raise OSError("closed terminal pipe")

            def flush(self):
                raise OSError("closed terminal pipe")

        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "terminal-mirror-failure")
            artifacts.freeze_cases([CaseSpec("case-001", "fixture")])
            artifacts.enable_terminal_mirror(BrokenTerminal())
            artifacts.timeline_case_start("case-001")
            artifacts.record_case(CaseResult("case-001", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture")

            tool_log = (Path(summary["result_directory"]) / "tool.log").read_text(encoding="utf-8")
            self.assertIn("终端镜像不可用", tool_log)
            self.assertIn("[RESULT] 本轮=PASS", tool_log)
            self.assertEqual(artifacts.stop.reason, "")
            self.assertEqual(ToolLogValidator().validate_run(Path(summary["result_directory"])), [])
    @staticmethod
    def _freeze(artifacts, *case_ids: str) -> None:
        artifacts.freeze_cases([{"case_id": case_id, "scenario": "fixture"} for case_id in case_ids])

    def test_connection_spec_supports_zero_one_and_many_ports(self):
        self.assertEqual(ConnectionSpec.from_mapping({}).ports, ())
        spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM1", "baudrate": 115200, "role": "csk"}, "COM2:upper"], "baudrate": 921600})
        self.assertEqual([item.port for item in spec.ports], ["COM1", "COM2"])
        self.assertEqual(spec.ports[1].baudrate, 921600)

    def test_profile_rejects_unapproved_and_chained_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(__import__("json").dumps(PROFILE), encoding="utf-8")
            profile = DeviceProfile.load(path)
            self.assertEqual(profile.assert_command_allowed("log.level 4", "csk")["ack_strength"], "strong")
            with self.assertRaises(ProfileError):
                profile.assert_command_allowed("reboot", "csk")
            with self.assertRaises(ProfileError):
                profile.assert_command_allowed("log.level 4; reboot", "csk")

    def test_correlation_and_timing_are_explicit(self):
        result = correlate({"requestId": "a"}, {"requestId": "a"}, {"id": "req", "fields": ["requestId"]})
        self.assertTrue(result.correlation_valid)
        clock = EventClock()
        clock.mark("ONLINE_REQUEST_START", 10.0)
        clock.mark("ONLINE_RESULT_END", 12.25)
        self.assertEqual(clock.latency("ONLINE_REQUEST_START", "ONLINE_RESULT_END"), 2.25)
        self.assertEqual(clock.to_dict()["durations_ms"]["online_ms"], 2250)

    def test_stop_supervisor_checks_stop_file_and_disk_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = StopSupervisor(root / "STOP", disk_limit_bytes=0)
            self.assertIsNone(supervisor.check(root))
            (root / "STOP").write_text("stop\n", encoding="utf-8")
            self.assertEqual(supervisor.check(root), "STOP_FILE")

    def test_finalizer_records_closer_exception_before_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "closer")
            artifacts.register_closer("broken", lambda: (_ for _ in ()).throw(RuntimeError("fixture")))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["sticky_failures"][0]["code"], "TOOL_EXCEPTION")

    def test_artifacts_flush_without_merged_serial_log(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "fixture", ["case_id", "raw_status", "reviewed_status", "reason", "facts", "evidence"])
            artifacts.update_progress(completed=0, target=1)
            artifacts.record_case(CaseResult("one", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue((artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))
            self.assertTrue((artifacts.run_dir / "results.csv").is_file())
            self.assertTrue((artifacts.run_dir / "cases.csv").is_file())
            self.assertEqual({item.name for item in artifacts.run_dir.iterdir()}, {"serial_logs", "tool.log", "results.csv", "cases.csv"})

    def test_tool_log_uses_fixed_human_readable_node_template(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "human-log")
            artifacts.freeze_cases([{
                "case_id": "online-weather-01",
                "scenario": "online-interaction",
                "input_text": "今天的天气",
            }] * 5)
            artifacts.emit(
                "CASE_WINDOW_OPENED", case_id="online-weather-01", scenario="online-interaction",
                input_text="今天的天气", raw={"case_id": "online-weather-01"},
            )
            artifacts.emit(
                "BROADCAST_STARTED", case_id="online-weather-01", broadcast_id="broadcast-000001",
                playback_name="default_wake", input_text="小T小T", audio_file="小T小T.mp3",
                raw={"broadcast_id": "broadcast-000001"},
            )
            artifacts.emit(
                "WAKE_WORD_VERIFIED", case_id="online-weather-01", broadcast_id="broadcast-000001",
                status="PASS", raw={"raw_values": {"keyword": "xiao ti xiao ti"}},
            )
            artifacts.emit(
                "BROADCAST_STARTED", case_id="online-weather-01", broadcast_id="broadcast-000002",
                playback_name="command", input_text="今天的天气", audio_file="online_weather_01.mp3",
                raw={"broadcast_id": "broadcast-000002"},
            )
            artifacts.emit(
                "RECOGNITION_RAW_RECORDED", case_id="online-weather-01",
                broadcast_id="broadcast-000002", recognition_source="online",
                request_id="e659...", response_id="e659..._0", correlation_valid=True,
                recognition_latency_ms=653, raw={"text": "今天的天气"},
            )
            artifacts.emit(
                "PLAYER_LIFECYCLE", case_id="online-weather-01", broadcast_id="broadcast-000002",
                raw_marker="PLAYER_PLAYING", player_state="START", lifecycle_status="DEVICE_START",
            )
            artifacts.emit(
                "PLAYER_LIFECYCLE", case_id="online-weather-01", broadcast_id="broadcast-000002",
                raw_marker="PLAYER_COMPLETE", player_state="END", lifecycle_status="DEVICE_END",
            )
            artifacts.emit(
                "CASE_RESULT", case_id="online-weather-01", status="PASS",
                raw={"case": {"raw_status": "PASS", "facts": {
                    "wake": {"status": "PASS"}, "asr": {"status": "OBSERVED"},
                    "online": {"status": "PASS"}, "player": {"status": "COMPLETED"},
                }}},
            )
            artifacts.finalize("PASS", "fixture complete")
            log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("[CASE 1/5] START", log)
            self.assertIn("[ACTION] 播放=default_wake 文本=小T小T 文件=小T小T.mp3", log)
            self.assertIn("[DEVICE] WAKE: xiao ti xiao ti", log)
            self.assertIn("[RESULT] 判定: 唤醒=PASS", log)
            self.assertIn("[ONLINE] REQUEST_ID: e659...", log)
            self.assertIn("[ONLINE] RESPONSE_ID: e659..._0", log)
            self.assertIn("[ONLINE] ONLINE_ASR: 今天的天气", log)
            self.assertIn("[ONLINE] ASR_LATENCY_MS: 653", log)
            self.assertIn("[PLAYER] PLAYER: playing", log)
            self.assertIn("[PLAYER] PLAYER: stop", log)
            self.assertIn("[RESULT] 判定: 本轮=PASS", log)
            self.assertNotIn("time:", log)
            self.assertNotIn("level:", log)

    def test_cases_are_frozen_as_utf8_bom_before_playback_and_results_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "fixture.mp3"
            audio.write_bytes(b"fixture")
            artifacts = TaskArtifacts(root, "ledger")
            self._freeze(artifacts, "case-a", "case-b")
            with self.assertRaises(RuntimeError):
                artifacts.freeze_cases([])
            artifacts.record_case(CaseResult("case-a", "PASS", reason="fixture"))
            artifacts.record_case(CaseResult("case-b", "FAIL", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["planned"], 2)
            self.assertEqual(summary["completed"], 2)
            self.assertEqual(summary["valid"], 2)
            self.assertEqual(summary["success_rate"], 50.0)
            self.assertEqual(artifacts.cases_path.read_bytes()[:3], b"\xef\xbb\xbf")
            self.assertEqual(artifacts.results_path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_lazy_cases_are_declared_before_playback_without_materializing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = TaskArtifacts(root, "lazy-million")
            runtime = ScenarioRuntime(artifacts)
            runtime.begin_lazy_cases(
                scenario="online-mixed", planned_total=1_000_000, random_seed="fixture-seed",
                profile_version="1", profile_sha256="fixture",
            )
            self.assertTrue(artifacts.cases_frozen)
            self.assertEqual(artifacts._case_positions, {})
            audio = root / "fixture.mp3"
            audio.write_bytes(b"fixture")
            with self.assertRaisesRegex(RuntimeError, "声明当前用例"):
                runtime.play(audio, case_id="lazy-001")
            case = {"case_id": "lazy-001", "scenario": "online-mixed", "input_text": "天气", "audio_file": str(audio)}
            self.assertEqual(runtime.declare_case(case), 1)
            with self.assertRaisesRegex(RuntimeError, "already declared"):
                runtime.declare_case(case)
            rows = list(csv.DictReader(artifacts.cases_path.open("r", encoding="utf-8-sig", newline="")))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["random_seed"], "fixture-seed")
            self.assertEqual(rows[0]["case_order"], "1")
            self.assertEqual(artifacts._planned_case_count, 1_000_000)
            artifacts.record_case(CaseResult("lazy-001", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["planned"], 1_000_000)

    def test_formal_result_facts_survive_without_a_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "facts-handoff")
            self._freeze(artifacts, "registration")
            artifacts.timeline_case_start("registration")
            artifacts.record_case(CaseResult(
                "registration", "PASS", reason="fixture",
                facts={"registration_handoff": {"ledger_owner": "task", "alias": "fixture-alias"}},
            ))
            artifacts.finalize("PASS", "fixture complete")
            row = next(csv.DictReader(artifacts.results_path.open("r", encoding="utf-8-sig", newline="")))
            facts = json.loads(row["facts_json"])
            self.assertEqual(facts["registration_handoff"]["alias"], "fixture-alias")
            self.assertEqual(ToolLogValidator().validate_run(artifacts.run_dir), [])

    def test_validator_rejects_extra_result_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "extra-sidecar")
            self._freeze(artifacts, "case")
            artifacts.timeline_case_start("case")
            artifacts.record_case(CaseResult("case", "PASS", reason="fixture"))
            artifacts.finalize("PASS", "fixture complete")
            (artifacts.run_dir / "registration_handoff.json").write_text("{}", encoding="utf-8")
            errors = ToolLogValidator().validate_run(artifacts.run_dir)
            self.assertTrue(any("非标准运行产物" in item for item in errors))

    def test_results_reconcile_supports_large_auditable_facts_json(self):
        """A valid compat summary must not hit csv's small default field cap."""
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "large-facts")
            self._freeze(artifacts, "case-a")
            artifacts.record_case(CaseResult(
                "case-a",
                "PASS",
                reason="fixture",
                facts={"child_summary": "x" * (csv.field_size_limit() + 1)},
            ))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["completed"], 1)

    def test_playback_requires_cases_to_be_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "freeze-required")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            with self.assertRaisesRegex(RuntimeError, "冻结 cases.csv"):
                runtime.play(audio, case_id="case")
            artifacts.finalize("BLOCKED", "fixture complete")

    def test_user_stop_keeps_four_artifacts_and_aborted_results_row(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "user-stop")
            self._freeze(artifacts, "done", "interrupted")
            artifacts.record_case(CaseResult("done", "PASS", reason="fixture"))
            artifacts.stop.request("USER_STOP")
            artifacts.record_case(CaseResult("interrupted", "ABORTED", reason="USER_STOP"))
            summary = artifacts.finalize("STOPPED", "USER_STOP")
            self.assertEqual(summary["status"], "STOPPED")
            self.assertEqual({item.name for item in artifacts.run_dir.iterdir()}, {"serial_logs", "tool.log", "results.csv", "cases.csv"})
            rows = list(__import__("csv").DictReader(artifacts.results_path.open(encoding="utf-8-sig")))
            self.assertEqual([row["final_status"] for row in rows], ["PASS", "ABORTED"])
            self.assertIn("USER_STOP", (artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))

    def test_exception_counts_accumulate_across_rounds_and_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "exception-summary")
            artifacts.record_case(CaseResult("round-1", "PASS", reason="fixture"))
            artifacts.record_case(CaseResult("round-2", "FAIL", reason="fixture"))
            artifacts.record_case(CaseResult("round-3", "BLOCKED", reason="fixture"))
            artifacts.record_case(CaseResult("round-4", "FAIL", reason="fixture"))
            artifacts.update_progress(completed=4, target=4)
            summary = artifacts.finalize("FAIL", "fixture complete")

            expected = {"BLOCKED": 1, "FAIL": 2}
            self.assertEqual(summary["exception_counts"], expected)
            self.assertEqual(summary["exception_total"], 3)
            tool_log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertEqual(tool_log.count("[SUMMARY]"), 4)
            self.assertIn("\n\n", tool_log)

    def test_serial_manager_writes_independent_port_log(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "serial", [])
            self._freeze(artifacts)
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM1", "baudrate": 115200, "role": "csk"}]})
            manager = SerialManager(spec.ports, artifacts, factory=FakeSerial)
            manager.start()
            time.sleep(0.05)
            manager.stop()
            artifacts.finalize("PASS", "fixture complete")
            log = artifacts.run_dir / "serial_logs" / "serial_COM1_csk.log"
            self.assertIn("algo ready", log.read_text(encoding="utf-8"))
            self.assertFalse((artifacts.run_dir / "serial_logs" / "serial_COM1_csk.bin").exists())

    def test_serial_since_single_port_does_not_consume_other_ports(self):
        class PortSerial(FakeSerial):
            def __init__(self, port, *_args, **_kwargs):
                self.closed = False
                self.lines = [f"{port} ready\n".encode()]

        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "serial-filter")
            self._freeze(artifacts)
            spec = ConnectionSpec.from_mapping({"ports": [
                {"port": "COM1", "baudrate": 115200, "role": "csk"},
                {"port": "COM2", "baudrate": 115200, "role": "upper"},
            ]})
            manager = SerialManager(spec.ports, artifacts, factory=PortSerial)
            manager.start()
            time.sleep(0.05)
            events = manager.since({"COM1": 0})
            manager.stop()
            artifacts.finalize("PASS", "fixture complete")
            self.assertTrue(events)
            self.assertEqual({item.port for item in events}, {"COM1"})

    def test_serial_role_inference_and_bounded_reconnect(self):
        class FlakySerial:
            created = 0

            def __init__(self, *_args, **_kwargs):
                type(self).created += 1
                self.closed = False
                self.lines = [RuntimeError("disconnect")] if self.created == 1 else [b"upper ready\n"]

            def readline(self):
                if self.lines:
                    item = self.lines.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                time.sleep(0.01)
                return b""

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "serial-reconnect")
            self._freeze(artifacts)
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM1", "baudrate": 115200}]})
            manager = SerialManager(spec.ports, artifacts, factory=FlakySerial)
            manager.start()
            time.sleep(0.35)
            inferred = manager.infer_roles(lambda text: ("upper" if "upper" in text else None, 1.0))
            manager.stop()
            artifacts.finalize("PASS", "fixture complete")
            self.assertGreaterEqual(manager.reconnect_attempts.get("COM1", 0), 1)
            self.assertEqual(inferred["COM1"]["role"], "upper")

    def test_observation_mirrors_raw_values_and_tool_judgement(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "observation", ["case_id", "raw_status", "reviewed_status", "reason", "facts", "evidence"])
            output = StringIO()
            with redirect_stdout(output):
                artifacts.emit_observation(
                    "COMMAND",
                    [
                        RawTag("keyword", "da kai kong tiao", port="COM11", role="csk", cursor=12, evidence="serial_logs/serial_COM11_csk.log#12"),
                        RawTag("intent", "da kai kong tiao", port="COM11", role="csk", cursor=12, evidence="serial_logs/serial_COM11_csk.log#12"),
                    ],
                    normalized={"command_status": "PASS"},
                    judgement=ToolJudgement(
                        "PASS", "EXACT_MATCH", expected={"keyword": "da kai kong tiao", "intent": "da kai kong tiao"},
                        actual={"keyword": "da kai kong tiao", "intent": "da kai kong tiao"}, duration_ms=42,
                        evidence_refs=("serial_logs/serial_COM11_csk.log#12",),
                    ),
                )
            artifacts.finalize("PASS", "fixture complete")
            tool_log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("da kai kong tiao", tool_log)
            self.assertIn("[DEVICE] OFFLINE_ASR: da kai kong tiao", tool_log)
            self.assertIn("[RESULT] 判定: COMMAND=PASS", tool_log)
            self.assertIn("da kai kong tiao", output.getvalue())

    def test_tool_log_and_player_marker_keep_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "tool-output")
            with self.assertRaises(RuntimeError):
                artifacts.write_tool_log("player.log", "tone player evt: 1\n")
            normalized = PlayerEventNormalizer({"tone player evt: 1": "START"}).observe("tone player evt: 1", state_class="active")
            self.assertEqual(normalized["raw_marker"], "tone player evt: 1")
            self.assertEqual(normalized["player_state"], "active")
            artifacts.finalize("PASS", "fixture complete")

    def test_audio_text_manifest_accepts_csv_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "text.csv"
            csv_path.write_text("case_id,text,filename\none,你好空调,offline-case.mp3\n", encoding="utf-8-sig")
            jsonl_path = root / "text.jsonl"
            jsonl_path.write_text('{"case_id":"two","text":"今天天气怎么样"}\n', encoding="utf-8")
            self.assertEqual(load_text_manifest(csv_path)[0]["filename"], "offline-case.mp3")
            self.assertEqual(load_text_manifest(jsonl_path)[0]["case_id"], "two")
            self.assertEqual(safe_audio_filename("", fallback_text="你好空调")[-4:], ".mp3")

    def test_tts_cli_accepts_utf8_text_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "utterance.txt"
            source.write_text("你好空调\n", encoding="utf-8")
            args = __import__("lstest.scripts.lstest", fromlist=["parser"]).parser().parse_args([
                "tts", "--text-file", str(source), "--output", str(Path(directory) / "fixture.mp3"),
            ])
            self.assertEqual(args.text_file.read_text(encoding="utf-8").strip(), "你好空调")

    def test_recognition_raw_log_and_one_to_one_anomalies(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "recognition-contract")
            try:
                runtime = ScenarioRuntime(artifacts)
                self._freeze(artifacts, "offline-case", "other-case")
                audio = Path(directory) / "fixture.mp3"
                audio.write_bytes(b"fixture audio")
                runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
                broadcast = runtime.play(
                    audio,
                    case_id="offline-case",
                    expected_recognition={"keyword": "ni3 hao3 kong1 tiao2", "intent": "ni3 hao3 kong1 tiao2"},
                )
                broadcast_id = broadcast["broadcast_id"]

                first = runtime.record_recognition(
                    {"keyword": "ni3 hao3 kong1 tiao2", "intent": "ni3 hao3 kong1 tiao2"},
                    source="offline",
                    case_id="offline-case",
                    broadcast_id=broadcast_id,
                    normalized={"keyword_text": "你好空调", "intent_text": "你好空调"},
                    evidence_refs=("serial_logs/serial_COM1_csk.log#12",),
                )
                duplicate = runtime.record_recognition(
                    {"keyword": "ni3 hao3 kong1 tiao2", "intent": "ni3 hao3 kong1 tiao2"},
                    source="offline",
                    case_id="offline-case",
                    broadcast_id=broadcast_id,
                )
                orphan = runtime.record_recognition(
                    {"asr_text": "云端返回的原始文本"}, source="online", case_id="other-case",
                )
                self.assertEqual(first["status"], "PASS")
                self.assertEqual(duplicate["reason"], "MULTIPLE_RECOGNITIONS_FOR_PLAYBACK")
                self.assertEqual(orphan["reason"], "UNEXPECTED_RECOGNITION")

                runtime.record_case(CaseResult("offline-case", "PASS", reason="fixture"))
                runtime.record_case(CaseResult("other-case", "PASS", reason="fixture"))
                summary = artifacts.finalize("FAIL", "fixture complete")
                self.assertEqual(summary["counts"]["FAIL"], 2)
                self.assertEqual(summary["anomaly_counts"]["MULTIPLE_RECOGNITIONS_FOR_PLAYBACK"], 1)
                self.assertEqual(summary["anomaly_counts"]["UNEXPECTED_RECOGNITION"], 1)
                self.assertEqual(summary["anomaly_counts"]["ONLINE_CORRELATION_INVALID"], 1)
                tool_log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
                self.assertIn("ni3 hao3 kong1 tiao2", tool_log)
                self.assertIn("OFFLINE_ASR:", tool_log)
            finally:
                if not artifacts.closed:
                    artifacts.finalize("FAIL", "test cleanup")

    def test_recognition_content_mismatch_fails_the_current_broadcast(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "recognition-content-contract")
            runtime = ScenarioRuntime(artifacts)
            self._freeze(artifacts, "offline-case")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(
                audio,
                case_id="offline-case",
                expected_recognition={"keyword": "ni3 hao3 kong1 tiao2"},
            )
            observed = runtime.record_recognition(
                {"keyword": "da3 kai1 kong1 tiao2"},
                source="offline",
                case_id="offline-case",
                broadcast_id=broadcast["broadcast_id"],
            )
            self.assertEqual(observed["reason"], "RECOGNITION_RESULT_MISMATCH")
            runtime.record_case(CaseResult("offline-case", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"FAIL": 1})
            self.assertEqual(summary["anomaly_counts"]["RECOGNITION_RESULT_MISMATCH"], 1)

    def test_recognition_keeps_raw_value_and_accepts_only_explicit_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "semantic-match")
            self._freeze(artifacts, "offline-case")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(
                audio,
                case_id="offline-case",
                expected_recognition={"keyword": "二十五度"},
                accepted_raw_variants={"keyword": ["25度"]},
            )
            observed = runtime.record_recognition(
                {"keyword": "25度"}, source="offline", case_id="offline-case",
                broadcast_id=broadcast["broadcast_id"], normalized={"keyword_text": "二十五度"},
            )
            self.assertEqual(observed["status"], "PASS")
            self.assertEqual(observed["association"]["raw_exact_status"], "FAIL")
            self.assertEqual(observed["association"]["semantic_status"], "PASS")
            runtime.record_case(CaseResult("offline-case", "PASS", reason="fixture"))
            artifacts.finalize("PASS", "fixture complete")
            row = next(__import__("csv").DictReader(artifacts.results_path.open(encoding="utf-8-sig")))
            facts = __import__("json").loads(row["facts_json"])
            self.assertEqual(facts["asr"]["keyword"], "25度")
            self.assertEqual(facts["raw_exact_status"], "FAIL")
            self.assertEqual(facts["semantic_status"], "PASS")

    def test_repeated_recognition_is_aggregated_in_one_results_row(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "retry-aggregation")
            self._freeze(artifacts, "offline-case")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="offline-case", expected_recognition={"keyword": "right"})
            runtime.record_recognition({"keyword": "wrong"}, source="offline", case_id="offline-case", broadcast_id=broadcast["broadcast_id"])
            runtime.record_recognition({"keyword": "right"}, source="offline", case_id="offline-case", broadcast_id=broadcast["broadcast_id"])
            runtime.record_case(CaseResult("offline-case", "PASS", reason="fixture"))
            artifacts.finalize("FAIL", "fixture complete")
            rows = list(__import__("csv").DictReader(artifacts.results_path.open(encoding="utf-8-sig")))
            self.assertEqual(len(rows), 1)
            self.assertEqual(__import__("json").loads(rows[0]["facts_json"])["asr"]["attempts"], 2)

    def test_online_timing_requires_unique_request_in_current_case_window(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "online-timing")
            self._freeze(artifacts, "online-case")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="online-case", expected_recognition={"requestId": "req-1", "asr_text": "天气"})
            runtime.record_online_request({"requestId": "req-1", "body": "raw-request"}, case_id="online-case")
            observed = runtime.record_recognition(
                {"requestId": "req-1", "responseId": "resp-1", "asr_text": "天气"},
                source="online", case_id="online-case", broadcast_id=broadcast["broadcast_id"],
            )
            self.assertTrue(observed["online_correlation"]["valid"])
            runtime.record_case(CaseResult("online-case", "PASS", reason="fixture"))
            artifacts.finalize("PASS", "fixture complete")
            row = next(__import__("csv").DictReader(artifacts.results_path.open(encoding="utf-8-sig")))
            facts = __import__("json").loads(row["facts_json"])
            self.assertEqual(facts["online"]["request_id"], "req-1")
            self.assertEqual(facts["online"]["response_id"], "resp-1")
            self.assertTrue(facts["correlation"]["valid"])
            self.assertGreaterEqual(facts["timing"]["recognition_latency_ms"], 0)

    def test_online_response_without_recorded_request_leaves_latency_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "online-unpaired")
            self._freeze(artifacts, "online-case")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="online-case", expected_recognition={"requestId": "req-1", "asr_text": "天气"})
            observed = runtime.record_recognition(
                {"requestId": "req-1", "responseId": "resp-1", "asr_text": "天气"},
                source="online", case_id="online-case", broadcast_id=broadcast["broadcast_id"],
            )
            self.assertFalse(observed["online_correlation"]["valid"])
            runtime.record_case(CaseResult("online-case", "PASS", reason="fixture"))
            artifacts.finalize("FAIL", "fixture complete")
            row = next(__import__("csv").DictReader(artifacts.results_path.open(encoding="utf-8-sig")))
            facts = __import__("json").loads(row["facts_json"])
            self.assertEqual(facts["timing"]["recognition_latency_ms"], "")
            self.assertFalse(facts["correlation"]["valid"])

    def test_late_result_after_case_close_is_not_consumed_by_next_case(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "late-result")
            self._freeze(artifacts, "old-case", "new-case")
            runtime = ScenarioRuntime(artifacts)
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="old-case", expected_recognition={"keyword": "old"})
            runtime.record_case(CaseResult("old-case", "PASS", reason="fixture"))
            late = runtime.record_recognition(
                {"keyword": "old"}, source="offline", case_id="old-case", broadcast_id=broadcast["broadcast_id"],
            )
            self.assertEqual(late["reason"], "LATE_RESULT_AFTER_CASE_CLOSE")
            runtime.record_case(CaseResult("new-case", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["counts"]["FAIL"], 1)
            self.assertIn("LATE_RESULT_AFTER_CASE_CLOSE", (artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))

    def test_health_policy_records_threshold_and_continues_with_adapter_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "health-policy")
            self._freeze(artifacts, "case")
            artifacts.configure_health_policy({
                "NO_WAKEUP": {"threshold": 2, "handling": "snapshot_and_session_recovery_continue", "stop": False},
            })
            calls = []
            runtime = ScenarioRuntime(artifacts, health_recovery=lambda category, detail: calls.append((category, detail)) or "ok")
            runtime.record_no_wakeup("case")
            runtime.record_no_wakeup("case")
            self.assertEqual(len(calls), 1)
            self.assertIsNone(artifacts.check_runtime())
            runtime.record_case(CaseResult("case", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "FAIL")
            self.assertIn("会话恢复回调已完成", (artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))

    def test_multiple_wake_results_for_one_playback_are_recorded_as_anomalies(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "multiple-wake-results")
            runtime = ScenarioRuntime(artifacts)
            self._freeze(artifacts, "wake-case")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            wake_word = {
                "wake_word_id": "fixture-wake",
                "spoken_text": "测试唤醒词",
                "expected_raw": {"keyword": "fixture wake"},
            }
            broadcast = runtime.play(audio, case_id="wake-case", expected_recognition=wake_word["expected_raw"])
            first = runtime.record_wakeup(
                wake_word, {"keyword": "fixture wake"}, case_id="wake-case", broadcast_id=broadcast["broadcast_id"],
            )
            duplicate = runtime.record_wakeup(
                wake_word, {"keyword": "fixture wake"}, case_id="wake-case", broadcast_id=broadcast["broadcast_id"],
            )
            self.assertEqual(first["wakeup_status"], "PASS")
            self.assertIn("WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK", duplicate["anomaly_codes"])
            runtime.record_case(CaseResult("wake-case", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"FAIL": 1})
            self.assertEqual(summary["anomaly_counts"]["MULTIPLE_RECOGNITIONS_FOR_PLAYBACK"], 1)
            self.assertEqual(summary["anomaly_counts"]["WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK"], 1)

    def test_playback_uses_system_default_without_key_and_binds_when_key_is_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "playback-target")
            script = Path(directory) / "player.py"
            script.write_text("# fixture", encoding="utf-8")
            completed = SimpleNamespace(returncode=0, stdout="probe ok", stderr="")
            with patch("lstest.scripts.playback.subprocess.run", return_value=completed) as run:
                default_player = PlaybackBackend(artifacts, None, script)
                specified_player = PlaybackBackend(artifacts, "VID_1234&PID_5678:FIXTURE", script)
                self.assertTrue(default_player.probe())
                self.assertTrue(specified_player.probe())
            default_command = run.call_args_list[0].args[0]
            specified_command = run.call_args_list[1].args[0]
            self.assertNotIn("--device-key", default_command)
            self.assertIn("--device-key", specified_command)
            self.assertIn("VID_1234&PID_5678:FIXTURE", specified_command)
            self.assertEqual(default_player.target_mode, "system_default_render")
            self.assertEqual(specified_player.target_mode, "specified_device_key")
            artifacts.finalize("PASS", "fixture complete")

    def test_player_lifecycle_log_separates_host_completion_from_device_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "player-lifecycle")
            runtime = ScenarioRuntime(artifacts)
            self._freeze(artifacts, "player-case")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="player-case", expected_recognition={"keyword": "fixture"})
            marker = runtime.record_player_marker(
                "START",
                case_id="player-case",
                broadcast_id=broadcast["broadcast_id"],
                port="COM9",
                raw_line="[player] start",
                evidence_refs=("serial_logs/serial_COM9_player.log#21",), state_class="active",
            )
            self.assertEqual(marker["player_state"], "active")
            lifecycle = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("PLAYER: START", lifecycle)
            self.assertNotIn(broadcast["broadcast_id"], lifecycle)
            artifacts.finalize("PASS", "fixture complete")

    def test_player_failure_and_device_error_marker_fail_the_current_case(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "player-lifecycle-failure")
            runtime = ScenarioRuntime(artifacts)
            self._freeze(artifacts, "host-failure", "device-failure")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
            failed = runtime.play(audio, case_id="host-failure", expected_recognition={"keyword": "fixture"})
            self.assertEqual(failed["error"], "playback_failed")

            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="device-failure", expected_recognition={"keyword": "fixture"})
            self.assertNotEqual(failed["broadcast_id"], broadcast["broadcast_id"])
            marker = runtime.record_player_marker(
                "ERROR", case_id="device-failure", broadcast_id=broadcast["broadcast_id"],
                port="COM9", raw_line="[player] error", state_class="error",
            )
            self.assertEqual(marker["anomaly_code"], "PLAYER_DEVICE_MARKER_ERROR")

            runtime.record_case(CaseResult("host-failure", "PASS", reason="fixture"))
            runtime.record_case(CaseResult("device-failure", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"FAIL": 2})
            self.assertEqual(summary["anomaly_counts"]["PLAYER_PLAYBACK_FAILED"], 1)
            self.assertEqual(summary["anomaly_counts"]["PLAYER_DEVICE_MARKER_ERROR"], 1)
            lifecycle = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("PLAYER: ERROR", lifecycle)
            self.assertIn("PLAYER_DEVICE_MARKER_ERROR", lifecycle)

    def test_host_player_success_and_timeout_are_written_to_lifecycle_log(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "player-host-lifecycle")
            script = Path(directory) / "player.py"
            script.write_text("# fixture", encoding="utf-8")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            completed = SimpleNamespace(returncode=0, stdout="player finished", stderr="")
            with patch("lstest.scripts.playback.subprocess.run", return_value=completed):
                player = PlaybackBackend(artifacts, None, script)
                self.assertTrue(player.play(audio, case_id="success", broadcast_id="broadcast-success", timeout=5.0))
            with patch(
                "lstest.scripts.playback.subprocess.run",
                side_effect=__import__("subprocess").TimeoutExpired("player", 0.1, output="partial output"),
            ):
                self.assertFalse(player.play(audio, case_id="timeout", broadcast_id="broadcast-timeout", timeout=0.1))
            lifecycle = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertNotIn("partial output", lifecycle)
            artifacts.finalize("PASS", "fixture complete")

    def test_ordered_wake_word_verification_requires_current_raw_result(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "wake-word-contract")
            wake_words = [
                {
                    "wake_word_id": "default-xiao-ti",
                    "spoken_text": "小T小T",
                    "expected_raw": {"keyword": "xiao ti xiao ti"},
                },
                {
                    "wake_word_id": "alternate-hey-tcl",
                    "spoken_text": "嘿TCL",
                    "expected_raw": {"keyword": "hey tcl"},
                },
            ]
            runtime = ScenarioRuntime(artifacts, wake_words=wake_words)
            self._freeze(artifacts, "wake-default", "wake-alternate")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            self.assertEqual(runtime.current_wake_word()["wake_word_id"], "default-xiao-ti")
            first = runtime.play(audio, case_id="wake-default")
            correct = runtime.record_wakeup(
                wake_words[0],
                {"keyword": "xiao ti xiao ti"},
                case_id="wake-default",
                broadcast_id=first["broadcast_id"],
            )
            self.assertEqual(runtime.current_wake_word()["wake_word_id"], "alternate-hey-tcl")
            second = runtime.play(audio, case_id="wake-alternate")
            mismatched = runtime.record_wakeup(
                wake_words[1],
                {"keyword": "xiao ti xiao ti"},
                case_id="wake-alternate",
                broadcast_id=second["broadcast_id"],
            )
            self.assertEqual(correct["wakeup_status"], "PASS")
            self.assertEqual(mismatched["wakeup_status"], "FAIL")
            self.assertEqual(mismatched["wake_word_id"], "alternate-hey-tcl")
            self.assertEqual(runtime.current_wake_word()["wake_word_id"], "alternate-hey-tcl")
            runtime.record_case(CaseResult("wake-default", "PASS", reason="fixture"))
            runtime.record_case(CaseResult("wake-alternate", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"PASS": 1, "FAIL": 1})
            self.assertEqual(summary["anomaly_counts"]["WAKE_WORD_MISMATCH"], 1)
            wake_log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("alternate-hey-tcl", wake_log)

    def test_wake_word_order_violation_fails_without_advancing_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "wake-word-order")
            requirements = [
                {"wake_word_id": "first", "spoken_text": "第一个", "expected_raw": {"keyword": "first"}},
                {"wake_word_id": "second", "spoken_text": "第二个", "expected_raw": {"keyword": "second"}},
            ]
            runtime = ScenarioRuntime(artifacts, wake_words=requirements)
            self._freeze(artifacts, "wake-order")
            audio = Path(directory) / "fixture.mp3"
            audio.write_bytes(b"fixture audio")
            runtime.player.play = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            broadcast = runtime.play(audio, case_id="wake-order")
            result = runtime.record_wakeup(
                requirements[1], {"keyword": "second"}, case_id="wake-order", broadcast_id=broadcast["broadcast_id"],
            )
            self.assertEqual(result["wakeup_status"], "FAIL")
            self.assertEqual(runtime.current_wake_word()["wake_word_id"], "first")
            runtime.record_case(CaseResult("wake-order", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"FAIL": 1})
            self.assertEqual(summary["anomaly_counts"]["WAKE_WORD_ORDER_VIOLATION"], 1)

    def test_capture_never_falls_back_to_default_microphone(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "capture")
            result = CaptureBackend(artifacts, None).probe()
            self.assertEqual(result["status"], "UNAVAILABLE")
            artifacts.finalize("PASS", "fixture complete")

    def test_profile_observation_rules_are_project_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            payload = {**PROFILE, "observations": {"online": {"id_fields": ["requestId"]}}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            profile = DeviceProfile.load(path)
            self.assertEqual(profile.observation_rules("online")["id_fields"], ["requestId"])

    def test_profile_regex_extracts_only_framework_facts_and_main_log_hides_regex(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({
                **PROFILE,
                "observations": {
                    "offline_asr": {
                        "patterns": [{
                            "rule_id": "PROJECT_OFFLINE_ASR_01",
                            "pattern": r"keyword=(?P<keyword>[^\s]+)",
                            "roles": ["csk"],
                            "phases": ["offline_asr"],
                            "fact_map": {"keyword": "OFFLINE_ASR"},
                            "fixtures": {"positive": ["keyword=ni3"], "negative": ["intent=ni3"]},
                        }],
                    },
                },
            }), encoding="utf-8")
            profile = DeviceProfile.load(path)
            rule = profile.observation_rules("offline_asr")["patterns"]
            match = profile.match_and_extract(rule, "keyword=ni3", role="csk", phase="offline_asr")[0]
            self.assertTrue(match["matched"])
            self.assertEqual(match["facts"], {"OFFLINE_ASR": "ni3"})
            artifacts = TaskArtifacts(Path(directory), "profile-fact")
            artifacts.emit_fact("OFFLINE_ASR", match["facts"]["OFFLINE_ASR"], evidence="serial_logs/serial_COM9_csk.log#1")
            artifacts.emit_judgement("离线识别", "PASS")
            artifacts.finalize("PASS", "fixture complete")
            log = artifacts.tool_log_path.read_text(encoding="utf-8")
            self.assertIn("OFFLINE_ASR: ni3", log)
            self.assertNotIn("PROJECT_OFFLINE_ASR_01", log)
            self.assertNotIn("keyword=(?P", log)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["observations"]["offline_asr"]["patterns"][0]["fact_map"] = {"keyword": "PROJECT_CUSTOM"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ProfileError):
                DeviceProfile.load(path)

    def test_marker_scope_and_same_event_debounce_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": [{
                    "pattern": "algo ready", "ports": ["COM9"], "roles": ["csk"],
                    "phases": ["initialization"], "debounce_ms": 1000,
                    "fixtures": {"positive": ["algo ready"], "negative": ["algo not ready"]},
                }],
            }), encoding="utf-8")
            profile = DeviceProfile.load(path)
            rule = profile.initialization_patterns
            self.assertFalse(profile.match_any(rule, "algo ready", port="COM1", role="csk", phase="initialization", monotonic_seconds=1.0))
            self.assertTrue(profile.match_any(rule, "algo ready", port="COM9", role="csk", phase="initialization", monotonic_seconds=1.0))
            self.assertTrue(profile.match_any(rule, "algo ready", port="COM9", role="csk", phase="initialization", monotonic_seconds=1.0))
            self.assertFalse(profile.match_any(rule, "algo ready", port="COM9", role="csk", phase="initialization", monotonic_seconds=1.1))

    def test_command_sender_blocks_wrong_port_and_accepts_configured_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            payload = {
                **PROFILE,
                "ports": [{"role": "csk", "port": "COM9"}],
                "commands": [{"command": "log.level 4", "roles": ["csk"], "port": "COM9", "success_patterns": ["level=4"]}],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            artifacts = TaskArtifacts(Path(directory), "command")
            sender = ProfileCommandSender(DeviceProfile.load(path), artifacts, lambda _command, _port: "level=4")
            self.assertEqual(sender.send("log.level 4", role="csk", port="COM9")["status"], "PASS")
            self.assertEqual(sender.send("log.level 4", role="csk", port="COM1")["status"], "BLOCKED_PORT_POLICY")
            artifacts.finalize("PASS", "fixture complete")

    def test_recovery_waits_for_initialization_and_validates_serial_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["set level 4 ok"], "retries": 1, "timeout_s": 0.1,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-ack")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [
                [RecoveryEvent("COM9", "csk", 1, "algo ready")],
                [RecoveryEvent("COM9", "csk", 2, "set level 4 ok")],
            ])
            result = ProfileRecoveryStateMachine(DeviceProfile.load(profile_path), artifacts, manager).run(0.1)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(manager.writes, [("log.level 4", "COM9")])
            self.assertEqual(result["commands"][0]["validation_source"], "serial_ack")
            self.assertTrue(result["commands"][0]["evidence_refs"])
            artifacts.finalize("PASS", "fixture complete")

    def test_recovery_reuses_observed_initialization_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "recovery": {"stable_for_s": 0.01},
                "commands": [
                    {
                        "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                        "success_patterns": ["set log level 4 ok"], "timeout_s": 0.1,
                    },
                    {
                        "command": "player.level 4", "roles": ["csk"], "safe_init": True,
                        "success_patterns": ["set player level 4 ok"], "timeout_s": 0.1,
                    },
                ],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-observed-init")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            init_event = RecoveryEvent("COM9", "csk", 1, "algo ready")
            manager = RecoveryManager(spec.ports, [
                [RecoveryEvent("COM9", "csk", 2, "set log level 4 ok")],
                [RecoveryEvent("COM9", "csk", 3, "set player level 4 ok")],
            ])
            result = ProfileRecoveryStateMachine(DeviceProfile.load(profile_path), artifacts, manager).run(
                0.1,
                initialization_events=[init_event],
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["initialization_source"], "observed")
            self.assertEqual(manager.writes, [("log.level 4", "COM9"), ("player.level 4", "COM9")])
            self.assertEqual(len(manager.wait_calls), 2)
            self.assertEqual(result["initialization_evidence_refs"], ["serial_logs/serial_COM9_csk.log#1"])
            log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("初始化证据: 已观察到", log)
            self.assertIn("LOG_LEVEL: 4", log)
            self.assertIn("player.level 4", log)
            artifacts.finalize("PASS", "fixture complete")

    def test_recovery_does_not_send_command_for_invalid_observed_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["set level 4 ok"], "timeout_s": 0.1,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-invalid-observed-init")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports)
            result = ProfileRecoveryStateMachine(DeviceProfile.load(profile_path), artifacts, manager).run(
                0.1,
                initialization_events=[RecoveryEvent("COM9", "csk", 1, "not initialized")],
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(manager.writes, [])
            self.assertEqual(manager.wait_calls, [])
            artifacts.finalize("BLOCKED", "fixture complete")

    def test_smoke_initialization_collection_rejects_unrelated_serial_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
            }), encoding="utf-8")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [[RecoveryEvent("COM9", "csk", 1, "unrelated runtime log")]])
            evidence = collect_initialization_evidence(
                manager,
                DeviceProfile.load(profile_path),
                timeout_s=0.1,
                cursors={"COM9": 0},
            )
            self.assertEqual(evidence, [])

    def test_recovery_accepts_profile_evidence_only_when_no_ack_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["ack level 4"], "evidence_patterns": ["log level is 4"],
                    "timeout_s": 0.05, "evidence_timeout_s": 0.05,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-evidence")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [
                [RecoveryEvent("COM9", "csk", 1, "algo ready")],
                [],
                [RecoveryEvent("COM9", "csk", 2, "log level is 4")],
            ])
            result = ProfileRecoveryStateMachine(DeviceProfile.load(profile_path), artifacts, manager).run(0.1)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["commands"][0]["validation_source"], "serial_evidence")
            artifacts.finalize("PASS", "fixture complete")

    def test_recovery_retries_and_records_failure_when_ack_and_evidence_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["ack level 4"], "evidence_patterns": ["log level is 4"],
                    "retries": 1, "timeout_s": 0.01, "evidence_timeout_s": 0.01, "retry_delay_s": 0.01,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-retry")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [[RecoveryEvent("COM9", "csk", 1, "algo ready")], [], [], [], []])
            result = ProfileRecoveryStateMachine(DeviceProfile.load(profile_path), artifacts, manager).run(0.1)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(len(manager.writes), 2)
            self.assertEqual(result["commands"][0]["reason"], "no_ack_or_evidence")
            self.assertEqual(artifacts.anomaly_counts["INITIALIZATION_RECOVERY_FAILED"], 1)
            self.assertIsNone(artifacts.check_runtime())
            recovery_log = (artifacts.run_dir / "tool.log").read_text(encoding="utf-8")
            self.assertIn("判定: 初始化=", recovery_log)
            artifacts.finalize("FAIL", "fixture complete")

    def test_recovery_does_not_replace_a_mismatched_direct_ack_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["ack level 4"], "evidence_patterns": ["log level is 4"],
                    "retries": 0,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "recovery-direct-ack")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [[RecoveryEvent("COM9", "csk", 1, "algo ready")]])
            sender = ProfileCommandSender(
                DeviceProfile.load(profile_path), artifacts, lambda _command, _port: "ack level 3", manager,
            )
            result = sender.send("log.level 4", role="csk", port="COM9")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["reason"], "direct_ack_not_matched")
            self.assertEqual(manager.wait_calls, [])
            artifacts.finalize("FAIL", "fixture complete")

    def test_restart_monitor_waits_for_new_initialization_then_recovers_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "restart_patterns": ["device rebooted"],
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["level 4 ok"], "timeout_s": 0.05,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "restart-recovery")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [
                [RecoveryEvent("COM9", "csk", 2, "algo ready")],
                [RecoveryEvent("COM9", "csk", 3, "level 4 ok")],
            ])
            manager.events.append(RecoveryEvent("COM9", "csk", 1, "device rebooted"))
            monitor = ProfileRestartRecoveryMonitor(
                DeviceProfile.load(profile_path), artifacts, manager, initialization_timeout_s=0.1,
            )
            recovery = monitor.poll()
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0]["status"], "PASS")
            self.assertEqual(recovery[0]["recovery_reason"], "restart")
            self.assertEqual(manager.writes, [("log.level 4", "COM9")])
            monitor.stop()
            artifacts.finalize("PASS", "fixture complete")

    def test_restart_during_recovery_cancels_old_epoch_before_command_send(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({
                **PROFILE,
                "initialization_patterns": ["algo ready"],
                "restart_patterns": ["device rebooted"],
                "recovery": {"stable_for_s": 0.02},
                "commands": [{
                    "command": "log.level 4", "roles": ["csk"], "safe_init": True,
                    "success_patterns": ["level 4 ok"], "timeout_s": 0.05,
                }],
            }), encoding="utf-8")
            artifacts = TaskArtifacts(root, "restart-cancel")
            spec = ConnectionSpec.from_mapping({"ports": [{"port": "COM9", "baudrate": 115200, "role": "csk"}]})
            manager = RecoveryManager(spec.ports, [
                [RecoveryEvent("COM9", "csk", 2, "algo ready"), RecoveryEvent("COM9", "csk", 3, "device rebooted")],
                [RecoveryEvent("COM9", "csk", 4, "algo ready")],
                [RecoveryEvent("COM9", "csk", 5, "level 4 ok")],
            ])
            manager.events.append(RecoveryEvent("COM9", "csk", 1, "device rebooted"))
            monitor = ProfileRestartRecoveryMonitor(DeviceProfile.load(profile_path), artifacts, manager, initialization_timeout_s=0.1)
            recovery = monitor.poll()
            self.assertEqual(recovery[0]["status"], "CANCELLED")
            self.assertEqual(recovery[-1]["status"], "PASS")
            self.assertEqual(manager.writes, [("log.level 4", "COM9")])
            self.assertIn("判定: 重启恢复=CANCELLED", (artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))
            monitor.stop()
            artifacts.finalize("PASS", "fixture complete")

    def test_wakeup_command_and_online_status_are_explicit(self):
        wake, wake_judgement = judge_wakeup("xiao ti xiao ti", "xiao ti xiao ti", duration_ms=120)
        self.assertEqual(wake["wakeup_status"], "PASS")
        self.assertEqual(wake_judgement.actual["wake_keyword"], "xiao ti xiao ti")
        command, command_judgement = judge_command("da kai kong tiao", "wrong", "da kai kong tiao", "expected", duration_ms=80)
        self.assertEqual(command["command_status"], "FAIL")
        self.assertIn("intent", command_judgement.tool_reason)
        online, online_judgement = judge_online(
            correlation_valid=True,
            actual={"asr_text": "今天的天气"},
            expected={"asr_text": "今天的天气"},
            duration_ms=812,
        )
        self.assertEqual(online["online_status"], "PASS")
        self.assertEqual(online_judgement.duration_ms, 812)

    def test_scenario_runtime_owns_window_evidence_and_case_record(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "scenario-runtime", ["case_id", "raw_status", "reviewed_status", "reason", "facts", "evidence"])
            runtime = ScenarioRuntime(artifacts)
            event = SimpleNamespace(
                timestamp="2026-08-11 12:00:00.000",
                port="COM11",
                role="csk",
                line="Wakeup keyword=xiao ti xiao ti",
            )
            state = {"fetched": 0}

            def fetch():
                state["fetched"] += 1
                return [event], True

            events, markers, reason, complete = runtime.wait_observation_window(
                0.2,
                fetch=fetch,
                parse=lambda values: [values[-1].line] if values else [],
                stop_reason=lambda: None,
                predicate=lambda values: bool(values),
            )
            self.assertEqual(markers, [event.line])
            self.assertIsNone(reason)
            self.assertTrue(complete)
            self.assertGreaterEqual(state["fetched"], 1)
            artifacts.emit(
                "OBSERVATION_EVIDENCE",
                message="项目适配器引用连续串口证据，不再复制专项 evidence 文件。",
                case_id="fixture-case",
                evidence=["serial_logs/serial_COM11_csk.log#1"],
                raw={"events": [item.line for item in events]},
            )
            runtime.record_case(CaseResult("fixture-case", "PASS", reason="runtime"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "PASS")
            self.assertIn("serial_logs/serial_COM11_csk.log#1", (artifacts.run_dir / "tool.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
