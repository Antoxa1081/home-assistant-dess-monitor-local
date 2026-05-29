import asyncio
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import homeassistant.helpers.config_validation as cv
import serial.tools.list_ports
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AGENT_DEVICE_ID,
    CONF_DEVICE,
    CONF_EYBOND_ANNOUNCE_IP,
    CONF_EYBOND_BROADCAST,
    CONF_EYBOND_DEVADDR,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_SERIAL_DEVICE,
    CONF_STRICT_CRC,
    CONF_TRANSPORT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AGENT_PORT,
    DEFAULT_EYBOND_ANNOUNCE_IP,
    DEFAULT_EYBOND_BIND_HOST,
    DEFAULT_EYBOND_BIND_PORT,
    DEFAULT_EYBOND_BROADCAST,
    DEFAULT_EYBOND_DEVADDR,
    DEFAULT_STRICT_CRC,
    DEFAULT_TCP_PORT,
    DEFAULT_TRANSPORT_BY_PROTOCOL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LEGACY_PROTOCOL_TRANSPORT,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    PROTOCOL_AGENT,
    PROTOCOL_MODBUS,
    PROTOCOL_PI18,
    PROTOCOL_VOLTRONIC,
    PROTOCOLS,
    TRANSPORT_AGENT_HTTP,
    TRANSPORT_EYBOND,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
    TRANSPORT_TCP_ELFIN,
    TRANSPORTS_BY_PROTOCOL,
)

# Protocols where the request/response framing carries a CRC and the
# strict-CRC option is meaningful. Modbus has its own integrated check
# already; agent receives pre-decoded JSON.
_CRC_CAPABLE_PROTOCOLS = (PROTOCOL_VOLTRONIC, PROTOCOL_PI18)

_LOGGER = logging.getLogger(__name__)


async def _list_serial_ports() -> list[str]:
    ports = await asyncio.to_thread(serial.tools.list_ports.comports)
    return [port.device for port in ports]


def _default_transport(protocol: str) -> str:
    return DEFAULT_TRANSPORT_BY_PROTOCOL.get(protocol, TRANSPORT_TCP_ELFIN)


def _normalize_protocol_transport(
    protocol: str | None, transport: str | None = None
) -> tuple[str, str]:
    """Return logical protocol + compatible transport."""
    if protocol in LEGACY_PROTOCOL_TRANSPORT:
        protocol, legacy_transport = LEGACY_PROTOCOL_TRANSPORT[protocol]
        if transport is None:
            transport = legacy_transport

    if protocol not in PROTOCOLS:
        protocol = PROTOCOL_VOLTRONIC

    supported = TRANSPORTS_BY_PROTOCOL.get(protocol, ())
    if transport not in supported:
        transport = _default_transport(protocol)
    return protocol, transport


def _build_device_uri(
    protocol: str,
    transport: str,
    host: str,
    port: int,
    serial_device: str,
    agent_device_id: str,
    eybond_devaddr: int = DEFAULT_EYBOND_DEVADDR,
    eybond_broadcast: str = DEFAULT_EYBOND_BROADCAST,
    eybond_announce_ip: str = DEFAULT_EYBOND_ANNOUNCE_IP,
) -> str:
    """Compose the storage `device` string from form fields."""
    protocol, transport = _normalize_protocol_transport(protocol, transport)

    if protocol == PROTOCOL_AGENT:
        return f"agent://{host}:{port}/{agent_device_id}"
    if protocol == PROTOCOL_MODBUS:
        return f"modbus://{host}:{port}"
    if protocol == PROTOCOL_PI18:
        if transport == TRANSPORT_SERIAL:
            return f"pi18-serial://{serial_device}"
        if transport == TRANSPORT_EYBOND:
            # eybond-pi18://bind_host:bind_port/devaddr?params
            uri = f"eybond-pi18://{host}:{port}/{eybond_devaddr}"
            params: list[str] = []
            if eybond_broadcast and eybond_broadcast != DEFAULT_EYBOND_BROADCAST:
                params.append(f"broadcast={eybond_broadcast}")
            if eybond_announce_ip:
                params.append(f"announce={eybond_announce_ip}")
            if params:
                uri += "?" + "&".join(params)
            return uri
        return f"pi18://{host}:{port}"
    if protocol != PROTOCOL_VOLTRONIC:
        return ""

    if transport == TRANSPORT_SERIAL:
        return serial_device
    if transport == TRANSPORT_TCP_ELFIN:
        return f"tcp://{host}:{port}"
    if transport == TRANSPORT_EYBOND:
        # host = bind interface (usually 0.0.0.0); port = listen port.
        # devaddr selects the RS485 slave; broadcast is the UDP target.
        # announce_ip is what we tell the dongle to connect back to —
        # critical for Docker bridge mode where auto-detect returns the
        # container IP instead of the host LAN IP.
        uri = f"eybond://{host}:{port}/{eybond_devaddr}"
        params: list[str] = []
        if eybond_broadcast and eybond_broadcast != DEFAULT_EYBOND_BROADCAST:
            params.append(f"broadcast={eybond_broadcast}")
        if eybond_announce_ip:
            params.append(f"announce={eybond_announce_ip}")
        if params:
            uri += "?" + "&".join(params)
        return uri
    return ""


