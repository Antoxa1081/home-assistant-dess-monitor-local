from __future__ import annotations
from .base import BaseAdapter
from .voltronic import VoltronicAdapter
from .pi18 import PI18Adapter
from .eybond import EyBondAdapter
from .modbus import ModbusAdapter
from .agent import AgentAdapter

def get_adapter(device_uri: str, timeout: float = 30.0, strict_crc: bool = False) -> BaseAdapter:
    """Factory function to create the appropriate adapter for a device URI."""
    if device_uri.startswith("agent://"):
        return AgentAdapter(device_uri, timeout, strict_crc)
    
    if device_uri.startswith("modbus://"):
        return ModbusAdapter(device_uri, timeout, strict_crc)
    
    if device_uri.startswith(("pi18://", "pi18-serial://")):
        return PI18Adapter(device_uri, timeout, strict_crc)
    
    if device_uri.startswith(("eybond://", "eybond-pi18://")):
        return EyBondAdapter(device_uri, timeout, strict_crc)
    
    # Default to Voltronic PI30 for tcp:// and serial paths
    return VoltronicAdapter(device_uri, timeout, strict_crc)
