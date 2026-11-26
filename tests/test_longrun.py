from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lstest.scripts import longrun


class LongRunEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.cmd = [sys.executable, "-c", "print('ok')"]

    def tearDown(self):
        self._tmp.cleanup()

    def _start(self, **kwargs):
        params = dict(
            name="t", work_dir=self.work, command=self.cmd,
            rounds=2, duration_s=0.0, infinite=False, start=0,
            milestone_every=1, min_free_gb=0.0, round_timeout_s=0.0,
            interval_s=0.0, issue="", multica=None,
        )
        params.update(kwargs)
        return longrun.run_loop(**params)

    def test_default_rounds_is_one(self):
        self.assertEqual(longrun.DEFAULTS["rounds"], 1)

    def test_bounded_run_completes(self):
        self._start(rounds=3)
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed"], 3)
        self.assertEqual(state["failed"], 0)

    def test_round_logs_are_written(self):
        self._start(rounds=2)
        logs = sorted((self.work / "rounds").glob("t_*.log"))
        self.assertEqual(len(logs), 2)
        self.assertIn("ok", logs[0].read_text(encoding="utf-8"))

    def test_resume_continues_from_checkpoint(self):
        self._start(rounds=2)
        self._start(rounds=4)
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["completed"], 4)
        logs = sorted((self.work / "rounds").glob("t_*.log"))
        self.assertEqual(len(logs), 4)

    def test_stop_file_halts_infinite_run(self):
        (self.work / "STOP_t").write_text("stop", encoding="utf-8")
        self._start(rounds=None, infinite=True)
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["status"], "stopped")

    def test_failed_round_is_counted(self):
        self._start(rounds=1, command=[sys.executable, "-c", "raise SystemExit(3)"])
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["failed"], 1)
        self.assertEqual(state["last_exit"], 3)

    def test_milestone_rows_written(self):
        self._start(rounds=2, milestone_every=1)
        lines = longrun.milestones_path(self.work, "t").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["round"], 1)

    def test_round_placeholder_substituted(self):
        script = "import sys;print('round=' + sys.argv[1])"
        self._start(rounds=2, command=[sys.executable, "-c", script, "{round}"])
        text = (self.work / "rounds" / "t_00002.log").read_text(encoding="utf-8")
        self.assertIn("round=2", text)

    def test_disk_guard_blocks(self):
        self._start(rounds=5, min_free_gb=10 ** 9)
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["status"], "blocked")
        self.assertIn("磁盘", state["last_error"])

    def test_duration_limit_stops(self):
        self._start(rounds=None, infinite=True, duration_s=0.001)
        state = longrun.read_state(longrun.state_path(self.work, "t"))
        self.assertEqual(state["status"], "completed")
        self.assertIn("时长", state["last_error"])

    def test_parse_duration(self):
        self.assertEqual(longrun.parse_duration("30m"), 1800)
        self.assertEqual(longrun.parse_duration("4h"), 14400)
        self.assertEqual(longrun.parse_duration("90s"), 90)


class LaunchHiddenTests(unittest.TestCase):
    def test_detach_returns_record(self):
        from lstest.scripts import launch_hidden

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            record = launch_hidden.launch(
                [sys.executable, "-c", "print('hi')"],
                name="probe", log_dir=work / "logs", detach=True,
            )
            self.assertTrue(record["pid"])
            self.assertTrue(record["detached"])
            evidence = work / "logs" / launch_hidden.EVIDENCE_NAME
            self.assertTrue(evidence.is_file())
            rows = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(rows[-1]["name"], "probe")
            # 等子进程写完并退出，避免临时目录被占用
            log = work / "logs" / "probe.log"
            for _ in range(100):
                try:
                    if log.is_file() and "hi" in log.read_text(encoding="utf-8"):
                        break
                except OSError:
                    pass
                time.sleep(0.05)

    def test_timeout_kills_child(self):
        from lstest.scripts import launch_hidden

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            record = launch_hidden.launch(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                name="slow", log_dir=work / "logs", timeout_s=1.0, verify_window=False,
            )
            self.assertTrue(record.get("timeout_hit"))
            self.assertLess(record["duration_s"], 10)

    def test_process_tree_window_check_runs(self):
        from lstest.scripts import launch_hidden

        script = ("import subprocess,sys,time;"
                  "subprocess.run([sys.executable,'-c','import time;time.sleep(1)']);"
                  "time.sleep(0.5)")
        with tempfile.TemporaryDirectory() as tmp:
            record = launch_hidden.launch(
                [sys.executable, "-c", script],
                name="tree", log_dir=Path(tmp) / "logs", timeout_s=20,
            )
            self.assertTrue(record.get("windows_checked_tree"))
            self.assertEqual(record.get("window_count"), 0)

    def test_child_pids_returns_list(self):
        from lstest.scripts import launch_hidden

        self.assertIsInstance(launch_hidden._child_pids(0), list)


if __name__ == "__main__":
    unittest.main()
