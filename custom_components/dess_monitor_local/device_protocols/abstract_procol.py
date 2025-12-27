from abc import ABC, abstractmethod
from typing import Any

from custom_components.dess_monitor_local.transport_coordinator.transports.common_transport import BaseInverterTransport
from custom_components.dess_monitor_local.types import InverterSensorData, InverterSettings, InverterSnapshot


class BaseInverterProtocol(ABC):
    def __init__(self, transport: BaseInverterTransport):
        self.transport = transport
        self._validate_transport(transport)

    @abstractmethod
    def _validate_transport(self, transport: BaseInverterTransport) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_device_sensors(self) -> InverterSensorData:
        raise NotImplementedError

    @abstractmethod
    async def get_device_config(self) -> InverterSettings:
        raise NotImplementedError

    @abstractmethod
    async def get_device_snapshot(self) -> InverterSnapshot:
        raise NotImplementedError


    # ----- internal helpers -----

    @abstractmethod
    def _parse_response(self, command: str, raw: str) -> Any:
        raise NotImplementedError

    async def _execute(self, command: str) -> Any:
        raw = await self.transport.send(command)
        return self._parse_response(command, raw)