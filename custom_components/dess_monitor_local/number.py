from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator
from custom_components.dess_monitor_local.hub import InverterDevice


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
            FloatVoltageWindowNumber(item, hass),
            FloatNoiseFloorNumber(item, hass),
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


class _VSocConfigNumber(NumberEntity, RestoreEntity):
    """Base for the user-tunable vSoC parameters.

    Common behavior: value restored across HA restarts, BOX input mode,
    and ``EntityCategory.CONFIG`` so HA files them under the device's
    "Configuration" section instead of cluttering the main dashboard.

    Subclasses set the class attributes below plus the standard
    ``_attr_native_*`` range / unit / icon fields.
    """

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    # Subclass contract:
    _id_suffix: str = ""        # unique_id tail
    _name_suffix: str = ""      # friendly-name tail (drives the entity_id)
    _default_value: float | None = 0.0

    def __init__(self, inverter_device, hass):
        self._inverter_device = inverter_device
        self._hass = hass
        self._attr_unique_id = f"{inverter_device.inverter_id}_{self._id_suffix}"
        self._attr_name = f"{inverter_device.name} {self._name_suffix}"
        self._value = self._default_value
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
                self._value = self._default_value

    @property
    def native_value(self):
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.async_write_ha_state()


class BatteryCapacityNumber(_VSocConfigNumber):
    """Nominal battery capacity in **ampere-hours**.

    Ah is the unit you can read directly off the battery label or BMS
    datasheet — no need to multiply by a guessed nominal voltage (LFP
    banks in particular are often labelled "48 V" but actually run at
    ~51.2 V nominal, a 6-7% capacity error trap).

    Coulomb counting in the SoC sensor uses this value directly:
    ``SoC% = (accumulated_charge_ah / capacity_ah) × 100``.
    """

    # Stays None until the user enters their nameplate value — the SoC
    # sensor reports unavailable until then.
    _id_suffix = "battery_capacity_ah"
    _name_suffix = "vSoC Battery Capacity (Ah)"
    _default_value = None

    # Residential banks 50–600 Ah; cap at 2000 for commercial / parallel.
    _attr_native_min_value = 0
    _attr_native_max_value = 2000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "Ah"
    _attr_icon = "mdi:battery"


class FullChargeSyncVoltageNumber(_VSocConfigNumber):
    """Battery voltage at which vSoC snaps to 100%.

    Overrides the inverter's bulk_charging_voltage for SoC-reset purposes.
    Useful for LiFePO4 packs that are effectively full at, say, 27.4 V
    while the inverter's bulk target sits at 28.0 V. ``0`` disables the
    override and falls back to the inverter's bulk voltage.
    """

    _id_suffix = "full_charge_sync_voltage"
    _name_suffix = "vSoC Full Charge Sync Voltage"
    _default_value = 0.0

    _attr_native_min_value = 0.0
    _attr_native_max_value = 120.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "V"
    _attr_icon = "mdi:battery-charging-100"


class DischargeFloorSoCNumber(_VSocConfigNumber):
    """Floor SoC for the "time to discharge floor" / backup-time sensors.

    Common values: 15-20% for LFP (cycle-life preservation), 50% for
    lead-acid (DoD limit), 0% for "until empty".
    """

    _id_suffix = "discharge_floor_soc"
    _name_suffix = "vSoC Discharge Floor"
    _default_value = 15.0

    _attr_native_min_value = 0.0
    _attr_native_max_value = 80.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-low"


class FloatVoltageWindowNumber(_VSocConfigNumber):
    """Float-mode deadband — voltage window.

    Half-width of the dead-zone around the inverter's float setpoint
    within which the SoC integrator treats the tick as "in float".
    Together with the noise floor it cancels the SoC drift caused by
    inverters that quantise discharge current to integer Amperes.
    See ``vSoC Float Noise Floor`` and the ``vSoC Float Deadband`` switch.
    """

    _id_suffix = "float_voltage_window"
    _name_suffix = "vSoC Float Voltage Window"
    _default_value = 0.5

    _attr_native_min_value = 0.0
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "V"
    _attr_icon = "mdi:sine-wave"


class FloatNoiseFloorNumber(_VSocConfigNumber):
    """Float-mode deadband — current noise floor.

    Reported battery current at or below this magnitude (while voltage is
    within the float window) is treated as quantisation noise and not
    integrated. Raise it for larger banks whose firmware quantises in
    2-3 A steps; lower it toward 0 to make the deadband stricter.
    """

    _id_suffix = "float_noise_floor"
    _name_suffix = "vSoC Float Noise Floor"
    _default_value = 1.5

    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "A"
    _attr_icon = "mdi:current-dc"
