"""Live debug panel — a custom HA sidebar panel + WebSocket API.

HA's Lovelace is too limited for protocol debugging, so this registers a small
custom panel (vanilla JS, no build step) backed by three WebSocket commands:

* ``dess_monitor_local/diag/state``     — one-shot snapshot (dongles + coordinator)
* ``dess_monitor_local/diag/subscribe`` — live event stream from ``diag_hub``
* ``dess_monitor_local/diag/send_frame``— send an arbitrary frame to one dongle

Producers (EyBond transport, coordinator) only emit events while a panel is
subscribed (``diag_hub.active()``), so this costs nothing when unused. Admin-only.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from . import diag_hub, eybond_hub
from .const import (
    CONF_DEBUG_PANEL,
    CONF_ENTRY_KIND,
    DEFAULT_DEBUG_PANEL,
    DOMAIN,
    ENTRY_KIND_EYBOND_HUB,
)
from .diagnostics import _coordinator_section

_LOGGER = logging.getLogger(__name__)

_PANEL_URL_PATH = "dess-debug"
_STATIC_URL = "/dess_monitor_local/dess_debug_panel.js"
# WS commands + the static JS asset register once per HA instance and stay put
# (HA has no clean unregister for them; they're idle when no panel subscribes).
_WS_REGISTERED = "dess_monitor_local_debug_ws_registered"
# Whether the sidebar panel is currently shown — toggled on/off at runtime.
_PANEL_REGISTERED = "dess_monitor_local_debug_panel_shown"
_SUB_QUEUE_MAX = 2000


def _collect_state(hass: HomeAssistant) -> dict:
    """Snapshot of every EyBond hub: discovered dongles + coordinator state."""
    hubs = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENTRY_KIND) != ENTRY_KIND_EYBOND_HUB:
            continue
        runtime = eybond_hub.get_hub_runtime(hass, entry.entry_id)
        dongles = (
            [r.to_dict() for r in runtime.registry.all()] if runtime else []
        )
        hubs.append({
            "entry_id": entry.entry_id,
            "title": entry.title,
            "dongles": dongles,
            "coordinator": _coordinator_section(entry),
        })
    # NOTE: events are NOT included here. The panel polls state every ~2s and
    # resending the (hex-heavy) recent ring on every poll made each refresh grow
    # heavier as the ring filled — the panel felt slower the longer it ran. The
    # event backlog is delivered once via the subscribe replay below instead.
    return {"hubs": hubs}


@websocket_api.websocket_command(
    {vol.Required("type"): "dess_monitor_local/diag/state"}
)
@callback
def _ws_state(hass, connection, msg):
    connection.send_result(msg["id"], _collect_state(hass))


@websocket_api.websocket_command(
    {vol.Required("type"): "dess_monitor_local/diag/subscribe"}
)
@websocket_api.async_response
async def _ws_subscribe(hass, connection, msg):
    """Stream diag_hub events to this panel until it unsubscribes/disconnects."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=_SUB_QUEUE_MAX)
    diag_hub.subscribe(queue)

    async def _pump() -> None:
        try:
            while True:
                event = await queue.get()
                connection.send_message(
                    websocket_api.event_message(msg["id"], event)
                )
        except asyncio.CancelledError:
            pass

    task = hass.async_create_background_task(_pump(), "dess_diag_pump")

    @callback
    def _unsubscribe() -> None:
        task.cancel()
        diag_hub.unsubscribe(queue)

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])
    # Replay the recent ring so a freshly-opened panel isn't blank.
    for event in diag_hub.recent(limit=150):
        connection.send_message(websocket_api.event_message(msg["id"], event))


@websocket_api.websocket_command({
    vol.Required("type"): "dess_monitor_local/diag/send_frame",
    vol.Required("device"): str,         # full eybond URI (carries pn/devaddr)
    vol.Required("command"): str,        # logical command, e.g. "QPIGS"
})
@websocket_api.async_response
async def _ws_send_frame(hass, connection, msg):
    """Send one logical command to a dongle and return the raw decoded reply."""
    from .api.dispatcher import get_direct_data

    try:
        result = await get_direct_data(msg["device"], msg["command"], 8)
    except Exception as err:  # noqa: BLE001 — surface any transport error to the UI
        connection.send_error(msg["id"], "send_failed", str(err))
        return
    connection.send_result(msg["id"], {"result": result})


def _debug_panel_wanted(hass: HomeAssistant) -> bool:
    """True if any EyBond hub entry asks for the sidebar panel."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENTRY_KIND) != ENTRY_KIND_EYBOND_HUB:
            continue
        if entry.options.get(CONF_DEBUG_PANEL, DEFAULT_DEBUG_PANEL):
            return True
    return False


async def _ensure_ws_registered(hass: HomeAssistant) -> None:
    """Register the WS commands + static JS asset once per HA instance."""
    if hass.data.get(_WS_REGISTERED):
        return
    hass.data[_WS_REGISTERED] = True
    websocket_api.async_register_command(hass, _ws_state)
    websocket_api.async_register_command(hass, _ws_subscribe)
    websocket_api.async_register_command(hass, _ws_send_frame)
    js_path = Path(__file__).parent / "www" / "dess_debug_panel.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_STATIC_URL, str(js_path), False)]
    )


async def async_apply_debug_panel(hass: HomeAssistant) -> None:
    """Show/hide the sidebar panel to match the hub option (idempotent).

    Called on every entry setup, so toggling the option and reloading the
    integration brings the sidebar in line with the new setting.
    """
    await _ensure_ws_registered(hass)
    wanted = _debug_panel_wanted(hass)
    shown = bool(hass.data.get(_PANEL_REGISTERED))
    if wanted and not shown:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=_PANEL_URL_PATH,
            webcomponent_name="dess-debug-panel",
            module_url=_STATIC_URL,
            sidebar_title="DESS Debug",
            sidebar_icon="mdi:bug-outline",
            require_admin=True,
        )
        hass.data[_PANEL_REGISTERED] = True
        _LOGGER.info("DESS debug panel registered at /%s", _PANEL_URL_PATH)
    elif shown and not wanted:
        frontend.async_remove_panel(hass, _PANEL_URL_PATH)
        hass.data[_PANEL_REGISTERED] = False
        _LOGGER.info("DESS debug panel removed from sidebar")
