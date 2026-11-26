"""长任务引擎：调试 / 短测 / 压测 / 长监控 共用一套实现。

场景只是参数组合，不是三套代码：

    --rounds 1                 调试（默认，最安全）
    --rounds 3                 短测
    --rounds 500               压测（自动转后台 + 里程碑）
    --duration 4h              定时长压
    --infinite                 长监控（必须显式声明）

引擎负责：循环、断点、里程碑、停止文件、磁盘下限、心跳、隐藏启动。
任务负责：只提供「跑一轮」的命令，用 {round} 占位。

用法：
    python -m lstest.scripts.longrun start --name soak --work-dir work \
        --rounds 500 --issue <issue-id> -- python work/one_round.py --round {round}

    python -m lstest.scripts.longrun status --work-dir work --name soak
    python -m lstest.scripts.longrun stop   --work-dir work --name soak
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

STATE_SCHEMA = "lstest.longrun.state.v1"
DEFAULTS = {
    "rounds": 1,
    "milestone_every": 50,
    "min_free_gb": 3.0,
    "round_timeout_s": 0,
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_duration(text: str) -> float:
    """把 30m / 4h / 90s 解析成秒。"""
    raw = str(text).strip().lower()
    if not raw:
        return 0.0
    unit = raw[-1]
    number = raw[:-1] if unit in "smh" else raw
    try:
        value = float(number)
    except ValueError as error:
        raise ValueError(f"无法解析时长 {text!r}") from error
    factor = {"s": 1, "m": 60, "h": 3600}.get(unit, 1)
    return value * factor


def state_path(work_dir: Path, name: str) -> Path:
    return work_dir / f"longrun_{name}.state.json"


def stop_path(work_dir: Path, name: str) -> Path:
    return work_dir / f"STOP_{name}"


def milestones_path(work_dir: Path, name: str) -> Path:
    return work_dir / f"longrun_{name}.milestones.jsonl"


def round_log_path(work_dir: Path, name: str, index: int) -> Path:
    return work_dir / "rounds" / f"{name}_{index:05d}.log"


def write_state(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return -1.0


def post_milestone(issue: str, text: str, multica: str | None) -> bool:
    """把里程碑写进 Multica Issue；失败只记录，不中断压测。"""
    exe = multica or os.environ.get("MULTICA_CLI_PATH", "")
    if not issue or not exe or not Path(exe).exists():
        return False
    try:
        res = subprocess.run(
            [exe, "issue", "comment", "add", issue, "--content", text, "--output", "json"],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        return res.returncode == 0
    except Exception:  # noqa: BLE001 - 上报失败不影响压测
        return False


def run_round(command: Sequence[str], index: int, work_dir: Path, name: str,
              timeout_s: float) -> tuple[int, Path]:
    """执行一轮；返回 (exit_code, 该轮日志路径)。"""
    log = round_log_path(work_dir, name, index)
    log.parent.mkdir(parents=True, exist_ok=True)
    rendered = [str(part).replace("{round}", str(index)).replace("{name}", name) for part in command]
    with log.open("wb") as handle:
        try:
            proc = subprocess.run(
                rendered, stdout=handle, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, timeout=timeout_s or None,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            return proc.returncode, log
        except subprocess.TimeoutExpired:
            return 124, log


def run_loop(
    *,
    name: str,
    work_dir: Path,
    command: Sequence[str],
    rounds: int | None,
    duration_s: float,
    infinite: bool,
    start: int,
    milestone_every: int,
    min_free_gb: float,
    round_timeout_s: float,
    interval_s: float,
    issue: str,
    multica: str | None,
) -> int:
    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_path(work_dir, name)
    stop_file = stop_path(work_dir, name)
    mile_file = milestones_path(work_dir, name)

    previous = read_state(state_file)
    if start <= 0:
        start = int(previous.get("completed") or 0) + 1

    target = None if infinite or rounds is None else int(rounds)
    state: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "name": name,
        "status": "running",
        "mode": "infinite" if infinite else ("duration" if duration_s else "rounds"),
        "started_at": previous.get("started_at") or now_text(),
        "updated_at": now_text(),
        "ended_at": None,
        "target_rounds": target,
        "target_duration_s": duration_s or None,
        "completed": start - 1,
        "failed": int(previous.get("failed") or 0),
        "blocked": 0,
        "start_round": start,
        "last_exit": None,
        "last_error": "",
        "next_milestone": max(milestone_every, ((start - 1) // milestone_every + 1) * milestone_every),
        "pid": os.getpid(),
        "work_dir": str(work_dir),
    }
    write_state(state_file, state)

    begin = time.time()
    index = start
    exit_code = 0
    milestone_every = max(1, int(milestone_every))

    while True:
        if stop_file.is_file():
            state["status"] = "stopped"
            state["last_error"] = "STOP 文件"
            break
        if target is not None and index > target:
            state["status"] = "completed"
            break
        if duration_s and (time.time() - begin) >= duration_s:
            state["status"] = "completed"
            state["last_error"] = "达到时长上限"
            break
        free = disk_free_gb(work_dir)
        if 0 <= free < min_free_gb:
            state["status"] = "blocked"
            state["last_error"] = f"磁盘剩余 {free:.1f}GB 低于门限 {min_free_gb}GB"
            break

        rc, _log = run_round(command, index, work_dir, name, round_timeout_s)
        state["completed"] = index
        state["last_exit"] = rc
        state["updated_at"] = now_text()
        if rc != 0:
            state["failed"] = int(state["failed"]) + 1
            state["last_error"] = f"第 {index} 轮 exit={rc}"
        write_state(state_file, state)

        if index >= int(state["next_milestone"]) or target == index:
            record = {
                "round": index,
                "completed": state["completed"],
                "failed": state["failed"],
                "at": now_text(),
                "elapsed_s": round(time.time() - begin, 1),
            }
            with mile_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            text = (f"【{name}】第 {index} 轮完成：累计 {state['completed']} 轮，"
                    f"失败 {state['failed']}，用时 {record['elapsed_s']}s")
            post_milestone(issue, text, multica)
            state["next_milestone"] = int(index) + milestone_every
            write_state(state_file, state)

        index += 1
        if interval_s:
            time.sleep(interval_s)

    state["ended_at"] = now_text()
    state["updated_at"] = state["ended_at"]
    write_state(state_file, state)
    summary = {
        "name": name, "status": state["status"], "completed": state["completed"],
        "failed": state["failed"], "last_error": state["last_error"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if state["status"] == "completed" else 1


def cmd_status(work_dir: Path, name: str) -> int:
    state = read_state(state_path(work_dir, name))
    if not state:
        print(json.dumps({"name": name, "status": "absent"}, ensure_ascii=False))
        return 1
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def cmd_stop(work_dir: Path, name: str) -> int:
    stop = stop_path(work_dir, name)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text(f"stop requested {now_text()}\n", encoding="utf-8")
    print(json.dumps({"name": name, "stop_file": str(stop)}, ensure_ascii=False))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="长任务引擎（调试/短测/压测/长监控共用）")
    sub = parser.add_subparsers(dest="action", required=True)

    start = sub.add_parser("start", help="开始或续跑")
    start.add_argument("--name", required=True)
    start.add_argument("--work-dir", required=True)
    start.add_argument("--rounds", type=int, default=DEFAULTS["rounds"],
                       help=f"轮次上限，默认 {DEFAULTS['rounds']}；--infinite 时忽略")
    start.add_argument("--duration", default="", help="时长上限，如 30m / 4h")
    start.add_argument("--infinite", action="store_true", help="显式长跑，无轮次上限")
    start.add_argument("--start", type=int, default=0, help="从第几轮开始，0=自动续跑")
    start.add_argument("--milestone-every", type=int, default=DEFAULTS["milestone_every"])
    start.add_argument("--min-free-gb", type=float, default=DEFAULTS["min_free_gb"])
    start.add_argument("--round-timeout", type=float, default=DEFAULTS["round_timeout_s"])
    start.add_argument("--interval", default="0s", help="轮次间隔，如 60s")
    start.add_argument("--issue", default="", help="里程碑上报的 Multica Issue")
    start.add_argument("--multica", default="", help="multica 可执行文件路径")
    start.add_argument("cmd", nargs=argparse.REMAINDER)

    status = sub.add_parser("status", help="查看状态")
    status.add_argument("--name", required=True)
    status.add_argument("--work-dir", required=True)

    stop = sub.add_parser("stop", help="停止")
    stop.add_argument("--name", required=True)
    stop.add_argument("--work-dir", required=True)

    args = parser.parse_args(argv)

    if args.action == "status":
        return cmd_status(Path(args.work_dir), args.name)
    if args.action == "stop":
        return cmd_stop(Path(args.work_dir), args.name)

    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("缺少每轮命令：... -- <exe> [args...]，可用 {round} 占位", file=sys.stderr)
        return 2
    try:
        duration_s = parse_duration(args.duration) if args.duration else 0.0
        interval_s = parse_duration(args.interval) if args.interval else 0.0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    return run_loop(
        name=args.name,
        work_dir=Path(args.work_dir),
        command=command,
        rounds=args.rounds,
        duration_s=duration_s,
        infinite=args.infinite,
        start=args.start,
        milestone_every=args.milestone_every,
        min_free_gb=args.min_free_gb,
        round_timeout_s=args.round_timeout,
        interval_s=interval_s,
        issue=args.issue,
        multica=args.multica,
    )


if __name__ == "__main__":
    sys.exit(main())
