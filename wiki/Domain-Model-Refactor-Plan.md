# Domain-Model Refactor Plan

Move the integration off the Voltronic-QPIGS-shaped data dicts to a
**protocol-neutral domain model**. Today every protocol normalizes to the
Voltronic `qpigs`/`qpiri`/`qmod`/`qpiws`/`qfws` wire shape — which forces
SMG-II Modbus and PI18 to **fabricate ~25-30 placeholder values** each
(`bus_voltage="400"`, `battery_capacity="100"`, `rated_output_active_power=
"4000"`, fake status bits, reserved fields…) and round-trips floats through
strings. The goal is a typed, semantic model where *absent = `None`* and no
protocol pretends to have data it doesn't.

## Principles

1. **No fabrication.** A field a protocol can't measure is `None` (the sensor
   goes `unavailable`), never a plausible constant.
2. **Proper types.** `float`/`int`/`enum`/`bool`, not strings. No string
   round-trips.
3. **Signed where natural.** `battery_current` is signed (+charge / −discharge);
   the split charge/discharge are derived properties.
4. **Structured faults.** One canonical warning set + numeric codes, instead of
   two naming conventions (`overload` vs `warn_overload`) merged ad-hoc.
5. **Capabilities drive entities.** A device exposes what it supports (pv2, dual
   temps, BMS SoC, PI18 directions, status bits); entities are created from
   that, not from a per-entry `is_pi18` flag.
6. **Non-breaking migration.** No single commit may break a working install.

## The model

`api/model.py` (pure, no HA):

```python
class WarningKey(StrEnum):
    # canonical, protocol-neutral warning identifiers (~50, unifying the
    # current PI30 bare keys, PI18/agent warn_* keys and SMG-II codes)
    INVERTER_FAULT = "inverter_fault"
    OVERLOAD = "overload"
    OVER_TEMPERATURE = "over_temperature"
    FAN_LOCKED = "fan_locked"
    BATTERY_UNDER_SHUTDOWN = "battery_under_shutdown"
    EEPROM_FAULT = "eeprom_fault"
    LINE_FAIL = "line_fail"
    # ... full set migrated from _QPIWS_FIELDS + pi18 warn_* + agent warn_*

@dataclass(frozen=True)
class PvInput:
    voltage: float | None = None
    current: float | None = None
    power: float | None = None

@dataclass
class Ratings:
    # nameplate / config — None when the protocol doesn't report it
    grid_voltage: float | None = None
    output_active_power: float | None = None
    output_apparent_power: float | None = None
    battery_voltage: float | None = None
    battery_capacity_ah: float | None = None
    bulk_charging_voltage: float | None = None
    float_charging_voltage: float | None = None
    low_battery_to_bypass_voltage: float | None = None
    shutdown_battery_voltage: float | None = None
    high_battery_to_battery_mode_voltage: float | None = None
    max_charging_current: float | None = None
    max_utility_charging_current: float | None = None
    battery_type: BatteryType | None = None
    ac_input_voltage_range: ACInputVoltageRange | None = None
    output_source_priority: OutputSourcePriority | None = None
    charger_source_priority: ChargerSourcePriority | None = None
    parallel_mode: ParallelMode | None = None
    parallel_max_number: int | None = None
    # ... remaining QPIRI fields, all Optional

@dataclass
class DeviceStatus:
    # parsed PI30 status bits — each Optional (None = protocol doesn't report)
    inverter_on: bool | None = None
    line_fail: bool | None = None
    battery_low: bool | None = None
    battery_high: bool | None = None
    bus_over: bool | None = None
    overload: bool | None = None
    charging_to_battery: bool | None = None
    charging_ac_active: bool | None = None
    charging_scc_active: bool | None = None

@dataclass
class Metrics:
    """Live telemetry — the QPIGS + QMOD analogue (changes every poll)."""
    mode: OperatingMode | None = None
    # grid / output
    grid_voltage: float | None = None
    grid_frequency: float | None = None
    grid_power: float | None = None            # SMG-II grid_ac_in_power
    ac_output_voltage: float | None = None
    ac_output_frequency: float | None = None
    ac_output_active_power: float | None = None
    ac_output_apparent_power: float | None = None
    load_percent: float | None = None
    bus_voltage: float | None = None
    # battery
    battery_voltage: float | None = None
    battery_current: float | None = None        # signed: + charge / − discharge
    battery_power: float | None = None          # signed
    battery_soc: float | None = None            # device/BMS %, None if unknown
    scc_battery_voltage: float | None = None
    scc2_battery_voltage: float | None = None
    # pv
    pv1: PvInput = field(default_factory=PvInput)
    pv2: PvInput | None = None                  # None when single-MPPT
    # temperatures
    temp_heatsink: float | None = None
    temp_dcdc: float | None = None
    temp_mppt1: float | None = None
    temp_mppt2: float | None = None
    # PI18 directions / sub-statuses (Optional enums)
    battery_power_direction: ... | None = None
    dcac_power_direction: ... | None = None
    line_power_direction: ... | None = None
    mppt1_status: ... | None = None
    mppt2_status: ... | None = None
    # parsed PI30 status bits
    status: DeviceStatus = field(default_factory=DeviceStatus)

    # derived (not stored)
    @property
    def battery_charge_current(self) -> float | None:
        return None if self.battery_current is None else max(0.0, self.battery_current)

    @property
    def battery_discharge_current(self) -> float | None:
        return None if self.battery_current is None else max(0.0, -self.battery_current)


@dataclass
class Faults:
    """Warnings / faults — the QPIWS + QFWS analogue."""
    warnings: set[WarningKey] = field(default_factory=set)
    fault_code: int | None = None
    warning_code: int | None = None
    fault_description: str | None = None

    @property
    def has_fault(self) -> bool:
        return bool(self.fault_code) or WarningKey.INVERTER_FAULT in self.warnings

    @property
    def any(self) -> bool:
        return bool(self.warnings) or bool(self.fault_code) or bool(self.warning_code)


@dataclass
class DeviceSnapshot:
    # identity
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    # three semantic buckets (mirror the protocol command groups, neutral types)
    metrics: Metrics = field(default_factory=Metrics)    # QPIGS + QMOD
    ratings: Ratings = field(default_factory=Ratings)    # QPIRI
    faults: Faults = field(default_factory=Faults)       # QPIWS + QFWS
    # capabilities (drives entity creation)
    capabilities: set[str] = field(default_factory=set)
    # diagnostic escape hatch — raw protocol values for troubleshooting
    raw: dict = field(default_factory=dict)
```

