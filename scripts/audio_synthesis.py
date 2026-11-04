"""IndexTTS synthesis with reproducible manifests and audio validation."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import mimetypes
import os
import re
import socket
import uuid
import wave
import zipfile
from array import array
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


BEIJING = timezone(timedelta(hours=8))
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
    for candidate in (voice_dir() / f"{value}.wav", voice_dir() / f"{value}{direct.suffix}"):
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        f"voice reference not found: {value}; expected {voice_dir() / (value + '.wav')} "
        f"or an explicit audio file path"
    )


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
    token = os.environ.get("LSTEST_TTS_TOKEN")
    if url and token:
        return {
            "base_url": url,
            "token": token,
            "request_timeout": float(os.environ.get("LSTEST_TTS_TIMEOUT", "10")),
        }
    for path in tts_config_paths():
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if payload.get("base_url") and payload.get("token"):
                return payload
    raise RuntimeError(
        "IndexTTS service config not found; set LSTEST_TTS_URL/LSTEST_TTS_TOKEN "
        "or LSTEST_TTS_CONFIG"
    )


def inspect_wav(path: Path) -> dict[str, Any]:
    """Validate a synthesized WAV without external tools and return manifest metrics."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"audio file is missing or empty: {path}")
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        data = handle.readframes(frames)
    if rate <= 0 or frames <= 0:
        raise RuntimeError(f"audio file has no frames: {path}")
    duration = frames / rate
    mean_db = -120.0
    peak_db = -120.0
    if width == 2 and data:
        samples = array("h")
        samples.frombytes(data[: len(data) - len(data) % 2])
        total = len(samples)
        peak = max(abs(min(samples)), abs(max(samples)))
        square = sum(value * value for value in samples) / total
        peak_db = 20 * math.log10(peak / 32768) if peak else -120.0
        mean_db = 10 * math.log10(square / (32768 ** 2)) if square else -120.0
    codec = {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}.get(width, f"pcm_{width * 8}")
    metadata = {
        "container": "wav",
        "codec": codec,
        "sample_rate": rate,
        "channels": channels,
        "duration_seconds": round(duration, 6),
        "mean_volume_db": round(mean_db, 1),
        "peak_volume_db": round(peak_db, 1),
    }
    valid = (
        width == 2
        and duration > 0.2
        and mean_db > -60.0
        and peak_db <= 0.0
    )
    if not valid:
        raise RuntimeError(
            "IndexTTS output validation failed: "
            f"metadata={metadata}, mean_volume_db={mean_db}, peak_volume_db={peak_db}"
        )
    return metadata


def _multipart_fields(speaker: Path, text: str, emotion: Path | None) -> tuple[bytes, str]:
    boundary = "----lstest" + uuid.uuid4().hex
    text_bytes = text.encode("utf-8")
    parts: list[tuple[str, str, bytes, str]] = [
        ("spk_audio_prompt", speaker.name, speaker.read_bytes(), mimetypes.guess_type(speaker.name)[0] or "application/octet-stream"),
        ("text", ".request.txt", text_bytes, "text/plain"),
    ]
    if emotion is not None:
        parts.append(("emo_audio_prompt", emotion.name, emotion.read_bytes(), mimetypes.guess_type(emotion.name)[0] or "application/octet-stream"))
    chunks: list[bytes] = []
    for field, filename, payload, content_type in parts:
        header = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        )
        chunks.extend([header.encode("utf-8"), payload, b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def request_audio(
    config: Mapping[str, Any],
    speaker: Path,
    text: str,
    *,
    emotion: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bytes:
    """POST one text/voice pair and return the raw ZIP payload."""
    body, boundary = _multipart_fields(speaker, text, emotion)
    base = str(config.get("base_url") or "").rstrip("/")
    endpoint = "/v1/emotion" if emotion is not None else "/v1/clone"
    url = urlsplit(base + endpoint)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise RuntimeError("invalid IndexTTS base_url")
    request = Request(url.geturl(), data=body, method="POST")
    request.add_header("Authorization", "Bearer " + str(config.get("token") or ""))
    request.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    request.add_header("Content-Length", str(len(body)))
    request.add_header("Connection", "close")
    try:
        with urlopen(request, timeout=max(0.1, float(timeout_s))) as response:
            payload = response.read()
            if response.status != 200:
                raise RuntimeError(f"synthesis service returned HTTP {response.status}")
            if (response.headers.get_content_type() or "") != "application/zip":
                raise RuntimeError("synthesis service did not return a ZIP payload")
            return payload
    except HTTPError as error:
        raise RuntimeError(f"synthesis service returned HTTP {error.code}") from error
    except (URLError, socket.timeout) as error:
        raise RuntimeError(f"synthesis request failed: {error}") from error


def extract_wav(payload: bytes, destination: Path) -> Path:
    """Extract the single WAV inside the service ZIP payload."""
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        entries = []
        for item in archive.infolist():
            name = item.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or ":" in name:
                raise RuntimeError("service ZIP contains an unsafe path")
            if not item.is_dir() and Path(name).suffix.lower() == ".wav":
                entries.append(item)
        if len(entries) != 1:
            raise RuntimeError(f"service returned {len(entries)} WAV files, expected 1")
        destination.write_bytes(archive.read(entries[0]))
    return destination


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
            os.replace(temporary, output)
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
