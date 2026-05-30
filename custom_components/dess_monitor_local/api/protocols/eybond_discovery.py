"""EyBond dongle discovery registry.

Phase 2 of the EyBond hub redesign (see ``wiki/EyBond-Hub-Discovery-Plan.md``):
a per-hub registry of dongles discovered by ``PN``, tracking ``last_seen``,
connection status, and per-device configuration (``enabled``, ``protocol``,
``devaddr``, ``name``).

This module is intentionally pure — no Home Assistant imports — so it can be
unit-tested without a running ``hass`` and serialized straight into a
config-entry store in a later phase. The :class:`EybondManager` owns one
registry per hub and updates it as dongles connect, heartbeat, and drop.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum


class DongleStatus(StrEnum):
    """Live connection status of a discovered dongle."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class DongleRecord:
    """One discovered (or pre-configured) EyBond dongle, keyed by ``pn``.

    Configuration fields (``enabled``, ``protocol``, ``devaddr``, ``name``)
    default to an unconfigured state: a freshly discovered dongle is disabled
    and has no protocol assigned (``protocol is None`` → not polled) until the
    user configures it. Lifecycle fields (``status``, ``peer``, timestamps)
    are maintained by the registry from session events.
    """

    pn: str
    name: str = ""
    enabled: bool = False
    protocol: str | None = None
    devaddr: int = 1
    status: DongleStatus = DongleStatus.DISCONNECTED
    peer: str = ""
    first_seen: str = ""
    last_seen: str = ""
    model_hint: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> DongleRecord:
        data = dict(data)
        try:
            data["status"] = DongleStatus(data.get("status"))
        except ValueError:
            data["status"] = DongleStatus.DISCONNECTED
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class EybondRegistry:
    """Registry of dongles discovered on one hub listener, keyed by ``PN``.

    ``now`` is injectable (returns an ISO-8601 timestamp string) so lifecycle
    tracking is deterministic under test.
    """

    def __init__(self, now: Callable[[], str] | None = None) -> None:
        self._records: dict[str, DongleRecord] = {}
        self._now = now or _utcnow_iso

    # -- lifecycle (driven by the manager) --------------------------------
    def record_seen(
        self, pn: str, peer: str = "", *, status: DongleStatus = DongleStatus.CONNECTED
    ) -> DongleRecord:
        """Note that ``pn`` was just seen (connect or heartbeat).

        Creates the record on first sight; otherwise refreshes ``last_seen``,
        ``status`` and ``peer``. Preserves user configuration on an existing
        record.
        """
        now = self._now()
        rec = self._records.get(pn)
        if rec is None:
            rec = DongleRecord(
                pn=pn, first_seen=now, last_seen=now, status=status, peer=peer
            )
            self._records[pn] = rec
            return rec
        rec.last_seen = now
        rec.status = status
        if peer:
            rec.peer = peer
        return rec

    def mark_disconnected(self, pn: str) -> DongleRecord | None:
        """Mark a known dongle as disconnected, refreshing ``last_seen``."""
        rec = self._records.get(pn)
        if rec is not None:
            rec.status = DongleStatus.DISCONNECTED
            rec.last_seen = self._now()
        return rec

    # -- configuration (driven by the options UI, later phases) -----------
    def _ensure(self, pn: str) -> DongleRecord:
        rec = self._records.get(pn)
        if rec is None:
            rec = DongleRecord(pn=pn)
            self._records[pn] = rec
        return rec

    def set_enabled(self, pn: str, enabled: bool) -> DongleRecord:
        rec = self._ensure(pn)
        rec.enabled = enabled
        return rec

    def set_protocol(self, pn: str, protocol: str | None) -> DongleRecord:
        rec = self._ensure(pn)
        rec.protocol = protocol
        return rec

    def set_devaddr(self, pn: str, devaddr: int) -> DongleRecord:
        rec = self._ensure(pn)
        rec.devaddr = devaddr
        return rec

    def set_name(self, pn: str, name: str) -> DongleRecord:
        rec = self._ensure(pn)
        rec.name = name
        return rec

    def remove(self, pn: str) -> DongleRecord | None:
        return self._records.pop(pn, None)

    # -- queries ----------------------------------------------------------
    def get(self, pn: str) -> DongleRecord | None:
        return self._records.get(pn)

    def all(self) -> list[DongleRecord]:
        return list(self._records.values())

    def enabled_pns(self) -> list[str]:
        return [r.pn for r in self._records.values() if r.enabled]

    def connected_pns(self) -> list[str]:
        return [
            r.pn
            for r in self._records.values()
            if r.status is DongleStatus.CONNECTED
        ]

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, pn: object) -> bool:
        return pn in self._records

    # -- serialization (for the config-entry store, Phase 3) --------------
    def to_dict(self) -> dict[str, dict]:
        return {pn: rec.to_dict() for pn, rec in self._records.items()}

    def load(self, data: dict[str, dict] | None) -> None:
        """Replace the registry contents from a serialized mapping."""
        self._records = {
            pn: DongleRecord.from_dict(rec) for pn, rec in (data or {}).items()
        }
