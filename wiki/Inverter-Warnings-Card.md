# Inverter Warnings — Lovelace Card

A ready-to-paste dashboard card that surfaces the new fault / warning
sensors from version ≥ 0.3.5. Shows a one-line status header, the six
critical binary_sensors as a tile grid, and a dynamic list of every
active warning (collapsed when everything is OK).

Replace `easun` in the entity IDs with your inverter's slug.

---

## Vanilla Lovelace (no HACS needed)

```yaml
type: vertical-stack
title: Inverter Status
cards:
  # ── Header: summary text + colour-coded icon ────────────────────────
  - type: markdown
    content: |
      {% set summary = states('sensor.easun_direct_inverter_fault_summary') %}
      {% set count = state_attr('sensor.easun_direct_inverter_fault_summary', 'active_count') or 0 %}
      {% set warn = is_state('binary_sensor.easun_direct_any_warning', 'on') %}
      ## {% if warn %}🔴{% else %}🟢{% endif %} {{ summary }}
      Active warnings: **{{ count }}** &nbsp;·&nbsp; Mode: **{{ states('sensor.easun_direct_operating_mode') }}**

  # ── Six critical warnings as a tile grid ────────────────────────────
  # NOTE: entity_ids are derived from HA from the friendly name, not from
  # the integration's unique_id. The "_warning_" segment in unique_ids
  # is internal only — public entity_ids omit it.
  - type: glance
    title: Critical alerts
    show_state: true
    columns: 3
    entities:
      - entity: binary_sensor.easun_direct_inverter_fault
        name: Fault
      - entity: binary_sensor.easun_direct_overload
        name: Overload
      - entity: binary_sensor.easun_direct_over_temperature
        name: Over Temp
      - entity: binary_sensor.easun_direct_fan_locked
        name: Fan Lock
      - entity: binary_sensor.easun_direct_battery_shutdown
        name: Battery Shutdown
      - entity: binary_sensor.easun_direct_eeprom_fault
        name: EEPROM

  # ── Dynamic list of every active warning (only shown when not OK) ───
  - type: conditional
    conditions:
      - condition: numeric_state
        entity: sensor.easun_direct_inverter_fault_summary
        attribute: active_count
        above: 0
    card:
      type: markdown
      content: |
        ### Active warnings ({{ state_attr('sensor.easun_direct_inverter_fault_summary', 'active_count') }})
        {%- set attrs = state_attr('sensor.easun_direct_inverter_fault_summary') or {} -%}
        {%- for key, value in attrs.items() | sort -%}
        {%- if value is true and key not in ('active_count', 'friendly_name', 'icon') %}
        - 🔴 **{{ key.replace('_', ' ') | title }}**
        {%- endif -%}
        {%- endfor -%}
        {%- if attrs.get('fault_description') %}

        ---
        **Inverter-reported fault code**: {{ attrs.get('fault_code') }} — {{ attrs.get('fault_description') }}
        {%- endif %}

  # ── Status from QPIGS device_status_bits (separate from QPIWS) ──────
  - type: glance
    title: Live status flags
    show_state: true
    columns: 3
    entities:
      - entity: binary_sensor.easun_direct_inverter_on
        name: Inverter On
      - entity: binary_sensor.easun_direct_line_fail
        name: Grid Lost
      - entity: binary_sensor.easun_direct_battery_low
        name: Battery Low
      - entity: binary_sensor.easun_direct_charging_to_battery
        name: Charging
      - entity: binary_sensor.easun_direct_ac_charging_active
        name: AC Charge
      - entity: binary_sensor.easun_direct_scc_charging_active
        name: PV Charge
```

---

## With `auto-entities` (HACS) — auto-expand all PI18 warnings too

If you have HACS' `auto-entities` installed, replace the third block
(the conditional markdown) with this — it auto-discovers every active
warn_* / fault flag from both PI30 (qpiws) and PI18 (qfws):

```yaml
  - type: custom:auto-entities
    card:
      type: entities
      title: Active warnings (auto)
    filter:
      template: |
        {% set attrs = state_attr('sensor.easun_direct_inverter_fault_summary') or {} %}
        {%- for key, value in attrs.items() -%}
        {%- if value is true and key not in ('active_count', 'friendly_name', 'icon') %}
        - entity: sensor.easun_direct_inverter_fault_summary
          name: {{ key.replace('_', ' ') | title }}
          icon: mdi:alert-circle
          state: ON
        {%- endif -%}
        {%- endfor %}
    show_empty: false
```

---

## Notification automation

```yaml
- alias: "Inverter warning"
  description: "Notify on any inverter warning bit transitioning ON"
  trigger:
    - platform: state
      entity_id: binary_sensor.easun_direct_any_warning
      to: 'on'
  action:
    - service: notify.persistent_notification
      data:
        title: "⚠️ Inverter warning"
        message: |
          {{ states('sensor.easun_direct_inverter_fault_summary') }}
          ({{ state_attr('sensor.easun_direct_inverter_fault_summary', 'active_count') }} active)
```

For severity-aware notifications (e.g. only ping for hardware faults,
not for grid loss which is normal during an outage), gate on the specific
binary_sensor:

```yaml
- alias: "Inverter hardware fault — urgent"
  trigger:
    - platform: state
      entity_id:
        - binary_sensor.easun_direct_inverter_fault
        - binary_sensor.easun_direct_over_temperature
        - binary_sensor.easun_direct_fan_locked
        - binary_sensor.easun_direct_eeprom_fault
        - binary_sensor.easun_direct_battery_shutdown
      to: 'on'
      for: '00:00:30'   # debounce: 30 sec of sustained ON
  action:
    - service: notify.mobile_app_phone
      data:
        title: "🚨 Inverter hardware fault"
        message: "{{ trigger.entity_id | replace('binary_sensor.', '') | replace('_', ' ') }} is active"
        data:
          priority: high
```

---

## Customisation tips

- **Different entity prefix**: most HA installs use `sensor.{your_inverter_name}_direct_*`. Use Find & Replace on `easun` to swap.
- **Hide "OK" state**: wrap the whole `vertical-stack` in a `conditional` card with `binary_sensor.easun_direct_any_warning == 'on'`. The header card stays in main view, all the granular blocks only appear during incidents.
- **Group by severity**: split the glance into two — "critical hardware" (fault, overload, over_temperature, fan_locked, battery_shutdown) and "warnings" (eeprom_fault, others surfaced via attributes).
- **Mobile-friendly**: change `columns: 3` → `columns: 2` if your dashboard is narrow.