Three semantic buckets keep **metric** sensors (live measurements) cleanly
separated from **spec/nameplate** sensors (`ratings`, already DIAGNOSTIC
category) and from faults — matching the existing entity grouping and enabling
a later optimisation where `ratings` is polled less often than `metrics`.

Capabilities (strings, set by each adapter): `pv2`, `dual_temp`, `mppt_temp`,
`bms_soc`, `status_bits`, `directions`, `grid_power`, `parallel`, `dcdc_temp`.

## Adapter interface

```python
class BaseAdapter:
    async def get_snapshot(self) -> DeviceSnapshot: ...
```

Each adapter builds the model from its native source — Voltronic decodes the
QPIGS/QPIRI/QMOD/QPIWS responses into it; Modbus reads SMG-II registers
straight into it (no `smg2_to_qpigs`); PI18 decodes GS/PIRI/MOD/FWS; agent maps
its JSON. **One snapshot read per cycle** (replaces the 6 per-command reads —
also kills the SMG-II 6× redundancy natively).

## Per-protocol: what becomes `None`

- **SMG-II Modbus** — drops all fabrication: `bus_voltage`, `battery_soc`
  (no register → SoC estimator runs on Coulomb counting only), `scc_*`,
  status bits, and every placeholder rating (`rated_*`, `battery_type`,
  `parallel_*`, reserved_*). Keeps real registers: grid/output V/Hz/W, signed
  `battery_current`, `battery_power`, temps (heatsink+dcdc), pv, `grid_power`,
  charge-voltage config, priorities, fault/warning codes.
- **PI18** — drops `bus_voltage="400"`, fake status bits, reserved_*,
  `parallel_*` placeholders. Keeps GS measurements, pv2, dual MPPT temps,
  directions, fault_code + warn_* → canonical `WarningKey`s.
- **Voltronic** — already faithful; straight field mapping, no behavior change.
- **Agent** — maps native JSON; warn_* → canonical `WarningKey`s.

## Fault model

One canonical `set[WarningKey]` of active warnings + `fault_code`/`warning_code`
(int|None) + `fault_description`. Each adapter maps its native faults to the
canonical set (PI30 bits, PI18 warn_*, agent warn_*, SMG-II codes). The
fault-summary sensor walks a severity table over `WarningKey`; the per-flag
binary sensors check membership; `any_warning` = non-empty set OR non-zero code.
This removes the bare-vs-`warn_` dual-convention merge.

