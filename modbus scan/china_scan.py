import argparse
import logging
import sys
import time

import serial
import serial.tools.list_ports


#python china_scan.py --port COM2 --baud 9600
#python china_scan.py --port COM2 --deep
#python china_scan.py --port COM2 --baud 9600 --parity N --deep --registers 0-32
#python china_scan.py --port COM2

BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
PARITIES = ["N", "E", "O"]
STOPBITS = 1
BYTESIZE = 8
TIMEOUT = 0.08
FAST_TIMEOUT = 0.04

START_ADDRESS = 1
END_ADDRESS = 247

READ_PROBES = [
    (0x01, "read coils"),
    (0x03, "read holding registers"),
]

DEEP_READ_PROBES = [
    (0x01, "read coils"),
    (0x02, "read discrete inputs"),
    (0x03, "read holding registers"),
    (0x04, "read input registers"),
]

FAST_REGISTERS = [0]

COMMON_REGISTERS = [
    0,
    1,
    2,
    3,
    4,
    8,
    16,
    32,
    64,
    100,
    255,
    256,
    512,
    1000,
    4096,
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def calculate_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def add_crc(frame):
    crc = calculate_crc(frame)
    frame.extend([crc & 0xFF, (crc >> 8) & 0xFF])
    return frame


def valid_crc(frame):
    if len(frame) < 4:
        return False

    received_crc = frame[-2] | (frame[-1] << 8)
    return calculate_crc(frame[:-2]) == received_crc


def build_read_request(address, function_code, register, count):
    return add_crc(
        bytearray(
            [
                address,
                function_code,
                (register >> 8) & 0xFF,
                register & 0xFF,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ]
        )
    )


def expected_response_size(header):
    if len(header) < 2:
        return 0

    if header[1] & 0x80:
        return 5

    if len(header) < 3:
        return 0

    return 3 + header[2] + 2


def read_response(ser):
    header = ser.read(3)
    if not header:
        return b""

    size = expected_response_size(header)
    if size <= len(header):
        return bytes(header)

    return bytes(header + ser.read(size - len(header)))


def find_serial_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None

    logger.info("Available serial ports:")
    for port in ports:
        logger.info("  %s: %s", port.device, port.description)

    for port in ports:
        text = f"{port.description} {port.hwid}".upper()
        if any(marker in text for marker in ("MOXA", "UPORT", "USB SERIAL PORT")):
            return port.device

    return ports[0].device


def open_serial_port(port, baudrate, parity, timeout):
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=BYTESIZE,
        parity=parity,
        stopbits=STOPBITS,
        timeout=timeout,
        write_timeout=timeout,
    )


def probe_address(ser, address, registers, probes):
    for register in registers:
        for function_code, function_name in probes:
            count = 8 if function_code in (0x01, 0x02) else 1
            request = build_read_request(address, function_code, register, count)

            ser.reset_input_buffer()
            ser.write(request)
            ser.flush()
            time.sleep(0.001)

            response = read_response(ser)
            if not response or response[0] != address or not valid_crc(response):
                continue

            if response[1] & 0x80:
                logger.info(
                    "Address %s answered with exception to %s at register %s: %s",
                    address,
                    function_name,
                    register,
                    response.hex(),
                )
            else:
                logger.info(
                    "Address %s answered to %s at register %s: %s",
                    address,
                    function_name,
                    register,
                    response.hex(),
                )

            return {
                "address": address,
                "function": function_code,
                "function_name": function_name,
                "register": register,
                "response": response.hex(),
            }

    return None


def scan_addresses(port, baudrate, parity, start_address, end_address, timeout, registers, probes):
    found = []

    try:
        with open_serial_port(port, baudrate, parity, timeout) as ser:
            logger.info(
                "Scanning addresses %s-%s at %s baud, parity %s",
                start_address,
                end_address,
                baudrate,
                parity,
            )

            for address in range(start_address, end_address + 1):
                result = probe_address(ser, address, registers, probes)
                if result:
                    found.append(result)

    except serial.SerialException as exc:
        logger.error("Could not scan %s at %s/%s: %s", port, baudrate, parity, exc)

    return found


def scan(port, baudrates, parities, start_address, end_address, timeout, registers, probes):
    results = []

    for baudrate in baudrates:
        for parity in parities:
            found = scan_addresses(
                port=port,
                baudrate=baudrate,
                parity=parity,
                start_address=start_address,
                end_address=end_address,
                timeout=timeout,
                registers=registers,
                probes=probes,
            )
            for item in found:
                item["baudrate"] = baudrate
                item["parity"] = parity
            results.extend(found)

    return results


def parse_registers(value, deep):
    if not value:
        return COMMON_REGISTERS if deep else FAST_REGISTERS

    registers = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            registers.extend(range(int(start, 0), int(end, 0) + 1))
        else:
            registers.append(int(part, 0))
    return sorted(set(registers))


def parse_args():
    parser = argparse.ArgumentParser(description="Unknown Modbus RTU module scanner.")
    parser.add_argument("--port", help="Serial port, for example COM2. Auto-detected if omitted.")
    parser.add_argument("--baud", type=int, help="Scan one baud rate only.")
    parser.add_argument("--parity", choices=PARITIES, help="Scan one parity only: N, E, or O.")
    parser.add_argument("--start", type=int, default=START_ADDRESS, help="First Modbus address.")
    parser.add_argument("--end", type=int, default=END_ADDRESS, help="Last Modbus address.")
    parser.add_argument("--timeout", type=float, help="Read timeout per request in seconds.")
    parser.add_argument("--deep", action="store_true", help="Probe more registers and Modbus functions.")
    parser.add_argument(
        "--registers",
        help="Registers to probe, for example '0,1,100,0x100' or '0-32'.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    port = args.port or find_serial_port()

    if not port:
        logger.error("No serial ports found.")
        return 1

    baudrates = [args.baud] if args.baud else BAUDRATES
    parities = [args.parity] if args.parity else PARITIES
    registers = parse_registers(args.registers, args.deep)
    probes = DEEP_READ_PROBES if args.deep else READ_PROBES
    timeout = args.timeout if args.timeout is not None else (TIMEOUT if args.deep else FAST_TIMEOUT)

    logger.info("Port: %s", port)
    logger.info("Mode: %s", "deep" if args.deep else "fast")
    logger.info("Registers: %s", registers)

    results = scan(
        port=port,
        baudrates=baudrates,
        parities=parities,
        start_address=args.start,
        end_address=args.end,
        timeout=timeout,
        registers=registers,
        probes=probes,
    )

    print()
    print("SCAN RESULTS")
    print("=" * 60)

    if not results:
        print("No Modbus responses found.")
        return 2

    for item in results:
        print(
            f"{item['baudrate']} baud, parity {item['parity']}, "
            f"address {item['address']}, {item['function_name']}, "
            f"register {item['register']}, response {item['response']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
