"""Config-entry / device diagnostics for DESS Monitor Local.

Triggered by HA's "Download Diagnostics" button. Returns a JSON-able
dict that contains everything needed to triage field-shift / parser /
CRC issues from the user's hardware — the primary value is the ring
buffer of recent raw transport frames captured by ``frame_log``.

Anything user-identifying (host, device URI, serial paths) is redacted
by default; the user can edit before sharing publicly.
"""
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .frame_log import snapshot as _frame_snapshot

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
        "devices": coordinator.devices,
        "consecutive_failures": dict(
            getattr(coordinator, "_consecutive_failures", {}) or {}
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


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a single device entry."""
    coordinator = None
    if entry.runtime_data is not None:
        coordinator = getattr(entry.runtime_data, "direct_coordinator", None)

    device_id = list(device.identifiers)[0][1] if device.identifiers else None
    device_data: dict[str, Any] = {}
    if coordinator is not None and coordinator.data and device_id is not None:
        device_data = coordinator.data.get(device_id, {}) or {}

    return {
        "device": {
            "id": device_id,
            "direct_data": device_data,
        },
        "frames": _frame_snapshot(),
    }
