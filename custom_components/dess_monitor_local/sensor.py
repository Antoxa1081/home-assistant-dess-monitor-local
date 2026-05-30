"""Platform for sensor integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.dess_monitor_local.const import (
    CONF_ENTRY_KIND,
    CONF_PROTOCOL,
    ENTRY_KIND_EYBOND_HUB,
    PROTOCOL_MODBUS,
    PROTOCOL_PI18,
)
from custom_components.dess_monitor_local.sensors.direct_sensor import (
    DIRECT_SENSORS,
    PI18_SENSORS,
    generate_qpiri_sensors,
)

from . import HubConfigEntry
from .sensors.direct_energy_sensors import (
    DirectBatteryBackupTimeSensor,
    DirectBatteryInEnergySensor,
    DirectBatteryOutEnergySensor,
    DirectBatteryStateOfChargeSensor,
    DirectBatteryTimeToFloorSensor,
    DirectBatteryTimeToFullSensor,
    DirectBatteryVSocLastSyncSensor,
    DirectInverterOutputEnergySensor,
    DirectPV2EnergySensor,
    DirectPVEnergySensor,
)

# Capability gating (domain-model refactor, Phase C group 4): (section, key)
# pairs a protocol structurally cannot report. The matching sensors are not
# created at all — so e.g. SMG-II no longer produces fake bus_voltage /
# nameplate / reserved sensors (previously these showed a fabricated constant;
# after the metric migration they'd show no value — now they simply don't
# exist). Real fields (charge voltages, priorities, mode) are unaffected.
_FABRICATED_QPIRI = {
    ("qpiri", "parallel_max_number"),
    ("qpiri", "parallel_mode"),
    ("qpiri", "rated_battery_capacity"),
    ("qpiri", "reserved_uu"),
    ("qpiri", "reserved_v"),
    ("qpiri", "reserved_b"),
    ("qpiri", "reserved_ccc"),
    ("qpiri", "solar_work_condition_in_parallel"),
    ("qpiri", "solar_max_charging_power_auto_adjust"),
}
_UNSUPPORTED_KEYS = {
    PROTOCOL_MODBUS: {
        ("qpigs", "bus_voltage"),
        ("qpigs", "scc_battery_voltage"),
        ("qpigs", "battery_capacity"),
        ("qpiri", "rated_grid_voltage"),
        ("qpiri", "rated_input_current"),
        ("qpiri", "rated_ac_output_voltage"),
        ("qpiri", "rated_output_frequency"),
        ("qpiri", "rated_output_current"),
        ("qpiri", "rated_output_apparent_power"),
        ("qpiri", "rated_output_active_power"),
        ("qpiri", "rated_battery_voltage"),
        ("qpiri", "battery_type"),
        *_FABRICATED_QPIRI,
    },
    # PI18 reports real rated_* but fabricates bus voltage + the parallel/
    # reserved/solar nameplate tail.
    PROTOCOL_PI18: {("qpigs", "bus_voltage"), *_FABRICATED_QPIRI},
}


def _supported(sensor, protocol: str | None) -> bool:
    """Whether a sensor's (section, key) is reportable by the protocol."""
    key = (getattr(sensor, "data_section", None), getattr(sensor, "data_key", None))
    return key not in _UNSUPPORTED_KEYS.get(protocol, ())


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: HubConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    hub = config_entry.runtime_data
    new_devices = []
    entry_protocol = config_entry.options.get(CONF_PROTOCOL)

    # Hub entries get a diagnostic device showing discovered dongles, so the
    # integration is visibly "working" even before any child is configured.
    if config_entry.options.get(CONF_ENTRY_KIND) == ENTRY_KIND_EYBOND_HUB:
        from .sensors.eybond_hub_sensor import EybondHubDiscoverySensor

        new_devices.append(EybondHubDiscoverySensor(hass, config_entry))

    for item in hub.items:
        # Per-item protocol (a hub may mix protocols across children); fall
        # back to the entry-level option for legacy single-device entries.
        protocol = getattr(item, "protocol", None) or entry_protocol
        is_pi18 = protocol == PROTOCOL_PI18

        # Construct the SoC sensor first — the time-to-* sensors hold a
        # reference to it so they read SoC and capacity from the same
        # in-memory state, avoiding any cross-entity state-lookup race
        # and guaranteeing all derived numbers move in lockstep.
        soc_sensor = DirectBatteryStateOfChargeSensor(item, hub.direct_coordinator, hass)

        # Capability gating: drop sensors for fields this protocol can't report.
        new_devices.extend(
            s for s in create_direct_sensors(item, hub.direct_coordinator)
            if _supported(s, protocol)
        )
        new_devices.extend(
            s for s in generate_qpiri_sensors(item, hub.direct_coordinator)
            if _supported(s, protocol)
        )
        new_devices.extend([
            DirectPVEnergySensor(item, hub.direct_coordinator),
            DirectPV2EnergySensor(item, hub.direct_coordinator),
            DirectInverterOutputEnergySensor(item, hub.direct_coordinator),
            DirectBatteryInEnergySensor(item, hub.direct_coordinator),
            DirectBatteryOutEnergySensor(item, hub.direct_coordinator),
            soc_sensor,
            DirectBatteryTimeToFloorSensor(item, hub.direct_coordinator, soc_sensor, hass),
            DirectBatteryTimeToFullSensor(item, hub.direct_coordinator, soc_sensor),
            DirectBatteryBackupTimeSensor(item, hub.direct_coordinator, soc_sensor, hass),
            DirectBatteryVSocLastSyncSensor(item, hub.direct_coordinator, soc_sensor),
        ])
        if is_pi18:
            # PI18 GS response exposes a second MPPT, two temperatures, and
            # several direction flags that don't exist in Voltronic PI30.
            # Only wire them up when the user is actually on PI18 — keeps the
            # entity list clean for PI30 deployments.
            new_devices.extend(
                sensor_cls(item, hub.direct_coordinator) for sensor_cls in PI18_SENSORS
            )

    if new_devices:
        async_add_entities(new_devices)


def create_direct_sensors(item, coordinator):
    """Return direct protocol-based sensors for an item."""
    direct_sensor_classes = DIRECT_SENSORS
    return [sensor_cls(item, coordinator) for sensor_cls in direct_sensor_classes]
