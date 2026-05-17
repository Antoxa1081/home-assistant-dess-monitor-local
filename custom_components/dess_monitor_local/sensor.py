"""Platform for sensor integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.dess_monitor_local.const import CONF_PROTOCOL, PROTOCOL_PI18
from custom_components.dess_monitor_local.sensors.direct_sensor import (
    DIRECT_SENSORS,
    PI18_SENSORS,
    generate_qpiri_sensors,
)
from . import HubConfigEntry
from .sensors.direct_energy_sensors import (
    DirectInverterOutputEnergySensor,
    DirectPV2EnergySensor,
    DirectPVEnergySensor,
    DirectBatteryInEnergySensor,
    DirectBatteryOutEnergySensor,
    DirectBatteryStateOfChargeSensor,
    DirectBatteryTimeToFloorSensor,
    DirectBatteryTimeToFullSensor,
    DirectBatteryBackupTimeSensor,
    DirectBatteryVSocLastSyncSensor,
)


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: HubConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    hub = config_entry.runtime_data
    new_devices = []
    is_pi18 = config_entry.options.get(CONF_PROTOCOL) == PROTOCOL_PI18

    for item in hub.items:
        # Construct the SoC sensor first — the time-to-* sensors hold a
        # reference to it so they read SoC and capacity from the same
        # in-memory state, avoiding any cross-entity state-lookup race
        # and guaranteeing all derived numbers move in lockstep.
        soc_sensor = DirectBatteryStateOfChargeSensor(item, hub.direct_coordinator, hass)

        new_devices.extend(create_direct_sensors(item, hub.direct_coordinator))
        new_devices.extend(generate_qpiri_sensors(item, hub.direct_coordinator))
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
