"""
终端适配器基类，定义统一接口，便于扩展 SSH / 本地 shell / 串口等实现。
"""
from abc import ABC, abstractmethod
from typing import Optional


class TerminalAdapter(ABC):
    """终端适配器抽象接口。"""

    @abstractmethod
    def start(self, cols: int = 80, rows: int = 24) -> None:
        """建立会话并准备好读写。"""
        raise NotImplementedError

    @abstractmethod
    def read(self, size: int = 4096):
        """读取输出。

        返回值：
            - str / bytes：读到的内容
            - ""：当前无数据
            - None：会话结束
        """
        raise NotImplementedError

    @abstractmethod
    def write(self, data) -> None:
        """写入输入。"""
        raise NotImplementedError

    @abstractmethod
    def resize(self, cols: int, rows: int) -> None:
        """调整终端大小。"""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """关闭会话并释放资源。"""
        raise NotImplementedError

    def is_active(self) -> bool:
        """可选：检查会话是否仍然活跃。"""
        return True

