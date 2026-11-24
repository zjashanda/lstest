"""One-window serial monitor: attach once, keep reading, detach only on the two legal reasons.

Usage::

    python -m lstest.scripts.serial_monitor \
        --task-dir D:\\zhsh\\workspace\\tasks\\NOAH-411 \
        --task-id NOAH-411 --node bsz001 \
        --ports COM10:CSK6_A:115200,COM8:HISI_C:1500000,COM7:CSK6_A:115200,COM6:HISI_C:1500000 \
        --hours 1

The monitor never closes a port because the feed went quiet.  A ``logs/STOP``
file is the operator interrupt; the window deadline is ``TASK_DONE``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from .core import CaseResult, PortSpec, TaskArtifacts, now_iso
    from .serial_analysis import analyze_run, dump
    from .serial_capture import SerialManager
    from .serial_progress import ProgressScanner, verdict
    from .serial_session import (
        REASON_INTERRUPT,
        REASON_TASK_DONE,
        SerialLeaseError,
        SerialSession,
    )
except ImportError:  # direct execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lstest.scripts.core import CaseResult, PortSpec, TaskArtifacts, now_iso
    from lstest.scripts.serial_analysis import analyze_run, dump
    from lstest.scripts.serial_capture import SerialManager
    from lstest.scripts.serial_progress import ProgressScanner, verdict
    from lstest.scripts.serial_session import (
        REASON_INTERRUPT,
        REASON_TASK_DONE,
        SerialLeaseError,
        SerialSession,
    )


def parse_ports(value: str) -> list[PortSpec]:
    specs = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) < 2:
            raise SystemExit(f"bad --ports entry: {chunk!r} (want DEVICE@PORT:ROLE:BAUD)")
        head = parts[0].strip()
        device = None
        port = head
        if "@" in head:
            device, _, port = head.partition("@")
            device = device.strip() or None
            port = port.strip()
        role = parts[1].strip() or None
        baud = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 115200
        specs.append(PortSpec(port=port, baudrate=baud, role=role, device=device))
    if not specs:
        raise SystemExit("--ports is empty")
    return specs


def emit_progress(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def render_progress(entry: dict) -> str:
    lines = [
        f"\n## {entry['at']}（运行 {entry['uptime_s']}s）",
        "",
        f"- 结论：{entry['verdict']}",
        f"- 新增行数：{entry['new_lines']}",
    ]
    stages = entry.get("stages") or {}
    if stages:
        lines.append("- 阶段计数：" + "，".join(f"{k}={v}" for k, v in stages.items()))
    versions = entry.get("versions") or {}
    if versions:
        lines.append("- 版本串：" + "，".join(f"{k}×{v}" for k, v in versions.items()))
    for name, item in (entry.get("ports") or {}).items():
        lines.append(
            f"- `{name}` 新增 {item['new_lines']} 行 / {item['new_bytes']} 字节，"
            f"OTA 最大偏移 {item['max_ota_offset']}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="long-window serial monitor")
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--node", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--ports", required=True)
    parser.add_argument("--hours", type=float, default=1.0,
                        help="窗口小时数；0 表示持续运行直到 STOP 文件")
    parser.add_argument("--snapshot-interval", type=float, default=60.0)
    parser.add_argument("--progress-interval", type=float, default=300.0,
                        help="进度分析间隔（秒），默认 300 = 5 分钟")
    parser.add_argument("--allow-stale-takeover", action="store_true")
    args = parser.parse_args()

    task_dir = Path(args.task_dir)
    logs_dir = task_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stop_path = logs_dir / "STOP"
    if stop_path.exists():
        emit_progress({
            "event": "BLOCKED_STOP_PRESENT", "stop": str(stop_path),
            "content": stop_path.read_text(encoding="utf-8", errors="replace")[:200],
        })
        return 3

    session = SerialSession(
        logs_dir / "serial_session.json",
        args.task_id,
        agent=args.agent,
        node=args.node,
    )
    artifacts = TaskArtifacts(task_dir / "result", args.task_id, layout="v4")
    artifacts.freeze_cases([{"case_id": "monitor_window", "scenario": "serial_monitor"}])
    ports = parse_ports(args.ports)
    started_at_iso = now_iso()
    artifacts.write_task_manifest(
        status="running",
        started_at=started_at_iso,
        inputs={
            "mode": "serial_monitor",
            "window_hours": args.hours,
            "progress_interval_s": args.progress_interval,
            "ports": [spec.to_dict() for spec in ports],
        },
        task_meta={"agent": args.agent, "workdir": str(task_dir)},
    )

    artifacts.emit(
        "MONITOR_START",
        message=f"开始 {args.hours}h 串口长连监控",
        task_log=True, ports=[spec.to_dict() for spec in ports], stop_file=str(stop_path),
    )

    manager = SerialManager(ports, artifacts, session=session)
    try:
        manager.start()
    except SerialLeaseError as error:
        artifacts.emit(
            "BLOCKED_SERIAL_LEASE", level="ERROR",
            message=str(error), task_log=True, holder=error.holder,
        )
        emit_progress({"event": "BLOCKED_SERIAL_LEASE", "error": str(error), "holder": error.holder})
        artifacts.finalize("BLOCKED", "BLOCKED_SERIAL_LEASE")
        return 4

    opened = sorted(manager.handles)
    missing = [spec.port for spec in ports if spec.port not in manager.handles]
    emit_progress({
        "event": "MONITOR_ATTACHED", "opened": opened, "missing": missing,
        "session": str(session.path), "run_dir": str(artifacts.run_dir),
    })
    if not opened:
        manager.finish(REASON_INTERRUPT, "no port could be opened")
        artifacts.finalize("BLOCKED", "no serial port opened")
        return 5

    heartbeat_path = logs_dir / "monitor_heartbeat.jsonl"
    progress_jsonl = logs_dir / "monitor_progress.jsonl"
    progress_md = logs_dir / "progress.md"
    unbounded = args.hours <= 0
    deadline = None if unbounded else time.monotonic() + args.hours * 3600.0
    reason = ""
    detail = ""
    started = time.monotonic()
    last_snapshot = 0.0
    last_progress = 0.0
    heartbeat = heartbeat_path.open("a", encoding="utf-8")
    scanner = ProgressScanner(artifacts.run_dir / "serial_logs")
    progress_md.parent.mkdir(parents=True, exist_ok=True)

    try:
        while True:
            now = time.monotonic()
            remaining = None if deadline is None else deadline - now
            if stop_path.exists():
                reason = REASON_INTERRUPT
                detail = "STOP file: " + stop_path.read_text(encoding="utf-8", errors="replace")[:120]
                break
            if remaining is not None and remaining <= 0:
                reason = REASON_TASK_DONE
                detail = f"monitor window {args.hours}h complete"
                break
            if now - last_snapshot >= args.snapshot_interval:
                last_snapshot = now
                snapshot = {
                    "at": now_iso(),
                    "uptime_s": round(now - started, 1),
                    "remaining_s": None if remaining is None else round(remaining, 1),
                    "bytes": dict(manager.bytes_written),
                    "lines": dict(manager.cursors),
                    "idle_polls": dict(manager.idle_polls),
                    "reconnects": dict(manager.reconnect_attempts),
                    "open_failures": dict(manager.open_failures),
                }
                heartbeat.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                heartbeat.flush()
                manager.heartbeat()
                emit_progress({"event": "SNAPSHOT", **snapshot})
            if now - last_progress >= args.progress_interval:
                last_progress = now
                payload = scanner.scan()
                verdict_text = verdict(payload)
                entry = {
                    "at": now_iso(),
                    "uptime_s": round(now - started, 1),
                    "verdict": verdict_text,
                    "stages": payload["stages"],
                    "versions": payload["versions"],
                    "new_lines": payload["new_lines_total"],
                    "ports": payload["ports"],
                }
                with progress_jsonl.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                with progress_md.open("a", encoding="utf-8") as handle:
                    handle.write(render_progress(entry))
                artifacts.emit(
                    "MONITOR_PROGRESS",
                    message=verdict_text,
                    task_log=True,
                    stages=payload["stages"], versions=payload["versions"],
                    new_lines=payload["new_lines_total"],
                )
                emit_progress({"event": "PROGRESS", **{k: entry[k] for k in ("at", "uptime_s", "verdict", "stages", "versions", "new_lines")}})
            time.sleep(min(1.0, max(0.0, 5.0 if remaining is None else remaining)))
    except KeyboardInterrupt:
        reason = REASON_INTERRUPT
        detail = "keyboard interrupt"
    finally:
        heartbeat.close()

    manager.finish(reason, detail)
    session_state = session.as_dict()
    report = analyze_run(artifacts.run_dir, session_state)
    paths = dump(report, logs_dir)

    totals = report["totals"]
    status = "PASS"
    reason_text = f"{reason}: {detail}"
    if missing:
        status = "WARN"
        reason_text += f"; ports missing: {','.join(missing)}"
    if totals["ports_with_data"] == 0:
        status = "FAIL"
        reason_text += "; no port produced data"
    if session_state.get("detach_reason") not in (REASON_TASK_DONE, REASON_INTERRUPT):
        status = "FAIL"
        reason_text += "; UNEXPECTED_DETACH"

    artifacts.record_case(CaseResult(
        case_id="monitor_window",
        raw_status=status,
        reviewed_status=status,
        reason=reason_text,
        evidence=[paths["analysis_json"], paths["analysis_md"]],
        facts={"totals": totals, "ota_status": report["ota_status"], "session": session_state},
    ))
    artifacts.emit(
        "MONITOR_FINISHED",
        message=f"监控结束：{reason}",
        task_log=True, detail=detail, totals=totals, ota_status=report["ota_status"],
    )
    summary = artifacts.finalize(status, reason_text)
    artifacts.write_task_manifest(
        status=summary.get("status", status),
        reason=reason_text,
        started_at=started_at_iso,
        ended_at=now_iso(),
        inputs={
            "mode": "serial_monitor",
            "window_hours": args.hours,
            "progress_interval_s": args.progress_interval,
            "ports": [spec.to_dict() for spec in ports],
        },
        lifecycle={"session": session_state, "reconnects": dict(manager.reconnect_attempts), "open_failures": dict(manager.open_failures)},
        summary={"totals": totals, "ota_status": report["ota_status"], "counts": summary.get("counts", {})},
        task_meta={"agent": args.agent, "workdir": str(task_dir)},
    )
    emit_progress({
        "event": "MONITOR_RESULT", "status": summary.get("status"),
        "reason": reason_text, "ota_status": report["ota_status"],
        "totals": totals, "analysis": paths,
    })
    return 0 if status == "PASS" else (2 if status == "WARN" else 1)


if __name__ == "__main__":
    sys.exit(main())
