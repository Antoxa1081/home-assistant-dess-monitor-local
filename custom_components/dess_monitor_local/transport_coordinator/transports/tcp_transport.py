import asyncio

from custom_components.dess_monitor_local.transport_coordinator.transports.common_transport import \
    BaseInverterTransport, TransportConfig


class _RemoteTcpProtocol(asyncio.Protocol):
    def __init__(self, payload: str, future: asyncio.Future):
        self.payload = payload
        self.future = future
        self.buffer = bytearray()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.transport.write(self.payload)

    def data_received(self, data: bytes):
        self.buffer.extend(data)

        if b"\r" in self.buffer or b"\n" in self.buffer:
            raw = self.buffer.split(b"\r", 1)[0].strip()
            if not self.future.done():
                self.future.set_result(bytes(raw))
            self.transport.close()

    def connection_lost(self, exc):
        if exc and not self.future.done():
            self.future.set_exception(exc)


class TcpTransport(BaseInverterTransport):
    def __init__(self, config: TransportConfig):
        if not config.host or not config.port:
            raise ValueError("TcpTransport requires host and port")

        super().__init__(config)

        self.host = config.host
        self.port = config.port
        self.timeout = config.timeout
        self.loop = None

    async def send(self, payload: str) -> str:
        loop = self.loop or asyncio.get_running_loop()
        future = loop.create_future()

        transport, _ = await loop.create_connection(
            lambda: _RemoteTcpProtocol(payload, future),
            self.host,
            self.port,
        )

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        finally:
            transport.close()

    async def close(self) -> None:
        pass
