from custom_components.dess_monitor_local.device_protocols.abstract_procol import BaseInverterProtocol
from custom_components.dess_monitor_local.transport_coordinator.transports.common_transport import BaseInverterTransport
from custom_components.dess_monitor_local.transport_coordinator.transports.serial_transport import SerialTransport
from custom_components.dess_monitor_local.transport_coordinator.transports.tcp_transport import TcpTransport


class VoltronicProtocol(BaseInverterProtocol):
    SUPPORTED_TRANSPORTS = (TcpTransport, SerialTransport)

    def _validate_transport(self, transport: BaseInverterTransport) -> None:
        if not isinstance(transport, self.SUPPORTED_TRANSPORTS):
            raise TypeError(
                f"{self.__class__.__name__} does not support {type(transport).__name__}"
            )

    # ----- internal -----

    def _build_packet(self, command: str) -> bytes:
        cmd = command.encode("ascii")
        return cmd + crc16(cmd) + b"\r"

    def _parse_response(self, command: str, raw: bytes):
        text = raw.decode(errors="ignore").strip("()")

        if command == "QPIGS":
            parts = text.split()
            return InverterSensors(
                grid_voltage=float(parts[0]),
                grid_frequency=float(parts[1]),
                ac_voltage=float(parts[2]),
                ac_frequency=float(parts[3]),
                battery_voltage=float(parts[8]),
            )

        if command == "QPIRI":
            parts = text.split()
            return InverterConfig(
                rated_ac_voltage=float(parts[0]),
                rated_ac_frequency=float(parts[1]),
                max_charge_current=int(parts[6]),
            )

        raise ValueError(f"Unsupported command {command}")

    # ----- public API -----

    async def get_device_sensors(self) -> InverterSensors:
        return await self._execute("QPIGS")

    async def get_device_config(self) -> InverterConfig:
        return await self._execute("QPIRI")
