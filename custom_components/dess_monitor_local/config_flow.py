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


def _build_device_uri(
    device: str | None,
    host: str | None,
    port: int,
    agent_device_id: str,
) -> str:
    """Assemble the stored `device` field from form inputs.

    Precedence:
      1. `agent_device_id` filled → agent://host:port/providerDeviceId
      2. `host` filled            → host:port    (Elfin TCP / Modbus path)
      3. otherwise                → serial port selection

    The agent branch requires host:port just like the TCP branch — we
    reuse the same two inputs so the form stays compact.
    """
    if agent_device_id and host:
        return f"agent://{host}:{port}/{agent_device_id}"
    if host:
        return f"{host}:{port}"
    return device or ""


def _parse_device_uri(device: str) -> tuple[str, int, str]:
    """Best-effort split of the stored `device` string into (host, port,
    agent_device_id) for pre-filling the edit form.

    Returns blanks for fields that don't apply to the detected shape.
    """
    if not device:
        return "", 8899, ""
    if device.startswith("agent://"):
        # Reuse urlparse by poking at the non-standard scheme — Python
        # still extracts hostname/port/path correctly.
        from urllib.parse import urlparse
        parsed = urlparse(device)
        if parsed.hostname and parsed.port:
            return parsed.hostname, parsed.port, parsed.path.lstrip("/")
        return "", 8899, ""
    if ":" in device and not device.startswith("/"):
        host_part, port_part = device.rsplit(":", 1)
        try:
            return host_part, int(port_part), ""
        except ValueError:
            return host_part, 8899, ""
    # Serial path — return device value verbatim via the `device` arg of
    # the caller; host/port defaults keep the form valid.
    return "", 8899, ""


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
        """Шаг выбора способа подключения: serial, TCP (Elfin/SMG) или local agent."""
        device_ports = await list_serial_ports()
        errors: dict[str, str] = {}

        if user_input is not None:
            device = user_input.get("device")
            host = user_input.get("host")
            port = user_input.get("port", 8899)
            agent_device_id = (user_input.get("agent_device_id") or "").strip()

            if not device and not host:
                errors["base"] = "select_or_enter"
            else:
                # Сохраняем единое поле device:
                #   - agent://host:port/providerDeviceId — локальный агент (если задан agent_device_id)
                #   - /dev/ttyUSB0                       — serial
                #   - host:port                          — Elfin TCP / Modbus
                device_value = _build_device_uri(
                    device, host, port, agent_device_id
                )

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
                # If filled, host/port point at the solar-system-agent's
                # debug HTTP server (default 8787) and we fetch pre-decoded
                # snapshots for this device id. Leave empty for classic
                # Elfin/Modbus TCP.
                vol.Optional("agent_device_id"): cv.string,
                vol.Optional("update_interval", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            }),
            errors=errors,
            description_placeholders={
                "tip": (
                    "Выберите локальный порт, введите IP/порт Elfin/SMG-II, "
                    "или укажите providerDeviceId локального solar-system-agent "
                    "(порт по умолчанию 8787)."
                ),
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

        # Распарсим текущий device в (host, port, agent_device_id)
        # чтобы предзаполнить форму независимо от режима.
        current_host, current_port, current_agent_device_id = _parse_device_uri(
            current_device,
        )

        # Список доступных serial-портов
        device_ports = await list_serial_ports()

        if user_input is not None:
            device = user_input.get("device")
            host = user_input.get("host")
            port = user_input.get("port", 8899)
            agent_device_id = (user_input.get("agent_device_id") or "").strip()

            if not device and not host:
                errors["base"] = "select_or_enter"
            else:
                new_device_value = _build_device_uri(
                    device, host, port, agent_device_id
                )

                # Сохраняем в options (data остаётся как было)
                return self.async_create_entry(
                    title="",  # title не меняем
                    data={
                        "device": new_device_value,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "host",
                    default=current_host,
                ): cv.string,
                vol.Optional(
                    "port",
                    default=current_port,
                ): cv.port,
                vol.Optional(
                    "agent_device_id",
                    default=current_agent_device_id,
                ): cv.string,
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
                "tip": (
                    "Elfin/SMG-II: заполните Host+Port, оставьте Agent Device ID пустым. "
                    "Локальный agent: Host+Port указывают на его HTTP API (обычно 8787), "
                    "а Agent Device ID — providerDeviceId устройства."
                ),
            },
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid auth."""
