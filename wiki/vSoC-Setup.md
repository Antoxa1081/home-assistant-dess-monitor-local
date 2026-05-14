# vSoC (Virtual State of Charge) — User Setup Guide

## What it is and why

`vSoC Battery State of Charge` is a battery state-of-charge sensor (in %),
computed using **Coulomb counting** (the same technique real BMS units use)
based on charge / discharge current. It is independent of terminal voltage
under load — which is unreliable on LFP — and accurately reflects the real
charge state between full-charge sync events.

## After upgrade — what's new

Under `Settings → Devices & Services → DESS Monitor Local → {Your inverter}`
you'll find three new control entities:

| Entity | Purpose |
|--------|---------|
| `vSoC Battery Mode` (select) | Choose chemistry and BMS connection style |
| `vSoC Battery Capacity (Ah)` (number) | Nominal bank capacity in **ampere-hours** |
| `vSoC Full Charge Sync Voltage` (number) | Optional override for the 100% sync threshold |

Plus the actual `vSoC Battery State of Charge` sensor (%).

---

## Step 1: Pick the battery mode

Open `vSoC Battery Mode` and select one of:

### `Lithium (Voltage)` — lithium without inverter communication

Use this if:
- You have an **LFP** (LiFePO4) or other Li-chemistry pack
- Inverter is set to **USER** battery type (`Battery Type = USER`), with custom voltage thresholds
- BMS is **not** wired to the inverter via CAN/RS485 (or talks to a standalone display only)

Algorithm: voltage-based snap-to-100% + Coulomb counter. Coulombic
efficiency 99%.

### `Lithium (BMS)` — lithium with BMS communication

Use this if:
- BMS is connected to the inverter via **CAN/RS485** (Pylontech, BYD,
  Goodwe, JK BMS with a BMS-CAN bridge, etc.)
- Inverter `Battery Type` is set to `LIB` or `LIC`
- Inverter reads SoC directly from the BMS and exposes it in QPIGS

Algorithm: SoC mirrors the inverter's `battery_capacity` field (which is
the BMS-reported SoC). Our integrator does not run.

### `Lead-acid` — lead-acid battery

Use this if:
- AGM, Flooded, or Gel — any lead chemistry
- Inverter set to a matching battery type

Algorithm: voltage-based snap + Coulomb counter + gassing-loss correction.
Efficiencies: 90% charge / 95% discharge. Wider hysteresis dead zone
(0.5 V).

**The default is `Lithium (Voltage)`** — the most common case.

---

## Step 2: Enter capacity in Ah

Open `vSoC Battery Capacity (Ah)` and enter the **nameplate capacity in
ampere-hours**, as printed on the battery label.

Examples:
- LiFePO4 `48V 200Ah` pack → **200**
- 4× lead-acid 12V 100Ah in series → **100** (series doesn't add capacity!)
- 4× LFP 48V 100Ah modules in parallel → **400**
- Pylontech US3000 (3.5 kWh × 2 modules) → ~68 Ah × 2 = **136**
  (or check the datasheet)

### If you use "usable capacity" (DoD)

Lead-acid banks are typically used at 50% DoD to preserve cycle life. If
you want the SoC sensor to read 100% when the battery is at 50% of
nameplate, **enter 50% of nameplate**:
- 200 Ah AGM @ 50% DoD → enter **100**

For LFP, DoD is usually 95–100%, so you can enter the nameplate value
as-is.

### If you only know capacity in Wh

Divide by the **chemistry's** nominal voltage (NOT the inverter's setting!):
- LFP 48V bank: nominal 51.2 V (16 × 3.2 V), Ah = Wh / 51.2
- LFP 24V bank: nominal 25.6 V (8 × 3.2 V), Ah = Wh / 25.6
- Lead-acid 48V bank: nominal 48 V, Ah = Wh / 48
- Single 12V lead cell: nominal 12 V, Ah = Wh / 12

