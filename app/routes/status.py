"""
状态查询路由模块
"""
from flask import Blueprint, jsonify
import threading
from app.utils.config import upload_target_dir, fota_target_dir

bp = Blueprint('status', __name__, url_prefix='')

# 从 core.monitoring 导入状态缓存
from core.monitoring import status_cache, cache_lock

# 从 app.extensions 导入任务管理器
from app.extensions import get_task_managers

# 获取任务管理器
task_managers = get_task_managers()
fota_task_manager = task_managers['fota']
fota_server_lock_manager = task_managers['fota_server_lock']

# 为了兼容性，提供直接访问接口
fota_tasks = fota_task_manager._tasks
fota_tasks_lock = fota_task_manager._lock
fota_server_locks = fota_server_lock_manager._locks
fota_server_locks_lock = fota_server_lock_manager._lock


@bp.route("/api/status")
def api_status():
    """获取服务器状态"""
    # 直接使用从 web_ui 导入的变量（保持兼容）
    with cache_lock:
        # 确保 status_cache["data"] 存在且是列表
        if "data" not in status_cache or not isinstance(status_cache["data"], list):
            # 如果数据不存在或格式错误，返回空列表
            servers_data = []
        else:
            servers_data = status_cache["data"].copy()
    
    # 为每个服务器添加FOTA状态信息
    with fota_server_locks_lock:
        server_locks_copy = fota_server_locks.copy()
    
    # 获取所有正在执行的FOTA任务状态
    with fota_tasks_lock:
        tasks_copy = {tid: task.copy() for tid, task in fota_tasks.items()}
    
    # 为每个服务器添加FOTA状态
    for server in servers_data:
        server_name = server.get("server_name", server.get("name", ""))
        server_ip = server.get("server_ip", server.get("ip", ""))
        
        if not server_name or not server_ip:
            server["fota_status_22"] = None
            server["fota_status_9999"] = None
            continue
        
        fota_status_22 = None
        fota_status_9999 = None
        
        for port in [22, 9999]:
            server_key = f"{server_name}:{server_ip}:{port}"
            if server_key in server_locks_copy:
                task_id = server_locks_copy[server_key]
                task_info = tasks_copy.get(task_id)
                if task_info and task_info.get("status") not in ("done", "error", "cancelled"):
                    status = task_info.get("status", "unknown")
                    step = task_info.get("step", "")
                    progress = task_info.get("progress", 0)
                    if port == 22:
                        fota_status_22 = {
                            "status": status,
                            "step": step,
                            "progress": progress,
                            "task_id": task_id
                        }
                    else:
                        fota_status_9999 = {
                            "status": status,
                            "step": step,
                            "progress": progress,
                            "task_id": task_id
                        }
        
        server["fota_status_22"] = fota_status_22
        server["fota_status_9999"] = fota_status_9999
    
    payload = {
        "timestamp": status_cache.get("timestamp", 0),
        "servers": servers_data,
        "error": status_cache.get("error", ""),
        "upload_target_dir": upload_target_dir,
        "fota_target_dir": fota_target_dir,
    }
    return jsonify(payload)

