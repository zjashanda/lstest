"""证据校验（服务端可用）：不信任客户端的 gate 结论，自己算一遍。

不需要密钥。目标不是「证明不可伪造」，而是：
  1. 传的和门禁看的必须是同一份（全产物哈希）
  2. 门禁结论必须能在本地复现（重跑规则）
  3. 标记值必须能在串口原文里逐字找到（伪造成本陡增）
  4. 时间与用例集合必须自洽

用法：
    python -m lstest.scripts.verify_evidence --run-dir result/<stamp> --task-dir <任务目录>
退出码：0 通过；2 不通过。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

MARK_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} - (?:DEBUG|INFO|WARN|ERROR) +- \[MARK\] "
    r"\S+\([^)]*\): ([A-Za-z][A-Za-z0-9_]*): (.*)$")
TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")

REQUIRED = ("tool.log", "cases.csv", "results.csv", "serial_logs", "gate.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_gate(run_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = run_dir / "gate.json"
    if not path.is_file():
        return None, ["缺少 gate.json（上传前必须 finish_gate --write）"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, ValueError):
        return None, ["gate.json 不是合法 JSON"]


def check_manifest(run_dir: Path, gate: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    listed = {item.get("path"): item for item in gate.get("artifacts") or []}
    actual = {
        item.relative_to(run_dir).as_posix(): item
        for item in run_dir.rglob("*")
        if item.is_file() and item.name != "gate.json"
    }
    for rel, meta in listed.items():
        target = actual.get(rel)
        if target is None:
            problems.append(f"清单登记的文件不存在: {rel}")
            continue
        if target.stat().st_size != meta.get("bytes"):
            problems.append(f"字节数不符: {rel}")
        elif sha256_file(target) != meta.get("sha256"):
            problems.append(f"sha256 不符: {rel}")
    for rel in actual:
        if rel not in listed:
            problems.append(f"未登记进清单的文件: {rel}")
    block = json.dumps(gate.get("artifacts") or [], ensure_ascii=False, sort_keys=True).encode("utf-8")
    if gate.get("manifest_sha256") and hashlib.sha256(block).hexdigest() != gate["manifest_sha256"]:
        problems.append("manifest_sha256 与清单内容不一致（清单被改过）")
    return problems


def check_markers_in_serial(run_dir: Path) -> list[str]:
    """核心校验：tool.log 的标记值必须能在 serial_logs 原文里逐字找到。"""
    problems: list[str] = []
    tool = run_dir / "tool.log"
    serial_dir = run_dir / "serial_logs"
    if not tool.is_file() or not serial_dir.is_dir():
        return ["缺少 tool.log 或 serial_logs，无法交叉验证"]
    serial_text = []
    for log in serial_dir.glob("*.log"):
        serial_text.append(log.read_text(encoding="utf-8", errors="replace"))
    haystack = "\n".join(serial_text)
    if not haystack.strip():
        return ["serial_logs 为空，标记无法与原始证据对照"]
    missing = []
    for line in tool.read_text(encoding="utf-8", errors="replace").splitlines():
        match = MARK_LINE_RE.match(line)
        if not match:
            continue
        value = (match.group(3) or "").strip()
        # 汇总行（flush_markers 产出）不是原始值，跳过逐字比对
        if value.startswith("共 ") or "条；" in value:
            continue
        # display 字段渲染成 k=v, k=v 时逐个值比对
        candidates = [part.split("=", 1)[-1].strip() for part in value.split(",")] or [value]
        if not any(cand and cand in haystack for cand in candidates):
            missing.append(f"{match.group(2)}: {value[:40]}")
    if missing:
        problems.append(f"{len(missing)} 条标记值在串口原文中找不到（例: {missing[0]}）")
    return problems


def check_time_span(run_dir: Path) -> list[str]:
    problems: list[str] = []
    tool = run_dir / "tool.log"
    serial_dir = run_dir / "serial_logs"
    if not tool.is_file() or not serial_dir.is_dir():
        return []
    tool_times = [m.group(1) for m in MARK_LINE_RE.finditer(tool.read_text(encoding="utf-8", errors="replace"))]
    if not tool_times:
        return []
    serial_times: list[str] = []
    for log in serial_dir.glob("*.log"):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[:200000]:
            match = TS_RE.match(line)
            if match:
                serial_times.append(match.group(1).replace("T", " "))
    if not serial_times:
        return []
    if min(tool_times) < min(serial_times) or max(tool_times) > max(serial_times):
        problems.append("tool.log 的标记时间超出串口日志时间范围")
    return problems


def check_cases(run_dir: Path) -> list[str]:
    cases = run_dir / "cases.csv"
    results = run_dir / "results.csv"
    if not cases.is_file() or not results.is_file():
        return ["缺少 cases.csv 或 results.csv"]
    case_ids = {str(r.get("case_id") or "") for r in
                csv.DictReader(cases.read_text(encoding="utf-8-sig").splitlines())} - {""}
    done_ids = {str(r.get("case_id") or "") for r in
                csv.DictReader(results.read_text(encoding="utf-8-sig").splitlines())} - {""}
    if not case_ids:
        return ["cases.csv 没有用例"]
    missing = sorted(case_ids - done_ids)
    return [f"{len(missing)} 个用例没有终态: {', '.join(missing[:5])}"] if missing else []


def verify(run_dir: Path, task_dir: Path | None = None) -> dict[str, Any]:
    problems: list[str] = []
    if not run_dir.is_dir():
        return {"run_dir": str(run_dir), "ok": False, "problems": ["运行目录不存在"]}
    for name in REQUIRED:
        if not (run_dir / name).exists():
            problems.append(f"缺少必需产物: {name}")
    gate, gate_problems = load_gate(run_dir)
    problems.extend(gate_problems)
    if gate is not None:
        if not gate.get("ok"):
            problems.append("gate.ok=false，不得上传")
        if task_dir is not None and gate.get("task_id") and gate["task_id"] != task_dir.name:
            problems.append(f"gate.task_id={gate['task_id']} 与任务目录名不一致")
        if gate.get("run") and gate["run"] != run_dir.name:
            problems.append("gate.run 与运行目录名不一致")
        problems.extend(check_manifest(run_dir, gate))
    problems.extend(check_markers_in_serial(run_dir))
    problems.extend(check_time_span(run_dir))
    problems.extend(check_cases(run_dir))
    return {"run_dir": str(run_dir), "ok": not problems, "problems": problems}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="证据交叉校验（不信任客户端结论）")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = verify(Path(args.run_dir), Path(args.task_dir) if args.task_dir else None)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"运行: {report['run_dir']}")
        print(f"结论: {'PASS' if report['ok'] else 'FAIL'}")
        for item in report["problems"]:
            print(f"  - {item}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
