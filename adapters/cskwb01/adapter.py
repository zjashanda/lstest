"""cskwb01 record adapter: raw serial lines -> :class:`RawLogRecord`.

Boundary (see ``adapters/README.md``): this module only collects.  It does not
decode markers, does not build facts, does not decide verdicts and never writes
``tool.log``.  Marker interpretation lives in ``profiles/cskwb01/profile.json``;
rendering and case accounting live in ``scripts/``.

Public entry points
-------------------
``iter_records(path, ...)``  stream raw records for one serial log file
``submit(runtime, records, ...)``  forward those records to the runtime
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from lstest.scripts.profile import RawLogRecord

__all__ = [
    "PROJECT",
    "PROFILE_PATH",
    "DEFAULT_SOURCE",
    "DEFAULT_PHASE",
    "iter_records",
    "submit",
    "resolve_endpoint",
]

PROJECT = "cskwb01"
PROFILE_PATH = Path(__file__).resolve().parents[2] / "profiles" / PROJECT / "profile.json"

#: Fallback ``source`` when the log file cannot be tied to a declared role.
DEFAULT_SOURCE = "serial"
#: Default case phase stamped on every record; the profile rule may override it.
DEFAULT_PHASE = "observe"


def _load_payload(profile: Any) -> dict:
    """Accept a profile path, a ``DeviceProfile`` or a raw mapping."""
    if profile is None:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
    if isinstance(profile, Mapping):
        return dict(profile)
    if isinstance(profile, (str, Path)):
        return json.loads(Path(profile).read_text(encoding="utf-8-sig"))
    payload = getattr(profile, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError(f"cannot read a profile from {type(profile).__name__}")


def _role_ports(payload: Mapping[str, Any]) -> dict[str, str]:
    """Return ``{role: port}`` from ``payload["ports"]``; first port wins."""
    ports: dict[str, str] = {}
    for entry in payload.get("ports") or []:
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role") or "").strip()
        port = str(entry.get("port") or "").strip()
        if role and role not in ports:
            ports[role] = port
    return ports


def _stem_tokens(path: str | Path) -> set[str]:
    stem = Path(path).stem.lower()
    for separator in ("-", ".", " "):
        stem = stem.replace(separator, "_")
    return {token for token in stem.split("_") if token}


def resolve_endpoint(
    *,
    profile: Any = None,
    role: str | None = None,
    path: str | Path | None = None,
    default_source: str = DEFAULT_SOURCE,
) -> tuple[str, str, str]:
    """Resolve ``(source, port, role)`` for one log file from the profile.

    ``source`` is the declared role name because profile event rules scope
    themselves with ``sources.sources``; ``default_source`` is only used when
    no role can be resolved at all.
    """
    ports = _role_ports(_load_payload(profile))
    if role:
        if role not in ports:
            raise ValueError(f"{PROJECT}: unknown role {role!r}; declared roles: {sorted(ports)}")
        return role, ports[role], role
    if path is not None and ports:
        tokens = _stem_tokens(path)
        by_role = sorted(
            (name for name in ports if name.lower() in tokens),
            key=len,
            reverse=True,
        )
        if by_role:
            chosen = by_role[0]
            return chosen, ports[chosen], chosen
        by_port = sorted((name for name, port in ports.items() if port and port.lower() in tokens))
        if len(by_port) == 1:
            chosen = by_port[0]
            return chosen, ports[chosen], chosen
        if not by_port and len(ports) == 1:
            chosen = next(iter(ports))
            return chosen, ports[chosen], chosen
        raise ValueError(
            f"{PROJECT}: cannot infer a role from {Path(path).name!r}; "
            f"declared roles: {sorted(ports)}"
        )
    if len(ports) == 1:
        chosen = next(iter(ports))
        return chosen, ports[chosen], chosen
    raise ValueError(
        f"{PROJECT}: role is required when the log name does not identify one; "
        f"declared roles: {sorted(ports)}"
    )


def _timestamp_prefix(text: str) -> str | None:
    """Return the leading ``YYYY-MM-DDTHH:MM:SS`` prefix, or ``None``.

    Implemented with plain character checks so the adapter carries no pattern
    language at all: adapters must never grow marker recognition.
    """
    if len(text) < 19:
        return None
    head = text[:19]
    if head[4] != "-" or head[7] != "-" or head[10] != "T":
        return None
    if head[13] != ":" or head[16] != ":":
        return None
    digits = head[0:4] + head[5:7] + head[8:10] + head[11:13] + head[14:16] + head[17:19]
    if not digits.isdigit():
        return None
    return head


def iter_records(
    path: str | Path,
    *,
    profile: Any = None,
    role: str | None = None,
    default_source: str = DEFAULT_SOURCE,
    default_phase: str = DEFAULT_PHASE,
    start_cursor: int = 1,
    start_sequence: int = 1,
) -> Iterator[RawLogRecord]:
    """Yield one raw record per non-blank line, in file order.

    ``cursor`` is the physical line number and ``sequence`` a dense counter of
    emitted records; both start at the given bases so a tail can continue an
    existing stream.  Empty lines are skipped, never interpreted.
    """
    source, port, resolved_role = resolve_endpoint(
        profile=profile, role=role, path=path, default_source=default_source
    )
    emitted = 0
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as handle:
        for offset, raw_line in enumerate(handle):
            text = raw_line.rstrip("\r\n")
            if not text.strip():
                continue
            yield RawLogRecord(
                text=text,
                source=source,
                port=port,
                role=resolved_role,
                cursor=start_cursor + offset,
                observed_at=_timestamp_prefix(text),
                sequence=start_sequence + emitted,
                phase=default_phase,
            )
            emitted += 1


def submit(runtime: Any, records: Iterable[RawLogRecord], *, case_id: str | None = None) -> int:
    """Forward records to ``runtime.submit_raw_record`` in arrival order."""
    forwarded = 0
    target_case = "" if case_id is None else case_id
    for record in records:
        runtime.submit_raw_record(record, case_id=target_case)
        forwarded += 1
    return forwarded
