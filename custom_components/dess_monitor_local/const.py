# This is the internal name of the integration, it should also match the directory
# name for the integration.
DOMAIN = "dess_monitor_local"

# Config / options keys
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_PROTOCOL = "protocol"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SERIAL_DEVICE = "serial_device"
CONF_AGENT_DEVICE_ID = "agent_device_id"
CONF_EYBOND_DEVADDR = "eybond_devaddr"
CONF_EYBOND_BROADCAST = "eybond_broadcast"
CONF_EYBOND_ANNOUNCE_IP = "eybond_announce_ip"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_STRICT_CRC = "strict_crc"

# Supported protocol identifiers
PROTOCOL_TCP_ELFIN = "tcp_elfin"
PROTOCOL_MODBUS = "modbus"
PROTOCOL_PI18 = "pi18"
PROTOCOL_AGENT = "agent"
PROTOCOL_SERIAL = "serial"
PROTOCOL_EYBOND = "eybond"

PROTOCOLS = [
    PROTOCOL_TCP_ELFIN,
    PROTOCOL_PI18,
    PROTOCOL_MODBUS,
    PROTOCOL_AGENT,
    PROTOCOL_SERIAL,
    PROTOCOL_EYBOND,
]

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
