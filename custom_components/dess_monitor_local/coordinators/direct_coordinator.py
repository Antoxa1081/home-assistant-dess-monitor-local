import asyncio
import logging
from datetime import datetime, timedelta

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from custom_components.dess_monitor_local.api.dispatcher import get_direct_data
from custom_components.dess_monitor_local.const import (
    CONF_DEVICE,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_STRICT_CRC,
    CONF_UPDATE_INTERVAL,
    DEFAULT_STRICT_CRC,
    DEFAULT_UPDATE_INTERVAL,
    PROTOCOL_VOLTRONIC,
)
from custom_components.dess_monitor_local.coordinators.device_target import DeviceTarget
from custom_components.dess_monitor_local.coordinators.failure_tracker import (
    FailureOutcome,
    FailureTracker,
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

    def __init__(self, hass: HomeAssistant, config_entry, targets=None):
        """Initialize my coordinator.

        ``targets`` is an explicit list of :class:`DeviceTarget` to poll
        (used by the EyBond hub, where children are derived from the
        discovery registry). When ``None``, the coordinator falls back to
        the legacy single ``CONF_DEVICE`` from the entry options.
        """
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
        self._targets = targets
        # Per-(target id, command) consecutive-failure counter + freeze policy.
        self._failures = FailureTracker(self._MAX_CONSECUTIVE_FAILURES)
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

    def set_targets(self, targets) -> None:
        """Swap the explicit poll-target list at runtime.

        Used by the EyBond hub's in-place child reconcile (no entry reload):
        the next poll cycle reads ``self.devices``, and ``_async_update_data``
        snapshots it per cycle, so replacing the list between cycles is safe.
        """
        self._targets = list(targets)
        self.devices = list(targets)

    async def get_active_devices(self):
        # Explicit targets (EyBond hub children) take precedence.
        if self._targets is not None:
            return list(self._targets)

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
        # Legacy single-device entry: id == uri preserves existing
        # entity unique_ids and HA device identifiers.
        protocol = self.config_entry.options.get(CONF_PROTOCOL, PROTOCOL_VOLTRONIC)
        name = self.config_entry.data.get(CONF_NAME) or "Inverter"
        return [DeviceTarget(id=device, uri=device, protocol=protocol, name=name)]

    async def _async_update_data(self):
        strict_crc = bool(
            self.config_entry.options.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC)
        )
        prev_data = self.data or {}
        queue = self.hass.data["dess_monitor_local_queue"]

        async def fetch_with_retry(key: str, uri: str, cmd: str, section: str) -> dict:
            """Read a command with one fast retry, then apply the pure
            freeze/unavailable policy (see FailureTracker).

            ``key`` is the target's stable id (failure tracking + last-known
            lookup); ``uri`` is the transport address the command is sent to.
            """
            for attempt in range(2):
                try:
                    result = await queue.enqueue(
                        lambda d=uri, c=cmd: get_direct_data(
                            d, c, 30, strict_crc=strict_crc
                        )
                    )
                except Exception as err:  # transport raised unexpectedly
                    _LOGGER.debug(
                        "%s/%s attempt %d raised %r", key, cmd, attempt + 1, err
                    )
                    result = None
                if result:
                    self._failures.on_success(key, cmd)
                    return result
                if attempt == 0:
                    await asyncio.sleep(self._RETRY_DELAY_S)

            count = self._failures.on_failure(key, cmd)
            last_known = (prev_data.get(key) or {}).get(section) or {}
            data, outcome = self._failures.resolve(count, last_known)
            if outcome is FailureOutcome.FREEZE:
                _LOGGER.debug(
                    "%s/%s read failed (consecutive=%d/%d); freezing on last known data",
                    key, cmd, count, self._MAX_CONSECUTIVE_FAILURES,
                )
            elif outcome is FailureOutcome.UNAVAILABLE:
                _LOGGER.warning(
                    "%s/%s failed %d times in a row; flipping to unavailable",
                    key, cmd, count,
                )
            return data

        try:
            async with async_timeout.timeout(120):
                async def fetch_device_data(target):
                    key = target.id
                    uri = target.uri
                    qpigs = await fetch_with_retry(key, uri, 'QPIGS', 'qpigs')
                    qpiri = await fetch_with_retry(key, uri, 'QPIRI', 'qpiri')
                    # QMOD = current operating mode (PowerOn / Standby /
                    # Line / Battery / Fault). Cheap one-byte answer; gives
                    # us a real status sensor for automations instead of
                    # parsing the QPIGS status bits string.
                    qmod = await fetch_with_retry(key, uri, 'QMOD', 'qmod')
                    # QPIGS2 = second PV input on dual-MPPT models. Many
                    # inverters NAK it, in which case fetch_with_retry
                    # returns {} and the PV2 sensors stay unavailable —
                    # zero cost for the rest of users.
                    qpigs2 = await fetch_with_retry(key, uri, 'QPIGS2', 'qpigs2')
                    # QPIWS = warning/fault bitstring. PI18 inverters NAK
                    # this and respond to QFWS instead; fetch both — the
                    # one that doesn't apply just returns ``{}`` and
                    # downstream sensors stay unavailable.
                    qpiws = await fetch_with_retry(key, uri, 'QPIWS', 'qpiws')
                    qfws = await fetch_with_retry(key, uri, 'QFWS', 'qfws')
                    return key, {
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
