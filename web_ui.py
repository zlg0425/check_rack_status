#!/usr/bin/env python3
"""
基于 Flask 的简易前端，实时展示服务器端口与 SSH 状态。
运行：python web_ui.py
"""

import threading
import time
import uuid
import json
from flask import Flask, jsonify, render_template_string, request, Response, stream_with_context
from check_rack_status import (
    load_config,
    run_checks_once,
    check_interval,
    ssh_username,
    upload_target_dir,
    sftp_upload,
    fota_target_dir,
    md5_bytes,
    md5_stream,
    create_md5_calculating_stream,
    create_tee_stream,
    remote_md5,
    run_ucm_with_log,
    remote_exists,
    remote_remove,
    resolve_auth_mode,
    resolve_key,
    ssh_timeout,
    ensure_remote_dir,
    fota_filename_validation,
)
import paramiko
import socket
import posixpath
import os

app = Flask(__name__)

status_cache = {
    "data": [],
    "timestamp": 0,
    "error": "",
}
cache_lock = threading.Lock()
FOTA_LOG = "fota.log"
BACKEND_LOG = "backend.log"
FOTA_TIMING_STATS_FILE = "fota_timing_stats.json"

# 上传任务进度字典 {task_id: {"progress": 0-100, "status": "uploading|done|error", "result": {...}}}
upload_tasks = {}
upload_tasks_lock = threading.Lock()

# FOTA耗时统计锁
fota_timing_stats_lock = threading.Lock()

# FOTA任务进度字典 {task_id: {"progress": 0-100, "status": "checking|md5|uploading|upgrading|done|error", "step": "...", "result": {...}}}
fota_tasks = {}
fota_tasks_lock = threading.Lock()

# 服务器FOTA执行锁 {server_key: task_id}，防止同一服务器同时执行多个FOTA任务
fota_server_locks = {}
fota_server_locks_lock = threading.Lock()

# 批量FOTA任务字典 {batch_id: {"tasks": [task_id1, task_id2, ...], "status": "running|done|error|cancelled", "total": N, "completed": M, "cancelled": False}}
batch_fota_tasks = {}
batch_fota_tasks_lock = threading.Lock()

# FOTA任务transport字典 {task_id: {"transport": transport, "sftp": sftp}}，用于终止时关闭连接
fota_transports = {}
fota_transports_lock = threading.Lock()


