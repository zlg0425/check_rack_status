"""
本地 shell 终端适配器（基于 pty + subprocess）。
仅在类 Unix 环境可用；Windows 导入时不报错，但实例化会抛出 NotImplementedError。
"""
import os
from typing import Optional

from app.terminal.base import TerminalAdapter

# 仅在非 Windows 下导入 pty/termios 相关模块，避免 Windows 启动时报缺少 termios。
if os.name != "nt":
    import pty
    import subprocess
    import select


class LocalTerminalAdapter(TerminalAdapter):
    def __init__(self, shell: str = "/bin/bash") -> None:
        if os.name == "nt":
            raise NotImplementedError("LocalTerminalAdapter 仅支持类 Unix 环境")
        self.shell = shell
        self.master: Optional[int] = None
        self.slave: Optional[int] = None
        self.proc: Optional["subprocess.Popen"] = None
        self._closed = False

    def start(self, cols: int = 80, rows: int = 24) -> None:
        self.master, self.slave = pty.openpty()
        self.proc = subprocess.Popen(
            [self.shell],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            close_fds=True,
        )
        # pty 大小不在此处设置（需要 termios/ioctl），由上层 resize 调整

    def read(self, size: int = 4096):
        if self._closed or self.master is None:
            return None
        rlist, _, _ = select.select([self.master], [], [], 0.05)
        if not rlist:
            return ""
        try:
            data = os.read(self.master, size)
            if not data:
                return None
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def write(self, data) -> None:
        if self._closed or self.master is None:
            return
        if isinstance(data, str):
            data = data.encode()
        os.write(self.master, data)

    def resize(self, cols: int, rows: int) -> None:
        # 可选：通过 fcntl + termios 设置窗口大小；此处保持简化
        pass

    def close(self) -> None:
        self._closed = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        try:
            if self.master:
                os.close(self.master)
            if self.slave:
                os.close(self.slave)
        except Exception:
            pass

    def is_active(self) -> bool:
        if self._closed:
            return False
        if self.proc:
            return self.proc.poll() is None
        return False

