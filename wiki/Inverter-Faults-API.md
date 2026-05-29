# Inverter Faults & Warnings — Integration Reference

Technical reference for external systems (EMS, monitoring, alerting,
control logic) consuming inverter fault / warning state from this
integration. Covers all three transports: PI30 (Voltronic Axpert), PI18
(InfiniSolar-V), and Modbus / agent (Easun SMG-II).

Audience: developers wiring inverter health into custom EMS pipelines.

---

## Entity contract

The integration exposes a uniform contract regardless of protocol:

| Entity | Type | Description |
|--------|------|-------------|
| `binary_sensor.{prefix}_direct_any_warning` | bool (PROBLEM device_class) | Master trigger: ON whenever any individual warning bit is set, *or* the PI18 explicit `fault_code != 0`. |
| `sensor.{prefix}_direct_inverter_fault_summary` | text + attributes | Human-readable summary string. Full flag dict in attributes. |
| `binary_sensor.{prefix}_direct_warning_<name>` | bool (PROBLEM) | Per-flag entities for the 6 most actionable warnings (see "Critical flags" below). |

where `{prefix}` is the lowercased / slugified inverter name from the
config entry (e.g. `easun_4200`, `anern_4200`).

### Summary sensor state machine

The text state of `sensor.*_direct_inverter_fault_summary` is one of:

| State | Meaning |
|-------|---------|
| `OK` | No warning bit set, no PI18 fault_code |
| `Warning: <name>` | One warning active. `<name>` is the human label for the highest-severity set bit. |
| `Warning: <name> (+N more)` | Multiple warnings active. `<name>` is the highest-severity one; `N` is the count of additional flags. |
| `Warning: K active` | PI18-only path with only `warn_*` flags set (rare). |
| `Fault: <code>: <description>` | PI18 reports a non-zero `fault_code`. Always takes priority over warning bits. |

### Summary sensor attributes

Every individual flag is exposed as a boolean attribute. Read via HA's
template engine or REST API:

```yaml
{{ state_attr('sensor.easun_direct_inverter_fault_summary', 'overload') }}
{{ state_attr('sensor.easun_direct_inverter_fault_summary', 'active_count') }}
```

Special attributes:
- `active_count` (int) — total number of set boolean flags
- `fault_code` (int, PI18 only) — hardware fault number from the inverter
- `fault_description` (str, PI18 only) — human description from spec table
- `has_fault` (bool, PI18 only) — convenience: `fault_code != 0`

---

## PI30 (Voltronic Axpert) — QPIWS bit map

Polled via the `QPIWS` command. Response is a 32-character (some firmwares
emit 28 / 36) string of `0`/`1` chars. Each bit maps to a fixed warning.

| Bit | Attribute key | Severity tier | Notes |
|----:|---------------|---------------|-------|
| a0  | `_reserved_0` | — | always 0 |
| **a1** | `inverter_fault` | 🔴 critical | Inverter DSP fault — usually requires power-cycle |
| **a2** | `bus_over` | 🔴 critical | DC bus overvoltage |
| **a3** | `bus_under` | 🔴 critical | DC bus undervoltage |
| **a4** | `bus_soft_fail` | 🔴 critical | Bus soft-start failed at boot |
| a5  | `line_fail` | 🟡 normal | Grid lost (expected during outages, also surfaced via QPIGS b7_b0) |
| a6  | `opv_short` | 🔴 critical | AC output short circuit |
| a7  | `inverter_voltage_too_low` | 🟠 warning | Output voltage out of spec |
| a8  | `inverter_voltage_too_high` | 🟠 warning | Output voltage out of spec |
| **a9** | `over_temperature` | 🔴 critical | Heatsink / module overtemp — protective derate or shutdown imminent |
| **a10** | `fan_locked` | 🔴 critical | Cooling failure — overtemp will follow |
| a11 | `battery_voltage_high` | 🟠 warning | Battery over-voltage from charger |
| a12 | `battery_low_alarm` | 🟠 warning | Approaching cut-off |
| a13 | `_reserved_13` | — | always 0 |
| **a14** | `battery_under_shutdown` | 🔴 critical | Inverter shut output to protect battery |
| a15 | `_reserved_15` | — | always 0 |
| **a16** | `overload` | 🔴 critical | Continuous overload, protection imminent |
| **a17** | `eeprom_fault` | 🔴 critical | Persistent storage failure — settings may be lost |
| **a18** | `inverter_over_current` | 🔴 critical | DC-side OCP triggered |
| a19 | `inverter_soft_fail` | 🟠 warning | Inverter soft-start failed |
| **a20** | `self_test_fail` | 🔴 critical | Power-on self-test failed |
| a21 | `op_dc_voltage_over` | 🟠 warning | DC bleed on AC output exceeds limit |
| **a22** | `battery_open` | 🔴 critical | Battery disconnected mid-operation |
| **a23** | `current_sensor_fail` | 🔴 critical | Hall / shunt sensor failure — all readings unreliable |
| **a24** | `battery_short` | 🔴 critical | Battery short detected at terminals |
| a25 | `power_limit` | 🟢 info | Inverter actively derating (normal high-load behaviour) |
| a26 | `pv_voltage_high` | 🟠 warning | PV string above MPPT range |
| a27 | `mppt_overload_fault` | 🟠 warning | MPPT current limit hit |
| a28 | `mppt_overload_warning` | 🟢 info | MPPT approaching limit |
| a29 | `battery_too_low_to_charge` | 🟠 warning | Voltage below charger's start threshold |
| a30 | `_reserved_30` | — | always 0 |
| a31 | `_reserved_31` | — | always 0 |

