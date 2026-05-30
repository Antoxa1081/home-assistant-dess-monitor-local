# This is the internal name of the integration, it should also match the directory
# name for the integration.
DOMAIN = "dess_monitor_local"

# Config / options keys
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_PROTOCOL = "protocol"
CONF_TRANSPORT = "transport"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SERIAL_DEVICE = "serial_device"
CONF_AGENT_DEVICE_ID = "agent_device_id"
CONF_EYBOND_DEVADDR = "eybond_devaddr"
CONF_EYBOND_BROADCAST = "eybond_broadcast"
CONF_EYBOND_ANNOUNCE_IP = "eybond_announce_ip"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_STRICT_CRC = "strict_crc"

# Entry kind — distinguishes a single-inverter entry (legacy/default) from an
# EyBond hub entry (one TCP listener, many auto-discovered dongles routed by
# PN). Absent or unknown is treated as a device entry for backward compat.
CONF_ENTRY_KIND = "entry_kind"
ENTRY_KIND_DEVICE = "device"
ENTRY_KIND_EYBOND_HUB = "eybond_hub"

# Hub listener config keys (stored in a hub entry's options).
CONF_EYBOND_BIND_HOST = "eybond_bind_host"
CONF_EYBOND_BIND_PORT = "eybond_bind_port"
# Monotonic counter bumped whenever the discovered-device registry (which
# lives in a dedicated Store, not in options) is edited — touching options
# this way triggers the update listener so the entry reloads and re-reads
# the registry to (re)build child devices/entities.
CONF_HUB_REVISION = "hub_revision"

# Supported protocol identifiers
PROTOCOL_VOLTRONIC = "voltronic"
PROTOCOL_MODBUS = "modbus"
PROTOCOL_PI18 = "pi18"
PROTOCOL_AGENT = "agent"

# Legacy combined protocol identifiers. Older config entries stored both the
# inverter protocol and physical transport in CONF_PROTOCOL.
PROTOCOL_TCP_ELFIN = "tcp_elfin"
PROTOCOL_SERIAL = "serial"
PROTOCOL_EYBOND = "eybond"

PROTOCOLS = [
    PROTOCOL_VOLTRONIC,
    PROTOCOL_PI18,
    PROTOCOL_MODBUS,
    PROTOCOL_AGENT,
]

# Supported transport identifiers
TRANSPORT_TCP_ELFIN = "tcp_elfin"
TRANSPORT_TCP = "tcp"
TRANSPORT_SERIAL = "serial"
TRANSPORT_EYBOND = "eybond"
TRANSPORT_AGENT_HTTP = "agent_http"

TRANSPORTS_BY_PROTOCOL = {
    PROTOCOL_VOLTRONIC: [
        TRANSPORT_TCP_ELFIN,
        TRANSPORT_SERIAL,
        TRANSPORT_EYBOND,
    ],
    PROTOCOL_PI18: [
        TRANSPORT_TCP,
        TRANSPORT_SERIAL,
        TRANSPORT_EYBOND,
    ],
    PROTOCOL_MODBUS: [
        TRANSPORT_TCP,
        TRANSPORT_SERIAL,
        TRANSPORT_EYBOND,
    ],
    PROTOCOL_AGENT: [
        TRANSPORT_AGENT_HTTP,
    ],
}

DEFAULT_TRANSPORT_BY_PROTOCOL = {
    protocol: transports[0]
    for protocol, transports in TRANSPORTS_BY_PROTOCOL.items()
    if transports
}

LEGACY_PROTOCOL_TRANSPORT = {
    PROTOCOL_TCP_ELFIN: (PROTOCOL_VOLTRONIC, TRANSPORT_TCP_ELFIN),
    PROTOCOL_SERIAL: (PROTOCOL_VOLTRONIC, TRANSPORT_SERIAL),
    PROTOCOL_EYBOND: (PROTOCOL_VOLTRONIC, TRANSPORT_EYBOND),
}

# Defaults
DEFAULT_TCP_PORT = 8899
DEFAULT_AGENT_PORT = 8787
DEFAULT_EYBOND_BIND_HOST = "0.0.0.0"
DEFAULT_EYBOND_BIND_PORT = 8899
DEFAULT_EYBOND_DEVADDR = 1
DEFAULT_EYBOND_BROADCAST = "255.255.255.255"
# Empty = auto-detect via _detect_local_ip(). Override needed in Docker
# bridge networking, where auto-detect returns the container's internal
# IP (172.x) instead of the host's LAN IP.
DEFAULT_EYBOND_ANNOUNCE_IP = ""
DEFAULT_UPDATE_INTERVAL = 10
MIN_UPDATE_INTERVAL = 1
MAX_UPDATE_INTERVAL = 300
DEFAULT_STRICT_CRC = False