def log_fota(message: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(FOTA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def log_srv(message: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(BACKEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def load_fota_timing_stats():
    """加载FOTA耗时统计"""
    try:
        if os.path.exists(FOTA_TIMING_STATS_FILE):
            with open(FOTA_TIMING_STATS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "md5_calculation": [],
        "file_upload": [],
        "lpucm_execution": []
    }


def save_fota_timing_stats(stats):
    """保存FOTA耗时统计"""
    try:
        with open(FOTA_TIMING_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def record_fota_timing(operation, duration_seconds, file_size_bytes=None):
    """记录FOTA操作耗时
    
    Args:
        operation: 操作类型 ("md5_calculation", "file_upload", "lpucm_execution")
        duration_seconds: 耗时（秒）
        file_size_bytes: 文件大小（字节），用于上传操作
    """
    with fota_timing_stats_lock:
        stats = load_fota_timing_stats()
        
        record = {
            "timestamp": time.time(),
            "duration": duration_seconds,
        }
        if file_size_bytes is not None:
            record["file_size"] = file_size_bytes
        
        stats[operation].append(record)
        
        # 只保留最近1000条记录
        if len(stats[operation]) > 1000:
            stats[operation] = stats[operation][-1000:]
        
        save_fota_timing_stats(stats)


def get_avg_fota_timing(operation, file_size_bytes=None):
    """获取FOTA操作的平均耗时（秒）
    
    Args:
        operation: 操作类型 ("md5_calculation", "file_upload", "lpucm_execution")
        file_size_bytes: 文件大小（字节），用于上传操作时按文件大小分组计算平均值
    
    Returns:
        平均耗时（秒），如果没有数据则返回None
    """
    with fota_timing_stats_lock:
        stats = load_fota_timing_stats()
        
        if operation not in stats or len(stats[operation]) == 0:
            return None
        
        records = stats[operation]
        
        # 对于上传操作，如果提供了文件大小，则只计算相似大小文件的平均值
        if operation == "file_upload" and file_size_bytes is not None:
            # 计算相似大小范围（±20%）
            size_min = file_size_bytes * 0.8
            size_max = file_size_bytes * 1.2
            filtered_records = [
                r for r in records
                if "file_size" in r and size_min <= r["file_size"] <= size_max
            ]
            if len(filtered_records) > 0:
                records = filtered_records
        
        if len(records) == 0:
            return None
        
        total_duration = sum(r["duration"] for r in records)
        return total_duration / len(records)


def sftp_upload_with_cancel(server_name: str, ip: str, port: int, target_dir: str, filename: str, data=None, progress_callback=None, batch_id=None, task_id=None, stream=None, file_size=None):
    """将文件上传到指定服务器和端口，支持进度回调和取消，返回(ok, info, transport, sftp)
    统一使用流式上传模式，支持stream参数（文件流）或data参数（bytes，内部转换为流）
    
    Args:
        server_name: 服务器名称
        ip: 服务器IP
        port: 端口
        target_dir: 目标目录
        filename: 文件名
        data: 文件数据（bytes），可选，如果提供则转换为流
        progress_callback: 进度回调函数
        batch_id: 批量任务ID（用于取消检查）
        task_id: 任务ID（用于保存transport引用）
        stream: 文件流对象，优先使用
        file_size: 文件大小（字节），必需
    """
    import io
    
    auth_mode = resolve_auth_mode(server_name)
    safe_dir = target_dir.rstrip("/") or "/"
    remote_path = posixpath.join(safe_dir, filename)
    
    # 统一为流式模式：优先使用stream，如果提供data则转换为流
    if stream is not None:
        # 流式模式
        if file_size is None:
            return False, "流式模式需要提供file_size参数", None, None
        total_size = file_size
        file_obj = stream
    elif data is not None:
        # 如果提供data参数，转换为流（向后兼容）
        total_size = len(data)
        file_obj = io.BytesIO(data)
        if file_size is not None and file_size != total_size:
            # 如果同时提供了file_size，使用file_size（可能更准确）
            total_size = file_size
    else:
        return False, "必须提供stream或data参数", None, None

    try:
        if auth_mode == "none":
            # 重新获取ssh_username，确保使用load_config()后的值
            from check_rack_status import ssh_username as current_ssh_username
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(current_ssh_username)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 确保目录存在
            ensure_remote_dir(sftp, safe_dir)
            
            # 保存transport引用以便终止时关闭
            if task_id:
                with fota_transports_lock:
                    fota_transports[task_id] = {"transport": transport, "sftp": sftp}
            
            # 使用putfo方法上传，支持流式上传
            import time as time_module
            putfo_start_time = time_module.time()
            putfo_last_log_time = putfo_start_time
            putfo_last_transferred = 0
            
            def putfo_progress_callback(transferred, total):
                nonlocal putfo_last_log_time, putfo_last_transferred
                if progress_callback:
                    try:
                        progress_callback(transferred, total)
                    except Exception as e:
                        if "任务已取消" in str(e):
                            raise
                        pass
                # 检查是否已取消
                if batch_id:
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            raise Exception("任务已取消")
                
                # 每5秒记录一次上传速度和进度
                current_time = time_module.time()
                if current_time - putfo_last_log_time >= 5.0:
                    elapsed = current_time - putfo_start_time
                    speed = transferred / elapsed if elapsed > 0 else 0
                    recent_speed = (transferred - putfo_last_transferred) / (current_time - putfo_last_log_time) if (current_time - putfo_last_log_time) > 0 else 0
                    putfo_last_log_time = current_time
                    putfo_last_transferred = transferred
            
            sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            
            # 上传完成后清理引用（但保持连接打开，因为可能还需要用于MD5校验）
            return True, remote_path, transport, sftp
        else:
            key_path = resolve_key(server_name, port)
            if not key_path:
                return False, f"端口{port}未配置私钥", None, None
            
            try:
                private_key = paramiko.RSAKey.from_private_key_file(key_path)
            except Exception as e:
                return False, f"无法加载密钥文件 {key_path}: {str(e)}", None, None
            
            transport = paramiko.Transport((ip, port))
            transport.start_client(timeout=ssh_timeout)
            # 重新获取ssh_username，确保使用load_config()后的值
            from check_rack_status import ssh_username as current_ssh_username
            try:
                transport.auth_publickey(username=current_ssh_username, key=private_key)
            except paramiko.AuthenticationException as e:
                if transport:
                    try:
                        transport.close()
                    except:
                        pass
                return False, f"认证失败: {str(e)}", None, None
            
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 确保目录存在
            ensure_remote_dir(sftp, safe_dir)
            
            # 保存transport引用以便终止时关闭
            if task_id:
                with fota_transports_lock:
                    fota_transports[task_id] = {"transport": transport, "sftp": sftp}
            
            # 使用putfo方法上传，支持流式上传
            import time as time_module
            putfo_start_time = time_module.time()
            putfo_last_log_time = putfo_start_time
            putfo_last_transferred = 0
            
            def putfo_progress_callback(transferred, total):
                nonlocal putfo_last_log_time, putfo_last_transferred
                if progress_callback:
                    try:
                        progress_callback(transferred, total)
                    except Exception as e:
                        if "任务已取消" in str(e):
                            raise
                        pass
                # 检查是否已取消
                if batch_id:
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            raise Exception("任务已取消")
                
                # 每5秒记录一次上传速度和进度
                current_time = time_module.time()
                if current_time - putfo_last_log_time >= 5.0:
                    elapsed = current_time - putfo_start_time
                    speed = transferred / elapsed if elapsed > 0 else 0
                    recent_speed = (transferred - putfo_last_transferred) / (current_time - putfo_last_log_time) if (current_time - putfo_last_log_time) > 0 else 0
                    putfo_last_log_time = current_time
                    putfo_last_transferred = transferred
            
            sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            
            return True, remote_path, transport, sftp
    except Exception as e:
        error_msg = str(e)
        # 清理资源
        if sftp:
            try:
                sftp.close()
            except:
                pass
        if transport:
            try:
                transport.close()
            except:
                pass
        # 清理transport引用
        if task_id:
            with fota_transports_lock:
                if task_id in fota_transports:
                    del fota_transports[task_id]
        
        if "任务已取消" in error_msg:
            return False, "任务已取消", None, None
        return False, error_msg, None, None


def refresh_loop():
    """后台循环刷新状态缓存"""
    while True:
        try:
            results = run_checks_once()
            with cache_lock:
                status_cache["data"] = results
                status_cache["timestamp"] = time.time()
                status_cache["error"] = ""
        except Exception as e:
            with cache_lock:
                status_cache["error"] = str(e)
            log_srv(f"refresh_loop error: {e}")
        time.sleep(max(5, check_interval))


@app.route("/api/status")
def api_status():
    with cache_lock:
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
            # 跳过无效的服务器数据
            server["fota_status_22"] = None
            server["fota_status_9999"] = None
            continue
        
        # 检查22和9999端口的FOTA状态
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
                # 如果任务已完成或已取消，清理锁（但不在API中清理，让任务完成后清理）
        
        server["fota_status_22"] = fota_status_22
        server["fota_status_9999"] = fota_status_9999
    
    payload = {
        "timestamp": status_cache["timestamp"],
        "servers": servers_data,
        "error": status_cache["error"],
        "upload_target_dir": upload_target_dir,
        "fota_target_dir": fota_target_dir,
    }
    return jsonify(payload)


@app.route("/api/upload", methods=["POST"])
def api_upload():
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

        task_id = str(uuid.uuid4())
        
        # 真正的流式上传：使用临时文件，避免将整个文件读入内存
        import io
        import tempfile
        import shutil
        
        file_stream = file.stream
        file_size = request.content_length
        
        # 创建临时文件（使用临时文件实现真正的流式处理）
        temp_file = None
        temp_file_path = None
        try:
            # 创建临时文件
            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='upload_', suffix='.tmp')
            temp_file_path = temp_file.name
            
            # 流式保存：从Flask流复制到临时文件（内存占用固定，如8MB缓冲区）
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
        
        with upload_tasks_lock:
            upload_tasks[task_id] = {"progress": 0, "status": "uploading", "result": None}
        
        def progress_cb(loaded, total):
            percent = int((loaded / total) * 100) if total > 0 else 0
            with upload_tasks_lock:
                if task_id in upload_tasks:
                    upload_tasks[task_id]["progress"] = percent
        
        def do_upload():
            file_obj = None
            try:
                # 从临时文件打开文件对象用于上传
                file_obj = open(temp_file_path, 'rb')
                ok, info = sftp_upload(server_name, server_ip, port, target_dir, file.filename, stream=file_obj, file_size=file_size, progress_callback=progress_cb)
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "done" if ok else "error",
                        "result": {"ok": ok, "path": info if ok else None, "error": info if not ok else None}
                    }
            except Exception as e:
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "error",
                        "result": {"ok": False, "error": str(e)}
                    }
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
        
        threading.Thread(target=do_upload, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_srv(f"upload exception: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/api/upload/progress/<task_id>")
def api_upload_progress(task_id):
    """SSE 推送上传进度"""
    def generate():
        last_progress = -1
        while True:
            with upload_tasks_lock:
                task = upload_tasks.get(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task["progress"]
            if current_progress != last_progress:
                yield f"data: {json.dumps({'progress': current_progress, 'status': task['status'], 'result': task['result']})}\n\n"
                last_progress = current_progress
            
            if task["status"] in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/fota", methods=["POST"])
def api_fota():
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = int(request.form.get("port", 0))
        file = request.files.get("file")

        if not server_name or not server_ip or port not in (22, 9999):
            log_fota(f"[{server_name}/{server_ip}:{port}] 参数缺失或端口非法")
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not file or file.filename == "":
            log_fota(f"[{server_name}/{server_ip}:{port}] 未选择文件")
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        filename = file.filename

        # 检查服务器是否已有正在执行的FOTA任务
        server_key = f"{server_name}:{server_ip}:{port}"
        with fota_server_locks_lock:
            if server_key in fota_server_locks:
                existing_task = fota_server_locks[server_key]
                # 检查现有任务是否还在执行中
                with fota_tasks_lock:
                    existing_task_info = fota_tasks.get(existing_task)
                    if existing_task_info and existing_task_info["status"] not in ("done", "error"):
                        log_fota(f"[{server_name}/{server_ip}:{port}] 服务器已有正在执行的FOTA任务: {existing_task}")
                        return jsonify({"ok": False, "error": f"服务器 {server_name} 已有正在执行的FOTA任务，请等待完成后再试"}), 409
            
            task_id = str(uuid.uuid4())
            fota_server_locks[server_key] = task_id

        # 真正的流式上传：使用临时文件，避免将整个文件读入内存
        import io
        import time as time_module
        import tempfile
        import shutil
        import os
        try:
            import psutil
            psutil_available = True
        except ImportError:
            psutil_available = False
        
        
        file_stream = file.stream
        file_size = request.content_length
        
        
        # 根据服务器类型选择不同的方案
        is_8650 = server_name.startswith("LP-8650")
        
        
        # 初始化变量
        read_duration = 0
        md5_duration = 0
        
        if is_8650:
            # 8650系列：使用临时文件方案（保存到临时文件时同时计算MD5）
            temp_file = None
            temp_file_path = None
            try:
                # 创建临时文件
                temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='fota_upload_', suffix='.tmp')
                temp_file_path = temp_file.name
                
                
                # 使用tee流，在保存到临时文件的同时计算MD5（只需读取一次）
                read_start_time = time_module.time()
                tee_stream, md5_calculator = create_tee_stream(file_stream, temp_file, server_name, file_size)
                
                
                # 流式保存：从Flask流复制到临时文件，同时计算MD5（内存占用固定，如8MB缓冲区）
                # 注意：tee_stream.read()已经将数据写入temp_file，不需要再次write
                bytes_copied = 0
                chunk_size = 8*1024*1024  # 8MB缓冲区
                while True:
                    chunk = tee_stream.read(chunk_size)
                    if not chunk:
                        break
                    # tee_stream.read()已经将chunk写入temp_file，不需要再次write
                    bytes_copied += len(chunk)
                
                
                # 获取MD5值（在保存过程中已计算）
                local_md5 = md5_calculator.get_md5()
                
                temp_file.close()
                temp_file = None
                read_duration = time_module.time() - read_start_time
                
                # 获取实际文件大小（从临时文件）
                actual_file_size = os.path.getsize(temp_file_path)
                
                
                # 检查文件是否完整读取
                if bytes_copied != file_size:
                    # 文件未完全读取，使用实际读取的字节数更新file_size（用于后续上传）
                    log_fota(f"[{server_name}/{server_ip}:{port}] 警告：文件未完全读取，期望={file_size}，实际={bytes_copied}，差异={file_size - bytes_copied}字节")
                    # 注意：MD5已经使用bytes_read计算，所以这里只需要更新file_size用于上传
                    file_size = bytes_copied  # 使用实际读取的字节数
                
                # 验证actual_file_size应该等于bytes_copied（数据只写入一次）
                if actual_file_size != bytes_copied:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 错误：临时文件大小异常，bytes_copied={bytes_copied}，actual_file_size={actual_file_size}，差异={actual_file_size - bytes_copied}字节")
                    # 使用bytes_copied作为实际文件大小
                    actual_file_size = bytes_copied
                
                
                md5_duration = read_duration  # MD5计算时间包含在读取时间内
                
                # 创建文件对象用于上传（从临时文件打开）
                file_obj = open(temp_file_path, 'rb')
                upload_stream = None  # 8650不使用边上传边计算
                upload_md5_stream = None  # 8650不使用边上传边计算
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
        else:
            # 8797系列：也使用临时文件方案，但边上传边计算MD5（Flask流只能读取一次）
            temp_file = None
            temp_file_path = None
            try:
                # 创建临时文件
                temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='fota_upload_', suffix='.tmp')
                temp_file_path = temp_file.name
                
                
                # 流式保存：从Flask流复制到临时文件（内存占用固定，如8MB缓冲区）
                read_start_time = time_module.time()
                bytes_copied = 0
                chunk_size = 8*1024*1024  # 8MB缓冲区
                while True:
                    chunk = file_stream.read(chunk_size)
                    if not chunk:
                        break
                    temp_file.write(chunk)
                    bytes_copied += len(chunk)
                
                temp_file.close()
                temp_file = None
                read_duration = time_module.time() - read_start_time
                
                # 如果file_size未知，从临时文件获取
                if file_size is None:
                    file_size = os.path.getsize(temp_file_path)
                
                
                # 创建文件对象用于上传（从临时文件打开）
                file_obj = open(temp_file_path, 'rb')
                # 创建MD5计算包装流，边上传边计算MD5
                upload_md5_stream = create_md5_calculating_stream(file_obj, server_name, file_size)
                upload_stream = upload_md5_stream  # 8797使用边上传边计算
                
                local_md5 = None  # 8797的MD5在上传完成后获取
                
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
        
        remote_path = f"{fota_target_dir.rstrip('/')}/{filename}"

        with fota_tasks_lock:
            fota_tasks[task_id] = {"progress": 0, "status": "checking", "step": "开始检查...", "result": None}

        def update_fota_progress(progress, status, step):
            # 获取预估耗时
            estimated_time = None
            if status == "md5":
                avg_md5 = get_avg_fota_timing("md5_calculation")
                if avg_md5:
                    estimated_time = f"（预估 {int(avg_md5 / 60)} 分钟）"
            elif status == "uploading":
                avg_upload = get_avg_fota_timing("file_upload", file_size)
                if avg_upload:
                    estimated_time = f"（预估 {int(avg_upload / 60)} 分钟）"
            elif status == "upgrading":
                avg_ucm = get_avg_fota_timing("lpucm_execution")
                if avg_ucm:
                    estimated_time = f"（预估 {int(avg_ucm / 60)} 分钟）"
            
            step_with_time = step + (estimated_time or "")
            with fota_tasks_lock:
                if task_id in fota_tasks:
                    fota_tasks[task_id]["progress"] = progress
                    fota_tasks[task_id]["status"] = status
                    fota_tasks[task_id]["step"] = step_with_time
                    # 如果是错误状态，设置result字段
                    if status == "error":
                        if fota_tasks[task_id].get("result") is None:
                            fota_tasks[task_id]["result"] = {"ok": False, "error": step}
                        else:
                            fota_tasks[task_id]["result"]["error"] = step

        def do_fota():
            try:
                # 初始化性能监控变量（使用nonlocal访问外层作用域的变量）
                nonlocal md5_duration, local_md5, file_obj, upload_stream, upload_md5_stream
                
                # 根据服务器类型记录不同的日志
                if is_8650:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 开始FOTA（8650临时文件方案），文件={filename}，本地MD5={local_md5}，文件大小={file_size}")
                else:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 开始FOTA（8797边上传边计算方案），文件={filename}，文件大小={file_size}")
                # 对于8650，local_md5已在保存临时文件时计算
                # 对于8797，local_md5在上传完成后计算
                # 如果文件不存在，md5_duration保持为本地MD5计算的耗时（8650）或0（8797）
                # 如果文件存在，会在后面更新为远端MD5计算的耗时

                # 验证文件名是否包含对应端口的正确值（在检查文件存在前）
                if fota_filename_validation:
                    # 确定服务器类型（LP-8650 或 LP-8797）
                    server_type = None
                    if server_name.startswith("LP-8650"):
                        server_type = "LP-8650"
                    elif server_name.startswith("LP-8797"):
                        server_type = "LP-8797"
                    if server_type and server_type in fota_filename_validation:
                        port_str = str(port)
                        if port_str in fota_filename_validation[server_type]:
                            expected_value = fota_filename_validation[server_type][port_str]
                            if expected_value not in filename:
                                error_msg = f"选择的文件不对，请选择对应文件：{expected_value}（{server_type} 端口{port}）"
                                log_fota(f"[{server_name}/{server_ip}:{port}] {error_msg}，当前文件名={filename}")
                                update_fota_progress(0, "error", error_msg)
                                return
                
                # 步骤1：检查文件是否存在 (0-10%)
                update_fota_progress(5, "checking", "检查远端文件是否存在...")
                file_exists = remote_exists(server_name, server_ip, port, remote_path)
                
                need_upload = True
                r_md5 = None  # 用于保存已获取的MD5值
                
                if file_exists:
                    update_fota_progress(10, "checking", "远端文件已存在")
                    # 步骤2：检验MD5 (10-30%)
                    update_fota_progress(15, "md5", "计算远端文件MD5...")
                    md5_start_time = time.time()
                    ok_md5_pre, r_md5_pre = remote_md5(server_name, server_ip, port, remote_path)
                    md5_duration = time.time() - md5_start_time  # 更新外层作用域的变量（已在函数开始处声明nonlocal）
                    record_fota_timing("md5_calculation", md5_duration)
                    if ok_md5_pre:
                            # 对于8797，如果文件存在，需要先上传才能计算本地MD5
                            # 所以8797在文件存在时，总是需要上传
                            if is_8650:
                                # 8650：直接比较本地MD5和远端MD5
                                update_fota_progress(25, "md5", f"MD5校验: 本地={local_md5[:8]}... 远端={r_md5_pre[:8]}...")
                                if r_md5_pre == local_md5:
                                    # MD5匹配，跳过上传，使用已获取的MD5值
                                    log_fota(f"[{server_name}/{server_ip}:{port}] 远端已存在且MD5一致，跳过上传，远端MD5={r_md5_pre}")
                                    need_upload = False
                                    r_md5 = r_md5_pre  # 保存已获取的MD5值
                                    update_fota_progress(30, "md5", f"MD5一致，跳过上传")
                                    # 跳过上传时，立即清理临时文件
                                    if 'file_obj' in locals() and file_obj:
                                        try:
                                            file_obj.close()
                                            file_obj = None
                                        except:
                                            pass
                                    if 'temp_file_path' in locals() and temp_file_path:
                                        try:
                                            if os.path.exists(temp_file_path):
                                                os.unlink(temp_file_path)
                                        except Exception as cleanup_error:
                                            log_srv(f"清理临时文件失败: {cleanup_error}")
                                else:
                                    # MD5不匹配，删除后上传
                                    log_fota(f"[{server_name}/{server_ip}:{port}] 远端已有同名文件，MD5不同，远端MD5={r_md5_pre}，删除后重传")
                                    update_fota_progress(28, "md5", "MD5不一致，删除旧文件...")
                                    remote_remove(server_name, server_ip, port, remote_path)
                                    need_upload = True
                            else:
                                # 8797：文件存在时，总是需要上传（因为需要边上传边计算MD5）
                                log_fota(f"[{server_name}/{server_ip}:{port}] 8797文件存在，需要上传以计算本地MD5")
                                update_fota_progress(28, "md5", "文件存在，需要上传...")
                                need_upload = True
                    else:
                        # MD5获取失败，删除后上传
                        log_fota(f"[{server_name}/{server_ip}:{port}] 远端已有同名文件，MD5获取失败: {r_md5_pre}，删除后重传")
                        update_fota_progress(28, "md5", "MD5获取失败，删除旧文件...")
                        remote_remove(server_name, server_ip, port, remote_path)
                        need_upload = True
                else:
                    update_fota_progress(10, "checking", "远端文件不存在，需要上传")
                    # 文件不存在时，md5_duration保持为0（已在函数开始时初始化）

                # 步骤3：上传文件（如果需要）(30-80%)
                if need_upload:
                    def upload_progress_cb(loaded, total):
                        # 上传进度从30%到80%
                        percent = 30 + int((loaded / total) * 50) if total > 0 else 30
                        update_fota_progress(percent, "uploading", f"上传中... {int((loaded/total)*100) if total > 0 else 0}%")
                    
                    update_fota_progress(30, "uploading", "开始上传文件...")
                    upload_start_time = time.time()
                    
                    # 根据服务器类型选择不同的上传方式
                    if is_8650:
                        # 8650：从临时文件上传
                        file_obj.seek(0)
                        ok, info = sftp_upload(server_name, server_ip, port, fota_target_dir, filename, stream=file_obj, file_size=file_size, progress_callback=upload_progress_cb)
                    else:
                        # 8797：使用边上传边计算MD5的流上传（从临时文件）
                        # 重置文件对象位置，然后创建MD5计算流
                        file_obj.seek(0)
                        upload_md5_stream = create_md5_calculating_stream(file_obj, server_name, file_size)
                        upload_stream = upload_md5_stream
                        ok, info = sftp_upload(server_name, server_ip, port, fota_target_dir, filename, stream=upload_stream, file_size=file_size, progress_callback=upload_progress_cb)
                        # 上传完成后获取MD5值
                        local_md5 = upload_md5_stream.get_md5()
                        log_fota(f"[{server_name}/{server_ip}:{port}] 上传完成，本地MD5={local_md5}")
                    upload_duration = time.time() - upload_start_time
                    record_fota_timing("file_upload", upload_duration, file_size)
                    
                    
                    # 关闭文件对象（8650和8797都需要，因为都使用了临时文件）
                    if 'file_obj' in locals() and file_obj:
                        try:
                            file_obj.close()
                            file_obj = None
                        except:
                            pass
                    
                    if not ok:
                        log_fota(f"[{server_name}/{server_ip}:{port}] SCP失败: {info}")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"SCP失败: {info}", "result": {"ok": False, "error": f"SCP失败: {info}"}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return

                    # 步骤4：上传后MD5校验 (80-90%)
                    update_fota_progress(80, "md5", "上传完成，计算远端MD5...")
                    md5_start_time = time.time()
                    ok_md5, r_md5 = remote_md5(server_name, server_ip, port, remote_path)
                    md5_duration = time.time() - md5_start_time
                    record_fota_timing("md5_calculation", md5_duration)
                    if not ok_md5:
                        log_fota(f"[{server_name}/{server_ip}:{port}] 远端MD5失败: {r_md5}")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"远端MD5失败: {r_md5}", "result": {"ok": False, "error": f"远端MD5失败: {r_md5}"}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return

                    log_fota(f"[{server_name}/{server_ip}:{port}] 上传后MD5校验，本地={local_md5} 远端={r_md5}")
                    update_fota_progress(85, "md5", f"MD5校验: 本地={local_md5[:8]}... 远端={r_md5[:8]}...")
                    

                    if local_md5 != r_md5:
                        log_fota(f"[{server_name}/{server_ip}:{port}] MD5不一致，本地={local_md5} 远端={r_md5}")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": "上传文件不完整&升级失败", "result": {"ok": False, "error": "上传文件不完整&升级失败", "local_md5": local_md5, "remote_md5": r_md5}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return
                    
                    update_fota_progress(90, "md5", "MD5校验通过")
                else:
                    # 如果跳过了上传，MD5已经在步骤2中校验通过
                    update_fota_progress(90, "md5", "MD5校验通过")

                # 步骤5：升级执行阶段 (90-100%)
                cancelled = update_fota_progress(90, "upgrading", f"开始执行 lpUCM -i {remote_path}")
                if cancelled:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 任务已取消")
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                    return
                log_fota(f"[{server_name}/{server_ip}:{port}] MD5一致，开始执行 lpUCM -i {remote_path}")

                ucm_start_time = time.time()
                ok_ucm, info_ucm = run_ucm_with_log(server_name, server_ip, port, remote_path)
                ucm_duration = time.time() - ucm_start_time
                record_fota_timing("lpucm_execution", ucm_duration)
                if not ok_ucm:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 升级失败: {info_ucm}")
                    with fota_tasks_lock:
                        fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"升级失败: {info_ucm}", "result": {"ok": False, "error": f"升级失败: {info_ucm}", "local_md5": local_md5, "remote_md5": r_md5}}
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                    return

                log_fota(f"[{server_name}/{server_ip}:{port}] 升级成功，远端={remote_path} MD5={r_md5}")
                with fota_tasks_lock:
                    fota_tasks[task_id] = {"progress": 100, "status": "done", "step": "升级成功", "result": {"ok": True, "path": remote_path, "local_md5": local_md5, "remote_md5": r_md5, "ucm_output": info_ucm}}
            except Exception as e:
                log_fota(f"FOTA异常: {e}")
                log_srv(f"FOTA异常: {e}")
                with fota_tasks_lock:
                    fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"服务器异常: {e}", "result": {"ok": False, "error": f"服务器异常: {e}"}}
            finally:
                # 清理临时文件和流（8650和8797都使用临时文件）
                if 'file_obj' in locals() and file_obj:
                    try:
                        file_obj.close()
                    except:
                        pass
                if 'upload_md5_stream' in locals() and upload_md5_stream:
                    try:
                        upload_md5_stream.close()
                    except:
                        pass
                if 'temp_file_path' in locals() and temp_file_path:
                    try:
                        if os.path.exists(temp_file_path):
                            os.unlink(temp_file_path)
                    except Exception as cleanup_error:
                        log_srv(f"清理临时文件失败: {cleanup_error}")
                # 释放服务器锁
                with fota_server_locks_lock:
                    if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                        del fota_server_locks[server_key]

        threading.Thread(target=do_fota, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_fota(f"FOTA异常: {e}")
        log_srv(f"FOTA异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/api/fota/progress/<task_id>")
def api_fota_progress(task_id):
    """SSE 推送FOTA进度"""
    def generate():
        last_progress = -1
        while True:
            with fota_tasks_lock:
                task = fota_tasks.get(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task["progress"]
            if current_progress != last_progress or task["status"] != "uploading":
                yield f"data: {json.dumps({'progress': current_progress, 'status': task['status'], 'step': task['step'], 'result': task['result']})}\n\n"
                last_progress = current_progress
            
            if task["status"] in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/batch-fota", methods=["POST"])
def api_batch_fota():
    """批量FOTA接口"""
    try:
        port = int(request.form.get("port", 0))
        servers_json = request.form.get("servers", "[]")
        file = request.files.get("file")

        if port not in (22, 9999):
            return jsonify({"ok": False, "error": "端口必须是22或9999"}), 400
        
        try:
            servers = json.loads(servers_json)
        except:
            return jsonify({"ok": False, "error": "服务器列表格式错误"}), 400
        
        if not servers or len(servers) == 0:
            return jsonify({"ok": False, "error": "服务器列表为空"}), 400
        
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        filename = file.filename

        # 真正的流式上传：使用临时文件，避免将整个文件读入内存
        import io
        import tempfile
        import shutil
        
        file_stream = file.stream
        file_size = request.content_length
        
        # 创建临时文件（使用临时文件实现真正的流式处理）
        temp_file = None
        temp_file_path = None
        try:
            # 创建临时文件
            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix='batch_fota_upload_', suffix='.tmp')
            temp_file_path = temp_file.name
            
            # 流式保存：从Flask流复制到临时文件（内存占用固定，如8MB缓冲区）
            shutil.copyfileobj(file_stream, temp_file, length=8*1024*1024)  # 8MB缓冲区
            temp_file.close()
            temp_file = None
            
            # 获取实际文件大小（从临时文件）
            actual_file_size = os.path.getsize(temp_file_path)
            
            # 如果file_size未知，使用实际文件大小
            if file_size is None:
                file_size = actual_file_size
            else:
                # 如果file_size与实际文件大小不一致，使用实际文件大小（用于MD5计算）
                if file_size != actual_file_size:
                    log_fota(f"[批量FOTA] 警告：文件大小不一致，期望={file_size}，实际={actual_file_size}，使用实际大小计算MD5")
                    file_size = actual_file_size  # 使用实际文件大小
            
            # 批量FOTA时，需要为每个服务器单独计算MD5（因为可能有8650和8797混合）
            # 这里先计算一个通用的MD5，实际使用时会在每个任务中根据服务器类型重新计算
            # 但为了兼容性，先使用第一个服务器的类型
            first_server_name = servers[0].get("name", "") if servers else ""
            # 从临时文件计算MD5（支持seek），使用实际文件大小
            with open(temp_file_path, 'rb') as temp_file_obj:
                local_md5 = md5_stream(temp_file_obj, first_server_name, file_size)
            remote_path = f"{fota_target_dir.rstrip('/')}/{filename}"
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

        # 创建批量任务
        batch_id = str(uuid.uuid4())
        task_ids = []

        with batch_fota_tasks_lock:
            batch_fota_tasks[batch_id] = {
                "tasks": [],
                "status": "running",
                "total": len(servers),
                "completed": 0,
                "port": port,
                "filename": filename,
                "cancelled": False,
                "temp_file_path": temp_file_path  # 保存临时文件路径，用于后续清理
            }

        # 为每个服务器创建FOTA任务
        for server in servers:
            server_name = server.get("name", "").strip()
            server_ip = server.get("ip", "").strip()
            
            if not server_name or not server_ip:
                continue

            server_key = f"{server_name}:{server_ip}:{port}"
            
            # 检查服务器是否已有正在执行的FOTA任务
            with fota_server_locks_lock:
                if server_key in fota_server_locks:
                    existing_task = fota_server_locks[server_key]
                    with fota_tasks_lock:
                        existing_task_info = fota_tasks.get(existing_task)
                        if existing_task_info and existing_task_info["status"] not in ("done", "error", "cancelled"):
                            log_fota(f"[批量FOTA] 服务器 {server_name} 已有正在执行的FOTA任务，跳过")
                            continue
                        # 如果任务已完成或已取消，清理旧的锁
                        elif existing_task_info and existing_task_info["status"] in ("done", "error", "cancelled"):
                            del fota_server_locks[server_key]
                
                task_id = str(uuid.uuid4())
                fota_server_locks[server_key] = task_id
                task_ids.append({"task_id": task_id, "server_key": server_key, "server_name": server_name, "server_ip": server_ip})

            # 初始化FOTA任务
            with fota_tasks_lock:
                fota_tasks[task_id] = {
                    "progress": 0,
                    "status": "checking",
                    "step": "等待开始...",
                    "result": None,
                    "server_key": server_key,
                    "batch_id": batch_id
                }

            # 启动FOTA任务
            def do_batch_fota(tid, sname, sip, skey):
                transport = None
                sftp = None
                try:
                    # 检查是否已取消
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                    
                    # 根据服务器类型重新计算本地MD5（确保与远端MD5计算方式一致）
                    # 获取实际文件大小（从临时文件）
                    actual_file_size = os.path.getsize(temp_file_path)
                    # 使用实际文件大小计算MD5（确保与远端MD5计算一致）
                    with open(temp_file_path, 'rb') as temp_file_obj:
                        server_local_md5 = md5_stream(temp_file_obj, sname, actual_file_size)
                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 开始FOTA，文件={filename}，本地MD5={server_local_md5}，文件大小={actual_file_size}（实际大小）")

                    # 验证文件名是否包含对应端口的正确值（在检查文件存在前）
                    if fota_filename_validation:
                        # 确定服务器类型（LP-8650 或 LP-8797）
                        server_type = None
                        if sname.startswith("LP-8650"):
                            server_type = "LP-8650"
                        elif sname.startswith("LP-8797"):
                            server_type = "LP-8797"
                        if server_type and server_type in fota_filename_validation:
                            port_str = str(port)
                            if port_str in fota_filename_validation[server_type]:
                                expected_value = fota_filename_validation[server_type][port_str]
                                if expected_value not in filename:
                                    error_msg = f"选择的文件不对，请选择对应文件：{expected_value}（{server_type} 端口{port}）"
                                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] {error_msg}，当前文件名={filename}")
                                    with fota_tasks_lock:
                                        if tid in fota_tasks:
                                            fota_tasks[tid]["progress"] = 0
                                            fota_tasks[tid]["status"] = "error"
                                            fota_tasks[tid]["step"] = error_msg
                                    return

                    def update_fota_progress(progress, status, step):
                        # 获取预估耗时
                        estimated_time = None
                        if status == "md5":
                            avg_md5 = get_avg_fota_timing("md5_calculation")
                            if avg_md5:
                                estimated_time = f"（预估 {int(avg_md5 / 60)} 分钟）"
                        elif status == "uploading":
                            avg_upload = get_avg_fota_timing("file_upload", file_size)
                            if avg_upload:
                                estimated_time = f"（预估 {int(avg_upload / 60)} 分钟）"
                        elif status == "upgrading":
                            avg_ucm = get_avg_fota_timing("lpucm_execution")
                            if avg_ucm:
                                estimated_time = f"（预估 {int(avg_ucm / 60)} 分钟）"
                        
                        step_with_time = step + (estimated_time or "")
                        with fota_tasks_lock:
                            if tid in fota_tasks:
                                fota_tasks[tid]["progress"] = progress
                                fota_tasks[tid]["status"] = status
                                fota_tasks[tid]["step"] = step_with_time
                        # 检查是否已取消
                        with batch_fota_tasks_lock:
                            if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                                return True  # 返回True表示已取消
                        return False

                    # 步骤1：检查文件是否存在 (0-10%)
                    cancelled = update_fota_progress(5, "checking", "检查远端文件是否存在...")
                    if cancelled:
                        log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                        return
                    
                    file_exists = remote_exists(sname, sip, port, remote_path)
                    need_upload = True
                    r_md5 = None  # 用于保存已获取的MD5值
                    
                    if file_exists:
                        cancelled = update_fota_progress(10, "checking", "远端文件已存在")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                        
                        # 步骤2：检验MD5 (10-30%)
                        cancelled = update_fota_progress(15, "md5", "计算远端文件MD5...")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                        
                        md5_start_time = time.time()
                        ok_md5_pre, r_md5_pre = remote_md5(sname, sip, port, remote_path)
                        md5_duration = time.time() - md5_start_time
                        record_fota_timing("md5_calculation", md5_duration)
                        
                        if ok_md5_pre:
                            cancelled = update_fota_progress(25, "md5", f"MD5校验: 本地={server_local_md5[:8]}... 远端={r_md5_pre[:8]}...")
                            if cancelled:
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                return
                            
                            if r_md5_pre == server_local_md5:
                                # MD5匹配，跳过上传，使用已获取的MD5值
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 远端已存在且MD5一致，跳过上传，远端MD5={r_md5_pre}")
                                need_upload = False
                                r_md5 = r_md5_pre  # 保存已获取的MD5值
                                cancelled = update_fota_progress(30, "md5", f"MD5一致，跳过上传")
                                if cancelled:
                                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                    return
                            else:
                                # MD5不匹配，删除后上传
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 远端已有同名文件，MD5不同，远端MD5={r_md5_pre}，删除后重传")
                                cancelled = update_fota_progress(28, "md5", "MD5不一致，删除旧文件...")
                                if cancelled:
                                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                    return
                                remote_remove(sname, sip, port, remote_path)
                                need_upload = True
                        else:
                            # MD5获取失败，删除后上传
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 远端已有同名文件，MD5获取失败: {r_md5_pre}，删除后重传")
                            cancelled = update_fota_progress(28, "md5", "MD5获取失败，删除旧文件...")
                            if cancelled:
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                return
                            remote_remove(sname, sip, port, remote_path)
                            need_upload = True
                    else:
                        cancelled = update_fota_progress(10, "checking", "远端文件不存在，需要上传")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return

                    # 检查是否已取消
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return

                    # 步骤3：上传文件（如果需要）(30-80%)
                    if need_upload:
                        def upload_progress_cb(loaded, total):
                            # 上传进度从30%到80%
                            percent = 30 + int((loaded / total) * 50) if total > 0 else 30
                            cancelled = update_fota_progress(percent, "uploading", f"上传中... {int((loaded/total)*100) if total > 0 else 0}%")
                            if cancelled:
                                raise Exception("任务已取消")
                        
                        cancelled = update_fota_progress(30, "uploading", "开始上传文件...")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                        upload_start_time = time.time()
                        # 从临时文件打开文件对象用于上传（每个任务都需要独立的文件对象）
                        # 使用实际文件大小上传（确保与MD5计算一致）
                        task_file_obj = open(temp_file_path, 'rb')
                        try:
                            ok, info, transport_ref, sftp_ref = sftp_upload_with_cancel(
                                sname, sip, port, fota_target_dir, filename, stream=task_file_obj, file_size=actual_file_size, progress_callback=upload_progress_cb, batch_id=batch_id, task_id=tid
                            )
                        finally:
                            task_file_obj.close()
                        upload_duration = time.time() - upload_start_time
                        record_fota_timing("file_upload", upload_duration, file_size)
                        if transport_ref:
                            transport = transport_ref
                        if sftp_ref:
                            sftp = sftp_ref
                        
                        # 检查是否已取消
                        with batch_fota_tasks_lock:
                            if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                return
                        
                        if not ok:
                            error_msg = "任务已取消" if "任务已取消" in str(info) else f"SCP失败: {info}"
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] {error_msg}")
                            with fota_tasks_lock:
                                fota_tasks[tid] = {
                                    "progress": 100,
                                    "status": "cancelled" if "任务已取消" in str(info) else "error",
                                    "step": error_msg,
                                    "result": {"ok": False, "error": error_msg},
                                    "server_key": skey,
                                    "batch_id": batch_id
                                }
                            with fota_server_locks_lock:
                                if skey in fota_server_locks and fota_server_locks[skey] == tid:
                                    del fota_server_locks[skey]
                            # 更新批量任务进度
                            with batch_fota_tasks_lock:
                                if batch_id in batch_fota_tasks:
                                    batch_fota_tasks[batch_id]["completed"] += 1
                            # 清理transport引用
                            with fota_transports_lock:
                                if tid in fota_transports:
                                    del fota_transports[tid]
                            return
                        
                        # 上传成功，关闭sftp和transport（因为后续操作会重新创建连接）
                        if sftp:
                            try:
                                sftp.close()
                            except:
                                pass
                        if transport:
                            try:
                                transport.close()
                            except:
                                pass
                        # 清理transport引用
                        with fota_transports_lock:
                            if tid in fota_transports:
                                del fota_transports[tid]
                        transport = None
                        sftp = None

                        # 步骤4：上传后MD5校验 (80-90%)
                        cancelled = update_fota_progress(80, "md5", "上传完成，计算远端MD5...")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                        
                        # 检查是否已取消
                        with batch_fota_tasks_lock:
                            if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                return
                        
                        # 等待文件完全写入磁盘（特别是8650系列使用SFTP读取时）
                        time.sleep(1.0)  # 等待1秒确保文件完全写入
                        
                        md5_start_time = time.time()
                        ok_md5, r_md5 = remote_md5(sname, sip, port, remote_path)
                        md5_duration = time.time() - md5_start_time
                        record_fota_timing("md5_calculation", md5_duration)
                        
                        
                        # 检查是否已取消
                        with batch_fota_tasks_lock:
                            if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                                log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                                return
                        
                        if not ok_md5:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 远端MD5失败: {r_md5}")
                            with fota_tasks_lock:
                                fota_tasks[tid] = {
                                    "progress": 100,
                                    "status": "error",
                                    "step": f"远端MD5失败: {r_md5}",
                                    "result": {"ok": False, "error": f"远端MD5失败: {r_md5}"},
                                    "server_key": skey,
                                    "batch_id": batch_id
                                }
                            with fota_server_locks_lock:
                                if skey in fota_server_locks and fota_server_locks[skey] == tid:
                                    del fota_server_locks[skey]
                            with batch_fota_tasks_lock:
                                if batch_id in batch_fota_tasks:
                                    batch_fota_tasks[batch_id]["completed"] += 1
                            return

                        log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 上传后MD5校验，本地={server_local_md5} 远端={r_md5}")
                        cancelled = update_fota_progress(85, "md5", f"MD5校验: 本地={server_local_md5[:8]}... 远端={r_md5[:8]}...")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                        
                        cancelled = update_fota_progress(90, "md5", "MD5校验通过")
                        if cancelled:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return

                        if server_local_md5 != r_md5:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] MD5不一致")
                            with fota_tasks_lock:
                                fota_tasks[tid] = {
                                    "progress": 100,
                                    "status": "error",
                                    "step": "上传文件不完整&升级失败",
                                    "result": {"ok": False, "error": "上传文件不完整&升级失败", "local_md5": server_local_md5, "remote_md5": r_md5},
                                    "server_key": skey,
                                    "batch_id": batch_id
                                }
                            with fota_server_locks_lock:
                                if skey in fota_server_locks and fota_server_locks[skey] == tid:
                                    del fota_server_locks[skey]
                            with batch_fota_tasks_lock:
                                if batch_id in batch_fota_tasks:
                                    batch_fota_tasks[batch_id]["completed"] += 1
                            return

                    # 检查是否已取消
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return

                    # 步骤5：升级执行阶段 (90-100%)
                    cancelled = update_fota_progress(90, "upgrading", f"开始执行 lpUCM -i {remote_path}")
                    if cancelled:
                        log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                        return
                    
                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] MD5一致，开始执行 lpUCM -i {remote_path}")

                    ucm_start_time = time.time()
                    ok_ucm, info_ucm = run_ucm_with_log(sname, sip, port, remote_path)
                    ucm_duration = time.time() - ucm_start_time
                    record_fota_timing("lpucm_execution", ucm_duration)
                    
                    # 检查是否已取消
                    with batch_fota_tasks_lock:
                        if batch_id not in batch_fota_tasks or batch_fota_tasks[batch_id].get("cancelled", False):
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 任务已取消")
                            return
                    
                    if not ok_ucm:
                        log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 升级失败: {info_ucm}")
                        with fota_tasks_lock:
                            fota_tasks[tid] = {
                                "progress": 100,
                                "status": "error",
                                "step": f"升级失败: {info_ucm}",
                                "result": {"ok": False, "error": f"升级失败: {info_ucm}", "local_md5": server_local_md5, "remote_md5": r_md5},
                                "server_key": skey,
                                "batch_id": batch_id
                            }
                        with fota_server_locks_lock:
                            if skey in fota_server_locks and fota_server_locks[skey] == tid:
                                del fota_server_locks[skey]
                        with batch_fota_tasks_lock:
                            if batch_id in batch_fota_tasks:
                                batch_fota_tasks[batch_id]["completed"] += 1
                        return

                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 升级成功")
                    with fota_tasks_lock:
                            fota_tasks[tid] = {
                                "progress": 100,
                                "status": "done",
                                "step": "升级成功",
                                "result": {"ok": True, "path": remote_path, "local_md5": server_local_md5, "remote_md5": r_md5, "ucm_output": info_ucm},
                                "server_key": skey,
                                "batch_id": batch_id
                            }
                except Exception as e:
                    error_msg = str(e)
                    is_cancelled = "任务已取消" in error_msg
                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 异常: {e}")
                    log_srv(f"[批量FOTA/{batch_id}] 异常: {e}")
                    with fota_tasks_lock:
                        if tid in fota_tasks:
                            fota_tasks[tid]["status"] = "cancelled" if is_cancelled else "error"
                            fota_tasks[tid]["step"] = error_msg
                            fota_tasks[tid]["result"] = {"ok": False, "error": error_msg}
                finally:
                    # 关闭transport和sftp连接
                    with fota_transports_lock:
                        if tid in fota_transports:
                            transport_info = fota_transports[tid]
                            if transport_info.get("sftp"):
                                try:
                                    transport_info["sftp"].close()
                                except:
                                    pass
                            if transport_info.get("transport"):
                                try:
                                    transport_info["transport"].close()
                                except:
                                    pass
                            del fota_transports[tid]
                    
                    # 手动关闭可能残留的连接
                    if sftp:
                        try:
                            sftp.close()
                        except:
                            pass
                    if transport:
                        try:
                            transport.close()
                        except:
                            pass
                    # 释放服务器锁
                    with fota_server_locks_lock:
                        if skey in fota_server_locks and fota_server_locks[skey] == tid:
                            del fota_server_locks[skey]
                    # 更新批量任务进度
                    with batch_fota_tasks_lock:
                        if batch_id in batch_fota_tasks:
                            batch_fota_tasks[batch_id]["completed"] += 1
                            # 检查是否所有任务都完成
                            if batch_fota_tasks[batch_id]["completed"] >= batch_fota_tasks[batch_id]["total"]:
                                # 检查是否有失败或取消的任务
                                all_done = True
                                has_error = False
                                has_cancelled = False
                                with fota_tasks_lock:
                                    for task_info in task_ids:
                                        tid = task_info["task_id"]
                                        if tid in fota_tasks:
                                            task_status = fota_tasks[tid]["status"]
                                            if task_status not in ("done", "error", "cancelled"):
                                                all_done = False
                                            if task_status == "error":
                                                has_error = True
                                            if task_status == "cancelled":
                                                has_cancelled = True
                                
                                if all_done:
                                    if has_cancelled or batch_fota_tasks[batch_id].get("cancelled", False):
                                        batch_fota_tasks[batch_id]["status"] = "cancelled"
                                    elif has_error:
                                        batch_fota_tasks[batch_id]["status"] = "error"
                                    else:
                                        batch_fota_tasks[batch_id]["status"] = "done"
                                    # 清理临时文件（所有任务完成后）
                                    temp_path = batch_fota_tasks[batch_id].get("temp_file_path")
                                    if temp_path and os.path.exists(temp_path):
                                        try:
                                            os.unlink(temp_path)
                                            log_srv(f"[批量FOTA] 清理临时文件: {temp_path}")
                                        except Exception as cleanup_error:
                                            log_srv(f"[批量FOTA] 清理临时文件失败: {cleanup_error}")

            threading.Thread(target=do_batch_fota, args=(task_id, server_name, server_ip, server_key), daemon=True).start()

        # 更新批量任务的任务列表
        with batch_fota_tasks_lock:
            if batch_id in batch_fota_tasks:
                batch_fota_tasks[batch_id]["tasks"] = [t["task_id"] for t in task_ids]

        log_fota(f"[批量FOTA] 创建批量任务 {batch_id}，共 {len(task_ids)} 个服务器，端口 {port}")
        return jsonify({"ok": True, "batch_id": batch_id, "total": len(task_ids)})
    except Exception as e:
        # 清理临时文件（如果创建失败）
        if 'temp_file_path' in locals() and temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except:
                pass
        log_fota(f"批量FOTA异常: {e}")
        log_srv(f"批量FOTA异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/api/batch-fota/progress/<batch_id>")
