"""`ark.tts-spec.v1` 轻量消费适配器的离线测试（NOAH-66）。

不使用 lstts_core、不联网、不访问 1006；假 asset-tools 脚本与任务目录都在临时目录内。
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tts_library import (  # noqa: E402  (路径注入后再导入)
    DEFAULT_OUTPUT,
    LIBRARY_ROOT,
    LibraryError,
    LibraryForbiddenError,
    LibraryUnreachableError,
    TtsLibraryAdapter,
    UsageError,
    build_spec,
    requirement_to_spec,
)


def write_stub(
    directory: Path,
    payload: dict,
    *,
    exit_code: int = 0,
    create_file: bool = False,
    log_path: Path | None = None,
) -> Path:
    """写一个假 asset-tools：按需输出 JSON、创建音频、记录 action。"""

    body = {
        "payload": payload,
        "exit_code": exit_code,
        "create_file": create_file,
        "log_path": str(log_path) if log_path else None,
    }
    template = """#!{python}
import json
import sys
from pathlib import Path

CONFIG = json.loads({config!r})
args = sys.argv[1:]
if "--json" not in args:
    print(json.dumps({{"error": "missing --json"}}))
    sys.exit(9)
action = args[1] if len(args) > 1 else ""
if CONFIG["log_path"]:
    with open(CONFIG["log_path"], "a", encoding="utf-8") as handle:
        handle.write(action + "\\n")
payload = dict(CONFIG["payload"])
if CONFIG["create_file"]:
    def value(flag):
        return args[args.index(flag) + 1] if flag in args else None
    dest = value("--dest")
    asset_id = payload.get("asset_id") or "tts-unknown"
    target = Path(dest) / asset_id
    target.mkdir(parents=True, exist_ok=True)
    wav = target / "audio.wav"
    wav.write_bytes(b"RIFF0000WAVEfmt ")
    payload["audio_path"] = str(wav)
