# DESS Monitor Local Home Assistant integration

# Installation

- Install as a custom repository via HACS
- Manually download and extract to the custom_components directory

Once installed, use Add Integration -> DESS Monitor Local.

# Configuration

The setup wizard asks for three things: a hub name, a protocol, and the
connection address. Everything can be changed later via **Configure** in the
integration card.

| Protocol | What it is | Address | Default port |
| --- | --- | --- | --- |
| `tcp_elfin` | Voltronic / Axpert PI30 over an Elfin Wi-Fi/Ethernet bridge | host / IP | `8899` |
| `pi18` | PI18 / InfiniSolar-V over TCP | host / IP | `8899` |
| `modbus` | Modbus RTU over TCP (SMG-II) | host / IP | `8899` |
| `agent` | Local [`solar-system-agent`] HTTP API | host / IP + `providerDeviceId` | `8787` |
| `serial` | Direct RS232 / USB connection | device path (`/dev/ttyUSB0`, `COM3`, …) | — |

Common option for all protocols:

- **Update interval** — how often Home Assistant polls the device, `1`–`300` s
  (default `10`).

For protocol-specific notes, troubleshooting and the internal `device` URI
format see the [Configuration wiki page](https://github.com/Antoxa1081/home-assistant-dess-monitor-local/wiki/Configuration).
