import asyncio
import logging
from datetime import timedelta

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from custom_components.dess_monitor_local.api.commands.serial_comm import get_direct_data

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

                data_map = dict(await asyncio.gather(*map(fetch_device_data, self.devices)))
                return data_map
                # return
        except TimeoutError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise err