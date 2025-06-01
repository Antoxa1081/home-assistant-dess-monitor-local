import asyncio
import logging
from datetime import timedelta

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from custom_components.dess_monitor_local.api.commands.direct_commands import get_direct_data

_LOGGER = logging.getLogger(__name__)


class DirectCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    devices = []

    def __init__(self, hass: HomeAssistant, config_entry):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="Direct request sensor",
            config_entry=config_entry,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=10),
            # Set always_update to `False` if the data returned from the
            # api can be compared via `__eq__` to avoid duplicate updates
            # being dispatched to listeners
            always_update=False

        )
        # self.my_api = my_api
        # self._device: MyDevice | None = None

    async def _async_setup(self):
        """Set up the coordinator

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """
        self.devices = await self.get_active_devices()

    async def get_active_devices(self):
        device = self.config_entry.options.get("device", None)

        return [device]

    async def _async_update_data(self):
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(30):

                async def fetch_device_data(device):
                    qpigs = await get_direct_data(device, 'QPIGS')
                    qpigs2 = await get_direct_data(device, 'QPIGS2')
                    qpiri = await get_direct_data(device, 'QPIRI')
                    return device, {
                        'qpigs': qpigs,
                        'qpigs2': qpigs2,
                        'qpiri': qpiri
                    }
                    # return device, {
                    #     "qpigs": {
                    #         "grid_voltage": "239.7",
                    #         "grid_frequency": "50.0",
                    #         "ac_output_voltage": "230.2",
                    #         "ac_output_frequency": "50.0",
                    #         "output_apparent_power": "0095",
                    #         "output_active_power": "0095",
                    #         "load_percent": "002",
                    #         "bus_voltage": "399",
                    #         "battery_voltage": "26.50",
                    #         "battery_charging_current": "000",
                    #         "battery_capacity": "068",
                    #         "inverter_heat_sink_temperature": "0040",
                    #         "pv_input_current": "0000",
                    #         "pv_input_voltage": "000.0",
                    #         "scc_battery_voltage": "00.00",
                    #         "battery_discharge_current": "00003",
                    #         "device_status_bits_b7_b0": "00010000",
                    #         "battery_voltage_offset": "00",
                    #         "eeprom_version": "00",
                    #         "pv_charging_power": "00001",
                    #         "device_status_bits_b10_b8": "010"
                    #     },
                    #     "qpigs2": {
                    #         "error": "NAK response received. Command not accepted."
                    #     },
                    #     "qpiri": {
                    #         "rated_grid_voltage": "230.0",
                    #         "rated_input_current": "15.2",
                    #         "rated_ac_output_voltage": "230.0",
                    #         "rated_output_frequency": "50.0",
                    #         "rated_output_current": "15.2",
                    #         "rated_output_apparent_power": "3500",
                    #         "rated_output_active_power": "3500",
                    #         "rated_battery_voltage": "24.0",
                    #         "low_battery_to_ac_bypass_voltage": "24.0",
                    #         "shut_down_battery_voltage": "23.0",
                    #         "bulk_charging_voltage": "29.2",
                    #         "float_charging_voltage": "27.2",
                    #         "battery_type": "UserDefined",
                    #         "max_utility_charging_current": "30",
                    #         "max_charging_current": "050",
                    #         "ac_input_voltage_range": "UPS",
                    #         "output_source_priority": "SBU",
                    #         "charger_source_priority": "SolarFirst",
                    #         "parallel_max_number": "6",
                    #         "reserved_uu": "01",
                    #         "reserved_v": "0",
                    #         "parallel_mode": "Master",
                    #         "high_battery_voltage_to_battery_mode": "26.0",
                    #         "solar_work_condition_in_parallel": "0",
                    #         "solar_max_charging_power_auto_adjust": "1_"
                    #     }
                    # }

                data_map = dict(await asyncio.gather(*map(fetch_device_data, self.devices)))
                return data_map
                # return
        except TimeoutError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise err