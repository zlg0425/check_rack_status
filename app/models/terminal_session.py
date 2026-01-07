"""
终端会话管理器（增强版）
基于 AsyncSSH 2.x 的网页 Terminal 实现方案

提供统一的会话生命周期管理，解决连接复用与生命周期问题
"""
import asyncio
import logging
import time
import threading
import concurrent.futures
from typing import Optional, Dict, Any
from app.models.task import TerminalSessionManager

logger = logging.getLogger(__name__)


class EnhancedTerminalSessionManager:
    """增强的终端会话管理器
    
    参考方案：基于 AsyncSSH 2.x 的网页 Terminal 实现
    提供统一的会话生命周期管理，避免连接泄露
    """
    
    def __init__(self, base_manager: TerminalSessionManager):
        """
        初始化增强的会话管理器
        
        Args:
            base_manager: 基础的任务管理器实例
        """
        self._base = base_manager
        self._read_tasks: Dict[str, Any] = {}  # session_id -> read_task
        self._locks: Dict[int, asyncio.Lock] = {}  # loop_id -> Lock (每个事件循环一个锁)
        self._lock_lock = threading.Lock()  # 保护 _locks 字典的线程锁
    
    async def create_session(self, session_id: str, adapter: Any, conn: Any, 
                           shell: Any, config: Any, server_name: str, 
                           server_ip: str, port: int, loop: Any = None):
        """为WebSocket会话创建SSH终端会话
        
        Args:
            session_id: WebSocket会话ID
            adapter: SSH适配器实例
            conn: SSH连接对象
            shell: Shell包装对象
            config: 连接配置
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
            loop: 事件循环（可选）
        """
        import json
        import time as time_module
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run1",
                    "hypothesisId": "SESSION_CREATE",
                    "location": "app/models/terminal_session.py:create_session:start",
                    "message": "开始创建增强会话",
                    "data": {
                        "session_id": session_id,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "port": port,
                        "has_adapter": adapter is not None,
                        "has_conn": conn is not None,
                        "has_shell": shell is not None,
                        "has_loop": loop is not None,
                        "session_exists": session_id in self._base._tasks
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 获取当前事件循环的锁（延迟初始化）
        # 注意：Python 3.10+ 中 asyncio.Lock() 不再接受 loop 参数
        # 锁会自动绑定到当前事件循环，所以我们需要在正确的事件循环中创建锁
        if loop is None:
            loop = asyncio.get_event_loop()
        loop_id = id(loop)
        with self._lock_lock:
            if loop_id not in self._locks:
                # 直接创建锁，锁会自动绑定到当前事件循环
                # 注意：Python 3.10+ 中 asyncio.Lock() 不再接受 loop 参数
                # 锁会在第一次使用时自动绑定到当前事件循环
                self._locks[loop_id] = asyncio.Lock()
            lock = self._locks[loop_id]
        
        async with lock:
            # 如果会话已存在，先关闭旧会话
            if session_id in self._base._tasks:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": session_id,
                            "runId": "run1",
                            "hypothesisId": "SESSION_CREATE",
                            "location": "app/models/terminal_session.py:create_session:close_existing",
                            "message": "关闭已存在的会话",
                            "data": {"session_id": session_id},
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                try:
                    await self.close_session(session_id, loop=loop)
                    # #region agent log
                    try:
                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "sessionId": session_id,
                                "runId": "run1",
                                "hypothesisId": "SESSION_CREATE",
                                "location": "app/models/terminal_session.py:create_session:close_existing_complete",
                                "message": "关闭已存在会话完成",
                                "data": {"session_id": session_id},
                                "timestamp": int(time_module.time() * 1000)
                            }) + '\n')
                    except Exception:
                        pass
                    # #endregion
                except Exception as close_err:
                    # #region agent log
                    try:
                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "sessionId": session_id,
                                "runId": "run1",
                                "hypothesisId": "SESSION_CREATE",
                                "location": "app/models/terminal_session.py:create_session:close_existing_error",
                                "message": "关闭已存在会话失败",
                                "data": {
                                    "session_id": session_id,
                                    "error": str(close_err),
                                    "error_type": type(close_err).__name__
                                },
                                "timestamp": int(time_module.time() * 1000)
                            }) + '\n')
                    except Exception:
                        pass
                    # #endregion
                    logger.warning(f"[{session_id}] 关闭已存在会话失败: {close_err}")
                    # 继续执行，即使关闭失败
            
            # 保存会话
            self._base.add_session(
                session_id=session_id,
                adapter=adapter,
                conn=conn,
                shell=shell,
                config=config,
                server_name=server_name,
                server_ip=server_ip,
                port=port,
                loop=loop
            )
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "SESSION_CREATE",
                        "location": "app/models/terminal_session.py:create_session:after_add",
                        "message": "会话已添加到基础管理器",
                        "data": {
                            "session_id": session_id,
                            "session_in_base": session_id in self._base._tasks,
                            "session_data": {
                                "has_adapter": session_id in self._base._tasks and self._base._tasks[session_id].get("adapter") is not None,
                                "has_conn": session_id in self._base._tasks and self._base._tasks[session_id].get("ssh_conn") is not None,
                                "has_shell": session_id in self._base._tasks and self._base._tasks[session_id].get("ssh_shell") is not None,
                                "has_loop": session_id in self._base._tasks and self._base._tasks[session_id].get("loop") is not None
                            }
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "SESSION_CREATE",
                        "location": "app/models/terminal_session.py:create_session:complete",
                        "message": "增强会话创建完成",
                        "data": {
                            "session_id": session_id,
                            "server_name": server_name,
                            "server_ip": server_ip,
                            "port": port,
                            "session_in_base": session_id in self._base._tasks
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            logger.info(f"终端会话创建成功: {session_id} -> {server_ip}:{port}")
            return True
    
    async def write_to_session(self, session_id: str, data: str):
        """向指定会话的终端写入数据
        
        Args:
            session_id: WebSocket会话ID
            data: 要写入的数据
        """
        import json
        import time
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run1",
                    "hypothesisId": "INPUT",
                    "location": "app/models/terminal_session.py:write_to_session:start",
                    "message": "准备写入数据到SSH shell",
                    "data": {
                        "session_id": session_id,
                        "data_length": len(data) if data else 0,
                        "data_preview": repr(data[:50]) if data else None,
                        "has_newline": '\n' in (data or ''),
                        "has_carriage_return": '\r' in (data or '')
                    },
                    "timestamp": int(time.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        session = self._base.get_session(session_id)
        if not session:
            logger.warning(f"会话不存在: {session_id}")
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "INPUT",
                        "location": "app/models/terminal_session.py:write_to_session:no_session",
                        "message": "会话不存在，无法写入",
                        "data": {"session_id": session_id},
                        "timestamp": int(time.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            return
        
        shell = session.get('ssh_shell')
        if not shell:
            logger.warning(f"Shell不存在: {session_id}")
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "INPUT",
                        "location": "app/models/terminal_session.py:write_to_session:no_shell",
                        "message": "Shell不存在，无法写入",
                        "data": {"session_id": session_id},
                        "timestamp": int(time.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            return
        
        try:
            # 关键：确保数据以换行符结尾（解决回车断开连接问题）
            # 但不要强制添加，因为某些控制字符（如 Ctrl+C）不应该有换行符
            # 只有在用户输入普通文本时才添加换行符
            original_data = data
            if data and not data.endswith('\n') and not data.endswith('\r'):
                # 检查是否是控制字符（如 Ctrl+C, Ctrl+D 等）
                if len(data) == 1 and ord(data[0]) < 32:
                    # 控制字符，不添加换行符
                    pass
                else:
                    # 普通文本，添加换行符
                    data += '\n'
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "INPUT",
                        "location": "app/models/terminal_session.py:write_to_session:before_write",
                        "message": "准备调用shell.write",
                        "data": {
                            "session_id": session_id,
                            "original_data_length": len(original_data) if original_data else 0,
                            "final_data_length": len(data) if data else 0,
                            "data_changed": original_data != data,
                            "final_data_preview": repr(data[:50]) if data else None
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            # 异步写入数据
            await shell.write(data)
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "INPUT",
                        "location": "app/models/terminal_session.py:write_to_session:after_write",
                        "message": "shell.write调用完成",
                        "data": {
                            "session_id": session_id,
                            "data_length": len(data) if data else 0
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            # 更新会话活动时间
            self._base.update_session_activity(session_id)
            
        except Exception as e:
            logger.error(f"写入会话失败 [{session_id}]: {e}")
            raise
    
    async def close_session(self, session_id: str, loop: Optional[Any] = None):
        """关闭并清理指定会话
        
        Args:
            session_id: WebSocket会话ID
            loop: 事件循环（可选，如果不提供则使用当前循环）
        """
        # 获取当前事件循环的锁（延迟初始化）
        # 注意：Python 3.10+ 中 asyncio.Lock() 不再接受 loop 参数
        # 锁会自动绑定到当前事件循环
        if loop is None:
            loop = asyncio.get_event_loop()
        loop_id = id(loop)
        with self._lock_lock:
            if loop_id not in self._locks:
                # 直接创建锁，锁会在第一次使用时自动绑定到当前事件循环
                self._locks[loop_id] = asyncio.Lock()
            lock = self._locks[loop_id]
        
        async with lock:
            session = self._base.get_session(session_id)
            if not session:
                return
            
            adapter = session.get('adapter')
            shell = session.get('ssh_shell')
            conn = session.get('ssh_conn')
            loop = session.get('loop')
            
            # 1. 先取消读取任务（如果存在）
            read_task = self._read_tasks.pop(session_id, None)
            if read_task:
                try:
                    read_task.cancel()
                    # 等待任务取消完成（最多等待1秒）
                    try:
                        if loop and loop.is_running():
                            await asyncio.wait_for(
                                asyncio.wrap_future(read_task, loop=loop),
                                timeout=1.0
                            )
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception:
                        pass
                    logger.debug(f"读取任务已取消: {session_id}")
                except Exception as e:
                    logger.warning(f"取消读取任务失败 [{session_id}]: {e}")
            
            # 2. 关闭 Shell（正确异步关闭）
            if shell:
                try:
                    if hasattr(shell, 'is_closed') and shell.is_closed():
                        logger.debug(f"[{session_id}] Shell已关闭")
                    else:
                        # ShellWrapper的close()是异步方法
                        if loop and loop.is_running():
                            close_future = asyncio.run_coroutine_threadsafe(shell.close(), loop)
                            try:
                                # 等待关闭完成（最多等待2秒）
                                close_future.result(timeout=2.0)
                            except Exception as close_err:
                                logger.warning(f"[{session_id}] 等待shell关闭超时或失败: {close_err}")
                        else:
                            if loop:
                                loop.run_until_complete(shell.close())
                            else:
                                await shell.close()
                        logger.debug(f"[{session_id}] Shell已关闭")
                except Exception as e:
                    logger.warning(f"[{session_id}] 关闭shell失败: {e}")
            
            # 3. 关闭 SSH 连接（正确异步关闭）
            if conn:
                try:
                    if hasattr(conn, 'is_closed') and conn.is_closed():
                        logger.debug(f"[{session_id}] SSH连接已关闭")
                    else:
                        # asyncssh.SSHClientConnection.close() 是同步方法
                        # 但在某些情况下可能需要异步处理
                        if hasattr(conn, 'close'):
                            conn.close()
                        logger.debug(f"[{session_id}] SSH连接已关闭")
                except Exception as e:
                    logger.warning(f"[{session_id}] 关闭SSH连接失败: {e}")
            
            # 4. 从基础管理器中移除会话
            self._base.remove_session(session_id)
            
            logger.info(f"终端会话已清理: {session_id}")
    
    def register_read_task(self, session_id: str, read_task: Any):
        """注册读取任务，以便在关闭时取消
        
        Args:
            session_id: WebSocket会话ID
            read_task: 读取任务（Future对象）
        """
        self._read_tasks[session_id] = read_task
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        return self._base.get_session(session_id)
    
    def update_session_activity(self, session_id: str):
        """更新会话活动时间"""
        self._base.update_session_activity(session_id)

