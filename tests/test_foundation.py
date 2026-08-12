from __future__ import annotations

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
from lstest.scripts.core import CaseResult, ConnectionSpec, StopSupervisor, TaskArtifacts
from lstest.scripts.observations import RawTag, ToolJudgement, judge_command, judge_online, judge_wakeup
from lstest.scripts.playback import CaptureBackend, PlayerEventNormalizer, PlaybackBackend
from lstest.scripts.profile import DeviceProfile, ProfileError
from lstest.scripts.runtime import ScenarioRuntime
from lstest.scripts.serial_capture import SerialManager
from lstest.scripts.shell import ProfileCommandSender, ProfileRecoveryStateMachine, ProfileRestartRecoveryMonitor
from lstest.scripts.timing import EventClock, correlate


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
            self.assertTrue((artifacts.run_dir / "task.log").read_text(encoding="utf-8"))
            self.assertTrue((artifacts.run_dir / "progress.json").is_file())
            self.assertFalse((artifacts.run_dir / "serial_merged.log").exists())

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
            progress = json.loads((artifacts.run_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["exception_counts"], expected)
            self.assertEqual(progress["exception_total"], 3)
            tool_log = (artifacts.run_dir / "tool_logs" / "exception_summary.log").read_text(encoding="utf-8")
            self.assertEqual(tool_log.count("[EXCEPTION_SUMMARY]"), 4)
            self.assertIn("\n\n", tool_log)

    def test_serial_manager_writes_independent_port_log(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "serial", [])
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
            task_log = (artifacts.run_dir / "task.log").read_text(encoding="utf-8")
            self.assertIn("keyword: da kai kong tiao", task_log)
            self.assertIn("command_status: PASS", task_log)
            self.assertIn("tool_status: PASS", task_log)
            self.assertIn("keyword: da kai kong tiao", output.getvalue())
            event = json.loads((artifacts.run_dir / "task_events.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["raw_tags"][0]["raw_value"], "da kai kong tiao")
            self.assertEqual(event["tool_judgement"]["tool_status"], "PASS")

    def test_tool_log_and_player_marker_keep_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "tool-output")
            path = artifacts.write_tool_log("player.log", "tone player evt: 1\n")
            self.assertIn("tone player evt: 1", path.read_text(encoding="utf-8"))
            normalized = PlayerEventNormalizer({"tone player evt: 1": "START"}).observe("tone player evt: 1")
            self.assertEqual(normalized["raw_marker"], "tone player evt: 1")
            self.assertEqual(normalized["player_state"], "START")
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
                self.assertEqual(summary["anomaly_counts"], {
                    "MULTIPLE_RECOGNITIONS_FOR_PLAYBACK": 1,
                    "UNEXPECTED_RECOGNITION": 1,
                })
                raw_log = (artifacts.run_dir / "tool_logs" / "recognition_raw.log").read_text(encoding="utf-8")
                self.assertIn('"keyword":"ni3 hao3 kong1 tiao2"', raw_log)
                task_log = (artifacts.run_dir / "task.log").read_text(encoding="utf-8")
                self.assertIn("keyword: ni3 hao3 kong1 tiao2", task_log)
                events = (artifacts.run_dir / "task_events.jsonl").read_text(encoding="utf-8")
                self.assertIn('"raw_value": "ni3 hao3 kong1 tiao2"', events)
            finally:
                if not artifacts.closed:
                    artifacts.finalize("FAIL", "test cleanup")

    def test_recognition_content_mismatch_fails_the_current_broadcast(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "recognition-content-contract")
            runtime = ScenarioRuntime(artifacts)
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

    def test_multiple_wake_results_for_one_playback_are_recorded_as_anomalies(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "multiple-wake-results")
            runtime = ScenarioRuntime(artifacts)
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
                evidence_refs=("serial_logs/serial_COM9_player.log#21",),
            )
            self.assertEqual(marker["player_state"], "START")
            lifecycle = [
                json.loads(line)
                for line in (artifacts.run_dir / "tool_logs" / "player_lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(lifecycle[-1]["lifecycle_status"], "DEVICE_START")
            self.assertEqual(lifecycle[-1]["broadcast_id"], broadcast["broadcast_id"])
            self.assertEqual(lifecycle[-1]["raw_marker"], "START")
            self.assertEqual(lifecycle[-1]["evidence_refs"], ["serial_logs/serial_COM9_player.log#21"])
            artifacts.finalize("PASS", "fixture complete")

    def test_player_failure_and_device_error_marker_fail_the_current_case(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "player-lifecycle-failure")
            runtime = ScenarioRuntime(artifacts)
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
                port="COM9", raw_line="[player] error",
            )
            self.assertEqual(marker["anomaly_code"], "PLAYER_DEVICE_MARKER_ERROR")

            runtime.record_case(CaseResult("host-failure", "PASS", reason="fixture"))
            runtime.record_case(CaseResult("device-failure", "PASS", reason="fixture"))
            summary = artifacts.finalize("FAIL", "fixture complete")
            self.assertEqual(summary["counts"], {"FAIL": 2})
            self.assertEqual(summary["anomaly_counts"]["PLAYER_PLAYBACK_FAILED"], 1)
            self.assertEqual(summary["anomaly_counts"]["PLAYER_DEVICE_MARKER_ERROR"], 1)
            lifecycle = (artifacts.run_dir / "tool_logs" / "player_lifecycle.jsonl").read_text(encoding="utf-8")
            self.assertIn('"lifecycle_status":"DEVICE_ERROR"', lifecycle)
            self.assertIn('"device_playback_status":"FAILED"', lifecycle)

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
            lifecycle = [
                json.loads(line)
                for line in (artifacts.run_dir / "tool_logs" / "player_lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            success = [row for row in lifecycle if row["broadcast_id"] == "broadcast-success"]
            timeout = [row for row in lifecycle if row["broadcast_id"] == "broadcast-timeout"]
            self.assertEqual([row["lifecycle_status"] for row in success], ["REQUESTED", "PROCESS_STARTED", "COMPLETED"])
            self.assertEqual(timeout[-1]["lifecycle_status"], "TIMEOUT")
            timeout_output = (artifacts.run_dir / "tool_logs" / "play_timeout.log").read_text(encoding="utf-8")
            self.assertIn("partial output", timeout_output)
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
            wake_log = (artifacts.run_dir / "tool_logs" / "wake_word_verification.log").read_text(encoding="utf-8")
            self.assertIn('"wake_word_id":"alternate-hey-tcl"', wake_log)

    def test_wake_word_order_violation_fails_without_advancing_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "wake-word-order")
            requirements = [
                {"wake_word_id": "first", "spoken_text": "第一个", "expected_raw": {"keyword": "first"}},
                {"wake_word_id": "second", "spoken_text": "第二个", "expected_raw": {"keyword": "second"}},
            ]
            runtime = ScenarioRuntime(artifacts, wake_words=requirements)
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
            self.assertEqual(artifacts.check_runtime(), "INITIALIZATION_RECOVERY_FAILED")
            recovery_log = (artifacts.run_dir / "tool_logs" / "initialization_recovery.jsonl").read_text(encoding="utf-8")
            self.assertIn('"status":"BLOCKED"', recovery_log)
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
            evidence_path, evidence_sha = runtime.write_evidence(
                events, unit=1, case_id="fixture-case", phase="response_wait", attempt=1,
            )
            self.assertTrue(evidence_path.is_file())
            self.assertTrue(evidence_sha)
            runtime.record_case(CaseResult("fixture-case", "PASS", reason="runtime"))
            summary = artifacts.finalize("PASS", "fixture complete")
            self.assertEqual(summary["status"], "PASS")
            self.assertIn("Wakeup keyword", evidence_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
