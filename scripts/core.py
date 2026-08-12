"""Core task, connection, evidence, and scenario contracts for lstest."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

try:
    from .observations import RawTag, ToolJudgement, raw_tags_from_mappings, render_observation
except ImportError:  # direct execution fallback
    from observations import RawTag, ToolJudgement, raw_tags_from_mappings, render_observation

BEIJING = timezone(timedelta(hours=8))
STICKY_FATAL_MARKERS = {
    "PANIC", "CRASH", "ASSERT", "WATCHDOG_RESET", "UNEXPECTED_REBOOT",
    "DATA_CORRUPTION", "SERIAL_LOSS", "TOOL_EXCEPTION",
}
NORMAL_CASE_STATUSES = {"PASS", "EXPECTED"}


def now_dt() -> datetime:
    return datetime.now(BEIJING)


def now_iso() -> str:
    return now_dt().isoformat(timespec="milliseconds")


def now_human() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class PortSpec:
    port: str
    baudrate: int
    role: str | None = None
    capabilities: tuple[str, ...] = ()
    required_for: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        result["required_for"] = list(self.required_for)
        return result


@dataclass(frozen=True)
class ConnectionSpec:
    ports: tuple[PortSpec, ...] = ()
    playback_device_key: str | None = None
    capture_device_key: str | None = None
    profile_id: str | None = None
    result_root: Path = Path("result")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ports": [item.to_dict() for item in self.ports],
            "playback_device_key": self.playback_device_key,
            "playback_target_mode": "specified_device_key" if self.playback_device_key else "system_default_render",
            "capture_device_key": self.capture_device_key,
            "profile_id": self.profile_id,
            "result_root": str(self.result_root),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ConnectionSpec":
        raw_ports = payload.get("ports") or []
        ports: list[PortSpec] = []
        for raw in raw_ports:
            if isinstance(raw, str):
                parts = raw.split(":")
                raw = {"port": parts[0], "role": parts[1] if len(parts) > 1 else None}
            if not isinstance(raw, Mapping):
                raise ValueError("each port must be a string or object")
            port = str(raw.get("port", "")).strip()
            if not port:
                raise ValueError("port cannot be empty")
            baudrate = int(raw.get("baudrate", payload.get("baudrate", 0)))
            if baudrate <= 0:
                raise ValueError(f"invalid baudrate for {port}")
            ports.append(PortSpec(
                port=port,
                baudrate=baudrate,
                role=str(raw["role"]).strip() if raw.get("role") else None,
                capabilities=tuple(str(value) for value in raw.get("capabilities", ())),
                required_for=tuple(str(value) for value in raw.get("required_for", ())),
            ))
        return cls(
            ports=tuple(ports),
            playback_device_key=payload.get("playback_device_key"),
            capture_device_key=payload.get("capture_device_key"),
            profile_id=payload.get("profile_id"),
            result_root=Path(payload.get("result_root", "result")),
        )


@dataclass
class CaseResult:
    case_id: str
    raw_status: str
    reviewed_status: str | None = None
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    text: str = ""
    audio_path: str | None = None
    domain: str | None = None
    intent: str | None = None
    action: str | None = None
    destructive: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ObservedFacts:
    case_id: str
    markers: list[dict[str, Any]] = field(default_factory=list)
    wake: dict[str, Any] = field(default_factory=dict)
    asr: dict[str, Any] = field(default_factory=dict)
    business: dict[str, Any] = field(default_factory=dict)
    player: dict[str, Any] = field(default_factory=dict)
    correlation: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


class Scenario(Protocol):
    """Scenario contract implemented by project adapters."""

    def preflight(self, runtime: "DeviceRuntime") -> None: ...
    def next_case(self, state: Any) -> CaseSpec | None: ...
    def run_case(self, case: CaseSpec, runtime: "DeviceRuntime") -> ObservedFacts: ...
    def judge(self, observed: ObservedFacts) -> CaseResult: ...
    def cleanup(self, runtime: "DeviceRuntime") -> None: ...


class StopSupervisor:
    def __init__(self, stop_file: Path, *, disk_limit_bytes: int = 1024**3):
        self.stop_file = stop_file
        self.disk_limit_bytes = disk_limit_bytes
        self._event = threading.Event()
        self.reason = ""

    def request(self, reason: str = "requested") -> None:
        self.reason = reason
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set() or self.stop_file.is_file()

    def disk_ok(self, path: Path) -> bool:
        try:
            return path.stat().st_size >= 0 and shutil.disk_usage(path).free > self.disk_limit_bytes
        except OSError:
            return False

    def check(self, path: Path | None = None) -> str | None:
        """返回停止原因；磁盘低于停止线时阻止新的用例。"""
        if self.stop_file.is_file():
            return self.reason or "STOP_FILE"
        if path is not None and not self.disk_ok(path):
            self.request("DISK_STOP_LINE")
            return self.reason
        return self.reason if self.requested else None

    def wait(self, seconds: float, *, tick: float = 0.1, path: Path | None = None) -> str | None:
        """可中断、有上限的等待；永不向 time.sleep 传递负数。"""
        duration = max(0.0, float(seconds))
        deadline = time.monotonic() + duration
        while True:
            reason = self.check(path)
            if reason:
                return reason
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._event.wait(min(max(0.01, tick), remaining))

    def raise_if_requested(self, path: Path | None = None) -> None:
        reason = self.check(path)
        if reason:
            raise RuntimeError(reason)


class TaskArtifacts:
    """Incrementally writes standard evidence without creating merged serial logs."""

    def __init__(self, result_root: Path, task_slug: str, detail_fields: Sequence[str] = ()):
        stamp = now_dt().strftime("%Y%m%d_%H%M%S")
        base = Path(result_root) / f"{stamp}_{task_slug}"
        candidate = base
        suffix = 1
        while candidate.exists():
            candidate = Path(f"{base}_{suffix:02d}")
            suffix += 1
        self.run_dir = candidate
        for name in ("serial_logs", "evidence", "tool_logs"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=False)
        self.task_log_path = self.run_dir / "task.log"
        self.events_path = self.run_dir / "task_events.jsonl"
        self.errors_path = self.run_dir / "errors.log"
        self.progress_path = self.run_dir / "progress.json"
        self.results_path = self.run_dir / "results.csv"
        self.summary_json = self.run_dir / "summary_final.json"
        self.summary_md = self.run_dir / "summary_final.md"
        self.stop = StopSupervisor(self.run_dir / "STOP")
        self._lock = threading.RLock()
        self._task = self.task_log_path.open("a", encoding="utf-8")
        self._events = self.events_path.open("a", encoding="utf-8")
        self._errors = self.errors_path.open("a", encoding="utf-8")
        self._csv = None
        self._writer = None
        fields = list(detail_fields)
        if fields:
            self._csv = self.results_path.open("w", encoding="utf-8-sig", newline="")
            self._writer = csv.DictWriter(self._csv, fieldnames=fields, extrasaction="ignore")
            self._writer.writeheader()
            self._csv.flush()
        self.counts: dict[str, int] = {}
        # This task-scoped state is not owned by a round or serial connection,
        # so device reconnects/reboots never reset the accumulated count.
        self.exception_counts: dict[str, int] = {}
        self.anomaly_counts: dict[str, int] = {}
        self.completed_cases = 0
        self.sticky: list[dict[str, Any]] = []
        self.capabilities: dict[str, dict[str, Any]] = {}
        self._closers: list[tuple[str, Any]] = []
        self.closed = False

    def configure(self, payload: Mapping[str, Any]) -> None:
        atomic_json(self.run_dir / "task_config.json", {**payload, "started_at": now_iso(), "result_directory": str(self.run_dir)})

    def write_tool_log(self, name: str, content: str, *, append: bool = True) -> Path:
        """保存播放器、在线客户端或其他工具的原始输出。"""
        safe_name = Path(name).name
        path = self.run_dir / "tool_logs" / safe_name
        mode = "a" if append else "w"
        with self._lock:
            with path.open(mode, encoding="utf-8", errors="replace") as handle:
                handle.write(content)
                if content and not content.endswith("\n"):
                    handle.write("\n")
        return path

    def append_tool_jsonl(self, name: str, payload: Mapping[str, Any]) -> Path:
        """线程安全地追加一条结构化工具证据。"""
        safe_name = Path(name).name
        path = self.run_dir / "tool_logs" / safe_name
        line = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path

    def emit(self, event: str, *, message: str = "", level: str = "INFO", task_log: bool = True, **fields: Any) -> None:
        payload = {"at": now_iso(), "event": event, "level": level, "message": message, **fields}
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self._events.write(line + "\n")
            self._events.flush()
            if level in {"ERROR", "CRITICAL"}:
                self._errors.write(line + "\n")
                self._errors.flush()
            if task_log:
                text = f"{now_human()} [{level}] {message or event}"
                self._task.write(text + "\n")
                self._task.flush()
                print(text, flush=True)

    def emit_observation(
        self,
        channel: str,
        tags: Sequence[RawTag | Mapping[str, Any]],
        *,
        event: str = "OBSERVED_TAGS",
        normalized: Mapping[str, Any] | None = None,
        judgement: ToolJudgement | None = None,
        level: str = "INFO",
        task_log: bool = True,
        **fields: Any,
    ) -> dict[str, Any]:
        """同时保存设备原始标签和工具侧判断，并镜像关键行。"""
        raw_tags = raw_tags_from_mappings(tags)
        normalized_payload = dict(normalized or {})
        judgement_payload = judgement.to_dict() if judgement else {}
        message = render_observation(channel, raw_tags, normalized=normalized_payload, judgement=judgement)
        event_fields = {
            "channel": channel,
            "raw_tags": [item.to_dict() for item in raw_tags],
            "normalized": normalized_payload,
            "tool_judgement": judgement_payload,
            **fields,
        }
        if judgement:
            event_fields.update({
                "tool_status": judgement.tool_status,
                "tool_reason": judgement.tool_reason,
                "expected": dict(judgement.expected),
                "actual": dict(judgement.actual),
                "duration_ms": judgement.duration_ms,
                "evidence_refs": list(judgement.evidence_refs),
            })
        self.emit(event, message=message, level=level, task_log=task_log, **event_fields)
        return event_fields

    def record_case(self, result: CaseResult) -> None:
        status = result.reviewed_status or result.raw_status
        self.counts[status] = self.counts.get(status, 0) + 1
        self.completed_cases += 1
        if status not in NORMAL_CASE_STATUSES:
            self.exception_counts[status] = self.exception_counts.get(status, 0) + 1
        with self._lock:
            if self._writer is not None:
                self._writer.writerow({"case_id": result.case_id, "raw_status": result.raw_status, "reviewed_status": result.reviewed_status or "", "reason": result.reason, "facts": json.dumps(result.facts, ensure_ascii=False), "evidence": json.dumps(result.evidence, ensure_ascii=False)})
            if self._csv is not None:
                self._csv.flush()
        self.emit("CASE_RESULT", message=f"用例 {result.case_id}: {status}；{result.reason}".rstrip("；"), case=result.to_dict())
        self._record_round_exception_summary(result.case_id, status)

    def _record_round_exception_summary(self, case_id: str, status: str) -> None:
        """Print and persist the cumulative, task-level exception snapshot."""
        snapshot = {
            "completed_cases": self.completed_cases,
            "current_case_id": case_id,
            "current_status": status,
            "exception_counts": dict(sorted(self.exception_counts.items())),
            "exception_total": sum(self.exception_counts.values()),
            "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
            "anomaly_total": sum(self.anomaly_counts.values()),
            "sticky_counts": self._sticky_counts(),
        }
        message = (
            f"[EXCEPTION_SUMMARY] round={self.completed_cases} "
            f"current={case_id}:{status} "
            f"cumulative={json.dumps(snapshot['exception_counts'], ensure_ascii=False)} "
            f"total={snapshot['exception_total']} "
            f"anomalies={json.dumps(snapshot['anomaly_counts'], ensure_ascii=False)} "
            f"anomaly_total={snapshot['anomaly_total']} "
            f"sticky={json.dumps(snapshot['sticky_counts'], ensure_ascii=False)}"
        )
        self.emit("ROUND_EXCEPTION_SUMMARY", message=message, **snapshot)
        # One blank line separates adjacent rounds in the human-readable tool log.
        self.write_tool_log(
            "exception_summary.log",
            f"{now_human()} {message}\n{json.dumps(snapshot, ensure_ascii=False, sort_keys=True)}\n\n",
        )

    def _sticky_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.sticky:
            code = str(item.get("code") or "UNKNOWN")
            counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    def update_progress(self, *, completed: int, target: int | None = None, **fields: Any) -> None:
        payload = {
            "updated_at": now_iso(),
            "completed": completed,
            "target": target,
            "exception_counts": dict(sorted(self.exception_counts.items())),
            "exception_total": sum(self.exception_counts.values()),
            "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
            "anomaly_total": sum(self.anomaly_counts.values()),
            "sticky_counts": self._sticky_counts(),
            **fields,
        }
        atomic_json(self.progress_path, payload)

    def set_capability(self, name: str, status: str, reason: str = "", **fields: Any) -> None:
        self.capabilities[name] = {"status": status, "reason": reason, **fields}
        self.emit("CAPABILITY", message=f"能力 {name}: {status}；{reason}".rstrip("；"), task_log=True, capability=name, status=status, reason=reason, **fields)

    def add_sticky(self, code: str, message: str, **fields: Any) -> None:
        record = {"code": code, "message": message, "at": now_iso(), **fields}
        self.sticky.append(record)
        self.emit("STICKY_FATAL", level="ERROR", message=message, code=code, at=record["at"], **fields)

    def record_anomaly(self, code: str, message: str, **fields: Any) -> dict[str, Any]:
        """Record a recoverable task anomaly without hiding it behind a later pass."""
        normalized_code = str(code or "UNKNOWN_ANOMALY").strip().upper() or "UNKNOWN_ANOMALY"
        self.anomaly_counts[normalized_code] = self.anomaly_counts.get(normalized_code, 0) + 1
        record = {
            "code": normalized_code,
            "message": message,
            "at": now_iso(),
            "count": self.anomaly_counts[normalized_code],
            **fields,
        }
        self.emit("TASK_ANOMALY", level="ERROR", **record)
        return record

    def register_closer(self, name: str, closer: Any) -> None:
        """登记可调用的资源关闭函数，按逆序执行且每项有界。"""
        if callable(closer):
            self._closers.append((name, closer))

    def check_runtime(self) -> str | None:
        return self.stop.check(self.run_dir)

    def finalize(self, status: str, reason: str) -> dict[str, Any]:
        if self.closed:
            return json.loads(self.summary_json.read_text(encoding="utf-8")) if self.summary_json.is_file() else {"status": status, "reason": reason}
        # 先收尾设备资源，再生成最终汇总；否则 closer 失败无法进入 summary。
        for name, closer in reversed(self._closers):
            try:
                closer()
            except Exception as error:  # cleanup must remain visible, never hide the result
                self.add_sticky("TOOL_EXCEPTION", f"资源收尾失败: {name}", resource=name, error=str(error))
        final_status = status
        final_reason = reason
        if self.sticky and final_status in {"PASS", "WARN"}:
            final_status = "FAIL"
            final_reason = f"{reason}；存在 sticky fatal: {self.sticky[-1].get('code', 'UNKNOWN')}"
        summary = {
            "status": final_status,
            "reason": final_reason,
            "ended_at": now_iso(),
            "counts": self.counts,
            "exception_counts": dict(sorted(self.exception_counts.items())),
            "exception_total": sum(self.exception_counts.values()),
            "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
            "anomaly_total": sum(self.anomaly_counts.values()),
            "sticky_counts": self._sticky_counts(),
            "capabilities": self.capabilities,
            "sticky_failures": self.sticky,
            "result_directory": str(self.run_dir),
        }
        atomic_json(self.summary_json, summary)
        self.summary_md.write_text(
            f"# lstest 任务汇总\n\n- 状态：`{final_status}`\n- 原因：{final_reason}\n"
            f"- 各状态数量：{json.dumps(self.counts, ensure_ascii=False)}\n"
            f"- 全程累计异常：{json.dumps(summary['exception_counts'], ensure_ascii=False)}\n"
            f"- 全程累计异常总数：{summary['exception_total']}\n"
            f"- 全程累计异常事件：{json.dumps(summary['anomaly_counts'], ensure_ascii=False)}\n"
            f"- 全程累计异常事件总数：{summary['anomaly_total']}\n"
            f"- Sticky 严重异常：{json.dumps(summary['sticky_counts'], ensure_ascii=False)}\n"
            f"- 证据目录：`{self.run_dir}`\n",
            encoding="utf-8",
        )
        self.emit("TASK_FINISHED", message=f"任务结束: {final_status}；{final_reason}", status=final_status, task_log=True)
        for handle in (self._task, self._events, self._errors, self._csv):
            if handle is not None:
                handle.close()
        self.closed = True
        return summary


class DeviceRuntime:
    """Small contract object; concrete serial/profile/playback adapters are injected."""

    def __init__(self, connection: ConnectionSpec, profile: Any, artifacts: TaskArtifacts):
        self.connection = connection
        self.profile = profile
        self.artifacts = artifacts
        self.started = False
        self.restart_recovery_monitor: Any | None = None

    def preflight(self) -> dict[str, Any]:
        self.artifacts.emit("PREFLIGHT", message="开始公共设备前置检查。", task_log=True, ports=self.connection.to_dict()["ports"])
        return {"ports": len(self.connection.ports), "profile": getattr(self.profile, "profile_id", None)}

    def start_restart_recovery(
        self,
        manager: Any,
        *,
        initialization_timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> Any:
        """Continuously recover profile initialization after restart markers."""
        if self.restart_recovery_monitor is not None:
            return self.restart_recovery_monitor
        try:
            from .shell import ProfileRestartRecoveryMonitor
        except ImportError:  # direct execution fallback
            from shell import ProfileRestartRecoveryMonitor
        monitor = ProfileRestartRecoveryMonitor(
            self.profile,
            self.artifacts,
            manager,
            initialization_timeout_s=initialization_timeout_s,
            poll_interval_s=poll_interval_s,
        )
        monitor.start()
        self.restart_recovery_monitor = monitor
        self.started = True
        return monitor

    def recover_initialization(
        self,
        manager: Any,
        *,
        cursors: Mapping[str, int] | None = None,
        recovery_reason: str = "startup",
    ) -> dict[str, Any]:
        """Verify all profile initialization commands after a completed boot.

        A successful first recovery also starts background restart monitoring.
        Commands and validation rules always come from the project profile.
        """
        try:
            from .shell import ProfileRecoveryStateMachine
        except ImportError:  # direct execution fallback
            from shell import ProfileRecoveryStateMachine
        recovery = self.profile.recovery if hasattr(self.profile, "recovery") else {}
        timeout_s = max(0.1, float(recovery.get("initialization_timeout_s", 10.0)))
        poll_interval_s = max(0.02, float(recovery.get("restart_poll_interval_s", 0.1)))
        result = ProfileRecoveryStateMachine(self.profile, self.artifacts, manager).run(
            timeout_s,
            cursors=cursors,
            recovery_reason=recovery_reason,
        )
        if result.get("status") == "PASS":
            self.start_restart_recovery(
                manager,
                initialization_timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
            )
        return result

    def check_ready(self) -> str | None:
        """Return a restart/recovery stop reason before a project starts new work."""
        return self.artifacts.check_runtime()

    def close(self) -> None:
        if self.restart_recovery_monitor is not None:
            self.restart_recovery_monitor.stop()
            self.restart_recovery_monitor = None
        if self.started:
            self.artifacts.emit("RUNTIME_CLOSE", message="公共运行时已收尾。", task_log=True)
            self.started = False
