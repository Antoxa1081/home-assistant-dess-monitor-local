import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import serial.tools.list_ports
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_DEVICE,
    CONF_PROTOCOL,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL_DEVICE,
    CONF_AGENT_DEVICE_ID,
    CONF_UPDATE_INTERVAL,
    PROTOCOL_TCP_ELFIN,
    PROTOCOL_MODBUS,
    PROTOCOL_PI18,
    PROTOCOL_AGENT,
    PROTOCOL_SERIAL,
    PROTOCOLS,
    DEFAULT_TCP_PORT,
    DEFAULT_AGENT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    MAX_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def _list_serial_ports() -> list[str]:
    ports = await asyncio.to_thread(serial.tools.list_ports.comports)
    return [port.device for port in ports]


def _build_device_uri(
    protocol: str,
    host: str,
    port: int,
    serial_device: str,
    agent_device_id: str,
) -> str:
    """Compose the storage `device` string from form fields."""
    if protocol == PROTOCOL_SERIAL:
        return serial_device
    if protocol == PROTOCOL_TCP_ELFIN:
        return f"tcp://{host}:{port}"
    if protocol == PROTOCOL_MODBUS:
        return f"modbus://{host}:{port}"
    if protocol == PROTOCOL_PI18:
        return f"pi18://{host}:{port}"
    if protocol == PROTOCOL_AGENT:
        return f"agent://{host}:{port}/{agent_device_id}"
    return ""