def _parse_device_uri(device: str) -> dict[str, Any]:
    """Best-effort recovery of connection fields from a stored device string."""
    blank = {
        CONF_PROTOCOL: PROTOCOL_VOLTRONIC,
        CONF_TRANSPORT: TRANSPORT_TCP_ELFIN,
        CONF_HOST: "",
        CONF_PORT: DEFAULT_TCP_PORT,
        CONF_SERIAL_DEVICE: "",
        CONF_AGENT_DEVICE_ID: "",
        CONF_EYBOND_DEVADDR: DEFAULT_EYBOND_DEVADDR,
        CONF_EYBOND_BROADCAST: DEFAULT_EYBOND_BROADCAST,
        CONF_EYBOND_ANNOUNCE_IP: DEFAULT_EYBOND_ANNOUNCE_IP,
    }
    if not device:
        return blank

    if device.startswith("agent://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_AGENT,
            CONF_TRANSPORT: TRANSPORT_AGENT_HTTP,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_AGENT_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: (parsed.path or "").lstrip("/"),
        }
    if device.startswith("modbus://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_MODBUS,
            CONF_TRANSPORT: TRANSPORT_TCP,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("pi18://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_PI18,
            CONF_TRANSPORT: TRANSPORT_TCP,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("pi18-serial://"):
        _, serial_device = device.split("pi18-serial://", 1)
        return {
            CONF_PROTOCOL: PROTOCOL_PI18,
            CONF_TRANSPORT: TRANSPORT_SERIAL,
            CONF_HOST: "",
            CONF_PORT: DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: serial_device,
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("tcp://"):
        parsed = urlparse(device)
        return {
            CONF_PROTOCOL: PROTOCOL_VOLTRONIC,
            CONF_TRANSPORT: TRANSPORT_TCP_ELFIN,
            CONF_HOST: parsed.hostname or "",
            CONF_PORT: parsed.port or DEFAULT_TCP_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    if device.startswith("eybond-pi18://") or device.startswith("eybond://"):
        is_pi18 = device.startswith("eybond-pi18://")
        parsed = urlparse(device)
        devaddr_str = (parsed.path or "/").lstrip("/")
        try:
            devaddr = int(devaddr_str) if devaddr_str else DEFAULT_EYBOND_DEVADDR
        except ValueError:
            devaddr = DEFAULT_EYBOND_DEVADDR
        query = parse_qs(parsed.query or "")
        broadcast = (query.get("broadcast") or [DEFAULT_EYBOND_BROADCAST])[0]
        announce_ip = (query.get("announce") or [DEFAULT_EYBOND_ANNOUNCE_IP])[0]
        return {
            CONF_PROTOCOL: PROTOCOL_PI18 if is_pi18 else PROTOCOL_VOLTRONIC,
            CONF_TRANSPORT: TRANSPORT_EYBOND,
            CONF_HOST: parsed.hostname or DEFAULT_EYBOND_BIND_HOST,
            CONF_PORT: parsed.port or DEFAULT_EYBOND_BIND_PORT,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
            CONF_EYBOND_DEVADDR: devaddr,
            CONF_EYBOND_BROADCAST: broadcast,
            CONF_EYBOND_ANNOUNCE_IP: announce_ip,
        }
    # Legacy "host:port" stored without scheme — assume Elfin TCP.
    if ":" in device and not device.startswith("/") and not device.startswith("\\"):
        host_part, _, port_part = device.rpartition(":")
        try:
            port = int(port_part)
        except ValueError:
            return blank
        return {
            CONF_PROTOCOL: PROTOCOL_VOLTRONIC,
            CONF_TRANSPORT: TRANSPORT_TCP_ELFIN,
            CONF_HOST: host_part,
            CONF_PORT: port,
            CONF_SERIAL_DEVICE: "",
            CONF_AGENT_DEVICE_ID: "",
        }
    return {
        CONF_PROTOCOL: PROTOCOL_VOLTRONIC,
        CONF_TRANSPORT: TRANSPORT_SERIAL,
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
    protocol: str, transport: str, defaults: dict[str, Any]
) -> vol.Schema:
    """Connection fields for the selected protocol and transport."""
    protocol, transport = _normalize_protocol_transport(protocol, transport)
    schema: dict = {}

    if transport == TRANSPORT_SERIAL:
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
        if transport == TRANSPORT_AGENT_HTTP:
            default_port = DEFAULT_AGENT_PORT
        elif transport == TRANSPORT_EYBOND:
            default_port = DEFAULT_EYBOND_BIND_PORT
        else:
            default_port = DEFAULT_TCP_PORT

        if transport == TRANSPORT_EYBOND:
            host_default = defaults.get(CONF_HOST) or DEFAULT_EYBOND_BIND_HOST
        else:
            host_default = defaults.get(CONF_HOST) or vol.UNDEFINED

        schema[
            vol.Required(CONF_HOST, default=host_default)
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
        if transport == TRANSPORT_EYBOND:
            schema[
                vol.Required(
                    CONF_EYBOND_DEVADDR,
                    default=defaults.get(CONF_EYBOND_DEVADDR, DEFAULT_EYBOND_DEVADDR),
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=16, step=1, mode=NumberSelectorMode.BOX
                )
            )
            schema[
                vol.Required(
                    CONF_EYBOND_BROADCAST,
                    default=defaults.get(CONF_EYBOND_BROADCAST, DEFAULT_EYBOND_BROADCAST),
                )
            ] = cv.string
            # Empty = auto-detect (correct on bare-metal HA). In Docker
            # bridge mode this must be set to the host's LAN IP so the
            # dongle can connect back through the NAT/port-mapping.
            schema[
                vol.Optional(
                    CONF_EYBOND_ANNOUNCE_IP,
                    default=defaults.get(
                        CONF_EYBOND_ANNOUNCE_IP, DEFAULT_EYBOND_ANNOUNCE_IP
                    ),
                )
            ] = cv.string

    schema[
        vol.Required(
            CONF_UPDATE_INTERVAL,
            default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        )
    ] = _update_interval_field()

    if protocol in _CRC_CAPABLE_PROTOCOLS:
        schema[
            vol.Optional(
                CONF_STRICT_CRC,
                default=defaults.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC),
            )
        ] = BooleanSelector()

    return vol.Schema(schema)


def _validate_connection(
    protocol: str, transport: str, user_input: dict[str, Any]
) -> dict[str, str]:
    protocol, transport = _normalize_protocol_transport(protocol, transport)
    errors: dict[str, str] = {}
    host = (user_input.get(CONF_HOST) or "").strip()
    serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
    agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()

    if transport == TRANSPORT_SERIAL and not serial_device:
        errors[CONF_SERIAL_DEVICE] = "serial_required"
    if transport != TRANSPORT_SERIAL and not host:
        errors[CONF_HOST] = "host_required"
    if protocol == PROTOCOL_AGENT and not agent_device_id:
        errors[CONF_AGENT_DEVICE_ID] = "agent_device_id_required"
    return errors


def _protocol_schema(default_protocol: str) -> vol.Schema:
    default_protocol, _ = _normalize_protocol_transport(default_protocol)
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


def _transport_schema(protocol: str, default_transport: str) -> vol.Schema:
    protocol, default_transport = _normalize_protocol_transport(
        protocol, default_transport
    )
    transports = TRANSPORTS_BY_PROTOCOL.get(protocol, ())
    return vol.Schema(
        {
            vol.Required(CONF_TRANSPORT, default=default_transport): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=t, label=t) for t in transports
                    ],
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_TRANSPORT,
                )
            )
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        self._name: str | None = None
        self._protocol: str = PROTOCOL_VOLTRONIC
        self._transport: str = TRANSPORT_TCP_ELFIN

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
        """Step 2: pick the inverter protocol."""
        if user_input is not None:
            self._protocol, self._transport = _normalize_protocol_transport(
                user_input[CONF_PROTOCOL], self._transport
            )
            return await self.async_step_transport()

        return self.async_show_form(
            step_id="protocol",
            data_schema=_protocol_schema(self._protocol),
        )

    async def async_step_transport(self, user_input=None):
        """Step 3: pick the physical transport."""
        if user_input is not None:
            self._protocol, self._transport = _normalize_protocol_transport(
                self._protocol, user_input[CONF_TRANSPORT]
            )
            return await self.async_step_connection()

        self._protocol, self._transport = _normalize_protocol_transport(
            self._protocol, self._transport
        )
        return self.async_show_form(
            step_id="transport",
            data_schema=_transport_schema(self._protocol, self._transport),
            description_placeholders={"protocol": self._protocol},
        )

    async def async_step_connection(self, user_input=None):
        """Step 4: connection details + update interval."""
        protocol, transport = _normalize_protocol_transport(
            self._protocol, self._transport
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_connection(protocol, transport, user_input)
            if not errors:
                host = (user_input.get(CONF_HOST) or "").strip()
                port = int(user_input.get(CONF_PORT) or DEFAULT_TCP_PORT)
                serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
                agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()
                eybond_devaddr = int(
                    user_input.get(CONF_EYBOND_DEVADDR, DEFAULT_EYBOND_DEVADDR)
                )
                eybond_broadcast = (
                    user_input.get(CONF_EYBOND_BROADCAST) or DEFAULT_EYBOND_BROADCAST
                ).strip()
                eybond_announce_ip = (
                    user_input.get(CONF_EYBOND_ANNOUNCE_IP)
                    or DEFAULT_EYBOND_ANNOUNCE_IP
                ).strip()
                update_interval = int(
                    user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                )
                strict_crc = bool(
                    user_input.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC)
                )

                device_value = _build_device_uri(
                    protocol, transport, host, port, serial_device, agent_device_id,
                    eybond_devaddr, eybond_broadcast, eybond_announce_ip,
                )

                return self.async_create_entry(
                    title=self._name or "Inverter",
                    data={CONF_NAME: self._name},
                    options={
                        CONF_PROTOCOL: protocol,
                        CONF_TRANSPORT: transport,
                        CONF_DEVICE: device_value,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_SERIAL_DEVICE: serial_device,
                        CONF_AGENT_DEVICE_ID: agent_device_id,
                        CONF_EYBOND_DEVADDR: eybond_devaddr,
                        CONF_EYBOND_BROADCAST: eybond_broadcast,
                        CONF_EYBOND_ANNOUNCE_IP: eybond_announce_ip,
                        CONF_UPDATE_INTERVAL: update_interval,
                        CONF_STRICT_CRC: strict_crc,
                    },
                )

        defaults = dict(user_input or {})
        schema = await _build_connection_schema(protocol, transport, defaults)
        return self.async_show_form(
            step_id="connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={"protocol": protocol, "transport": transport},
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
        self._protocol: str = PROTOCOL_VOLTRONIC
        self._transport: str = TRANSPORT_TCP_ELFIN

    def _load_defaults(self) -> None:
        opts = dict(self._config_entry.options)
        # Recover protocol/host/port from the legacy `device` string when the
        # entry predates the new schema.
        parsed = _parse_device_uri(opts.get(CONF_DEVICE, "") or "")
        self._defaults = {
            CONF_TRANSPORT: opts.get(
                CONF_TRANSPORT, parsed.get(CONF_TRANSPORT, TRANSPORT_TCP_ELFIN)
            ),
            CONF_HOST: opts.get(CONF_HOST, parsed[CONF_HOST]),
            CONF_PORT: opts.get(CONF_PORT, parsed[CONF_PORT]),
            CONF_SERIAL_DEVICE: opts.get(
                CONF_SERIAL_DEVICE, parsed[CONF_SERIAL_DEVICE]
            ),
            CONF_AGENT_DEVICE_ID: opts.get(
                CONF_AGENT_DEVICE_ID, parsed[CONF_AGENT_DEVICE_ID]
            ),
            CONF_EYBOND_DEVADDR: opts.get(
                CONF_EYBOND_DEVADDR,
                parsed.get(CONF_EYBOND_DEVADDR, DEFAULT_EYBOND_DEVADDR),
            ),
            CONF_EYBOND_BROADCAST: opts.get(
                CONF_EYBOND_BROADCAST,
                parsed.get(CONF_EYBOND_BROADCAST, DEFAULT_EYBOND_BROADCAST),
            ),
            CONF_EYBOND_ANNOUNCE_IP: opts.get(
                CONF_EYBOND_ANNOUNCE_IP,
                parsed.get(CONF_EYBOND_ANNOUNCE_IP, DEFAULT_EYBOND_ANNOUNCE_IP),
            ),
            CONF_UPDATE_INTERVAL: opts.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
            CONF_STRICT_CRC: opts.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC),
        }
        self._protocol, self._transport = _normalize_protocol_transport(
            opts.get(CONF_PROTOCOL, parsed[CONF_PROTOCOL]),
            opts.get(CONF_TRANSPORT, parsed.get(CONF_TRANSPORT)),
        )

    async def async_step_init(self, user_input=None):
        self._load_defaults()
        return await self.async_step_protocol()

    async def async_step_protocol(self, user_input=None):
        if user_input is not None:
            self._protocol, self._transport = _normalize_protocol_transport(
                user_input[CONF_PROTOCOL], self._transport
            )
            return await self.async_step_transport()

        return self.async_show_form(
            step_id="protocol",
            data_schema=_protocol_schema(self._protocol),
        )

    async def async_step_transport(self, user_input=None):
        if user_input is not None:
            self._protocol, self._transport = _normalize_protocol_transport(
                self._protocol, user_input[CONF_TRANSPORT]
            )
            return await self.async_step_connection()

        self._protocol, self._transport = _normalize_protocol_transport(
            self._protocol, self._transport
        )
        return self.async_show_form(
            step_id="transport",
            data_schema=_transport_schema(self._protocol, self._transport),
            description_placeholders={"protocol": self._protocol},
        )

    async def async_step_connection(self, user_input=None):
        protocol, transport = _normalize_protocol_transport(
            self._protocol, self._transport
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_connection(protocol, transport, user_input)
            if not errors:
                host = (user_input.get(CONF_HOST) or "").strip()
                port = int(user_input.get(CONF_PORT) or DEFAULT_TCP_PORT)
                serial_device = (user_input.get(CONF_SERIAL_DEVICE) or "").strip()
                agent_device_id = (user_input.get(CONF_AGENT_DEVICE_ID) or "").strip()
                eybond_devaddr = int(
                    user_input.get(CONF_EYBOND_DEVADDR, DEFAULT_EYBOND_DEVADDR)
                )
                eybond_broadcast = (
                    user_input.get(CONF_EYBOND_BROADCAST) or DEFAULT_EYBOND_BROADCAST
                ).strip()
                eybond_announce_ip = (
                    user_input.get(CONF_EYBOND_ANNOUNCE_IP)
                    or DEFAULT_EYBOND_ANNOUNCE_IP
                ).strip()
                update_interval = int(
                    user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                )
                strict_crc = bool(
                    user_input.get(CONF_STRICT_CRC, DEFAULT_STRICT_CRC)
                )

                device_value = _build_device_uri(
                    protocol, transport, host, port, serial_device, agent_device_id,
                    eybond_devaddr, eybond_broadcast, eybond_announce_ip,
                )

                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PROTOCOL: protocol,
                        CONF_TRANSPORT: transport,
                        CONF_DEVICE: device_value,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_SERIAL_DEVICE: serial_device,
                        CONF_AGENT_DEVICE_ID: agent_device_id,
                        CONF_EYBOND_DEVADDR: eybond_devaddr,
                        CONF_EYBOND_BROADCAST: eybond_broadcast,
                        CONF_EYBOND_ANNOUNCE_IP: eybond_announce_ip,
                        CONF_UPDATE_INTERVAL: update_interval,
                        CONF_STRICT_CRC: strict_crc,
                    },
                )

        defaults = {**self._defaults, **(user_input or {})}
        schema = await _build_connection_schema(protocol, transport, defaults)
        return self.async_show_form(
            step_id="connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={"protocol": protocol, "transport": transport},
        )
