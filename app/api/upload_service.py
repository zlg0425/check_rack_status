"""
上传服务模块
处理文件上传的业务逻辑
"""

import os
import tempfile
import shutil
import threading
import uuid
from typing import Optional, Callable, Dict, Any

from app.utils.config import upload_target_dir
from core.sftp import sftp_upload

# 从 app.utils.helpers 导入 log_srv
from app.utils.helpers import log_srv


class UploadService:
    """上传服务类"""
    
    def __init__(self):
        """初始化上传服务"""
        # #region agent log
        import json
        import time as time_module
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "UPLOAD_SERVICE_INIT",
                    "location": "app/api/upload_service.py:UploadService.__init__",
                    "message": "上传服务初始化",
                    "data": {},
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        self.upload_tasks: Dict[str, Dict[str, Any]] = {}
        self.upload_tasks_lock = threading.Lock()
    
    def create_upload_task(
        self,
        server_name: str,
        server_ip: str,
        port: int,
        target_dir: str,
        filename: str,
        file_stream,
        file_size: Optional[int] = None
    ) -> str:
        """
        创建上传任务
        
        Args:
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
            target_dir: 目标目录
            filename: 文件名
            file_stream: 文件流对象
            file_size: 文件大小（字节）
            
        Returns:
            task_id: 任务ID
        """
        task_id = str(uuid.uuid4())
        
        # 创建临时文件
        temp_file = None
        temp_file_path = None
        
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='upload_', suffix='.tmp')
            temp_file_path = temp_file.name
            
            # 流式保存到临时文件
            shutil.copyfileobj(file_stream, temp_file, length=8*1024*1024)  # 8MB缓冲区
            temp_file.close()
            temp_file = None
            
            # 如果file_size未知，从临时文件获取
            if file_size is None:
                file_size = os.path.getsize(temp_file_path)
        except Exception as e:
            # 清理临时文件
            if temp_file:
                try:
                    temp_file.close()
                except:
                    pass
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except:
                    pass
            raise
        
        # 初始化任务状态
        with self.upload_tasks_lock:
            self.upload_tasks[task_id] = {
                "progress": 0,
                "status": "uploading",
                "result": None,
                "temp_file_path": temp_file_path,
                "server_name": server_name,
                "server_ip": server_ip,
                "port": port,
                "target_dir": target_dir,
                "filename": filename,
                "file_size": file_size
            }
        
        return task_id
    
    def execute_upload(self, task_id: str) -> None:
        """
        执行上传任务
        
        Args:
            task_id: 任务ID
        """
        with self.upload_tasks_lock:
            task = self.upload_tasks.get(task_id)
            if not task:
                return
        
        temp_file_path = task["temp_file_path"]
        server_name = task["server_name"]
        server_ip = task["server_ip"]
        port = task["port"]
        target_dir = task["target_dir"]
        filename = task["filename"]
        file_size = task["file_size"]
        
        file_obj = None
        try:
            # 从临时文件打开文件对象用于上传
            file_obj = open(temp_file_path, 'rb')
            
            # 进度回调
            def progress_cb(loaded, total):
                percent = int((loaded / total) * 100) if total > 0 else 0
                with self.upload_tasks_lock:
                    if task_id in self.upload_tasks:
                        self.upload_tasks[task_id]["progress"] = percent
            
            # 执行上传
            ok, info = sftp_upload(
                server_name, server_ip, port, target_dir, filename,
                stream=file_obj, file_size=file_size,
                progress_callback=progress_cb,
                check_disk_space=True,
                check_file_exists=True
            )
            
            # 处理上传结果
            result = {}
            if ok:
                if isinstance(info, dict) and info.get("exists"):
                    result = {
                        "ok": True,
                        "path": info.get("path"),
                        "exists": True,
                        "message": "文件已存在，已覆盖"
                    }
                else:
                    result = {
                        "ok": True,
                        "path": info if isinstance(info, str) else info.get("path") if isinstance(info, dict) else None
                    }
            else:
                result = {
                    "ok": False,
                    "error": info if isinstance(info, str) else str(info)
                }
            
            # 更新任务状态
            with self.upload_tasks_lock:
                if task_id in self.upload_tasks:
                    self.upload_tasks[task_id].update({
                        "progress": 100,
                        "status": "done" if ok else "error",
                        "result": result
                    })
        except Exception as e:
            with self.upload_tasks_lock:
                if task_id in self.upload_tasks:
                    self.upload_tasks[task_id].update({
                        "progress": 100,
                        "status": "error",
                        "result": {"ok": False, "error": str(e)}
                    })
            log_srv(f"upload exception: {e}")
        finally:
            # 关闭文件对象
            if file_obj:
                try:
                    file_obj.close()
                except:
                    pass
            # 清理临时文件
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception as cleanup_error:
                    log_srv(f"清理临时文件失败: {cleanup_error}")
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务信息
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务信息字典，如果不存在则返回None
        """
        with self.upload_tasks_lock:
            return self.upload_tasks.get(task_id)
    
    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有任务
        
        Returns:
            所有任务的字典
        """
        with self.upload_tasks_lock:
            return self.upload_tasks.copy()