## Non-breaking migration sequence

**Phase A — model + adapters (additive, behavior-preserving).**
Define `api/model.py`. Implement `get_snapshot()` on every adapter natively.

> Status: **Phase A done** — `api/model.py` + `get_snapshot()` on all five
> adapters (Modbus, Voltronic, PI18, agent, EyBond):
> - Modbus/SMG-II: `smg2_to_snapshot()` (no fabrication); `get_data` derives
>   the legacy sections from `snapshot.raw` (golden test = byte-identical).
> - Voltronic: `voltronic_to_snapshot()` typed projection; full `WarningKey`
>   set + `WarningKey.from_flags()`.
> - PI18: `pi18_to_snapshot()` reuses Voltronic + adds PV2/MPPT temps/
>   directions, drops PI18 fabrications.
> - Agent: `agent_to_snapshot()` reuses Voltronic + merges qfws faults.
> - EyBond: dispatches to the PI18/PI30 projection per scheme.
>
> `get_data` is unchanged for every protocol (no fabrication for the honest
> ones; a `snapshot.raw` shim for Modbus), so entity behaviour is identical.
> Deferred to later phases: status bits → `DeviceStatus`, and full PI18/agent
> `warn_*` → `WarningKey` name reconciliation (Phase C, with the fault
> summary). `BaseAdapter.get_snapshot` added in Phase B.

Provide a transition shim `snapshot.to_legacy_sections() -> {qpigs, qpiri, ...}`
that *reproduces today's output including the placeholders*, and make the old
`get_data(cmd)` return `to_legacy_sections()[section]`. Result: one snapshot
read per cycle, entities byte-identical, SMG-II 6× redundancy gone. Fully
unit-testable (snapshot per protocol from canned input).

**Phase B — coordinator stores the snapshot. ✅ DONE.**
Implemented additively to avoid any behaviour change or extra transport: each
adapter has `snapshot_from_sections(sections)` (sync, no I/O); the coordinator
derives `DeviceSnapshot` per device from the sections it already fetched
(post-FailureTracker, so it reflects frozen/last-known data) and stores them
in `DirectCoordinator.snapshots[id]` (NOT in `coordinator.data`, which stays
JSON-serialisable for diagnostics). Entities still read the legacy sections;
the snapshots are ready for Phase C.

**Phase C — migrate entities group by group.**
Move sensor groups to read `snapshot` fields directly (real `None` for absent),
starting with the SMG-II-affected ones (power/battery/pv), then ratings, then
the fault summary + binary sensors, then SoC/energy/time-to-*. As each group
migrates, drop its placeholders from `to_legacy_sections`. Gate entity creation
on `snapshot.capabilities` instead of `is_pi18`/entry protocol.

**Phase D — remove legacy.**
Delete `to_legacy_sections`, `smg2_to_qpigs`/`smg2_to_qpiri`, the PI18 placeholder
emitters, the per-command `get_data` path, and the section keys. The model is the
only data shape.

## Testing strategy

- Pure per-protocol snapshot tests: canned response/registers → assert model
  fields (and assert formerly-fabricated fields are now `None`).
- Golden test in Phase A: `to_legacy_sections()` equals today's `get_data()`
  output for the same input (proves behavior preservation).
- Fault mapping tests: native faults → canonical `WarningKey` set.
- Entity tests migrate alongside each group (read from a `DeviceSnapshot`).

## Resolved decisions

- ✅ **Grouping:** three semantic buckets `metrics` / `ratings` / `faults`
  (QPIGS+QMOD / QPIRI / QPIWS+QFWS analogues), keeping metric sensors separate
  from device-spec sensors. Modbus maps 1:1 (register block 200→metrics,
  300→ratings, 100→faults); enables polling ratings less often later.
- ✅ **`battery_soc` = `None` for SMG-II** — the SoC estimator runs on Coulomb
  counting (already supports non-BMS modes; verify the no-device-SoC path).
- ✅ **Canonical `WarningKey`** = union of all current flags (PI30 bits + PI18
  warn_* + agent warn_* + SMG-II codes), including the agent diagnostic tail.
- ✅ **Keep the `raw: dict` escape hatch** on the snapshot for diagnostics.
