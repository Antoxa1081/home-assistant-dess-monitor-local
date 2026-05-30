from __future__ import annotations

from .agent import AgentAdapter
from .base import BaseAdapter
from .eybond import EyBondAdapter
from .modbus import ModbusAdapter
from .pi18 import PI18Adapter
from .voltronic import VoltronicAdapter


def get_adapter(device_uri: str, timeout: float = 30.0, strict_crc: bool = False) -> BaseAdapter:
    """Factory function to create the appropriate adapter for a device URI."""
    if device_uri.startswith("agent://"):
        return AgentAdapter(device_uri, timeout, strict_crc)

    # SMG-II Modbus, either over TCP or forwarded through an EyBond dongle.
    if device_uri.startswith(("modbus://", "eybond-modbus://")):
        return ModbusAdapter(device_uri, timeout, strict_crc)

    if device_uri.startswith(("pi18://", "pi18-serial://")):
        return PI18Adapter(device_uri, timeout, strict_crc)

    if device_uri.startswith(("eybond://", "eybond-pi18://")):
        return EyBondAdapter(device_uri, timeout, strict_crc)

    # Default to Voltronic PI30 for tcp:// and serial paths
    return VoltronicAdapter(device_uri, timeout, strict_crc)
