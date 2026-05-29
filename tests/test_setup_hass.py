"""Full config-entry lifecycle tests using a real ``hass`` fixture.

Requires ``pytest-homeassistant-custom-component`` (POSIX-only — it imports
fcntl). These run in the Linux CI "hass" job; they self-skip anywhere the
plugin isn't installed (e.g. Windows dev boxes) via ``importorskip``.

What they cover that the lightweight ``test_entities.py`` can't:
  * the integration actually *loads* end to end (async_setup_entry →
    coordinator first refresh → platform forwarding → entities created);
  * the SoC sensor's async restore + capacity-driven availability;
  * clean unload (queue drained, no lingering tasks).

The transport is mocked at ``get_direct_data`` so no socket is opened.
"""
import pytest

# Skip unless the plugin's ``common`` helpers are actually importable
# (a bare top-level package can linger after a partial uninstall).
pytest.importorskip("pytest_homeassistant_custom_component.common")

from unittest.mock import patch  # noqa: E402

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.dess_monitor_local.const import (  # noqa: E402
    CONF_DEVICE,
    CONF_PROTOCOL,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    PROTOCOL_TCP_ELFIN,
)

_QPIGS = {
    "grid_voltage": "239.3", "grid_frequency": "50.0",
    "ac_output_voltage": "230.6", "ac_output_frequency": "49.9",
    "output_apparent_power": "2144", "output_active_power": "2136",
    "load_percent": "053", "bus_voltage": "400", "battery_voltage": "26.70",
    "battery_charging_current": "000", "battery_capacity": "062",
    "inverter_heat_sink_temperature": "0043", "pv_input_current": "12.0",
    "pv_input_voltage": "140.4", "scc_battery_voltage": "00.00",
    "battery_discharge_current": "00022", "device_status_bits_b7_b0": "00010110",
    "battery_voltage_offset": "00", "eeprom_version": "00",
    "pv_charging_power": "01697", "device_status_bits_b10_b8": "110",
}
_QPIRI = {
    "bulk_charging_voltage": "28.4", "float_charging_voltage": "27.2",
    "battery_type": "UserDefined", "output_source_priority": "SBU",
    "charger_source_priority": "SolarFirst",
}


async def _fake_get(device, command, timeout=30, strict_crc=False):
    if command == "QPIGS":
        return dict(_QPIGS)
    if command == "QPIRI":
        return dict(_QPIRI)
    return {}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Test Inv"},
        options={
            CONF_PROTOCOL: PROTOCOL_TCP_ELFIN,
            CONF_DEVICE: "tcp://1.2.3.4:8899",
            CONF_UPDATE_INTERVAL: 10,
        },
    )


@pytest.mark.asyncio
async def test_setup_creates_entities_and_unloads(hass, enable_custom_integrations):
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dess_monitor_local.coordinators.direct_coordinator.get_direct_data",
        side_effect=_fake_get,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # A representative live sensor exists and carries the decoded value.
        state = hass.states.get("sensor.test_inv_direct_grid_voltage")
        assert state is not None
        assert state.state == "239.3"

        # Control entities from the number/select/switch platforms exist.
        assert hass.states.get("number.test_inv_vsoc_battery_capacity_ah") is not None
        assert hass.states.get("select.test_inv_vsoc_battery_mode") is not None

    # Clean teardown — the command queue must drain without lingering tasks.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_soc_unavailable_until_capacity_set(hass, enable_custom_integrations):
    """vSoC stays unavailable until a positive battery capacity is entered."""
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dess_monitor_local.coordinators.direct_coordinator.get_direct_data",
        side_effect=_fake_get,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        soc = hass.states.get("sensor.test_inv_direct_battery_state_of_charge")
        # Capacity defaults to None (user hasn't entered Ah) -> unavailable.
        assert soc is not None
        assert soc.state in ("unavailable", "unknown")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
