import asyncio
import serial_asyncio_fast as serial_asyncio

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, byteorder='little')


class SerialCommandProtocol(asyncio.Protocol):
    def __init__(self, command: bytes, on_response):
        self.transport = None
        self.command = command
        self.buffer = bytearray()
        self.on_response = on_response

    def connection_made(self, transport):
        self.transport = transport
        # Формируем пакет: команда + CRC16 + \r
        crc = crc16(self.command)
        packet = self.command + crc + b'\r'
        self.transport.write(packet)

    def data_received(self, data):
        self.buffer.extend(data)
        # Ждём, пока придёт \r (окончание)
        if b'\r' in self.buffer:
            idx = self.buffer.index(b'\r')
            response = self.buffer[:idx]
            # вырезаем CRC (2 байта в конце)
            if len(response) < 3:
                self.on_response(None, Exception("Response too short"))
                self.transport.close()
                return
            data_part = response[:-2]
            received_crc = response[-2:]
            calc_crc = crc16(data_part)
            if calc_crc != received_crc:
                self.on_response(None, Exception("CRC mismatch"))
                self.transport.close()
                return
            try:
                decoded = data_part.decode('ascii').strip()
            except Exception as e:
                self.on_response(None, e)
                self.transport.close()
                return
            self.on_response(decoded, None)
            self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


async def get_direct_data(device: str, command_str: str) -> str:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def on_response(data, err):
        if err:
            fut.set_exception(err)
        else:
            fut.set_result(data)

    command_bytes = command_str.encode('ascii')
    transport, protocol = await serial_asyncio.create_serial_connection(
        loop,
        lambda: SerialCommandProtocol(command_bytes, on_response),
        device,
        baudrate=2400,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=1,
    )

    try:
        result = await asyncio.wait_for(fut, timeout=5)
    finally:
        transport.close()
    return result
