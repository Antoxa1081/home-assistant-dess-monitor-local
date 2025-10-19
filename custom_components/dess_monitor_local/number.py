from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator
from custom_components.dess_monitor_local.hub import InverterDevice


# SCAN_INTERVAL = timedelta(seconds=30)
# PARALLEL_UPDATES = 1


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: HubConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    hub = config_entry.runtime_data
    new_devices = []

    for item in hub.items:
        new_devices.extend([
            BatteryCapacityNumber(item, hass)
        ])
    if new_devices:
        async_add_entities(new_devices)


class NumberBase(CoordinatorEntity, NumberEntity):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._inverter_device = inverter_device


    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {
            "identifiers": {(DOMAIN, self._inverter_device.inverter_id)},
            "name": self._inverter_device.name,
            "sw_version": self._inverter_device.firmware_version,
            "model": self._inverter_device.inverter_id,
            "manufacturer": 'ESS'
        }

    @property
    def available(self) -> bool:
        """Return True if inverter_device and hub is available."""
        return True
        # return self._inverter_device.online and self._inverter_device.hub.online

    @property
    def data(self):
        return self.coordinator.data[self._inverter_device.inverter_id]


class BatteryCapacityNumber(NumberEntity, RestoreEntity):
    def __init__(self, inverter_device, hass):
        self._inverter_device = inverter_device
        self._hass = hass
        self._attr_unique_id = f"{inverter_device.inverter_id}_battery_capacity_wh"
        self._attr_name = f"{inverter_device.name} vSoC Battery Capacity"
        self._value = None  # Начальное значение, None

        self._attr_native_min_value = 0
        self._attr_native_max_value = 100000
        self._attr_native_step = 10
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = "Wh"
        self._attr_icon = "mdi:battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter_device.inverter_id)},
            name=inverter_device.name,
            manufacturer="ESS",
            model=inverter_device.inverter_id,
            sw_version=inverter_device.firmware_version,
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._value = float(state.state)
            except ValueError:
                self._value = None

    @property
    def native_value(self):
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.async_write_ha_state()

# def resolve_max_utility_charging_current(device_data):
#     return device_data.get('qpiri').get('max_utility_charging_current')
#
#
# class InverterMaxUtilityChargingCurrentNumber(NumberBase):
#     def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
#         super().__init__(inverter_device, coordinator)
#         self._attr_unique_id = f"{self._inverter_device.inverter_id}_max_utility_charging_current"
#         self._attr_name = f"{self._inverter_device.name} Max Utility Charging Current"
#     @callback
#     def _handle_coordinator_update(self) -> None:
#         data = self.coordinator.data[self._inverter_device.inverter_id]
#         # device_data = self._inverter_device.device_data
#         self._attr_current_option = resolve_max_utility_charging_current(data)
#         self.async_write_ha_state()