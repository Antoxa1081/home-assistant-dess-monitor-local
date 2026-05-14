from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.api.commands.direct_commands import set_output_source_priority, \
    OutputSourcePrioritySetting, ChargeSourcePrioritySetting, set_charge_source_priority, set_max_utility_charge_current
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator
from custom_components.dess_monitor_local.hub import InverterDevice


BATTERY_MODE_LI_VOLTAGE = "Lithium (Voltage)"
BATTERY_MODE_LI_BMS = "Lithium (BMS)"
BATTERY_MODE_LEAD_ACID = "Lead-acid"
BATTERY_MODES = (BATTERY_MODE_LI_VOLTAGE, BATTERY_MODE_LI_BMS, BATTERY_MODE_LEAD_ACID)


#
# SCAN_INTERVAL = timedelta(seconds=30)
# PARALLEL_UPDATES = 1


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: HubConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    hub = config_entry.runtime_data
    coordinator = hub.direct_coordinator
    coordinator_data = hub.direct_coordinator.data

    new_devices = []
    for item in hub.items:
        new_devices.append(InverterOutputPrioritySelect(item, coordinator))
        new_devices.append(InverterChargeSourcePrioritySelect(item, coordinator))
        new_devices.append(InverterMaxUtilityChargingCurrentNumber(item, coordinator))
        new_devices.append(BatteryModeSelect(item))

    if new_devices:
        async_add_entities(new_devices)


class BatteryModeSelect(SelectEntity, RestoreEntity):
    """User-selected battery chemistry / connection preset.

    Drives the SoC algorithm strategy:
      - "Lithium (Voltage)" — LFP-style voltage snap + Coulomb counter,
        eff=0.97, tail=0.05C, hysteresis=0.2V
      - "Lithium (BMS)"     — mirror battery_capacity field (BMS source)
      - "Lead-acid"         — wider hysteresis 0.5V, eff=0.85/0.90, tail=0.02C
    """

    _attr_options = list(BATTERY_MODES)
    _attr_icon = "mdi:battery-sync"

    def __init__(self, inverter_device: InverterDevice):
        self._inverter_device = inverter_device
        self._attr_unique_id = f"{inverter_device.inverter_id}_battery_mode"
        self._attr_name = f"{inverter_device.name} vSoC Battery Mode"
        # Default preserves the existing (LFP voltage-based) behavior so
        # existing users don't see their SoC sensor change after upgrade.
        self._attr_current_option = BATTERY_MODE_LI_VOLTAGE
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter_device.inverter_id)},
            name=inverter_device.name,
            manufacturer="ESS",
            model=inverter_device.inverter_id,
            sw_version=inverter_device.firmware_version,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state in self._attr_options:
            self._attr_current_option = state.state

    async def async_select_option(self, option: str) -> None:
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()


class SelectBase(CoordinatorEntity, SelectEntity):
    # should_poll = True

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._inverter_device = inverter_device

    # To link this entity to the cover device, this property must return an
    # identifiers value matching that used in the cover, but no other information such
    # as name. If name is returned, this entity will then also become a device in the
    # HA UI.
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

    # async def async_added_to_hass(self):
    #     """Run when this Entity has been added to HA."""
    #     # Sensors should also register callbacks to HA when their state changes
    #     self._inverter_device.register_callback(self.async_write_ha_state)
    #
    # async def async_will_remove_from_hass(self):
    #     """Entity being removed from hass."""
    #     # The opposite of async_added_to_hass. Remove any registered call backs here.
    #     self._inverter_device.remove_callback(self.async_write_ha_state)


def resolve_output_priority(device_data):
    return device_data.get('qpiri').get('output_source_priority')


def resolve_chrage_source_priority(device_data):
    return device_data.get('qpiri').get('charger_source_priority')


def resolve_max_utility_charging_current(device_data):
    return device_data.get('qpiri').get('max_utility_charging_current')


