"""profile 合并与保护：只增不减，改前留痕。

问题背景：NOAH-413 的 profile 在运行中被整体重写，10 条规则里能命中的规则丢失，
导致后续窗口 0 命中。根因是「整体 json.dump」而不是「合并」。

用法：
    from lstest.scripts import profile_merge
    result = profile_merge.merge(path, new_rules)   # 保留未知规则，只更新同 rule_id
    print(result["added"], result["updated"], result["kept"])

    profile_merge.guard(path, previous_count=10)    # 规则数减少即抛错
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


class ProfileRegression(RuntimeError):
    """规则数减少时抛出，防止已可用的规则被删。"""


def _now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> Path | None:
    """修改前留痕：profile.json -> profile.json.bak-<时间戳>。"""
    path = Path(path)
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.bak-{_now()}")
    shutil.copy2(path, target)
    return target


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload.get("event_rules"), list):
        payload["event_rules"] = []
    return payload


def merge(path: Path, new_rules: Iterable[Mapping[str, Any]], *,
          allow_remove: bool = False,
          drop: Iterable[str] | None = None) -> dict[str, Any]:
    """按 rule_id 合并：已存在的更新，不存在的追加，未提及的原样保留。"""
    path = Path(path)
    payload = load(path)
    existing = {str(r.get("rule_id")): dict(r) for r in payload["event_rules"] if r.get("rule_id")}
    before = len(existing)
    added: list[str] = []
    updated: list[str] = []
    for rule in new_rules:
        rule_id = str(rule.get("rule_id") or "").strip()
        if not rule_id:
            continue
        if rule_id in existing:
            if existing[rule_id] != dict(rule):
                existing[rule_id] = dict(rule)
                updated.append(rule_id)
        else:
            existing[rule_id] = dict(rule)
            added.append(rule_id)
    removed: list[str] = []
    for rule_id in drop or ():
        if rule_id in existing:
            existing.pop(rule_id)
            removed.append(str(rule_id))
    after = len(existing)
    if after < before and not allow_remove:
        raise ProfileRegression(f"合并会导致规则减少 {before} -> {after}，拒绝写入")
    backup_path = backup(path)
    payload["event_rules"] = [existing[key] for key in existing]
    payload["rules_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"added": added, "updated": updated, "removed": removed,
            "kept": before - len(updated), "total": after,
            "backup": str(backup_path) if backup_path else ""}


def guard(path: Path, previous_count: int) -> None:
    """门禁用：当前规则数不得少于上次记录。"""
    payload = load(path)
    current = len(payload["event_rules"])
    if current < previous_count:
        raise ProfileRegression(
            f"profile 规则数从 {previous_count} 降到 {current}，疑似被整体覆盖")


def count(path: Path) -> int:
    return len(load(path)["event_rules"])


def diff_rules(path: Path) -> dict[str, list[str]]:
    """与最近一次备份对比，列出新增/删除/修改的 rule_id。"""
    path = Path(path)
    backups = sorted(path.parent.glob(f"{path.name}.bak-*"))
    if not backups:
        return {"added": [], "removed": [], "changed": []}
    old = load(backups[-1])
    new = load(path)
    old_map = {str(r.get("rule_id")): r for r in old["event_rules"]}
    new_map = {str(r.get("rule_id")): r for r in new["event_rules"]}
    return {
        "added": sorted(set(new_map) - set(old_map)),
        "removed": sorted(set(old_map) - set(new_map)),
        "changed": sorted(k for k in set(old_map) & set(new_map) if old_map[k] != new_map[k]),
    }
