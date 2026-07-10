import argparse
import logging
import time

import serial
import serial.tools.list_ports


#python main.py --port COM2 --baud 9600


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
_logger = logging.getLogger(__name__)


BAUDRATES = [2400, 4800, 9600, 19200, 38400, 57600, 115200]
BYTESIZE = 8
PARITY = "N"
STOPBITS = 1
TIMEOUT = 0.06

START_ADDRESS = 1
END_ADDRESS = 247
DEFAULT_REGISTER = 0x33


def calculate_crc(data):
    """Calculate Modbus RTU CRC16."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc


def append_crc(frame):
    crc = calculate_crc(frame)
    frame.append(crc & 0xFF)
    frame.append((crc >> 8) & 0xFF)
    return frame


def has_valid_crc(frame):
    if len(frame) < 4:
        return False

    received = frame[-2] | (frame[-1] << 8)
    return calculate_crc(frame[:-2]) == received


def build_read_request(address, register=DEFAULT_REGISTER, count=1):
    return append_crc(
        bytearray(
            [
                address,
                0x03,
                (register >> 8) & 0xFF,
                register & 0xFF,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ]
        )
    )


def expected_response_size(response_header):
    if len(response_header) < 2:
        return 0

    function_code = response_header[1]
    if function_code & 0x80:
        return 5

    if len(response_header) < 3:
        return 0

    return 3 + response_header[2] + 2


def find_moxa_port():
    """Find the first likely MOXA USB serial port."""
    ports = list(serial.tools.list_ports.comports())
    _logger.info("Available ports:")

    for port in ports:
        _logger.info("  %s: %s (HWID: %s)", port.device, port.description, port.hwid)
        text = f"{port.description} {port.hwid}".upper()
        if any(marker in text for marker in ("MOXA", "UPORT", "1150", "USB SERIAL PORT")):
            _logger.info("Selected port: %s", port.device)
            return port.device

    return None


def open_serial_port(port, baudrate, timeout=TIMEOUT):
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=timeout,
        write_timeout=timeout,
    )


def read_modbus_response(ser):
    header = ser.read(3)
    if not header:
        return b""

    size = expected_response_size(header)
    if size <= len(header):
        return bytes(header)

    return bytes(header + ser.read(size - len(header)))


def test_serial_port(port, baudrate):
    """Open the port and try one Modbus request to address 1."""
    try:
        _logger.info("Opening %s at %s baud...", port, baudrate)
        with open_serial_port(port, baudrate) as ser:
            request = build_read_request(address=16)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(request)
            ser.flush()
            response = read_modbus_response(ser)

        if not response:
            _logger.info("Port opened at %s baud, but address 16 did not answer.", baudrate)
            return False

        if has_valid_crc(response):
            _logger.info("Address 16 answered at %s baud: %s", baudrate, response.hex())
            return True

        _logger.warning("Got response with bad CRC at %s baud: %s", baudrate, response.hex())
        return False

    except serial.SerialException as exc:
        _logger.error("Serial error on %s at %s baud: %s", port, baudrate, exc)
        return False


def scan_modbus_addresses(
    port,
    baudrate,
    start_address=START_ADDRESS,
    end_address=END_ADDRESS,
    timeout=TIMEOUT,
):
    """Scan Modbus RTU slave addresses with a read holding registers request."""
    found_devices = []

    try:
        with open_serial_port(port, baudrate, timeout=timeout) as ser:
            _logger.info(
                "--- Scanning addresses %s-%s at %s baud ---",
                start_address,
                end_address,
                baudrate,
            )

            for address in range(start_address, end_address + 1):
                request = build_read_request(address)
                ser.reset_input_buffer()
                ser.write(request)
                ser.flush()

                time.sleep(0.004)
                response = read_modbus_response(ser)

                if not response:
                    continue

                if response[0] != address:
                    _logger.debug(
                        "Ignoring response from address %s while scanning %s: %s",
                        response[0],
                        address,
                        response.hex(),
                    )
                    continue

                if not has_valid_crc(response):
                    _logger.debug("Ignoring response with bad CRC from %s: %s", address, response.hex())
                    continue

                _logger.info("Found device at address %s, response: %s", address, response.hex())
                found_devices.append(address)

    except serial.SerialException as exc:
        _logger.error("Serial error while scanning %s at %s baud: %s", port, baudrate, exc)

    return found_devices


def scan_all_speeds(
    port,
    start_address=START_ADDRESS,
    end_address=END_ADDRESS,
    timeout=TIMEOUT,
    baudrates=None,
):
    """Scan all configured baud rates."""
    baudrates = baudrates or BAUDRATES
    all_found = {}

    _logger.info("=" * 60)
    _logger.info("Fast Modbus address scan")
    _logger.info("=" * 60)

    for baudrate in baudrates:
        found = scan_modbus_addresses(port, baudrate, start_address, end_address, timeout)
        if found:
            all_found[baudrate] = found
            _logger.info("At %s baud found addresses: %s", baudrate, found)
        else:
            _logger.info("No devices found at %s baud.", baudrate)

    return all_found


def scan_all(port, start_address=START_ADDRESS, end_address=END_ADDRESS, timeout=TIMEOUT):
    """Backward-compatible alias for scan_all_speeds."""
    return scan_all_speeds(port, start_address, end_address, timeout)


def parse_args():
    parser = argparse.ArgumentParser(description="Scan Modbus RTU devices through a serial port.")
    parser.add_argument("--port", help="Serial port, for example COM2. Auto-detected if omitted.")
    parser.add_argument("--start", type=int, default=START_ADDRESS, help="First Modbus address.")
    parser.add_argument("--end", type=int, default=END_ADDRESS, help="Last Modbus address.")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="Read timeout per address in seconds.")
    parser.add_argument("--baud", type=int, help="Scan one baud rate only, for example 9600.")
    return parser.parse_args()


def main():
    args = parse_args()
    port = args.port or find_moxa_port()

    if not port:
        _logger.error("MOXA/USB serial port was not found.")
        _logger.info("Check the adapter connection, drivers, and the selected COM port.")
        return 1

    baudrates = [args.baud] if args.baud else None
    results = scan_all_speeds(port, args.start, args.end, args.timeout, baudrates)

    print()
    print("=" * 60)
    print("SCAN RESULTS:")
    print("=" * 60)

    if results:
        for baudrate, addresses in results.items():
            print(f"{baudrate} baud: found addresses {addresses}")
        return 0

    print("No Modbus devices found.")
    print("Check RS-485 A/B wiring, GND, device power, baud/parity settings, and adapter RS-485 mode.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
