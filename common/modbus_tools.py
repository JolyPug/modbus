from __future__ import annotations

import dataclasses
import queue
import threading
import time
from typing import Callable

import serial
import serial.tools.list_ports


DEFAULT_BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
DEFAULT_PARITIES = ["N", "E", "O"]
DEFAULT_STOPBITS = [1, 2]
DEFAULT_FUNCTIONS = [1, 2, 3, 4]


def list_serial_ports() -> list[str]:
    return [port.device for port in serial.tools.list_ports.comports()]


def calculate_crc(data: bytes | bytearray) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def add_crc(frame: bytearray) -> bytearray:
    crc = calculate_crc(frame)
    frame.extend([crc & 0xFF, (crc >> 8) & 0xFF])
    return frame


def valid_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    received = frame[-2] | (frame[-1] << 8)
    return calculate_crc(frame[:-2]) == received


def build_read_request(address: int, function_code: int, register: int, count: int) -> bytes:
    return bytes(
        add_crc(
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
    )


def expected_response_size(header: bytes) -> int:
    if len(header) < 2:
        return 0
    if header[1] & 0x80:
        return 5
    if len(header) < 3:
        return 0
    return 3 + header[2] + 2


def read_response(ser: serial.Serial) -> bytes:
    header = ser.read(3)
    if not header:
        return b""
    size = expected_response_size(header)
    if size <= len(header):
        return bytes(header)
    return bytes(header + ser.read(size - len(header)))


def parse_number_list(value: str) -> list[int]:
    numbers: list[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text, 0)
            end = int(end_text, 0)
            step = 1 if end >= start else -1
            numbers.extend(range(start, end + step, step))
        else:
            numbers.append(int(item, 0))
    return sorted(set(numbers))


@dataclasses.dataclass(slots=True)
class ScanHit:
    baudrate: int
    parity: str
    stopbits: int
    address: int
    function_code: int
    register: int
    response_hex: str
    is_exception: bool


@dataclasses.dataclass(slots=True)
class ScanConfig:
    port: str
    baudrates: list[int]
    parities: list[str]
    stopbits: list[int]
    addresses: list[int]
    registers: list[int]
    functions: list[int]
    timeout: float
    request_pause: float = 0.002


def scan_modbus(
    config: ScanConfig,
    stop_event: threading.Event,
    log: Callable[[str], None],
    on_hit: Callable[[ScanHit], None],
) -> None:
    for baudrate in config.baudrates:
        for parity in config.parities:
            for stopbits in config.stopbits:
                if stop_event.is_set():
                    log("Сканирование остановлено.")
                    return
                log(f"Открываю {config.port}: {baudrate} baud, parity {parity}, stopbits {stopbits}")
                try:
                    with serial.Serial(
                        port=config.port,
                        baudrate=baudrate,
                        bytesize=8,
                        parity=parity,
                        stopbits=stopbits,
                        timeout=config.timeout,
                        write_timeout=config.timeout,
                    ) as ser:
                        for address in config.addresses:
                            if stop_event.is_set():
                                log("Сканирование остановлено.")
                                return
                            for function_code in config.functions:
                                count = 8 if function_code in (1, 2) else 1
                                for register in config.registers:
                                    if stop_event.is_set():
                                        log("Сканирование остановлено.")
                                        return
                                    request = build_read_request(address, function_code, register, count)
                                    ser.reset_input_buffer()
                                    ser.reset_output_buffer()
                                    ser.write(request)
                                    ser.flush()
                                    time.sleep(config.request_pause)
                                    response = read_response(ser)
                                    if not response:
                                        continue
                                    if response[0] != address:
                                        continue
                                    if not valid_crc(response):
                                        continue
                                    hit = ScanHit(
                                        baudrate=baudrate,
                                        parity=parity,
                                        stopbits=stopbits,
                                        address=address,
                                        function_code=function_code,
                                        register=register,
                                        response_hex=response.hex(),
                                        is_exception=bool(response[1] & 0x80),
                                    )
                                    on_hit(hit)
                except serial.SerialException as exc:
                    log(f"Ошибка порта {config.port} на {baudrate}/{parity}/{stopbits}: {exc}")


class LogBus:
    def __init__(self) -> None:
        self.queue: queue.Queue[str] = queue.Queue()

    def write(self, message: str) -> None:
        self.queue.put(message)

