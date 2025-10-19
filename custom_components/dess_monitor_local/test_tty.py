import time

import serial


def decode_qpigs(ascii_str: str) -> dict:
    """Parse QPIGS ASCII response string into a dictionary with meaningful keys."""
    values = ascii_str.strip().split()
    fields = [
        "grid_voltage",
        "grid_frequency",
        "ac_output_voltage",
        "ac_output_frequency",
        "output_apparent_power",
        "output_active_power",
        "load_percent",
        "bus_voltage",
        "battery_voltage",
        "battery_charging_current",
        "battery_capacity",
        "inverter_heat_sink_temperature",
        "pv_input_current",
        "pv_input_voltage",
        "scc_battery_voltage",
        "battery_discharge_current",
        "device_status_bits_b7_b0",
        "battery_voltage_offset",
        "eeprom_version",
        "pv_charging_power",
        "device_status_bits_b10_b8",
        "reserved_a",
        "reserved_b",
        "reserved_c"
    ]

    def parse_value(v):
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except ValueError:
            return v

    parsed_values = list(map(parse_value, values))
    return dict(zip(fields, parsed_values))


def main():
    port = '/dev/ttyUSB0'  # your USB→RS232 adapter
    baudrate = 2400
    timeout = 1

    with serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout
    ) as ser:
        time.sleep(0.2)  # Give the inverter a moment to wake
        ser.reset_input_buffer()  # Flush out any stray CRs before we send

        packet = b'QPIGS' + b'\r\n'  # Try CR+LF terminator
        ser.write(packet)

        time.sleep(0.3)
        response = ser.read(200)  # Read up to 200 bytes just in case

        if not response:
            print("No response from inverter (timeout or wiring issue).")
            return

        print("Raw response (hex):", response.hex())

        try:
            decoded_str = response.strip().decode('ascii')
        except Exception as e:
            print("ASCII decoding error:", e)
            return

        print("Decoded ASCII payload:", decoded_str)

        try:
            decoded_data = decode_qpigs(decoded_str)
        except Exception as e:
            print("Parsing error:", e)
            return

        for key, val in decoded_data.items():
            print(f"{key}: {val}")


if __name__ == '__main__':
    main()
