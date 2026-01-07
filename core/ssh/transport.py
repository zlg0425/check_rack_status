"""
SSH Transport 创建模块
提供 create_transport 函数
"""

import paramiko
import os

# 从 app.utils.config 导入配置和认证函数
from app.utils.config import (
    ssh_timeout,
    resolve_key,
    resolve_auth_mode,
    resolve_key_path,
)
import app.utils.config as config  # 使用模块引用以获取动态更新的值


def create_transport(server_name: str, ip: str, port: int) -> paramiko.Transport:
    """按认证模式创建并返回已认证的 Transport"""
    auth_mode = resolve_auth_mode(server_name)
    transport = None
    try:
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
    except Exception as e:
        if transport:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 错误
                pass
        raise

    try:
        if auth_mode == "none":
            transport.auth_none(config.ssh_username)
            return transport

        key_path = resolve_key(server_name, port)
        if not key_path:
            try:
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                pass
            raise RuntimeError(f"端口{port}未配置私钥")
        
        # 将相对路径转换为绝对路径（相对于项目根目录）
        key_path = resolve_key_path(key_path)
        
        # 检查私钥文件是否存在
        if not os.path.exists(key_path):
            try:
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                pass
            raise FileNotFoundError(f"私钥文件不存在: {key_path}")
        
        private_key = paramiko.RSAKey.from_private_key_file(key_path)
        try:
            transport.auth_publickey(username=config.ssh_username, key=private_key)
        except paramiko.ssh_exception.SSHException as auth_err:
            # 如果认证失败且连接已关闭，提供更明确的错误信息
            if "No existing session" in str(auth_err) or not transport.is_alive():
                try:
                    if transport.is_active():
                        transport.close()
                except (RuntimeError, Exception):
                    pass
                raise paramiko.ssh_exception.SSHException(f"Connection closed by remote host during authentication: {auth_err}")
            raise
        return transport
    except Exception as e:
        if transport:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 错误
                pass
        raise

__all__ = ['create_transport']
