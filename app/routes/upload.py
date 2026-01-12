"""
上传相关路由模块
"""
from flask import Blueprint, jsonify, request, Response, stream_with_context
import threading
import uuid
import os
import tempfile
import shutil
import json
import time
import multiprocessing
import sys

from app.utils.config import upload_target_dir
from app.api.upload_service import get_upload_service

# 从 app.utils.helpers 导入 log_srv
from app.utils.helpers import log_srv

bp = Blueprint('upload', __name__, url_prefix='/api')

# 获取上传服务实例
upload_service = get_upload_service()

# 从 app.extensions 导入批量上传管理器
from app.extensions import get_batch_upload_manager

# 获取批量上传管理器（延迟初始化）
def _get_batch_upload_resources():
    """获取批量上传资源"""
    manager, tasks_dict, lock = get_batch_upload_manager()
    return manager, tasks_dict, lock

# 为了兼容性，提供直接访问接口
def get_batch_upload_tasks_dict():
    """获取批量上传任务字典"""
    _, tasks_dict, _ = _get_batch_upload_resources()
    return tasks_dict

def get_batch_upload_tasks_lock():
    """获取批量上传任务锁"""
    _, _, lock = _get_batch_upload_resources()
    return lock

# 兼容性变量（延迟初始化）
batch_upload_tasks_dict = None
batch_upload_tasks_lock = None

def _init_batch_upload_vars():
    """初始化批量上传变量（延迟初始化）"""
    global batch_upload_tasks_dict, batch_upload_tasks_lock
    if batch_upload_tasks_dict is None:
        batch_upload_tasks_dict = get_batch_upload_tasks_dict()
        batch_upload_tasks_lock = get_batch_upload_tasks_lock()


