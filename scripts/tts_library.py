"""1006 共享 TTS 音频库的轻量消费适配器（`ark.tts-spec.v1`）。

定位与边界
----------
* 本模块只做两件事：把测试用例的 TTS 需求转成统一规格；调用
  ``asset-tools tts-library`` 完成 lookup / fetch / publish。
* **不计算 cache key**（键与命中判定属于 ``asset-tools``）、**不实现缓存**、
  **不读写库根**、**不保存任何凭据**。
* 仅使用 Python 标准库；不 import ``lstts_core``，不联网。合成是
  ``tts-runtime`` 的职责，本模块缺失时调用方按「未命中」交给它。
* 退出码映射：0 成功；2 用法错误；3 **库不可达**（不等于资产不存在）；
  4 拒绝/越权；其他为通用库错误。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

SCHEMA = "ark.tts-spec.v1"
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_ENGINE = "lstts"
DEFAULT_NORMALIZATION_PROFILE = "wav-pcm16-44100-stereo"
DEFAULT_OUTPUT = {"format": "wav-pcm16", "sample_rate": 44100, "channels": 2}
SYNTHESIS_PARAM_KEYS = ("rate", "pitch", "style", "emotion", "speed")
OUTPUT_KEYS = ("format", "sample_rate", "channels")
ENGINE_DEFAULTS = {
    "lstts": {"model": "IndexTTS", "model_version": "unknown"},
    "melotts": {"model": "MeloTTS", "model_version": "unknown"},
    "mock": {"model": "mock", "model_version": "unknown"},
}

#: 共享库根（1006）。只读边界：任何写操作都必须由 asset-tools 执行。
LIBRARY_ROOT = Path("/srv/storage/ark/artifacts/audio/tts-library")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_SOURCE = 3
EXIT_FORBIDDEN = 4
DEFAULT_TIMEOUT_S = 120.0
_REF_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATH_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg")

Runner = Callable[[Sequence[str]], Any]


class LibraryError(RuntimeError):
    """调用共享库失败（默认映射退出码 1 / 5 / 6 等）。"""


class LibraryUnreachableError(LibraryError):
    """库不可达（退出码 3）。**绝不等于资产不存在**，禁止据此判定 miss。"""


class LibraryForbiddenError(LibraryError):
    """库拒绝操作（退出码 4）：越权、敏感内容、只读写入、路径越界。"""


class UsageError(LibraryError):
    """参数或任务目录不合法（退出码 2）。"""


# --------------------------------------------------------------------- 规格构造


def _require_non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _voice_block(speaker: Any, voice_ref_sha256: Optional[str]) -> dict[str, Any]:
    if speaker and voice_ref_sha256:
        raise ValueError("voice and voice_ref_sha256 are mutually exclusive")
    if not speaker and not voice_ref_sha256:
        raise ValueError("voice or voice_ref_sha256 is required")
    if voice_ref_sha256:
        ref = _require_non_empty(voice_ref_sha256, "voice_ref_sha256")
        if not _REF_SHA256.match(ref):
            raise ValueError("voice_ref_sha256 must be 64 lowercase hex characters")
        return {"speaker": None, "voice_ref_sha256": ref}
    name = _require_non_empty(speaker, "voice").strip()
    if "/" in name or "\\" in name or ":" in name or Path(name).suffix.lower() in _PATH_SUFFIXES:
        raise ValueError(
            "voice accepts a speaker name only; hash the reference audio yourself "
            "and pass voice_ref_sha256 instead of a path"
        )
    return {"speaker": name, "voice_ref_sha256": None}


def _engine_block(engine: Any) -> dict[str, Any]:
    if isinstance(engine, Mapping):
        extra = set(engine) - {"name", "engine", "model", "model_version"}
        if extra:
            raise ValueError(f"unknown engine keys: {', '.join(sorted(extra))}")
        name = engine.get("name") or engine.get("engine")
        model = engine.get("model")
        version = engine.get("model_version")
    else:
        name, model, version = engine, None, None
    name = _require_non_empty(name, "engine").strip()
    defaults = ENGINE_DEFAULTS.get(name, {"model": "unknown", "model_version": "unknown"})
    return {
        "name": name,
        "model": model or defaults["model"],
        "model_version": version or defaults["model_version"],
    }


def _synthesis_params_block(params: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if params is None:
        return {key: None for key in SYNTHESIS_PARAM_KEYS}
    if not isinstance(params, Mapping):
        raise ValueError("synthesis_params must be a mapping or None")
    extra = set(params) - set(SYNTHESIS_PARAM_KEYS)
    if extra:
        raise ValueError(f"unknown synthesis_params keys: {', '.join(sorted(extra))}")
    return {key: params.get(key) for key in SYNTHESIS_PARAM_KEYS}


def _output_block(output: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if output is None:
        return dict(DEFAULT_OUTPUT)
    if not isinstance(output, Mapping):
        raise ValueError("output must be a mapping or None")
    extra = set(output) - set(OUTPUT_KEYS)
    if extra:
        raise ValueError(f"unknown output keys: {', '.join(sorted(extra))}")
    block = {key: output.get(key, DEFAULT_OUTPUT[key]) for key in OUTPUT_KEYS}
    _require_non_empty(block["format"], "output.format")
    for key in ("sample_rate", "channels"):
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"output.{key} must be a positive integer")
    return block


def build_spec(
    text: str,
    *,
    voice: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    engine: Any = DEFAULT_ENGINE,
    synthesis_params: Optional[Mapping[str, Any]] = None,
    output: Optional[Mapping[str, Any]] = None,
    normalization_profile: str = DEFAULT_NORMALIZATION_PROFILE,
    voice_ref_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """构造 `ark.tts-spec.v1` 规格（确定性；不做文本规范化，交给 asset-tools）。

    `voice` 是音色名；需要参考音频时传 `voice_ref_sha256`（64 位小写十六进制），
    两者互斥。`engine` 可传字符串或 ``{"name","model","model_version"}`` 映射。
    """

    raw_text = _require_non_empty(text, "text")
    return {
        "schema": SCHEMA,
        "text": raw_text,
        "language": _require_non_empty(language, "language"),
        "voice": _voice_block(voice, voice_ref_sha256),
        "engine": _engine_block(engine),
        "synthesis_params": _synthesis_params_block(synthesis_params),
        "output": _output_block(output),
        "normalization_profile": _require_non_empty(
            normalization_profile, "normalization_profile"
        ),
    }


REQUIREMENT_KEYS = {
    "text",
    "voice",
    "language",
    "engine",
    "synthesis_params",
    "params",
    "output",
    "normalization_profile",
    "voice_ref_sha256",
}


def requirement_to_spec(requirement: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把测试用例需求映射成规格，返回 ``(spec, warnings)``。

    接受 ``text`` / ``voice`` / ``language`` / ``engine`` / ``params``
    （或 ``synthesis_params``）/ ``output`` / ``normalization_profile`` /
    ``voice_ref_sha256``；``voice`` 也可以是
    ``{"speaker": ..., "voice_ref_sha256": ...}``。未知键被忽略并写入 warnings。
    """

    if not isinstance(requirement, Mapping):
        raise ValueError("requirement must be a mapping")
    if "params" in requirement and "synthesis_params" in requirement:
        raise ValueError("use only one of params / synthesis_params")

    warnings = [f"unknown requirement key ignored: {key}" for key in sorted(
        set(requirement) - REQUIREMENT_KEYS
    )]

    voice = requirement.get("voice")
    voice_ref_sha256 = requirement.get("voice_ref_sha256")
    if isinstance(voice, Mapping):
        extra = set(voice) - {"speaker", "voice_ref_sha256"}
        warnings.extend(f"unknown voice key ignored: {key}" for key in sorted(extra))
        voice_ref_sha256 = voice.get("voice_ref_sha256", voice_ref_sha256)
        voice = voice.get("speaker")

    spec = build_spec(
        requirement.get("text"),
        voice=voice,
        language=requirement.get("language", DEFAULT_LANGUAGE),
        engine=requirement.get("engine", DEFAULT_ENGINE),
        synthesis_params=requirement.get("synthesis_params", requirement.get("params")),
        output=requirement.get("output"),
        normalization_profile=requirement.get(
            "normalization_profile", DEFAULT_NORMALIZATION_PROFILE
        ),
        voice_ref_sha256=voice_ref_sha256,
    )
    return spec, warnings


