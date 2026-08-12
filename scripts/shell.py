"""Profile-controlled initialization recovery and restart monitoring."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterable, Mapping

try:
    from .core import TaskArtifacts
    from .profile import DeviceProfile, ProfileError
except ImportError:  # direct execution fallback
    from core import TaskArtifacts
    from profile import DeviceProfile, ProfileError


def _patterns(rule: Mapping[str, Any], name: str) -> list[str]:
    value = rule.get(name, [])
    return [str(item) for item in value] if isinstance(value, list) else []


def _positive_float(rule: Mapping[str, Any], name: str, default: float) -> float:
    try:
        return max(0.01, float(rule.get(name, default)))
    except (TypeError, ValueError):
        return default


def _nonnegative_int(rule: Mapping[str, Any], name: str, default: int) -> int:
    try:
        return max(0, int(rule.get(name, default)))
    except (TypeError, ValueError):
        return default


class ProfileCommandSender:
    """Send one profile-approved command with observable verification and retry."""

    def __init__(
        self,
        profile: DeviceProfile,
        artifacts: TaskArtifacts,
        writer: Callable[[str, str], str | None],
        manager: Any | None = None,
    ):
        self.profile = profile
        self.artifacts = artifacts
        self.writer = writer
        self.manager = manager

    def _resolve_port(self, rule: Mapping[str, Any], role: str | None, port: str | None) -> str | None:
        resolved = (port or str(rule.get("port", ""))).strip()
        if not resolved:
            return None
        declared_roles = {str(item.get("role")): item for item in self.profile.ports if item.get("role")}
        if role and role in declared_roles:
            allowed_port = str(declared_roles[role].get("port", "")).strip()
            if allowed_port and resolved != allowed_port:
                raise ProfileError(f"port {resolved} does not match profile role {role}")
        return resolved

    def _snapshot(self, port: str) -> dict[str, int]:
        if self.manager is None or not hasattr(self.manager, "snapshot"):
            return {port: 0}
        snapshot = self.manager.snapshot(port)
        return {port: int(snapshot.get(port, 0))}

    def _matching_events(
        self,
        patterns: Iterable[str],
        *,
        port: str,
        cursors: Mapping[str, int],
        timeout_s: float,
    ) -> list[Any]:
        values = [str(item) for item in patterns]
        if not values or self.manager is None or not hasattr(self.manager, "wait_for"):
            return []
        events = self.manager.wait_for(
            lambda items: any(self.profile.match_any(values, item.line) for item in items),
            timeout_s,
            cursors={port: int(cursors.get(port, 0))},
        )
        return [item for item in events if self.profile.match_any(values, item.line)]

    @staticmethod
    def _evidence_refs(events: Iterable[Any]) -> list[str]:
        refs: list[str] = []
        for item in events:
            port = str(getattr(item, "port", "unknown"))
            role = str(getattr(item, "role", "unknown"))
            cursor = getattr(item, "cursor", "")
            refs.append(f"serial_logs/serial_{port}_{role}.log#{cursor}")
        return refs

    def _blocked_result(
        self,
        command: str,
        reason: str,
        *,
        role: str | None,
        port: str | None,
        status: str = "BLOCKED_COMMAND_POLICY",
    ) -> dict[str, Any]:
        self.artifacts.emit(
            "SERIAL_COMMAND_BLOCKED", level="ERROR", message=reason, task_log=True,
            command=command, role=role, port=port,
        )
        return {"status": status, "command": command, "reason": reason}

    def send(self, command: str, *, role: str | None = None, port: str | None = None) -> dict[str, Any]:
        """Send and verify an approved command without treating silence as success."""
        try:
            rule = dict(self.profile.assert_command_allowed(command, role))
        except ProfileError as error:
            return self._blocked_result(command, str(error), role=role, port=port)
        try:
            resolved_port = self._resolve_port(rule, role, port)
            if not resolved_port:
                raise ProfileError("approved command has no target port")
        except ProfileError as error:
            return self._blocked_result(
                command,
                str(error),
                role=role,
                port=port,
                status="BLOCKED_PORT_POLICY",
            )

        success_patterns = _patterns(rule, "success_patterns")
        evidence_patterns = _patterns(rule, "evidence_patterns")
        if not success_patterns and not evidence_patterns:
            return self._blocked_result(
                command,
                "approved command requires success_patterns or evidence_patterns for verification",
                role=role,
                port=resolved_port,
            )

        retries = _nonnegative_int(rule, "retries", 1)
        max_attempts = retries + 1
        ack_timeout_s = _positive_float(rule, "timeout_s", 3.0)
        evidence_timeout_s = _positive_float(rule, "evidence_timeout_s", ack_timeout_s)
        retry_delay_s = _positive_float(rule, "retry_delay_s", 0.2)
        attempts: list[dict[str, Any]] = []

        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            cursors = self._snapshot(resolved_port)
            self.artifacts.emit(
                "SERIAL_COMMAND_ATTEMPT",
                message=f"发送初始化命令，第 {attempt}/{max_attempts} 次: {command}。",
                task_log=True,
                command=command,
                role=role,
                port=resolved_port,
                attempt=attempt,
                max_attempts=max_attempts,
                cursors=cursors,
            )
            direct_reply = ""
            write_error = ""
            try:
                direct_reply = str(self.writer(command, resolved_port) or "")
            except Exception as error:
                write_error = f"{type(error).__name__}: {error}"

            validation_source = ""
            evidence_events: list[Any] = []
            failure_reason = ""
            if write_error:
                failure_reason = "command_write_failed"
            elif direct_reply.strip():
                if success_patterns and self.profile.match_any(success_patterns, direct_reply):
                    validation_source = "direct_ack"
                else:
                    failure_reason = "direct_ack_not_matched"
            else:
                ack_events = self._matching_events(
                    success_patterns,
                    port=resolved_port,
                    cursors=cursors,
                    timeout_s=ack_timeout_s,
                )
                if ack_events:
                    validation_source = "serial_ack"
                    evidence_events = ack_events
                elif evidence_patterns:
                    evidence_events = self._matching_events(
                        evidence_patterns,
                        port=resolved_port,
                        cursors=cursors,
                        timeout_s=evidence_timeout_s,
                    )
                    if evidence_events:
                        validation_source = "serial_evidence"
                    else:
                        failure_reason = "no_ack_or_evidence"
                else:
                    failure_reason = "no_ack"

            result = {
                "command": command,
                "role": role,
                "port": resolved_port,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "reply": direct_reply,
                "write_error": write_error,
                "validation_source": validation_source,
                "success_patterns": success_patterns,
                "evidence_patterns": evidence_patterns,
                "evidence_refs": self._evidence_refs(evidence_events),
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
            if validation_source:
                result["status"] = "PASS"
                attempts.append(result)
                self.artifacts.emit(
                    "SERIAL_COMMAND_VALIDATED",
                    message=f"初始化命令已验证成功: {command}；来源={validation_source}。",
                    task_log=True,
                    **result,
                )
                return {**result, "attempts": attempts}

            result.update({"status": "FAIL", "reason": failure_reason})
            attempts.append(result)
            level = "WARN" if attempt < max_attempts else "ERROR"
            self.artifacts.emit(
                "SERIAL_COMMAND_ATTEMPT_FAILED",
                level=level,
                message=f"初始化命令第 {attempt}/{max_attempts} 次未验证成功: {command}；{failure_reason}。",
                task_log=True,
                **result,
            )
            if attempt < max_attempts:
                self.artifacts.emit(
                    "SERIAL_COMMAND_RETRY",
                    level="WARN",
                    message=f"初始化命令将在 {retry_delay_s:.2f}s 后重试: {command}。",
                    task_log=True,
                    command=command,
                    role=role,
                    port=resolved_port,
                    next_attempt=attempt + 1,
                    max_attempts=max_attempts,
                )
                time.sleep(retry_delay_s)

        final = {**attempts[-1], "attempts": attempts}
        self.artifacts.emit(
            "SERIAL_COMMAND_RECOVERY_FAILED",
            level="ERROR",
            message=f"初始化命令重试耗尽仍未恢复: {command}。",
            task_log=True,
            **final,
        )
        return final


class ProfileRecoveryStateMachine:
    """Wait for completed initialization before recovering profile-approved commands."""

    def __init__(self, profile: DeviceProfile, artifacts: TaskArtifacts, manager: Any):
        self.profile = profile
        self.artifacts = artifacts
        self.manager = manager
        self.state = "CREATED"

    def _append_result_log(self, result: Mapping[str, Any]) -> None:
        self.artifacts.write_tool_log(
            "initialization_recovery.jsonl",
            json.dumps(dict(result), ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _evidence_refs(events: Iterable[Any]) -> list[str]:
        return ProfileCommandSender._evidence_refs(events)

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        level = "INFO" if result["status"] == "PASS" else "ERROR"
        self.artifacts.emit(
            "PROFILE_RECOVERY_RESULT",
            level=level,
            message=f"初始化恢复 {result['status']}；原因={result['reason']}。",
            task_log=True,
            **result,
        )
        self._append_result_log(result)
        if result["status"] != "PASS":
            self.artifacts.record_anomaly(
                "INITIALIZATION_RECOVERY_FAILED",
                "初始化命令恢复未完成，已保留命令尝试、回执和旁证。",
                recovery=result,
            )
            recovery_config = self.profile.recovery if hasattr(self.profile, "recovery") else {}
            if bool(recovery_config.get("stop_on_failure", True)):
                self.artifacts.stop.request("INITIALIZATION_RECOVERY_FAILED")
                self.artifacts.emit(
                    "INITIALIZATION_RECOVERY_STOP_REQUESTED",
                    level="ERROR",
                    message="初始化恢复失败，已请求安全停止，避免继续产生无日志结果。",
                    task_log=True,
                    recovery_reason=result.get("recovery_reason", ""),
                    recovery_state=result.get("state", ""),
                )
        return result

    def run(
        self,
        timeout_s: float = 5.0,
        *,
        cursors: Mapping[str, int] | None = None,
        recovery_reason: str = "startup",
    ) -> dict[str, Any]:
        enabled = [
            dict(item) for item in self.profile.payload.get("commands", [])
            if isinstance(item, Mapping) and item.get("safe_init", True)
        ]
        if not self.manager.handles:
            self.state = "BLOCKED"
            return self._complete({
                "status": "BLOCKED",
                "reason": "no serial port is open",
                "state": self.state,
                "recovery_reason": recovery_reason,
                "commands": [],
            })
        if not enabled:
            self.state = "READY"
            return self._complete({
                "status": "PASS",
                "reason": "no safe initialization commands configured",
                "state": self.state,
                "recovery_reason": recovery_reason,
                "commands": [],
            })
        if not self.profile.initialization_patterns:
            self.state = "BLOCKED"
            return self._complete({
                "status": "BLOCKED",
                "reason": "initialization_patterns are required before safe initialization commands",
                "state": self.state,
                "recovery_reason": recovery_reason,
                "commands": [],
            })

        start_cursors = dict(cursors or {port: 0 for port in self.manager.handles})
        self.state = "WAIT_INIT"
        self.artifacts.emit(
            "PROFILE_RECOVERY_WAIT_INIT",
            message="等待设备完成初始化后再发送恢复命令。",
            task_log=True,
            recovery_reason=recovery_reason,
            initialization_patterns=self.profile.initialization_patterns,
            timeout_s=timeout_s,
            cursors=start_cursors,
        )
        events = self.manager.wait_for(
            lambda items: any(self.profile.match_any(self.profile.initialization_patterns, item.line) for item in items),
            timeout_s,
            cursors=start_cursors,
        )
        init_events = [item for item in events if self.profile.match_any(self.profile.initialization_patterns, item.line)]
        if not init_events:
            self.state = "BLOCKED"
            return self._complete({
                "status": "BLOCKED",
                "reason": "initialization marker missing",
                "state": self.state,
                "recovery_reason": recovery_reason,
                "marker_count": len(events),
                "initialization_evidence_refs": [],
                "commands": [],
            })

        self.state = "SEND_APPROVED"
        sender = ProfileCommandSender(self.profile, self.artifacts, self.manager.write, self.manager)
        results: list[dict[str, Any]] = []
        for rule in enabled:
            command = str(rule.get("command", "")).strip()
            roles = rule.get("roles") or [None]
            role = str(roles[0]) if roles and roles[0] else None
            target = next((spec.port for spec in self.manager.ports if role is None or spec.role == role), None)
            results.append(sender.send(command, role=role, port=target))

        all_passed = bool(results) and all(item.get("status") == "PASS" for item in results)
        self.state = "READY" if all_passed else "BLOCKED"
        return self._complete({
            "status": "PASS" if all_passed else "BLOCKED",
            "reason": "all initialization commands verified" if all_passed else "one or more initialization commands were not verified",
            "state": self.state,
            "recovery_reason": recovery_reason,
            "marker_count": len(events),
            "initialization_evidence_refs": self._evidence_refs(init_events),
            "commands": results,
        })


class ProfileRestartRecoveryMonitor:
    """Continuously detect profile restart markers and rerun verified recovery."""

    def __init__(
        self,
        profile: DeviceProfile,
        artifacts: TaskArtifacts,
        manager: Any,
        *,
        initialization_timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> None:
        self.profile = profile
        self.artifacts = artifacts
        self.manager = manager
        self.initialization_timeout_s = max(0.1, float(initialization_timeout_s))
        self.poll_interval_s = max(0.02, float(poll_interval_s))
        self.cursors: dict[str, int] = {}
        self.history: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.profile.restart_patterns:
            self.artifacts.emit(
                "RESTART_RECOVERY_MONITOR_SKIPPED",
                message="profile 未配置 restart_patterns，跳过重启恢复监控。",
                task_log=True,
            )
            return
        self.cursors = dict(self.manager.snapshot())
        self._thread = threading.Thread(target=self._run, name="lstest-restart-recovery", daemon=True)
        self._thread.start()
        self.artifacts.emit(
            "RESTART_RECOVERY_MONITOR_STARTED",
            message="已启动设备重启后的初始化恢复监控。",
            task_log=True,
            restart_patterns=self.profile.restart_patterns,
            cursors=self.cursors,
        )

    def _run(self) -> None:
        while not self._stop.is_set() and not self.manager.stop_event.is_set():
            self.poll()
            self._stop.wait(self.poll_interval_s)

    def poll(self) -> list[dict[str, Any]]:
        """Process currently available restart markers; exposed for deterministic tests."""
        if not self.profile.restart_patterns or self._stop.is_set():
            return []
        batch_start = dict(self.cursors)
        events = self.manager.since(batch_start)
        if not events:
            return []
        progressed = dict(batch_start)
        for event in events:
            port = str(getattr(event, "port", ""))
            if port:
                progressed[port] = int(getattr(event, "cursor", progressed.get(port, 0)))
            if not self.profile.match_any(self.profile.restart_patterns, event.line):
                continue
            baseline = dict(progressed)
            self.artifacts.emit(
                "DEVICE_RESTART_DETECTED",
                level="WARN",
                message="检测到设备重启 marker，等待重新初始化并恢复配置命令。",
                task_log=True,
                restart_line=event.line,
                restart_port=getattr(event, "port", ""),
                restart_role=getattr(event, "role", ""),
                restart_cursor=getattr(event, "cursor", 0),
                evidence_refs=ProfileCommandSender._evidence_refs([event]),
            )
            recovery = ProfileRecoveryStateMachine(self.profile, self.artifacts, self.manager).run(
                self.initialization_timeout_s,
                cursors=baseline,
                recovery_reason="restart",
            )
            self.history.append(recovery)
            self.cursors = dict(self.manager.snapshot())
            return [recovery]
        self.cursors = progressed
        return []

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(2.0, self.initialization_timeout_s + self.poll_interval_s))
        self.artifacts.emit(
            "RESTART_RECOVERY_MONITOR_STOPPED",
            message="设备重启恢复监控已停止。",
            task_log=True,
            recovery_count=len(self.history),
        )
