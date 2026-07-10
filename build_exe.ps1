$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed --name modbus_scanner "modbus scan\\app.py"
pyinstaller --noconfirm --onefile --windowed --name serial_tcp_server "server\\app.py"
pyinstaller --noconfirm --onefile --windowed --name tcp_serial_client "client\\app.py"