@bp.route("/upload", methods=["POST"])
def api_upload():
    """单文件上传"""
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = int(request.form.get("port", 0))
        target_dir = request.form.get("target_dir", "").strip() or upload_target_dir
        file = request.files.get("file")

        if not server_name or not server_ip or port not in (22, 9999):
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        # 使用上传服务创建任务
        file_stream = file.stream
        file_size = request.content_length
        
        try:
            task_id = upload_service.create_upload_task(
                server_name=server_name,
                server_ip=server_ip,
                port=port,
                target_dir=target_dir,
                filename=file.filename,
                file_stream=file_stream,
                file_size=file_size
            )
        except Exception as e:
            log_srv(f"创建上传任务失败: {e}")
            return jsonify({"ok": False, "error": f"创建任务失败: {e}"}), 500
        
        # 在后台线程中执行上传
        threading.Thread(target=upload_service.execute_upload, args=(task_id,), daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_srv(f"upload exception: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@bp.route("/upload/progress/<task_id>")
def api_upload_progress(task_id):
    """SSE 推送上传进度"""
    def generate():
        last_progress = -1
        import time as time_module
        last_send_time = time_module.time()
        
        while True:
            task = upload_service.get_task(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task["progress"]
            current_status = task["status"]
            current_time = time_module.time()
            should_send = False
            
            # 进度变化时发送
            if current_progress != last_progress:
                should_send = True
            # 状态变化时发送
            elif current_status != "uploading":
                should_send = True
            # 保持SSE连接活跃，每0.5秒发送一次心跳（即使没有变化）
            elif current_time - last_send_time >= 0.5:
                should_send = True
            
            if should_send:
                yield f"data: {json.dumps({'progress': current_progress, 'status': current_status, 'result': task.get('result')})}\n\n"
                last_progress = current_progress
                last_send_time = current_time
            
            if current_status in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# 从 app.api.upload_service 导入批量上传进程函数
from app.api.upload_service import do_batch_upload_process

# 从 app.extensions 导入任务管理器（用于文件夹上传）
from app.extensions import get_task_managers
task_managers = get_task_managers()
upload_task_manager = task_managers['upload']

# 为了兼容性，提供直接访问接口（文件夹上传使用）
upload_tasks = upload_task_manager._tasks
upload_tasks_lock = upload_task_manager._lock

# 导入必要的模块
from core.sftp import sftp_upload, ensure_remote_dir
from core.ssh.transport import create_transport
import paramiko
import posixpath
import copy


@bp.route("/batch-upload", methods=["POST"])
def api_batch_upload():
    """批量上传接口"""
    try:
        port = int(request.form.get("port", 0))
        servers_json = request.form.get("servers", "[]")
        files = request.files.getlist("files")
        target_dir = request.form.get("target_dir", "").strip() or upload_target_dir

        if port not in (22, 9999):
            return jsonify({"ok": False, "error": "端口必须是22或9999"}), 400
        
        try:
            servers = json.loads(servers_json)
        except:
            return jsonify({"ok": False, "error": "服务器列表格式错误"}), 400
        
        if not servers or len(servers) == 0:
            return jsonify({"ok": False, "error": "服务器列表为空"}), 400
        
        if not files or len(files) == 0:
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        batch_id = str(uuid.uuid4())
        
        # 存储所有临时文件路径
        temp_files = []
        
        try:
            # 处理所有文件，保存到临时文件
            file_info_list = []
            total_temp_size = 0
            for file in files:
                if not file.filename:
                    continue
                    
                file_stream = file.stream
                temp_file = None
                temp_file_path = None
                try:
                    # 检查临时文件磁盘空间（如果知道文件大小）
                    file_size_from_request = request.content_length
                    if file_size_from_request:
                        try:
                            temp_dir = tempfile.gettempdir()
                            stat = shutil.disk_usage(temp_dir)
                            # 预留10%的缓冲空间
                            required_space = file_size_from_request * 1.1
                            if stat.free < required_space + total_temp_size:
                                raise Exception(f"临时文件磁盘空间不足，需要 {int(required_space + total_temp_size)} 字节，可用 {stat.free} 字节")
                        except ImportError:
                            # Python < 3.3 不支持 shutil.disk_usage，跳过检查
                            pass
                    
                    temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='batch_upload_', suffix='.tmp')
                    temp_file_path = temp_file.name
                    temp_files.append(temp_file_path)
                    
                    shutil.copyfileobj(file_stream, temp_file, length=8*1024*1024)
                    temp_file.close()
                    temp_file = None
                    
                    file_size = os.path.getsize(temp_file_path)
                    total_temp_size += file_size
                    file_info_list.append({
                        "filename": file.filename,
                        "temp_path": temp_file_path,
                        "file_size": file_size
                    })
                except Exception as e:
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
            
            if not file_info_list:
                return jsonify({"ok": False, "error": "没有有效的文件"}), 400
            
            # 初始化批量上传任务
            tasks_dict = {}
            task_list = []
            
            for server in servers:
                server_name = server.get("name", "")
                server_ip = server.get("ip", "")
                if not server_name or not server_ip:
                    continue
                
                for file_info in file_info_list:
                    task_id = str(uuid.uuid4())
                    task_list.append({
                        "task_id": task_id,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "filename": file_info["filename"]
                    })
                    
                    tasks_dict[task_id] = {
                        "progress": 0,
                        "status": "pending",
                        "result": None,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "filename": file_info["filename"]
                    }
            
            # 初始化multiprocessing.Manager（延迟初始化）
            manager, shared_dict, shared_lock = get_batch_upload_manager()
            
            # 存储批量任务信息（使用Manager的dict）
            with shared_lock:
                shared_dict[batch_id] = manager.dict({
                    "tasks": manager.dict(tasks_dict),
                    "temp_files": temp_files,
                    "file_info_list": file_info_list,
                    "target_dir": target_dir,
                    "port": port,
                    "cancelled": False
                })
                # 将tasks_dict中的每个任务也转换为Manager.dict
                for tid, task_data in tasks_dict.items():
                    shared_dict[batch_id]["tasks"][tid] = manager.dict(task_data)
            
            # 启动所有上传任务（使用进程）
            processes = []
            for task_info in task_list:
                task_id = task_info["task_id"]
                server_name = task_info["server_name"]
                server_ip = task_info["server_ip"]
                filename = task_info["filename"]
                
                # 找到对应的文件信息
                file_info = next((f for f in file_info_list if f["filename"] == filename), None)
                if not file_info:
                    continue
                
                # 使用进程启动上传任务
                p = multiprocessing.Process(
                    target=do_batch_upload_process,
                    args=(task_id, server_name, server_ip, filename, file_info, target_dir, port, batch_id, shared_dict, shared_lock),
                    daemon=False
                )
                p.start()
                processes.append(p)
            
            # 不等待进程完成，让它们独立运行
            # 进程会在完成后自动退出
            
            return jsonify({
                "ok": True,
                "batch_id": batch_id,
                "tasks": task_list
            })
        except Exception as e:
            # 清理临时文件
            for temp_path in temp_files:
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except:
                    pass
            raise
    except Exception as e:
        log_srv(f"batch upload exception: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@bp.route("/batch-upload/progress/<batch_id>")
def api_batch_upload_progress(batch_id):
    """SSE 推送批量上传进度"""
    def generate():
        last_tasks_state = None
        # 获取共享字典和锁
        manager, shared_dict, shared_lock = get_batch_upload_manager()
        
        while True:
            with shared_lock:
                batch_task = shared_dict.get(batch_id)
            
            if not batch_task:
                yield f"data: {json.dumps({'error': '批量上传任务不存在'})}\n\n"
                break
            
            # 检查是否已取消（Manager.dict需要特殊处理）
            cancelled = batch_task.get("cancelled", False) if isinstance(batch_task, dict) else False
            if cancelled:
                yield f"data: {json.dumps({'error': '批量上传任务已取消'})}\n\n"
                break
            
            # 获取任务字典（Manager.dict需要转换为普通dict以便序列化）
            tasks = batch_task.get("tasks", {})
            if isinstance(tasks, type(shared_dict)):  # 如果是Manager.dict，需要转换为普通dict
                tasks = dict(tasks)
            current_tasks_state = {}
            all_done = True
            has_error = False
            success_count = 0
            error_count = 0
            total_count = len(tasks)
            
            # 汇总错误信息
            error_summary = []
            
            for task_id, task_info in tasks.items():
                # 如果task_info是Manager.dict，需要转换为普通dict
                if isinstance(task_info, type(shared_dict)):
                    task_info = dict(task_info)
                
                status = task_info.get("status", "pending")
                result = task_info.get("result")
                
                current_tasks_state[task_id] = {
                    "progress": task_info.get("progress", 0),
                    "status": status,
                    "result": result,
                    "server_name": task_info.get("server_name", ""),
                    "server_ip": task_info.get("server_ip", ""),
                    "filename": task_info.get("filename", "")
                }
                
                if status not in ("done", "error"):
                    all_done = False
                elif status == "error":
                    has_error = True
                    error_count += 1
                    if result and result.get("error"):
                        error_summary.append({
                            "server": f"{task_info.get('server_name', '')} ({task_info.get('server_ip', '')})",
                            "filename": task_info.get("filename", ""),
                            "error": result.get("error")
                        })
                elif status == "done":
                    success_count += 1
            
            # 强制每次循环都发送更新，确保前端能及时收到进度变化
            import time as time_module
            current_time = time_module.time()
            
            # 检查是否有实际变化（用于标记心跳）
            is_heartbeat = False
            if last_tasks_state is not None:
                # 简单比较：如果状态完全相同，则标记为心跳
                state_changed = False
                if len(current_tasks_state) != len(last_tasks_state):
                    state_changed = True
                else:
                    for task_id in current_tasks_state:
                        if task_id not in last_tasks_state:
                            state_changed = True
                            break
                        current_task = current_tasks_state[task_id]
                        last_task = last_tasks_state[task_id]
                        if (current_task.get("progress", 0) != last_task.get("progress", 0) or
                            current_task.get("status", "pending") != last_task.get("status", "pending")):
                            state_changed = True
                            break
                is_heartbeat = not state_changed
            
            # 强制发送：每次循环都发送更新，不管是否有变化
            # 这样可以确保multiprocessing.Manager.dict的更新能及时传递到前端
            summary = {
                "tasks": current_tasks_state,
                "all_done": all_done,
                "has_error": has_error,
                "success_count": success_count,
                "error_count": error_count,
                "total_count": total_count,
                "heartbeat": is_heartbeat  # 标记是否为心跳更新
            }
            if error_summary:
                summary["error_summary"] = error_summary
            yield f"data: {json.dumps(summary)}\n\n"
            # 深拷贝保存状态，避免引用问题
            last_tasks_state = copy.deepcopy(current_tasks_state)
            if not hasattr(generate, '_last_send_time'):
                generate._last_send_time = current_time
            generate._last_send_time = current_time
            
            if all_done:
                # **关键改进：发送多次完成确认，确保前端收到**
                # 方案1：发送明确的完成事件类型
                final_summary = {
                    "tasks": current_tasks_state,
                    "all_done": True,
                    "has_error": has_error,
                    "success_count": success_count,
                    "error_count": error_count,
                    "total_count": total_count,
                    "status": "completed",  # 明确的完成状态
                    "message": "所有文件上传完成",
                    "final": True  # 标记为最终消息
                }
                if error_summary:
                    final_summary["error_summary"] = error_summary
                
                # 发送3次完成确认，确保消息送达
                for i in range(3):
                    # 使用明确的event类型
                    yield f"event: complete\ndata: {json.dumps(final_summary)}\n\n"
                    # 同时发送普通消息（兼容性）
                    yield f"data: {json.dumps(final_summary)}\n\n"
                    if i < 2:  # 最后一次不需要延迟
                        time.sleep(0.05)  # 小延迟确保顺序
                
                # 清理临时文件
                temp_files = batch_task.get("temp_files", [])
                if isinstance(temp_files, list):
                    for temp_path in temp_files:
                        try:
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                        except:
                            pass
                break
            
            # 使用极短的检查间隔（0.1秒），确保能及时检测到multiprocessing.Manager.dict的更新
            # 这样可以最大程度减少进程间通信延迟对前端显示的影响
            time.sleep(0.1)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@bp.route("/batch-upload/status/<batch_id>")
def api_batch_upload_status(batch_id):
    """查询批量上传任务状态（用于前端主动查询）"""
    try:
        manager, shared_dict, shared_lock = get_batch_upload_manager()
        with shared_lock:
            batch_task = shared_dict.get(batch_id)
        
        if not batch_task:
            return jsonify({"error": "批量上传任务不存在"}), 404
        
        # 检查是否已取消
        cancelled = batch_task.get("cancelled", False) if isinstance(batch_task, dict) else False
        if cancelled:
            return jsonify({
                "batch_id": batch_id,
                "status": "cancelled",
                "message": "批量上传任务已取消"
            })
        
        # 获取任务字典
        tasks = batch_task.get("tasks", {})
        if isinstance(tasks, type(shared_dict)):
            tasks = dict(tasks)
        
        all_done = True
        has_error = False
        success_count = 0
        error_count = 0
        total_count = len(tasks)
        tasks_info = {}
        
        for task_id, task_info in tasks.items():
            if isinstance(task_info, type(shared_dict)):
                task_info = dict(task_info)
            
            status = task_info.get("status", "pending")
            result = task_info.get("result")
            
            tasks_info[task_id] = {
                "progress": task_info.get("progress", 0),
                "status": status,
                "result": result,
                "server_name": task_info.get("server_name", ""),
                "server_ip": task_info.get("server_ip", ""),
                "filename": task_info.get("filename", "")
            }
            
            if status not in ("done", "error"):
                all_done = False
            elif status == "error":
                has_error = True
                error_count += 1
            elif status == "done":
                success_count += 1
        
        return jsonify({
            "batch_id": batch_id,
            "status": "completed" if all_done else "running",
            "all_done": all_done,
            "has_error": has_error,
            "success_count": success_count,
            "error_count": error_count,
            "total_count": total_count,
            "tasks": tasks_info
        })
    except Exception as e:
        log_srv(f"查询批量上传状态异常: {e}")
        return jsonify({"error": f"服务器异常: {e}"}), 500


@bp.route("/batch-upload/cancel/<batch_id>", methods=["POST"])
def api_batch_upload_cancel(batch_id):
    """取消批量上传任务"""
    try:
        manager, shared_dict, shared_lock = get_batch_upload_manager()
        with shared_lock:
            batch_task = shared_dict.get(batch_id)
            
            if not batch_task:
                return jsonify({"ok": False, "error": "批量上传任务不存在"}), 404
            
            if batch_task.get("cancelled", False):
                return jsonify({"ok": False, "error": "任务已取消"}), 400
            
            # 设置取消标志
            batch_task["cancelled"] = True
            
            # 清理临时文件
            temp_files = batch_task.get("temp_files", [])
            for temp_path in temp_files:
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except:
                    pass
            
            return jsonify({"ok": True, "message": "批量上传任务已取消"})
    except Exception as e:
        log_srv(f"取消批量上传异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@bp.route("/upload-folder", methods=["POST"])
def api_upload_folder():
    """多文件/文件夹上传接口 - 支持保持文件夹结构或平铺上传"""
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = int(request.form.get("port", 0))
        target_dir = request.form.get("target_dir", "").strip() or upload_target_dir
        preserve_structure = request.form.get("preserve_structure", "false").lower() == "true"
        files = request.files.getlist("files")

        if not server_name or not server_ip or port not in (22, 9999):
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not files or len(files) == 0:
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        task_id = str(uuid.uuid4())
        
        
        # 关键修复：在主线程中（请求处理期间）先读取所有文件数据并保存到临时文件
        # 这样后台线程就不需要访问 Flask 的流（流在请求结束后会被关闭）
        file_list = []
        temp_files_to_cleanup = []
        
        try:
            for file in files:
                if not file.filename:
                    continue
                    
                if preserve_structure:
                    # 文件夹模式：保持相对路径结构
                    relative_path = file.filename  # 包含相对路径，如 "folder/subfolder/file.txt"
                    filename = os.path.basename(file.filename)
                else:
                    # 多文件模式：所有文件平铺到目标目录
                    relative_path = os.path.basename(file.filename)  # 只有文件名
                    filename = os.path.basename(file.filename)
                
                
                # 在主线程中读取文件数据并保存到临时文件
                temp_file = None
                temp_file_path = None
                try:
                    temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='upload_folder_', suffix='.tmp')
                    temp_file_path = temp_file.name
                    temp_files_to_cleanup.append(temp_file_path)
                    
                    # 使用 file.read() 读取所有数据（在主线程中，流还未关闭）
                    file_data = file.read()
                    temp_file.write(file_data)
                    temp_file.close()
                    temp_file = None
                    
                    file_size = os.path.getsize(temp_file_path)
                    
                    
                    file_list.append({
                        "temp_file_path": temp_file_path,
                        "relative_path": relative_path,
                        "filename": filename,
                        "file_size": file_size
                    })
                except Exception as e:
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
            
            if not file_list:
                return jsonify({"ok": False, "error": "没有有效的文件"}), 400
            
            with upload_tasks_lock:
                upload_tasks[task_id] = {
                    "progress": 0,
                    "status": "uploading",
                    "result": None,
                    "file_index": 0,
                    "total_files": len(file_list),
                    "success_count": 0,
                    "fail_count": 0,
                    "current_file": ""
                }
        except Exception as e:
            # 清理已创建的临时文件
            for temp_path in temp_files_to_cleanup:
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except:
                    pass
            log_srv(f"准备文件时出错: {e}")
            return jsonify({"ok": False, "error": f"准备文件失败: {e}"}), 500
        
        def do_upload_folder():
            success_count = 0
            fail_count = 0
            total_files = len(file_list)
            
            
            try:
                for idx, file_info in enumerate(file_list):
                    # 从主线程准备好的数据中获取信息（不再访问 Flask 的流）
                    temp_file_path = file_info["temp_file_path"]
                    relative_path = file_info["relative_path"]
                    filename = file_info["filename"]
                    file_size = file_info["file_size"]
                    
                    
                    # 更新当前文件信息
                    with upload_tasks_lock:
                        if task_id in upload_tasks:
                            upload_tasks[task_id]["file_index"] = idx + 1
                            upload_tasks[task_id]["current_file"] = relative_path
                    
                    # 构建目标路径：target_dir + 相对路径
                    # 例如：target_dir="/tmp", relative_path="folder/sub/file.txt"
                    # 结果："/tmp/folder/sub/file.txt"
                    target_path = posixpath.join(target_dir, relative_path)
                    target_path = posixpath.normpath(target_path)  # 规范化路径
                    
                    # 确保目标目录存在
                    target_file_dir = posixpath.dirname(target_path)
                    
                    file_obj = None
                    
                    try:
                        # 直接打开主线程准备好的临时文件（不再访问 Flask 的流）
                        if not os.path.exists(temp_file_path):
                            raise FileNotFoundError(f"临时文件不存在: {temp_file_path}")
                        
                        # 验证文件大小
                        actual_file_size = os.path.getsize(temp_file_path)
                        if actual_file_size != file_size:
                            # 使用实际文件大小
                            file_size = actual_file_size
                        
                        file_obj = open(temp_file_path, 'rb')
                        
                        
                        # 确保文件对象位置在开头
                        if hasattr(file_obj, 'seek'):
                            file_obj.seek(0)
                        
                        
                        # 创建SFTP连接并确保目录存在
                        transport = create_transport(server_name, server_ip, port)
                        sftp = paramiko.SFTPClient.from_transport(transport)
                        
                        try:
                            # 确保目标目录存在
                            ensure_remote_dir(sftp, target_file_dir)
                            
                            # 上传文件
                            def progress_cb(loaded, total):
                                # 计算整体进度：已完成文件 + 当前文件进度
                                file_progress = (loaded / total) if total > 0 else 0
                                overall_progress = int(((idx + file_progress) / total_files) * 100)
                                with upload_tasks_lock:
                                    if task_id in upload_tasks:
                                        upload_tasks[task_id]["progress"] = overall_progress
                            
                            ok, info = sftp_upload(
                                server_name, server_ip, port,
                                target_file_dir, filename,
                                stream=file_obj, file_size=file_size,
                                progress_callback=progress_cb,
                                check_disk_space=True,
                                check_file_exists=True
                            )
                            
                            
                            if ok:
                                success_count += 1
                            else:
                                fail_count += 1
                                log_srv(f"文件夹上传失败 [{relative_path}]: {info}")
                        finally:
                            sftp.close()
                            transport.close()
                    except Exception as e:
                        fail_count += 1
                        log_srv(f"文件夹上传异常 [{relative_path}]: {e}")
                    finally:
                        if file_obj:
                            try:
                                file_obj.close()
                            except:
                                pass
                        if temp_file_path and os.path.exists(temp_file_path):
                            try:
                                os.unlink(temp_file_path)
                            except:
                                pass
                
                # 清理所有临时文件
                for file_info in file_list:
                    temp_file_path = file_info.get("temp_file_path")
                    if temp_file_path and os.path.exists(temp_file_path):
                        try:
                            os.unlink(temp_file_path)
                        except Exception:
                            pass
                
                # 更新最终结果
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "done",
                        "result": {
                            "ok": True,
                            "success_count": success_count,
                            "fail_count": fail_count,
                            "total_files": total_files
                        },
                        "file_index": total_files,
                        "total_files": total_files,
                        "success_count": success_count,
                        "fail_count": fail_count,
                        "current_file": ""
                    }
            except Exception as e:
                
                # 清理所有临时文件
                for file_info in file_list:
                    temp_file_path = file_info.get("temp_file_path")
                    if temp_file_path and os.path.exists(temp_file_path):
                        try:
                            os.unlink(temp_file_path)
                        except Exception:
                            pass
                
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "error",
                        "result": {"ok": False, "error": str(e)},
                        "file_index": 0,
                        "total_files": total_files,
                        "success_count": success_count,
                        "fail_count": fail_count,
                        "current_file": ""
                    }
                log_srv(f"文件夹上传异常: {e}")
        
        threading.Thread(target=do_upload_folder, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_srv(f"upload-folder exception: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@bp.route("/upload-folder/progress/<task_id>")
def api_upload_folder_progress(task_id):
    """SSE 推送文件夹上传进度"""
    def generate():
        last_progress = -1
        import time as time_module
        last_send_time = time_module.time()
        
        while True:
            with upload_tasks_lock:
                task = upload_tasks.get(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task.get("progress", 0)
            status = task.get("status", "uploading")
            result = task.get("result")
            file_index = task.get("file_index", 0)
            total_files = task.get("total_files", 0)
            current_file = task.get("current_file", "")
            
            # 如果状态改变或进度改变，发送更新
            current_time = time_module.time()
            should_send = (
                current_progress != last_progress or
                (current_time - last_send_time) >= 1.0  # 至少每秒发送一次
            )
            
            if should_send:
                data = {
                    "progress": current_progress,
                    "status": status,
                    "file_index": file_index,
                    "total_files": total_files,
                    "current_file": current_file
                }
                
                if status == "done":
                    data["result"] = result
                    yield f"data: {json.dumps(data)}\n\n"
                    break
                elif status == "error":
                    data["result"] = result
                    yield f"data: {json.dumps(data)}\n\n"
                    break
                else:
                    yield f"data: {json.dumps(data)}\n\n"
                
                last_progress = current_progress
                last_send_time = current_time
            
            time_module.sleep(0.3)
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
