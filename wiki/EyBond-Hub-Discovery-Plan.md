# EyBond Hub Discovery Plan

This document describes the planned redesign for EyBond support in
`dess_monitor_local`: one TCP listener, multiple dongles on one port,
discovery by `PN`, and per-device protocol configuration inside Home Assistant.

## Goal

Move from the current model:

- one config entry = one `device` URI
- one EyBond manager session per port

to a new model:

- one config entry = one EyBond hub / listener
- one TCP listener can accept multiple dongles
- dongles are identified by `PN`
- each discovered inverter/device is configured separately
- each discovered device can be enabled, disabled, renamed, and assigned its
  own protocol

## Current Limitations

Current EyBond implementation is single-session per port:

- one manager per `(bind_host, bind_port)`
- one active TCP session in manager state
- new connection replaces the previous one
- polling is tied to a single `CONF_DEVICE`

This means:

- multiple inverters behind one dongle can work via `devaddr`
- multiple dongles on one port cannot work at the same time

## Target Architecture

### 1. Hub Entry

Add a new EyBond-specific config entry mode where the entry represents a hub,
not an inverter.

Hub-level fields:

- `name`
- `bind_host`
- `bind_port`
- `broadcast`
- `announce_ip`

This entry owns:

- one TCP listener
- one UDP announcer
- multiple active dongle sessions
- a registry of discovered child devices

### 2. Dongle Discovery by PN

Each EyBond dongle already sends a heartbeat that includes `PN`.

Plan:

- accept multiple TCP sessions on one listener
- keep newly connected sessions in an `unidentified` pool
- once a heartbeat is received, extract `PN`
- move session into `sessions_by_pn[pn]`
- update `last_seen` and connection status for that `PN`

`PN` becomes the primary identity of the physical dongle.

### 3. Child Device Model

For each discovered dongle or inverter path, store a child-device record.

Minimum child-device fields:

- `pn`
- `name`
- `enabled`
- `protocol`
- `transport = "eybond"`
- `devaddr`
- `last_seen`
- `status`

Possible optional fields:

- `model_hint`
- `notes`

Device identity inside the integration should be based on:

- `pn + devaddr`

Recommended device key format:

```text
eybond:<pn>:<devaddr>
```

## Routing Model

### Current

Current request routing is effectively:

```text
(bind_host, bind_port) -> single session -> devaddr
```

### Planned

New request routing should be:

```text
(bind_host, bind_port, pn) -> session -> devaddr
```

This allows multiple dongles to share one port while still targeting
individual inverters behind each dongle.

## Protocol Handling

Discovery should be automatic for the physical dongle, but protocol selection
should remain manual.

Supported workflow:

- integration auto-discovers a dongle by `PN`
- user sees the discovered device in options/config UI
- user selects protocol for that device
- user can disable the device entirely

Protocol auto-detection is not the default plan because it would require active
probing and can be noisy, slow, or ambiguous.

Examples:

- Voltronic: probe `QPIGS`, `QMOD`, `QPIRI`
- PI18: probe native PI18 request framing
- Modbus: probe known registers

The preferred UX is:

- auto-discover dongle
- manually assign protocol

## Home Assistant UX

### Initial Setup

New EyBond hub setup flow:

1. enter hub name
2. choose transport mode: `eybond_hub`
3. configure:
   - `bind_host`
   - `bind_port`
   - `broadcast`
   - `announce_ip`

### Options / Management UI

The hub options flow should show discovered child devices and allow editing:

- `enabled`
- `name`
- `protocol`
- `devaddr`

Optional future actions:

- hide/unhide unconfigured devices
- force rediscovery
- remove stale discovered devices

## Polling Model

Current polling is built around a single `CONF_DEVICE`.

Planned redesign:

- keep one hub entry
- poll only `enabled` child devices
- route each request by `pn + devaddr`

Recommended implementation approach:

- one coordinator per child device

Why:

- simpler unavailable/retry semantics
- simpler entity ownership
- less coupling between devices
- easier to preserve existing entity code patterns

Alternative:

- one shared hub coordinator for all child devices

This is possible but increases coordination complexity and raises the blast
radius of failures.

## HA Device Registry Model

