"""Tests for building hub child poll-targets from the discovery registry
(coordinators/eybond_children.py). Pure — no Home Assistant."""
from custom_components.dess_monitor_local.api.protocols.eybond_discovery import (
    EybondRegistry,
)
from custom_components.dess_monitor_local.coordinators.eybond_children import (
    build_child_targets,
    build_child_uri,
)


def _rec(reg, pn, *, enabled=False, protocol=None, devaddr=1, name=""):
    reg.record_seen(pn, "10.0.0.1:1")
    reg.set_devaddr(pn, devaddr)
    reg.set_protocol(pn, protocol)
    reg.set_name(pn, name)
    reg.set_enabled(pn, enabled)
    return reg.get(pn)


class TestChildUri:
    def test_voltronic_scheme_and_pn(self):
        reg = EybondRegistry()
        rec = _rec(reg, "PN1", protocol="voltronic", devaddr=2)
        uri = build_child_uri(rec, "0.0.0.0", 8899)
        assert uri == "eybond://0.0.0.0:8899/2?pn=PN1"

    def test_pi18_scheme(self):
        reg = EybondRegistry()
        rec = _rec(reg, "PN1", protocol="pi18", devaddr=1)
        uri = build_child_uri(rec, "0.0.0.0", 8899)
        assert uri.startswith("eybond-pi18://0.0.0.0:8899/1?pn=PN1")

    def test_modbus_scheme(self):
        reg = EybondRegistry()
        rec = _rec(reg, "PN1", protocol="modbus", devaddr=3)
        uri = build_child_uri(rec, "0.0.0.0", 8899)
        # SMG-II Modbus over the dongle; devaddr doubles as the unit id.
        assert uri.startswith("eybond-modbus://0.0.0.0:8899/3?pn=PN1")

    def test_custom_broadcast_and_announce_included(self):
        reg = EybondRegistry()
        rec = _rec(reg, "PN1", protocol="voltronic")
        uri = build_child_uri(
            rec, "0.0.0.0", 8899, broadcast="192.168.1.255", announce_ip="192.168.1.10"
        )
        assert "broadcast=192.168.1.255" in uri
        assert "announce=192.168.1.10" in uri
        assert "pn=PN1" in uri

    def test_default_broadcast_omitted(self):
        reg = EybondRegistry()
        rec = _rec(reg, "PN1", protocol="voltronic")
        uri = build_child_uri(rec, "0.0.0.0", 8899)  # default broadcast
        assert "broadcast=" not in uri


class TestChildTargets:
    def test_only_enabled_supported_protocol(self):
        reg = EybondRegistry()
        _rec(reg, "PN_on", enabled=True, protocol="voltronic", name="Inv A")
        _rec(reg, "PN_off", enabled=False, protocol="voltronic")
        _rec(reg, "PN_noproto", enabled=True, protocol=None)
        _rec(reg, "PN_modbus", enabled=True, protocol="modbus")
        # Agent is HTTP-only — not forwardable through a dongle, so skipped.
        _rec(reg, "PN_agent", enabled=True, protocol="agent")

        targets = build_child_targets(reg, "0.0.0.0", 8899)
        ids = {t.id for t in targets}
        assert ids == {"eybond:PN_on:1", "eybond:PN_modbus:1"}
        by_id = {t.id: t for t in targets}
        assert by_id["eybond:PN_on:1"].protocol == "voltronic"
        assert by_id["eybond:PN_on:1"].name == "Inv A"
        assert by_id["eybond:PN_modbus:1"].protocol == "modbus"

    def test_name_falls_back_to_pn(self):
        reg = EybondRegistry()
        _rec(reg, "PN_x", enabled=True, protocol="pi18", name="")
        targets = build_child_targets(reg, "0.0.0.0", 8899)
        assert targets[0].name == "PN_x"

    def test_empty_when_nothing_enabled(self):
        reg = EybondRegistry()
        _rec(reg, "PN1", enabled=False, protocol="voltronic")
        assert build_child_targets(reg, "0.0.0.0", 8899) == []

    def test_legacy_id_overrides_target_id(self):
        # A migrated child keeps its original URI as id so unique_ids/history
        # survive; the poll uri still carries the live pn routing.
        reg = EybondRegistry()
        rec = _rec(reg, "PN_mig", enabled=True, protocol="voltronic")
        rec.legacy_id = "eybond://0.0.0.0:8899/1?broadcast=10.0.0.255"
        targets = build_child_targets(reg, "0.0.0.0", 8899)
        assert targets[0].id == "eybond://0.0.0.0:8899/1?broadcast=10.0.0.255"
        assert "pn=PN_mig" in targets[0].uri  # routing still by pn
