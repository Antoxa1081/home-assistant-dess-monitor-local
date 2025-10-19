from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Return diagnostics for a config entry."""
    return {
        "config_entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, []),
            "options": dict(entry.options),
        },
    }


async def async_get_device_diagnostics(
        hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a device entry."""
    return {
        "device": {
            'direct_data': (entry.runtime_data.direct_coordinator.data or {}) \
                .get(list(device.identifiers)[0][1], {})
        }
    }
