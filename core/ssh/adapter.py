#!/usr/bin/env python3
"""
AsyncSSH 2.x 适配层
提供统一的SSH连接接口，基于 AsyncSSH 2.x 实现

注意：当前实现仅支持 AsyncSSH 2.x，不再支持 1.x 版本
"""

import asyncio
import sys
import os
import time
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field
from functools import wraps
import logging

# 尝试导入asyncssh，如果未安装则提供友好的错误提示
try:
    import asyncssh
    ASYNSSH_AVAILABLE = True
except ImportError:
    ASYNSSH_AVAILABLE = False
    asyncssh = None

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ConnectionConfig:
    """连接配置数据类"""
    host: str
    port: int = 22
    username: Optional[str] = None
    password: Optional[str] = None
    client_keys: Optional[List[str]] = None
    known_hosts: Optional[str] = None
    connect_timeout: int = 10
    login_timeout: int = 10
    # 额外配置选项
    compression: bool = False
    keepalive_interval: int = 30
    keepalive_count_max: int = 3
    # 主机密钥验证选项（⚠️ 仅用于测试环境）
    skip_host_key_check: bool = False  # 是否跳过主机密钥验证
    client_host_keys: Optional[str] = None  # 'auto' 自动接受首次连接, None 跳过验证

def get_asyncssh_version() -> str:
    """获取 asyncssh 版本字符串（仅用于日志）"""
    if not ASYNSSH_AVAILABLE:
        return "not_installed"
    return getattr(asyncssh, '__version__', 'unknown')

