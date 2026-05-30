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

### Phase 1: Multi-session foundation — ✅ DONE

Implemented the multi-session EyBond manager with routing by `PN` in
`api/protocols/eybond_dongle.py`. The entity/HA layer is unchanged.

Delivered:

- multiple TCP sessions coexist on one listener (`_sessions: set[_Session]`)
- a new connection never evicts an existing one
- dongles identified by `PN` from the heartbeat → `_sessions_by_pn`
- per-PN readiness gating (`_ready_by_pn`) plus a legacy `_any_ready` gate
- per-session heartbeat task (`_Session.hb_task`)
- continuous UDP announcer (no longer stopped after the first connection)
- same-PN reconnect evicts the stale session
- disconnect of one dongle does not affect others
- `send_frame(devaddr, v_frame, timeout, context, pn=None)` — routes by PN;
  `pn=None` preserves legacy single-dongle behaviour
- `pn` threaded through `send_eybond_bytes` / `send_eybond_voltronic` /
  `send_eybond_set_command` so later phases can target a specific dongle

Internal routing entry (PN-aware):

```python
await send_eybond_voltronic(device, command, timeout, protocol, pn=<PN>)
```

Implementation notes / decisions:

- `_Session` is `@dataclass(eq=False)` so instances are identity-hashable
  (usable as `set` elements / dict values).
- Announcer runs continuously (the simpler, safer option from the open
  questions below) — additional dongles can attach at any time.
- Tests: `tests/test_eybond_dongle.py::TestMultiSession` drives the manager
  with in-memory fake `StreamReader`/`StreamWriter` (no real socket) and
  covers: PN learned from heartbeat, two dongles coexist without eviction,
  routing targets the correct PN, disconnect isolation, same-PN reconnect
  eviction, legacy `pn=None` routing, and no-dongle timeout.

This phase does not change the entity model.

### Phase 2: Discovery registry — ✅ DONE

Added a per-hub discovery registry in
`api/protocols/eybond_discovery.py` (`DongleStatus`, `DongleRecord`,
`EybondRegistry`). Pure module — no HA imports, injectable clock — so it
unit-tests without `hass` and serializes straight into a store in Phase 3.

Delivered:

- discovered `PN` records (`DongleRecord`: `pn`, `name`, `enabled`,
  `protocol`, `devaddr`, `status`, `peer`, `first_seen`, `last_seen`,
  `model_hint`)
- `first_seen` / `last_seen` lifecycle timestamps (ISO-8601 strings)
- session status (`connected` / `disconnected`)
- enable/disable + protocol/devaddr/name configuration setters (records may
  be pre-configured before a dongle first connects)
- `enabled_pns()` / `connected_pns()` queries (foundation for Phase 4 polling)
- `to_dict()` / `load()` JSON round-trip for the Phase 3 store

Manager wiring (`EybondManager`):

- owns `self.registry: EybondRegistry` (one per hub)
- `record_seen(pn, peer)` on identify and on every subsequent heartbeat
  (keeps `last_seen` current); preserves user config on existing records
- `mark_disconnected(pn)` on session teardown — **skipped** when the PN was
  already re-claimed by a same-PN reconnect (record stays `connected`)
- `discovered` property exposes `registry.all()`

Defaults chosen: newly discovered dongles are `enabled = False` with
`protocol = None` (unconfigured / not polled) — see resolved open question
below. Tests: `tests/test_eybond_discovery.py` (registry unit tests) and
`tests/test_eybond_dongle.py::TestDiscoveryIntegration` (manager feeds the
registry across connect / disconnect / same-PN reconnect).

This phase does not change the entity model.

Original deliverables (all met):

- discovered `PN` records
- `last_seen`
- session status
- enabled/disabled state support

### Phase 3: Hub config entry — ✅ DONE

Added a dedicated EyBond hub config entry alongside the legacy single-device
flow. The config flow's first step is now a menu: **Single inverter**
(unchanged 4-step flow) or **EyBond hub**.

Delivered:

- `CONF_ENTRY_KIND` marks an entry as `device` (default/legacy) or
  `eybond_hub`; `__init__.async_setup_entry` branches on it
- hub creation step collects name + listener config (bind host/port,
  broadcast, announce IP, update interval)
- `eybond_hub.py` runtime: loads the discovery registry from a dedicated
  `Store` (`{DOMAIN}.eybond_hub.<entry_id>`), starts the listener with that
  registry (`get_eybond_manager(..., registry=...)`), persists periodically
  + on unload, and shuts down only its own listener on unload
  (`shutdown_eybond_manager`)
- stale `connected` statuses are cleared on load
  (`EybondRegistry.reset_connection_state`)
- hub options flow exposes discovered devices and listener settings

Translations added (en/ru + strings.json) for the menu and all new steps.

### Phase 4: Child device polling — ✅ DONE

Reused the existing coordinator/entity stack via a per-child `DeviceTarget`
(id / uri / protocol / name):

