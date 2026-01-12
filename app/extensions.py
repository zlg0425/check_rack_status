"""
扩展初始化模块
初始化SocketIO、任务管理器、后台任务等扩展
"""

import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flask_socketio import SocketIO

# 全局扩展实例（延迟初始化）
_socketio: Optional['SocketIO'] = None
_task_managers: Optional[dict] = None


def init_extensions(socketio_instance: 'SocketIO') -> None:
    """初始化所有扩展
    
    Args:
        socketio_instance: SocketIO实例
    """
    global _socketio, _task_managers
    
    
    _socketio = socketio_instance
    
    # 初始化任务管理器
    from app.models.task import get_task_managers
    _task_managers = get_task_managers()
    


def get_socketio() -> Optional['SocketIO']:
    """获取SocketIO实例"""
    return _socketio


def get_task_managers() -> dict:
    """获取任务管理器字典"""
    global _task_managers
    if _task_managers is None:
        from app.models.task import get_task_managers as _get_task_managers
        _task_managers = _get_task_managers()
    return _task_managers


def get_batch_upload_manager():
    """获取或创建批量上传管理器（延迟初始化）
    
    兼容 web_ui.py 中的函数签名
    Returns:
        (manager, tasks_dict, lock)
    """
    managers = get_task_managers()
    batch_upload_manager = managers['batch_upload']
    return batch_upload_manager.get_manager()


def start_background_tasks():
    """启动后台任务"""
    from core.monitoring.checker import refresh_loop, stop_refresh_loop
    import threading
    import core.monitoring.checker as checker_module
    
    # 如果线程已经在运行，先停止它
    try:
        stop_refresh_loop()
    except Exception:
        pass
    
    # 重置停止事件（如果存在）
    if hasattr(checker_module, '_refresh_loop_stop_event'):
        checker_module._refresh_loop_stop_event.clear()
    
    # 启动新线程
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()
    
    # 保存线程引用到监控模块
    checker_module._refresh_loop_thread = t
    
    return t


def stop_background_tasks():
    """停止后台任务"""
    from core.monitoring.checker import stop_refresh_loop
    
    # 停止后台刷新循环
    try:
        stop_refresh_loop()
    except (RuntimeError, Exception):
        # 忽略解释器关闭时的错误
        pass
    
    # 关闭所有终端事件循环
    try:
        managers = get_task_managers()
        event_loop_manager = managers.get('terminal_event_loop')
        if event_loop_manager:
            event_loop_manager.close_all_loops()
    except (RuntimeError, Exception):
        # 忽略解释器关闭时的错误
        pass

