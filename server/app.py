from __future__ import annotations

import pathlib
import queue
import sys

import ttkbootstrap as tb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, W

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.bridge_tools import SerialTcpServer
from common.modbus_tools import DEFAULT_BAUDRATES, DEFAULT_PARITIES, DEFAULT_STOPBITS, LogBus, list_serial_ports


class ServerApp(tb.Window):
    def __init__(self) -> None:
        super().__init__(title="Serial TCP Server", themename="darkly", size=(820, 560))
        self.minsize(760, 480)

        self.log_bus = LogBus()
        self.server: SerialTcpServer | None = None

        self.serial_port_var = tb.StringVar()
        self.baud_var = tb.StringVar(value=str(DEFAULT_BAUDRATES[3]))
        self.parity_var = tb.StringVar(value="N")
        self.stopbits_var = tb.StringVar(value="1")
        self.host_var = tb.StringVar(value="0.0.0.0")
        self.tcp_port_var = tb.StringVar(value="9000")

        self._settings_widgets: list = []

        self._build()
        self.refresh_ports()
        self.after(100, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        pad = {"padx": 12, "pady": 8}

        header = tb.Frame(self)
        header.pack(fill=X, padx=16, pady=(14, 0))
        tb.Label(header, text="Serial → TCP Server", font=("Segoe UI", 16, "bold")).pack(side=LEFT)
        self.status_lbl = tb.Label(header, text="● Остановлен", bootstyle="secondary")
        self.status_lbl.pack(side=RIGHT)

        settings = tb.Labelframe(self, text="Настройки", bootstyle="info")
        settings.pack(fill=X, **pad)

        row1 = tb.Frame(settings)
        row1.pack(fill=X, padx=10, pady=(8, 4))
        tb.Label(row1, text="COM порт:").pack(side=LEFT)
        self.port_box = tb.Combobox(row1, textvariable=self.serial_port_var, width=16, state="readonly")
        self.port_box.pack(side=LEFT, padx=8)
        tb.Button(row1, text="Обновить", bootstyle="secondary-outline", command=self.refresh_ports).pack(side=LEFT)

        tb.Label(row1, text="   Baud:").pack(side=LEFT, padx=(20, 0))
        baud_box = tb.Combobox(row1, textvariable=self.baud_var, values=[str(v) for v in DEFAULT_BAUDRATES], width=10, state="readonly")
        baud_box.pack(side=LEFT, padx=8)

        tb.Label(row1, text="Parity:").pack(side=LEFT, padx=(12, 0))
        parity_box = tb.Combobox(row1, textvariable=self.parity_var, values=DEFAULT_PARITIES, width=6, state="readonly")
        parity_box.pack(side=LEFT, padx=8)

        tb.Label(row1, text="Stop bits:").pack(side=LEFT, padx=(12, 0))
        stop_box = tb.Combobox(row1, textvariable=self.stopbits_var, values=[str(v) for v in DEFAULT_STOPBITS], width=6, state="readonly")
        stop_box.pack(side=LEFT, padx=8)

        row2 = tb.Frame(settings)
        row2.pack(fill=X, padx=10, pady=(4, 10))
        tb.Label(row2, text="Listen IP:").pack(side=LEFT)
        host_entry = tb.Entry(row2, textvariable=self.host_var, width=16)
        host_entry.pack(side=LEFT, padx=8)
        tb.Label(row2, text="TCP порт:").pack(side=LEFT, padx=(12, 0))
        tcp_entry = tb.Entry(row2, textvariable=self.tcp_port_var, width=8)
        tcp_entry.pack(side=LEFT, padx=8)

        self.toggle_btn = tb.Button(row2, text="Старт", bootstyle="success", width=12, command=self._toggle)
        self.toggle_btn.pack(side=RIGHT)

        self._settings_widgets = [self.port_box, baud_box, parity_box, stop_box, host_entry, tcp_entry]

        log_frame = tb.Labelframe(self, text="Журнал", bootstyle="secondary")
        log_frame.pack(fill=BOTH, expand=True, **pad)
        self.log_text = tb.ScrolledText(log_frame, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill=BOTH, expand=True, padx=6, pady=6)

    # ------------------------------------------------------------- helpers --
    def refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_box["values"] = ports
        if ports and self.serial_port_var.get() not in ports:
            self.serial_port_var.set(ports[0])

    def _set_settings_state(self, enabled: bool) -> None:
        combo_state = "readonly" if enabled else "disabled"
        entry_state = "normal" if enabled else "disabled"
        for widget in self._settings_widgets:
            widget.configure(state=entry_state if isinstance(widget, tb.Entry) else combo_state)

    def _toggle(self) -> None:
        if self.server is None:
            self.start_server()
        else:
            self.stop_server()

    def start_server(self) -> None:
        serial_port = self.serial_port_var.get().strip()
        if not serial_port:
            tb.dialogs.Messagebox.show_error("Выберите COM порт.", "Ошибка")
            return
        try:
            self.server = SerialTcpServer(
                serial_port=serial_port,
                baudrate=int(self.baud_var.get()),
                parity=self.parity_var.get(),
                stopbits=int(self.stopbits_var.get()),
                host=self.host_var.get().strip(),
                tcp_port=int(self.tcp_port_var.get()),
                log=self.log_bus.write,
            )
            self.server.start()
        except Exception as exc:
            self.server = None
            tb.dialogs.Messagebox.show_error(str(exc), "Ошибка")
            return

        self._set_settings_state(False)
        self.toggle_btn.configure(text="Стоп", bootstyle="danger")
        self.status_lbl.configure(
            text=f"● Запущен: {self.host_var.get()}:{self.tcp_port_var.get()}",
            bootstyle="success",
        )
        self.log_bus.write("Старт сервера.")

    def stop_server(self) -> None:
        if self.server is None:
            return
        self.server.stop()
        self.server = None
        self._set_settings_state(True)
        self.toggle_btn.configure(text="Старт", bootstyle="success")
        self.status_lbl.configure(text="● Остановлен", bootstyle="secondary")
        self.log_bus.write("Остановка сервера запрошена.")

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
        if self.server is not None:
            self.server.stop()
        self.destroy()


def main() -> int:
    ServerApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
