"""
基于 Paramiko 的 SSH 终端适配器（同步版，稳定首选）。
"""
import time
import paramiko
from typing import Optional

from app.terminal.base import TerminalAdapter
from core.ssh.transport import create_transport


class ParamikoTerminalAdapter(TerminalAdapter):
    def __init__(
        self,
        server_name: str,
        server_ip: str,
        port: int = 22,
        term: str = "xterm-256color",
    ) -> None:
        self.server_name = server_name
        self.server_ip = server_ip
        self.port = port
        self.term = term
        self.transport: Optional[paramiko.Transport] = None
        self.channel: Optional[paramiko.Channel] = None
        self._closed = False

    def start(self, cols: int = 80, rows: int = 24) -> None:
        # 复用已有的 create_transport 以沿用 auth/key 配置与超时设置
        self.transport = create_transport(self.server_name, self.server_ip, self.port)
        self.channel = self.transport.open_session()
        # 申请 PTY 并启动 shell
        self.channel.get_pty(term=self.term, width=cols, height=rows)
        self.channel.invoke_shell()

    def read(self, size: int = 4096):
        if not self.channel:
            return None
        if self.channel.exit_status_ready():
            return None
        try:
            if self.channel.recv_ready():
                data = self.channel.recv(size)
                return data.decode("utf-8", errors="ignore")
            # 没有数据，短暂休眠以降低空转
            time.sleep(0.05)
            return ""
        except Exception:
            return None

    def write(self, data) -> None:
        if not self.channel or self._closed:
            return
        if isinstance(data, str):
            data = data.encode()
        try:
            self.channel.send(data)
        except Exception:
            # 如果写入失败，标记关闭，交由上层清理
            self._closed = True
            raise

    def resize(self, cols: int, rows: int) -> None:
        if not self.channel or self._closed:
            return
        try:
            self.channel.resize_pty(width=cols, height=rows)
        except Exception:
            # 不中断会话，记录但忽略（由上层决定是否继续）
            pass

    def close(self) -> None:
        self._closed = True
        try:
            if self.channel and not self.channel.closed:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.transport and self.transport.is_active():
                self.transport.close()
        except Exception:
            pass

    def is_active(self) -> bool:
        if self._closed:
            return False
        if self.channel:
            try:
                if self.channel.exit_status_ready():
                    return False
            except Exception:
                return False
        return True

