from __future__ import annotations

import select
import socket
import threading
import time
from typing import Callable

import serial


def serial_open(port: str, baudrate: int, parity: str, stopbits: int, timeout: float) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=8,
        parity=parity,
        stopbits=stopbits,
        timeout=timeout,
        write_timeout=timeout,
    )


class SerialTcpServer:
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        host: str,
        tcp_port: int,
        log: Callable[[str], None],
    ) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.host = host
        self.tcp_port = tcp_port
        self.log = log
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        server_socket: socket.socket | None = None
        client_socket: socket.socket | None = None
        serial_handle: serial.Serial | None = None
        try:
            serial_handle = serial_open(
                self.serial_port,
                self.baudrate,
                self.parity,
                self.stopbits,
                timeout=0.1,
            )
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((self.host, self.tcp_port))
            server_socket.listen(1)
            server_socket.settimeout(0.5)
            self.log(f"Сервер слушает {self.host}:{self.tcp_port}")
            self.log(f"Открыт COM {self.serial_port} {self.baudrate}/{self.parity}/{self.stopbits}")

            while not self.stop_event.is_set():
                if client_socket is None:
                    try:
                        client_socket, address = server_socket.accept()
                        client_socket.setblocking(False)
                        self.log(f"Клиент подключен: {address[0]}:{address[1]}")
                    except socket.timeout:
                        continue
                try:
                    self._pump(serial_handle, client_socket)
                except (ConnectionResetError, OSError):
                    if client_socket:
                        try:
                            client_socket.close()
                        except OSError:
                            pass
                    client_socket = None
                    self.log("Ожидание нового клиента.")
        except Exception as exc:
            self.log(f"Ошибка сервера: {exc}")
        finally:
            if client_socket:
                try:
                    client_socket.close()
                except OSError:
                    pass
            if server_socket:
                try:
                    server_socket.close()
                except OSError:
                    pass
            if serial_handle:
                try:
                    serial_handle.close()
                except serial.SerialException:
                    pass
            self.log("Сервер остановлен.")

    def _pump(self, serial_handle: serial.Serial, client_socket: socket.socket) -> None:
        try:
            data = serial_handle.read(serial_handle.in_waiting or 1)
            if data:
                client_socket.sendall(data)
        except serial.SerialException as exc:
            self.log(f"Ошибка чтения COM: {exc}")
            self.stop_event.set()
            return
        except OSError as exc:
            self.log(f"Ошибка отправки клиенту: {exc}")
            raise

        try:
            readable, _, exceptional = select.select([client_socket], [], [client_socket], 0.05)
            if exceptional:
                raise OSError("TCP client socket exception")
            if readable:
                packet = client_socket.recv(4096)
                if not packet:
                    self.log("Клиент отключился.")
                    raise ConnectionResetError("client disconnected")
                serial_handle.write(packet)
                serial_handle.flush()
        except ConnectionResetError:
            raise
        except OSError as exc:
            self.log(f"Ошибка приема от клиента: {exc}")
            raise


class TcpSerialClient:
    def __init__(
        self,
        host: str,
        tcp_port: int,
        serial_port: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        log: Callable[[str], None],
    ) -> None:
        self.host = host
        self.tcp_port = tcp_port
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.log = log
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            serial_handle: serial.Serial | None = None
            sock: socket.socket | None = None
            try:
                serial_handle = serial_open(
                    self.serial_port,
                    self.baudrate,
                    self.parity,
                    self.stopbits,
                    timeout=0.1,
                )
                self.log(f"Открыт локальный COM {self.serial_port} {self.baudrate}/{self.parity}/{self.stopbits}")
                sock = socket.create_connection((self.host, self.tcp_port), timeout=5)
                sock.setblocking(False)
                self.log(f"Подключено к серверу {self.host}:{self.tcp_port}")

                while not self.stop_event.is_set():
                    try:
                        data = serial_handle.read(serial_handle.in_waiting or 1)
                        if data:
                            sock.sendall(data)
                    except serial.SerialException as exc:
                        self.log(f"Ошибка чтения локального COM: {exc}")
                        self.stop_event.set()
                        break

                    readable, _, exceptional = select.select([sock], [], [sock], 0.05)
                    if exceptional:
                        raise OSError("TCP socket exception")
                    if readable:
                        packet = sock.recv(4096)
                        if not packet:
                            raise ConnectionResetError("server disconnected")
                        serial_handle.write(packet)
                        serial_handle.flush()
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.log(f"Соединение потеряно: {exc}")
                time.sleep(2)
            finally:
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                if serial_handle:
                    try:
                        serial_handle.close()
                    except serial.SerialException:
                        pass
        self.log("Клиент остановлен.")