class SSHConnectionAdapter:
    """
    AsyncSSH 2.x 适配层
    提供统一的SSH连接接口，基于 AsyncSSH 2.x 实现
    
    使用示例:
        adapter = SSHConnectionAdapter()
        config = ConnectionConfig(
            host='example.com',
            port=22,
            username='admin',
            password='password'
        )
        conn = await adapter.create_connection(config)
        result = await adapter.run_command(conn, 'ls -la')
    """
    
    def __init__(self, enable_connection_pool: bool = True, max_pool_size: int = 50):
        """
        初始化适配器
        
        Args:
            enable_connection_pool: 是否启用连接池
            max_pool_size: 连接池最大大小
        """
        if not ASYNSSH_AVAILABLE:
            raise ImportError(
                "asyncssh未安装。请运行: pip install asyncssh\n"
                "注意：当前实现需要 AsyncSSH 2.x 版本\n"
                "如果使用paramiko，请使用check_rack_status.py中的create_transport函数"
            )
        
        self._enable_pool = enable_connection_pool
        self._max_pool_size = max_pool_size
        
        logger.info(f"检测到 AsyncSSH 版本: {get_asyncssh_version()}")
        logger.info("使用 AsyncSSH 2.x 实现")
        
        # 连接池 {connection_key: conn}
        self._connection_pool: Dict[str, Any] = {}
        self._pool_lock = asyncio.Lock()
        # 连接元数据 {connection_key: metadata}
        self._connection_metadata: Dict[str, Dict[str, Any]] = {}
    
    async def create_connection(self, config: ConnectionConfig) -> Any:
        """
        创建SSH连接（AsyncSSH 2.x 实现）
        
        Args:
            config: 连接配置
            
        Returns:
            asyncssh.SSHClientConnection: SSH连接对象
            
        Raises:
            asyncssh.Error: SSH连接错误
            ValueError: 配置错误
        """
        if not config.host:
            raise ValueError("host参数不能为空")
        
        connection_key = f"{config.host}:{config.port}:{config.username or 'default'}"
        
        # 如果启用连接池，先检查是否有可用连接
        if self._enable_pool:
            async with self._pool_lock:
                if connection_key in self._connection_pool:
                    conn = self._connection_pool[connection_key]
                    if await self._is_connection_healthy(conn):
                        # 更新最后使用时间
                        if connection_key in self._connection_metadata:
                            self._connection_metadata[connection_key]['last_used'] = asyncio.get_event_loop().time()
                            self._connection_metadata[connection_key]['use_count'] = \
                                self._connection_metadata[connection_key].get('use_count', 0) + 1
                        logger.debug(f"复用池中连接: {connection_key}")
                        return conn
                    else:
                        # 连接已失效，从池中移除
                        del self._connection_pool[connection_key]
                        if connection_key in self._connection_metadata:
                            del self._connection_metadata[connection_key]
        
        # 使用 AsyncSSH 2.x 连接实现
        try:
            conn = await self._connect(config)
        except Exception as e:
            logger.error(f"创建SSH连接失败: {e}")
            raise
        
        # 将新连接放入连接池
        if self._enable_pool:
            async with self._pool_lock:
                self._connection_pool[connection_key] = conn
                self._connection_metadata[connection_key] = {
                    'created_at': asyncio.get_event_loop().time(),
                    'last_used': asyncio.get_event_loop().time(),
                    'use_count': 1,
                    'host': config.host,
                    'port': config.port,
                    'username': config.username
                }
                
                # 如果连接池已满，清理最旧的连接
                if len(self._connection_pool) > self._max_pool_size:
                    await self._cleanup_idle_connections()
        
        return conn
    
    async def _connect(self, config: ConnectionConfig) -> Any:
        """AsyncSSH 2.x 连接实现"""
        logger.debug("使用 AsyncSSH 2.x 连接")
        
        # 2.x版本严格要求关键字参数
        conn_params: Dict[str, Any] = {
            'host': config.host,
            'port': config.port,
            'connect_timeout': config.connect_timeout,
            'login_timeout': config.login_timeout,
        }
        
        # 处理认证信息
        if config.username:
            conn_params['username'] = config.username
        
        if config.password:
            conn_params['password'] = config.password
        elif config.client_keys:
            conn_params['client_keys'] = config.client_keys
        
        # 处理主机密钥验证（⚠️ 仅用于测试环境）
        if config.skip_host_key_check:
            # 完全跳过主机密钥验证
            conn_params['known_hosts'] = None
            # 注意：asyncssh 2.x 中，设置 known_hosts=None 即可跳过验证
            # 不需要设置 client_host_keys
            logger.warning(f"⚠️ 跳过主机密钥验证（仅用于测试环境）: {config.host}:{config.port}")
        elif config.client_host_keys == 'auto' or (config.client_host_keys is None and config.known_hosts):
            # 自动接受首次连接的主机密钥
            # asyncssh 会自动处理：如果 known_hosts 文件不存在或主机不在其中，会自动添加
            known_hosts_path = config.known_hosts or 'known_hosts'
            # 确保路径是绝对路径或相对于当前工作目录
            if not os.path.isabs(known_hosts_path):
                # 相对于项目根目录
                script_dir = os.path.dirname(os.path.abspath(__file__))
                known_hosts_path = os.path.join(script_dir, known_hosts_path)
            conn_params['known_hosts'] = known_hosts_path
            # 重要：不设置 client_host_keys 参数，asyncssh 不支持此参数
            # asyncssh 会自动处理 known_hosts 文件（不存在则创建，主机不在则添加）
            logger.info(f"使用自动主机密钥验证: {config.host}:{config.port} (known_hosts={known_hosts_path})")
        elif config.known_hosts is not None:
            # 使用指定的 known_hosts 文件
            conn_params['known_hosts'] = config.known_hosts
        
        # 处理keepalive
        if config.keepalive_interval:
            conn_params['keepalive_interval'] = config.keepalive_interval
            conn_params['keepalive_count_max'] = config.keepalive_count_max
        
        # 2.x版本必须使用关键字参数
        try:
            conn = await asyncssh.connect(**conn_params)
            return conn
        except asyncssh.HostKeyNotVerifiable as e:
            # 主机密钥验证失败（使用正确的异常类名称）
            logger.error(f"主机密钥验证失败: {config.host}:{config.port} - {e}")
            logger.info("提示: 如果这是可信的测试环境，可以设置 skip_host_key_check=True")
            raise
        except asyncssh.Error as e:
            # 捕获其他SSH错误，检查是否是主机密钥相关问题
            error_msg = str(e).lower()
            error_type = type(e).__name__.lower()
            
            # 检查错误消息或类型名称中是否包含主机密钥相关关键词
            host_key_keywords = ['host key', 'hostkey', 'not verified', 'not trusted', 
                               'verification failed', 'not verifiable']
            
            if any(keyword in error_msg for keyword in host_key_keywords) or \
               'hostkey' in error_type:
                logger.error(f"主机密钥验证失败: {config.host}:{config.port} - {e}")
                logger.info("提示: 如果这是可信的测试环境，可以设置 skip_host_key_check=True")
            else:
                logger.error(f"SSH连接失败: {config.host}:{config.port} - {e}")
            raise
        except Exception as e:
            # 捕获其他非asyncssh异常
            error_msg = str(e).lower()
            error_type = type(e).__name__
            
            if any(keyword in error_msg for keyword in ['host key', 'hostkey', 'not verified', 'not trusted']):
                logger.error(f"主机密钥验证失败: {config.host}:{config.port} - {e}")
                logger.info("提示: 如果这是可信的测试环境，可以设置 skip_host_key_check=True")
            else:
                logger.error(f"连接失败: {config.host}:{config.port} - {e}")
            raise
    
    async def _is_connection_healthy(self, conn: Any) -> bool:
        """检查连接是否健康"""
        try:
            # 发送小型测试命令
            result = await conn.run("echo health_check", timeout=2)
            return result.exit_status == 0
        except Exception:
            return False
    
    async def _cleanup_idle_connections(self, idle_timeout: int = 300):
        """清理空闲连接"""
        current_time = asyncio.get_event_loop().time()
        to_remove = []
        
        async with self._pool_lock:
            for key, metadata in list(self._connection_metadata.items()):
                if current_time - metadata.get('last_used', 0) > idle_timeout:
                    to_remove.append(key)
            
            for key in to_remove:
                if key in self._connection_pool:
                    try:
                        conn = self._connection_pool[key]
                        conn.close()
                    except:
                        pass
                    del self._connection_pool[key]
                if key in self._connection_metadata:
                    del self._connection_metadata[key]
        
        if to_remove:
            logger.info(f"清理了 {len(to_remove)} 个空闲连接")
    
    async def run_command(self, conn: Any, command: str, timeout: int = 30) -> Dict[str, Any]:
        """
        执行命令（版本兼容）
        
        Args:
            conn: SSH连接对象
            command: 要执行的命令
            timeout: 超时时间（秒）
            
        Returns:
            Dict包含执行结果:
            {
                'success': bool,
                'stdout': str,
                'stderr': str,
                'returncode': int,
                'exit_status': int,
                'error': str (如果失败),
                'error_type': str (如果失败)
            }
        """
        try:
            result = await conn.run(command, timeout=timeout)
            
            return {
                'success': True,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
                'exit_status': getattr(result, 'exit_status', result.returncode)
            }
        except asyncssh.TimeoutError as e:
            return {
                'success': False,
                'error': f"命令执行超时: {command}",
                'error_type': 'TIMEOUT',
                'exception': str(e)
            }
        except asyncssh.Error as e:
            return {
                'success': False,
                'error': f"SSH错误: {e}",
                'error_type': 'SSH_ERROR',
                'exception': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"未知错误: {e}",
                'error_type': 'UNKNOWN_ERROR',
                'exception': str(e)
            }
    
    async def create_interactive_shell(self, conn: Any, term_type: str = 'xterm-256color',
                                      cols: int = 80, rows: int = 24) -> Any:
        """
        创建交互式Shell会话（用户方案推荐方法）
        
        这是 open_shell() 的别名，提供与用户方案一致的接口
        
        Args:
            conn: SSH连接对象
            term_type: 终端类型
            cols: 终端列数
            rows: 终端行数
            
        Returns:
            ShellWrapper对象，提供统一的read/write接口
        """
        return await self.open_shell(conn, term_type, cols, rows)
    
    async def open_shell(self, conn: Any, term_type: str = 'xterm-256color',
                        cols: int = 80, rows: int = 24) -> Any:
        """
        打开交互式Shell（AsyncSSH 2.x 实现）
        
        Args:
            conn: SSH连接对象
            term_type: 终端类型
            cols: 终端列数
            rows: 终端行数
            
        Returns:
            ShellWrapper对象，提供统一的read/write接口
        """
        try:
            # AsyncSSH 2.x 使用 create_session + 自定义Session类
            # 数据通过回调接收，需要创建一个包装类来模拟read()接口
            logger.debug("使用 create_session + 自定义Session创建shell（AsyncSSH 2.x）")
            try:
                from asyncssh import SSHClientSession
                import asyncio
                
                # 创建一个数据队列用于接收shell输出
                data_queue = asyncio.Queue()
                exit_status = [None]  # 使用列表以便在回调中修改
                connection_lost_event = asyncio.Event()
                
                class ShellSession(SSHClientSession):
                    """自定义Session类，用于接收shell数据"""
                    def data_received(self, data: bytes, datatype):
                        """接收数据时调用（关键：这是asyncssh 2.x接收数据的唯一方式）"""
                        try:
                            if data:
                                data_queue.put_nowait(data)
                        except Exception as e:
                            logger.debug(f"数据队列已满或已关闭: {e}")
                    
                    def exit_status_received(self, status: int):
                        """接收退出状态时调用"""
                        exit_status[0] = status
                        try:
                            data_queue.put_nowait(None)  # 发送结束信号
                        except:
                            pass
                    
                    def connection_lost(self, exc):
                        """连接丢失时调用"""
                        try:
                            data_queue.put_nowait(None)  # 发送结束信号
                        except:
                            pass
                        connection_lost_event.set()
                    
                    def eof_received(self):
                        """EOF接收时调用"""
                        try:
                            data_queue.put_nowait(None)  # 发送结束信号
                        except:
                            pass
                
                # 创建会话通道和会话对象
                chan, session = await conn.create_session(
                    session_factory=ShellSession,  # 使用自定义Session类
                    request_pty=True,  # 请求PTY（伪终端），交互式shell必需
                    term_type=term_type,
                    term_size=(cols, rows) if cols and rows else None,
                    encoding='utf-8',  # 明确指定编码
                    errors='replace'  # 处理编码错误
                )
                
                # 创建一个包装对象，提供统一的异步read/write接口（完全异步化）
                class ShellWrapper:
                    """Shell包装类，提供统一的异步read/write接口，完全匹配asyncssh 2.x的异步模型"""
                    def __init__(self, channel, session, queue, status_ref, lost_event):
                        self._channel = channel
                        self._session = session
                        self._queue = queue
                        self._exit_status = status_ref
                        self._connection_lost = lost_event
                        self._closed = False
                    
                    async def read(self, n: int = -1, timeout: float = None):
                        """
                        异步读取数据
                        持续读取数据流，直到有数据或超时
                        """
                        if self._closed:
                            return None
                        
                        try:
                            if timeout:
                                data = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                            else:
                                # 如果没有超时，等待一小段时间避免阻塞
                                try:
                                    data = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                                except asyncio.TimeoutError:
                                    return b''  # 超时返回空数据，继续循环
                            
                            if data is None:  # 结束信号
                                self._closed = True
                                return b''
                            
                            # 如果指定了读取长度，截取数据
                            if n > 0 and len(data) > n:
                                # 将多余的数据放回队列
                                remaining = data[n:]
                                try:
                                    self._queue.put_nowait(remaining)
                                except:
                                    pass
                                return data[:n]
                            
                            return data
                        except asyncio.TimeoutError:
                            return b''  # 超时返回空，继续读取
                        except Exception as e:
                            logger.debug(f"读取数据异常: {e}")
                            self._closed = True
                            return None
                    
                    async def write(self, data):
                            """
                            异步写入数据到shell
                            
                            根据 AsyncSSH 2.x 官方文档：
                            - 当 create_session() 指定了 encoding 参数时，channel.write() 应该接收字符串（str）
                            - 当 create_session() 未指定 encoding 参数时，channel.write() 应该接收字节（bytes）
                            
                            由于我们在 create_session() 中指定了 encoding='utf-8'，
                            因此这里确保传入的是字符串类型。
                            
                            Args:
                                data: 要写入的数据，可以是 str 或 bytes
                            """
                            if self._closed:
                                return
                            
                            # 检查连接状态
                            channel_is_closing = hasattr(self._channel, 'is_closing') and self._channel.is_closing()
                            channel_is_closed = hasattr(self._channel, 'is_closed') and self._channel.is_closed()
                            
                            if channel_is_closing:
                                return
                            if channel_is_closed:
                                self._closed = True
                                return
                            
                            try:
                                # 根据 AsyncSSH 2.x 官方文档：
                                # - 当 create_session() 指定了 encoding 参数时，channel.write() 期望接收字符串（str）
                                # - 当 create_session() 未指定 encoding 参数时，channel.write() 期望接收字节（bytes）
                                # 
                                # 由于我们在 create_session() 中指定了 encoding='utf-8'（见第636行），
                                # 因此 channel.write() 期望接收字符串，而不是 bytes。
                                # 如果传入 bytes，asyncssh 内部会尝试再次编码，导致 TypeError。
                                if isinstance(data, bytes):
                                    # 如果传入的是 bytes，先解码为字符串
                                    data = data.decode('utf-8', errors='ignore')
                                
                                # 确保 data 是字符串类型（符合 AsyncSSH 2.x 的要求）
                                if not isinstance(data, str):
                                    # 如果仍然不是字符串，转换为字符串
                                    data = str(data)
                                
                                # 重要：记录发送的数据（特别是回车符），用于调试
                                # 回车键可能发送 '\n'、'\r' 或 '\r\n'，这些都是正常的
                                data_repr = repr(data)
                                if '\n' in data or '\r' in data:
                                    logger.debug(f"发送包含换行符的数据: {data_repr}")
                                
                                # 调用 asyncssh 2.x 的 channel.write()
                                # 注意：根据官方文档，当指定了 encoding 时，write() 是同步方法，返回 None
                                # 但为了保持统一的异步接口，我们将此方法标记为 async
                                self._channel.write(data)
                                
                            except Exception as e:
                                # 重要：不要直接关闭连接，而是向上抛出异常
                                # 让调用者（如断开事件处理器）决定如何清理
                                
                                # 检查是否是 SSH 相关错误
                                if ASYNSSH_AVAILABLE and isinstance(e, asyncssh.Error):
                                    logger.error(f"发送数据到SSH通道失败（asyncssh.Error）: {e}")
                                elif isinstance(e, (OSError, ConnectionError)):
                                    logger.error(f"发送数据到SSH通道失败（连接错误）: {e}")
                                else:
                                    logger.error(f"发送数据到SSH通道失败（未知错误）: {e}")
                                
                                # 标记为关闭状态，但不直接关闭通道（让调用者处理）
                                self._closed = True
                                # 向上抛出异常，让调用者决定如何处理
                                raise
                    
                    def exit_status(self):
                        """获取退出状态（同步方法，仅查询状态）"""
                        return self._exit_status[0]
                    
                    async def close(self):
                        """异步关闭连接"""
                        if not self._closed:
                            self._closed = True
                            try:
                                # asyncssh 2.x的channel.close()是同步的，但为了统一接口，保持异步
                                self._channel.close()
                            except Exception as e:
                                logger.debug(f"关闭通道异常: {e}")
                    
                    def is_closing(self):
                        """检查是否正在关闭（同步方法，仅查询状态）"""
                        return self._closed or (hasattr(self._channel, 'is_closing') and self._channel.is_closing())
                    
                    def is_closed(self):
                        """检查是否已关闭（同步方法，仅查询状态）"""
                        return self._closed or (hasattr(self._channel, 'is_closed') and self._channel.is_closed())
                    
                    async def change_terminal_size(self, cols: int, rows: int):
                        """异步调整终端尺寸"""
                        try:
                            # asyncssh 2.x的change_terminal_size()是同步的，但为了统一接口，保持异步
                            self._channel.change_terminal_size(cols, rows)
                        except Exception as e:
                            logger.debug(f"调整终端尺寸异常: {e}")
                    
                    async def resize(self, cols: int, rows: int):
                        """异步调整终端尺寸（别名）"""
                        await self.change_terminal_size(cols, rows)
                
                shell_wrapper = ShellWrapper(chan, session, data_queue, exit_status, connection_lost_event)
                
                # 等待一小段时间，让shell初始化完成
                await asyncio.sleep(0.3)
                
                logger.debug("使用 create_session + 自定义Session创建shell成功（AsyncSSH 2.x）")
                return shell_wrapper
                
            except Exception as e2:
                    logger.error(f"创建shell失败: {e2}")
                    import traceback
                    logger.error(traceback.format_exc())
                    raise ValueError(
                        f"无法创建shell会话。AsyncSSH 2.x要求使用create_session(session_factory=..., request_pty=True)。"
                        f"错误详情: {e2}"
                    )
        except Exception as e:
            logger.error(f"创建Shell失败: {e}")
            raise
    
    async def write_to_shell(self, channel: Any, data: str):
        """
        向Shell写入数据（用户方案推荐方法）
        
        Args:
            channel: Shell通道对象（ShellWrapper）
            data: 要写入的数据（字符串）
        """
        if channel and not channel.is_closed():
            try:
                await channel.write(data)
            except Exception as e:
                logger.error(f"写入Shell失败: {e}")
                raise
    
    async def read_from_shell(self, channel: Any, n: int = 1024, timeout: float = 1.0):
        """
        从Shell读取数据（用户方案推荐方法）
        
        Args:
            channel: Shell通道对象（ShellWrapper）
            n: 读取的最大字节数
            timeout: 超时时间（秒）
            
        Returns:
            读取的数据（bytes），超时返回空bytes
        """
        try:
            data = await asyncio.wait_for(channel.read(n), timeout)
            return data if data else b''
        except asyncio.TimeoutError:
            return b''  # 超时返回空
        except Exception as e:
            logger.error(f"从Shell读取失败: {e}")
            raise
    
    async def close_shell(self, channel: Any):
        """
        关闭Shell通道（用户方案推荐方法）
        
        Args:
            channel: Shell通道对象（ShellWrapper）
        """
        if channel and not channel.is_closed():
            try:
                await channel.close()
                logger.debug("Shell通道已关闭")
            except Exception as e:
                logger.error(f"关闭Shell失败: {e}")
    
    async def close_connection(self, connection_key: Optional[str] = None):
        """
        关闭连接并从池中移除
        
        Args:
            connection_key: 连接键，如果为None则关闭所有连接
        """
        async with self._pool_lock:
            if connection_key:
                if connection_key in self._connection_pool:
                    conn = self._connection_pool[connection_key]
                    try:
                        conn.close()
                    except:
                        pass
                    del self._connection_pool[connection_key]
                    logger.debug(f"已关闭连接: {connection_key}")
                if connection_key in self._connection_metadata:
                    del self._connection_metadata[connection_key]
            else:
                # 关闭所有连接
                for key, conn in list(self._connection_pool.items()):
                    try:
                        conn.close()
                    except:
                        pass
                self._connection_pool.clear()
                self._connection_metadata.clear()
                logger.debug("已关闭所有连接")
    
    def get_connection_info(self) -> Dict[str, Any]:
        """获取适配层和连接池信息"""
        return {
            'asyncssh_version': get_asyncssh_version(),
            'implementation': 'AsyncSSH 2.x',
            'pool_enabled': self._enable_pool,
            'pool_size': len(self._connection_pool),
            'max_pool_size': self._max_pool_size,
            'pool_connections': list(self._connection_pool.keys()),
            'connection_metadata': dict(self._connection_metadata)
        }
    
    async def cleanup_idle_connections(self, idle_timeout: int = 300):
        """手动清理空闲连接"""
        await self._cleanup_idle_connections(idle_timeout)