def _parse_device_uri(device: str) -> dict[str, Any]:
    """Best-effort recovery of (protocol, host, port, serial_device, agent_id)
    from a stored device string. Used to pre-fill the options form for
    entries created by older versions of this integration."""
    blank = {
        CONF_PROTOCOL: PROTOCOL_TCP_ELFIN,
        CONF_HOST: "",
        CONF_PORT: DEFAULT_TCP_PORT,
        CONF_SERIAL_DEVICE: "",
        CONF_AGENT_DEVICE_ID: "",
    }
    if not device:
        return blank

    if device.startswith("agent://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_AGENT,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_AGENT_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: (parsed.path or "").lstrip("/"),
        }
    if device.startswith("modbus://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_MODBUS,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("pi18://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_PI18,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("tcp://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_TCP_ELFIN,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    # Legacy "host:port" stored without scheme — assume Elfin TCP.
    if ":" in device and not device.startswith("/") and not device.startswith("\\"):
        host_part, _, port_part = device.rpartition(":")
        try:
            port = int(port_part)
        except ValueError:
            return blank
        return {
            CONF_PROTOCOL: PROTOCOL_TCP_ELFIN,
            CONF_HOST: host_part,
            CONF_PORT: port,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    return {
        CONF_PROTOCOL: PROTOCOL_SERIAL,
        CONF_HOST: "",
        CONF_PORT: DEFAULT_TCP_PORT,
        CONF_SERIAL_DEVICE: device,
        CONF_AGENT_DEVICE_ID: "",
    }


def _update_interval_field() -> Any:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_UPDATE_INTERVAL,
            max=MAX_UPDATE_INTERVAL,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


async def _build_connection_schema(
    protocol: str, defaults: dict[str, Any]
) -> vol.Schema:
    """Per-protocol schema for the connection step.

    Each protocol shows only the fields it actually needs, plus the shared
    update_interval at the bottom — keeps the form short and unambiguous.
    """
    schema: dict = {}

    if protocol == PROTOCOL_SERIAL:
        ports = await _list_serial_ports()
        default_serial = defaults.get(CONF_SERIAL_DEVICE) or ""
        # Make sure a previously-saved port stays selectable even when the
        # adapter is unplugged at the moment of editing.
        if default_serial and default_serial not in ports:
            ports = [default_serial, *ports]
        schema[
            vol.Required(
                CONF_SERIAL_DEVICE,
                default=default_serial or vol.UNDEFINED,
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=p, label=p) for p in ports],
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )
    else:
        default_port = (
            DEFAULT_AGENT_PORT if protocol == PROTOCOL_AGENT else DEFAULT_TCP_PORT
        )
        schema[
            vol.Required(
                CONF_HOST,
                default=defaults.get(CONF_HOST) or vol.UNDEFINED,
            )
        ] = cv.string
        schema[
            vol.Required(
                CONF_PORT,
                default=defaults.get(CONF_PORT) or default_port,
            )
        ] = cv.port
        if protocol == PROTOCOL_AGENT:
            schema[
                vol.Required(
                    CONF_AGENT_DEVICE_ID,
                    default=defaults.get(CONF_AGENT_DEVICE_ID) or vol.UNDEFINED,
                )
            ] = cv.string

    schema[
        vol.Required(
            CONF_UPDATE_INTERVAL,
            default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        )
    ] = _update_interval_field()

    return vol.Schema(schema)


def _validate_connection(
    protocol: str, user_input: dict[str, Any]
) -> dict[str, str]:
    errors: dict[str, str] = {}
    host = (user_input.get(CONF_HOST) or "").strip()
    serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
    agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()

    if protocol == PROTOCOL_SERIAL and not serial_device:
        errors[CONF_SERIAL_DEVICE] = "serial_required"
    if (
        protocol in (PROTOCOL_TCP_ELFIN, PROTOCOL_MODBUS, PROTOCOL_PI18, PROTOCOL_AGENT)
        and not host
    ):
        errors[CONF_HOST] = "host_required"
    if protocol == PROTOCOL_AGENT and not agent_device_id:
        errors[CONF_AGENT_DEVICE_ID] = "agent_device_id_required"
    return errors


def _protocol_schema(default_protocol: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PROTOCOL, default=default_protocol): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=p, label=p) for p in PROTOCOLS
                    ],
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_PROTOCOL,
                )
            )
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        self._name: str | None = None
        self._protocol: str = PROTOCOL_TCP_ELFIN

    async def async_step_user(self, user_input=None):
        """Step 1: hub name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._name = user_input[CONF_NAME].strip()
            if not self._name:
                errors[CONF_NAME] = "name_required"
            else:
                return await self.async_step_protocol()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME): str}),
            errors=errors,
        )

    async def async_step_protocol(self, user_input=None):
        """Step 2: pick the transport protocol."""
        if user_input is not None:
            self._protocol = user_input[CONF_PROTOCOL]
            return await self.async_step_connection()

        return self.async_show_form(
            step_id="protocol",
            data_schema=_protocol_schema(self._protocol),
        )

    async def async_step_connection(self, user_input=None):
        """Step 3: protocol-specific connection details + update interval."""
        protocol = self._protocol
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_connection(protocol, user_input)
            if not errors:
                host = (user_input.get(CONF_HOST) or "").strip()
                port = int(user_input.get(CONF_PORT) or DEFAULT_TCP_PORT)
                serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
                agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()
                update_interval = int(
                    user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                )

                device_value = _build_device_uri(
                    protocol, host, port, serial_device, agent_device_id
                )

                return self.async_create_entry(
                    title=self._name or "Inverter",
                    data={CONF_NAME: self._name},
                    options={
                        CONF_PROTOCOL: protocol,
                        CONF_DEVICE: device_value,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_SERIAL_DEVICE: serial_device,
                        CONF_AGENT_DEVICE_ID: agent_device_id,
                        CONF_UPDATE_INTERVAL: update_interval,
                    },
                )

        defaults = dict(user_input or {})
        schema = await _build_connection_schema(protocol, defaults)
        return self.async_show_form(
            step_id="connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={"protocol": protocol},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Edit transport, address and polling interval after install."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self._config_entry = config_entry
        self._defaults: dict[str, Any] = {}
        self._protocol: str = PROTOCOL_TCP_ELFIN

    def _load_defaults(self) -> None:
        opts = dict(self._config_entry.options)
        # Recover protocol/host/port from the legacy `device` string when the
        # entry predates the new schema.
        parsed = _parse_device_uri(opts.get(CONF_DEVICE, "") or "")
        self._defaults = {
            CONF_HOST: opts.get(CONF_HOST, parsed[CONF_HOST]),
            CONF_PORT: opts.get(CONF_PORT, parsed[CONF_PORT]),
            CONF_SERIAL_DEVICE: opts.get(
                CONF_SERIAL_DEVICE, parsed[CONF_SERIAL_DEVICE]
            ),
            CONF_AGENT_DEVICE_ID: opts.get(
                CONF_AGENT_DEVICE_ID, parsed[CONF_AGENT_DEVICE_ID]
            ),
            CONF_UPDATE_INTERVAL: opts.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
        }
        self._protocol = opts.get(CONF_PROTOCOL, parsed[CONF_PROTOCOL])

    async def async_step_init(self, user_input=None):
        self._load_defaults()
        return await self.async_step_protocol()

    async def async_step_protocol(self, user_input=None):
        if user_input is not None:
            self._protocol = user_input[CONF_PROTOCOL]
            return await self.async_step_connection()

        return self.async_show_form(
            step_id="protocol",
            data_schema=_protocol_schema(self._protocol),
        )

    async def async_step_connection(self, user_input=None):
        protocol = self._protocol
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_connection(protocol, user_input)
            if not errors:
                host = (user_input.get(CONF_HOST) or "").strip()
                port = int(user_input.get(CONF_PORT) or DEFAULT_TCP_PORT)
                serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
                agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()
                update_interval = int(
                    user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                )

                device_value = _build_device_uri(
                    protocol, host, port, serial_device, agent_device_id
                )

                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PROTOCOL: protocol,
                        CONF_DEVICE: device_value,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_SERIAL_DEVICE: serial_device,
                        CONF_AGENT_DEVICE_ID: agent_device_id,
                        CONF_UPDATE_INTERVAL: update_interval,
                    },
                )

        defaults = {**self._defaults, **(user_input or {})}
        schema = await _build_connection_schema(protocol, defaults)
        return self.async_show_form(
            step_id="connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={"protocol": protocol},
        )
