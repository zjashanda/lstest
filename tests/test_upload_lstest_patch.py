from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import upload_lstest_patch as up


class SafeRelTest(unittest.TestCase):
    def test_rejects_parent_reference(self):
        with self.assertRaises(ValueError):
            up._safe_rel("../etc/passwd")

    def test_rejects_absolute(self):
        with self.assertRaises(ValueError):
            up._safe_rel("/etc/passwd")

    def test_normalizes_backslashes(self):
        self.assertEqual(up._safe_rel("profiles\\demo\\profile.json"), "profiles/demo/profile.json")


class BuildManifestTest(unittest.TestCase):
    def _file(self, root: Path, rel: str, text: str = "{}\n") -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_happy_path_carries_birth_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._file(root, "profiles/demo/profile.json", '{"x":1}\n')
            manifest, payloads = up.build_manifest(
                node="bszT14", task="T1", run="r1",
                base_commit="5820427", base_version="2.8.2",
                files=[target], checks={"preflight_pass": True},
                log_sample=["line-a"], log_sample_note="note", root=root,
            )
            self.assertEqual(manifest["base_commit"], "5820427")
            self.assertEqual(manifest["base_version"], "2.8.2")
            self.assertEqual(manifest["files"][0]["path"], "profiles/demo/profile.json")
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertEqual(len(payloads), 1)

    def test_rejects_bad_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._file(root, "a.json")
            for bad in ("", "zzz", "12345", "g" * 10):
                with self.assertRaises(ValueError):
                    up.build_manifest(
                        node="n", task="t", run="r", base_commit=bad, base_version="",
                        files=[target], checks={"x": 1}, log_sample=["a"],
                        log_sample_note="", root=root,
                    )

    def test_rejects_empty_checks_or_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._file(root, "a.json")
            with self.assertRaises(ValueError):
                up.build_manifest(
                    node="n", task="t", run="r", base_commit="5820427", base_version="",
                    files=[target], checks={}, log_sample=["a"], log_sample_note="", root=root,
                )
            with self.assertRaises(ValueError):
                up.build_manifest(
                    node="n", task="t", run="r", base_commit="5820427", base_version="",
                    files=[target], checks={"x": 1}, log_sample=[], log_sample_note="", root=root,
                )

    def test_rejects_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                up.build_manifest(
                    node="n", task="t", run="r", base_commit="5820427", base_version="",
                    files=[], checks={"x": 1}, log_sample=["a"], log_sample_note="", root=Path(tmp),
                )


class LogSampleTest(unittest.TestCase):
    def test_limits_to_fifty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.log"
            path.write_text("\n".join(f"line {i}" for i in range(60)), encoding="utf-8")
            with self.assertRaises(ValueError):
                up._read_log_sample(path, None)

    def test_strips_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.log"
            path.write_text("a\n\n  \nb\n", encoding="utf-8")
            self.assertEqual(up._read_log_sample(path, None), ["a", "b"])


class MainTest(unittest.TestCase):
    def test_dry_run_writes_manifest_without_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.json").write_text("{}\n", encoding="utf-8")
            (root / "s.log").write_text("line-a\nline-b\n", encoding="utf-8")
            out = root / "manifest.json"
            code = up.main([
                "--task", "T", "--run", "r", "--base-commit", "5820427",
                "--file", str(root / "p.json"),
                "--checks", '{"preflight_pass": true}',
                "--log-sample", str(root / "s.log"),
                "--root", str(root), "--out", str(out), "--dry-run",
            ])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["base_commit"], "5820427")
            self.assertEqual(len(payload["log_sample"]), 2)

    def test_missing_log_sample_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.json").write_text("{}\n", encoding="utf-8")
            code = up.main([
                "--task", "T", "--run", "r", "--base-commit", "5820427",
                "--file", str(root / "p.json"),
                "--checks", '{"preflight_pass": true}',
                "--root", str(root), "--dry-run",
            ])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