# 装饰器：提供向后兼容的简便方式
def compatible_ssh_connection(func):
    """
    装饰器：使任何函数都能获得版本兼容的SSH连接
    
    使用示例:
        @compatible_ssh_connection
        async def my_operation(conn, *args, **kwargs):
            result = await conn.run('ls -la')
            return result.stdout
        
        # 调用时传入连接参数
        output = await my_operation('example.com', port=22, username='admin', password='pass')
    """
    @wraps(func)
    async def wrapper(host: str, port: int = 22, username: Optional[str] = None,
                     password: Optional[str] = None, **kwargs):
        adapter = SSHConnectionAdapter()
        config = ConnectionConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            **kwargs
        )
        
        conn = None
        try:
            conn = await adapter.create_connection(config)
            return await func(conn, *args, **kwargs)  # type: ignore
        finally:
            if conn:
                # 注意：这里简化处理，实际应用中可能需要更复杂的连接管理
                pass
    
    return wrapper


# 工厂函数：创建版本自适应的连接对象
async def create_ssh_connection(host: str, port: int = 22, 
                                username: Optional[str] = None,
                                password: Optional[str] = None,
                                skip_host_key_check: bool = False,
                                **kwargs) -> Any:
    """
    工厂函数：创建SSH连接的简便方式
    
    示例:
        # 正常连接（需要主机密钥验证）
        conn = await create_ssh_connection('example.com', 
                                          username='root',
                                          password='pass',
                                          port=2222)
        
        # 测试环境连接（跳过主机密钥验证）
        conn = await create_ssh_connection('example.com',
                                          username='root',
                                          password='pass',
                                          skip_host_key_check=True)
    """
    adapter = SSHConnectionAdapter()
    config = ConnectionConfig(host=host, port=port, username=username, 
                           password=password, skip_host_key_check=skip_host_key_check,
                           **kwargs)
    return await adapter.create_connection(config)


# 使用示例
if __name__ == "__main__":
    async def example_usage():
        """使用示例"""
        try:
            # 1. 创建适配器实例
            adapter = SSHConnectionAdapter()
            
            # 2. 准备连接配置
            config = ConnectionConfig(
                host='localhost',  # 替换为实际主机
                port=22,
                username='test',  # 替换为实际用户名
                password='test',  # 替换为实际密码或使用client_keys
                connect_timeout=10
            )
            
            # 3. 建立连接
            conn = await adapter.create_connection(config)
            print(f"连接成功！实现: {adapter.get_connection_info()['implementation']}")
            
            # 4. 执行命令
            result = await adapter.run_command(conn, 'uname -a')
            if result['success']:
                print(f"系统信息: {result['stdout']}")
            else:
                print(f"命令执行失败: {result['error']}")
            
            # 5. 清理连接
            await adapter.close_connection()
            
        except ImportError as e:
            print(f"导入错误: {e}")
        except Exception as e:
            print(f"操作失败: {e}")
    
    # 运行示例
    asyncio.run(example_usage())

