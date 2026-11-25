"""标记位复查：扫已有串口日志，找出「日志里有但 profile 没配」和「配了但从没出现」的标记。

用法：
    python -m lstest.scripts.marker_discovery \
        --logs result/<stamp>/serial_logs \
        --catalog markers/catalog.json \
        --profile profiles/<项目>/profile.json

只报告，不改 profile；由人或智能体确认后再补规则。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CATALOG_KIND = "catalog"
PROFILE_KIND = "profile"


def _iter_lines(paths: Iterable[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if path.is_dir():
            files = sorted(p for p in path.rglob("*.log") if p.is_file())
        else:
            files = [path]
        for file in files:
            try:
                lines.extend(file.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
    return lines


def _compile(rule: Mapping[str, Any]) -> re.Pattern[str] | None:
    pattern = rule.get("regex")
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def scan(
    lines: Sequence[str],
    catalog: Mapping[str, Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """返回已配置命中、未配置候选、失效规则三张表。"""
    configured: dict[str, dict[str, Any]] = {}
    for rule in rules:
        code = str(rule.get("code") or rule.get("rule_id") or "")
        compiled = _compile(rule)
        if not code or compiled is None:
            continue
        hits = [line for line in lines if compiled.search(line)]
        entry = configured.setdefault(code, {"rule_id": rule.get("rule_id"), "count": 0, "sample": ""})
        entry["count"] += len(hits)
        if hits and not entry["sample"]:
            entry["sample"] = hits[0].strip()[:200]

    undiscovered: list[dict[str, Any]] = []
    for code, spec in catalog.items():
        if code in configured:
            continue
        desc = str(spec.get("desc") or "")
        category = str(spec.get("category") or "")
        hits = [line for line in lines if _matches_keyword(code, line)]
        if hits:
            undiscovered.append({
                "code": code,
                "category": category,
                "desc": desc,
                "count": len(hits),
                "sample": hits[0].strip()[:200],
            })

    silent = [
        {"code": code, "rule_id": info["rule_id"]}
        for code, info in configured.items()
        if info["count"] == 0
    ]
    unclaimed = _unclaimed(lines, rules)
    return {"configured": configured, "undiscovered": undiscovered,
            "silent": silent, "unclaimed": unclaimed}


def _strip_log_prefix(line: str) -> str:
    """去掉日志采集器前缀（时间戳、端口、序号），只留设备原文。"""
    text = re.sub(r"^\[[^\]]*\]\s*", "", line)
    text = re.sub(r"^\S+\s+[A-Z]\s+#\d+\s*", "", text)
    return text


def _shape(text: str) -> str:
    """把数字/十六进制归一化，得到可聚合的行形状。"""
    shaped = re.sub(r"0x[0-9A-Fa-f]+", "0x#", text)
    shaped = re.sub(r"-?\d+(?:\.\d+)?", "#", shaped)
    return shaped.strip()[:120]


def _unclaimed(lines: Sequence[str], rules: Sequence[Mapping[str, Any]], top: int = 20) -> list[dict[str, Any]]:
    compiled = [c for c in (_compile(rule) for rule in rules) if c]
    counter: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for line in lines:
        if any(c.search(line) for c in compiled):
            continue
        body = _strip_log_prefix(line)
        if not body.strip():
            continue
        shape = _shape(body)
        if not shape:
            continue
        counter[shape] += 1
        samples.setdefault(shape, line.strip()[:200])
    return [{"count": n, "shape": s, "sample": samples[s]} for s, n in counter.most_common(top)
            if n >= 2]


def _keywords_of(code: str) -> list[str]:
    """把 camelCase 码还原成日志里可能的词形，按词边界匹配，避免子串误报。"""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", code).strip()
    words = [w for w in spaced.split() if w]
    if not words:
        return []
    variants = [code, " ".join(words), "_".join(words), "".join(words)]
    return [v for v in dict.fromkeys(variants) if v]


def _matches_keyword(code: str, line: str) -> bool:
    for word in _keywords_of(code):
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", line, re.I):
            return True
    return False


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for item in payload.get("markers", []):
        code = str(item.get("code") or "").strip()
        if code:
            out[code] = dict(item)
    return out


def load_rules(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [dict(item) for item in payload.get("event_rules", []) if isinstance(item, Mapping)]


def render_text(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    undiscovered = report.get("undiscovered") or []
    silent = report.get("silent") or []
    configured = report.get("configured") or {}

    lines.append(f"已配置标记: {len(configured)} 个；未配置候选: {len(undiscovered)} 个；"
                 f"从未命中: {len(silent)} 个；未认领行形状: {len(report.get('unclaimed') or [])} 个")
    if undiscovered:
        lines.append("")
        lines.append("[可新增] 日志里出现、profile 未配置：")
        for item in undiscovered:
            lines.append(f"  {item['code']} ({item['category']}) x{item['count']}  "
                         f"{item['desc']}  例: {item['sample'][:90]}")
    if silent:
        lines.append("")
        lines.append("[疑似失效] profile 配了但日志里从没出现：")
        for item in silent:
            lines.append(f"  {item['code']}  rule_id={item['rule_id']}")
    unclaimed = report.get("unclaimed") or []
    if unclaimed:
        lines.append("")
        lines.append("[未认领] 没有被任何规则匹配的日志行（按出现次数排序）：")
        for item in unclaimed:
            lines.append(f"  x{item['count']}  {item['shape']}")
            lines.append(f"        例: {item['sample'][:110]}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="标记位复查：扫日志比对码表与 profile 规则")
    parser.add_argument("--logs", required=True, help="串口日志目录或单个 .log 文件")
    parser.add_argument("--catalog", required=True, help="markers/catalog.json")
    parser.add_argument("--profile", required=True, help="项目 profile.json")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    lines = _iter_lines([Path(args.logs)])
    if not lines:
        print("没有可扫描的日志行", file=sys.stderr)
        return 2
    catalog = load_catalog(Path(args.catalog))
    rules = load_rules(Path(args.profile))
    report = scan(lines, catalog, rules)
    report["scanned_lines"] = len(lines)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
