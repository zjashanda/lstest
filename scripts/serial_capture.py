"""Independent serial collectors with injectable factories for fake tests.

串口生命周期：连接属于任务，不属于采集窗口。``start()`` 附着，收尾只允许
``finish(TASK_DONE)`` / ``finish(INTERRUPT)``；空闲、无数据、切阶段都不关闭。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Any, Iterable, Optional

try:
    import serial
except ImportError:  # pragma: no cover - preflight reports the missing dependency
    serial = None

try:
    from .core import PortSpec, TaskArtifacts, now_log
    from .serial_session import (
        DETACH_REASONS,
        REASON_INTERRUPT,
        REASON_TASK_DONE,
        IllegalDetach,
        SerialSession,
        SerialSessionError,
    )
except ImportError:  # direct execution fallback
    from core import PortSpec, TaskArtifacts, now_log
    from serial_session import (
        DETACH_REASONS,
        REASON_INTERRUPT,
        REASON_TASK_DONE,
        IllegalDetach,
        SerialSession,
        SerialSessionError,
    )


MAX_EVENTS_PER_PORT = 20000
HEARTBEAT_INTERVAL_S = 30.0
OPEN_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class SerialEvent:
    port: str
    role: str
    cursor: int
    timestamp: str
    monotonic_seconds: float
    line: str


class SerialManager:
    def __init__(
        self,
        ports: tuple[PortSpec, ...],
        artifacts: TaskArtifacts,
        factory: Callable[..., Any] | None = None,
        *,
        session: Optional[SerialSession] = None,
    ):
        self.ports = ports
        self.artifacts = artifacts
        self.factory = factory or (serial.Serial if serial is not None else None)
        self.session = session
        self.task_id = session.task_id if session is not None else artifacts.task_id
        self.handles: dict[str, Any] = {}
        self.threads: list[threading.Thread] = []
        self.stop_event = threading.Event()
        self.errors: dict[str, str] = {}
        self.cursors: dict[str, int] = {}
        self.reconnect_attempts: dict[str, int] = {}
        self.events: dict[str, deque[SerialEvent]] = {}
        self.event_base: dict[str, int] = {}
        self._events_lock = threading.RLock()
        self.open_failures: dict[str, str] = {}
        self.bytes_written: dict[str, int] = {}
        self.idle_polls: dict[str, int] = {}
        self.detach_reason: Optional[str] = None
        self.detach_detail: str = ""
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat = 0.0

    def _open_port(self, spec: PortSpec) -> Any:
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["handle"] = self.factory(spec.port, spec.baudrate, timeout=0.2)
            except Exception as error:  # noqa: BLE001
                box["error"] = error

        opener = threading.Thread(target=worker, name=f"lstest-open-{spec.port}", daemon=True)
        opener.start()
        opener.join(OPEN_TIMEOUT_S)
        if opener.is_alive():
            raise TimeoutError(f"open({spec.port}) 超过 {OPEN_TIMEOUT_S}s 未返回")
        if "error" in box:
            raise box["error"]
        return box["handle"]

    def start(self) -> None:
        self.artifacts.require_cases_frozen("serial_capture")
        if not self.ports:
            self.artifacts.emit("SERIAL_SKIP", message="未声明串口，设备语音能力将按缺失证据处理。", task_log=True)
            return
        if self.factory is None:
            raise RuntimeError("pyserial is required for serial capture")

        if self.session is not None:
            payload = self.session.attach([
                {
                    "port": spec.port,
                    "role": spec.role or "unknown",
                    "baud": spec.baudrate,
                    "offset": 0,
                    "log": str(self.artifacts.serial_log_path(spec.port, spec.role or "unknown", spec.device)),
                }
                for spec in self.ports
            ])
            for gap in payload.get("gaps") or []:
                self.artifacts.emit(
                    "SERIAL_GAP", level="WARN", message=f"串口会话存在空隙：{gap.get('reason')}",
                    task_log=True, gap_from=gap.get("from"), gap_to=gap.get("to"), gap_reason=gap.get("reason"),
                )
            self.artifacts.emit(
                "SERIAL_SESSION_ATTACH", message="串口会话已附着：任务未结束前不主动断开。",
                task_log=True, session=str(self.session.path), since=payload.get("attach_at"),
                resume_count=payload.get("resume_count", 0),
            )

        for spec in self.ports:
            # Create the continuous fact source before opening or writing to a
            # device so an early open/write failure still has a stable path.
            self.artifacts.serial_log_path(spec.port, spec.role, spec.device).touch(exist_ok=True)
            try:
                handle = self._open_port(spec)
            except Exception as error:
                self.open_failures[spec.port] = f"{type(error).__name__}: {error}"
                self.artifacts.emit(
                    "SERIAL_OPEN_BLOCKED", level="ERROR", message=f"串口 {spec.port} 无法打开。",
                    task_log=True, port=spec.port, role=spec.role or "unknown", error=str(error),
                )
                continue
            self.handles[spec.port] = handle
            self.cursors[spec.port] = 0
            self.events[spec.port] = deque(maxlen=MAX_EVENTS_PER_PORT)
            self.event_base[spec.port] = 0
            self.bytes_written[spec.port] = 0
            self.idle_polls[spec.port] = 0
            role = spec.role or "unknown"
            self.artifacts.emit("SERIAL_OPEN", message=f"串口 {spec.port} 已打开。", task_log=True, port=spec.port, role=role, baudrate=spec.baudrate)
            thread = threading.Thread(target=self._reader, args=(spec,), name=f"lstest-{spec.port}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def _reader(self, spec: PortSpec) -> None:
        role = spec.role or "unknown"
        path = self.artifacts.serial_log_path(spec.port, role, spec.device)
        try:
            with path.open("a", encoding="utf-8") as log:
                while not self.stop_event.is_set():
                    try:
                        raw = self.handles[spec.port].readline()
                    except Exception as error:
                        if self.stop_event.is_set():
                            break
                        attempts = self.reconnect_attempts.get(spec.port, 0) + 1
                        self.reconnect_attempts[spec.port] = attempts
                        self.artifacts.emit("SERIAL_RECONNECT", level="WARN", message=f"串口 {spec.port} 读取异常，尝试重连 {attempts}/3。", task_log=True, port=spec.port, error=str(error))
                        if attempts > 3:
                            raise
                        time.sleep(0.2)
                        self.handles[spec.port] = self._open_port(spec)
                        continue
                    if not raw:
                        self.idle_polls[spec.port] = self.idle_polls.get(spec.port, 0) + 1
                        continue
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    self.cursors[spec.port] += 1
                    timestamp = now_log()
                    event = SerialEvent(spec.port, role, self.cursors[spec.port], timestamp, time.monotonic(), text)
                    with self._events_lock:
                        events = self.events[spec.port]
                        if events.maxlen is not None and len(events) >= events.maxlen:
                            self.event_base[spec.port] = self.event_base.get(spec.port, 0) + 1
                        events.append(event)
                    # Each captured event occupies exactly one physical line;
                    # cursor therefore maps directly to the human log line.
                    # 前缀是 host receive time（北京时间毫秒）；设备自身时钟保留在 text 原文中。
                    rendered = f"[{timestamp}] {spec.port} {role} #{self.cursors[spec.port]} {text}\n"
                    log.write(rendered)
                    log.flush()
                    self.bytes_written[spec.port] = self.bytes_written.get(spec.port, 0) + len(rendered.encode("utf-8"))
                    self.artifacts.emit("SERIAL_LINE", message=f"{spec.port}: {text[:180]}", task_log=False, port=spec.port, role=role, cursor=self.cursors[spec.port], line=text)
                    self._maybe_heartbeat()
        except Exception as error:
            self.errors[spec.port] = f"{type(error).__name__}: {error}"
            self.artifacts.add_sticky("SERIAL_LOSS", f"串口 {spec.port} 采集失败。", port=spec.port, error=str(error))

    def _maybe_heartbeat(self) -> None:
        if self.session is None:
            return
        stamp = time.monotonic()
        with self._heartbeat_lock:
            if stamp - self._last_heartbeat < HEARTBEAT_INTERVAL_S:
                return
            self._last_heartbeat = stamp
            try:
                self.session.heartbeat(dict(self.bytes_written))
            except SerialSessionError as error:
                self.artifacts.emit(
                    "SERIAL_SESSION_HEARTBEAT_FAILED", level="WARN",
                    message=f"串口会话心跳写入失败：{error}", task_log=True,
                )

    def heartbeat(self) -> None:
        if self.session is None:
            return
        with self._heartbeat_lock:
            self._last_heartbeat = time.monotonic()
            self.session.heartbeat(dict(self.bytes_written))

    def finish(self, reason: str, detail: str = "") -> None:
        """以合法理由关闭端口：只允许 TASK_DONE / INTERRUPT。"""
        if reason not in DETACH_REASONS:
            raise IllegalDetach(f"finish(reason={reason!r}) 不是合法断开理由，只允许 {DETACH_REASONS}。")
        if self.detach_reason is not None:
            return
        self.detach_reason = reason
        self.detach_detail = detail
        if self.session is not None:
            try:
                self.session.detach(reason, detail)
            except SerialSessionError as error:
                self.artifacts.emit(
                    "SERIAL_SESSION_DETACH_FAILED", level="ERROR",
                    message=f"串口会话断开记录失败：{error}", task_log=True,
                )
        self._close(reason, detail)

    def stop(self, reason: str | None = None) -> None:
        """兼容入口：仍必须显式给出合法理由。"""
        if reason is None:
            raise IllegalDetach(
                "SerialManager.stop() 已禁止隐式断连：请调用 finish('TASK_DONE') 或 finish('INTERRUPT')。"
            )
        self.finish(reason)

    def _close(self, reason: str, detail: str) -> None:
        self.stop_event.set()
        for handle in self.handles.values():
            try:
                handle.close()
            except Exception:
                pass
        for thread in self.threads:
            thread.join(timeout=1.0)
        self.artifacts.emit(
            "SERIAL_CLOSE", message=f"分端口串口采集已停止（{reason}）。",
            task_log=True, reason=reason, detail=detail,
            cursors=dict(self.cursors), bytes=dict(self.bytes_written), errors=dict(self.errors),
        )

    def assert_detached_legally(self) -> None:
        if self.detach_reason not in DETACH_REASONS:
            raise IllegalDetach(
                f"串口仍未按合法理由断开（当前={self.detach_reason!r}）；任务未结束或未收到中断命令时不得断开。"
            )

    def write(self, command: str, port: str) -> str:
        """仅写入已打开的显式端口；回显仍由连续采集保留。"""
        if port not in self.handles:
            raise RuntimeError(f"serial port is not open: {port}")
        payload = (command.rstrip("\r\n") + "\r\n").encode("utf-8")
        handle = self.handles[port]
        handle.write(payload)
        if hasattr(handle, "flush"):
            handle.flush()
        return ""

    def snapshot(self, port: str | None = None) -> dict[str, int]:
        with self._events_lock:
            ports: Iterable[str] = (port,) if port else self.events.keys()
            return {
                name: self.event_base.get(name, 0) + len(self.events.get(name, ()))
                for name in ports
            }

    def since(self, cursors: dict[str, int], *, ports: Iterable[str] | None = None) -> list[SerialEvent]:
        with self._events_lock:
            result: list[SerialEvent] = []
            selected = set(ports) if ports is not None else set(cursors)
            # A cursor map is an explicit event scope.  This prevents a
            # single-port wait from accidentally consuming another port.
            for port, values in self.events.items():
                if selected and port not in selected:
                    continue
                base = self.event_base.get(port, 0)
                start = max(0, int(cursors.get(port, 0)) - base)
                result.extend(islice(values, start, None))
        return sorted(result, key=lambda item: item.monotonic_seconds)

    def wait_for(self, predicate: Callable[[list[SerialEvent]], bool], timeout_s: float, *, cursors: dict[str, int] | None = None) -> list[SerialEvent]:
        start = cursors or self.snapshot()
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline and not self.stop_event.is_set():
            events = self.since(start)
            if predicate(events):
                return events
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return self.since(start)

    def infer_roles(self, infer: Callable[[str], tuple[str | None, float]]) -> dict[str, dict[str, Any]]:
        """以 profile marker 推断未声明角色，永不依赖端口名称。"""
        result: dict[str, dict[str, Any]] = {}
        for spec in self.ports:
            if spec.role:
                continue
            text = "\n".join(item.line for item in self.since({spec.port: 0}, ports=(spec.port,)))
            role, confidence = infer(text)
            result[spec.port] = {"role": role, "confidence": confidence, "role_inferred": bool(role)}
            self.artifacts.emit(
                "SERIAL_ROLE_INFERENCE", level="INFO" if role else "WARN",
                message=f"串口 {spec.port} 角色{'推断为 ' + role if role else '无法推断'}。",
                task_log=True, port=spec.port, **result[spec.port],
            )
        return result
