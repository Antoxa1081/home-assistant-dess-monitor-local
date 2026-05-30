from __future__ import annotations

import logging

from ..decoders.pi18 import pi18_to_snapshot
from ..model import DeviceSnapshot
from ..protocols.pi18_tcp import query_pi18
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

class PI18Adapter(BaseAdapter):
    """Adapter for PI18 protocol over TCP or Serial."""

    async def get_snapshot(self) -> DeviceSnapshot:
        """Fetch GS/PIRI/MOD/FWS and assemble the domain model."""
        sections = {
            "qpigs": await self.get_data("QPIGS"),
            "qpiri": await self.get_data("QPIRI"),
            "qmod": await self.get_data("QMOD"),
            "qfws": await self.get_data("QFWS"),
        }
        return self.snapshot_from_sections(sections)

    def snapshot_from_sections(self, sections: dict) -> DeviceSnapshot:
        return pi18_to_snapshot(sections)

    async def get_data(self, command: str) -> dict:
        return await query_pi18(self.uri, command, self.timeout, self.strict_crc)

    async def set_data(self, command: str) -> dict:
        # For PI18, get_data (query_pi18) already handles set commands (ACK/NAK)
        # because the decoder handles ^1 and ^0.
        return await self.get_data(command)
