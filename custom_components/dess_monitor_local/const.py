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
CONF_UPDATE_INTERVAL = "update_interval"

# Supported protocol identifiers
PROTOCOL_TCP_ELFIN = "tcp_elfin"
PROTOCOL_MODBUS = "modbus"
PROTOCOL_AGENT = "agent"
PROTOCOL_SERIAL = "serial"

PROTOCOLS = [
    PROTOCOL_TCP_ELFIN,
    PROTOCOL_MODBUS,
    PROTOCOL_AGENT,
    PROTOCOL_SERIAL,
]

# Defaults
DEFAULT_TCP_PORT = 8899
DEFAULT_AGENT_PORT = 8787
DEFAULT_UPDATE_INTERVAL = 10
MIN_UPDATE_INTERVAL = 1
MAX_UPDATE_INTERVAL = 300