**Bold rows = surfaced as dedicated binary_sensor.** All bits are
accessible as attributes on the summary sensor regardless.

---

## PI18 (InfiniSolar-V) — QFWS

Polled via `QFWS`. Returns two layers of info:

### Layer 1: explicit fault code

| Attribute | Type | Description |
|-----------|------|-------------|
| `fault_code` | int | Numeric code from inverter, `0` = no fault |
| `fault_description` | str | Human description from PI18 spec table |
| `has_fault` | bool | True iff `fault_code != 0` |

`fault_code` reference (see `_PI18_FAULT_CODES` in `api/decoders/pi18.py`):

| Code | Description | Severity |
|------|-------------|----------|
| 0 | No fault | — |
| 1 | Fan is locked | 🔴 |
| 2 | Over temperature | 🔴 |
| 3 | Battery voltage too high | 🔴 |
| 4 | Battery voltage too low | 🔴 |
| 5 | Output short or over temperature | 🔴 |
| 6 | Output voltage too high | 🔴 |
| 7 | Over load time out | 🔴 |
| 8 | Bus voltage too high | 🔴 |
| 9 | Bus soft start failed | 🔴 |
| 11 | Main relay failed | 🔴 |
| 51 | Over current inverter | 🔴 |
| 52 | Bus soft start failed | 🔴 |
| 53 | Inverter soft start failed | 🔴 |
| 54 | Self-test failed | 🔴 |
| 55 | Over DC voltage on output | 🔴 |
| 56 | Battery connection open | 🔴 |
| 57 | Current sensor failed | 🔴 |
| 58 | Output voltage too low | 🔴 |
| 60 | Inverter negative power | 🔴 |
| 71 | Parallel version different | 🟠 |
| 72 | Output circuit failed | 🔴 |
| 80 | CAN communication failed | 🟠 |
| 81 | Parallel host line lost | 🟠 |
| 82 | Parallel synchronized signal lost | 🟠 |
| 83 | Parallel battery voltage detect different | 🟠 |
| 84 | Parallel line voltage / freq different | 🟠 |
| 85 | Parallel line input current unbalanced | 🟠 |
| 86 | Parallel output setting different | 🟠 |

### Layer 2: warning bits

Independent flags reported alongside the fault code:

| Attribute | Notes |
|-----------|-------|
| `warn_line_fail` | Grid lost |
| `warn_output_short` | Transient output overload |
| `warn_inverter_over_temperature` | Inverter (not battery) over-temp |
| `warn_fan_lock` | Fan stuck |
| `warn_battery_voltage_high` | Charger over-voltage |
| `warn_battery_low` | Low alarm |
| `warn_battery_under` | Battery under shutdown |
| `warn_overload` | Overload warning |
| `warn_eeprom_fail` | EEPROM read/write fail |
| `warn_power_limit` | Power limiting active |
| `warn_pv1_voltage_high` | PV1 string overvoltage |
| `warn_pv2_voltage_high` | PV2 string overvoltage |
| `warn_mppt1_overload` | MPPT1 overload |
| `warn_mppt2_overload` | MPPT2 overload |
| `warn_battery_too_low_scc1` | SCC1 won't start charge |
| `warn_battery_too_low_scc2` | SCC2 won't start charge |

