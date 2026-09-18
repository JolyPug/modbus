from __future__ import annotations

import pathlib
import queue
import sys
import threading

import ttkbootstrap as tb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, W

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.modbus_tools import DEFAULT_BAUDRATES, DEFAULT_FUNCTIONS, DEFAULT_PARITIES, DEFAULT_STOPBITS, LogBus, ScanConfig, ScanHit, list_serial_ports, parse_number_list, scan_modbus


class ScannerApp(tb.Window):
    def __init__(self) -> None:
        super().__init__(title="Modbus Scanner", themename="darkly", size=(1020, 760))
        self.minsize(900, 640)

        self.log_bus = LogBus()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.port_var = tb.StringVar()
        self.address_start_var = tb.StringVar(value="1")
        self.address_end_var = tb.StringVar(value="247")
        self.registers_var = tb.StringVar(value="0-32")
        self.timeout_var = tb.StringVar(value="0.08")

        self.baud_vars = {value: tb.BooleanVar(value=True) for value in DEFAULT_BAUDRATES}
        self.parity_vars = {value: tb.BooleanVar(value=True) for value in DEFAULT_PARITIES}
        self.stopbits_vars = {value: tb.BooleanVar(value=(value == 1)) for value in DEFAULT_STOPBITS}
        self.function_vars = {value: tb.BooleanVar(value=True) for value in DEFAULT_FUNCTIONS}

        self._settings_widgets: list = []
        self._hit_count = 0

        self._build()
        self.refresh_ports()
        self.after(100, self._drain_logs)
        self.after(200, self._watch_worker)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        pad = {"padx": 12, "pady": 8}

        header = tb.Frame(self)
        header.pack(fill=X, padx=16, pady=(14, 0))
        tb.Label(header, text="Modbus RTU Scanner", font=("Segoe UI", 16, "bold")).pack(side=LEFT)
        self.status_lbl = tb.Label(header, text="● Простой", bootstyle="secondary")
        self.status_lbl.pack(side=RIGHT)

        settings = tb.Labelframe(self, text="Настройки", bootstyle="info")
        settings.pack(fill=X, **pad)

        row1 = tb.Frame(settings)
        row1.pack(fill=X, padx=10, pady=(8, 4))
        tb.Label(row1, text="COM порт:").pack(side=LEFT)
        self.port_box = tb.Combobox(row1, textvariable=self.port_var, width=16, state="readonly")
        self.port_box.pack(side=LEFT, padx=8)
        tb.Button(row1, text="Обновить", bootstyle="secondary-outline", command=self.refresh_ports).pack(side=LEFT)

        tb.Label(row1, text="   Адреса:").pack(side=LEFT, padx=(20, 0))
        addr_start = tb.Entry(row1, textvariable=self.address_start_var, width=6)
        addr_start.pack(side=LEFT, padx=(8, 2))
        tb.Label(row1, text="—").pack(side=LEFT)
        addr_end = tb.Entry(row1, textvariable=self.address_end_var, width=6)
        addr_end.pack(side=LEFT, padx=(2, 0))

        tb.Label(row1, text="   Timeout, с:").pack(side=LEFT, padx=(20, 0))
        timeout_entry = tb.Entry(row1, textvariable=self.timeout_var, width=8)
        timeout_entry.pack(side=LEFT, padx=8)

        row2 = tb.Frame(settings)
        row2.pack(fill=X, padx=10, pady=(4, 10))
        tb.Label(row2, text="Регистры:").pack(side=LEFT)
        registers_entry = tb.Entry(row2, textvariable=self.registers_var, width=40)
        registers_entry.pack(side=LEFT, padx=8)
        tb.Label(row2, text="напр. 0-32 или 0,1,2,100,256", bootstyle="secondary").pack(side=LEFT)

        options = tb.Labelframe(self, text="Перебор параметров", bootstyle="warning")
        options.pack(fill=X, **pad)

        def chip_row(parent, label, values, var_map):
            row = tb.Frame(parent)
            row.pack(fill=X, padx=10, pady=6)
            tb.Label(row, text=label, width=12, bootstyle="secondary").pack(side=LEFT)
            for value in values:
                tb.Checkbutton(
                    row, text=str(value), variable=var_map[value],
                    bootstyle="info-toolbutton",
                ).pack(side=LEFT, padx=(0, 6))

        chip_row(options, "Скорости", DEFAULT_BAUDRATES, self.baud_vars)
        chip_row(options, "Parity", DEFAULT_PARITIES, self.parity_vars)
        chip_row(options, "Stop bits", DEFAULT_STOPBITS, self.stopbits_vars)
        chip_row(options, "Функции", DEFAULT_FUNCTIONS, self.function_vars)

        self._settings_widgets = [self.port_box, addr_start, addr_end, timeout_entry, registers_entry]

        action_row = tb.Frame(self)
        action_row.pack(fill=X, padx=12, pady=(0, 4))
        self.toggle_btn = tb.Button(action_row, text="Старт", bootstyle="success", width=14, command=self._toggle)
        self.toggle_btn.pack(side=LEFT)
        self.progress = tb.Progressbar(action_row, bootstyle="info-striped", mode="indeterminate")
        self.progress.pack(side=LEFT, fill=X, expand=True, padx=12)
        self.hits_lbl = tb.Label(action_row, text="Найдено: 0", bootstyle="secondary")
        self.hits_lbl.pack(side=RIGHT)

        results = tb.Labelframe(self, text="Найдено", bootstyle="success")
        results.pack(fill=BOTH, expand=True, padx=12, pady=8)
        columns = ("baud", "parity", "stopbits", "address", "function", "register", "response")
        self.tree = tb.Treeview(results, columns=columns, show="headings", height=10, bootstyle="success")
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
        self.tree.pack(fill=BOTH, expand=True, padx=6, pady=6)

        log_frame = tb.Labelframe(self, text="Журнал", bootstyle="secondary")
        log_frame.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        self.log_text = tb.ScrolledText(log_frame, height=8, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill=BOTH, expand=True, padx=6, pady=6)

    # ------------------------------------------------------------- helpers --
    def refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def _set_settings_state(self, enabled: bool) -> None:
        combo_state = "readonly" if enabled else "disabled"
        entry_state = "normal" if enabled else "disabled"
        for widget in self._settings_widgets:
            widget.configure(state=entry_state if isinstance(widget, tb.Entry) else combo_state)

    def _toggle(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_scan()
        else:
            self.start_scan()

    def start_scan(self) -> None:
        try:
            config = self._build_config()
        except Exception as exc:
            tb.dialogs.Messagebox.show_error(str(exc), "Ошибка")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.log_text.delete("1.0", "end")
        self._hit_count = 0
        self.hits_lbl.configure(text="Найдено: 0")
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._scan_worker, args=(config,), daemon=True)
        self.worker.start()

        self._set_settings_state(False)
        self.toggle_btn.configure(text="Стоп", bootstyle="danger")
        self.status_lbl.configure(text="● Сканирование", bootstyle="warning")
        self.progress.start(12)
        self.log_bus.write("Старт сканирования.")

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.toggle_btn.configure(state="disabled")
        self.log_bus.write("Запрошена остановка.")

    def _watch_worker(self) -> None:
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            self._set_settings_state(True)
            self.toggle_btn.configure(text="Старт", bootstyle="success", state="normal")
            self.status_lbl.configure(text="● Простой", bootstyle="secondary")
            self.progress.stop()
        self.after(200, self._watch_worker)

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
        self._hit_count += 1

        def insert():
            self.tree.insert(
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
                tags=("exception",) if hit.is_exception else (),
            )
            self.hits_lbl.configure(text=f"Найдено: {self._hit_count}")

        self.tree.tag_configure("exception", foreground="#f0ad4e")
        self.after(0, insert)

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_bus.queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.after(100, self._drain_logs)

    def _on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> int:
    ScannerApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
