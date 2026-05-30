"""Build pollable child targets from an EyBond hub's discovery registry.

Pure (no Home Assistant imports): turns enabled, configured ``DongleRecord``s
into :class:`DeviceTarget`s the coordinator can poll. Each child encodes its
dongle's PN in the URI query so the existing dispatcher/adapter path routes
to the right dongle on a shared listener.
"""
from __future__ import annotations

from ..api.protocols.eybond_discovery import DongleRecord, EybondRegistry
from ..const import (
    DEFAULT_EYBOND_BROADCAST,
    PROTOCOL_PI18,
    PROTOCOL_VOLTRONIC,
)
from .device_target import DeviceTarget

# EyBond dongles forward Voltronic-family ASCII frames; only these protocols
# are pollable through the dongle. Other protocols on a record are ignored.
SUPPORTED_CHILD_PROTOCOLS = (PROTOCOL_VOLTRONIC, PROTOCOL_PI18)


def child_id(rec: DongleRecord) -> str:
    """Stable identity for a child inverter: ``eybond:<pn>:<devaddr>``."""
    return f"eybond:{rec.pn}:{rec.devaddr}"


def build_child_uri(
    rec: DongleRecord,
    bind_host: str,
    bind_port: int,
    broadcast: str = DEFAULT_EYBOND_BROADCAST,
    announce_ip: str | None = None,
) -> str:
    scheme = "eybond-pi18" if rec.protocol == PROTOCOL_PI18 else "eybond"
    uri = f"{scheme}://{bind_host}:{bind_port}/{rec.devaddr}"
    params = [f"pn={rec.pn}"]
    if broadcast and broadcast != DEFAULT_EYBOND_BROADCAST:
        params.append(f"broadcast={broadcast}")
    if announce_ip:
        params.append(f"announce={announce_ip}")
    return uri + "?" + "&".join(params)


def build_child_targets(
    registry: EybondRegistry,
    bind_host: str,
    bind_port: int,
    broadcast: str = DEFAULT_EYBOND_BROADCAST,
    announce_ip: str | None = None,
) -> list[DeviceTarget]:
    """Targets for every enabled record with a supported protocol assigned."""
    targets: list[DeviceTarget] = []
    for rec in registry.all():
        if not rec.enabled:
            continue
        if rec.protocol not in SUPPORTED_CHILD_PROTOCOLS:
            continue
        targets.append(
            DeviceTarget(
                id=child_id(rec),
                uri=build_child_uri(
                    rec, bind_host, bind_port, broadcast, announce_ip
                ),
                protocol=rec.protocol,
                name=rec.name or rec.pn,
            )
        )
    return targets
