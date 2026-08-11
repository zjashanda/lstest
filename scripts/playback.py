"""Playback probe and lifecycle abstraction."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .core import TaskArtifacts
except ImportError:  # direct execution fallback
    from core import TaskArtifacts


class PlaybackBackend:
    def __init__(self, artifacts: TaskArtifacts, device_key: str | None, script: Path | None = None):
        self.artifacts = artifacts
        self.device_key = device_key
        self.script = script or (Path.home() / ".codex" / "skills" / "listenai-play" / "scripts" / "listenai_play.py")
        self.normalizer = PlayerEventNormalizer()
        self.lifecycle: list[dict[str, Any]] = []
        self._lifecycle_times: dict[str, float] = {}

    def probe(self) -> bool:
        if not self.device_key:
            self.artifacts.emit("PLAYER_BLOCKED", level="ERROR", message="未提供播放声卡稳定 key。", task_log=True)
            return False
        if not self.script.is_file():
            self.artifacts.emit("PLAYER_BLOCKED", level="ERROR", message="播放工具不存在。", task_log=True, script=str(self.script))
            return False
        try:
            completed = subprocess.run([sys.executable, str(self.script), "probe", "--device-key", self.device_key], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.artifacts.emit("PLAYER_PROBE_FAIL", level="ERROR", message=f"播放器探测失败: {error}", task_log=True)
            return False
        ok = completed.returncode == 0
        output = completed.stdout or completed.stderr or ""
        tool_log = self.artifacts.write_tool_log("player_probe.log", output)
        self.artifacts.emit(
            "PLAYER_PROBE",
            message=f"播放器探测{'通过' if ok else '失败'}。",
            task_log=True,
            returncode=completed.returncode,
            device_key=self.device_key,
            tool_output=output[-500:],
            evidence=str(tool_log),
            tool_status="PASS" if ok else "FAIL",
        )
        return ok

    def play(self, audio: Path, *, case_id: str = "") -> bool:
        if not self.device_key or not self.script.is_file():
            return False
        started = time.monotonic()
        self.artifacts.emit("PLAYER_REQUEST", message=f"请求播放音频: {audio.name}。", task_log=True, case_id=case_id, audio=str(audio), device_key=self.device_key)
        self.artifacts.emit("HOST_AUDIO_START", message=f"开始播放音频: {audio.name}。", task_log=True, case_id=case_id, audio=str(audio), monotonic_start=started)
        try:
            completed = subprocess.run([sys.executable, str(self.script), "play", "--audio-file", str(audio), "--device-key", self.device_key], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.artifacts.emit("HOST_AUDIO_ERROR", level="ERROR", message=f"主机播放失败: {error}", task_log=True, case_id=case_id)
            return False
        ended = time.monotonic()
        output = (completed.stdout or "") + (completed.stderr or "")
        tool_log = self.artifacts.write_tool_log(f"play_{case_id or audio.stem}.log", output)
        duration_s = round(ended - started, 3)
        self.artifacts.emit(
            "HOST_AUDIO_END",
            message=f"音频播放结束，耗时 {duration_s:.3f}s。",
            task_log=True,
            case_id=case_id,
            returncode=completed.returncode,
            duration_s=duration_s,
            evidence=str(tool_log),
            tool_status="PASS" if completed.returncode == 0 else "FAIL",
        )
        return completed.returncode == 0

    def observe_marker(self, marker: str, *, case_id: str = "", port: str | None = None, raw_line: str = "") -> dict[str, Any]:
        """记录项目原始播放器 marker，并附加公共生命周期状态。"""
        observed_at = time.monotonic()
        normalized = self.normalizer.observe(marker)
        record = {**normalized, "case_id": case_id, "port": port, "raw_line": raw_line, "monotonic_seconds": observed_at}
        self.lifecycle.append(record)
        state = normalized.get("player_state")
        if state in {"START", "PREPARED", "REQUEST"}:
            self._lifecycle_times.setdefault(str(state), observed_at)
        if state == "END" and "START" in self._lifecycle_times:
            record["duration_ms"] = round((observed_at - self._lifecycle_times["START"]) * 1000)
        level = "ERROR" if state == "ERROR" else ("INFO" if state else "WARN")
        self.artifacts.emit(
            "PLAYER_LIFECYCLE", level=level,
            message=f"播放器 marker: {marker}；state={state or 'UNKNOWN'}。",
            task_log=True, **record,
        )
        return record


class CaptureBackend:
    """可选录音适配器；没有显式 key/adapter 时绝不回退默认麦克风。"""

    def __init__(self, artifacts: TaskArtifacts, device_key: str | None, adapter: Any = None):
        self.artifacts = artifacts
        self.device_key = device_key
        self.adapter = adapter

    def probe(self) -> dict[str, Any]:
        if not self.device_key:
            self.artifacts.emit("CAPTURE_UNAVAILABLE", level="WARN", message="未提供录音声卡稳定 key，跳过录音。", task_log=True)
            return {"status": "UNAVAILABLE", "reason": "capture device key missing"}
        if self.adapter is None:
            self.artifacts.emit("CAPTURE_UNAVAILABLE", level="WARN", message="未注入录音 adapter，跳过录音且不使用默认麦克风。", task_log=True, device_key=self.device_key)
            return {"status": "UNAVAILABLE", "reason": "capture adapter missing", "device_key": self.device_key}
        try:
            result = self.adapter.probe(self.device_key)
        except Exception as error:
            self.artifacts.emit("CAPTURE_PROBE_FAIL", level="ERROR", message=f"录音设备探测失败: {error}", task_log=True, device_key=self.device_key)
            return {"status": "FAIL", "reason": str(error), "device_key": self.device_key}
        self.artifacts.emit("CAPTURE_PROBE", message="录音设备探测通过。", task_log=True, device_key=self.device_key)
        return {"status": "PASS", "device_key": self.device_key, "result": result}


class PlayerEventNormalizer:
    """Maps project marker names to a stable player lifecycle vocabulary."""

    STATES = {"REQUEST", "PREPARED", "START", "PAUSE", "STOP", "END", "ERROR"}

    def __init__(self, mapping: dict[str, str] | None = None):
        self.mapping = mapping or {}

    def normalize(self, marker: str) -> str | None:
        state = self.mapping.get(marker, marker.upper())
        return state if state in self.STATES else None

    def observe(self, marker: str) -> dict[str, str | None]:
        """保留原始播放器 marker，并给出可比较的生命周期状态。"""
        return {
            "raw_marker": marker,
            "player_state": self.normalize(marker),
            "tool_status": "OBSERVED" if self.normalize(marker) else "WARN",
        }
