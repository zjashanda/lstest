"""只读 device-io 产出的设备文档，绝不重新枚举 USB/串口。

设备事实的唯一来源是 device-io（`ports --format json`、`roles --save`）。本模块
只读取这两种 JSON 并按 role 返回端口：不模糊匹配、不猜序列号、不打开设备。
失败抛出 :class:`DeviceSnapshotError`（ValueError 子类），主入口沿用 BLOCKED
与退出码 2 约定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_SNAPSHOT = "ark.device-io.v1"
SCHEMA_ROLE_MAP = "ark.device-role-map.v1"
MODE_SNAPSHOT = "snapshot"
MODE_ROLE_MAP = "role-map"
RESOLVED_STATES = frozenset({"resolved", "pinned"})


class DeviceSnapshotError(ValueError):
    """设备文档存在，但无法满足本次声明的全部 role。"""


@dataclass(frozen=True)
class RoleResolution:
    role: str
    state: str
    port: str | None
    reason: str = ""


@dataclass(frozen=True)
class DeviceSnapshot:
    source: str
    schema: str
    generated_at: str
    mode: str
    roles: Mapping[str, RoleResolution]

    def port_for(self, role: str) -> str:
        entry = self.roles.get(role)
        if entry is None or not entry.port:
            raise DeviceSnapshotError(f"device document has no resolved port for role '{role}'")
        return entry.port

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "schema": self.schema, "generated_at": self.generated_at,
            "mode": self.mode,
            "roles": {
                role: {"state": item.state, "port": item.port, "reason": item.reason}
                for role, item in self.roles.items()
            },
        }


def _required(required: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """把 ``{role: 已声明端口名/接口标签}`` 规范成 role 到标签元组的映射。"""
    return {
        str(role): tuple(
            label for label in (value if isinstance(value, (list, tuple, set)) else (value,))
            if str(label or "").strip()
        )
        for role, value in required.items()
    }


def load_device_snapshot(path: str | Path, required: Mapping[str, Any]) -> DeviceSnapshot:
    """读取一份文档并解析全部必需 role；任一 role 未解析即硬失败。"""
    source = Path(path).resolve()
    if not source.is_file():
        raise DeviceSnapshotError(f"device document not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeviceSnapshotError(f"invalid device document {source}: {error}") from error
    if not isinstance(payload, Mapping):
        raise DeviceSnapshotError(f"device document root must be an object: {source}")
    schema, wanted = str(payload.get("schema") or ""), _required(required)
    if schema == SCHEMA_ROLE_MAP:
        roles, mode = _resolve_role_map(payload, wanted), MODE_ROLE_MAP
    elif schema == SCHEMA_SNAPSHOT:
        roles, mode = _resolve_snapshot(payload, wanted), MODE_SNAPSHOT
    else:
        raise DeviceSnapshotError(
            f"unsupported device document schema: {schema or '<missing>'}; "
            f"accepted: {SCHEMA_SNAPSHOT}, {SCHEMA_ROLE_MAP}"
        )
    return DeviceSnapshot(str(source), schema, str(payload.get("generated_at") or ""), mode, roles)


def _state_of(entry: Any) -> str:
    return str(entry.get("state") or "missing") if isinstance(entry, Mapping) else "missing"


def _resolve_role_map(
    payload: Mapping[str, Any], required: Mapping[str, tuple[str, ...]]
) -> dict[str, RoleResolution]:
    raw_roles = payload.get("roles")
    if not isinstance(raw_roles, Mapping):
        raise DeviceSnapshotError("role-map document has no roles object")
    if payload.get("ok") is False:
        unresolved = [f"{name}({_state_of(entry)})" for name, entry in raw_roles.items()
                      if _state_of(entry) not in RESOLVED_STATES]
        raise DeviceSnapshotError("role-map reports ok=false; unresolved roles: "
                                  + (", ".join(unresolved) or "none declared"))
    results: dict[str, RoleResolution] = {}
    for role in required:
        entry = raw_roles.get(role)
        if not isinstance(entry, Mapping):
            raise DeviceSnapshotError(f"role-map has no entry object for role '{role}'")
        state, reason = _state_of(entry), str(entry.get("reason") or "")
        if state not in RESOLVED_STATES:
            raise DeviceSnapshotError(f"role '{role}' state '{state}' (reason: {reason or 'none'})")
        port = str(entry.get("port") or "").strip()
        if not port:
            raise DeviceSnapshotError(
                f"role '{role}' state '{state}' has no port (reason: {reason or 'none'})")
        results[role] = RoleResolution(role, state, port, reason)
    return results


def _resolve_snapshot(
    payload: Mapping[str, Any], required: Mapping[str, tuple[str, ...]]
) -> dict[str, RoleResolution]:
    """只在 device/interface 上做大小写不敏感精确相等匹配。"""
    raw_ports = payload.get("ports")
    if not isinstance(raw_ports, list):
        raise DeviceSnapshotError("snapshot document has no ports list")
    ports = [item for item in raw_ports if isinstance(item, Mapping)]
    results: dict[str, RoleResolution] = {}
    for role, labels in required.items():
        wanted = {label.casefold() for label in labels}
        if not wanted:
            raise DeviceSnapshotError(
                f"role '{role}' declares no port or interface label to match in the snapshot")
        matches = [
            str(item.get("device") or "").strip() for item in ports
            if item.get("present") is not False
            and (str(item.get("device") or "").strip().casefold() in wanted
                 or str(item.get("interface") or "").strip().casefold() in wanted)
        ]
        shown = ", ".join(sorted(wanted))
        if not matches:
            raise DeviceSnapshotError(f"role '{role}' missing: no snapshot port matches [{shown}]")
        if len(matches) > 1:
            raise DeviceSnapshotError(
                f"role '{role}' ambiguous: {len(matches)} snapshot ports match [{shown}]: "
                + ", ".join(repr(item) for item in matches))
        if not matches[0]:
            raise DeviceSnapshotError(f"role '{role}' matched a snapshot entry without a device path")
        results[role] = RoleResolution(role, "resolved", matches[0], f"matched declared label [{shown}]")
    return results
