from __future__ import annotations

from abc import ABC, abstractmethod

from ..decoders.enums import (
    BatteryTypeSetting,
    ChargeSourcePrioritySetting,
    OutputSourcePrioritySetting,
)


class BaseAdapter(ABC):
    """Abstract base class for all communication adapters."""

    def __init__(self, uri: str, timeout: float = 30.0, strict_crc: bool = False):
        self.uri = uri
        self.timeout = timeout
        self.strict_crc = strict_crc

    @abstractmethod
    async def get_data(self, command: str) -> dict:
        """Read data from the device."""
        pass

    @abstractmethod
    async def set_data(self, command: str) -> dict:
        """Send a raw set command to the device."""
        pass

    # --- Semantic settings (default Voltronic implementation) ---

    async def set_battery_type(self, battery_type: BatteryTypeSetting) -> dict:
        return await self.set_data(battery_type.value)

    async def set_output_source_priority(self, mode: OutputSourcePrioritySetting) -> dict:
        return await self.set_data(mode.value)

    async def set_charge_source_priority(self, mode: ChargeSourcePrioritySetting) -> dict:
        return await self.set_data(mode.value)

    async def set_battery_bulk_voltage(self, voltage: float) -> dict:
        return await self.set_data(f"PBAV{voltage:.2f}")

    async def set_battery_float_voltage(self, voltage: float) -> dict:
        return await self.set_data(f"PBFV{voltage:.2f}")

    async def set_rated_battery_voltage(self, voltage: int) -> dict:
        return await self.set_data(f"PBRV{voltage}")

    async def set_max_combined_charge_current(self, amps: int) -> dict:
        return await self.set_data(f"MCHGC{amps:03d}")

    async def set_battery_charge_current(self, amps: int) -> dict:
        return await self.set_data(f"PBATC{amps:03d}")

    async def set_max_utility_charge_current(self, amps: int, float_format: bool = False) -> dict:
        payload = f"MUCHGC{amps:04.1f}" if float_format else f"MUCHGC{amps:03d}"
        return await self.set_data(payload)
