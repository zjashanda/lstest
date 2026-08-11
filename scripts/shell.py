"""Profile-controlled shell command sender."""

from __future__ import annotations

import time
from typing import Any, Callable

try:
    from .core import TaskArtifacts
    from .profile import DeviceProfile, ProfileError
except ImportError:  # direct execution fallback
    from core import TaskArtifacts
    from profile import DeviceProfile, ProfileError


class ProfileCommandSender:
    def __init__(self, profile: DeviceProfile, artifacts: TaskArtifacts, writer: Callable[[str, str], str | None]):
        self.profile = profile
        self.artifacts = artifacts
        self.writer = writer

    def _resolve_port(self, rule: dict[str, Any], role: str | None, port: str | None) -> str | None:
        resolved = (port or str(rule.get("port", ""))).strip()
        if not resolved:
            return None
        declared_roles = {str(item.get("role")): item for item in self.profile.ports if item.get("role")}
        if role and role in declared_roles:
            allowed_port = str(declared_roles[role].get("port", "")).strip()
            if allowed_port and resolved != allowed_port:
                raise ProfileError(f"port {resolved} does not match profile role {role}")
        return resolved

    def send(self, command: str, *, role: str | None = None, port: str | None = None) -> dict[str, Any]:
        try:
            rule = self.profile.assert_command_allowed(command, role)
        except ProfileError as error:
            self.artifacts.emit("SERIAL_COMMAND_BLOCKED", level="ERROR", message=str(error), task_log=True, command=command, role=role, port=port)
            return {"status": "BLOCKED_COMMAND_POLICY", "command": command, "reason": str(error)}
        try:
            resolved_port = self._resolve_port(dict(rule), role, port)
            if not resolved_port:
                raise ProfileError("approved command has no target port")
        except ProfileError as error:
            self.artifacts.emit("SERIAL_COMMAND_BLOCKED", level="ERROR", message=str(error), task_log=True, command=command, role=role, port=port)
            return {"status": "BLOCKED_PORT_POLICY", "command": command, "reason": str(error)}
        started = time.monotonic()
        self.artifacts.emit("SERIAL_COMMAND_SENT", message=f"发送配置命令: {command}。", task_log=True, command=command, role=role, port=resolved_port)
        try:
            reply = self.writer(command, resolved_port)
        except Exception as error:
            self.artifacts.emit("SERIAL_COMMAND_ERROR", level="ERROR", message=f"命令发送失败: {error}", task_log=True, command=command, role=role, port=resolved_port)
            return {"status": "FAIL", "command": command, "reason": str(error)}
        patterns = [str(value) for value in rule.get("success_patterns", [])]
        matched = self.profile.match_any(patterns, reply or "") if patterns else False
        ack_strength = str(rule.get("ack_strength", "strong"))
        status = "PASS" if matched else ("WARN" if ack_strength == "weak" and not reply else "FAIL")
        self.artifacts.emit("SERIAL_COMMAND_ACK", level="INFO" if status != "FAIL" else "ERROR", message=f"命令回执: {status}。", task_log=True, command=command, role=role, port=resolved_port, reply=reply or "", ack_strength=ack_strength, duration_ms=round((time.monotonic() - started) * 1000))
        return {"status": status, "command": command, "reply": reply or "", "ack_strength": ack_strength, "port": resolved_port}


class ProfileRecoveryStateMachine:
    """按 profile marker 执行安全日志恢复；未初始化时不发送命令。"""

    def __init__(self, profile: DeviceProfile, artifacts: TaskArtifacts, manager: Any):
        self.profile = profile
        self.artifacts = artifacts
        self.manager = manager
        self.state = "CREATED"

    def run(self, timeout_s: float = 5.0) -> dict[str, Any]:
        if not self.manager.handles:
            self.state = "BLOCKED"
            return {"status": "BLOCKED", "reason": "no serial port is open", "state": self.state}
        self.state = "WAIT_INIT"
        events = self.manager.wait_for(
            lambda items: any(self.profile.match_any(self.profile.initialization_patterns, item.line) for item in items),
            timeout_s,
            cursors={port: 0 for port in self.manager.handles},
        )
        if not any(self.profile.match_any(self.profile.initialization_patterns, item.line) for item in events):
            self.state = "BLOCKED"
            self.artifacts.emit("PROFILE_RECOVERY_BLOCKED", level="WARN", message="未观察到 profile 初始化 marker，未发送日志命令。", task_log=True)
            return {"status": "BLOCKED", "reason": "initialization marker missing", "state": self.state, "marker_count": len(events)}
        self.state = "SEND_APPROVED"
        sender = ProfileCommandSender(self.profile, self.artifacts, self.manager.write)
        results: list[dict[str, Any]] = []
        for item in self.profile.payload.get("commands", []):
            if not isinstance(item, dict) or not item.get("safe_init", True):
                continue
            command = str(item.get("command", "")).strip()
            roles = item.get("roles") or [None]
            role = str(roles[0]) if roles and roles[0] else None
            target = next((spec.port for spec in self.manager.ports if role is None or spec.role == role), None)
            results.append(sender.send(command, role=role, port=target))
        self.state = "READY" if all(item.get("status") in {"PASS", "WARN"} for item in results) else "BLOCKED"
        status = "PASS" if self.state == "READY" and all(item.get("status") == "PASS" for item in results) else ("WARN" if self.state == "READY" else "BLOCKED")
        return {"status": status, "state": self.state, "commands": results, "marker_count": len(events)}