def do_batch_upload_process(task_id, server_name, server_ip, filename, file_info, target_dir, port, batch_id, shared_dict, shared_lock):
    """独立的进程函数，执行单个上传任务
    
    从 web_ui.py 迁移而来
    用于批量上传的多进程处理
    
    Args:
        task_id: 任务ID
        server_name: 服务器名称
        server_ip: 服务器IP
        filename: 文件名
        file_info: 文件信息字典（包含temp_path和file_size）
        target_dir: 目标目录
        port: 端口
        batch_id: 批量任务ID
        shared_dict: 共享字典（multiprocessing.Manager.dict）
        shared_lock: 共享锁（multiprocessing.Manager.Lock）
    """
    import sys
    import os
    import json
    import time
    
    # 重新导入必要的模块（子进程需要）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from core.sftp import sftp_upload
    from app.utils.config import load_config
    
    # 子进程中必须加载配置，否则resolve_key等函数无法工作
    load_config()
    
    try:
        def progress_cb(loaded, total):
            try:
                # 检查是否已取消
                with shared_lock:
                    if batch_id in shared_dict:
                        if shared_dict[batch_id].get("cancelled", False):
                            raise Exception("任务已取消")
                
                percent = int((loaded / total) * 100) if total > 0 else 0
                with shared_lock:
                    if batch_id in shared_dict:
                        if shared_dict[batch_id].get("cancelled", False):
                            raise Exception("任务已取消")
                        if task_id in shared_dict[batch_id]["tasks"]:
                            shared_dict[batch_id]["tasks"][task_id]["progress"] = percent
                            current_status = shared_dict[batch_id]["tasks"][task_id].get("status", "pending")
                            if current_status not in ("done", "error", "cancelled"):
                                shared_dict[batch_id]["tasks"][task_id]["status"] = "uploading"
            except Exception as e:
                # 只有明确的取消异常才重新抛出
                if "任务已取消" in str(e) or "取消" in str(e):
                    raise
                # 其他异常忽略，继续上传
                pass
        
        # 再次检查是否已取消
        with shared_lock:
            if batch_id in shared_dict:
                if shared_dict[batch_id].get("cancelled", False):
                    raise Exception("任务已取消")
        
        file_obj = open(file_info["temp_path"], 'rb')
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "BATCH_UPLOAD_FILE_OPENED",
                    "location": "app/api/upload_service.py:do_batch_upload_process:file_opened",
                    "message": "批量上传文件对象已打开",
                    "data": {
                        "task_id": task_id,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "filename": filename,
                        "temp_path": file_info["temp_path"],
                        "file_size": file_info["file_size"],
                        "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown",
                        "file_obj_seekable": file_obj.seekable() if hasattr(file_obj, 'seekable') else "unknown"
                    },
                    "timestamp": int(time.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 确保文件对象位置在开头
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "BATCH_UPLOAD_BEFORE_UPLOAD",
                    "location": "app/api/upload_service.py:do_batch_upload_process:before_upload",
                    "message": "准备调用sftp_upload",
                    "data": {
                        "task_id": task_id,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "filename": filename,
                        "file_size": file_info["file_size"],
                        "file_obj_tell_after_seek": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown"
                    },
                    "timestamp": int(time.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        try:
            ok, info = sftp_upload(
                server_name, server_ip, port, target_dir, filename,
                stream=file_obj, file_size=file_info["file_size"],
                progress_callback=progress_cb,
                check_disk_space=True,
                check_file_exists=True
            )
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "BATCH_UPLOAD_AFTER_UPLOAD",
                        "location": "app/api/upload_service.py:do_batch_upload_process:after_upload",
                        "message": "sftp_upload完成",
                        "data": {
                            "task_id": task_id,
                            "server_name": server_name,
                            "server_ip": server_ip,
                            "filename": filename,
                            "ok": ok,
                            "info": str(info) if info else None
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            # 处理上传结果
            result = {}
            if ok:
                if isinstance(info, dict) and info.get("exists"):
                    result = {
                        "ok": True,
                        "path": info.get("path"),
                        "exists": True,
                        "message": "文件已存在，已覆盖"
                    }
                else:
                    result = {
                        "ok": True,
                        "path": info if isinstance(info, str) else info.get("path") if isinstance(info, dict) else None
                    }
            else:
                result = {
                    "ok": False,
                    "error": info if isinstance(info, str) else str(info)
                }
            
            with shared_lock:
                if batch_id in shared_dict:
                    if task_id in shared_dict[batch_id]["tasks"]:
                        shared_dict[batch_id]["tasks"][task_id]["progress"] = 100
                        shared_dict[batch_id]["tasks"][task_id]["status"] = "done" if ok else "error"
                        shared_dict[batch_id]["tasks"][task_id]["result"] = result
        finally:
            file_obj.close()
    except Exception as e:
        error_msg = str(e)
        with shared_lock:
            if batch_id in shared_dict:
                if shared_dict[batch_id].get("cancelled", False) and "取消" in error_msg:
                    # 任务已取消，标记为取消状态
                    if task_id in shared_dict[batch_id]["tasks"]:
                        shared_dict[batch_id]["tasks"][task_id]["progress"] = 0
                        shared_dict[batch_id]["tasks"][task_id]["status"] = "cancelled"
                        shared_dict[batch_id]["tasks"][task_id]["result"] = {
                            "ok": False,
                            "error": "任务已取消"
                        }
                else:
                    if task_id in shared_dict[batch_id]["tasks"]:
                        shared_dict[batch_id]["tasks"][task_id]["progress"] = 100
                        shared_dict[batch_id]["tasks"][task_id]["status"] = "error"
                        shared_dict[batch_id]["tasks"][task_id]["result"] = {
                            "ok": False,
                            "error": error_msg
                        }


# 全局服务实例
_upload_service = None
_upload_service_lock = threading.Lock()


def get_upload_service() -> UploadService:
    """获取上传服务实例（单例模式）"""
    global _upload_service
    if _upload_service is None:
        with _upload_service_lock:
            if _upload_service is None:
                _upload_service = UploadService()
    return _upload_service

