"""Tests for building hub child poll-targets from the discovery registry
(coordinators/eybond_children.py). Pure — no Home Assistant."""
from custom_components.dess_monitor_local.api.protocols.eybond_discovery import (
    EybondRegistry,
)
from custom_components.dess_monitor_local.coordinators.eybond_children import (
    build_child_targets,
    build_child_uri,
    child_id,
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

        targets = build_child_targets(reg, "0.0.0.0", 8899)
        ids = {t.id for t in targets}
        assert ids == {child_id(reg.get("PN_on"))}
        t = targets[0]
        assert t.id == "eybond:PN_on:1"
        assert t.protocol == "voltronic"
        assert t.name == "Inv A"

    def test_name_falls_back_to_pn(self):
        reg = EybondRegistry()
        _rec(reg, "PN_x", enabled=True, protocol="pi18", name="")
        targets = build_child_targets(reg, "0.0.0.0", 8899)
        assert targets[0].name == "PN_x"

    def test_empty_when_nothing_enabled(self):
        reg = EybondRegistry()
        _rec(reg, "PN1", enabled=False, protocol="voltronic")
        assert build_child_targets(reg, "0.0.0.0", 8899) == []