Example: 10 kWh LFP 48V pack → 10000 / 51.2 = **195 Ah**.

---

## Step 3: (Optional) Snap-to-100% threshold

`vSoC Full Charge Sync Voltage` is the voltage at which the SoC sensor
forces itself to 100%.

**Default = 0** means "use the inverter's `bulk_charging_voltage`". Most
users don't need to touch this.

### When to override

If you have **LFP** and the inverter's bulk is set to 28.0 V (or 56.0 V
for 48V banks), but your pack is fully charged at 27.4 V (or 54.8 V), set
this to **27.4** (or **54.8**). Otherwise the snap will never fire — the
inverter transitions to float before voltage ever reaches bulk.

Typical values:
- LFP 48V (16S): full at **53.6–54.4 V** (3.35–3.4 V/cell) at ≤ 0.1C
  charge rate
- LFP 24V (8S): **26.8–27.2 V**
- LFP 12V (4S): **13.4–13.6 V**

### In `Lead-acid` mode

Lead-acid normally reaches the inverter's bulk setting just fine. Leave
at 0.

### In `Lithium (BMS)` mode

This field is unused — SoC comes pre-cooked from the BMS.

---

## Step 4: Verify

After configuration, `vSoC Battery State of Charge` should:

1. Become **available** (no longer `unavailable`)
2. Show a number between 0 and 100%
3. **Move in the right direction**: rise while charging, fall while
   discharging

### If the sensor stays `unavailable`

Check in order:
- Is capacity set and > 0? (`vSoC Battery Capacity (Ah)`)
- In `Lithium (BMS)` mode: does the inverter expose `battery_capacity` in
  QPIGS? Open `sensor.{inverter}_battery_capacity` — it should show a
  number
- In `Lithium (Voltage)` or `Lead-acid` mode: either the inverter
  must expose `bulk_charging_voltage` in QPIRI, or you must enter a value
  in `vSoC Full Charge Sync Voltage`

### If SoC is "stuck" / not rising during charge

- Check the `battery_charging_current` sensor — it should show a current
  > 0
- At zero current, SoC doesn't move (nothing to integrate)

### If SoC disagrees with reality

This is normal until the battery reaches **full charge**. Coulomb counting
between snaps has a small drift (~1–3% per day), especially if your
specific pack's efficiency differs from the preset. Once charging reaches
`sync_voltage`, the sensor auto-syncs to 100% and the drift resets.

If you consistently see > 10% disagreement, the **mode** may be wrong:
- Lead-acid configured as `Lithium (Voltage)` → drifts upward (we use
  eff 0.99 instead of 0.90)
- LFP configured as `Lead-acid` → drifts downward (we use eff 0.90
  instead of 0.99)

## Migration from the old version

If you used the previous `vSoC Battery Capacity` (Wh):

1. After upgrade, the old entity becomes orphan
   (`Settings → Entities`, filter by "orphaned" or "no longer provided").
   You can delete it manually.
2. A new `vSoC Battery Capacity (Ah)` appears with value 0
3. Enter the nameplate capacity in **Ah** as described above
4. SoC will be unavailable until you complete step 3 — that's expected
5. After the next full charge, the sensor will sync and start producing
   correct readings

## Parameter table by mode (reference)

| Mode | Snap condition | Charge eff | Discharge eff | Tail current |
|------|---------------|-----------|---------------|--------------|
| Lithium (Voltage) | V ≥ sync for 30 sec | 99% | 100% | 0.05C (5A on 100Ah) |
| Lithium (BMS) | mirror BMS | — | — | — |
| Lead-acid | V ≥ sync for 30 sec | 90% | 95% | 0.02C (2A on 100Ah) |

where `sync` is either your override or the inverter's
`bulk_charging_voltage`.

---

If SoC behaves oddly, share a screenshot of the past 24h history plus
the values of the three control entities and we'll debug.