def api_batch_fota_progress(batch_id):
    """查询批量FOTA任务进度"""
    try:
        with batch_fota_tasks_lock:
            batch_task = batch_fota_tasks.get(batch_id)
        
        if not batch_task:
            return jsonify({"error": "批量任务不存在"}), 404

        tasks_info = []
        with fota_tasks_lock:
            for task_id in batch_task.get("tasks", []):
                task = fota_tasks.get(task_id)
                if task:
                    result = task.get("result", {})
                    tasks_info.append({
                        "task_id": task_id,
                        "server_key": task.get("server_key", ""),
                        "status": task.get("status", "unknown"),
                        "progress": task.get("progress", 0),
                        "step": task.get("step", ""),
                        "result": result,
                        "error": result.get("error") if result else None,
                        "local_md5": result.get("local_md5") if result else None,
                        "remote_md5": result.get("remote_md5") if result else None
                    })

        return jsonify({
            "batch_id": batch_id,
            "status": batch_task.get("status", "running"),
            "total": batch_task.get("total", 0),
            "completed": batch_task.get("completed", 0),
            "tasks": tasks_info
        })
    except Exception as e:
        log_srv(f"查询批量FOTA进度异常: {e}")
        return jsonify({"error": f"服务器异常: {e}"}), 500


@app.route("/api/batch-fota/cancel/<batch_id>", methods=["POST"])
def api_batch_fota_cancel(batch_id):
    """取消批量FOTA任务"""
    try:
        with batch_fota_tasks_lock:
            batch_task = batch_fota_tasks.get(batch_id)
            
            if not batch_task:
                return jsonify({"ok": False, "error": "批量任务不存在"}), 404
            
            if batch_task.get("status") in ("done", "error", "cancelled"):
                return jsonify({"ok": False, "error": "任务已完成或已取消"}), 400
            
            # 设置取消标志
            batch_task["cancelled"] = True
            batch_task["status"] = "cancelling"
            
            # 关闭所有正在进行的任务的transport和sftp
            task_ids = batch_task.get("tasks", [])
            closed_count = 0
            with fota_transports_lock:
                for task_id in task_ids:
                    if task_id in fota_transports:
                        transport_info = fota_transports[task_id]
                        try:
                            if transport_info.get("sftp"):
                                transport_info["sftp"].close()
                                closed_count += 1
                        except:
                            pass
                        try:
                            if transport_info.get("transport"):
                                transport_info["transport"].close()
                                closed_count += 1
                        except:
                            pass
                        del fota_transports[task_id]
            
            # 更新任务状态为cancelled
            with fota_tasks_lock:
                for task_id in task_ids:
                    if task_id in fota_tasks:
                        task = fota_tasks[task_id]
                        if task.get("status") not in ("done", "error"):
                            task["status"] = "cancelled"
                            task["step"] = "任务已取消"
                            task["result"] = {"ok": False, "error": "任务已取消"}
            
            log_fota(f"[批量FOTA] 取消批量任务 {batch_id}，关闭了 {closed_count} 个连接")
            return jsonify({"ok": True, "message": f"已取消批量任务，关闭了 {closed_count} 个连接"})
    except Exception as e:
        log_fota(f"取消批量FOTA异常: {e}")
        log_srv(f"取消批量FOTA异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/")