---

## Modbus / agent (Easun SMG-II)

Newer agent builds (`solar-system-agent` postgen pipeline) expose a
rich PI18-style warning map directly on the snapshot — every `warn_*`
key in the agent JSON is automatically routed into the QFWS section
and consumed by the same fault-summary / any_warning / per-flag
binary_sensor entities as PI30 and PI18 inverters.

Coverage includes (non-exhaustive):

| Agent flag | Severity tier |
|------------|---------------|
| `warn_fault_active` | 🔴 critical |
| `warn_inverter_over_current`, `warn_battery_over_current`, `warn_pv_over_current` | 🔴 critical |
| `warn_bus_over`, `warn_bus_under`, `warn_bus_soft_fail` | 🔴 critical |
| `warn_over_temperature`, `warn_inverter_over_temperature`, `warn_dcdc_over_temperature`, `warn_pv_over_temperature` | 🔴 critical |
| `warn_battery_under_shutdown`, `warn_battery_open` | 🔴 critical |
| `warn_inverter_negative_power` | 🔴 critical |
| `warn_fan_locked`, `warn_eeprom_fault` | 🔴 critical |
| `warn_overload`, `warn_inverter_voltage_too_high/low`, `warn_op_dc_voltage_over` | 🟠 warning |
| `warn_battery_voltage_high`, `warn_battery_low_alarm`, `warn_battery_type_incompatible` | 🟠 warning |
| `warn_pv_voltage_high`, `warn_pv_low_voltage` | 🟠 warning |
| `warn_mains_low_frequency`, `warn_mains_over_frequency`, `warn_mains_waveform_abnormal` | 🟠 warning |
| `warn_parallel_*` (host_lost, sync_abnormal, battery_diff, mode_inconsistent, version_incompatible, comm_interrupted) | 🟠 warning |
| `warn_battery_eq_charging`, `warn_pv_energy_low` | 🟢 info |
| `warn_power_limit`, `warn_mppt_overload_warning` | 🟢 info |
| `warn_line_fail` | 🟡 normal (grid down) |
| `warn_*_current_bias` (battery/inverter/output/pv) | 🟢 diagnostic |

Plus the agent's convenience aggregates (`warn_any`, `warn_active_count`)
are visible if you want a fast path that skips bit-by-bit inspection.

### Synthesised status bits (still relevant)

QPIGS-side `device_status_bits_b7_b0` and `b10_b8` are reconstructed
from `operating_mode`, `grid_voltage`, `load_percent`, and
`battery_current` so the legacy PI30-style binary_sensors stay
functional alongside the new warn_* flags. This handles the live state
signals — `inverter_on`, `line_fail`, `charging_to_battery`,
`ac_charging_active`, `scc_charging_active`.

### Recommended EMS approach for Easun

1. **Primary trigger**: `binary_sensor.{prefix}_direct_any_warning` —
   ON when any `warn_*` bit is set or `operating_mode == "Fault"`.
2. **Severity**: `sensor.{prefix}_direct_inverter_fault_summary` text +
   `active_count` attribute.
3. **Specific**: bare-name binary_sensors (`*_warning_overload`, etc.)
   work transparently — they accept `warn_<name>` from the agent as the
   same flag.
4. **Live state**: `sensor.{prefix}_direct_operating_mode` for the
   high-level state machine (`Battery` / `Line` / `Standby` / `Fault`).

For uncatalogued `warn_*` keys (e.g. firmware updates that add new
flags before the integration catches up) — they're still **counted**
in `active_count` and surfaced via "+N more" in the summary text, so
your EMS won't miss a state change even if the label is generic.

---

## Consumption examples

### HA Template — severity-aware

```yaml
template:
  - sensor:
      - name: "Inverter health"
        state: >
          {% set summary = states('sensor.easun_direct_inverter_fault_summary') %}
          {% set crit = is_state('binary_sensor.easun_direct_inverter_fault', 'on')
              or is_state('binary_sensor.easun_direct_over_temperature', 'on')
              or is_state('binary_sensor.easun_direct_battery_shutdown', 'on') %}
          {% if crit %}CRITICAL
          {% elif is_state('binary_sensor.easun_direct_any_warning', 'on') %}WARNING
          {% else %}OK
          {% endif %}
```

### HA REST API — pull current state for an external EMS

