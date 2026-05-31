"""Tests for the EyBond hub bulk device-config parsing (config_flow).

Covers ``EybondHubOptionsFlow._apply_device_bulk`` — the pure mapping from one
bulk-form submission (per-PN ``<pn>__field`` keys) onto the discovery registry.
Needs ``homeassistant`` importable (to import config_flow) but no hass fixture.
"""
import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.api.protocols.eybond_discovery import (  # noqa: E402
    DongleRecord,
    EybondRegistry,
)
from custom_components.dess_monitor_local.config_flow import (  # noqa: E402
    EybondHubOptionsFlow,
)

_apply = EybondHubOptionsFlow._apply_device_bulk


def _registry(*pns):
    reg = EybondRegistry()
    for pn in pns:
        reg.put(DongleRecord(pn=pn))
    return reg


class TestBulkApply:
    def test_configures_enables_and_disables_in_one_pass(self):
        reg = _registry("PNAAA", "PNBBB")
        ui = {
            "PNAAA__enabled": True, "PNAAA__name": "Inv A",
            "PNAAA__protocol": "voltronic", "PNAAA__devaddr": 1,
            "PNBBB__enabled": False, "PNBBB__name": "",
            "PNBBB__protocol": "none", "PNBBB__devaddr": 3,
        }
        _apply(reg, reg.all(), ui)

        a = reg.get("PNAAA")
        assert a.enabled and a.protocol == "voltronic"
        assert a.name == "Inv A" and a.devaddr == 1
        b = reg.get("PNBBB")
        # protocol "none" → unconfigured (None); disabled; devaddr applied.
        assert not b.enabled and b.protocol is None and b.devaddr == 3

    def test_remove_flag_drops_record_others_unaffected(self):
        reg = _registry("PNAAA", "PNBBB")
        ui = {
            "PNAAA__remove": True,
            "PNBBB__enabled": True, "PNBBB__protocol": "pi18",
            "PNBBB__devaddr": 2,
        }
        _apply(reg, reg.all(), ui)

        assert reg.get("PNAAA") is None              # removed
        b = reg.get("PNBBB")
        assert b.enabled and b.protocol == "pi18" and b.devaddr == 2

    def test_name_trimmed_and_devaddr_coerced_from_float(self):
        reg = _registry("PNAAA")
        # NumberSelector hands back a float; name may carry whitespace.
        _apply(reg, reg.all(), {
            "PNAAA__enabled": True, "PNAAA__name": "  Garage  ",
            "PNAAA__protocol": "modbus", "PNAAA__devaddr": 5.0,
        })
        a = reg.get("PNAAA")
        assert a.name == "Garage" and a.devaddr == 5 and a.protocol == "modbus"
