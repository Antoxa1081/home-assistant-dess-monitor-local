import asyncio
import logging
from typing import Any

import serial.tools.list_ports
import voluptuous as vol
from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import selector
import homeassistant.helpers.config_validation as cv

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
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        self._raw_sensors = False
        self._name: str | None = None
        self._info: dict[str, Any] | None = None

    async def async_step_user(self, user_input=None):
        """Первый шаг – ввод имени."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                self._info = info
                self._name = user_input["name"]
                return await self.async_step_select_devices()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["name"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_devices(self, user_input=None):
        """Шаг выбора способа подключения: serial или TCP (Elfin/SMG)."""
        device_ports = await list_serial_ports()
        errors: dict[str, str] = {}

        if user_input is not None:
            device = user_input.get("device")
            host = user_input.get("host")
            port = user_input.get("port", 8899)

            if not device and not host:
                errors["base"] = "select_or_enter"
            else:
                # Сохраняем единое поле device:
                #   - либо /dev/ttyUSB0
                #   - либо "10.0.0.106:17824"
                device_value = f"{host}:{port}" if host else device

                return self.async_create_entry(
                    title=self._info.get("title", "Inverter") if self._info else "Inverter",
                    data={
                        "name": self._name,
                        "device": device_value,
                    },
                )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema({
                vol.Optional("device"): selector({
                    "select": {
                        "options": [
                            {"value": d, "label": d}
                            for d in device_ports
                        ],
                        "multiple": False,
                    }
                }),
                vol.Optional("host"): cv.string,
                vol.Optional("port", default=8899): cv.port,
                vol.Optional("update_interval", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            }),
            errors=errors,
            description_placeholders={
                "tip": "Выберите локальный порт или введите IP вашего Elfin EW10A / SMG-II"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Возвращает flow для редактирования настроек (host/port/device)."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Опции интеграции: редактирование host/port или serial-порта."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}

        # Текущие данные – сначала options, потом data как fallback
        data = {
            **self._config_entry.data,
            **self._config_entry.options,
        }
        current_device: str = data.get("device", "") or ""

        # Попробуем выделить host/port из device, если это "ip:port"
        current_host = ""
        current_port = 8899

        if current_device and ":" in current_device and not current_device.startswith("/"):
            # что-то вроде "10.0.0.106:17824"
            host_part, port_part = current_device.rsplit(":", 1)
            current_host = host_part
            try:
                current_port = int(port_part)
            except (ValueError, TypeError):
                current_port = 8899

        # Список доступных serial-портов
        device_ports = await list_serial_ports()

        if user_input is not None:
            device = user_input.get("device")
            host = user_input.get("host")
            port = user_input.get("port", 8899)

            if not device and not host:
                errors["base"] = "select_or_enter"
            else:
                new_device_value = f"{host}:{port}" if host else device

                # Сохраняем в options (data остаётся как было)
                return self.async_create_entry(
                    title="",  # title не меняем
                    data={
                        "device": new_device_value,
                    },
                )

        # Формируем форму для изменения
        # Если у нас текущий host выделен, то по умолчанию в выпадающем списке device оставляем пусто,
        # чтобы не путать пользователя: он сейчас в режиме TCP.
        default_device = "" if current_host else current_device

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # vol.Optional(
                #     "device",
                #     default=default_device,
                # ): selector({
                #     "select": {
                #         "multiple": False,
                #         "options": [
                #             {"value": d, "label": d}
                #             for d in device_ports
                #         ],
                #     }
                # }),
                vol.Optional(
                    "host",
                    default=current_host,
                ): cv.string,
                vol.Optional(
                    "port",
                    default=current_port,
                ): cv.port,
                vol.Optional(
                    "update_interval",
                    default=self._config_entry.options.get(
                        "update_interval",
                        self._config_entry.data.get("update_interval", 10),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            }),
            errors=errors,
            description_placeholders={
                "tip": "Вы можете сменить serial-порт или IP/порт Elfin/SMG-II",
            },
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid auth."""
