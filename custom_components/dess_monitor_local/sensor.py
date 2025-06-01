"""Platform for sensor integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.dess_monitor_local.sensors.direct_sensor import DIRECT_SENSORS, generate_qpiri_sensors
from . import HubConfigEntry
from .sensors.direct_energy_sensors import DirectInverterOutputEnergySensor, DirectPV2EnergySensor, \
    DirectPVEnergySensor, DirectBatteryInEnergySensor, DirectBatteryOutEnergySensor, DirectBatteryStateOfChargeSensor

async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: HubConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    hub = config_entry.runtime_data
    new_devices = []

    for item in hub.items:
        new_devices.extend(create_direct_sensors(item, hub.direct_coordinator))
        new_devices.extend(generate_qpiri_sensors(item, hub.direct_coordinator))
        new_devices.extend([
            DirectPVEnergySensor(item, hub.direct_coordinator),
            DirectPV2EnergySensor(item, hub.direct_coordinator),
            DirectInverterOutputEnergySensor(item, hub.direct_coordinator),
            DirectBatteryInEnergySensor(item, hub.direct_coordinator),
            DirectBatteryOutEnergySensor(item, hub.direct_coordinator),
            DirectBatteryStateOfChargeSensor(item, hub.direct_coordinator, hass),
        ])

    if new_devices:
        async_add_entities(new_devices)



def create_direct_sensors(item, coordinator):
    """Return direct protocol-based sensors for an item."""
    direct_sensor_classes = DIRECT_SENSORS
    return [sensor_cls(item, coordinator) for sensor_cls in direct_sensor_classes]
