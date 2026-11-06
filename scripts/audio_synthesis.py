"""IndexTTS synthesis with reproducible manifests and audio validation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from lstts_core.audio import inspect_wav as _core_inspect_wav
    from lstts_core.client import extract_wav as _core_extract_wav
    from lstts_core.client import request_audio as _core_request_audio
    from lstts_core.config import load_config as _core_load_config
    from lstts_core.voices import resolve_voice as _core_resolve_voice
except ImportError as error:
    raise ImportError(
        'lstts_core is required. Run: pip install "lstts-core @ git+https://github.com/zjashanda/lstts.git@main"'
    ) from error


BEIJING = timezone(timedelta(hours=8))
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOICE = "付小春"
DEFAULT_TIMEOUT_S = 60.0
VOICE_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
MANIFEST_FIELDS = [
    "case_id", "text", "audio_path", "provider", "model", "voice", "rate", "pitch", "volume",
    "container", "codec", "sample_rate", "channels", "duration_seconds", "size_bytes",
    "mean_volume_db", "peak_volume_db", "non_silent", "clipping_status", "sha256", "generated_at",
    "tool", "tool_version", "validation_status", "error",
]


def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def voice_dir() -> Path:
    configured = os.environ.get("LSTEST_VOICE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".lstest" / "voices"


def resolve_voice(voice: str) -> Path:
    """Resolve a voice name to a local reference clip, or accept a direct audio path."""
    value = (voice or "").strip()
    if not value:
        raise ValueError("voice cannot be empty")
    direct = Path(value).expanduser()
    if direct.is_file() and direct.suffix.lower() in VOICE_SUFFIXES:
        return direct
    configured = voice_dir()
    for candidate in (configured / f"{value}.wav", configured / f"{value}{direct.suffix}"):
        if candidate.is_file():
            return candidate
    return _core_resolve_voice(value)


def tts_config_paths() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("LSTEST_TTS_CONFIG")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path.home() / ".lstest" / "tts.json")
    for base in (Path.home() / ".claude" / "skills", Path.home() / ".codex" / "skills", Path.home() / ".opencode" / "skills"):
        candidates.append(base / "index-tts" / "references" / "index_tts.json")
        candidates.append(base / "lstts" / "references" / "index_tts.json")
    return candidates


def load_tts_config() -> dict[str, Any]:
    """Load service credentials from env or the installed IndexTTS skill config."""
    url = os.environ.get("LSTEST_TTS_URL")
    token = os.environ.get("LSTTS_TTS_TOKEN") or os.environ.get("LSTEST_TTS_TOKEN")
    if url and token:
        return {"base_url": url, "token": token, "request_timeout": float(os.environ.get("LSTEST_TTS_TIMEOUT", "10"))}
    payload = _core_load_config("index_tts.json")
    if payload.get("base_url") and payload.get("token"):
        return payload
    raise RuntimeError("IndexTTS service config not found; set LSTEST_TTS_CONFIG or configure ~/.lstest/tts.json")


def inspect_wav(path: Path) -> dict[str, Any]:
    """Validate a synthesized WAV without external tools and return manifest metrics."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"audio file is missing or empty: {path}")
    return _core_inspect_wav(path, strict=True)


