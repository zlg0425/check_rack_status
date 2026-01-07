"""
下载服务模块
处理文件下载的业务逻辑
"""

import threading
from typing import Optional, Dict, Any

from core.sftp import sftp_download

# 从 app.utils.helpers 导入 log_srv
from app.utils.helpers import log_srv


class DownloadService:
    """下载服务类"""
    
    def __init__(self):
        """初始化下载服务"""
        self.download_tasks: Dict[str, Dict[str, Any]] = {}
        self.download_tasks_lock = threading.Lock()
    
    def execute_download(
        self,
        server_name: str,
        server_ip: str,
        port: int,
        remote_path: str,
        local_path: Optional[str] = None,
        progress_callback: Optional[callable] = None
    ) -> tuple[bool, str]:
        """
        执行下载任务
        
        Args:
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
            remote_path: 远程文件路径
            local_path: 本地保存路径（可选）
            progress_callback: 进度回调函数
            
        Returns:
            (success, message): 成功标志和消息
        """
        try:
            ok, info = sftp_download(
                server_name, server_ip, port,
                remote_path, local_path or "",
                progress_callback=progress_callback
            )
            
            if ok:
                return True, info if isinstance(info, str) else str(info)
            else:
                return False, info if isinstance(info, str) else str(info)
        except Exception as e:
            log_srv(f"download exception: {e}")
            return False, str(e)


# 全局服务实例
_download_service = None
_download_service_lock = threading.Lock()


def get_download_service() -> DownloadService:
    """获取下载服务实例（单例模式）"""
    global _download_service
    if _download_service is None:
        with _download_service_lock:
            if _download_service is None:
                _download_service = DownloadService()
    return _download_service

