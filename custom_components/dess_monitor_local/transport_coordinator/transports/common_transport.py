from abc import ABC, abstractmethod

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TransportType(Enum):
    TCP = "TCP"
    SERIAL = "SERIAL"
    DESS_SERIAL = "DESS_SERIAL"
    # DESS_API = "DESS_API"


@dataclass(slots=True)
class TransportConfig:
    # common
    timeout: float = 5.0

    # tcp
    host: Optional[str] = None
    port: Optional[int] = None

    # serial
    device: Optional[str] = None
    baudrate: Optional[int] = None

    # dessmonitor
    username: Optional[str] = None
    password: Optional[str] = None
    datalogger_sn: Optional[str] = None


class BaseInverterTransport(ABC):
    def __init__(self, config: TransportConfig):
        self.config = config

    @abstractmethod
    async def send(self, payload: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