def request_audio(
    config: Mapping[str, Any],
    speaker: Path,
    text: str,
    *,
    emotion: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bytes:
    """POST one text/voice pair and return the raw ZIP payload."""
    text_path = speaker.parent / ".lstest.request.txt"
    try:
        text_path.write_text(text, encoding="utf-8")
        return _core_request_audio(config, speaker, emotion, text_path)
    finally:
        text_path.unlink(missing_ok=True)


def extract_wav(payload: bytes, destination: Path) -> Path:
    """Extract the single WAV inside the service ZIP payload."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    return _core_extract_wav(payload, destination)


def synthesize_indextts(
    text: str,
    output: Path,
    *,
    case_id: str = "",
    voice: str = DEFAULT_VOICE,
    emotion: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    retries: int = 1,
) -> dict[str, Any]:
    """Generate one validated IndexTTS WAV; retry transient failures once by default."""
    normalized_text = (text or "").strip()
    if not normalized_text:
        raise ValueError("text cannot be empty")
    if output.suffix.lower() != ".wav":
        raise ValueError("IndexTTS output must use the .wav extension")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if retries < 0:
        raise ValueError("retries must be >= 0")

    speaker = resolve_voice(voice)
    emotion_path = resolve_voice(str(emotion)) if emotion else None
    config = load_tts_config()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    failure: Exception | None = None
    for attempt in range(1, retries + 2):
        temporary.unlink(missing_ok=True)
        try:
            payload = request_audio(config, speaker, normalized_text, emotion=emotion_path, timeout_s=timeout_s)
            extract_wav(payload, temporary)
            metadata = inspect_wav(temporary)
            shutil.move(str(temporary), str(output))
            return {
                "case_id": case_id,
                "text": normalized_text,
                "audio_path": str(output),
                "provider": "indextts",
                "model": "indextts",
                "voice": voice,
                "rate": "",
                "pitch": "",
                "volume": "",
                **{name: metadata[name] for name in ("container", "codec", "sample_rate", "channels", "duration_seconds")},
                "size_bytes": output.stat().st_size,
                "mean_volume_db": metadata["mean_volume_db"],
                "peak_volume_db": metadata["peak_volume_db"],
                "non_silent": "true",
                "clipping_status": "PASS" if metadata["peak_volume_db"] <= -0.1 else "WARN_NEAR_ZERO_DBFS",
                "sha256": sha256_file(output),
                "generated_at": now_iso(),
                "tool": "indextts",
                "tool_version": str(config.get("tool_version") or "http-api"),
                "validation_status": "PASS",
                "error": "",
            }
        except Exception as error:
            failure = error
            temporary.unlink(missing_ok=True)
            if attempt > retries:
                break
    raise RuntimeError(f"IndexTTS synthesis failed after {retries + 1} attempt(s): {failure}") from failure


def safe_audio_filename(value: str, *, fallback_text: str) -> str:
    candidate = Path(value or "").name
    stem = Path(candidate).stem if candidate else ""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    if not stem:
        stem = hashlib.sha256(fallback_text.encode("utf-8")).hexdigest()[:16]
    return f"{stem}.wav"


def load_text_manifest(path: Path) -> list[dict[str, str]]:
    """Load a UTF-8 CSV or JSONL manifest containing at least a text field."""
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raise ValueError("text manifest must be .csv, .jsonl, or .ndjson")
    result: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        if not isinstance(row, Mapping):
            raise ValueError(f"manifest row {index} is not an object")
        text = str(row.get("text") or "").strip()
        if not text:
            raise ValueError(f"manifest row {index} has empty text")
        result.append({
            "case_id": str(row.get("case_id") or f"row-{index - 1}"),
            "text": text,
            "filename": str(row.get("filename") or row.get("audio_filename") or ""),
            "voice": str(row.get("voice") or "").strip(),
        })
    if not result:
        raise ValueError("text manifest has no rows")
    return result


def write_audio_manifest(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def synthesize_manifest(
    rows: Iterable[Mapping[str, str]],
    output_dir: Path,
    manifest_output: Path,
    *,
    voice: str = DEFAULT_VOICE,
    emotion: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    retries: int = 1,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for row in rows:
        case_id = str(row.get("case_id") or "")
        text = str(row.get("text") or "").strip()
        row_voice = str(row.get("voice") or "").strip() or voice
        target = output_dir / safe_audio_filename(str(row.get("filename") or case_id), fallback_text=text)
        try:
            records.append(
                synthesize_indextts(
                    text, target, case_id=case_id, voice=row_voice, emotion=emotion,
                    timeout_s=timeout_s, retries=retries,
                )
            )
        except Exception as error:
            records.append({
                "case_id": case_id,
                "text": text,
                "audio_path": str(target),
                "provider": "indextts",
                "voice": row_voice,
                "generated_at": now_iso(),
                "tool": "indextts",
                "tool_version": "http-api",
                "validation_status": "FAIL",
                "error": f"{type(error).__name__}: {error}",
            })
    write_audio_manifest(manifest_output, records)
    failures = [row for row in records if row["validation_status"] != "PASS"]
    return {
        "status": "PASS" if not failures else "FAIL",
        "requested": len(records),
        "generated": len(records) - len(failures),
        "failed": len(failures),
        "manifest": str(manifest_output),
        "failures": [{"case_id": row["case_id"], "error": row["error"]} for row in failures],
    }