def index():
    html = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>杭州办公室台架服务器监控</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --card-bg: #fff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #e5e7eb;
      --accent1: #2563eb;
      --accent2: #3b82f6;
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: var(--bg); color: var(--text); }
    header { background: linear-gradient(135deg, var(--accent1), var(--accent2)); color: #fff; padding: 16px 24px; box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
    .container { padding: 20px; max-width: 1400px; margin: 0 auto; }
    .card { background: var(--card-bg); border-radius: 12px; box-shadow: 0 10px 30px rgba(15,23,42,0.08); padding: 14px; }
    .table-wrap { overflow: auto; max-width: 100%; }
    table { width: 100%; min-width: 1200px; border-collapse: separate; border-spacing: 0; }
    th, td { padding: 10px 8px; text-align: left; white-space: nowrap; border-bottom: 1px solid #eaeef4; }
    th { font-weight: 600; color: #374151; background: #f8fafc; position: sticky; top: 0; z-index: 1; border-bottom: 1px solid #d9e2ec; }
    th.col-checkbox { z-index: 5; }
    th.col-status { z-index: 4; }
    th.col-name { z-index: 4; }
    th.col-ip { z-index: 4; }
    tr:hover td { background: #eef2ff; }
    tr:hover td.col-checkbox, tr:hover td.col-status, tr:hover td.col-name, tr:hover td.col-ip { background: #eef2ff; }
    th + th, td + td { border-left: 1px solid #f0f2f6; }
    th.col-checkbox, td.col-checkbox { position: sticky; left: 0; z-index: 4; width: 50px; min-width: 50px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); text-align: center; }
    th.col-status, td.col-status { position: sticky; left: 50px; z-index: 3; min-width: 80px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); }
    th.col-name, td.col-name { position: sticky; left: 130px; z-index: 3; min-width: 150px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); }
    th.col-ip, td.col-ip { position: sticky; left: 280px; z-index: 3; min-width: 150px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
    .ok { background: #dcfce7; color: #166534; }
    .warn { background: #fee2e2; color: #991b1b; }
    .unknown { background: #e0f2fe; color: #075985; }
    .meta { color: var(--muted); font-size: 14px; margin-top: 6px; }
    .error { color: #b91c1c; margin-bottom: 12px; }
    .btn { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border); background: #eef2ff; color: #1e3a8a; cursor: pointer; font-size: 12px; }
    .btn:hover { background: #e0e7ff; }
    .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; align-items: center; justify-content: center; z-index: 20; }
    .modal { background: #fff; border-radius: 12px; padding: 18px; width: 360px; box-shadow: 0 12px 40px rgba(0,0,0,0.18); }
    .modal h3 { margin: 0 0 12px 0; }
    .modal label { display: block; margin: 10px 0 4px 0; font-size: 13px; color: #374151; }
    .modal input[type="text"], .modal input[type="file"] { width: 100%; }
    .modal-actions { margin-top: 14px; display: flex; gap: 8px; justify-content: flex-end; }
    .progress { margin-top: 8px; font-size: 13px; color: #374151; }
    @media (max-width: 900px) {
      table { min-width: 820px; }
    }
  </style>
</head>
<body>
  <header>
    <h2>杭州办公室台架服务器监控面板</h2>
    <div class="meta" id="updatedAt">加载中...</div>
  </header>
  <div class="container">
    <div class="card">
      <div style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center;">
        <button class="btn" onclick="openBatchFota()" style="background: #3b82f6; color: #fff; border: none;">批量FOTA操作</button>
        <span id="selectedCount" style="color: var(--muted); font-size: 13px;">已选择: 0</span>
      </div>
      <div id="errorBox" class="error"></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="col-checkbox"><input type="checkbox" id="selectAll" onchange="toggleSelectAll()"></th>
              <th class="col-status">状态</th>
              <th class="col-name">名称</th>
              <th class="col-ip">IP</th>
              <th>智驾域(22)</th>
              <th>智驾域SSH</th>
              <th>智驾域详情</th>
              <th>座舱域(9999)</th>
              <th>座舱域SSH</th>
              <th>座舱域详情</th>
              <th>智驾域版本</th>
              <th>座舱域版本</th>
              <th>上传</th>
              <th>FOTA</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody id="tableBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="uploadModal">
    <div class="modal">
      <h3>上传文件</h3>
      <div>服务器：<span id="uploadServer"></span></div>
      <div>端口：<span id="uploadPort"></span></div>
      <label>目标目录</label>
      <input type="text" id="targetDir" placeholder="例如 /tmp">
      <label style="margin-top:8px;">选择文件</label>
      <input type="file" id="fileInput">
      <div class="progress" id="uploadProgress"></div>
      <div class="modal-actions">
        <button class="btn" onclick="closeUpload()">取消</button>
        <button class="btn" onclick="uploadFile()">上传</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="fotaModal">
    <div class="modal">
      <h3>FOTA 升级</h3>
      <div>服务器：<span id="fotaServer"></span></div>
      <div>端口：<span id="fotaPort"></span></div>
      <div>目标目录：<span id="fotaTargetText"></span></div>
      <label style="margin-top:8px;">选择升级包</label>
      <input type="file" id="fotaFile">
      <div class="progress" id="fotaProgress"></div>
      <div class="progress" id="fotaStep" style="margin-top:4px;"></div>
      <div class="modal-actions">
        <button class="btn" onclick="closeFota()">取消</button>
        <button class="btn" onclick="startFota()">升级</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="batchFotaModal">
    <div class="modal" style="width: 500px;">
      <h3>批量FOTA 升级</h3>
      <div style="margin-bottom: 8px; max-height: 200px; overflow-y: auto; border: 1px solid var(--border); padding: 8px; border-radius: 6px;">
        <div style="font-size: 13px; color: var(--muted); margin-bottom: 4px;">已选择服务器 (<span id="batchServerCount">0</span>):</div>
        <div id="batchServerList" style="font-size: 12px; color: var(--text);"></div>
      </div>
      <div>目标目录：<span id="batchFotaTargetText"></span></div>
      <label style="margin-top:8px;">选择升级包</label>
      <input type="file" id="batchFotaFile">
      <div class="progress" id="batchFotaProgress"></div>
      <div class="progress" id="batchFotaStep" style="margin-top:4px;"></div>
      <div style="margin-top: 12px; max-height: 300px; overflow-y: auto; border: 1px solid var(--border); padding: 8px; border-radius: 6px;">
        <div style="font-size: 13px; color: var(--muted); margin-bottom: 4px;">任务进度:</div>
        <div id="batchFotaTasks" style="font-size: 12px;"></div>
      </div>
      <div id="batchFotaTaskDetails" style="margin-top: 8px; display: none;"></div>
      <div class="modal-actions">
        <button class="btn" onclick="closeBatchFota()">关闭</button>
        <button class="btn" id="batchFotaCancelBtn" onclick="cancelBatchFota()" style="background: #ef4444; color: #fff; border: none; display: none;">终止</button>
        <button class="btn" id="batchFotaStart22Btn" onclick="startBatchFota(22)" style="background: #3b82f6; color: #fff; border: none;">22端口升级</button>
        <button class="btn" id="batchFotaStart9999Btn" onclick="startBatchFota(9999)" style="background: #3b82f6; color: #fff; border: none;">9999端口升级</button>
      </div>
    </div>
  </div>

  <script>
    function tag(text, cls) {
      return '<span class="tag ' + cls + '">' + text + '</span>';
    }

    function statusClass(val) {
      if (val === 'online') return 'ok';
      if (val === '异常' || val === 'offline') return 'warn';
      return 'unknown';
    }

    const sshUser = "{{ ssh_user }}";
    let defaultTarget = "";
    let fotaTarget = "";
    let currentUpload = { name: "", ip: "", port: 22 };
    let currentFota = { name: "", ip: "", port: 22 };
    let selectedServers = {}; // {server_key: {name, ip}}

    const backdrop = document.getElementById('uploadModal');
    const targetInput = document.getElementById('targetDir');
    const fileInput = document.getElementById('fileInput');
    const progressBox = document.getElementById('uploadProgress');
    const fotaBackdrop = document.getElementById('fotaModal');
    const fotaFile = document.getElementById('fotaFile');
    const fotaServer = document.getElementById('fotaServer');
    const fotaPort = document.getElementById('fotaPort');
    const fotaTargetText = document.getElementById('fotaTargetText');
    const fotaProgress = document.getElementById('fotaProgress');
    const fotaStep = document.getElementById('fotaStep');

    function formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
      return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
    }

    function formatSpeed(loaded, seconds) {
      const bps = loaded / seconds;
      if (bps < 1024) return bps.toFixed(0) + ' B/s';
      if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + ' KB/s';
      return (bps / 1024 / 1024).toFixed(1) + ' MB/s';
    }

    const uploadServer = document.getElementById('uploadServer');
    const uploadPort = document.getElementById('uploadPort');

    function openUpload(name, ip, port) {
      currentUpload = { name, ip, port };
      uploadServer.textContent = name;
      uploadPort.textContent = port;
      targetInput.value = defaultTarget || '';
      fileInput.value = '';
      progressBox.textContent = '';
      backdrop.style.display = 'flex';
    }

    function closeUpload() {
      backdrop.style.display = 'none';
    }

    function uploadFile() {
      const file = fileInput.files[0];
      const targetDir = (targetInput.value || defaultTarget || '').trim();
      if (!file) {
        alert('请先选择文件');
        return;
      }
      if (!targetDir) {
        alert('请填写目标目录');
        return;
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('server_name', currentUpload.name);
      fd.append('server_ip', currentUpload.ip);
      fd.append('port', currentUpload.port);
      fd.append('target_dir', targetDir);

      const startTs = Date.now();
      const totalSize = file.size;
      progressBox.textContent = '开始上传...';

      fetch('/api/upload', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.task_id) {
          progressBox.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        const es = new EventSource(`/api/upload/progress/${taskId}`);
        
        es.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.error) {
            progressBox.textContent = `失败: ${msg.error}`;
            es.close();
            return;
          }

          const percent = msg.progress || 0;
          const elapsed = Math.max((Date.now() - startTs) / 1000, 0.001);
          const loaded = Math.floor(totalSize * percent / 100);
          const speed = formatSpeed(loaded, elapsed);
          
          if (msg.status === 'uploading') {
            progressBox.textContent = `上传进度: ${percent}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
          } else if (msg.status === 'done') {
            es.close();
            if (msg.result && msg.result.ok) {
              progressBox.textContent = `上传成功: ${msg.result.path}`;
            } else {
              progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
            }
          } else if (msg.status === 'error') {
            es.close();
            progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
          }
        };

        es.onerror = () => {
          es.close();
          progressBox.textContent = '进度监听失败';
        };
      }).catch(err => {
        progressBox.textContent = `上传失败: ${err}`;
      });
    }

    function openFota(name, ip, port) {
      currentFota = { name, ip, port };
      fotaServer.textContent = name;
      fotaPort.textContent = port;
      fotaTargetText.textContent = fotaTarget;
      fotaFile.value = '';
      fotaProgress.textContent = '';
      fotaStep.textContent = '等待选择文件...';
      fotaBackdrop.style.display = 'flex';
    }

    function closeFota() {
      fotaBackdrop.style.display = 'none';
    }

    function startFota() {
      const file = fotaFile.files[0];
      if (!file) {
        alert('请先选择文件');
        return;
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('server_name', currentFota.name);
      fd.append('server_ip', currentFota.ip);
      fd.append('port', currentFota.port);

      const startTs = Date.now();
      const totalSize = file.size;
      fotaStep.textContent = '开始上传...';
      fotaProgress.textContent = '';

      fetch('/api/fota', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.task_id) {
          fotaStep.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        const es = new EventSource(`/api/fota/progress/${taskId}`);
        
        es.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.error) {
            fotaStep.textContent = `失败: ${msg.error}`;
            es.close();
            return;
          }

          const percent = msg.progress || 0;
          fotaStep.textContent = msg.step || '处理中...';

          if (msg.status === 'checking') {
            fotaProgress.textContent = `检查文件... ${percent}%`;
          } else if (msg.status === 'md5') {
            fotaProgress.textContent = `MD5校验中... ${percent}%`;
          } else if (msg.status === 'uploading') {
            const elapsed = Math.max((Date.now() - startTs) / 1000, 0.001);
            // 上传进度从30%到80%，需要计算实际上传的百分比
            const uploadPercent = Math.max(0, Math.min(100, ((percent - 30) / 50) * 100));
            const loaded = Math.floor(totalSize * uploadPercent / 100);
            const speed = formatSpeed(loaded, elapsed);
            fotaProgress.textContent = `上传进度: ${uploadPercent.toFixed(0)}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
          } else if (msg.status === 'upgrading') {
            fotaProgress.textContent = `升级执行中... ${percent}%`;
          } else if (msg.status === 'done') {
            es.close();
            if (msg.result && msg.result.ok) {
              fotaStep.textContent = '完成：升级成功';
              fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
            } else {
              fotaStep.textContent = `失败: ${msg.result?.error || '未知错误'}`;
              if (msg.result?.local_md5 && msg.result?.remote_md5) {
                fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
              }
            }
          } else if (msg.status === 'error') {
            es.close();
            fotaStep.textContent = `失败: ${msg.result?.error || '未知错误'}`;
            if (msg.result?.local_md5 && msg.result?.remote_md5) {
              fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
            }
          }
        };

        es.onerror = () => {
          es.close();
          fotaStep.textContent = '进度监听失败';
        };
      }).catch(err => {
        fotaStep.textContent = `升级失败: ${err}`;
      });
    }

    async function loadStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        defaultTarget = data.upload_target_dir || '';
        fotaTarget = data.fota_target_dir || '/opt/data/fota';

        const tbody = document.getElementById('tableBody');
        tbody.innerHTML = '';

        const sorted = (data.servers || []).slice().sort((a, b) =>
          a.server_name.localeCompare(b.server_name, 'zh-Hans-CN-u-nu-latn', { numeric: true })
        );

        sorted.forEach(item => {
          const tr = document.createElement('tr');
          const serverKey = `${item.server_name}:${item.server_ip}`;
          // 保持选中状态
          const isSelected = selectedServers[serverKey] ? 'checked' : '';
          
          // 生成FOTA状态显示
          function getFotaStatusHtml(fotaStatus, port) {
            if (!fotaStatus) return '<span style="color: #6b7280;">空闲</span>';
            const statusMap = {
              'checking': { text: '检查文件', color: '#6366f1', icon: '🔎' },
              'md5': { text: 'MD5校验', color: '#f59e0b', icon: '🔍' },
              'uploading': { text: '上传中', color: '#3b82f6', icon: '⬆️' },
              'upgrading': { text: '升级中', color: '#8b5cf6', icon: '⚡' },
              'done': { text: '完成', color: '#10b981', icon: '✅' },
              'error': { text: '失败', color: '#ef4444', icon: '❌' },
              'cancelled': { text: '已取消', color: '#6b7280', icon: '🚫' }
            };
            const statusInfo = statusMap[fotaStatus.status] || { text: fotaStatus.status, color: '#6b7280', icon: '⏳' };
            const progress = fotaStatus.progress || 0;
            return `<span style="color: ${statusInfo.color}; font-weight: 600;" title="${fotaStatus.step || ''}">${statusInfo.icon} ${statusInfo.text} ${progress}%</span>`;
          }
          
          const status22 = getFotaStatusHtml(item.fota_status_22, 22);
          const status9999 = getFotaStatusHtml(item.fota_status_9999, 9999);
          const hasFota = item.fota_status_22 || item.fota_status_9999;
          const statusCell = hasFota 
            ? `<div style="font-size: 11px; line-height: 1.4;">
                 <div>22: ${status22}</div>
                 <div>9999: ${status9999}</div>
               </div>`
            : '<span style="color: #6b7280;">空闲</span>';
          
          tr.innerHTML = `
            <td class="col-checkbox"><input type="checkbox" class="server-checkbox" data-key="${serverKey.replace(/"/g, '&quot;')}" data-name="${item.server_name.replace(/"/g, '&quot;')}" data-ip="${item.server_ip.replace(/"/g, '&quot;')}" ${isSelected} onchange="updateSelectedServers()"></td>
            <td class="col-status">${statusCell}</td>
            <td class="col-name">${item.server_name}</td>
            <td class="col-ip">${item.server_ip}</td>
            <td>${tag(item.port_22, statusClass(item.port_22))}</td>
            <td>${tag(item.port_22_ssh, statusClass(item.port_22_ssh))}</td>
            <td>${item.port_22_detail || ''}</td>
            <td>${tag(item.port_9999, statusClass(item.port_9999))}</td>
            <td>${tag(item.port_9999_ssh, statusClass(item.port_9999_ssh))}</td>
            <td>${item.port_9999_detail || ''}</td>
            <td>${item.version || item.version_detail || ''}</td>
            <td>${item.version_9999 || item.version_9999_detail || ''}</td>
            <td>
              <button class="btn" onclick="openUpload('${item.server_name}','${item.server_ip}',22)">上传22</button>
              <button class="btn" onclick="openUpload('${item.server_name}','${item.server_ip}',9999)">上传9999</button>
            </td>
            <td>
              <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',22)" ${item.fota_status_22 ? 'disabled style="opacity: 0.5;"' : ''}>FOTA 22</button>
              <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',9999)" ${item.fota_status_9999 ? 'disabled style="opacity: 0.5;"' : ''}>FOTA 9999</button>
            </td>
            <td>${item.timestamp}</td>
          `;
          tbody.appendChild(tr);
        });

        const updatedAt = document.getElementById('updatedAt');
        if (data.timestamp) {
          const date = new Date(data.timestamp * 1000);
          updatedAt.textContent = '最后更新：' + date.toLocaleString();
        } else {
          updatedAt.textContent = '最后更新：等待数据...';
        }

        const errorBox = document.getElementById('errorBox');
        if (data.error) {
          errorBox.textContent = '错误：' + data.error;
        } else {
          errorBox.textContent = '';
        }
      } catch (e) {
        document.getElementById('errorBox').textContent = '请求失败：' + e;
      }
    }

    function toggleSelectAll() {
      const selectAll = document.getElementById('selectAll');
      const checkboxes = document.querySelectorAll('.server-checkbox');
      checkboxes.forEach(cb => {
        cb.checked = selectAll.checked;
        if (selectAll.checked) {
          const key = cb.getAttribute('data-key');
          const name = cb.getAttribute('data-name');
          const ip = cb.getAttribute('data-ip');
          selectedServers[key] = {name, ip};
        } else {
          selectedServers = {};
        }
      });
      updateSelectedCount();
    }

    function updateSelectedServers() {
      selectedServers = {};
      const checkboxes = document.querySelectorAll('.server-checkbox:checked');
      checkboxes.forEach(cb => {
        const key = cb.getAttribute('data-key');
        const name = cb.getAttribute('data-name');
        const ip = cb.getAttribute('data-ip');
        selectedServers[key] = {name, ip};
      });
      updateSelectedCount();
      // 更新全选状态
      const allCheckboxes = document.querySelectorAll('.server-checkbox');
      const selectAll = document.getElementById('selectAll');
      selectAll.checked = allCheckboxes.length > 0 && checkboxes.length === allCheckboxes.length;
    }

    function updateSelectedCount() {
      const count = Object.keys(selectedServers).length;
      document.getElementById('selectedCount').textContent = `已选择: ${count}`;
    }

    let currentBatchId = null;
    let batchProgressInterval = null;

    function openBatchFota() {
      const count = Object.keys(selectedServers).length;
      if (count === 0) {
        alert('请先选择至少一个服务器');
        return;
      }
      const batchFotaBackdrop = document.getElementById('batchFotaModal');
      const batchServerList = document.getElementById('batchServerList');
      const batchServerCount = document.getElementById('batchServerCount');
      const batchFotaTargetText = document.getElementById('batchFotaTargetText');
      const batchFotaFile = document.getElementById('batchFotaFile');
      const batchFotaProgress = document.getElementById('batchFotaProgress');
      const batchFotaStep = document.getElementById('batchFotaStep');
      const batchFotaTasks = document.getElementById('batchFotaTasks');
      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');
      const batchFotaStart22Btn = document.getElementById('batchFotaStart22Btn');
      const batchFotaStart9999Btn = document.getElementById('batchFotaStart9999Btn');

      // 重置状态
      currentBatchId = null;
      if (batchProgressInterval) {
        clearInterval(batchProgressInterval);
        batchProgressInterval = null;
      }

      batchServerCount.textContent = count;
      batchFotaTargetText.textContent = fotaTarget;
      batchFotaFile.value = '';
      batchFotaProgress.textContent = '';
      batchFotaStep.textContent = '等待选择文件...';
      batchFotaTasks.innerHTML = '';
      batchFotaCancelBtn.style.display = 'none';
      batchFotaStart22Btn.style.display = 'inline-block';
      batchFotaStart9999Btn.style.display = 'inline-block';

      let serverListHtml = '';
      for (const key in selectedServers) {
        const srv = selectedServers[key];
        serverListHtml += `<div>${srv.name} (${srv.ip})</div>`;
      }
      batchServerList.innerHTML = serverListHtml;

      batchFotaBackdrop.style.display = 'flex';
    }

    function closeBatchFota() {
      // 如果正在执行，先取消
      if (currentBatchId && batchProgressInterval) {
        cancelBatchFota();
      }
      document.getElementById('batchFotaModal').style.display = 'none';
    }

    function cancelBatchFota() {
      if (!currentBatchId) {
        return;
      }

      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');
      const batchFotaStep = document.getElementById('batchFotaStep');
      
      batchFotaCancelBtn.disabled = true;
      batchFotaStep.textContent = '正在终止任务...';

      fetch(`/api/batch-fota/cancel/${currentBatchId}`, {
        method: 'POST'
      }).then(res => res.json()).then(data => {
        if (data.ok) {
          batchFotaStep.textContent = '任务已终止: ' + (data.message || '');
          if (batchProgressInterval) {
            clearInterval(batchProgressInterval);
            batchProgressInterval = null;
          }
          currentBatchId = null;
          batchFotaCancelBtn.style.display = 'none';
        } else {
          batchFotaStep.textContent = '终止失败: ' + (data.error || '未知错误');
          batchFotaCancelBtn.disabled = false;
        }
      }).catch(err => {
        batchFotaStep.textContent = '终止失败: ' + err;
        batchFotaCancelBtn.disabled = false;
      });
    }

    function startBatchFota(port) {
      const file = document.getElementById('batchFotaFile').files[0];
      if (!file) {
        alert('请先选择文件');
        return;
      }

      const servers = [];
      for (const key in selectedServers) {
        servers.push(selectedServers[key]);
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('port', port);
      fd.append('servers', JSON.stringify(servers));

      const batchFotaProgress = document.getElementById('batchFotaProgress');
      const batchFotaStep = document.getElementById('batchFotaStep');
      const batchFotaTasks = document.getElementById('batchFotaTasks');
      const batchFotaTaskDetails = document.getElementById('batchFotaTaskDetails');
      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');
      const batchFotaStart22Btn = document.getElementById('batchFotaStart22Btn');
      const batchFotaStart9999Btn = document.getElementById('batchFotaStart9999Btn');

      batchFotaStep.textContent = `开始批量FOTA (${port}端口)...`;
      batchFotaProgress.textContent = '';
      batchFotaTasks.innerHTML = '';
      batchFotaTaskDetails.innerHTML = '';
      batchFotaTaskDetails.style.display = 'block';
      batchFotaCancelBtn.style.display = 'inline-block';
      batchFotaStart22Btn.style.display = 'none';
      batchFotaStart9999Btn.style.display = 'none';

      const totalSize = file.size;
      const taskStartTimes = {}; // {taskKey: startTime}

      fetch('/api/batch-fota', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.batch_id) {
          batchFotaStep.textContent = `失败: ${data.error || '未知错误'}`;
          batchFotaCancelBtn.style.display = 'none';
          batchFotaStart22Btn.style.display = 'inline-block';
          batchFotaStart9999Btn.style.display = 'inline-block';
          return;
        }

        const batchId = data.batch_id;
        currentBatchId = batchId;
        batchFotaStep.textContent = `批量任务已启动，任务ID: ${batchId}`;

        // 初始化任务列表显示 - 每个任务显示详细进度，参考单个FOTA
        servers.forEach(srv => {
          const taskKey = `${srv.name}:${srv.ip}:${port}`;
          const taskId = `task-${taskKey.replace(/:/g, '-')}`;
          taskStartTimes[taskKey] = Date.now();
          
          const taskContainer = document.createElement('div');
          taskContainer.id = taskId;
          taskContainer.style.marginBottom = '12px';
          taskContainer.style.padding = '8px';
          taskContainer.style.border = '1px solid var(--border)';
          taskContainer.style.borderRadius = '6px';
          taskContainer.style.backgroundColor = '#f8fafc';
          
          const taskHeader = document.createElement('div');
          taskHeader.style.fontWeight = '600';
          taskHeader.style.marginBottom = '4px';
          taskHeader.textContent = `${srv.name} (${srv.ip})`;
          
          const taskStep = document.createElement('div');
          taskStep.id = `${taskId}-step`;
          taskStep.className = 'progress';
          taskStep.style.marginTop = '4px';
          taskStep.style.fontSize = '12px';
          taskStep.textContent = '等待开始...';
          
          const taskProgress = document.createElement('div');
          taskProgress.id = `${taskId}-progress`;
          taskProgress.className = 'progress';
          taskProgress.style.fontSize = '12px';
          taskProgress.style.color = 'var(--muted)';
          taskProgress.textContent = '';
          
          taskContainer.appendChild(taskHeader);
          taskContainer.appendChild(taskStep);
          taskContainer.appendChild(taskProgress);
          batchFotaTasks.appendChild(taskContainer);
        });

        // 轮询批量任务进度
        batchProgressInterval = setInterval(() => {
          if (!currentBatchId || currentBatchId !== batchId) {
            clearInterval(batchProgressInterval);
            batchProgressInterval = null;
            return;
          }

          fetch(`/api/batch-fota/progress/${batchId}`)
            .then(res => res.json())
            .then(progressData => {
              if (progressData.error) {
                batchFotaStep.textContent = `错误: ${progressData.error}`;
                clearInterval(progressInterval);
                return;
              }

              const total = progressData.total || servers.length;
              const completed = progressData.completed || 0;
              const status = progressData.status || 'running';

              batchFotaProgress.textContent = `总体进度: ${completed}/${total} (${Math.floor(completed * 100 / total)}%)`;

              // 更新各个任务的详细进度，完全参考单个FOTA的显示方式
              if (progressData.tasks) {
                progressData.tasks.forEach(taskInfo => {
                  const taskKey = taskInfo.server_key || '';
                  const taskId = `task-${taskKey.replace(/:/g, '-')}`;
                  const taskStepDiv = document.getElementById(`${taskId}-step`);
                  const taskProgressDiv = document.getElementById(`${taskId}-progress`);
                  
                  if (taskStepDiv && taskProgressDiv) {
                    const percent = taskInfo.progress || 0;
                    const taskStatus = taskInfo.status || 'unknown';
                    const step = taskInfo.step || '';
                    
                    // 更新步骤信息
                    taskStepDiv.textContent = step || '处理中...';
                    
                    // 根据状态显示详细进度，完全参考单个FOTA
                    if (taskStatus === 'uploading') {
                      const startTime = taskStartTimes[taskKey] || Date.now();
                      const elapsed = Math.max((Date.now() - startTime) / 1000, 0.001);
                      const loaded = Math.floor(totalSize * percent / 100);
                      const speed = formatSpeed(loaded, elapsed);
                      taskProgressDiv.textContent = `上传进度: ${percent}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'md5') {
                      taskProgressDiv.textContent = `MD5校验中... ${percent}%`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'upgrading') {
                      taskProgressDiv.textContent = `升级执行中... ${percent}%`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'done') {
                      if (taskInfo.result && taskInfo.result.ok) {
                        taskStepDiv.textContent = '完成：升级成功';
                        taskStepDiv.style.color = '#166534';
                        if (taskInfo.local_md5 && taskInfo.remote_md5) {
                          taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                        }
                        taskProgressDiv.style.color = '#166534';
                      } else {
                        taskStepDiv.textContent = `失败: ${taskInfo.error || '未知错误'}`;
                        taskStepDiv.style.color = '#991b1b';
                        if (taskInfo.local_md5 && taskInfo.remote_md5) {
                          taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                        }
                        taskProgressDiv.style.color = '#991b1b';
                      }
                    } else if (taskStatus === 'error' || taskStatus === 'cancelled') {
                      taskStepDiv.textContent = taskStatus === 'cancelled' ? '已取消' : `失败: ${taskInfo.error || '未知错误'}`;
                      taskStepDiv.style.color = '#991b1b';
                      if (taskInfo.local_md5 && taskInfo.remote_md5) {
                        taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                      }
                      taskProgressDiv.style.color = '#991b1b';
                    } else {
                      taskProgressDiv.textContent = '';
                    }
                  }
                });
              }

              if (status === 'done' || status === 'error' || status === 'cancelled') {
                clearInterval(batchProgressInterval);
                batchProgressInterval = null;
                batchFotaCancelBtn.style.display = 'none';
                if (status === 'cancelled') {
                  batchFotaStep.textContent = '批量FOTA已取消';
                } else {
                  batchFotaStep.textContent = status === 'done' ? '批量FOTA完成' : '批量FOTA失败';
                }
              }
            })
            .catch(err => {
              batchFotaStep.textContent = `进度查询失败: ${err}`;
              clearInterval(batchProgressInterval);
              batchProgressInterval = null;
              batchFotaCancelBtn.style.display = 'none';
            });
        }, 200); // 改为200ms更新一次，与单个FOTA的SSE更新频率接近
      }).catch(err => {
        batchFotaStep.textContent = `批量FOTA失败: ${err}`;
      });
    }

    // 性能优化：根据标签页可见性调整刷新频率
    let refreshInterval = 5000; // 默认5秒
    let isPageVisible = true;
    let refreshTimer = null;
    
    // 监听页面可见性变化
    document.addEventListener('visibilitychange', () => {
      isPageVisible = !document.hidden;
      if (isPageVisible) {
        // 页面可见时立即刷新一次，然后恢复正常频率
        loadStatus();
        refreshInterval = 5000;
      } else {
        // 页面不可见时降低刷新频率到30秒
        refreshInterval = 30000;
      }
      // 重新启动定时器
      if (refreshTimer) {
        clearTimeout(refreshTimer);
      }
      scheduleNextRefresh();
    });
    
    // 使用动态间隔刷新
    function scheduleNextRefresh() {
      if (refreshTimer) {
        clearTimeout(refreshTimer);
      }
      refreshTimer = setTimeout(() => {
        if (isPageVisible) {
          loadStatus();
        }
        scheduleNextRefresh();
      }, refreshInterval);
    }
    
    // 初始加载
    loadStatus();
    scheduleNextRefresh();
  </script>
</body>
</html>
    """
    return render_template_string(html, ssh_user=ssh_username or "root")


def start_background():
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    load_config()  # 读取配置
    # 初始化一次数据
    try:
        status_cache["data"] = run_checks_once()
        status_cache["timestamp"] = time.time()
    except Exception as e:
        status_cache["error"] = str(e)

    start_background()
    app.run(host="0.0.0.0", port=5000, debug=False)

