"""
SSH 相关模块
提供 SSH 连接和适配器功能
"""

from core.ssh.adapter import (
    SSHConnectionAdapter,
    ConnectionConfig,
    create_ssh_connection,
    get_asyncssh_version
)
from core.ssh.transport import create_transport

__all__ = [
    'SSHConnectionAdapter',
    'ConnectionConfig',
    'create_ssh_connection',
    'get_asyncssh_version',
    'create_transport',
]
