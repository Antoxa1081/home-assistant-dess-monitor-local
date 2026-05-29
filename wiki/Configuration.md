# Configuration

This page describes every option of the **DESS Monitor Local** integration
and the connection details for each supported protocol.

## Adding the integration

After installing the integration (via HACS or manually), open in Home
Assistant:

**Settings → Devices & Services → Add Integration → DESS Monitor Local**

The setup wizard has four steps:

1. **Hub name** — any friendly name the device will appear under in Home
   Assistant.
2. **Protocol** — the command/data protocol spoken by the inverter or local
   data source.
3. **Transport** — how Home Assistant reaches that protocol.
4. **Connection settings** — address, port and polling interval.

All options can be changed later via **Configure** on the integration card —
there is no need to remove and re-add it.

## Supported protocols and transports

| Protocol | Description | Supported transports |
| --- | --- | --- |
| `voltronic` | Voltronic / Axpert PI30 | `tcp_elfin`, `serial`, `eybond` |
| `pi18` | PI18 / InfiniSolar-V | `tcp`, `serial` |
| `modbus` | Modbus RTU (SMG-II) | `tcp` |
| `agent` | Local `solar-system-agent` | `agent_http` |

| Transport | Description | Default port |
| --- | --- | --- |
| `tcp_elfin` | Elfin / transparent TCP bridge | `8899` |
| `tcp` | Plain TCP transport | `8899` |
| `serial` | Direct serial / USB (RS232) | — |
| `eybond` | EyBond / SmartESS Wi-Fi dongle reverse TCP transport | `8899` |
| `agent_http` | HTTP transport for `solar-system-agent` | `8787` |

### Voltronic over Elfin TCP (`voltronic` + `tcp_elfin`)

For Voltronic / Axpert PI30 inverters connected through an Elfin
Wi-Fi/Ethernet bridge (or a compatible transparent TCP bridge).

Fields:

- **Host / IP address** — IP address of the bridge on your LAN.
- **Port** — TCP port of the bridge (default `8899`).

#### Elfin bridge configuration

Open the Elfin web UI (default `http://<bridge-ip>`) and configure the
serial-to-TCP socket:

- **Work mode** — `TCP Server`.
- **Local port** — the same port you enter in Home Assistant
  (default `8899`).
- **Protocol** — **`None`**. Any non-`None` value (Modbus TCP, HTTPD client,
  etc.) makes the bridge re-frame or proxy bytes and breaks the inverter
  protocol.
- **CLI** — **disabled**. Leaving the CLI enabled lets the bridge intercept
  control sequences inside the data stream, which corrupts replies from
  the inverter.

Both options above are critical: with either of them on, requests will
either time out or come back with garbled payloads.

#### Serial port (Elfin → inverter)

Match the bridge's serial settings to the inverter's interface:

- **Baud rate** — `2400` for Axpert / Voltronic PI30 (ASCII `QPIGS`),
  `9600` for Modbus RTU devices like SMG-II.
- **Data bits / parity / stop bits** — `8 / None / 1` (typical default).
- **Flow control** — `None`.

If you don't see any data and the connection just hangs, the baud rate is
the first thing to double-check.

### Voltronic over EyBond (`voltronic` + `eybond`)

For EyBond / SmartESS Wi-Fi dongles. The dongle initiates a reverse TCP
connection to Home Assistant, so the Home Assistant host must accept incoming
connections on the configured port.

See the dedicated setup guide:

[EyBond / SmartESS Wi-Fi Dongle Transport Setup](EyBond-Transport-Setup.md)

### PI18 / InfiniSolar-V (`pi18`)

For InfiniSolar-V and other models speaking PI18 over TCP.

Fields:

- **Host / IP address** — IP of the device or bridge.
- **Port** — TCP port (default `8899`).

### Modbus RTU over TCP (`modbus` + `tcp`)

For SMG-II inverters and similar devices that expose Modbus RTU through a
TCP bridge.

Fields:

- **Host / IP address** — IP of the bridge.
- **Port** — TCP port (default `8899`).

> If you use an RTU-over-TCP converter, make sure it works in transparent
> mode (no RTU↔TCP frame translation).

### Serial / USB (`voltronic` + `serial`, `pi18` + `serial`)

Direct RS232 connection — typically through the manufacturer's USB-to-RS232
cable.

Fields:

- **Serial port** — device path. The dropdown is auto-populated with
  available ports; you can also type the path manually:
  - Linux: `/dev/ttyUSB0`, `/dev/ttyACM0`, `/dev/serial/by-id/...`
  - Windows: `COM3`, `COM4`, …

> On Linux prefer a path under `/dev/serial/by-id/` so the USB adapter does
> not change its name across reboots.
>
> Container installs (Home Assistant OS / Container) need access to the
> device — pass it through with `--device` or via the supervised install's
> "Advanced" options.

## Common options

### Update interval

How often Home Assistant polls the device, in seconds.

- Allowed range: **1 – 300 s**.
- Default: **10 s**.
- Lower values mean fresher sensor data but more load on the link and the
  inverter. For slow links (Wi-Fi bridges, 2400-baud serial) keep this at
  `10` or higher.

## Changing settings after install

Every field is editable via **Configure**:

1. **Settings → Devices & Services**
2. Open the **DESS Monitor Local** card → click **Configure**
3. Pick a different protocol and/or update the address and polling
   interval.

After saving, the integration reconnects automatically with the new
settings — no Home Assistant restart required.

## Internal `device` URI

Connection settings are stored as a single `device` string. This is useful
to know if you ever edit storage or YAML by hand.

| Protocol + transport | Format |
| --- | --- |
| `voltronic` + `tcp_elfin` | `tcp://<host>:<port>` |
| `voltronic` + `serial` | device path (e.g. `/dev/ttyUSB0` or `COM3`) |
| `voltronic` + `eybond` | `eybond://<bind_host>:<port>/<rs485_address>` |
| `pi18` + `tcp` | `pi18://<host>:<port>` |
| `pi18` + `serial` | `pi18-serial://<device_path>` |
| `modbus` + `tcp` | `modbus://<host>:<port>` |
| `agent` + `agent_http` | `agent://<host>:<port>/<providerDeviceId>` |

Legacy entries stored as bare `host:port` without a scheme are interpreted
as `voltronic` + `tcp_elfin` — migration is automatic.

## Troubleshooting

If the connection doesn't work:

- Enable verbose logging by adding this to `configuration.yaml`:

  ```yaml
  logger:
    default: warning
    logs:
      custom_components.dess_monitor_local: debug
  ```

- Download a diagnostics report from the integration card
  (**⋮ → Download diagnostics**) and attach it to a
  [GitHub issue](https://github.com/Antoxa1081/home-assistant-dess-monitor-local/issues).
- Verify the chosen IP/port is reachable from the Home Assistant host
  (`ping`, `telnet <host> <port>`).
