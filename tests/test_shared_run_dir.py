from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import longrun

REPO_ROOT = Path(__file__).resolve().parents[2]

CHILD = """
import json, os, sys
from pathlib import Path
from lstest.scripts import core

root = Path(sys.argv[1])
slug = sys.argv[2]
art = core.TaskArtifacts(root, slug)
arts_dir = art.run_dir
art.finalize("done", "test")
print(json.dumps({"run_dir": str(arts_dir)}))
"""


def run_child(root: Path, slug: str, env_run_dir: str | None) -> dict:
    env = dict(os.environ)
    env.pop(longrun.RUN_DIR_ENV, None)
    if env_run_dir:
        env[longrun.RUN_DIR_ENV] = env_run_dir
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), slug],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(f"child failed rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TaskRunDirTest(unittest.TestCase):
    def test_new_task_creates_new_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            first = longrun.task_run_dir(work, "r1")
            second = longrun.task_run_dir(work, "r1")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, Path(tmp) / "result")

    def test_reuse_returns_same_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            first = longrun.task_run_dir(work, "r1")
            again = longrun.task_run_dir(work, "r1", str(first))
            self.assertEqual(first, again)


class SharedRunDirTest(unittest.TestCase):
    def test_env_var_reuses_dir_without_wiping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "result"
            shared = root / "taskdir"
            shared.mkdir(parents=True)
            marker = shared / "rounds.jsonl"
            marker.write_text('{"round": 1}\n', encoding="utf-8")

            first = run_child(root, "t1", str(shared))
            second = run_child(root, "t1", str(shared))

            self.assertEqual(first["run_dir"], str(shared))
            self.assertEqual(second["run_dir"], str(shared))
            self.assertTrue(marker.is_file())
            self.assertIn("round", marker.read_text(encoding="utf-8"))
            self.assertEqual(len(list(root.iterdir())), 1, list(root.iterdir()))

    def test_without_env_creates_timestamp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "result"
            first = run_child(root, "t2", None)
            second = run_child(root, "t2", None)
            self.assertNotEqual(first["run_dir"], second["run_dir"])
            self.assertEqual(Path(first["run_dir"]).parent, root)
            self.assertRegex(Path(first["run_dir"]).name, r"^\d{12}(-\d{2})?$")
            self.assertEqual(len(list(root.iterdir())), 2, list(root.iterdir()))


class RoundEnvTest(unittest.TestCase):
    def test_run_round_exports_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            shared = Path(tmp) / "result" / "taskdir"
            shared.mkdir(parents=True)
            code = "import os; print(os.environ.get('LSTEST_RUN_DIR',''))"
            rc, log = longrun.run_round(
                [sys.executable, "-c", code], 1, work, "r1", 30, shared
            )
            self.assertEqual(rc, 0)
            self.assertIn(str(shared), log.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
