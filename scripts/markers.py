"""标记位引擎：把设备日志流压缩成 tool.log 的标记行。

设计要点：

- ``markers/catalog.json`` 是码表（code 与项目无关，跨项目统一）。
- ``profiles/<项目>/profile.json`` 的 ``event_rules`` 提供正则与展示字段；
  规则可选带 ``code`` 字段映射到码表，缺省时回退到 ``rule_id``（兼容旧 profile）。
- 未命中任何规则的原始行不写 tool.log（原文只在 serial_logs）。
- 码表里有、项目 profile 里没配正则的 code 记为 missing，提示后续补。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CATALOG_SCHEMA = "lstest.marker-catalog.v1"
PROFILE_SCHEMA = "lstest.project-markers.v1"
INDENT = "  "


@dataclass(frozen=True)
class MarkerSpec:
    code: str
    category: str
    desc: str
    display: tuple[str, ...] = ()
    log_policy: str = "always"   # always | change | summary


@dataclass
class MarkerRule:
    code: str
    rule_id: str
    sources: tuple[str, ...]
    pattern: re.Pattern[str]
    capture: str | None
    display: tuple[str, ...]
    stage: str | None = None


@dataclass
class MarkerHit:
    code: str
    device: str
    port: str
    value: str
    extra: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        """标记正文（不含时间戳前缀）。code 永远在最前，display 字段以 k=v 跟在后面。"""
        if self.extra:
            body = ", ".join(f"{key}={value}" for key, value in self.extra.items())
            return f"{self.code}: {body}"
        return f"{self.code}: {self.value}".rstrip(": ")

    def lines(self) -> list[str]:
        """兼容旧调用：带缩进的裸标记行。新代码请用 core.emit_markers()。"""
        if self.extra:
            return [f"{INDENT}{self.device}({self.port}): {key}: {value}".rstrip(": ")
                    for key, value in self.extra.items()]
        return [f"{INDENT}{self.device}({self.port}): {self.code}: {self.value}".rstrip(": ")]


def load_catalog(path: Path | str) -> dict[str, MarkerSpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != CATALOG_SCHEMA:
        raise ValueError(f"marker catalog schema 不是 {CATALOG_SCHEMA}: {payload.get('schema')}")
    specs: dict[str, MarkerSpec] = {}
    for item in payload.get("markers") or []:
        code = str(item.get("code") or "").strip()
        if not code:
            raise ValueError("marker catalog 存在空 code")
        specs[code] = MarkerSpec(
            code=code,
            category=str(item.get("category") or ""),
            desc=str(item.get("desc") or ""),
            display=tuple(item.get("display") or ()),
            log_policy=str(item.get("log_policy") or "always"),
        )
    return specs


def _sources_of(rule: Mapping[str, Any]) -> tuple[str, ...]:
    raw = rule.get("sources")
    if isinstance(raw, Mapping):
        items = raw.get("sources") or raw.get("roles") or []
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    return tuple(str(item) for item in items)


def load_profile_capabilities(profile_path: Path | str) -> dict[str, Any]:
    """读取 profile 声明的机型能力；未声明返回空字典。"""
    try:
        payload = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    declared = payload.get("capabilities")
    return dict(declared) if isinstance(declared, Mapping) else {}


def load_rules(profile_path: Path | str, catalog: Mapping[str, MarkerSpec] | None = None) -> list[MarkerRule]:
    payload = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    rules: list[MarkerRule] = []
    for item in payload.get("event_rules") or []:
        rule_id = str(item.get("rule_id") or "").strip()
        pattern = item.get("regex")
        if not rule_id or not pattern:
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            code = rule_id
        if catalog is not None and code not in catalog:
            code = rule_id
        display = tuple(item.get("display_captures") or ())
        capture = item.get("presentation_capture")
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        rules.append(MarkerRule(
            code=code,
            rule_id=rule_id,
            sources=_sources_of(item),
            pattern=compiled,
            capture=str(capture) if capture else None,
            display=display,
            stage=item.get("stage"),
        ))
    return rules


class MarkerExtractor:
    """按端口角色匹配标记；只输出码表里有的 code。"""

    def __init__(
        self,
        catalog: Mapping[str, MarkerSpec],
        rules: Sequence[MarkerRule],
    ) -> None:
        self.catalog = dict(catalog)
        self.rules = list(rules)
        self.missing: set[tuple[str, str]] = set()
        self.capabilities: dict[str, Any] = {}

    @classmethod
    def from_paths(cls, catalog_path: Path | str, profile_path: Path | str) -> "MarkerExtractor":
        catalog = load_catalog(catalog_path)
        extractor = cls(catalog, load_rules(profile_path, catalog))
        extractor.capabilities = load_profile_capabilities(profile_path)
        return extractor

    def match(self, role: str, port: str, text: str) -> list[MarkerHit]:
        hits: list[MarkerHit] = []
        for rule in self.rules:
            if rule.sources and role not in rule.sources:
                continue
            found = rule.pattern.search(text)
            if not found:
                continue
            value = self._value(rule, found)
            spec = self.catalog.get(rule.code)
            display = rule.display or (spec.display if spec else ())
            extra = self._extra(display, value)
            code = rule.code if rule.code in self.catalog else rule.code
            hits.append(MarkerHit(code=code, device=role, port=port, value=value, extra=extra))
        return hits

    def report_missing(self, role: str) -> list[str]:
        configured = {rule.code for rule in self.rules if not rule.sources or role in rule.sources}
        skip_categories = set(self.capabilities.get("markers_absent") or ())
        absent = [code for code in self.catalog if code not in configured
                  and getattr(self.catalog[code], "category", "") not in skip_categories]
        for code in absent:
            self.missing.add((role, code))
        return absent

    @staticmethod
    def _value(rule: MarkerRule, found: re.Match[str]) -> str:
        if rule.capture:
            try:
                group = found.group(rule.capture)
            except (IndexError, KeyError):
                group = found.group(0)
        else:
            group = found.group(0)
        return str(group if group is not None else "").strip()

    @staticmethod
    def _extra(display: Sequence[str], value: str) -> dict[str, str]:
        if not display:
            return {}
        try:
            payload = json.loads(value)
        except (ValueError, TypeError):
            return {}
        if isinstance(payload, Mapping) and isinstance(payload.get("rlt"), list) and payload["rlt"]:
            first = payload["rlt"][0]
            if isinstance(first, Mapping):
                payload = first
        if not isinstance(payload, Mapping):
            if isinstance(payload, list) and payload and isinstance(payload[0], Mapping):
                payload = payload[0]
            else:
                return {}
        out: dict[str, str] = {}
        for key in display:
            if key in payload:
                out[key] = str(payload[key])
        return out


class MarkerBuffer:
    """按码表的 log_policy 决定标记是否落盘，抑制刷屏。

    always  : 每条都写
    change  : 值变化时才写（首次必写）
    summary : 不逐条写，flush() 时输出一行汇总
    """

    def __init__(self, catalog: Mapping[str, MarkerSpec]) -> None:
        self.catalog = dict(catalog)
        self._last: dict[tuple[str, str, str], str] = {}
        self._pending: dict[tuple[str, str, str], dict[str, Any]] = {}

    def feed(self, hit: MarkerHit) -> bool:
        spec = self.catalog.get(hit.code)
        policy = spec.log_policy if spec else "always"
        key = (hit.device, hit.port, hit.code)
        if policy == "summary":
            bucket = self._pending.setdefault(key, {"count": 0, "values": {}})
            bucket["count"] += 1
            bucket["values"][hit.value] = bucket["values"].get(hit.value, 0) + 1
            return False
        if policy == "change":
            if self._last.get(key) == hit.value:
                return False
            self._last[key] = hit.value
            return True
        return True

    def flush(self) -> list[tuple[str, str, str]]:
        """返回 (device, port, 汇总正文)。"""
        out: list[tuple[str, str, str]] = []
        for (device, port, code), bucket in self._pending.items():
            count = bucket["count"]
            if not count:
                continue
            values = sorted(bucket["values"].items(), key=lambda kv: -kv[1])
            distinct = len(values)
            top = ", ".join(f"{value} x{n}" for value, n in values[:3])
            more = "" if distinct <= 3 else f" 等 {distinct} 种"
            out.append((device, port, f"{code}: 共 {count} 条{more}；{top}"))
        self._pending.clear()
        return out


def render_hits(hits: Iterable[MarkerHit]) -> list[str]:
    lines: list[str] = []
    for hit in hits:
        lines.extend(hit.lines())
    return lines