print(json.dumps(payload, ensure_ascii=False))
sys.exit(CONFIG["exit_code"])
""".format(python=sys.executable, config=json.dumps(body, ensure_ascii=False))
    path = directory / f"asset-tools-{len(list(directory.iterdir()))}.py"
    path.write_text(template, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class SpecConstructionTests(unittest.TestCase):
    def test_default_spec_is_deterministic_and_complete(self) -> None:
        first = build_spec("今天天气不错", voice="付小春")
        second = build_spec("今天天气不错", voice="付小春")
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "ark.tts-spec.v1")
        self.assertEqual(first["language"], "zh-CN")
        self.assertEqual(first["voice"], {"speaker": "付小春", "voice_ref_sha256": None})
        self.assertEqual(first["engine"]["name"], "lstts")
        self.assertEqual(first["engine"]["model"], "IndexTTS")
        self.assertEqual(first["synthesis_params"], {
            "rate": None, "pitch": None, "style": None, "emotion": None, "speed": None,
        })
        self.assertEqual(first["output"], DEFAULT_OUTPUT)
        self.assertEqual(first["normalization_profile"], "wav-pcm16-44100-stereo")

    def test_params_and_output_override(self) -> None:
        spec = build_spec(
            "hello",
            voice="小宁",
            language="en-US",
            engine={"name": "melotts", "model": "MeloTTS", "model_version": "1.0"},
            synthesis_params={"rate": 1.2, "emotion": "happy"},
            output={"format": "wav-pcm16", "sample_rate": 16000, "channels": 1},
        )
        self.assertEqual(spec["synthesis_params"]["rate"], 1.2)
        self.assertEqual(spec["synthesis_params"]["emotion"], "happy")
        self.assertIsNone(spec["synthesis_params"]["pitch"])
        self.assertEqual(spec["output"], {
            "format": "wav-pcm16", "sample_rate": 16000, "channels": 1,
        })
        self.assertEqual(spec["engine"]["model_version"], "1.0")

    def test_voice_ref_only(self) -> None:
        ref = "a" * 64
        spec = build_spec("hi", voice_ref_sha256=ref)
        self.assertEqual(spec["voice"], {"speaker": None, "voice_ref_sha256": ref})

    def test_rejections(self) -> None:
        cases = [
            {"text": ""},
            {"text": "   "},
            {"text": "x"},
            {"text": "x", "voice": "付小春", "voice_ref_sha256": "b" * 64},
            {"text": "x", "voice": "/tmp/ref.wav"},
            {"text": "x", "voice": "ref.wav"},
            {"text": "x", "voice": "付小春", "voice_ref_sha256": "not-a-sha"},
        ]
        for kwargs in cases:
            with self.subTest(case=kwargs):
                with self.assertRaises(ValueError):
                    build_spec(**kwargs)

    def test_rejects_unknown_param_and_output_keys(self) -> None:
        with self.assertRaises(ValueError):
            build_spec("x", voice="付小春", synthesis_params={"volume": 1})
        with self.assertRaises(ValueError):
            build_spec("x", voice="付小春", output={"codec": "pcm_s16le"})


class RequirementTests(unittest.TestCase):
    def test_requirement_warns_unknown_keys(self) -> None:
        spec, warnings = requirement_to_spec({
            "text": "你好",
            "voice": "付小春",
            "params": {"rate": 1.0},
            "case_id": "CASE-1",
            "note": "ignore me",
        })
        self.assertEqual(spec["synthesis_params"]["rate"], 1.0)
        self.assertIsNone(spec["synthesis_params"]["pitch"])
        joined = "\n".join(warnings)
        self.assertIn("case_id", joined)
        self.assertIn("note", joined)

    def test_requirement_accepts_voice_mapping(self) -> None:
        ref = "c" * 64
        spec, warnings = requirement_to_spec({
            "text": "hello", "voice": {"speaker": None, "voice_ref_sha256": ref},
        })
        self.assertEqual(warnings, [])
        self.assertEqual(spec["voice"]["voice_ref_sha256"], ref)

    def test_requirement_rejects_ambiguous_params(self) -> None:
        with self.assertRaises(ValueError):
            requirement_to_spec({
                "text": "x", "voice": "付小春",
                "params": {"rate": 1}, "synthesis_params": {"rate": 1},
            })


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.task = self.root / "NOAH-66"
        self.task.mkdir()
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.log = self.root / "calls.log"
        self.spec = build_spec("你好", voice="付小春")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def adapter(self, payload: dict, **stub_kwargs) -> TtsLibraryAdapter:
        stub = write_stub(self.stub_dir, payload, log_path=self.log, **stub_kwargs)
        return TtsLibraryAdapter(str(stub), self.task)

    def calls(self) -> list[str]:
        if not self.log.is_file():
            return []
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def test_lookup_hit(self) -> None:
        adapter = self.adapter({
            "hit": True,
            "asset_id": "tts-0123456789abcdef",
            "cache_key": "d" * 64,
            "quality": "formal",
            "status": "active",
        })
        result = adapter.lookup(self.spec)
        self.assertTrue(result["hit"])
        self.assertEqual(result["asset_id"], "tts-0123456789abcdef")
        self.assertEqual(result["cache_key"], "d" * 64)
        self.assertEqual(result["quality"], "formal")
        self.assertEqual(result["status"], "active")
        self.assertEqual(self.calls(), ["lookup"])

    def test_lookup_miss(self) -> None:
        adapter = self.adapter({"hit": False})
        result = adapter.lookup(self.spec)
        self.assertFalse(result["hit"])
        self.assertIsNone(result["asset_id"])
        self.assertEqual(result["status"], "missing")

    def test_exit_3_is_unreachable_not_absent(self) -> None:
        adapter = self.adapter({"unreachable": True, "error": "storage mount down"}, exit_code=3)
        with self.assertRaises(LibraryUnreachableError) as caught:
            adapter.lookup(self.spec)
        message = str(caught.exception)
        self.assertIn("不可达", message)
        self.assertIn("不等于不存在", message)

    def test_exit_4_is_forbidden(self) -> None:
        adapter = self.adapter({"error": "SENSITIVE_DENIED"}, exit_code=4)
        with self.assertRaises(LibraryForbiddenError):
            adapter.lookup(self.spec)

    def test_exit_2_is_usage_error(self) -> None:
        adapter = self.adapter({"error": "missing --task-dir"}, exit_code=2)
        with self.assertRaises(UsageError):
            adapter.lookup(self.spec)

    def test_exit_5_is_generic_library_error(self) -> None:
        adapter = self.adapter({"error": "network timeout"}, exit_code=5)
        with self.assertRaises(LibraryError) as caught:
            adapter.lookup(self.spec)
        self.assertNotIsInstance(caught.exception, LibraryUnreachableError)
        self.assertNotIsInstance(caught.exception, LibraryForbiddenError)

    def test_fetch_copies_into_task_assets(self) -> None:
        adapter = self.adapter({
            "hit": True,
            "asset_id": "tts-0123456789abcdef",
            "cache_key": "e" * 64,
            "quality": "formal",
            "status": "active",
        }, create_file=True)
        result = adapter.fetch(self.spec)
        expected_root = self.task / "assets" / "tts" / "original"
        path = Path(result["audio_path"])
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(expected_root))
        self.assertEqual(path.parent.name, "tts-0123456789abcdef")
        self.assertEqual(path.stat().st_nlink, 1)  # 复制，不是硬链接
        self.assertEqual(self.calls(), ["fetch"])

    def test_fetch_rejects_missing_task_dir(self) -> None:
        with self.assertRaises(UsageError):
            TtsLibraryAdapter("asset-tools", self.root / "missing")

    def test_fetch_rejects_destination_outside_task(self) -> None:
        adapter = self.adapter({"hit": True, "asset_id": "tts-x"}, create_file=True)
        with self.assertRaises(UsageError):
            adapter.fetch(self.spec, dest=self.root / "elsewhere")

    def test_fetch_rejects_library_root_destination(self) -> None:
        adapter = self.adapter({"hit": True, "asset_id": "tts-x"}, create_file=True)
        with self.assertRaises(LibraryForbiddenError):
            adapter.fetch(self.spec, dest=LIBRARY_ROOT / "tts-x")

    def test_resolve_miss_does_not_fetch(self) -> None:
        adapter = self.adapter({"hit": False})
        result = adapter.resolve(self.spec)
        self.assertEqual(result, {
            "hit": False,
            "asset_id": None,
            "cache_key": None,
            "audio_path": None,
            "quality": None,
            "status": "miss",
        })
        self.assertEqual(self.calls(), ["lookup"])

    def test_publish_rejects_staging_outside_task(self) -> None:
        adapter = self.adapter({"ok": True})
        outside = self.root / "staging"
        outside.mkdir()
        with self.assertRaises(UsageError):
            adapter.publish(outside, self.spec)

    def test_injected_runner_gets_json_flag_and_spec_file(self) -> None:
        seen: dict[str, object] = {}

        def runner(argv):
            spec_path = Path(argv[argv.index("--spec") + 1])
            seen["argv"] = list(argv)
            seen["spec"] = json.loads(spec_path.read_text(encoding="utf-8"))
            seen["existed"] = spec_path.is_file()
            return SimpleNamespace(returncode=0, stdout=json.dumps({"hit": False}), stderr="")

        adapter = TtsLibraryAdapter("asset-tools", self.task, runner=runner)
        adapter.lookup(self.spec)
        argv = seen["argv"]
        self.assertEqual(argv[:3], ["asset-tools", "tts-library", "lookup"])
        self.assertIn("--json", argv)
        self.assertNotIn("key", argv)  # 缓存键由 asset-tools 计算
        self.assertTrue(seen["existed"])
        self.assertEqual(seen["spec"], self.spec)
        self.assertFalse(Path(argv[argv.index("--spec") + 1]).exists())  # 临时规格已清理

    def test_module_has_no_key_computation_or_env_reads(self) -> None:
        source = (SCRIPTS / "tts_library.py").read_text(encoding="utf-8")
        self.assertNotIn("hashlib", source)
        self.assertNotIn("sha256(", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)
        self.assertNotIn("LSTTS_TTS_TOKEN", source)
        self.assertNotIn("os.link", source)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "tts_library_cli.py"), *args],
            capture_output=True, text=True, timeout=60, check=False,
        )

    def test_spec_without_lstts_core(self) -> None:
        completed = self.run_cli("spec", "--text", "你好", "--voice", "付小春")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        spec = json.loads(completed.stdout)
        self.assertEqual(spec["schema"], "ark.tts-spec.v1")
        self.assertEqual(spec["voice"]["speaker"], "付小春")

    def test_lookup_and_unreachable_exit_codes(self) -> None:
        task = self.root / "NOAH-66"
        task.mkdir()
        stub_dir = self.root / "bin"
        stub_dir.mkdir()
        spec_path = self.root / "spec.json"
        spec_path.write_text(
            json.dumps(build_spec("你好", voice="付小春"), ensure_ascii=False), encoding="utf-8"
        )

        hit_stub = write_stub(stub_dir, {
            "hit": True, "asset_id": "tts-0123456789abcdef", "cache_key": "f" * 64,
            "quality": "formal", "status": "active",
        })
        completed = self.run_cli(
            "lookup", "--spec", str(spec_path), "--task-dir", str(task),
            "--asset-tools-bin", str(hit_stub),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["hit"])

        down_stub = write_stub(stub_dir, {"unreachable": True}, exit_code=3)
        completed = self.run_cli(
            "lookup", "--spec", str(spec_path), "--task-dir", str(task),
            "--asset-tools-bin", str(down_stub),
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("不等于不存在", completed.stderr)


if __name__ == "__main__":
    unittest.main()
