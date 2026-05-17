import asyncio
import logging
from datetime import timedelta, datetime

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from custom_components.dess_monitor_local.api.dispatcher import get_direct_data
from custom_components.dess_monitor_local.const import (
    CONF_DEVICE,
    CONF_UPDATE_INTERVAL,
    CONF_STRICT_CRC,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_STRICT_CRC,
)

_LOGGER = logging.getLogger(__name__)


class DirectCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    devices = []

    # Resilience to transient transport errors (CRC mismatches, brief
    # buffer corruption, gateway hiccups). One fast retry per command,
    # then up to N-1 consecutive failures fall back to the last known
    # sub-dict before the entity finally goes to "unavailable".
    _RETRY_DELAY_S = 0.25
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, hass: HomeAssistant, config_entry):
        """Initialize my coordinator."""
        interval_seconds = int(
            config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="Direct request sensor",
            config_entry=config_entry,
            update_interval=timedelta(seconds=interval_seconds),
            # Set always_update to `False` if the data returned from the
            # api can be compared via `__eq__` to avoid duplicate updates
            # being dispatched to listeners
            always_update=False

        )
        # Per-(device, command) consecutive-failure counter. Reset on any
        # successful read.
        self._consecutive_failures: dict[str, dict[str, int]] = {}
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
        device = self.config_entry.options.get(CONF_DEVICE, None)
        if not device:
            # No device URI configured (entry created but never finished setup,
            # or options got wiped). Returning an empty list lets the
            # coordinator complete without crashing in `_async_update_data`
            # where dispatcher code does ``device.startswith(...)``.
            _LOGGER.warning(
                "No device URI configured for entry %s; nothing to poll",
                self.config_entry.entry_id,
            )
            return []
        return [device]

    async def _async_update_data(self):
        strict_crc = bool(
            self.config_entry.options.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC)
        )
        prev_data = self.data or {}
        queue = self.hass.data["dess_monitor_local_queue"]

        async def fetch_with_retry(device: str, cmd: str, section: str) -> dict:
            """Read a command with one fast retry, falling back to the last
            known section if both attempts return empty. After
            ``_MAX_CONSECUTIVE_FAILURES`` failures in a row the section goes
            empty so HA flips the entities to unavailable."""
            failures = self._consecutive_failures.setdefault(device, {})
            for attempt in range(2):
                try:
                    result = await queue.enqueue(
                        lambda d=device, c=cmd: get_direct_data(
                            d, c, 30, strict_crc=strict_crc
                        )
                    )
                except Exception as err:  # transport raised unexpectedly
                    _LOGGER.debug(
                        "%s/%s attempt %d raised %r", device, cmd, attempt + 1, err
                    )
                    result = None
                if result:
                    failures[cmd] = 0
                    return result
                if attempt == 0:
                    await asyncio.sleep(self._RETRY_DELAY_S)

            failures[cmd] = failures.get(cmd, 0) + 1
            if failures[cmd] < self._MAX_CONSECUTIVE_FAILURES:
                last = (prev_data.get(device) or {}).get(section) or {}
                if last:
                    _LOGGER.debug(
                        "%s/%s read failed (consecutive=%d/%d); freezing on last known data",
                        device,
                        cmd,
                        failures[cmd],
                        self._MAX_CONSECUTIVE_FAILURES,
                    )
                    return last
            else:
                _LOGGER.warning(
                    "%s/%s failed %d times in a row; flipping to unavailable",
                    device,
                    cmd,
                    failures[cmd],
                )
            return {}

        try:
            async with async_timeout.timeout(120):
                async def fetch_device_data(device):
                    qpigs = await fetch_with_retry(device, 'QPIGS', 'qpigs')
                    qpiri = await fetch_with_retry(device, 'QPIRI', 'qpiri')
                    # QMOD = current operating mode (PowerOn / Standby /
                    # Line / Battery / Fault). Cheap one-byte answer; gives
                    # us a real status sensor for automations instead of
                    # parsing the QPIGS status bits string.
                    qmod = await fetch_with_retry(device, 'QMOD', 'qmod')
                    # QPIGS2 = second PV input on dual-MPPT models. Many
                    # inverters NAK it, in which case fetch_with_retry
                    # returns {} and the PV2 sensors stay unavailable —
                    # zero cost for the rest of users.
                    qpigs2 = await fetch_with_retry(device, 'QPIGS2', 'qpigs2')
                    # QPIWS = warning/fault bitstring. PI18 inverters NAK
                    # this and respond to QFWS instead; fetch both — the
                    # one that doesn't apply just returns ``{}`` and
                    # downstream sensors stay unavailable.
                    qpiws = await fetch_with_retry(device, 'QPIWS', 'qpiws')
                    qfws = await fetch_with_retry(device, 'QFWS', 'qfws')
                    return device, {
                        "timestamp": datetime.now(),
                        'qpigs': qpigs,
                        'qpiri': qpiri,
                        'qmod': qmod,
                        'qpigs2': qpigs2,
                        'qpiws': qpiws,
                        'qfws': qfws,
                    }
                    # return device, {
                    #     "timestamp": datetime.now(),
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
                # print('devices', self.devices, data_map)
                return data_map
        except TimeoutError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise err