- `DirectCoordinator` accepts explicit targets; data is keyed by the stable
  child id (`eybond:<pn>:<devaddr>`), commands route to the child URI, which
  carries `?pn=<PN>` so the dispatcher/adapter reach the right dongle on the
  shared listener (`_parse_pn_from_uri`)
- `build_child_targets` turns enabled, protocol-assigned records into targets.
  Supported child protocols: Voltronic, PI18, and Modbus/SMG-II. Modbus rides
  the dongle via a new `eybond-modbus://` scheme — the ModbusAdapter is now
  transport-agnostic (`read_smg2_snapshot_via` + RTU framing helpers), sending
  RTU frames through the dongle's FC=4 channel; the child `devaddr` doubles as
  the Modbus unit id. (Agent is HTTP-only and excluded.)
- one HA device per child under the hub entry; platforms read protocol
  per-item so a hub can mix Voltronic/PI18 children
- legacy single-device entries keep `id == uri`, so existing entity
  `unique_id`s/device identifiers are unchanged

### Phase 5 (partial): Device management UI — ✅ core done

The hub options flow already lets the user, per discovered dongle:

- enable/disable
- rename
- choose protocol (`none` = discovered-but-not-polled, voltronic, pi18)
- set `devaddr`

Edits write the registry → save the Store → bump `CONF_HUB_REVISION` in
options, which reloads the entry and rebuilds child devices/entities.

Hub visibility: the hub entry always creates a hub device with a
`Discovered dongles` diagnostic sensor (state = count, attributes = per-dongle
PN/status/last_seen/enabled/protocol) so the integration is visibly working
before any child is configured. A one-shot persistent notification fires when
a brand-new, still-unconfigured dongle appears, nudging the user to assign a
protocol (seeded from the persisted registry so restarts don't re-notify).

Stale-record removal is implemented: the device-edit step has a **Remove this
device** option that drops a gone dongle from the registry (it is re-discovered
if it comes back online).

Force rediscovery is implemented: a **Scan for new dongles** option in the hub
options opens a ~60s window (`EybondManager.force_rediscovery` →
`_force_announce_until`) where the announcer broadcasts regardless of
connection state, so brand-new dongles attach and get discovered. It briefly
flaps connected dongles (the broadcast can't target one), acceptable for an
explicit scan. Remaining Phase 5 nicety (protocol auto-probe) is still open.

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

### Phase 6: Migration helper — ✅ DONE

Opt-in conversion of a legacy single-device `eybond://` / `eybond-pi18://`
entry into a hub entry. A legacy EyBond entry's options flow now shows a menu
(**Edit connection** / **Convert to EyBond hub**); the conversion
(`eybond_hub.async_migrate_legacy_to_hub`):

- parses the legacy device URI for bind host/port, broadcast, announce IP,
  devaddr, and protocol (voltronic/pi18)
- captures the connected dongle's PN from the live session (aborts with
  `dongle_offline` if it isn't online — the PN is needed to configure the
  child)
- writes the hub Store with one enabled, fully-configured child, storing the
  original URI as `legacy_id` so the migrated child keeps its **entity
  unique_ids and history** (`build_child_targets` uses `legacy_id` as the
  target id when set)
- returns the hub options; applying them reloads the entry as a hub

Migration is **opt-in** — legacy entries that aren't converted keep working
unchanged.

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

- ✅ Where exactly should child-device discovery state live: `options` or a
  dedicated storage file? — **Decided in Phase 3: dedicated `Store`.** The
  registry (lifecycle + per-device config) lives in
  `{DOMAIN}.eybond_hub.<entry_id>`; only listener settings + a revision
  counter live in options. Editing a device bumps the revision to trigger a
  reload.
- ✅ Should UDP announce run continuously, or only while there are unidentified
  or missing configured dongles? — **Revised after field testing: gated, NOT
  continuous.** Phase 1 chose continuous, but field logs showed every
  `set>server` broadcast makes an already-connected dongle reconnect — so a
  continuous announce flaps connected dongles every ~5s. The announcer now
  broadcasts only while an expected dongle is missing (`_should_announce`),
  re-evaluating every 1s but rate-limiting sends to 5s; once all expected
  dongles are connected it pauses, and sessions stay up via the per-session
  heartbeat. See `eybond_dongle.py`. **Known limit:** multiple dongles on one
  shared listener still interfere (the broadcast can't target a single dongle)
  and flap under Docker-Desktop-on-Windows NAT; single-dongle-per-listener is
  rock-solid. A clean multi-dongle design (discovery port + per-dongle port +
  unicast `set>server`) needs real dongle IPs (Linux host/bridged networking),
  not Windows-Docker bridge NAT.
- ✅ Should newly discovered devices default to `enabled = false` until
  explicitly configured? — **Decided in Phase 2: yes.** A freshly discovered
  `DongleRecord` is `enabled = False` with `protocol = None`, so unconfigured
  dongles are tracked but not polled and create no noisy entities.
- Do we want protocol auto-probe later as an optional action, while keeping
  manual assignment as the default?