class InverterOutputPrioritySelect(SelectBase):
    _attr_current_option = None

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = f"{self._inverter_device.inverter_id}_output_priority"
        self._attr_name = f"{self._inverter_device.name} Output Priority"
        self._attr_options = ['UtilityFirst', 'SBU', 'Solar']

        if coordinator.data is not None:
            data = coordinator.data[self._inverter_device.inverter_id]
            # device_data = self._inverter_device.device_data
            # print('device_data')
            output_source_priority = resolve_output_priority(data)
            self._attr_current_option = output_source_priority
            # self._attr_current_option = resolve_output_priority(data, device_data)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data[self._inverter_device.inverter_id]
        # device_data = self._inverter_device.device_data
        mapper = {
            'UtilityFirst': 'UtilityFirst',
            'SBU': 'SBU',
            'Solar': 'Solar',
            'SolarFirst': 'Solar',
        }
        priority = resolve_output_priority(data)
        mapped_priority = mapper.get(priority, priority)
        self._attr_current_option = mapped_priority
        self.async_write_ha_state()

    async def async_select_option(self, option: str):
        if option in self._attr_options:
            map_priority = {
                'UtilityFirst': OutputSourcePrioritySetting.UTILITY_FIRST,
                'SBU': OutputSourcePrioritySetting.SBU_PRIORITY,
                'Solar': OutputSourcePrioritySetting.SOLAR_FIRST,
            }
            queue = self.hass.data["dess_monitor_local_queue"]
            await queue.enqueue(
                lambda: set_output_source_priority(self._inverter_device.device_data, map_priority[option]))
            self._attr_current_option = option
        await self.coordinator.async_request_refresh()


class InverterChargeSourcePrioritySelect(SelectBase):
    _attr_current_option = None

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = f"{self._inverter_device.inverter_id}_charge_source_priority"
        self._attr_name = f"{self._inverter_device.name} Charge Source Priority"
        self._attr_options = ['UtilityFirst', 'SolarFirst', 'SolarAndUtility']  ## ChargeSourcePriority

        if coordinator.data is not None:
            data = coordinator.data[self._inverter_device.inverter_id]
            self._attr_current_option = resolve_chrage_source_priority(data)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data[self._inverter_device.inverter_id]
        self._attr_current_option = resolve_chrage_source_priority(data)
        self.async_write_ha_state()

    async def async_select_option(self, option: str):
        if option in self._attr_options:
            map_priority = {
                'UtilityFirst': ChargeSourcePrioritySetting.UTILITY_FIRST,
                'SolarFirst': ChargeSourcePrioritySetting.SOLAR_FIRST,
                'SolarAndUtility': ChargeSourcePrioritySetting.SOLAR_AND_UTILITY,
            }
            queue = self.hass.data["dess_monitor_local_queue"]
            await queue.enqueue(
                lambda: set_charge_source_priority(self._inverter_device.device_data, map_priority[option]))
            self._attr_current_option = option
        await self.coordinator.async_request_refresh()


def _normalize_amps(raw) -> str | None:
    """Coerce firmware-reported current ('02.0', '030', '2.0') to canonical str(int)."""
    if raw is None:
        return None
    try:
        return str(int(float(raw)))
    except (TypeError, ValueError):
        return None


class InverterMaxUtilityChargingCurrentNumber(SelectBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = f"{self._inverter_device.inverter_id}_max_utility_charging_current"
        self._attr_name = f"{self._inverter_device.name} Max Utility Charging Current"
        self._attr_options = ['2', '10', '20', '30', '40', '50', '60', '70', '80', '90', '100', '110', '120']
        self._raw_readback: str | None = None

        if coordinator.data is not None:
            data = coordinator.data[self._inverter_device.inverter_id]
            raw = resolve_max_utility_charging_current(data)
            self._raw_readback = raw if raw is None else str(raw)
            self._attr_current_option = _normalize_amps(raw)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data[self._inverter_device.inverter_id]
        raw = resolve_max_utility_charging_current(data)
        self._raw_readback = raw if raw is None else str(raw)
        self._attr_current_option = _normalize_amps(raw)
        self.async_write_ha_state()

    async def async_select_option(self, option: str):
        if option in self._attr_options:
            amps = int(option)
            float_format = self._raw_readback is not None and '.' in self._raw_readback
            queue = self.hass.data["dess_monitor_local_queue"]
            await queue.enqueue(
                lambda: set_max_utility_charge_current(
                    self._inverter_device.device_data, amps, float_format=float_format
                )
            )
            self._attr_current_option = option
        await self.coordinator.async_request_refresh()
