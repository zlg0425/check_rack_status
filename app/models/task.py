"""
任务管理模块
统一管理上传、FOTA、终端等任务状态
"""

import threading
import multiprocessing
import sys
import time
from typing import Dict, Any, Optional, Tuple


class TaskManager:
    """任务管理器基类"""
    
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def add_task(self, task_id: str, task_data: Dict[str, Any]) -> None:
        """添加任务"""
        with self._lock:
            self._tasks[task_id] = task_data
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务"""
        with self._lock:
            return self._tasks.get(task_id)
    
    def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        """更新任务"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(updates)
    
    def remove_task(self, task_id: str) -> None:
        """删除任务"""
        with self._lock:
            self._tasks.pop(task_id, None)
    
    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """获取所有任务（返回副本）"""
        with self._lock:
            return {tid: task.copy() for tid, task in self._tasks.items()}
    
    def clear(self) -> None:
        """清空所有任务"""
        with self._lock:
            self._tasks.clear()


class UploadTaskManager(TaskManager):
    """上传任务管理器
    
    管理单文件上传任务状态
    任务格式: {task_id: {"progress": 0-100, "status": "uploading|done|error", "result": {...}}}
    """
    pass


class BatchUploadTaskManager:
    """批量上传任务管理器
    
    管理批量上传任务，使用multiprocessing.Manager支持进程间共享
    """
    
    def __init__(self):
        self._manager: Optional[multiprocessing.Manager] = None
        self._tasks_dict: Optional[Any] = None
        self._lock: Optional[Any] = None
        self._init_lock = threading.Lock()
    
    def _ensure_initialized(self) -> None:
        """确保管理器已初始化（延迟初始化）"""
        if self._manager is None:
            with self._init_lock:
                if self._manager is None:
                    # Windows上需要使用spawn启动方式
                    if sys.platform == 'win32':
                        try:
                            multiprocessing.set_start_method('spawn', force=True)
                        except RuntimeError:
                            # 如果已经设置过，忽略错误
                            pass
                    self._manager = multiprocessing.Manager()
                    self._tasks_dict = self._manager.dict()
                    self._lock = self._manager.Lock()
    
    def get_manager(self) -> Tuple[multiprocessing.Manager, Any, Any]:
        """获取批量上传管理器（延迟初始化）"""
        self._ensure_initialized()
        return self._manager, self._tasks_dict, self._lock
    
    def get_tasks_dict(self) -> Any:
        """获取任务字典"""
        self._ensure_initialized()
        return self._tasks_dict
    
    def get_lock(self) -> Any:
        """获取锁"""
        self._ensure_initialized()
        return self._lock


class FotaTaskManager(TaskManager):
    """FOTA任务管理器
    
    管理FOTA升级任务状态
    任务格式: {task_id: {"progress": 0-100, "status": "checking|md5|uploading|upgrading|done|error", "step": "...", "result": {...}}}
    """
    pass


class BatchFotaTaskManager(TaskManager):
    """批量FOTA任务管理器
    
    管理批量FOTA任务状态
    任务格式: {batch_id: {"tasks": [task_id1, task_id2, ...], "status": "running|done|error|cancelled", "total": N, "completed": M, "cancelled": False}}
    """
    pass


class FotaServerLockManager:
    """FOTA服务器锁管理器
    
    防止同一服务器同时执行多个FOTA任务
    格式: {server_key: task_id}
    """
    
    def __init__(self):
        self._locks: Dict[str, str] = {}
        self._lock = threading.Lock()
    
    def acquire_lock(self, server_key: str, task_id: str) -> bool:
        """尝试获取服务器锁
        
        Returns:
            True: 成功获取锁
            False: 服务器已有任务在执行
        """
        with self._lock:
            if server_key in self._locks:
                return False
            self._locks[server_key] = task_id
            return True
    
    def release_lock(self, server_key: str, task_id: str) -> None:
        """释放服务器锁"""
        with self._lock:
            if self._locks.get(server_key) == task_id:
                del self._locks[server_key]
    
    def get_all_locks(self) -> Dict[str, str]:
        """获取所有锁（返回副本）"""
        with self._lock:
            return self._locks.copy()
    
    def clear_lock(self, server_key: str) -> None:
        """清除指定服务器的锁"""
        with self._lock:
            self._locks.pop(server_key, None)


