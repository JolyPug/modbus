from __future__ import annotations

import pathlib
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.modbus_tools import DEFAULT_BAUDRATES, DEFAULT_FUNCTIONS, DEFAULT_PARITIES, DEFAULT_STOPBITS, LogBus, ScanConfig, ScanHit, list_serial_ports, parse_number_list, scan_modbus


class ScannerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Modbus Scanner")
        self.root.geometry("980x700")
        self.log_bus = LogBus()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.port_var = tk.StringVar()
        self.address_start_var = tk.StringVar(value="1")
        self.address_end_var = tk.StringVar(value="247")
        self.registers_var = tk.StringVar(value="0-32")
        self.timeout_var = tk.StringVar(value="0.08")

        self.baud_vars = {value: tk.BooleanVar(value=True) for value in DEFAULT_BAUDRATES}
        self.parity_vars = {value: tk.BooleanVar(value=True) for value in DEFAULT_PARITIES}
        self.stopbits_vars = {value: tk.BooleanVar(value=(value == 1)) for value in DEFAULT_STOPBITS}
        self.function_vars = {value: tk.BooleanVar(value=True) for value in DEFAULT_FUNCTIONS}

        self._build()
        self.refresh_ports()
        self.root.after(100, self._drain_logs)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="both", expand=True)

        controls = ttk.LabelFrame(top, text="Настройки", padding=10)
        controls.pack(fill="x")

        ttk.Label(controls, text="COM порт").grid(row=0, column=0, sticky="w")
        self.port_box = ttk.Combobox(controls, textvariable=self.port_var, width=18, state="readonly")
        self.port_box.grid(row=0, column=1, sticky="w", padx=(8, 12))
        ttk.Button(controls, text="Обновить", command=self.refresh_ports).grid(row=0, column=2, sticky="w")

        ttk.Label(controls, text="Адреса").grid(row=0, column=3, sticky="w", padx=(16, 0))
        ttk.Entry(controls, textvariable=self.address_start_var, width=8).grid(row=0, column=4, sticky="w", padx=(8, 4))
        ttk.Entry(controls, textvariable=self.address_end_var, width=8).grid(row=0, column=5, sticky="w")

        ttk.Label(controls, text="Регистры").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.registers_var, width=32).grid(row=1, column=1, columnspan=2, sticky="we", padx=(8, 12), pady=(10, 0))

        ttk.Label(controls, text="Timeout").grid(row=1, column=3, sticky="w", padx=(16, 0), pady=(10, 0))
        ttk.Entry(controls, textvariable=self.timeout_var, width=8).grid(row=1, column=4, sticky="w", padx=(8, 0), pady=(10, 0))

        ttk.Label(controls, text="Скорости").grid(row=2, column=0, sticky="nw", pady=(10, 0))
        baud_frame = ttk.Frame(controls)
        baud_frame.grid(row=2, column=1, columnspan=5, sticky="w", pady=(10, 0))
        for index, value in enumerate(DEFAULT_BAUDRATES):
            ttk.Checkbutton(baud_frame, text=str(value), variable=self.baud_vars[value]).grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 10))

        ttk.Label(controls, text="Parity").grid(row=3, column=0, sticky="w", pady=(10, 0))
        parity_frame = ttk.Frame(controls)
        parity_frame.grid(row=3, column=1, sticky="w", pady=(10, 0))
        for index, value in enumerate(DEFAULT_PARITIES):
            ttk.Checkbutton(parity_frame, text=value, variable=self.parity_vars[value]).grid(row=0, column=index, sticky="w", padx=(0, 10))

        ttk.Label(controls, text="Stop bits").grid(row=3, column=3, sticky="w", pady=(10, 0))
        stop_frame = ttk.Frame(controls)
        stop_frame.grid(row=3, column=4, columnspan=2, sticky="w", pady=(10, 0))
        for index, value in enumerate(DEFAULT_STOPBITS):
            ttk.Checkbutton(stop_frame, text=str(value), variable=self.stopbits_vars[value]).grid(row=0, column=index, sticky="w", padx=(0, 10))

        ttk.Label(controls, text="Функции").grid(row=4, column=0, sticky="w", pady=(10, 0))
        function_frame = ttk.Frame(controls)
        function_frame.grid(row=4, column=1, columnspan=5, sticky="w", pady=(10, 0))
        for index, value in enumerate(DEFAULT_FUNCTIONS):
            ttk.Checkbutton(function_frame, text=str(value), variable=self.function_vars[value]).grid(row=0, column=index, sticky="w", padx=(0, 10))

        buttons = ttk.Frame(top, padding=(0, 12, 0, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Старт", command=self.start_scan).pack(side="left")
        ttk.Button(buttons, text="Стоп", command=self.stop_scan).pack(side="left", padx=(8, 0))

        results = ttk.LabelFrame(top, text="Найдено", padding=10)
        results.pack(fill="both", expand=True)
        columns = ("baud", "parity", "stopbits", "address", "function", "register", "response")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=12)
        headings = {
            "baud": "Baud",
            "parity": "Parity",
            "stopbits": "Stop",
            "address": "Addr",
            "function": "Func",
            "register": "Reg",
            "response": "Response",
        }
        widths = {"baud": 90, "parity": 60, "stopbits": 60, "address": 60, "function": 60, "register": 90, "response": 420}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.pack(fill="both", expand=True)

        log_frame = ttk.LabelFrame(top, text="Лог", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = tk.Text(log_frame, height=12, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Сканер", "Сканирование уже идет.")
            return
        try:
            config = self._build_config()
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.log_text.delete("1.0", "end")
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._scan_worker, args=(config,), daemon=True)
        self.worker.start()
        self.log_bus.write("Старт сканирования.")

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.log_bus.write("Запрошена остановка.")

    def _build_config(self) -> ScanConfig:
        port = self.port_var.get().strip()
        if not port:
            raise ValueError("Выберите COM порт.")
        baudrates = [value for value, var in self.baud_vars.items() if var.get()]
        parities = [value for value, var in self.parity_vars.items() if var.get()]
        stopbits = [value for value, var in self.stopbits_vars.items() if var.get()]
        functions = [value for value, var in self.function_vars.items() if var.get()]
        if not baudrates or not parities or not stopbits or not functions:
            raise ValueError("Нужно выбрать хотя бы один вариант в каждом блоке.")
        start_address = int(self.address_start_var.get())
        end_address = int(self.address_end_var.get())
        if start_address < 1 or end_address > 247 or start_address > end_address:
            raise ValueError("Диапазон адресов должен быть в пределах 1..247.")
        registers = parse_number_list(self.registers_var.get())
        if not registers:
            raise ValueError("Укажите хотя бы один регистр.")
        timeout = float(self.timeout_var.get())
        return ScanConfig(
            port=port,
            baudrates=baudrates,
            parities=parities,
            stopbits=stopbits,
            addresses=list(range(start_address, end_address + 1)),
            registers=registers,
            functions=functions,
            timeout=timeout,
        )

    def _scan_worker(self, config: ScanConfig) -> None:
        scan_modbus(config, self.stop_event, self.log_bus.write, self._on_hit)
        self.log_bus.write("Сканирование завершено.")

    def _on_hit(self, hit: ScanHit) -> None:
        marker = "EXC" if hit.is_exception else "OK"
        self.log_bus.write(
            f"{marker}: {hit.baudrate}/{hit.parity}/{hit.stopbits} addr={hit.address} func={hit.function_code} reg={hit.register} {hit.response_hex}"
        )
        self.root.after(
            0,
            lambda: self.tree.insert(
                "",
                "end",
                values=(
                    hit.baudrate,
                    hit.parity,
                    hit.stopbits,
                    hit.address,
                    hit.function_code,
                    hit.register,
                    hit.response_hex,
                ),
            ),
        )

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
        self.stop_event.set()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ScannerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
