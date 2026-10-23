"""Optional capability probes used before a formal lstest run."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


VALID_CHECKS = frozenset({"serial_port", "audio_device", "file", "command"})


class CapabilityError(ValueError):
    def __init__(self, code: str, message: str, capability_id: str = "") -> None:
        self.code = str(code)
        self.capability_id = str(capability_id)
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    required: bool
    check: str
    params: Mapping[str, Any] = field(default_factory=dict)
    blocked_reason: str = ""


@dataclass(frozen=True)
class CapabilityResult:
    capability_id: str
    status: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "status": self.status,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


def load_capabilities(path: Path | str) -> list[CapabilitySpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise CapabilityError("CAPABILITY_CONFIG_INVALID", "capabilities file must be an object")
    if int(payload.get("schema_version", 0)) != 1:
        raise CapabilityError("CAPABILITY_CONFIG_INVALID", "unsupported capabilities schema_version")
    raw_items = payload.get("capabilities")
    if not isinstance(raw_items, list) or not raw_items:
        raise CapabilityError("CAPABILITY_CONFIG_INVALID", "capabilities must be a non-empty list")
    result: list[CapabilitySpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        location = f"capabilities[{index}]"
        if not isinstance(item, Mapping):
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", "capability must be an object", location)
        capability_id = str(item.get("id") or "").strip()
        if not capability_id:
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", "capability id is required", f"{location}.id")
        if capability_id in seen:
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", f"duplicate capability id: {capability_id}", f"{location}.id")
        seen.add(capability_id)
        check = str(item.get("check") or "").strip()
        if check not in VALID_CHECKS:
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", f"unsupported check: {check}", f"{location}.check")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", "required must be boolean", f"{location}.required")
        params = item.get("params", {})
        if not isinstance(params, Mapping):
            raise CapabilityError("CAPABILITY_CONFIG_INVALID", "params must be an object", f"{location}.params")
        result.append(CapabilitySpec(capability_id, required, check, dict(params), str(item.get("blocked_reason") or "")))
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_serial(params: Mapping[str, Any]) -> CapabilityResult:
    capability_id = str(params.get("id") or "serial_port")
    port = str(params.get("port") or "").strip()
    baud = int(params.get("baud") or 115200)
    if not port:
        return CapabilityResult(capability_id, "BLOCKED", "serial port is not configured", {"port": port})
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception as error:
        return CapabilityResult(capability_id, "BLOCKED", f"pyserial unavailable: {error}", {"port": port})
    try:
        matches = [
            item for item in list_ports.comports()
            if str(getattr(item, "device", "")).casefold() == port.casefold()
        ]
    except Exception as error:
        return CapabilityResult(capability_id, "BLOCKED", f"serial port enumeration failed: {error}", {"port": port})
    if not matches:
        return CapabilityResult(capability_id, "BLOCKED", f"serial port is not present: {port}", {"port": port, "baud": baud})
    record = matches[0]
    return CapabilityResult(
        capability_id,
        "PASS",
        "",
        {
            "port": port,
            "baud": baud,
            "device": str(getattr(record, "device", "")),
            "description": str(getattr(record, "description", "")),
        },
    )


def _probe_audio(params: Mapping[str, Any]) -> CapabilityResult:
    capability_id = str(params.get("id") or "audio_device")
    device_key = str(params.get("device_key") or "").strip()
    direction = str(params.get("direction") or "Render")
    platform = str(params.get("platform") or "auto")
    try:
        try:
            from . import listenai_play  # type: ignore
        except ImportError:
            import listenai_play  # type: ignore
        if platform == "auto":
            platform = listenai_play.detect_runtime_platform()
        if device_key:
            record = listenai_play.resolve_device_key(platform, device_key, direction=direction)
            return CapabilityResult(capability_id, "PASS", "", {"platform": platform, "direction": direction, "device_key": record.device_key, "name": record.name})
        items = listenai_play.filter_direction(listenai_play.scan_devices(platform), direction)
        if not items:
            return CapabilityResult(capability_id, "BLOCKED", f"no active {direction} endpoint", {"platform": platform, "direction": direction})
        return CapabilityResult(capability_id, "PASS", "", {"platform": platform, "direction": direction, "count": len(items), "name": items[0].name})
    except Exception as error:
        return CapabilityResult(capability_id, "BLOCKED", str(error), {"platform": platform, "direction": direction})


def _probe_file(params: Mapping[str, Any]) -> CapabilityResult:
    capability_id = str(params.get("id") or "file")
    path = Path(str(params.get("path") or "")).expanduser()
    if not path.is_file():
        return CapabilityResult(capability_id, "BLOCKED", f"file not found: {path}", {"path": str(path)})
    evidence: dict[str, Any] = {"path": str(path), "size": path.stat().st_size}
    expected = str(params.get("sha256") or "").strip().lower()
    if expected:
        actual = _sha256(path)
        evidence["sha256"] = actual
        if actual.lower() != expected:
            return CapabilityResult(capability_id, "BLOCKED", "sha256 mismatch", evidence)
    return CapabilityResult(capability_id, "PASS", "", evidence)


def _probe_command(params: Mapping[str, Any]) -> CapabilityResult:
    capability_id = str(params.get("id") or "command")
    command = str(params.get("command") or "").strip()
    if not command:
        return CapabilityResult(capability_id, "BLOCKED", "command is not configured")
    path = shutil.which(command)
    if not path:
        return CapabilityResult(capability_id, "BLOCKED", f"command not found: {command}", {"command": command})
    evidence: dict[str, Any] = {"command": command, "path": path}
    version_args = params.get("version_args")
    if isinstance(version_args, list) and version_args:
        try:
            completed = subprocess.run(
                [path, *[str(item) for item in version_args]],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            evidence["returncode"] = completed.returncode
            evidence["stdout"] = completed.stdout.strip()[:200]
            evidence["stderr"] = completed.stderr.strip()[:200]
            if completed.returncode != 0:
                return CapabilityResult(capability_id, "BLOCKED", f"command exited {completed.returncode}", evidence)
        except Exception as error:
            return CapabilityResult(capability_id, "BLOCKED", str(error), evidence)
    return CapabilityResult(capability_id, "PASS", "", evidence)


def probe_capability(spec: CapabilitySpec) -> CapabilityResult:
    params = {"id": spec.capability_id, **dict(spec.params)}
    if spec.check == "serial_port":
        result = _probe_serial(params)
    elif spec.check == "audio_device":
        result = _probe_audio(params)
    elif spec.check == "file":
        result = _probe_file(params)
    elif spec.check == "command":
        result = _probe_command(params)
    else:  # pragma: no cover - load_capabilities rejects this
        return CapabilityResult(spec.capability_id, "BLOCKED", f"unsupported check: {spec.check}")
    if result.status == "BLOCKED" and spec.blocked_reason:
        return CapabilityResult(result.capability_id, result.status, spec.blocked_reason, result.evidence)
    return result


def probe_capabilities(specs: Iterable[CapabilitySpec]) -> dict[str, Any]:
    specs = list(specs)
    results = [probe_capability(spec) for spec in specs]
    required_blocked = [result for spec, result in zip(specs, results) if spec.required and result.status != "PASS"]
    if required_blocked:
        status = "BLOCKED"
    elif any(result.status != "PASS" for result in results):
        status = "WARN"
    else:
        status = "PASS"
    return {
        "status": status,
        "results": [result.to_dict() for result in results],
        "required_blocked": [result.capability_id for result in required_blocked],
    }
