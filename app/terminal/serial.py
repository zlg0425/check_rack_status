"""
串口终端适配器，适用于 /dev/tty* 或 /dev/powerctl。
"""
import time
from typing import Optional

from app.terminal.base import TerminalAdapter

try:
    import serial  # type: ignore
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    serial = None  # type: ignore


class SerialTerminalAdapter(TerminalAdapter):
    def __init__(self, device: str = "/dev/powerctl", baudrate: int = 115200, timeout: float = 0.1) -> None:
        if not SERIAL_AVAILABLE:
            raise ImportError("未安装 pyserial，无法使用串口终端")
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None
        self._closed = False

    def start(self, cols: int = 80, rows: int = 24) -> None:
        self.ser = serial.Serial(self.device, self.baudrate, timeout=self.timeout)

    def read(self, size: int = 4096):
        if not self.ser or self._closed:
            return None
        try:
            data = self.ser.read(size)
            if not data:
                time.sleep(0.05)
                return ""
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def write(self, data) -> None:
        if not self.ser or self._closed:
            return
        if isinstance(data, str):
            data = data.encode()
        self.ser.write(data)

    def resize(self, cols: int, rows: int) -> None:
        # 串口无尺寸概念，忽略
        pass

    def close(self) -> None:
        self._closed = True
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass

    def is_active(self) -> bool:
        if self._closed:
            return False
        if self.ser:
            return self.ser.is_open
        return False

