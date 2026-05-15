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
            BatteryCapacityNumber(item, hass),
            FullChargeSyncVoltageNumber(item, hass),
            DischargeFloorSoCNumber(item, hass),
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
    """Nominal battery capacity in **ampere-hours**.

    Ah is the unit you can read directly off the battery label or BMS
    datasheet — no need to multiply by a guessed nominal voltage (LFP banks
    in particular are often labelled "48 V" but actually run at ~51.2 V
    nominal, which trips users into 6-7% capacity errors).

    Coulomb counting in the SoC sensor uses this value directly:
    ``SoC% = (accumulated_charge_ah / capacity_ah) × 100``.
    """

    def __init__(self, inverter_device, hass):
        self._inverter_device = inverter_device
        self._hass = hass
        # Fresh entity for the Ah-based capacity. The old Wh-based entity
        # (unique_id = "..._battery_capacity_wh", entity_id =
        # "number.{slug}_vsoc_battery_capacity") is no longer registered by
        # the integration on upgrade — HA marks it as orphan and the user
        # can remove it from the entity registry. This avoids reinterpreting
        # a stored "5000 Wh" as "5000 Ah" and the surprise that would cause.
        self._attr_unique_id = f"{inverter_device.inverter_id}_battery_capacity_ah"
        self._attr_name = f"{inverter_device.name} vSoC Battery Capacity (Ah)"
        self._value = None  # Начальное значение, None

        # Residential banks typically 50–600 Ah; cap at 2000 to cover
        # commercial / parallel setups while keeping the picker usable.
        self._attr_native_min_value = 0
        self._attr_native_max_value = 2000
        self._attr_native_step = 1
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = "Ah"
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


class FullChargeSyncVoltageNumber(NumberEntity, RestoreEntity):
    """User-configurable battery voltage at which vSoC snaps to 100%.

    Overrides the inverter's bulk_charging_voltage (QPIRI setting 26) for
    SoC reset purposes. Useful for LiFePO4 packs where the cells are
    effectively full at, say, 27.4 V while the inverter's bulk target is
    set to 28.0 V — without this override the SoC sensor would never reach
    100% during normal absorption.

    A value of 0 disables the override and falls back to the inverter's
    bulk voltage.
    """

    def __init__(self, inverter_device, hass):
        self._inverter_device = inverter_device
        self._hass = hass
        self._attr_unique_id = f"{inverter_device.inverter_id}_full_charge_sync_voltage"
        self._attr_name = f"{inverter_device.name} vSoC Full Charge Sync Voltage"
        self._value = 0.0  # 0 = disabled, fall back to inverter bulk

        # Range covers 12V, 24V, 48V, and rare 96V banks. Step 0.1 V matches
        # the inverter's own precision for charge thresholds.
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 120.0
        self._attr_native_step = 0.1
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = "V"
        self._attr_icon = "mdi:battery-charging-100"
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
                self._value = 0.0

    @property
    def native_value(self):
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.async_write_ha_state()


class DischargeFloorSoCNumber(NumberEntity, RestoreEntity):
    """User-configurable floor SoC for the "time to floor" calculation.

    Used by the ``vSoC Time to Discharge Floor`` sensor as the target
    end-of-discharge percentage. Common values: 15-20% for LFP (preserves
    cycle life), 50% for lead-acid (DoD limit), 0% for "until empty".
    """

    def __init__(self, inverter_device, hass):
        self._inverter_device = inverter_device
        self._hass = hass
        self._attr_unique_id = f"{inverter_device.inverter_id}_discharge_floor_soc"
        self._attr_name = f"{inverter_device.name} vSoC Discharge Floor"
        self._value = 15.0  # default — sensible for LFP

        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 80.0
        self._attr_native_step = 1.0
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:battery-low"
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
                self._value = 15.0

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