`GET /api/states/sensor.easun_direct_inverter_fault_summary` returns:

```json
{
  "entity_id": "sensor.easun_direct_inverter_fault_summary",
  "state": "OK",
  "attributes": {
    "inverter_fault": false,
    "overload": false,
    "over_temperature": false,
    "fan_locked": false,
    ...
    "active_count": 0,
    "friendly_name": "Easun Direct Inverter Fault Summary"
  },
  "last_changed": "2026-05-17T20:14:32+00:00",
  "last_updated": "2026-05-17T20:14:32+00:00"
}
```

Auth via long-lived access token (`Authorization: Bearer <token>`).

### MQTT bridge (optional — HA MQTT statestream)

If you enable `mqtt_statestream` in HA, every sensor + attribute is
published to MQTT. Topic for the summary:

```
homeassistant/sensor/easun_direct_inverter_fault_summary/state         # text
homeassistant/sensor/easun_direct_inverter_fault_summary/inverter_fault # "False"
```

Convenient for EMS that already subscribes to MQTT — no HA-specific
client needed.

### Direct Recorder / DB query

History for trend analysis:

```sql
SELECT
  last_updated_ts,
  state AS summary_text,
  -- attribute value lives in shared_attrs as JSON
  JSON_EXTRACT(shared_attrs.shared_attrs, '$.active_count') AS active_warnings
FROM states
JOIN shared_attrs ON states.attributes_id = shared_attrs.attributes_id
WHERE entity_id = 'sensor.easun_direct_inverter_fault_summary'
  AND last_updated_ts > UNIX_TIMESTAMP() - 86400
ORDER BY last_updated_ts DESC;
```

(Adjust JSON syntax for Postgres / MariaDB / SQLite — HA uses one of
these depending on installation.)

---

## Severity tiers — recommended EMS routing

| Tier | What it includes | EMS action |
|------|------------------|------------|
| **🔴 Critical hardware** | `inverter_fault`, `over_temperature`, `fan_locked`, `eeprom_fault`, `battery_under_shutdown`, `current_sensor_fail`, `battery_open`, `battery_short`, `self_test_fail`, `bus_over`, `bus_under`, `bus_soft_fail`, `inverter_over_current`, `opv_short`, `mppt_overload_fault`, `overload`, PI18 `fault_code != 0` | Immediate page / SMS. Shed load. Disable automations that depend on AC output. |
| **🟠 Warning — likely-OK but watch** | `battery_voltage_high`, `battery_low_alarm`, `battery_too_low_to_charge`, `inverter_voltage_too_low`, `inverter_voltage_too_high`, `op_dc_voltage_over`, `pv_voltage_high`, `inverter_soft_fail`, parallel-mode warnings (`warn_battery_under`, etc.) | Notify, log, continue normal operation. |
| **🟡 Normal context flag** | `line_fail` (grid down — expected during outages) | Suppress alerts during planned outages (e.g. cross-reference with `yasno_outages` schedule). |
| **🟢 Informational** | `power_limit`, `mppt_overload_warning` | Log only — these fire during legitimate high-PV-production. |

---

## Polling frequency & freshness

- All fault entities update on the integration's coordinator cycle
  (default **10 seconds**, configurable in the entry options).
- `last_updated_ts` on the entity reflects the last time HA's state
  machine wrote a new state (state-change-only by default — see notes
  in `direct_sensor.py` about `state_class` / `force_update`).
- For sub-10-second response from your EMS, subscribe via HA WebSocket
  (`/api/websocket`, event `state_changed`) instead of polling.

## Known limits

1. **Bit coverage varies by firmware.** Some PI30 clones populate only
   a subset of QPIWS bits (Anern 4200 typically reports `a5 line_fail`
   and `a25 power_limit`, leaves others 0 even when conditions match).
   Trust the high-severity bits more than the absence of warnings.
2. **Quantisation.** Several firmwares report continuous quantities
   (currents, temperatures) in integer units. Don't build threshold
   triggers tighter than 1 A / 1 °C.
3. **Agent path is summary-only.** Modbus / SMG-II inverters don't have
   a true fault register; what you get is derived from operating_mode
   and grid presence. Use the integration's `operating_mode` sensor as
   the primary signal there.
4. **Snap-to-100% confusion.** vSoC's "battery full" derived from
   voltage + tail current is *not* a fault state. Don't trigger alerts
   on `sensor.*_vsoc_battery_state_of_charge` plateaus.
