"""Tests for device-level diagnostics (diagnostics.py).

Needs Home Assistant importable (diagnostics.py imports HA helpers) but no
running instance — the entry, device and coordinator are lightweight fakes.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local import diagnostics, frame_log  # noqa: E402
from custom_components.dess_monitor_local.const import DOMAIN  # noqa: E402
from custom_components.dess_monitor_local.coordinators.device_target import (  # noqa: E402
    DeviceTarget,
)
from custom_components.dess_monitor_local.coordinators.failure_tracker import (  # noqa: E402
    FailureTracker,
)


def _entry(targets, data=None, failures=None, success=False):
    coordinator = SimpleNamespace(
        devices=targets,
        data=data or {},
        last_update_success=success,
        update_interval=timedelta(seconds=10),
        _failures=failures or FailureTracker(),
    )
    return SimpleNamespace(runtime_data=SimpleNamespace(direct_coordinator=coordinator))


def _device(device_id):
    return SimpleNamespace(identifiers={(DOMAIN, device_id)})


@pytest.fixture(autouse=True)
def _clean_frames():
    frame_log.clear()
    yield
    frame_log.clear()


def _diag(entry, device):
    return asyncio.run(diagnostics.async_get_device_diagnostics(None, entry, device))


class TestEybondHubChild:
    TARGET = DeviceTarget(
        id="eybond:PN1:1",
        uri="eybond://192.168.1.5:8899/1?pn=PN1",
        protocol="voltronic",
        name="Inverter PN1",
    )

    def test_target_failures_and_own_frames(self):
        failures = FailureTracker()
        for _ in range(3):
            failures.on_failure("eybond:PN1:1", "QPIGS")
        failures.on_failure("eybond:PN2:1", "QPIGS")  # another child
        frame_log.record("QPIGS", b"", False, outcome="timeout", transport="eybond", pn="PN1", devaddr=1)
        frame_log.record("QPIGS", b"(x", False, transport="eybond", pn="PN2", devaddr=1)
        frame_log.record("QPIRI", b"(y", True)  # unrelated direct transport

        out = _diag(_entry([self.TARGET], failures=failures), _device("eybond:PN1:1"))

        target = out["device"]["target"]
        assert out["device"]["id"] == "eybond:PN1:1"
        assert target["protocol"] == "voltronic"
        assert target["transport"] == "eybond"
        assert target["uri"] == "**REDACTED**"
        assert (target["pn"], target["devaddr"]) == ("PN1", 1)
        assert out["coordinator"]["last_update_success"] is False
        assert out["coordinator"]["consecutive_failures"] == {"QPIGS": 3}
        assert list(out["frames"]) == ["QPIGS"]
        assert out["frames"]["QPIGS"][0]["outcome"] == "timeout"
        assert "192.168.1.5" not in repr(out)


class TestLegacyDeviceEntry:
    URI = "tcp://10.0.0.7:8899"
    TARGET = DeviceTarget(id=URI, uri=URI, protocol="voltronic", name="Inverter")

    def test_uri_id_redacted_and_untagged_frames(self):
        frame_log.record("QPIGS", b"(1", True)
        frame_log.record("QPIGS", b"(2", True, transport="eybond", pn="PN1", devaddr=1)
        out = _diag(
            _entry([self.TARGET], data={self.URI: {"qpigs": {"v": 1}}}, success=True),
            _device(self.URI),
        )
        assert out["device"]["id"] == "**REDACTED**"
        assert out["device"]["target"]["transport"] == "tcp"
        assert out["device"]["direct_data"] == {"qpigs": {"v": 1}}
        assert out["coordinator"]["consecutive_failures"] == {}
        assert [e["raw_ascii"] for e in out["frames"]["QPIGS"]] == ["(1"]
        assert "10.0.0.7" not in repr(out)

    def test_no_runtime_data(self):
        out = _diag(SimpleNamespace(runtime_data=None), _device(self.URI))
        assert out["device"]["target"] == {"present": False}
        assert out["coordinator"] == {"present": False}
