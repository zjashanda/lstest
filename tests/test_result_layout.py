"""结果目录布局 v3 的回归测试。

覆盖：canonical 路径、旧 v1/v2/v2.5/cloud_run resolver、根目录审计、
迁移 dry-run/apply（SHA + mtime + 原路径 manifest）、README 阅读顺序、
validator 兼容读取（无 receipt、中断、旧布局）、CLI。

fixture ``legacy_layout_screenshot`` 对应"根目录散落多个 JSON + serial_logs +
cloud_run + recorder + milestones"的真实结果截图布局；数值为脱敏样例，
不含设备数据或凭据。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import eventlog, result_layout
from lstest.scripts.core import TaskArtifacts
from lstest.scripts.timeline import ToolLogValidator

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "legacy_flat_v1_smoke10.txt"
GOLDEN_META = FIXTURES / "legacy_flat_v1_smoke10.meta.json"
GOLDEN_MARKERS = FIXTURES / "legacy_flat_v1_smoke10.markers.json"
SCREENSHOT = FIXTURES / "legacy_layout_screenshot"


def build_bundle(target: Path, layout: str = "v3") -> dict:
    return eventlog.import_legacy_log(
        GOLDEN, target, meta=json.loads(GOLDEN_META.read_text(encoding="utf-8")),
        markers=GOLDEN_MARKERS, layout=layout,
    )


def add_screenshot_artifacts(run_dir: Path) -> None:
    """把截图里的非核心产物复制进结果目录（root JSON/日志/录音/里程碑）。"""
    for item in sorted(SCREENSHOT.rglob("*")):
        if item.is_dir():
            continue
        target = run_dir / item.relative_to(SCREENSHOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        item.relative_to(root).as_posix(): (item.stat().st_size, item.stat().st_mtime_ns)
        for item in root.rglob("*") if item.is_file()
    }


class LayoutDetectionTests(unittest.TestCase):
    def test_detect_v3_v2_v1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(result_layout.detect_layout(root / "missing"), "v3")
            self.assertEqual(result_layout.detect_layout(root), "v3")
            (root / "serial_logs").mkdir()
            (root / "tool.log").write_text("x", encoding="utf-8")
            self.assertEqual(result_layout.detect_layout(root), "v1")
            (root / "events.jsonl").write_text("", encoding="utf-8")
            self.assertEqual(result_layout.detect_layout(root), "v2")
            (root / "machine").mkdir()
            self.assertEqual(result_layout.detect_layout(root), "v2.5")
            (root / "reports").mkdir()
            self.assertEqual(result_layout.detect_layout(root), "v3")

    def test_canonical_paths_follow_authorized_layout(self) -> None:
        root = Path("/run")
        self.assertEqual(result_layout.path(root, "tool_log"), root / "events/tool.log")
        self.assertEqual(result_layout.path(root, "events"), root / "events/events.jsonl")
        self.assertEqual(result_layout.path(root, "rounds"), root / "events/rounds.jsonl")
        self.assertEqual(result_layout.path(root, "serial_dir"), root / "logs/serial")
        self.assertEqual(result_layout.path(root, "results"), root / "reports/results.csv")
        self.assertEqual(result_layout.path(root, "validation"), root / "reports/validation.json")
        self.assertEqual(result_layout.path(root, "raw_dir"), root / "evidence/raw")
        self.assertEqual(result_layout.path(root, "recorder_dir"), root / "artifacts/recorder")
        self.assertEqual(result_layout.path(root, "stage_index"), root / "milestones/stage-index.json")

    def test_root_allow_list_is_closed(self) -> None:
        self.assertEqual(
            set(result_layout.ROOT_FILES),
            {"README.md", "manifest.json", "summary.md", "receipt.json"},
        )
        self.assertEqual(
            set(result_layout.ROOT_DIRS),
            {"events", "logs", "reports", "artifacts", "evidence", "milestones"},
        )
        for name in ("preflight.json", "status.json", "validator.json", "events.jsonl",
                     "summary.json", "serial_logs", "cloud_run"):
            self.assertNotIn(name, result_layout.ROOT_ALLOWED)


class ResolverTests(unittest.TestCase):
    def test_resolve_reads_v3_v2_v1_and_transitional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root)
            self.assertEqual(result_layout.resolve(root, "events").relative_to(root).as_posix(),
                             "events/events.jsonl")
            self.assertEqual(result_layout.resolve(root, "tool_log").relative_to(root).as_posix(),
                             "events/tool.log")
            legacy = Path(tmp) / "legacy"
            build_bundle(legacy, layout="v2")
            self.assertEqual(result_layout.resolve(legacy, "events").name, "events.jsonl")
            self.assertEqual(result_layout.resolve(legacy, "tool_log").name, "tool.log")
            self.assertEqual(result_layout.resolve(legacy, "summary_json").name, "summary.json")
            v1 = Path(tmp) / "v1"
            (v1 / "serial_logs").mkdir(parents=True)
            (v1 / "tool.log").write_text("x", encoding="utf-8")
            self.assertEqual(result_layout.resolve(v1, "tool_log"), v1 / "tool.log")
            transitional = Path(tmp) / "t"
            (transitional / "machine").mkdir(parents=True)
            (transitional / "machine/summary.json").write_text("{}", encoding="utf-8")
            self.assertEqual(result_layout.resolve(transitional, "summary_json").relative_to(
                transitional).as_posix(), "machine/summary.json")

    def test_resolve_relative_maps_legacy_evidence_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs/serial").mkdir(parents=True)
            (root / "logs/serial/serial_COM5_ap.log").write_text("x", encoding="utf-8")
            (root / "evidence/raw").mkdir(parents=True)
            (root / "evidence/raw/e0001_X.json").write_text("{}", encoding="utf-8")
            resolved = result_layout.resolve_relative(root, "serial_logs/serial_COM5_ap.log#000123")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.relative_to(root).as_posix(), "logs/serial/serial_COM5_ap.log")
            raw = result_layout.resolve_relative(root, "raw/e0001_X.json")
            self.assertEqual(raw.relative_to(root).as_posix(), "evidence/raw/e0001_X.json")
            self.assertIsNone(result_layout.resolve_relative(root, "serial_logs/missing.log#1"))

    def test_normalize_reference_is_idempotent(self) -> None:
        self.assertEqual(result_layout.normalize_reference("tool.log"), "events/tool.log")
        self.assertEqual(result_layout.normalize_reference("events.jsonl"), "events/events.jsonl")
        self.assertEqual(result_layout.normalize_reference("serial_logs/a.log"),
                         "logs/serial/a.log")
        self.assertEqual(result_layout.normalize_reference("machine/status.json"),
                         "reports/status.json")
        self.assertEqual(result_layout.normalize_reference("reports/validation.json"),
                         "reports/validation.json")
        self.assertEqual(result_layout.normalize_reference("artifacts/recorder/a.wav"),
                         "artifacts/recorder/a.wav")


class AuditTests(unittest.TestCase):
    def test_legacy_flat_layout_reports_migratable_root_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root, layout="v2")
            add_screenshot_artifacts(root)
            report = result_layout.audit(root)
            self.assertEqual(report["layout"], "v2")
            self.assertFalse(report["root_ok"])
            names = {item["name"] for item in report["violations"]}
            for name in ("events.jsonl", "summary.json", "preflight.json", "status.json",
                         "validator.json", "evidence_summary.json", "finalize.json",
                         "rounds.jsonl", "serial_logs", "raw", "cloud_run"):
                self.assertIn(name, names)
            for item in report["violations"]:
                if item["reason"] == "LEGACY_READABLE":
                    self.assertTrue(item["suggestion"])

    def test_clean_v3_directory_passes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root)
            report = result_layout.audit(root)
            self.assertEqual(report["layout"], "v3")
            self.assertTrue(report["root_ok"], report["violations"])
            self.assertEqual(report["missing"], [])

    def test_unclassified_root_file_is_never_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "something.json").write_text("{}", encoding="utf-8")
            report = result_layout.audit(root)
            item = next(one for one in report["violations"] if one["name"] == "something.json")
            self.assertEqual(item["reason"], "UNCLASSIFIED")

    def test_missing_required_v3_artifacts_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events").mkdir()
            (root / "events/events.jsonl").write_text("", encoding="utf-8")
            report = result_layout.audit(root)
            self.assertIn("README.md", report["missing"])
            self.assertIn("manifest.json", report["missing"])


class MigrationTests(unittest.TestCase):
    def _legacy_run(self, tmp: str) -> Path:
        root = Path(tmp) / "run"
        build_bundle(root, layout="v2")
        add_screenshot_artifacts(root)
        return root

    def test_dry_run_plans_without_touching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            before = tree_state(root)
            plan = result_layout.migrate(root, apply=False)
            self.assertEqual(plan["layout_before"], "v2")
            self.assertEqual(plan["layout_after"], "v3")
            self.assertTrue(plan["moves"])
            self.assertEqual(tree_state(root), before)
            targets = {move["target"] for move in plan["moves"]}
            for expected in ("events/events.jsonl", "events/tool.log", "reports/summary.json",
                             "reports/preflight.json", "reports/status.json",
                             "reports/validation.json", "reports/evidence-summary.json",
                             "reports/finalize.json", "events/rounds.jsonl",
                             "logs/serial", "evidence/raw", "logs/legacy/cloud_run",
                             "artifacts/recorder"):
                self.assertIn(expected, targets)
            for move in plan["moves"]:
                if move["kind"] == "file":
                    self.assertEqual(len(move["sha256"]), 64)
                    self.assertGreater(move["bytes"], 0)
                else:
                    self.assertEqual(len(move["tree_sha256"]), 64)

    def test_apply_preserves_sha_and_mtime_and_writes_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            plan = result_layout.plan_migration(root)
            shas = {move["source"]: move.get("sha256") for move in plan["moves"]}
            result = result_layout.migrate(root, apply=True, task_id="golden")
            self.assertTrue(result["ok"], result["errors"])
            self.assertTrue(result["moves"])
            for move in result["moves"]:
                self.assertTrue((root / move["target"]).exists(), move["target"])
                self.assertFalse((root / move["source"]).exists(), move["source"])
                if move["kind"] == "file":
                    self.assertEqual(move["sha256"], shas[move["source"]])
                    self.assertEqual(
                        result_layout._sha256(root / move["target"]), move["sha256"]
                    )
                    self.assertEqual(move["mtime"], move["mtime_after"])
            migration = json.loads(
                (root / "reports/migration-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(migration["schema"], result_layout.MIGRATION_SCHEMA)
            self.assertEqual(migration["layout_before"], "v2")
            self.assertTrue(migration["moves"])
            for move in migration["moves"]:
                self.assertIn("source", move)
                self.assertIn("target", move)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], result_layout.MANIFEST_SCHEMA)
            self.assertIn("migration", manifest)
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("## 阅读顺序", readme)
            report = result_layout.audit(root)
            self.assertEqual(report["layout"], "v3")
            self.assertTrue(report["root_ok"], report["violations"])
            errors = [item for item in eventlog.verify_bundle(root) if item["level"] == "ERROR"]
            self.assertEqual(errors, [])

    def test_migration_keeps_legacy_evidence_chain_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            result_layout.migrate(root, apply=True)
            records = eventlog.read_events(result_layout.resolve_required(root, "events"))
            refs = [ref["path"] for record in records for ref in (record.evidence or [])]
            self.assertTrue(refs)
            for ref in refs:
                # 事件里的旧相对路径（serial_logs/...）必须仍可通过 resolver 找到证据。
                if ref.startswith("serial_logs/"):
                    self.assertIsNotNone(result_layout.resolve_relative(root, ref), ref)

    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            result_layout.migrate(root, apply=True)
            snapshot = tree_state(root)
            plan = result_layout.plan_migration(root)
            self.assertEqual(plan["moves"], [])
            self.assertEqual(plan["conflicts"], [])
            again = result_layout.migrate(root, apply=True)
            self.assertTrue(again["ok"])
            after = tree_state(root)
            self.assertEqual(set(after), set(snapshot))

    def test_collision_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            target = root / "reports/preflight.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"keep": true}', encoding="utf-8")
            plan = result_layout.plan_migration(root)
            conflicts = {item["source"] for item in plan["conflicts"]}
            self.assertIn("preflight.json", conflicts)
            self.assertFalse(plan["ok"])
            result = result_layout.migrate(root, apply=True)
            self.assertIn('{"keep": true}', target.read_text(encoding="utf-8"))
            self.assertTrue((root / "preflight.json").is_file())

    def test_unclassified_content_stays_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            (root / "notes.txt").write_text("人工备注", encoding="utf-8")
            plan = result_layout.plan_migration(root)
            skipped = {item["source"] for item in plan["skipped"]}
            self.assertIn("notes.txt", skipped)
            result_layout.migrate(root, apply=True)
            self.assertTrue((root / "notes.txt").is_file())

    def test_v3_rerun_after_migration_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._legacy_run(tmp)
            result_layout.migrate(root, apply=True)
            summary = eventlog.write_bundle(
                eventlog.read_events(result_layout.resolve_required(root, "events")), root,
            )
            self.assertEqual(summary["task_id"], "device-x-protocol-v2-smoke10-20261112-170155")
            self.assertTrue((root / "events/tool.log").is_file())


class ManifestTests(unittest.TestCase):
    def test_manifest_verifies_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root)
            target = result_layout.write_manifest(root, task_id="golden")
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], result_layout.MANIFEST_SCHEMA)
            self.assertEqual(payload["layout"], "v3")
            self.assertTrue(payload["entries"])
            self.assertEqual(result_layout.verify_manifest(root), [])
            (root / "events/tool.log").write_text("手工修改\n", encoding="utf-8")
            codes = {item["code"] for item in result_layout.verify_manifest(root)}
            self.assertIn("MANIFEST_SHA", codes)

    def test_manifest_records_directories_with_tree_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root)
            add_screenshot_artifacts(root)
            result_layout.write_manifest(root)
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            serial = next(item for item in payload["entries"] if item["key"] == "serial_dir")
            self.assertEqual(serial["kind"], "dir")
            self.assertEqual(len(serial["tree_sha256"]), 64)
            self.assertGreaterEqual(serial["files"], 1)


class ReadmeTests(unittest.TestCase):
    def test_reading_order_and_root_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = result_layout.write_readme(root, task_id="T-1")
            text = path.read_text(encoding="utf-8")
            self.assertTrue(path.is_file())
            order = [text.index(f"`{name}`") for name, _ in result_layout.READING_ORDER]
            self.assertEqual(order, sorted(order))
            self.assertLess(text.index("`summary.md`"), text.index("`events/tool.log`"))
            self.assertLess(text.index("`events/tool.log`"), text.index("`receipt.json`"))
            self.assertIn("根目录保留项", text)
            self.assertIn("README.md", text)
            self.assertIn("manifest.json", text)
            self.assertIn("旧布局", text)
            before = path.read_bytes()
            result_layout.write_readme(root, task_id="T-1")
            self.assertEqual(path.read_bytes(), before)


class ValidatorCompatibilityTests(unittest.TestCase):
    def test_v1_run_is_still_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "v1")
            artifacts.emit("TASK_STARTED", message="开始")
            artifacts.finalize("PASS", "v1")
            self.assertEqual(ToolLogValidator().validate_run(artifacts.run_dir), [])

    def test_v2_run_is_still_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "v2"
            build_bundle(root, layout="v2")
            add_screenshot_artifacts(root)
            self.assertEqual(ToolLogValidator().validate_run(root), [])

    def test_v3_run_is_valid_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = TaskArtifacts(Path(tmp), "v3", structured_events=True)
            artifacts.emit("TASK_STARTED", message="开始")
            artifacts.emit("TASK_END", message="结束", status="PASS",
                           actual={"gate": {"gate_pass": True, "checks": {}}})
            artifacts.finalize("PASS", "v3")
            self.assertFalse((artifacts.run_dir / "receipt.json").exists())
            self.assertEqual(ToolLogValidator().validate_run(artifacts.run_dir), [])

    def test_v3_stray_root_json_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root)
            (root / "status.json").write_text("{}", encoding="utf-8")
            errors = ToolLogValidator().validate_run(root)
            self.assertTrue(any("status.json" in item for item in errors), errors)

    def test_interrupted_run_without_task_end_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = eventlog.EventLogWriter(root, "interrupted")
            writer.emit("TASK_STARTED", message="开始")
            writer.emit("ROUND_RESULT", round=1, status="PASS",
                        actual={"attempts_total": 2, "first_success_attempt": 2})
            writer.close()
            summary = eventlog.write_bundle(eventlog.read_events(writer.events_path), root)
            self.assertIn(summary["final_status"], {"NEEDS_REVIEW", "INCOMPLETE"})
            errors = [item for item in eventlog.verify_bundle(root) if item["level"] == "ERROR"]
            self.assertEqual(errors, [])
            self.assertEqual(ToolLogValidator().validate_run(root), [])

    def test_cloud_run_directory_is_readable_but_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "v2"
            build_bundle(root, layout="v2")
            add_screenshot_artifacts(root)
            cloud = result_layout.resolve(root, "cloud_run")
            self.assertIsNotNone(cloud)
            self.assertTrue((cloud / "window.log").is_file())


class WriterLayoutTests(unittest.TestCase):
    def test_default_writer_uses_v3_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = build_bundle(root)
            self.assertTrue((root / "events/events.jsonl").is_file())
            self.assertTrue((root / "events/tool.log").is_file())
            self.assertTrue((root / "reports/summary.json").is_file())
            self.assertTrue((root / "summary.md").is_file())
            self.assertTrue((root / "README.md").is_file())
            self.assertTrue((root / "manifest.json").is_file())
            self.assertFalse((root / "events.jsonl").exists())
            self.assertFalse((root / "tool.log").exists())
            records = eventlog.read_events(root / "events/events.jsonl")
            raw_refs = [record.raw_ref["path"] for record in records if record.raw_ref]
            self.assertTrue(raw_refs)
            self.assertTrue(all(path.startswith("evidence/raw/") for path in raw_refs))
            self.assertEqual(summary["task_id"], "device-x-protocol-v2-smoke10-20261112-170155")

    def test_v2_layout_remains_available_for_old_readers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_bundle(root, layout="v2")
            for name in ("events.jsonl", "tool.log", "summary.json", "summary.md"):
                self.assertTrue((root / name).is_file(), name)
            self.assertFalse((root / "events").exists())
            self.assertFalse((root / "README.md").exists())

    def test_same_events_render_identical_tool_log_in_both_layouts(self) -> None:
        """布局只改路径，不改时序、判定和行格式。"""
        with tempfile.TemporaryDirectory() as tmp:
            v3 = Path(tmp) / "v3"
            v2 = Path(tmp) / "v2"
            build_bundle(v3)
            build_bundle(v2, layout="v2")
            v3_text = (v3 / "events/tool.log").read_text(encoding="utf-8")
            v2_text = (v2 / "tool.log").read_text(encoding="utf-8")
            self.assertEqual(v3_text.replace("evidence/raw/", "raw/"), v2_text)


class CliLayoutTests(unittest.TestCase):
    def test_cli_migrate_dry_run_and_apply(self) -> None:
        from lstest.scripts.lstest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            build_bundle(root, layout="v2")
            add_screenshot_artifacts(root)
            self.assertEqual(main(["result", "audit", "--run-dir", str(root)]), 1)
            self.assertEqual(main(["result", "migrate", "--run-dir", str(root)]), 0)
            self.assertTrue((root / "events.jsonl").is_file())
            self.assertEqual(main(["result", "migrate", "--run-dir", str(root), "--apply"]), 0)
            self.assertFalse((root / "events.jsonl").exists())
            self.assertEqual(main(["result", "audit", "--run-dir", str(root)]), 0)
            self.assertEqual(main(["eventlog", "verify", "--run-dir", str(root)]), 0)

    def test_cli_resolve_reports_missing_with_exit_3(self) -> None:
        from lstest.scripts.lstest import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(main(["result", "resolve", "--run-dir", str(root), "--name", "receipt"]), 3)

    def test_cli_result_schema_is_machine_readable(self) -> None:
        from lstest.scripts.lstest import main

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "layout.json"
            self.assertEqual(main(["result", "schema", "--output", str(target)]), 0)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "lstest.result-manifest.v1")
            self.assertIn("legacy_readable", payload)

    def test_schema_exposes_layout_contract(self) -> None:
        schema = eventlog.schema_document()["layout"]
        self.assertEqual(schema["layout"], "v3")
        self.assertIn("README.md", schema["root_allowed"]["files"])
        self.assertIn("events/tool.log", schema["canonical"].values())
        self.assertTrue(any("旧布局" in rule for rule in schema["rules"]))


if __name__ == "__main__":
    unittest.main()
