"""Serial attach/detach session ledger (NoahsArk serial lifecycle baseline).

Only two detach reasons are legal:

``TASK_DONE``
    The current task (or its debug phase) is finished, including the quiet
    hand-off window where the operator waits to start the real task.

``INTERRUPT``
    An explicit stop command from the operator, or an agent that judges the
    task unrecoverable and therefore actively breaks the connection.

Anything else must keep the connection open.  A run that ends while the
session is still ``ATTACHED`` is an ``UNEXPECTED_DETACH`` and must be reported
as a failure instead of being silently closed.

The same file doubles as the ownership registry: a second task reads it,
sees ``ATTACHED`` with a foreign owner and reports the lease instead of
blindly trying to open the port and guessing why it failed.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA = "noahsark.serial-session.v1"

STATE_ATTACHED = "ATTACHED"
STATE_DETACHED = "DETACHED"

REASON_TASK_DONE = "TASK_DONE"
REASON_INTERRUPT = "INTERRUPT"
DETACH_REASONS = (REASON_TASK_DONE, REASON_INTERRUPT)


class SerialSessionError(RuntimeError):
    """Base class for session contract violations."""


class SerialLeaseError(SerialSessionError):
    """Another live task already holds the ports."""

    def __init__(self, holder: Mapping[str, Any], ports: Iterable[str]) -> None:
        self.holder = dict(holder)
        self.ports = list(ports)
        owner = self.holder.get("task_id") or "unknown"
        super().__init__(
            f"serial lease already held by {owner} "
            f"(state={self.holder.get('state')}, heartbeat={self.holder.get('heartbeat_at')}); "
            "report BLOCKED_SERIAL_LEASE instead of retrying open()"
        )


class IllegalDetach(SerialSessionError):
    """A detach was attempted without one of the two legal reasons."""


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def detect_own_detach(client: Any) -> Optional[str]:
    """Return the detach reason if the client recorded one, else ``None``."""
    return getattr(client, "detach_reason", None)


class SerialSession:
    """Reads and writes the node-level serial session ledger."""

    def __init__(
        self,
        path: Path | str,
        task_id: str,
        *,
        agent: str = "",
        node: str = "",
        heartbeat_ttl_s: float = 900.0,
        clock: Any = now_iso,
    ) -> None:
        self.path = Path(path)
        self.task_id = str(task_id)
        self.agent = agent
        self.node = node or os.environ.get("COMPUTERNAME", "")
        self.heartbeat_ttl_s = float(heartbeat_ttl_s)
        self.clock = clock
        self.state: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------------- reading

    def read(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def holder_is_stale(self, data: Mapping[str, Any]) -> bool:
        stamp = _parse_iso(str(data.get("heartbeat_at") or ""))
        if stamp is None:
            return False
        age = (datetime.now().astimezone() - stamp).total_seconds()
        return age > self.heartbeat_ttl_s

    # ---------------------------------------------------------------- writing

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent),
            prefix=self.path.name + ".", suffix=".tmp", delete=False,
        )
        try:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, str(self.path))
        self.state = dict(payload)

    # ----------------------------------------------------------------- attach

    def attach(
        self,
        ports: Iterable[Mapping[str, Any]],
        *,
        takeover_stale: bool = False,
        allow_same_owner_resume: bool = True,
    ) -> Dict[str, Any]:
        """Claim the ports for this task.

        Raises :class:`SerialLeaseError` when another task still holds them.
        Resuming the same task keeps the ledger and records an explicit gap,
        so a cross-run reconnect never pretends the feed was continuous.
        """
        port_map = {
            str(item["port"]).upper(): {
                "role": item.get("role") or "unknown",
                "baud": int(item.get("baud") or 0),
                "offset": int(item.get("offset") or 0),
                "log": str(item.get("log") or ""),
            }
            for item in ports
        }
        stamp = self.clock()
        previous = self.read()
        gaps: List[Dict[str, str]] = []
        resume_count = 0
        attach_at = stamp

        if previous:
            state = str(previous.get("state") or "")
            owner = str(previous.get("task_id") or "")
            if state == STATE_ATTACHED:
                if owner and owner != self.task_id:
                    if not (takeover_stale and self.holder_is_stale(previous)):
                        raise SerialLeaseError(previous, port_map.keys())
                    gaps.append({
                        "from": str(previous.get("heartbeat_at") or ""),
                        "to": stamp,
                        "reason": "stale_lease_takeover_after_" + owner,
                    })
                else:
                    if not allow_same_owner_resume:
                        raise SerialLeaseError(previous, port_map.keys())
                    resume_count = int(previous.get("resume_count") or 0) + 1
                    attach_at = str(previous.get("attach_at") or stamp)
                    gaps.append({
                        "from": str(previous.get("heartbeat_at") or ""),
                        "to": stamp,
                        "reason": "cross_run_resume",
                    })
                    for port, entry in (previous.get("ports") or {}).items():
                        if port not in port_map and isinstance(entry, dict):
                            port_map[port] = dict(entry)
            elif state == STATE_DETACHED:
                carry = [item for item in (previous.get("gaps") or []) if isinstance(item, dict)]
                gaps = carry + gaps
            else:
                raise SerialSessionError(f"unknown session state: {state!r}")

        payload = {
            "schema": SCHEMA,
            "node": self.node,
            "agent": self.agent,
            "task_id": self.task_id,
            "state": STATE_ATTACHED,
            "attach_at": attach_at,
            "detach_at": None,
            "detach_reason": None,
            "detach_detail": "",
            "resume_count": resume_count,
            "heartbeat_at": stamp,
            "ports": port_map,
            "gaps": gaps,
        }
        self._write(payload)
        return payload

    # -------------------------------------------------------------- heartbeat

    def heartbeat(self, offsets: Mapping[str, int]) -> Dict[str, Any]:
        current = self.read() or {}
        if str(current.get("state")) != STATE_ATTACHED:
            raise SerialSessionError("heartbeat requires an ATTACHED session")
        if str(current.get("task_id") or "") not in ("", self.task_id):
            raise SerialLeaseError(current, offsets.keys())
        ports = current.get("ports") or {}
        for port, offset in offsets.items():
            key = str(port).upper()
            entry = ports.get(key)
            if not isinstance(entry, dict):
                continue
            entry["offset"] = int(offset)
        current["ports"] = ports
        current["heartbeat_at"] = self.clock()
        self._write(current)
        return current

    # ----------------------------------------------------------------- detach

    def detach(self, reason: str, detail: str = "") -> Dict[str, Any]:
        if reason not in DETACH_REASONS:
            raise IllegalDetach(
                f"refusing to detach with reason={reason!r}; "
                f"only {DETACH_REASONS} are legal. "
                "Keep the connection open until the task/debug phase ends or an interrupt is issued."
            )
        current = self.read() or {}
        if str(current.get("state")) != STATE_ATTACHED:
            raise SerialSessionError(
                f"detach requires an ATTACHED session, current state={current.get('state')!r}"
            )
        if str(current.get("task_id") or "") not in ("", self.task_id):
            raise SerialLeaseError(current, (current.get("ports") or {}).keys())
        current["state"] = STATE_DETACHED
        current["detach_at"] = self.clock()
        current["detach_reason"] = reason
        current["detach_detail"] = detail
        self._write(current)
        return current

    def unexpected_detach(self, detail: str) -> None:
        """Record a close that had no legal reason.  Callers must report FAIL."""
        current = self.read() or {}
        current["state"] = STATE_DETACHED
        current["detach_at"] = self.clock()
        current["detach_reason"] = "UNEXPECTED_DETACH"
        current["detach_detail"] = detail
        self._write(current)

    # ------------------------------------------------------------------ views

    def is_attached(self) -> bool:
        return str((self.read() or {}).get("state")) == STATE_ATTACHED

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.read() or {})
