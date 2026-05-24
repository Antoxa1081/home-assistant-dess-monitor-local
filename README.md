# DESS Monitor Local Home Assistant integration

# Installation

- Install as a custom repository via HACS
- Manually download and extract to the custom_components directory

Once installed, use Add Integration -> DESS Monitor Local.

# Configuration

The setup wizard asks for a hub name, protocol, transport, and connection
settings. Everything can be changed later via **Configure** in the integration
card.

| Protocol | What it is | Transports |
| --- | --- | --- |
| `voltronic` | Voltronic / Axpert PI30 | `tcp_elfin`, `serial`, `eybond` |
| `pi18` | PI18 / InfiniSolar-V | `tcp`, `serial` |
| `modbus` | Modbus RTU (SMG-II) | `tcp` |
| `agent` | Local [`solar-system-agent`] HTTP API | `agent_http` |

Common option for all protocols:

- **Update interval** — how often Home Assistant polls the device, `1`–`300` s
  (default `10`).

For protocol-specific notes, troubleshooting and the internal `device` URI
format see the [Configuration wiki page](https://github.com/Antoxa1081/home-assistant-dess-monitor-local/wiki).
