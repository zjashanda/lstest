"""控制台闪窗审计：扫出未禁止创建窗口的子进程调用。

用法：
    python -m lstest.scripts.console_audit --roots D:\\zhsh\\workspace\\repos,D:\\zhsh\\workspace\\tasks

规则：
  * subprocess.(Popen|run|call|check_output|check_call) 必须带 creationflags 或封装在已知安全的函数里
  * os.system / shell=True 一律报错
  * 计划任务必须 Settings.Hidden = True（Windows，可选用 --check-tasks）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

CALL_RE = re.compile(r"subprocess\.(Popen|run|call|check_output|check_call)\s*\(")
SHELL_RE = re.compile(r"shell\s*=\s*True\s*[,)]" + "|" + r"os\.system\s*\(")
SAFE_WRAPPERS = {"run_command", "_run", "run_cmd", "launch", "_no_window_flags"}
SKIP_DIRS = {"tests", "__pycache__", ".git", "toolchain_backup_20261125", "toolchain_backup_a6d121f"}


def iter_python_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name == "console_audit.py":
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path


def audit_file(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return problems
    lines = text.splitlines()
    for index, line in enumerate(lines, 1):
        if SHELL_RE.search(line):
            problems.append(f"{path}:{index} 使用 shell=True 或 os.system（会弹窗）")
            continue
        if not CALL_RE.search(line):
            continue
        snippet = "\n".join(lines[index - 1:index + 8])
        if "creationflags" in snippet or "_no_window_flags" in snippet:
            continue
        if any(name in snippet for name in SAFE_WRAPPERS):
            continue
        problems.append(f"{path}:{index} subprocess 调用缺少 CREATE_NO_WINDOW")
    return problems


def check_tasks() -> list[str]:
    import subprocess

    problems: list[str] = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-ScheduledTask | Where-Object { $_.TaskName -match 'NoahsArk|NOAH-' } | "
             "ForEach-Object { $_.TaskName + '|' + $_.Settings.Hidden }"],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception as error:  # noqa: BLE001
        return [f"计划任务检查失败: {type(error).__name__}"]
    for line in (out.stdout or "").splitlines():
        if "|" not in line:
            continue
        name, hidden = line.rsplit("|", 1)
        if hidden.strip().lower() != "true":
            problems.append(f"计划任务 {name.strip()} 未设置 Hidden=True")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="控制台闪窗审计")
    parser.add_argument("--roots", required=True, help="逗号分隔的目录")
    parser.add_argument("--check-tasks", action="store_true", help="同时检查计划任务 Hidden")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    roots = [Path(item.strip()) for item in args.roots.split(",") if item.strip()]
    problems: list[str] = []
    scanned = 0
    for path in iter_python_files(roots):
        scanned += 1
        problems.extend(audit_file(path))
    if args.check_tasks:
        problems.extend(check_tasks())

    if args.json:
        import json

        print(json.dumps({"scanned": scanned, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        print(f"扫描 {scanned} 个文件，发现 {len(problems)} 个问题")
        for item in problems:
            print("  -", item)
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
