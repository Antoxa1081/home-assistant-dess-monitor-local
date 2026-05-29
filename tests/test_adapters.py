"""Adapter layer: factory URI routing + the BaseAdapter semantic-command
formatting (the Voltronic default set_* payloads).

Gated on Home Assistant being importable only because importing the
adapter package pulls the transport modules (aiohttp / serial). The logic
itself is HA-free. Runs in the CI "hass" job alongside the entity tests.
"""
import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.api.adapters import factory  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.agent import AgentAdapter  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.base import BaseAdapter  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.eybond import EyBondAdapter  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.modbus import ModbusAdapter  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.pi18 import PI18Adapter  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.voltronic import VoltronicAdapter  # noqa: E402
from custom_components.dess_monitor_local.api.decoders.enums import (  # noqa: E402
    BatteryTypeSetting,
    ChargeSourcePrioritySetting,
    OutputSourcePrioritySetting,
)


class TestFactoryRouting:
    @pytest.mark.parametrize("uri,cls", [
        ("agent://10.0.0.5:8787/dev1", AgentAdapter),
        ("modbus://10.0.0.5:502", ModbusAdapter),
        ("pi18://10.0.0.5:8899", PI18Adapter),
        ("pi18-serial:///dev/ttyUSB0", PI18Adapter),
        ("eybond://0.0.0.0:8899/1", EyBondAdapter),
        ("eybond-pi18://0.0.0.0:8899/1", EyBondAdapter),
        ("tcp://10.0.0.5:8899", VoltronicAdapter),
        ("/dev/ttyUSB0", VoltronicAdapter),       # bare serial path -> default
        ("COM3", VoltronicAdapter),               # Windows serial -> default
    ])
    def test_scheme_maps_to_adapter(self, uri, cls):
        assert isinstance(factory.get_adapter(uri), cls)

    def test_passes_timeout_and_strict_crc(self):
        a = factory.get_adapter("tcp://x:1", timeout=12.5, strict_crc=True)
        assert a.timeout == 12.5
        assert a.strict_crc is True
        assert a.uri == "tcp://x:1"

    def test_eybond_pi18_flag(self):
        a = factory.get_adapter("eybond-pi18://0.0.0.0:8899/1")
        assert a.is_pi18 is True
        assert factory.get_adapter("eybond://0.0.0.0:8899/1").is_pi18 is False


class _RecordingAdapter(BaseAdapter):
    """Captures the raw payload that the semantic helpers produce."""
    def __init__(self):
        super().__init__("tcp://x:1")
        self.sent = None

    async def get_data(self, command):
        return {}

    async def set_data(self, command):
        self.sent = command
        return {"status": "OK"}


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class TestBaseSemanticCommands:
    def test_bulk_voltage_format(self):
        a = _RecordingAdapter()
        _run(a.set_battery_bulk_voltage(28.4))
        assert a.sent == "PBAV28.40"

    def test_float_voltage_format(self):
        a = _RecordingAdapter()
        _run(a.set_battery_float_voltage(27.2))
        assert a.sent == "PBFV27.20"

    def test_rated_voltage_format(self):
        a = _RecordingAdapter()
        _run(a.set_rated_battery_voltage(24))
        assert a.sent == "PBRV24"

    def test_max_combined_charge_current(self):
        a = _RecordingAdapter()
        _run(a.set_max_combined_charge_current(50))
        assert a.sent == "MCHGC050"

    def test_battery_charge_current(self):
        a = _RecordingAdapter()
        _run(a.set_battery_charge_current(40))
        assert a.sent == "PBATC040"

    def test_max_utility_charge_current_int(self):
        a = _RecordingAdapter()
        _run(a.set_max_utility_charge_current(30))
        assert a.sent == "MUCHGC030"

    def test_max_utility_charge_current_float(self):
        a = _RecordingAdapter()
        _run(a.set_max_utility_charge_current(2, float_format=True))
        assert a.sent == "MUCHGC02.0"

    def test_output_priority_uses_enum_value(self):
        a = _RecordingAdapter()
        _run(a.set_output_source_priority(OutputSourcePrioritySetting.SBU_PRIORITY))
        assert a.sent == OutputSourcePrioritySetting.SBU_PRIORITY.value

    def test_charge_priority_uses_enum_value(self):
        a = _RecordingAdapter()
        _run(a.set_charge_source_priority(ChargeSourcePrioritySetting.SOLAR_FIRST))
        assert a.sent == ChargeSourcePrioritySetting.SOLAR_FIRST.value

    def test_battery_type_uses_enum_value(self):
        a = _RecordingAdapter()
        _run(a.set_battery_type(BatteryTypeSetting.LIFEP04))
        assert a.sent == BatteryTypeSetting.LIFEP04.value
