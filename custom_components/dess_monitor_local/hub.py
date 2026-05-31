from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator


class Hub:
    manufacturer = "DESS Monitor Local"

    def __init__(self, hass: HomeAssistant, username: str, direct_coordinator1: DirectCoordinator) -> None:
        self.auth = None
        self._username = username
        self._hass = hass
        self._name = username
        self.direct_coordinator = direct_coordinator1
        self._id = username.lower()
        self.items = []
        self.online = True

    @property
    def hub_id(self) -> str:
        return self._id

    async def rebuild_items(self) -> None:
        """Rebuild ``items`` from the coordinator's current targets.

        Used by the EyBond hub's in-place child reconcile after the poll
        targets change, so the platforms recreate entities for the new set.
        """
        self.items = []
        await self.init()

    async def init(self):
        targets = self.direct_coordinator.devices
        for target in targets:
            # ``targets`` are DeviceTarget descriptors (id / uri / protocol /
            # name). Tolerate a bare string for safety, treating it as a
            # legacy URI where id == uri.
            tid = getattr(target, "id", target)
            uri = getattr(target, "uri", target)
            name = getattr(target, "name", None) or self._username
            protocol = getattr(target, "protocol", None)
            inverter_device = InverterDevice(
                f"{tid}", f"{name}", uri, self, protocol=protocol
            )
            self.items.append(inverter_device)


class InverterDevice:

    def __init__(
        self,
        inverter_pn: str,
        name: str,
        device_data,
        hub: Hub,
        protocol: str | None = None,
    ) -> None:
        self._id = inverter_pn
        self.hub = hub
        self.device_data = device_data
        self.name = name
        # Per-device protocol so a hub can mix protocols across children;
        # platforms read this instead of the single entry-level option.
        self.protocol = protocol
        self.firmware_version = "0.0.1"
        self.model = "DESS Local Device"

    @property
    def inverter_id(self) -> str:
        return self._id

    @property
    def online(self) -> float:
        # if self.hub.direct_coordinator.data is not None and self.inverter_id not in self.hub.direct_coordinator.data:
        #     return False
        return True
