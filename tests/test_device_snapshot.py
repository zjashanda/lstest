"""`--device-snapshot` 解析与主入口接线测试，全部离线、不触碰硬件。"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

GENERATED_AT = "2026-11-10 20:47:30"
PROFILE_PORTS = [
    {"role": "ap", "port": "COM14", "baudrate": 921600, "capabilities": ["serial_log"]},
    {"role": "control", "port": "COM15", "baudrate": 115200},
]


def _load_cli_main():
    """加载主入口；TTS 是可选依赖，未安装时用桩模块隔离。"""
    stubs = {}
    for name in ("lstts_core", "lstts_core.audio", "lstts_core.client",
                 "lstts_core.config", "lstts_core.voices"):
        module = types.ModuleType(name)
        for attribute in ("inspect_wav", "extract_wav", "request_audio", "load_config", "resolve_voice"):
            setattr(module, attribute, lambda *args, **kwargs: None)
        stubs[name] = module
    with mock.patch.dict(sys.modules, stubs):
        from lstest.scripts.lstest import main
    for name in ("lstest.scripts.lstest", "lstest.scripts.audio_synthesis"):
        sys.modules.pop(name, None)
    return main


MAIN = _load_cli_main()


class DeviceSnapshotCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _profile(self, ports: list | None = None) -> Path:
        return self._write("profile.json", {
            "schema_version": 2, "profile_id": "snapshot-fixture",
            "ports": PROFILE_PORTS if ports is None else ports, "commands": [],
        })

    def _role_map(self, roles: dict, ok: bool = True) -> Path:
        return self._write("roles.json", {
            "schema": "ark.device-role-map.v1", "generated_at": GENERATED_AT,
            "ok": ok, "strict": True, "roles": roles,
        })

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = MAIN(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_role_map_resolved_and_pinned_override_profile_ports(self):
        profile = self._profile()
        doc = self._role_map({
            "ap": {"role": "ap", "state": "resolved", "port": "COM7", "reason": "only serial port on the selected board"},
            "control": {"role": "control", "state": "pinned", "port": "COM16", "reason": "pinned by spec"},
        })
        code, out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        ports = payload["connection"]["ports"]
        self.assertEqual([item["port"] for item in ports], ["COM7", "COM16"])
        self.assertEqual(ports[0]["capabilities"], ["serial_log"])
        self.assertEqual(ports[0]["baudrate"], 921600)
        block = payload["device_snapshot"]
        self.assertEqual(set(block), {"source", "schema", "generated_at", "mode", "roles"})
        self.assertEqual(block["source"], str(doc.resolve()))
        self.assertEqual(block["schema"], "ark.device-role-map.v1")
        self.assertEqual(block["generated_at"], GENERATED_AT)
        self.assertEqual(block["mode"], "role-map")
        self.assertEqual(block["roles"]["ap"], {"state": "resolved", "port": "COM7", "reason": "only serial port on the selected board"})
        self.assertEqual(block["roles"]["control"]["state"], "pinned")

    def test_raw_snapshot_matches_declared_labels_exactly(self):
        profile = self._profile([
            {"role": "ap", "port": "com6", "baudrate": 921600},
            {"role": "control", "port": "COM9", "baudrate": 115200},
        ])
        doc = self._write("ports.json", {
            "schema": "ark.device-io.v1", "generated_at": GENERATED_AT, "os": "windows",
            "ports": [
                {"device": "COM60", "interface": "SERIAL-A", "present": True},
                {"device": "COM6", "interface": "SERIAL-B", "present": True},
                {"device": "COM9", "interface": "SERIAL-C", "present": True},
            ],
        })
        code, out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual([item["port"] for item in payload["connection"]["ports"]], ["COM6", "COM9"])
        self.assertEqual(payload["device_snapshot"]["mode"], "snapshot")
        self.assertEqual(payload["device_snapshot"]["schema"], "ark.device-io.v1")
        self.assertEqual(payload["device_snapshot"]["roles"]["ap"]["state"], "resolved")

    def test_missing_role_exits_two_with_role_state_and_reason(self):
        profile = self._profile()
        doc = self._role_map({
            "ap": {"role": "ap", "state": "missing", "port": None, "reason": "no port matches spec"},
            "control": {"role": "control", "state": "resolved", "port": "COM16", "reason": "ok"},
        })
        code, out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("role 'ap'", err)
        self.assertIn("missing", err)
        self.assertIn("no port matches spec", err)
        self.assertEqual(json.loads(err)["status"], "BLOCKED")

    def test_ambiguous_raw_snapshot_exits_two(self):
        profile = self._profile([
            {"role": "ap", "port": "COM6", "baudrate": 921600},
            {"role": "control", "port": "COM9", "baudrate": 115200},
        ])
        doc = self._write("ports.json", {
            "schema": "ark.device-io.v1", "generated_at": GENERATED_AT,
            "ports": [
                {"device": "COM6", "interface": "SERIAL-A", "present": True},
                {"device": "COM6", "interface": "SERIAL-B", "present": True},
                {"device": "COM9", "interface": "SERIAL-C", "present": True},
            ],
        })
        code, _out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 2)
        self.assertIn("role 'ap'", err)
        self.assertIn("ambiguous", err)

    def test_role_map_conflict_exits_two(self):
        profile = self._profile()
        doc = self._role_map({
            "ap": {"role": "ap", "state": "conflict", "port": "COM7", "reason": "COM7 claimed by roles: ap, control"},
            "control": {"role": "control", "state": "resolved", "port": "COM16", "reason": "ok"},
        })
        code, _out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 2)
        self.assertIn("conflict", err)
        self.assertIn("claimed by roles", err)

    def test_role_map_ok_false_lists_unresolved_roles(self):
        profile = self._profile()
        doc = self._role_map({
            "ap": {"role": "ap", "state": "missing", "port": None, "reason": "absent"},
            "control": {"role": "control", "state": "resolved", "port": "COM16", "reason": "ok"},
        }, ok=False)
        code, _out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 2)
        self.assertIn("ok=false", err)
        self.assertIn("ap(missing)", err)

    def test_flag_with_empty_profile_ports_still_reports_block(self):
        profile = self._profile([])
        doc = self._role_map({})
        code, out, err = self._run(["preflight", "--profile", str(profile), "--device-snapshot", str(doc)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["connection"]["ports"], [])
        self.assertEqual(payload["device_snapshot"]["roles"], {})

    def test_without_flag_keeps_legacy_cli_port_behaviour(self):
        profile = self._profile()
        code, out, err = self._run([
            "preflight", "--profile", str(profile), "--port", "COM9:ap", "--baudrate", "115200",
        ])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertNotIn("device_snapshot", payload)
        self.assertEqual(payload["connection"]["ports"], [{
            "port": "COM9", "baudrate": 115200, "role": "ap",
            "capabilities": [], "required_for": [],
        }])

    def test_without_flag_empty_ports_profile_stays_empty(self):
        profile = self._profile([])
        code, out, err = self._run(["preflight", "--profile", str(profile)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["connection"]["ports"], [])
        self.assertNotIn("device_snapshot", payload)


if __name__ == "__main__":
    unittest.main()
