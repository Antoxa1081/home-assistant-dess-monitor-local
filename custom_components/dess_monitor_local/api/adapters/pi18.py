from __future__ import annotations

import logging

from ..protocols.pi18_tcp import query_pi18
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

class PI18Adapter(BaseAdapter):
    """Adapter for PI18 protocol over TCP or Serial."""

    async def get_data(self, command: str) -> dict:
        return await query_pi18(self.uri, command, self.timeout, self.strict_crc)

    async def set_data(self, command: str) -> dict:
        # For PI18, get_data (query_pi18) already handles set commands (ACK/NAK)
        # because the decoder handles ^1 and ^0.
        return await self.get_data(command)
