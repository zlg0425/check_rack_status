"""
FOTA 服务模块
处理 FOTA 升级的业务逻辑
"""

import threading
import uuid
from typing import Optional, Dict, Any, Tuple

from app.utils.config import fota_target_dir, fota_filename_validation
from core.sftp import (
    sftp_upload,
    remote_md5,
    remote_exists,
    remote_remove,
)
from app.utils.helpers import (
    md5_stream,
    create_md5_calculating_stream,
    create_tee_stream,
    run_ucm_with_log,
)

# 从 app.utils.helpers 导入日志函数
from app.utils.helpers import log_fota

# 从 core.fota 导入 FOTA 相关函数
from core.fota import record_fota_timing, get_avg_fota_timing

# 从 app.extensions 导入任务管理器
from app.extensions import get_task_managers

# 获取任务管理器
task_managers = get_task_managers()
fota_task_manager = task_managers['fota']
batch_fota_task_manager = task_managers['batch_fota']
fota_server_lock_manager = task_managers['fota_server_lock']
fota_transport_manager = task_managers['fota_transport']

# 为了兼容性，提供直接访问接口
fota_tasks = fota_task_manager._tasks
fota_tasks_lock = fota_task_manager._lock
fota_server_locks = fota_server_lock_manager._locks
fota_server_locks_lock = fota_server_lock_manager._lock
batch_fota_tasks = batch_fota_task_manager._tasks
batch_fota_tasks_lock = batch_fota_task_manager._lock
fota_transports = fota_transport_manager._transports
fota_transports_lock = fota_transport_manager._lock

# detect_fota_port 已从 core.fota 导入
# sftp_upload_with_cancel 已迁移到 core.sftp.operations
# #region agent log
import json
import time as time_module
try:
    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
        f.write(json.dumps({
            "sessionId": "system",
            "runId": "run1",
            "hypothesisId": "FOTA_SERVICE_IMPORT",
            "location": "app/api/fota_service.py:import:sftp_upload_with_cancel",
            "message": "导入sftp_upload_with_cancel",
            "data": {},
            "timestamp": int(time_module.time() * 1000)
        }) + '\n')
except Exception:
    pass
# #endregion
try:
    from core.sftp.operations import sftp_upload_with_cancel
    # #region agent log
    try:
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": "system",
                "runId": "run1",
                "hypothesisId": "FOTA_SERVICE_IMPORT",
                "location": "app/api/fota_service.py:import:sftp_upload_with_cancel:success",
                "message": "sftp_upload_with_cancel导入成功",
                "data": {},
                "timestamp": int(time_module.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
except ImportError as e:
    # #region agent log
    try:
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": "system",
                "runId": "run1",
                "hypothesisId": "FOTA_SERVICE_IMPORT",
                "location": "app/api/fota_service.py:import:sftp_upload_with_cancel:failed",
                "message": "sftp_upload_with_cancel导入失败",
                "data": {"error": str(e)},
                "timestamp": int(time_module.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
    raise


class FOTAService:
    """FOTA 服务类"""
    
    def __init__(self):
        """初始化 FOTA 服务"""
        pass
    
    def detect_port(self, server_name: str, filename: str) -> Tuple[int, str]:
        """
        检测 FOTA 端口
        
        Args:
            server_name: 服务器名称
            filename: 文件名
            
        Returns:
            (port, message): 端口号和消息
        """
        return detect_fota_port(server_name, filename)
    
    def validate_filename(self, filename: str) -> bool:
        """
        验证文件名
        
        Args:
            filename: 文件名
            
        Returns:
            是否有效
        """
        if not fota_filename_validation:
            return True
        
        # 遍历所有服务器类型的所有端口配置，检查文件名是否包含任何有效的前缀
        for server_type, port_config in fota_filename_validation.items():
            for port_str, prefix in port_config.items():
                if prefix in filename:
                    return True
        
        return False
    
    def check_server_busy(self, server_name: str, server_ip: str, port: int) -> Optional[str]:
        """
        检查服务器是否正在执行 FOTA 任务
        
        Args:
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
            
        Returns:
            如果忙碌则返回任务ID，否则返回None
        """
        server_key = f"{server_name}:{server_ip}:{port}"
        with fota_server_locks_lock:
            if server_key in fota_server_locks:
                existing_task = fota_server_locks[server_key]
                with fota_tasks_lock:
                    existing_task_info = fota_tasks.get(existing_task)
                    if existing_task_info and existing_task_info["status"] not in ("done", "error"):
                        return existing_task
        return None
    
    def create_task(self, server_name: str, server_ip: str, port: int) -> str:
        """
        创建 FOTA 任务
        
        Args:
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
            
        Returns:
            task_id: 任务ID
        """
        task_id = str(uuid.uuid4())
        server_key = f"{server_name}:{server_ip}:{port}"
        
        with fota_server_locks_lock:
            fota_server_locks[server_key] = task_id
        
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务信息
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务信息字典，如果不存在则返回None
        """
        with fota_tasks_lock:
            return fota_tasks.get(task_id)
    
    def update_task(self, task_id: str, **kwargs) -> None:
        """
        更新任务信息
        
        Args:
            task_id: 任务ID
            **kwargs: 要更新的字段
        """
        with fota_tasks_lock:
            if task_id in fota_tasks:
                fota_tasks[task_id].update(kwargs)


# 全局服务实例
_fota_service = None
_fota_service_lock = threading.Lock()


def get_fota_service() -> FOTAService:
    """获取 FOTA 服务实例（单例模式）"""
    global _fota_service
    if _fota_service is None:
        with _fota_service_lock:
            if _fota_service is None:
                _fota_service = FOTAService()
    return _fota_service