The Home Assistant device model should be:

- one logical hub/root entry
- many inverter devices under it

Each inverter device should have a stable identity derived from:

- `pn`
- `devaddr`

This allows:

- per-device enable/disable
- per-device protocol selection
- per-device naming
- clean separation in the HA device registry

## EyBond Manager Redesign

### Required Changes

Replace single-session state:

- `self._session`
- `self._session_ready`

with multi-session state:

- `sessions_by_pn: dict[str, Session]`
- `unidentified_sessions: set[Session]`
- `session_ready_by_pn: dict[str, asyncio.Event]`

### Behavioral Changes

- a new TCP connection must not evict existing sessions
- heartbeat identifies the session by `PN`
- requests target a specific `PN`
- disconnecting one dongle must not affect others
- UDP announcer must not stop permanently after the first connection

The announcer can either:

- stay active continuously

or:

- stay active while at least one expected device is missing

For the first implementation, continuous announce is simpler and safer.

## Storage Model

The integration needs persistent storage for discovered child devices.

Possible placement:

- `config_entry.options`

or:

- dedicated storage helper / JSON store

Recommended direction:

- keep listener settings in entry options
- keep discovered child-device registry in a dedicated structured store

Reason:

- avoids bloating the entry options payload
- cleaner updates for discovery metadata like `last_seen`
- easier future migration

## Backward Compatibility

Do not break the current single-device EyBond flow immediately.

Migration strategy:

1. keep legacy single-device `eybond://...` entries working
2. add the new EyBond hub mode in parallel
3. optionally add a migration helper later

Possible future migration helper:

- read legacy `bind_host`, `bind_port`, `broadcast`, `announce_ip`, `devaddr`
- convert into hub settings
- create one discovered/configured child device
- bind actual `PN` after first heartbeat if not already known

## Implementation Phases

### Phase 1: Multi-session foundation

Implement multi-session EyBond manager with routing by `PN`.

Deliverables:

- multiple TCP sessions on one port
- identify dongles by `PN`
- internal API:

```python
send_voltronic(pn, devaddr, command, timeout)
```

This phase should not yet change the entity model.

### Phase 2: Discovery registry

Add discovery storage and lifecycle tracking.

Deliverables:

- discovered `PN` records
- `last_seen`
- session status
- enabled/disabled state support

### Phase 3: Hub config entry

Add a dedicated EyBond hub config flow.

Deliverables:

- create hub entry
- configure listener
- expose discovered devices in options

### Phase 4: Child device polling

Move polling from single `CONF_DEVICE` to child-device records.

Deliverables:

- `pn + devaddr` routing
- coordinator per child device
- entities bound to child devices

### Phase 5: Device management UI

Expose editable per-device configuration.

Deliverables:

- enable/disable device
- rename device
- choose protocol
- set `devaddr`

### Phase 6: Migration helper

Optional final phase.

Deliverables:

- import legacy EyBond entries into hub mode
- preserve old behavior where migration is not performed

## Testing Plan

Minimum tests:

- multiple TCP sessions can coexist on one port
- `PN` is learned from heartbeat
- routing `pn + devaddr -> session` is correct
- disconnect/reconnect of one dongle does not affect others
- disabled child devices are not polled
- unknown discovered devices do not create noisy active entities by default
- legacy single-device EyBond entries still work

Additional useful tests:

- duplicate `PN` conflict handling
- stale session cleanup
- announcer behavior with zero, one, and many connected dongles
- options flow updates for discovered devices

## Recommended First Implementation Step

The first code change should be limited to the EyBond transport layer.

Specifically:

- redesign `eybond_dongle.py` for multiple simultaneous sessions
- keep existing higher-level HA entity behavior unchanged
- introduce internal routing by `PN`

This is the foundation. Without it, discovery and child-device management
cannot be implemented cleanly.

## Open Questions

- Where exactly should child-device discovery state live: `options` or a
  dedicated storage file?
- Should UDP announce run continuously, or only while there are unidentified or
  missing configured dongles?
- Should newly discovered devices default to `enabled = false` until explicitly
  configured?
- Do we want protocol auto-probe later as an optional action, while keeping
  manual assignment as the default?
