"""Pre/post execution gate for serial tasks.

``pre`` blocks the run when the workdir is wrong, a STOP file is left over,
another task still holds the lease, a declared port is missing, or the launch
command carries a self-test window.  ``post`` blocks the result when the
session did not detach with a legal reason or no port produced bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    from .serial_session import (
        DETACH_REASONS,
        STATE_ATTACHED,
        SerialSession,
        detect_own_detach,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lstest.scripts.serial_session import (  # type: ignore
        DETACH_REASONS,
        STATE_ATTACHED,
        SerialSession,
        detect_own_detach,
    )

SELF_TEST_MARKERS = ("SELFTEST", "SELF_TEST", "_SELFTEST_SECONDS", "TEST_MODE")
WINDOWS_TASK_ROOT = Path("D:/zhsh/workspace/tasks")
LINUX_TASK_ROOT = Path("/home/zhsh/workspace/tasks")


def _check(name: str, ok: bool, detail: str = "", level: str = "block") -> Dict[str, Any]:
    return {"check": name, "ok": bool(ok), "level": level if not ok else "info",
            "detail": "" if ok else detail}


def _task_root_for(path: Path) -> Path | None:
    text = str(path).replace("\\", "/").lower()
    for root in (WINDOWS_TASK_ROOT, LINUX_TASK_ROOT):
        if text.startswith(str(root).replace("\\", "/").lower()):
            return root
    return None


def _enumerated_ports() -> List[str]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return sorted(item.device for item in list_ports.comports())


def run_pre(args: argparse.Namespace) -> Dict[str, Any]:
    task_dir = Path(args.task_dir).resolve()
    checks: List[Dict[str, Any]] = []

    root = _task_root_for(task_dir)
    checks.append(_check(
        "workdir_root", root is not None,
        f"task dir must live under {WINDOWS_TASK_ROOT} or {LINUX_TASK_ROOT}; got {task_dir}",
    ))
    checks.append(_check(
        "workdir_issue_id", task_dir.name.upper() == args.task_id.upper(),
        f"directory name {task_dir.name!r} must equal issue id {args.task_id!r}",
    ))
    checks.append(_check("task_dir_writable", os.access(str(task_dir), os.W_OK),
                         f"not writable: {task_dir}"))

    stop_path = task_dir / "logs" / "STOP"
    checks.append(_check("stop_absent", not stop_path.exists(),
                         f"leftover STOP file would end the run immediately: {stop_path}"))

    if args.command:
        upper = args.command.upper()
        hits = [marker for marker in SELF_TEST_MARKERS if marker in upper]
        checks.append(_check("no_selftest_window", not hits,
                             f"launch command carries self-test markers {hits}; "
                             "a self-test window is not a monitor window"))

    session_path = Path(args.session) if args.session else task_dir / "logs" / "serial_session.json"
    session = SerialSession(session_path, args.task_id)
    holder = session.read()
    if holder and str(holder.get("state")) == STATE_ATTACHED:
        same_owner = str(holder.get("task_id") or "") == args.task_id
        stale = session.holder_is_stale(holder)
        checks.append(_check(
            "serial_lease_free", same_owner or (stale and args.allow_stale_takeover),
            f"held by {holder.get('task_id')} heartbeat={holder.get('heartbeat_at')} "
            f"stale={stale}; report BLOCKED_SERIAL_LEASE",
        ))
    else:
        checks.append(_check("serial_lease_free", True, "no active holder"))

    if args.ports:
        declared = [item.split(":")[0].strip().upper() for item in args.ports.split(",") if item.strip()]
        present = {port.upper() for port in _enumerated_ports()}
        if present:
            missing = [port for port in declared if port.upper() not in present]
            checks.append(_check("ports_present", not missing,
                                 f"declared but not enumerated: {missing}",))
        else:
            checks.append(_check("ports_present", True,
                                 "port enumeration unavailable (pyserial missing); skipped", "info"))
    return _summarize(checks, "pre", task_dir)


def run_post(args: argparse.Namespace) -> Dict[str, Any]:
    task_dir = Path(args.task_dir).resolve()
    session_path = Path(args.session) if args.session else task_dir / "logs" / "serial_session.json"
    session = SerialSession(session_path, args.task_id)
    state = session.read() or {}
    checks: List[Dict[str, Any]] = []

    reason = state.get("detach_reason") or detect_own_detach(args)
    checks.append(_check("detach_reason_legal", reason in DETACH_REASONS,
                         f"detach_reason={reason!r}; only {DETACH_REASONS} are legal"))
    checks.append(_check("session_detached", str(state.get("state")) != STATE_ATTACHED,
                         "session still ATTACHED: the task ended without a legal detach"))
    gaps = state.get("gaps") or []
    checks.append(_check("no_resume_gap", not gaps,
                         f"session recorded {len(gaps)} gap(s): {gaps[:2]}", "warn"))

    run_dir = task_dir / "result" / args.run_name
    tool_log = run_dir / "tool.log"
    checks.append(_check("tool_log_present", tool_log.is_file(), f"missing {tool_log}", "warn"))

    offsets = {port: int(entry.get("offset") or 0)
               for port, entry in (state.get("ports") or {}).items() if isinstance(entry, dict)}
    advanced = [port for port, value in offsets.items() if value > 0]
    checks.append(_check("serial_bytes_advanced", bool(advanced),
                         f"offsets={offsets}"))
    return _summarize(checks, "post", task_dir)


def _summarize(checks: List[Dict[str, Any]], phase: str, task_dir: Path) -> Dict[str, Any]:
    blocking = [item for item in checks if not item["ok"] and item["level"] == "block"]
    warnings = [item for item in checks if not item["ok"] and item["level"] == "warn"]
    status = "BLOCKED" if blocking else ("WARN" if warnings else "PASS")
    return {
        "phase": phase,
        "task_dir": str(task_dir),
        "status": status,
        "blocking": [item["check"] for item in blocking],
        "warnings": [item["check"] for item in warnings],
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="serial task pre/post gate")
    parser.add_argument("phase", choices=["pre", "post"])
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--ports", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--run-name", default="monitor")
    parser.add_argument("--allow-stale-takeover", action="store_true")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    result = run_pre(args) if args.phase == "pre" else run_post(args)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if result["status"] == "PASS":
        return 0
    if result["status"] == "WARN":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
