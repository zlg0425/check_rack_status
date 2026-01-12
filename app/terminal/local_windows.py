"""
Windows 本地 PowerShell 终端适配器。

使用场景：
    - 在 Windows 开发/调试时，需要提供一个本地 shell 终端。
    - 与前端的 start → write → read → resize → close 接口一致。
"""
import subprocess
import threading
from typing import Optional

from app.terminal.base import TerminalAdapter


class LocalPowerShellAdapter(TerminalAdapter):
    def __init__(self, shell: str = "powershell.exe") -> None:
        self.shell = shell
        self.proc: Optional[subprocess.Popen] = None
        self._closed = False

    def start(self, cols: int = 120, rows: int = 32) -> None:
        # 创建无缓冲的子进程，stdout/stderr 合并，便于单通道读取
        self.proc = subprocess.Popen(
            [self.shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,  # 避免弹窗
        )

        def reader():
            try:
                if not self.proc or not self.proc.stdout:
                    return
                for chunk in iter(lambda: self.proc.stdout.readline(), b""):
                    if not chunk:
                        break
                    try:
                        # PowerShell 输出默认 UTF-8（新版），降级忽略错误
                        self.emit(chunk.decode("utf-8", errors="ignore"))
                    except Exception:
                        break
            finally:
                try:
                    self.emit("")  # 通知前端刷新
                except Exception:
                    pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()

    def read(self, size: int = 4096):
        # 使用线程式读取，不在此同步读取，返回空字符串即可
        return ""

    def write(self, data) -> None:
        if not self.proc or not self.proc.stdin or self._closed:
            return
        if isinstance(data, str):
            data = data.encode()
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except Exception:
            self._closed = True
            raise

    def resize(self, cols: int, rows: int) -> None:
        # PowerShell 不易直接调整终端尺寸，忽略
        return

    def close(self) -> None:
        self._closed = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def is_active(self) -> bool:
        if self._closed:
            return False
        if self.proc:
            return self.proc.poll() is None
        return False

