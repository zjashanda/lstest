from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path

from lstest.scripts.core import CaseResult, ConnectionSpec, StopSupervisor, TaskArtifacts
from lstest.scripts.observations import RawTag, ToolJudgement, judge_command, judge_online, judge_wakeup
from lstest.scripts.playback import CaptureBackend, PlayerEventNormalizer, PlaybackBackend
from lstest.scripts.profile import DeviceProfile, ProfileError
from lstest.scripts.serial_capture import SerialManager
from lstest.scripts.shell import ProfileCommandSender
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


if __name__ == "__main__":
    unittest.main()