# --------------------------------------------------------------------- 库调用


def _default_runner(argv: Sequence[str]) -> Any:
    """默认 runner：调用 asset-tools；PATH 由 subprocess 自行解析。"""

    return subprocess.run(
        list(argv), capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT_S
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _manifest_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    manifest = payload.get("manifest")
    return manifest if isinstance(manifest, Mapping) else {}


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _detail(payload: Any, stderr: str, limit: int = 600) -> str:
    message = ""
    if isinstance(payload, Mapping):
        for key in ("error", "message", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                message = value
                break
    if not message:
        message = stderr
    return " ".join(message.split())[:limit]


def _normalize_lookup(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _manifest_of(payload)
    asset_id = payload.get("asset_id") or manifest.get("asset_id")
    hit = _coerce_bool(payload.get("hit"))
    if hit is None:
        hit = _coerce_bool(payload.get("found"))
    if hit is None:
        hit = bool(asset_id)
    cache_key = payload.get("cache_key") or manifest.get("cache_key")
    quality = payload.get("quality") or manifest.get("quality")
    status = payload.get("asset_status") or manifest.get("status")
    if not status:
        candidate = payload.get("status")
        status = candidate if candidate in {"active", "stale", "superseded"} else None
    return {
        "hit": bool(hit),
        "asset_id": asset_id,
        "cache_key": cache_key,
        "quality": quality,
        "status": status or ("active" if hit else "missing"),
        "manifest": manifest or None,
    }


class TtsLibraryAdapter:
    """`asset-tools tts-library` 的薄封装：只转发规格，不实现缓存。"""

    def __init__(
        self,
        asset_tools_bin: Any = "asset-tools",
        task_dir: Any = None,
        *,
        runner: Optional[Runner] = None,
    ) -> None:
        if task_dir is None:
            raise UsageError("task_dir is required for tts-library operations")
        self.asset_tools_bin = asset_tools_bin
        self.task_dir = Path(task_dir).expanduser()
        if not self.task_dir.is_dir():
            raise UsageError(f"task_dir does not exist or is not a directory: {self.task_dir}")
        task_resolved = self.task_dir.resolve()
        if _is_within(task_resolved, LIBRARY_ROOT.resolve()):
            raise LibraryForbiddenError(
                "task_dir must not be inside the shared library root; use a task directory"
            )
        self.runner: Runner = runner or _default_runner

    # -- 内部工具 ---------------------------------------------------

    def _command(self, action: str, args: Sequence[str]) -> list[str]:
        if isinstance(self.asset_tools_bin, (list, tuple)):
            base = [str(item) for item in self.asset_tools_bin]
        else:
            base = [str(self.asset_tools_bin)]
        return [*base, "tts-library", action, *[str(item) for item in args], "--json"]

    @contextmanager
    def _spec_file(self, spec: Mapping[str, Any]) -> Iterator[Path]:
        directory = self.task_dir / "work" / "tts-library"
        directory.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix="spec-", suffix=".json", delete=False
        )
        path = Path(handle.name)
        try:
            with handle:
                json.dump(spec, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            yield path
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _execute(self, action: str, args: Sequence[str]) -> dict[str, Any]:
        argv = self._command(action, args)
        try:
            completed = self.runner(argv)
        except subprocess.TimeoutExpired as error:
            raise LibraryError(f"asset-tools tts-library {action} 超时: {error}") from error
        except FileNotFoundError as error:
            raise LibraryError(f"asset-tools 不可用（{self.asset_tools_bin}）: {error}") from error
        except OSError as error:
            raise LibraryError(f"无法执行 asset-tools（{self.asset_tools_bin}）: {error}") from error

        returncode = int(getattr(completed, "returncode", 1))
        stdout = _as_text(getattr(completed, "stdout", ""))
        stderr = _as_text(getattr(completed, "stderr", ""))

        payload: Any = None
        if stdout.strip():
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = None

        detail = _detail(payload, stderr)
        suffix = f" {detail}" if detail else ""

        if returncode == EXIT_OK:
            if not isinstance(payload, dict):
                raise LibraryError(
                    f"asset-tools tts-library {action} 退出 0 但未返回 JSON 对象" + suffix
                )
            if payload.get("ok") is False:
                raise LibraryError(f"asset-tools tts-library {action} 报告失败" + suffix)
            return payload
        if returncode == EXIT_USAGE:
            raise UsageError(f"用法错误（exit 2）：asset-tools tts-library {action}" + suffix)
        if returncode == EXIT_NO_SOURCE:
            raise LibraryUnreachableError(
                f"asset-tools tts-library {action} 退出 3：库不可达（不等于不存在，"
                "禁止据此判定资产缺失）" + suffix
            )
        if returncode == EXIT_FORBIDDEN:
            raise LibraryForbiddenError(
                f"asset-tools tts-library {action} 退出 4（FORBIDDEN，拒绝操作）" + suffix
            )
        raise LibraryError(
            f"asset-tools tts-library {action} 失败（exit {returncode}）" + suffix
        )

    def _resolve_dest(self, dest: Any) -> Path:
        candidate = Path(dest) if dest is not None else Path("assets/tts/original")
        if not candidate.is_absolute():
            candidate = self.task_dir / candidate
        resolved = candidate.resolve()
        task_resolved = self.task_dir.resolve()
        if _is_within(resolved, LIBRARY_ROOT.resolve()):
            raise LibraryForbiddenError(
                "refusing to fetch into the shared library root; choose a task directory"
            )
        if not _is_within(resolved, task_resolved):
            raise UsageError(f"fetch destination must stay inside task_dir: {resolved}")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _resolve_staging(self, staging_dir: Any) -> Path:
        if staging_dir is None:
            raise UsageError("staging_dir is required for publish")
        staging = Path(staging_dir)
        if not staging.is_absolute():
            staging = self.task_dir / staging
        resolved = staging.resolve()
        task_resolved = self.task_dir.resolve()
        if _is_within(resolved, LIBRARY_ROOT.resolve()):
            raise LibraryForbiddenError("publish staging must not be the shared library root")
        if not _is_within(resolved, task_resolved):
            raise UsageError(f"publish staging must stay inside task_dir: {resolved}")
        if not resolved.is_dir():
            raise UsageError(f"publish staging is not a directory: {resolved}")
        return resolved

    def _locate_audio(
        self,
        payload: Mapping[str, Any],
        dest: Path,
        asset_id: Optional[str],
        manifest: Mapping[str, Any],
    ) -> str:
        for key in ("audio_path", "path", "dest_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = dest / candidate
                if candidate.is_file():
                    return str(candidate.resolve())

        audio = payload.get("audio")
        audio = audio if isinstance(audio, Mapping) else manifest.get("audio")
        if isinstance(audio, Mapping):
            filename = audio.get("filename")
            if isinstance(filename, str) and filename.strip():
                base = dest / asset_id if asset_id else dest
                candidate = base / filename
                if candidate.is_file():
                    return str(candidate.resolve())

        search_root = dest / asset_id if asset_id else dest
        if search_root.is_dir():
            for candidate in sorted(search_root.rglob("*.wav")):
                return str(candidate.resolve())
        for candidate in sorted(dest.rglob("*.wav")):
            return str(candidate.resolve())

        raise LibraryError(
            "fetch 报告成功，但任务目录内找不到音频文件（请检查 asset-tools 输出）"
        )

    # -- 公开 API ---------------------------------------------------

    def lookup(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        """查库。命中返回 hit=True；未命中返回 hit=False。退出码 3 一律视为不可达。"""

        with self._spec_file(spec) as path:
            payload = self._execute("lookup", ["--spec", path])
        return _normalize_lookup(payload)

    def fetch(self, spec: Mapping[str, Any], dest: Any = None) -> dict[str, Any]:
        """复制命中资产到任务目录（默认 ``<task>/assets/tts/original``）。"""

        destination = self._resolve_dest(dest)
        with self._spec_file(spec) as path:
            payload = self._execute(
                "fetch",
                [
                    "--spec", path,
                    "--dest", destination,
                    "--task-dir", self.task_dir.resolve(),
                ],
            )
        result = _normalize_lookup(payload)
        result["hit"] = True
        result["audio_path"] = self._locate_audio(
            payload, destination, result.get("asset_id"), _manifest_of(payload)
        )
        result["dest"] = str(destination)
        return result

    def publish(self, staging_dir: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
        """发布暂存产物；暂存目录必须在任务目录内。"""

        staging = self._resolve_staging(staging_dir)
        with self._spec_file(spec) as path:
            payload = self._execute(
                "publish",
                [
                    "--staging", staging,
                    "--task-dir", self.task_dir.resolve(),
                    "--spec", path,
                ],
            )
        return payload

    def resolve(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        """先查库、命中才取件；未命中返回 hit=False 交给 tts-runtime 合成。"""

        found = self.lookup(spec)
        if not found.get("hit"):
            return {
                "hit": False,
                "asset_id": None,
                "cache_key": found.get("cache_key"),
                "audio_path": None,
                "quality": None,
                "status": "miss",
            }
        fetched = self.fetch(spec)
        return {
            "hit": True,
            "asset_id": fetched.get("asset_id"),
            "cache_key": fetched.get("cache_key"),
            "audio_path": fetched.get("audio_path"),
            "quality": fetched.get("quality"),
            "status": fetched.get("status"),
        }
