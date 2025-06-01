import asyncio
import logging
from typing import Any

import serial.tools.list_ports
import voluptuous as vol
from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required("name"): str,
})


async def validate_input(hass: HomeAssistant, data: dict) -> dict[str, Any]:
    return {"title": data["name"]}


async def list_serial_ports() -> list[str]:
    # return ['/dev/ttyUSB0']
    ports = await asyncio.to_thread(serial.tools.list_ports.comports)
    return [port.device for port in ports]


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    # Pick one of the available connection classes in homeassistant/config_entries.py
    # This tells HA if it should be asking for updates, or it'll be notified of updates
    # automatically. This example uses PUSH, as the dummy hub will notify HA of
    # changes.
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        self._raw_sensors = False
        self._name = None
        self._info = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        # This goes through the steps to take the user through the setup process.
        # Using this it is possible to update the UI and prompt for additional
        # information. This example provides a single form (built from `DATA_SCHEMA`),
        # and when that has some validated input, it calls `async_create_entry` to
        # actually create the HA config entry. Note the "title" value is returned by
        # `validate_input` above.
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                self._info = info
                self._name = user_input['name']
                return await self.async_step_select_devices()
                # return self.async_create_entry(title=info["title"], data={
                #     'username': user_input['username'],
                #     'password_hash': info['password_hash'],
                #     'dynamic_settings': user_input['dynamic_settings'],
                #     # 'raw_sensors': user_input['raw_sensors'],
                # })
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                # The error string is set here, and should be translated.
                # This example does not currently cover translations, see the
                # comments on `DATA_SCHEMA` for further details.
                # Set the error on the `host` field, not the entire form.
                errors["name"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        # If there is no user input or there were errors, show the form again, including any errors that were found with the input.
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_select_devices(self, user_input=None):
        device_ports = await list_serial_ports()

        if user_input is not None:
            device = user_input.get("device")
            if device is not None:
                return self.async_create_entry(
                    title=self._info["title"],
                    data={
                        'name': self._name,
                        'device': device,
                    }
                )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema({
                vol.Required("device"): selector({
                    "select": {
                        "options": [{"value": d, "label": d} for d in device_ports],
                        "multiple": False,
                    }
                })
            }),
        )

    # @staticmethod
    # @callback
    # def async_get_options_flow(config_entry):
    #     return OptionsFlow(config_entry)


# class OptionsFlow(config_entries.OptionsFlow):
#     def __init__(self, config_entry: config_entries.ConfigEntry):
#         self._config_entry = config_entry
#         self._devices = []  # All available devices
#
#     async def async_step_init(self, user_input=None):
#         if user_input is not None:
#             # print('user_input', user_input)
#             return self.async_create_entry(data=user_input)
#
#         return self.async_show_form(
#             step_id="init",
#             data_schema=vol.Schema({
#                 vol.Required(
#                     "devices",
#                     default=self._config_entry.options.get('devices',
#                                                            list(map(lambda x: str(x['pn']), active_devices)))
#                 ): selector({
#                     "select": {
#                         "multiple": True,
#                         "options": [
#                             {"value": str(device['pn']),
#                              "label": f'{device['devalias']}; pn: {device['pn']}; devcode: {device['devcode']}'}
#                             for device in self._devices
#                         ]
#                     }
#                 }),
#                 vol.Optional("dynamic_settings",
#                              default=self._config_entry.options.get('dynamic_settings', False)): bool,
#                 vol.Optional("raw_sensors",
#                              default=self._config_entry.options.get('raw_sensors', False)): bool,
#                 vol.Optional("direct_request_protocol",
#                              default=self._config_entry.options.get('direct_request_protocol', False)): bool,
#             })
#         )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""
