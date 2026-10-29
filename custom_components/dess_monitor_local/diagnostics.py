"""Config-entry / device diagnostics for DESS Monitor Local.

Triggered by HA's "Download Diagnostics" button. Returns a JSON-able
dict that contains everything needed to triage field-shift / parser /
CRC issues from the user's hardware — the primary value is the ring
buffer of recent raw transport frames captured by ``frame_log``.

Anything user-identifying (host, device URI, serial paths) is redacted
by default; the user can edit before sharing publicly.

Device diagnostics are self-contained (target protocol/transport, failure
counts, last-update verdict, that device's frames) because users often
download those instead of the entry-level file.
"""
from typing import Any
from urllib.parse import urlparse

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api.protocols.eybond_dongle import _parse_pn_from_uri, parse_eybond_uri
from .const import DOMAIN
from .frame_log import snapshot as _frame_snapshot
from .frame_log import snapshot_for as _frame_snapshot_for

_REDACT_KEYS = {"host", "device", "serial_device", "agent_device_id"}


def _coordinator_section(entry: ConfigEntry) -> dict[str, Any]:
    if entry.runtime_data is None:
        return {"present": False}
    coordinator = getattr(entry.runtime_data, "direct_coordinator", None)
    if coordinator is None:
        return {"present": False}
    return {
        "present": True,
        "last_update_success": coordinator.last_update_success,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
        "devices": [
            {
                "id": getattr(t, "id", t),
                "uri": getattr(t, "uri", t),
                "protocol": getattr(t, "protocol", None),
                "name": getattr(t, "name", None),
            }
            for t in (coordinator.devices or [])
        ],
        "consecutive_failures": dict(
            getattr(getattr(coordinator, "_failures", None), "_counts", {}) or {}
        ),
        "data": coordinator.data,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), _REDACT_KEYS),
            "options": async_redact_data(dict(entry.options), _REDACT_KEYS),
        },
        "coordinator": _coordinator_section(entry),
        "frames": _frame_snapshot(),
    }


def _device_target(coordinator: Any, device_id: str | None) -> Any:
    """The coordinator's poll target (``DeviceTarget``) for ``device_id``."""
    for target in getattr(coordinator, "devices", None) or []:
        if getattr(target, "id", target) == device_id:
            return target
    return None


def _device_frames(uri: str | None) -> dict[str, list[dict]]:
    """Frames relevant to one device, picked by the tags its transport sets.

    EyBond entries are tagged with the dongle PN + RS485 devaddr (several
    dongles share one listener); the direct transports are untagged and
    one-device-per-endpoint, so all untagged frames belong to it. Unknown
    target → the full snapshot rather than nothing.
    """
    if not uri:
        return _frame_snapshot()
    if urlparse(uri).scheme.startswith("eybond"):
        _, _, devaddr, _, _ = parse_eybond_uri(uri)
        return _frame_snapshot_for("eybond", _parse_pn_from_uri(uri), devaddr)
    return _frame_snapshot_for(None)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a single device entry.

    Self-contained so a user who downloads *device* (not entry) diagnostics
    still gives enough to triage: the target's protocol / transport, its
    consecutive-failure counts, the coordinator's last-update verdict and
    the frames that belong to this device. Works for plain device entries
    and EyBond hub children alike — both keep the coordinator at
    ``entry.runtime_data.direct_coordinator`` keyed by ``DeviceTarget.id``.
    """
    coordinator = None
    if entry.runtime_data is not None:
        coordinator = getattr(entry.runtime_data, "direct_coordinator", None)

    device_id = next(
        (ident for domain, ident in device.identifiers if domain == DOMAIN),
        next((ident for _, ident in device.identifiers), None),
    )
    device_data: dict[str, Any] = {}
    if coordinator is not None and coordinator.data and device_id is not None:
        device_data = coordinator.data.get(device_id, {}) or {}

    target = _device_target(coordinator, device_id) if coordinator is not None else None
    uri = getattr(target, "uri", None)
    target_section: dict[str, Any] = {"present": target is not None}
    if target is not None:
        target_section.update({
            "name": getattr(target, "name", None),
            "protocol": getattr(target, "protocol", None),
            "transport": urlparse(uri).scheme if uri else None,
            **async_redact_data({"uri": uri}, {"uri"}),
        })
        if uri and urlparse(uri).scheme.startswith("eybond"):
            target_section["pn"] = _parse_pn_from_uri(uri)
            target_section["devaddr"] = parse_eybond_uri(uri)[2]

    coordinator_section: dict[str, Any] = {"present": coordinator is not None}
    if coordinator is not None:
        counts = getattr(getattr(coordinator, "_failures", None), "_counts", {}) or {}
        coordinator_section.update({
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "consecutive_failures": dict(counts.get(device_id) or {}),
        })

    return {
        "device": {
            # Legacy device entries use the URI (host / serial path) as id.
            "id": "**REDACTED**" if uri is not None and device_id == uri else device_id,
            "target": target_section,
            "direct_data": device_data,
        },
        "coordinator": coordinator_section,
        "frames": _device_frames(uri),
    }
