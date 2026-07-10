from __future__ import annotations

import pathlib
import queue
import sys
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.bridge_tools import TcpSerialClient
from common.modbus_tools import DEFAULT_BAUDRATES, DEFAULT_PARITIES, DEFAULT_STOPBITS, LogBus, list_serial_ports


class ClientApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TCP Serial Client")
        self.root.geometry("760x520")
        self.log_bus = LogBus()
        self.client: TcpSerialClient | None = None

        self.host_var = tk.StringVar()
        self.tcp_port_var = tk.StringVar(value="9000")
        self.serial_port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUDRATES[3]))
        self.parity_var = tk.StringVar(value="N")
        self.stopbits_var = tk.StringVar(value="1")

        self._build()
        self.refresh_ports()
        self.root.after(100, self._drain_logs)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(frame, text="Настройки", padding=10)
        settings.pack(fill="x")

        ttk.Label(settings, text="Server IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.host_var, width=18).grid(row=0, column=1, sticky="w", padx=(8, 12))

        ttk.Label(settings, text="TCP port").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.tcp_port_var, width=10).grid(row=0, column=3, sticky="w", padx=(8, 12))

        ttk.Label(settings, text="Локальный COM").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.port_box = ttk.Combobox(settings, textvariable=self.serial_port_var, width=18, state="readonly")
        self.port_box.grid(row=1, column=1, sticky="w", padx=(8, 12), pady=(10, 0))
        ttk.Button(settings, text="Обновить", command=self.refresh_ports).grid(row=1, column=2, sticky="w", pady=(10, 0))

        ttk.Label(settings, text="Baud").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(settings, textvariable=self.baud_var, values=[str(item) for item in DEFAULT_BAUDRATES], width=10, state="readonly").grid(row=2, column=1, sticky="w", padx=(8, 12), pady=(10, 0))

        ttk.Label(settings, text="Parity").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Combobox(settings, textvariable=self.parity_var, values=DEFAULT_PARITIES, width=10, state="readonly").grid(row=2, column=3, sticky="w", padx=(8, 12), pady=(10, 0))

        ttk.Label(settings, text="Stop bits").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(settings, textvariable=self.stopbits_var, values=[str(item) for item in DEFAULT_STOPBITS], width=10, state="readonly").grid(row=3, column=1, sticky="w", padx=(8, 12), pady=(10, 0))

        buttons = ttk.Frame(frame, padding=(0, 12, 0, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Старт", command=self.start_client).pack(side="left")
        ttk.Button(buttons, text="Стоп", command=self.stop_client).pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(frame, text="Лог", padding=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_box["values"] = ports
        if ports and self.serial_port_var.get() not in ports:
            self.serial_port_var.set(ports[0])

    def start_client(self) -> None:
        if self.client is not None:
            messagebox.showinfo("Client", "Клиент уже запущен.")
            return
        host = self.host_var.get().strip()
        serial_port = self.serial_port_var.get().strip()
        if not host:
            messagebox.showerror("Ошибка", "Укажите IP адрес сервера.")
            return
        if not serial_port:
            messagebox.showerror("Ошибка", "Выберите локальный COM порт.")
            return
        try:
            self.client = TcpSerialClient(
                host=host,
                tcp_port=int(self.tcp_port_var.get()),
                serial_port=serial_port,
                baudrate=int(self.baud_var.get()),
                parity=self.parity_var.get(),
                stopbits=int(self.stopbits_var.get()),
                log=self.log_bus.write,
            )
            self.client.start()
            self.log_bus.write("Старт клиента.")
        except Exception as exc:
            self.client = None
            messagebox.showerror("Ошибка", str(exc))

    def stop_client(self) -> None:
        if self.client is None:
            return
        self.client.stop()
        self.client = None
        self.log_bus.write("Остановка клиента запрошена.")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_bus.queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.root.after(100, self._drain_logs)

    def _on_close(self) -> None:
        if self.client is not None:
            self.client.stop()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ClientApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
