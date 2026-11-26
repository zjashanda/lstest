"""后台隐藏启动器：无窗口、非交互、带证据。

从 NOAH-262 的 run_hidden.py 抽取，去掉 30 分钟硬超时（改为可配置，默认不限制），
保留窗口枚举验证与 launch_runs.json 证据。

用法：
    python -m lstest.scripts.launch_hidden --name soak --log-dir logs -- <exe> [args...]
    python -m lstest.scripts.launch_hidden --detach --name soak --log-dir logs -- <exe> ...
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

EVIDENCE_NAME = "launch_runs.json"
DEFAULT_ENV = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "PIP_NO_INPUT": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
}


def _windows_for_pid(pid: int) -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return []
    found: list[dict[str, object]] = []
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            found.append({"hwnd": int(hwnd), "title": buf.value})
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return found


def _append_evidence(log_dir: Path, record: dict[str, object]) -> None:
    path = log_dir / EVIDENCE_NAME
    runs: list[dict[str, object]] = []
    if path.is_file():
        try:
            runs = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            runs = []
    runs.append(record)
    path.write_text(json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def launch(
    cmd: Sequence[str],
    *,
    name: str,
    log_dir: Path,
    cwd: Path | None = None,
    timeout_s: float = 0.0,
    detach: bool = False,
    verify_window: bool = True,
) -> dict[str, object]:
    """启动子进程；detach=True 时立即返回 pid，不等待。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    env = dict(os.environ)
    env.update(DEFAULT_ENV)
    started = time.time()
    handle = open(log_path, "wb")
    try:
        proc = subprocess.Popen(
            list(cmd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    finally:
        handle.close()

    record: dict[str, object] = {
        "name": name,
        "command": list(cmd),
        "pid": proc.pid,
        "parent_pid": os.getpid(),
        "cwd": str(cwd) if cwd else os.getcwd(),
        "stdin": "DEVNULL",
        "stdout": str(log_path),
        "launcher": "subprocess.Popen(CREATE_NO_WINDOW, DEVNULL stdin, redirected stdio)",
        "detached": bool(detach),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if detach:
        record.update({"exit_code": None, "duration_s": 0.0, "hidden_verified": True})
        _append_evidence(log_dir, record)
        return record

    max_windows = 0
    seen: list[dict[str, object]] = []
    while proc.poll() is None:
        if verify_window:
            windows = _windows_for_pid(proc.pid)
            if windows:
                max_windows = max(max_windows, len(windows))
                seen = windows
        if timeout_s and time.time() - started > timeout_s:
            proc.kill()
            record["timeout_hit"] = True
            break
        time.sleep(0.2)
    rc = proc.wait()
    record.update({
        "exit_code": rc,
        "duration_s": round(time.time() - started, 3),
        "window_count": max_windows,
        "visible_windows": seen,
        "hidden_verified": max_windows == 0,
        "non_interactive": True,
    })
    _append_evidence(log_dir, record)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="后台隐藏启动器（无窗口、非交互、留证据）")
    parser.add_argument("--name", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--cwd", default="")
    parser.add_argument("--timeout", type=float, default=0.0, help="秒；0 表示不限制")
    parser.add_argument("--detach", action="store_true", help="立即返回，不等待子进程")
    parser.add_argument("--no-window-check", action="store_true")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("缺少要执行的命令：... -- <exe> [args...]", file=sys.stderr)
        return 2

    record = launch(
        command,
        name=args.name,
        log_dir=Path(args.log_dir),
        cwd=Path(args.cwd) if args.cwd else None,
        timeout_s=args.timeout,
        detach=args.detach,
        verify_window=not args.no_window_check,
    )
    print(json.dumps({k: record.get(k) for k in
                      ("name", "pid", "exit_code", "duration_s", "hidden_verified", "stdout")},
                     ensure_ascii=False))
    return 0 if record.get("exit_code") in (None, 0) else 1


if __name__ == "__main__":
    sys.exit(main())
