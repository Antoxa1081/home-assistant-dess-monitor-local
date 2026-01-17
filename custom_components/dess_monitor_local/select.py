from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.api.commands.direct_commands import set_output_source_priority, \
    OutputSourcePrioritySetting, ChargeSourcePrioritySetting, set_charge_source_priority, set_max_utility_charge_current
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.coordinators.direct_coordinator import DirectCoordinator
from custom_components.dess_monitor_local.hub import InverterDevice


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

    if new_devices:
        async_add_entities(new_devices)


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


class InverterMaxUtilityChargingCurrentNumber(SelectBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = f"{self._inverter_device.inverter_id}_max_utility_charging_current"
        self._attr_name = f"{self._inverter_device.name} Max Utility Charging Current"
        self._attr_options = ['2', '10', '20', '30', '40', '50', '60', '70', '80', '90', '100', '110', '120']

        if coordinator.data is not None:
            data = coordinator.data[self._inverter_device.inverter_id]
            self._attr_current_option = resolve_chrage_source_priority(data)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data[self._inverter_device.inverter_id]
        self._attr_current_option = resolve_max_utility_charging_current(data)
        self.async_write_ha_state()

    async def async_select_option(self, option: str):
        if option in self._attr_options:
            queue = self.hass.data["dess_monitor_local_queue"]
            await queue.enqueue(lambda: set_max_utility_charge_current(self._inverter_device.device_data, int(option)))
            self._attr_current_option = option
        await self.coordinator.async_request_refresh()
