"""EyBond hub entry runtime: listener lifecycle + discovery persistence.

A hub config entry owns one EyBond TCP listener (many dongles, routed by PN)
plus a dedicated Store holding the discovery registry. On setup we start the
listener with the persisted registry, build pollable child targets from the
enabled/configured dongles, and run a normal coordinator + Hub over them so
the existing entity platforms work unchanged.

The discovered-device registry lives in a Store (not entry options) so
volatile lifecycle metadata (``last_seen`` / ``status``) doesn't bloat the
entry or trigger reload churn. A background task persists it periodically and
on unload.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..api.protocols.eybond_discovery import (
    DongleRecord,
    DongleStatus,
    EybondRegistry,
)
from ..api.protocols.eybond_dongle import (
    get_eybond_manager,
    parse_eybond_uri,
    shutdown_eybond_manager,
)
from ..const import (
    CONF_DEVICE,
    CONF_ENTRY_KIND,
    CONF_EYBOND_ANNOUNCE_IP,
    CONF_EYBOND_BIND_HOST,
    CONF_EYBOND_BIND_PORT,
    CONF_EYBOND_BROADCAST,
    CONF_HUB_REVISION,
    CONF_NAME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_EYBOND_BIND_HOST,
    DEFAULT_EYBOND_BIND_PORT,
    DEFAULT_EYBOND_BROADCAST,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    ENTRY_KIND_EYBOND_HUB,
    PROTOCOL_PI18,
    PROTOCOL_VOLTRONIC,
)
from ..coordinators.direct_coordinator import DirectCoordinator
from ..coordinators.eybond_children import build_child_targets
from . import Hub

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_INTERVAL = 30.0


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.eybond_hub.{entry_id}")


def hub_listener_config(entry: ConfigEntry) -> tuple[str, int, str, str | None]:
    """Return ``(bind_host, bind_port, broadcast, announce_ip)`` from options."""
    opts = entry.options
    bind_host = opts.get(CONF_EYBOND_BIND_HOST, DEFAULT_EYBOND_BIND_HOST)
    bind_port = int(opts.get(CONF_EYBOND_BIND_PORT, DEFAULT_EYBOND_BIND_PORT))
    broadcast = opts.get(CONF_EYBOND_BROADCAST, DEFAULT_EYBOND_BROADCAST)
    announce_ip = (opts.get(CONF_EYBOND_ANNOUNCE_IP) or "").strip() or None
    return bind_host, bind_port, broadcast, announce_ip


class EybondHubRuntime:
    """Per-entry runtime state for an EyBond hub (stored in ``hass.data``)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: Store,
        registry: EybondRegistry,
        bind_host: str,
        bind_port: int,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.registry = registry
        self.bind_host = bind_host
        self.bind_port = bind_port
        self._task: asyncio.Task | None = None
        self._last_saved = registry.to_dict()

    def start_persistence(self) -> None:
        self._task = self.hass.async_create_background_task(
            self._persist_loop(), name=f"eybond_hub_persist_{self.entry.entry_id}"
        )

    async def _persist_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(SAVE_INTERVAL)
                await self.async_save()
        except asyncio.CancelledError:
            pass

    async def async_save(self, force: bool = False) -> None:
        snap = self.registry.to_dict()
        if force or snap != self._last_saved:
            await self.store.async_save(snap)
            self._last_saved = snap

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self.async_save(force=True)
        await shutdown_eybond_manager(self.bind_host, self.bind_port)


def get_hub_runtime(hass: HomeAssistant, entry_id: str) -> EybondHubRuntime | None:
    return hass.data.get(DOMAIN, {}).get(entry_id)


async def async_setup_eybond_hub(hass: HomeAssistant, entry: ConfigEntry) -> Hub:
    bind_host, bind_port, broadcast, announce_ip = hub_listener_config(entry)
    name = entry.data.get(CONF_NAME) or "EyBond Hub"

    # Load the persisted discovery registry; stale "connected" statuses from
    # the previous run are cleared (no live sessions yet).
    store = _store(hass, entry.entry_id)
    data = await store.async_load()
    registry = EybondRegistry()
    registry.load(data or {})
    registry.reset_connection_state()

    # Start the listener with our registry so heartbeats populate it directly.
    manager = await get_eybond_manager(
        bind_host, bind_port, broadcast, announce_ip, registry=registry
    )
    # Adopt whatever registry the manager actually uses (in case it pre-existed).
    registry = manager.registry

    targets = build_child_targets(
        registry, bind_host, bind_port, broadcast, announce_ip
    )
    _LOGGER.info(
        "EyBond hub '%s' on %s:%d — %d enabled child(ren), %d discovered",
        name, bind_host, bind_port, len(targets), len(registry),
    )

    coordinator = DirectCoordinator(hass, entry, targets=targets)
    await coordinator.async_config_entry_first_refresh()
    hub_obj = Hub(hass, name, coordinator)
    await hub_obj.init()
    entry.runtime_data = hub_obj

    runtime = EybondHubRuntime(
        hass, entry, store, registry, bind_host, bind_port
    )
    runtime.start_persistence()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    return hub_obj


async def async_unload_eybond_hub(hass: HomeAssistant, entry: ConfigEntry) -> None:
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.stop()


async def async_migrate_legacy_to_hub(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict | str:
    """Convert a legacy single-device ``eybond://`` entry into a hub entry.

    Returns the new options dict on success (the caller applies it via the
    options flow, which reloads the entry as a hub), or an error reason
    string on failure. The connected dongle's PN is captured from the live
    session so the migrated child is fully configured; the original device
    URI is stored as the child's ``legacy_id`` so entity unique_ids — and
    therefore history — are preserved.
    """
    opts = entry.options
    device = opts.get(CONF_DEVICE, "") or ""
    if not (device.startswith("eybond://") or device.startswith("eybond-pi18://")):
        return "not_eybond"

    is_pi18 = device.startswith("eybond-pi18://")
    protocol = PROTOCOL_PI18 if is_pi18 else PROTOCOL_VOLTRONIC
    bind_host, bind_port, devaddr, broadcast, announce_ip = parse_eybond_uri(device)

    # Need the connected dongle's PN to create a fully-configured child.
    manager = await get_eybond_manager(bind_host, bind_port, broadcast, announce_ip)
    pns = manager.identified_pns
    if not pns:
        return "dongle_offline"
    pn = pns[0]

    name = entry.data.get(CONF_NAME) or entry.title or "EyBond"

    registry = EybondRegistry()
    registry.put(
        DongleRecord(
            pn=pn,
            name=name,
            enabled=True,
            protocol=protocol,
            devaddr=devaddr,
            legacy_id=device,  # preserve original unique_ids / history
            status=DongleStatus.CONNECTED,
        )
    )
    await _store(hass, entry.entry_id).async_save(registry.to_dict())

    _LOGGER.info(
        "EyBond: migrated legacy entry '%s' (%s) to hub mode, child PN=%s",
        name, device, pn,
    )
    return {
        CONF_ENTRY_KIND: ENTRY_KIND_EYBOND_HUB,
        CONF_EYBOND_BIND_HOST: bind_host,
        CONF_EYBOND_BIND_PORT: bind_port,
        CONF_EYBOND_BROADCAST: broadcast,
        CONF_EYBOND_ANNOUNCE_IP: announce_ip or "",
        CONF_UPDATE_INTERVAL: int(
            opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        ),
        CONF_HUB_REVISION: int(opts.get(CONF_HUB_REVISION, 0)) + 1,
    }
