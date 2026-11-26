"""收尾门禁：用一条命令判定任务产物是否合规。

不通过就不得报完成。把「手册里的规矩」变成「跑不过就失败」。

用法：
    python -m lstest.scripts.finish_gate --task-dir D:\\zhsh\\workspace\\tasks\\NOAH-415
    python -m lstest.scripts.finish_gate --task-dir ... --run 202611251651 --json

退出码：0 = 通过；2 = 不通过（有 ERROR）；1 = 无法判定（缺目录）。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

STAMP_RE = re.compile(r"^\d{12}(-\d{2})?$")
LEDGER_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (DEBUG|INFO|WARN|ERROR) +- \[[A-Z]+\] ")
BARE_MARKER_RE = re.compile(r"^\s{2}\S+\(COM\d+\): [A-Za-z][A-Za-z0-9_]*: ")
LEGACY_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)? \[[A-Z]+\]")
MARK_RE = re.compile(r"\[MARK\]\s+\S+\([^)]*\):\s+([A-Za-z][A-Za-z0-9_]*):")
CORE_ITEMS = ("tool.log", "cases.csv", "results.csv", "serial_logs")


class Gate:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warns: list[dict[str, str]] = []

    def error(self, code: str, message: str, fix: str = "") -> None:
        self.errors.append({"code": code, "message": message, "fix": fix})

    def warn(self, code: str, message: str, fix: str = "") -> None:
        self.warns.append({"code": code, "message": message, "fix": fix})


def catalog_codes(repo_root: Path) -> set[str]:
    path = repo_root / "lstest" / "markers" / "catalog.json"
    if not path.is_file():
        alt = Path(__file__).resolve().parent.parent / "markers" / "catalog.json"
        path = alt if alt.is_file() else path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(item.get("code")) for item in payload.get("markers", []) if item.get("code")}


def check_layout(gate: Gate, task_dir: Path) -> Path | None:
    result = task_dir / "result"
    if not result.is_dir():
        gate.error("NO_RESULT_DIR", "任务目录下没有 result/", "确认任务目录是否正确")
        return None
    runs = sorted(p for p in result.iterdir() if p.is_dir() and STAMP_RE.match(p.name))
    extra = [p.name for p in result.iterdir() if p.is_dir() and not STAMP_RE.match(p.name)]
    if extra:
        gate.error("RESULT_ROOT_EXTRA", f"result/ 下存在非时间戳目录: {', '.join(sorted(extra))}",
                   "只保留 result/<YYYYmmddHHMM>/，删除副本目录")
    if not runs:
        gate.error("NO_RUN_DIR", "result/ 下没有时间戳运行目录", "确认脚本用 TaskArtifacts v4 布局")
        return None
    return runs[-1]


def check_run_items(gate: Gate, run_dir: Path) -> None:
    actual = {p.name for p in run_dir.iterdir()}
    missing = [name for name in CORE_ITEMS if name not in actual]
    if missing:
        gate.error("MISSING_ITEMS", f"运行目录缺六项中的: {', '.join(missing)}", "补齐后重新收尾")
    allowed = set(CORE_ITEMS) | {"burn_logs", "task.json", "STOP", "events", "reports"}
    unexpected = actual - allowed
    if unexpected:
        gate.error("EXTRA_ITEMS", f"运行目录存在非标准产物: {', '.join(sorted(unexpected))}",
                   "artifacts/ 等自定义目录必须移出运行目录")


def check_tool_log(gate: Gate, run_dir: Path) -> None:
    path = run_dir / "tool.log"
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    bare = [i + 1 for i, line in enumerate(lines) if BARE_MARKER_RE.match(line)]
    legacy = [i + 1 for i, line in enumerate(lines) if LEGACY_RE.match(line) and not LEDGER_RE.match(line)]
    sha = [i + 1 for i, line in enumerate(lines) if "sha256=" in line]
    if bare:
        gate.error("BARE_MARKER", f"{len(bare)} 行标记没有时间戳（首个第 {bare[0]} 行）",
                   "标记必须经 artifacts.emit_markers() 写入")
    if legacy:
        gate.error("LEGACY_FORMAT", f"{len(legacy)} 行是旧格式（首个第 {legacy[0]} 行）",
                   "统一走 ledger 渲染器")
    if sha:
        gate.warn("SHA_IN_LOG", f"{len(sha)} 行含 sha256（首个第 {sha[0]} 行）", "校验值只放 task.json")


def check_task_json(gate: Gate, task_dir: Path, run_dir: Path) -> None:
    path = run_dir / "task.json"
    if not path.is_file():
        gate.warn("NO_TASK_JSON", "运行目录没有 task.json", "收尾必须写 task.json")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        gate.error("BAD_TASK_JSON", "task.json 不是合法 JSON", "检查写入逻辑")
        return
    task = payload.get("task") or {}
    task_id = str(task.get("task_id") or "")
    if not task_id:
        gate.error("NO_TASK_ID", "task.json 缺少 task.task_id", "写入任务编号")
    elif task_id != task_dir.name:
        gate.error("TASK_ID_MISMATCH", f"task_id={task_id} ≠ 任务目录名 {task_dir.name}",
                   "task_id 必须等于任务目录名（Issue 编号）")
    if not task.get("status"):
        gate.warn("NO_STATUS", "task.json 缺少 task.status", "写 PASS/FAIL/BLOCKED 等")


def check_marker_codes(gate: Gate, run_dir: Path, codes: set[str]) -> None:
    path = run_dir / "tool.log"
    if not path.is_file() or not codes:
        return
    used = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = MARK_RE.search(line)
        if match:
            used.add(match.group(1))
    unknown = sorted(code for code in used if code not in codes)
    if unknown:
        gate.error("UNKNOWN_MARKER_CODE", f"标记 code 不在码表: {', '.join(unknown[:8])}",
                   "改用 markers/catalog.json 里的 code，缺的先补码表")


def check_cases_results(gate: Gate, run_dir: Path) -> None:
    cases = run_dir / "cases.csv"
    results = run_dir / "results.csv"
    if not cases.is_file() or not results.is_file():
        return
    try:
        case_rows = list(csv.DictReader(cases.read_text(encoding="utf-8-sig").splitlines()))
        result_rows = list(csv.DictReader(results.read_text(encoding="utf-8-sig").splitlines()))
    except (OSError, ValueError):
        return
    case_ids = {str(r.get("case_id") or "").strip() for r in case_rows} - {""}
    done_ids = {str(r.get("case_id") or "").strip() for r in result_rows} - {""}
    missing = sorted(case_ids - done_ids)
    if missing:
        gate.warn("CASE_WITHOUT_RESULT", f"{len(missing)} 个用例没有终态: {', '.join(missing[:5])}",
                  "results.csv 每用例必须一行")


def check_serial(gate: Gate, run_dir: Path) -> None:
    folder = run_dir / "serial_logs"
    if not folder.is_dir():
        return
    logs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".log"]
    if not logs:
        gate.error("NO_SERIAL_LOG", "serial_logs/ 里没有 .log", "确认串口被正确打开并落盘")
        return
    empty = [p.name for p in logs if p.stat().st_size == 0]
    if len(empty) == len(logs):
        gate.error("SERIAL_ALL_EMPTY", "所有串口日志都是空的", "检查串口连接与波特率")


def check_longrun(gate: Gate, task_dir: Path) -> None:
    work = task_dir / "work"
    if not work.is_dir():
        return
    for state_file in work.glob("longrun_*.state.json"):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = str(state.get("status") or "")
        target = state.get("target_rounds")
        done = state.get("completed")
        if status == "running":
            gate.warn("LONGRUN_RUNNING", f"{state_file.stem} 仍在运行（{done}/{target}）",
                      "确认是否已到目标；未到不要报完成")
        elif status in ("blocked", "failed"):
            gate.error("LONGRUN_" + status.upper(), f"{state_file.stem} 状态 {status}：{state.get('last_error', '')}",
                       "处理阻塞原因后重跑")
        elif target and isinstance(done, int) and done < int(target) and status != "stopped":
            gate.warn("LONGRUN_SHORT", f"{state_file.stem} 只完成 {done}/{target}",
                      "目标次数未跑满，不得报完成")


def _sha256(path: Path) -> str:
    import hashlib

    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def require_pass(run_dir: Path) -> list[str]:
    """上传证据前的强校验：gate.json 必须存在且 ok=true，且与当前 tool.log 一致。"""
    problems: list[str] = []
    gate = run_dir / "gate.json"
    if not gate.is_file():
        return ["缺少 gate.json：上传前必须运行 finish_gate --write"]
    try:
        payload = json.loads(gate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["gate.json 不是合法 JSON"]
    if not payload.get("ok"):
        codes = ", ".join(item.get("code", "?") for item in payload.get("errors") or [])
        problems.append(f"gate 未通过（{codes}），不得上传证据")
    expected = payload.get("tool_log_sha256") or ""
    actual = _sha256(run_dir / "tool.log")
    if expected and actual and expected != actual:
        problems.append("tool.log 在门禁通过后被修改，必须重跑门禁")
    return problems



def run_gate(task_dir: Path, run_dir: Path | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    gate = Gate()
    repo = repo_root or Path(__file__).resolve().parent.parent.parent
    if not task_dir.is_dir():
        gate.error("NO_TASK_DIR", f"任务目录不存在: {task_dir}", "确认任务编号")
        return {"task_dir": str(task_dir), "ok": False, "errors": gate.errors, "warnings": gate.warns}
    latest = check_layout(gate, task_dir)
    target = run_dir or latest
    if target is not None:
        check_run_items(gate, target)
        check_tool_log(gate, target)
        check_task_json(gate, task_dir, target)
        check_marker_codes(gate, target, catalog_codes(repo))
        check_cases_results(gate, target)
        check_serial(gate, target)
    check_longrun(gate, task_dir)
    return {
        "task_dir": str(task_dir),
        "run_dir": str(target) if target else None,
        "ok": not gate.errors,
        "errors": gate.errors,
        "warnings": gate.warns,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="收尾门禁：产物不合规即失败")
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--run", default="", help="指定运行目录名；默认取最新")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="把结果写入运行目录 gate.json（上传证据时必须带）")
    args = parser.parse_args(argv)

    task_dir = Path(args.task_dir)
    run_dir = task_dir / "result" / args.run if args.run else None
    report = run_gate(task_dir, run_dir, Path(args.repo_root) if args.repo_root else None)

    if args.write and report.get("run_dir"):
        run_dir = Path(report["run_dir"])
        artifacts = []
        for item in sorted(run_dir.rglob("*")):
            if not item.is_file() or item.name == "gate.json":
                continue
            artifacts.append({
                "path": item.relative_to(run_dir).as_posix(),
                "bytes": item.stat().st_size,
                "sha256": _sha256(item),
            })
        artifact_block = json.dumps(artifacts, ensure_ascii=False, sort_keys=True).encode("utf-8")
        import hashlib as _hashlib

        payload = {
            "schema": "lstest.gate.v1",
            "ok": report["ok"],
            "task_id": run_dir.parent.parent.name,
            "run": run_dir.name,
            "checked_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors": report["errors"],
            "warnings": report["warnings"],
            "tool_log_sha256": _sha256(run_dir / "tool.log"),
            "artifacts": artifacts,
            "manifest_sha256": _hashlib.sha256(artifact_block).hexdigest(),
        }
        (run_dir / "gate.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"gate.json -> {run_dir / 'gate.json'}（ok={report['ok']}）")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"任务: {report['task_dir']}")
        print(f"运行: {report.get('run_dir')}")
        print(f"结论: {'PASS' if report['ok'] else 'FAIL'}")
        for item in report["errors"]:
            print(f"  [ERROR] {item['code']}: {item['message']}")
            if item.get("fix"):
                print(f"          修复: {item['fix']}")
        for item in report["warnings"]:
            print(f"  [WARN ] {item['code']}: {item['message']}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
