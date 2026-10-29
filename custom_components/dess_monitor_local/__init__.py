from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.dess_monitor_local import frame_log
from custom_components.dess_monitor_local.api.commands.direct_command_queue import (
    stop_all_command_queues,
)
from custom_components.dess_monitor_local.api.protocols.eybond_dongle import (
    shutdown_all_eybond_managers,
)
from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator

from . import debug_panel, eybond_hub, hub
from .const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    ENTRY_KIND_DEVICE,
    ENTRY_KIND_EYBOND_HUB,
)

# List of platforms to support. There should be a matching .py file for each,
# eg <cover.py> and <sensor.py>
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT, Platform.BUTTON,
             Platform.SWITCH]

type HubConfigEntry = ConfigEntry[hub.Hub]


def _entry_kind(entry: ConfigEntry) -> str:
    """Resolve the entry kind (device vs EyBond hub), defaulting to device."""
    return (
        entry.options.get(CONF_ENTRY_KIND)
        or entry.data.get(CONF_ENTRY_KIND)
        or ENTRY_KIND_DEVICE
    )


async def async_setup_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    # Command queues are created lazily, one per physical endpoint, the first
    # time a device is polled/written (see get_command_queue). No global queue
    # is created here anymore — a single shared queue serialized every device
    # against every other and broke when one of several entries unloaded.

    # Show/hide the admin-only live debug panel to match the hub option
    # (Configure -> Debug panel). Re-evaluated on every setup/reload.
    await debug_panel.async_apply_debug_panel(hass)

    if _entry_kind(entry) == ENTRY_KIND_EYBOND_HUB:
        # Hub entry: one listener, many PN-routed children built from the
        # persisted discovery registry. Sets entry.runtime_data (a Hub).
        await eybond_hub.async_setup_eybond_hub(hass, entry)
    else:
        await _migrate_data_to_options(hass, entry)
        direct_coordinator_ctx = DirectCoordinator(hass, entry)
        await asyncio.gather(
            direct_coordinator_ctx.async_config_entry_first_refresh()
        )
        entry.runtime_data = hub.Hub(hass, entry.data["name"], direct_coordinator_ctx)
        await entry.runtime_data.init()

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await asyncio.gather(
        entry.runtime_data.direct_coordinator.async_refresh(),
    )
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Drain the endpoint queues' worker tasks so HA doesn't log "Task was
    # destroyed but it is pending!" at shutdown. The queues are shared across
    # all entries (keyed by physical endpoint), so only drain them once the
    # LAST DESS entry unloads — otherwise a sibling entry loses its queue
    # mid-flight. Done after unload_platforms so platform teardown that still
    # enqueues completes against a live queue.
    others_loaded = any(
        e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
        for e in hass.config_entries.async_entries(DOMAIN)
    )
    if not others_loaded:
        await stop_all_command_queues(hass)
    # Drop the diagnostic frame buffer too — keeps memory clean across
    # reloads and avoids leaking stale frames from a previous device URI.
    frame_log.clear()
    # Free the EyBond TCP listener / UDP announcer so a reload can rebind
    # the port cleanly. Hub entries shut down only their own listener (and
    # persist the registry); legacy single-device entries drain all.
    if _entry_kind(entry) == ENTRY_KIND_EYBOND_HUB:
        await eybond_hub.async_unload_eybond_hub(hass, entry)
    else:
        await shutdown_all_eybond_managers()

    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry):
    # Reload the integration
    await hass.config_entries.async_reload(entry.entry_id)


async def _migrate_data_to_options(hass: HomeAssistant, entry: ConfigEntry):
    new_data = dict(entry.data)
    new_options = dict(entry.options)
    fields = [
        'device',
    ]
    k = 0
    for field in fields:
        if field in new_data:
            k += 1
            new_options[field] = new_data.pop(field)
    if k > 0:
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options
        )