class FotaTransportManager:
    """FOTA传输管理器
    
    管理FOTA任务的transport和sftp连接，用于终止时关闭连接
    格式: {task_id: {"transport": transport, "sftp": sftp}}
    """
    
    def __init__(self):
        self._transports: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def add_transport(self, task_id: str, transport: Any, sftp: Any) -> None:
        """添加传输连接"""
        with self._lock:
            self._transports[task_id] = {"transport": transport, "sftp": sftp}
    
    def get_transport(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取传输连接"""
        with self._lock:
            return self._transports.get(task_id)
    
    def remove_transport(self, task_id: str) -> None:
        """移除传输连接"""
        with self._lock:
            self._transports.pop(task_id, None)
    
    def remove_transports(self, task_ids: list) -> None:
        """批量移除传输连接"""
        with self._lock:
            for task_id in task_ids:
                self._transports.pop(task_id, None)
    
    def clear(self) -> None:
        """清空所有传输连接"""
        with self._lock:
            self._transports.clear()


class TerminalSessionManager(TaskManager):
    """终端会话管理器（增强版）
    
    管理Web终端会话，提供统一的会话生命周期管理
    格式: {session_id: {"adapter": adapter, "conn": conn, "shell": shell, "config": config, ...}}
    
    参考方案：基于 AsyncSSH 2.x 的网页 Terminal 实现
    """
    
    def __init__(self):
        super().__init__()
        # 额外的会话元数据
        self._session_metadata: Dict[str, Dict[str, Any]] = {}
    
    def add_session(self, session_id: str, adapter: Any, conn: Any, shell: Any, 
                   config: Any, server_name: str, server_ip: str, port: int, loop: Any = None):
        """添加终端会话
        
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
        # #region agent log
        import json
        import time as time_module
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run1",
                    "hypothesisId": "SESSION_ADD",
                    "location": "app/models/task.py:add_session:start",
                    "message": "开始添加会话到基础管理器",
                    "data": {
                        "session_id": session_id,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "port": port,
                        "has_adapter": adapter is not None,
                        "has_conn": conn is not None,
                        "has_shell": shell is not None,
                        "has_loop": loop is not None,
                        "session_exists": session_id in self._tasks
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        with self._lock:
            self._tasks[session_id] = {
                "adapter": adapter,
                "ssh_conn": conn,
                "ssh_shell": shell,
                "config": config,
                "server_name": server_name,
                "server_ip": server_ip,
                "port": port,
                "loop": loop
            }
            self._session_metadata[session_id] = {
                "created_at": time.time(),
                "last_activity": time.time()
            }
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": session_id,
                        "runId": "run1",
                        "hypothesisId": "SESSION_ADD",
                        "location": "app/models/task.py:add_session:complete",
                        "message": "会话已添加到基础管理器",
                        "data": {
                            "session_id": session_id,
                            "session_in_tasks": session_id in self._tasks,
                            "session_in_metadata": session_id in self._session_metadata,
                            "total_sessions": len(self._tasks)
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        with self._lock:
            return self._tasks.get(session_id)
    
    def update_session_activity(self, session_id: str):
        """更新会话活动时间"""
        with self._lock:
            if session_id in self._session_metadata:
                self._session_metadata[session_id]["last_activity"] = time.time()
    
    def remove_session(self, session_id: str):
        """移除会话（不关闭连接，由调用者负责关闭）"""
        with self._lock:
            self._tasks.pop(session_id, None)
            self._session_metadata.pop(session_id, None)
    
    def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话元数据"""
        with self._lock:
            return self._session_metadata.get(session_id)


class TerminalEventLoopManager:
    """终端事件循环管理器
    
    管理异步事件循环（用于适配层）
    格式: {thread_id: event_loop}
    """
    
    def __init__(self):
        self._loops: Dict[int, Any] = {}
        self._lock = threading.Lock()
    
    def get_or_create_loop(self, thread_id: Optional[int] = None) -> Any:
        """获取或创建当前线程的事件循环"""
        import asyncio
        
        if thread_id is None:
            thread_id = threading.get_ident()
        
        with self._lock:
            if thread_id not in self._loops:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loops[thread_id] = loop
            return self._loops[thread_id]
    
    def remove_loop(self, thread_id: int) -> None:
        """移除事件循环"""
        with self._lock:
            self._loops.pop(thread_id, None)
    
    def close_all_loops(self):
        """关闭所有事件循环（用于程序退出时清理）"""
        import asyncio
        
        with self._lock:
            loops_to_close = list(self._loops.items())
            self._loops.clear()
        
        for thread_id, loop in loops_to_close:
            try:
                if loop.is_running():
                    # 如果循环正在运行，尝试停止它
                    loop.call_soon_threadsafe(loop.stop)
                    # 等待一小段时间让循环停止
                    import time
                    time.sleep(0.1)
                
                # 取消所有待处理的任务
                pending = asyncio.all_tasks(loop=loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    # 等待任务取消完成
                    try:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                
                # 关闭循环
                loop.close()
            except Exception as e:
                # 忽略关闭时的错误（可能是在解释器关闭时）
                pass


# 全局任务管理器实例（单例模式）
_upload_task_manager: Optional[UploadTaskManager] = None
_batch_upload_task_manager: Optional[BatchUploadTaskManager] = None
_fota_task_manager: Optional[FotaTaskManager] = None
_batch_fota_task_manager: Optional[BatchFotaTaskManager] = None
_fota_server_lock_manager: Optional[FotaServerLockManager] = None
_fota_transport_manager: Optional[FotaTransportManager] = None
_terminal_session_manager: Optional[TerminalSessionManager] = None
_terminal_event_loop_manager: Optional[TerminalEventLoopManager] = None


def get_task_managers() -> Dict[str, Any]:
    """获取所有任务管理器实例（单例模式）"""
    global _upload_task_manager
    global _batch_upload_task_manager
    global _fota_task_manager
    global _batch_fota_task_manager
    global _fota_server_lock_manager
    global _fota_transport_manager
    global _terminal_session_manager
    global _terminal_event_loop_manager
    
    if _upload_task_manager is None:
        _upload_task_manager = UploadTaskManager()
    if _batch_upload_task_manager is None:
        _batch_upload_task_manager = BatchUploadTaskManager()
    if _fota_task_manager is None:
        _fota_task_manager = FotaTaskManager()
    if _batch_fota_task_manager is None:
        _batch_fota_task_manager = BatchFotaTaskManager()
    if _fota_server_lock_manager is None:
        _fota_server_lock_manager = FotaServerLockManager()
    if _fota_transport_manager is None:
        _fota_transport_manager = FotaTransportManager()
    if _terminal_session_manager is None:
        _terminal_session_manager = TerminalSessionManager()
    if _terminal_event_loop_manager is None:
        _terminal_event_loop_manager = TerminalEventLoopManager()
    
    return {
        'upload': _upload_task_manager,
        'batch_upload': _batch_upload_task_manager,
        'fota': _fota_task_manager,
        'batch_fota': _batch_fota_task_manager,
        'fota_server_lock': _fota_server_lock_manager,
        'fota_transport': _fota_transport_manager,
        'terminal_session': _terminal_session_manager,
        'terminal_event_loop': _terminal_event_loop_manager,
    }

