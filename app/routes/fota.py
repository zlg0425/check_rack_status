"""
FOTA 相关路由模块
"""
from flask import Blueprint, jsonify, request, Response, stream_with_context
import threading
import uuid
import json
import time
import os
import tempfile
import shutil
import io

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
from app.api.fota_service import get_fota_service

# 从 app.utils.helpers 导入日志函数
from app.utils.helpers import log_srv, log_fota

# 从 core.fota 导入 FOTA 相关函数
from core.fota import record_fota_timing, get_avg_fota_timing

# 获取 FOTA 服务实例
fota_service = get_fota_service()

bp = Blueprint('fota', __name__, url_prefix='/api')

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

# sftp_upload_with_cancel 函数已迁移到 core.sftp.operations
try:
    from core.sftp.operations import sftp_upload_with_cancel
except ImportError as e:
    raise


@bp.route("/fota", methods=["POST"])
def api_fota():
    """单服务器 FOTA 升级"""
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port_str = request.form.get("port", "0")  # 允许传入0或空字符串
        file = request.files.get("file")

        if not server_name or not server_ip:
            log_fota(f"[{server_name}/{server_ip}] 参数缺失")
            return jsonify({"ok": False, "error": "参数缺失"}), 400
        
        if not file or file.filename == "":
            log_fota(f"[{server_name}/{server_ip}] 未选择文件")
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        filename = file.filename
        
        # 验证文件名
        if not fota_service.validate_filename(filename):
            log_fota(f"[{server_name}/{server_ip}] 文件名验证失败: {filename}")
            return jsonify({"ok": False, "error": f"文件名不符合验证规则: {filename}"}), 400
        
        # 如果端口为0或未提供，尝试自动检测
        if port_str == "0" or not port_str:
            detected_port, message = fota_service.detect_port(server_name, filename)
            
            if detected_port == 0:
                log_fota(f"[{server_name}/{server_ip}] 自动检测端口失败: {message}")
                return jsonify({"ok": False, "error": message}), 400
            
            port = detected_port
            log_fota(f"[{server_name}/{server_ip}] 自动检测端口: {port} ({message})")
        else:
            port = int(port_str)
            if port not in (22, 9999):
                log_fota(f"[{server_name}/{server_ip}:{port}] 端口非法")
                return jsonify({"ok": False, "error": "端口必须是22或9999"}), 400

        # 检查服务器是否已有正在执行的FOTA任务
        existing_task = fota_service.check_server_busy(server_name, server_ip, port)
        if existing_task:
            log_fota(f"[{server_name}/{server_ip}:{port}] 服务器已有正在执行的FOTA任务: {existing_task}")
            return jsonify({"ok": False, "error": f"服务器 {server_name} 已有正在执行的FOTA任务，请等待完成后再试"}), 409
        
        # 创建任务
        task_id = fota_service.create_task(server_name, server_ip, port)
        
        # 定义server_key（用于do_fota函数中的锁管理）
        server_key = f"{server_name}:{server_ip}:{port}"

        # 真正的流式上传：使用临时文件，避免将整个文件读入内存
        import time as time_module
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
                # 注意：对于8650系列，get_md5()返回None，需要从临时文件重新计算
                local_md5 = md5_calculator.get_md5()
                if local_md5 is None:
                    # 8650系列：从临时文件重新计算MD5（使用实际文件大小）
                    temp_file.close()
                    temp_file = None
                    # 使用实际读取的字节数作为文件大小
                    actual_file_size = bytes_copied
                    with open(temp_file_path, 'rb') as temp_file_for_md5:
                        local_md5 = md5_stream(temp_file_for_md5, server_name, actual_file_size)
                    log_fota(f"[{server_name}/{server_ip}:{port}] 从临时文件重新计算MD5: {local_md5}")
                else:
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
                nonlocal md5_duration, local_md5, file_obj, upload_stream, upload_md5_stream, server_key
                
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
                                local_md5_preview = local_md5[:8] if local_md5 else "None"
                                r_md5_pre_preview = r_md5_pre[:8] if r_md5_pre else "None"
                                update_fota_progress(25, "md5", f"MD5校验: 本地={local_md5_preview}... 远端={r_md5_pre_preview}...")
                                if local_md5 is None:
                                    log_fota(f"[{server_name}/{server_ip}:{port}] 错误：本地MD5为None，无法比较")
                                    update_fota_progress(0, "error", "本地MD5计算失败")
                                    return
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
                    if local_md5 is None:
                        log_fota(f"[{server_name}/{server_ip}:{port}] 错误：本地MD5为None")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": "本地MD5计算失败", "result": {"ok": False, "error": "本地MD5计算失败"}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return
                    if r_md5 is None:
                        log_fota(f"[{server_name}/{server_ip}:{port}] 错误：远端MD5为None")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": "远端MD5计算失败", "result": {"ok": False, "error": "远端MD5计算失败"}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return
                    local_md5_preview = local_md5[:8] if local_md5 else "None"
                    r_md5_preview = r_md5[:8] if r_md5 else "None"
                    update_fota_progress(85, "md5", f"MD5校验: 本地={local_md5_preview}... 远端={r_md5_preview}...")
                    

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
                update_fota_progress(90, "upgrading", f"开始执行 lpUCM -i {remote_path}")
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
                try:
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                except NameError:
                    # 如果server_key未定义，记录警告（不应该发生）
                    log_srv(f"警告: server_key未定义，无法释放锁")
                except Exception as lock_error:
                    log_srv(f"释放服务器锁失败: {lock_error}")

        threading.Thread(target=do_fota, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_fota(f"FOTA异常: {e}")
        log_srv(f"FOTA异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@bp.route("/fota/progress/<task_id>")
def api_fota_progress(task_id):
    """SSE 推送FOTA进度"""
    def generate():
        last_progress = -1
        last_status = None
        import time as time_module
        last_send_time = time_module.time()
        
        while True:
            with fota_tasks_lock:
                task = fota_tasks.get(task_id)
            
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
            elif current_status != last_status:
                should_send = True
            # 保持SSE连接活跃，每0.5秒发送一次心跳（即使没有变化）
            elif current_time - last_send_time >= 0.5:
                should_send = True
            
            if should_send:
                yield f"data: {json.dumps({'progress': current_progress, 'status': current_status, 'step': task.get('step', ''), 'result': task.get('result')})}\n\n"
                last_progress = current_progress
                last_status = current_status
                last_send_time = current_time
            
            if current_status in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@bp.route("/batch-fota", methods=["POST"])
def api_batch_fota():
    """批量FOTA接口 - 支持自动端口检测和服务器类型验证"""
    try:
        port_str = request.form.get("port", "0")  # 允许传入0
        servers_json = request.form.get("servers", "[]")
        file = request.files.get("file")
        
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "未选择文件"}), 400
        
        filename = file.filename
        
        try:
            servers = json.loads(servers_json)
            if not servers or len(servers) == 0:
                return jsonify({"ok": False, "error": "服务器列表为空"}), 400
        except:
            return jsonify({"ok": False, "error": "服务器列表格式错误"}), 400
        
        # **关键增强：验证文件是否与所有服务器类型匹配**
        server_types = set()
        for server in servers:
            server_name = server.get("name", "")
            if server_name.startswith("LP-8650"):
                server_types.add("LP-8650")
            elif server_name.startswith("LP-8797"):
                server_types.add("LP-8797")
        
        if len(server_types) > 1:
            # 混合了不同类型的服务器
            return jsonify({
                "ok": False, 
                "error": f"批量FOTA不支持混合不同服务器类型。\n"
                        f"检测到服务器类型: {', '.join(server_types)}\n"
                        f"请选择相同类型的服务器进行批量升级"
            }), 400
        
        if len(server_types) == 0:
            return jsonify({"ok": False, "error": "无法识别服务器类型"}), 400
        
        # 使用第一个服务器检测端口和验证文件匹配
        first_server = servers[0]
        detected_port, message = fota_service.detect_port(first_server["name"], filename)
        
        if detected_port == 0:
            return jsonify({"ok": False, "error": message}), 400
        
        # 如果明确传入了端口，验证是否一致
        if port_str != "0" and port_str:
            explicit_port = int(port_str)
            if explicit_port != detected_port:
                return jsonify({
                    "ok": False,
                    "error": f"指定的端口 {explicit_port} 与文件检测到的端口 {detected_port} 不一致。\n"
                            f"检测信息: {message}"
                }), 400
            port = explicit_port
        else:
            port = detected_port
            log_fota(f"[批量FOTA] 自动检测端口: {port} ({message})")
        
        if port not in (22, 9999):
            return jsonify({"ok": False, "error": "端口必须是22或9999"}), 400

        # 真正的流式上传：使用临时文件，避免将整个文件读入内存
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

        # 定义FOTA任务函数（在循环外定义，确保能访问batch_id和temp_file_path）
        def do_batch_fota(tid, sname, sip, skey):
                transport = None
                sftp = None
                try:
                    
                    # 检查是否已取消
                    with batch_fota_tasks_lock:
                        batch_exists = batch_id in batch_fota_tasks
                        batch_cancelled = batch_fota_tasks[batch_id].get("cancelled", False) if batch_exists else False
                        if not batch_exists or batch_cancelled:
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
                            batch_exists = batch_id in batch_fota_tasks
                            batch_cancelled = batch_fota_tasks[batch_id].get("cancelled", False) if batch_exists else False
                            if not batch_exists or batch_cancelled:
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
                                remove_ok, remove_error = remote_remove(sname, sip, port, remote_path)
                                if not remove_ok:
                                    log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 删除远端文件失败: {remove_error}")
                                    # 删除失败不影响上传，继续执行
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
                        batch_exists_check = batch_id in batch_fota_tasks
                        batch_cancelled_check = batch_fota_tasks[batch_id].get("cancelled", False) if batch_exists_check else False
                        if not batch_exists_check or batch_cancelled_check:
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
                                sname, sip, port, fota_target_dir, filename, stream=task_file_obj, file_size=actual_file_size, progress_callback=upload_progress_cb, batch_id=batch_id, task_id=tid, batch_fota_tasks=batch_fota_tasks, batch_fota_tasks_lock=batch_fota_tasks_lock, fota_transports=fota_transports, fota_transports_lock=fota_transports_lock
                            )
                        except Exception as upload_exc:
                            log_fota(f"[批量FOTA/{batch_id}] [{sname}/{sip}:{port}] 上传文件时发生异常: {type(upload_exc).__name__}: {upload_exc}")
                            raise
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
                            batch_exists_after_upload = batch_id in batch_fota_tasks
                            batch_cancelled_after_upload = batch_fota_tasks[batch_id].get("cancelled", False) if batch_exists_after_upload else False
                            if not batch_exists_after_upload or batch_cancelled_after_upload:
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
                                # 使用 batch_fota_tasks[batch_id]["tasks"] 而不是外层的 task_ids
                                # 这样可以避免作用域问题，并且确保使用的是最新的任务列表
                                batch_task_ids = batch_fota_tasks[batch_id].get("tasks", [])
                                with fota_tasks_lock:
                                    for task_id_in_batch in batch_task_ids:
                                        if task_id_in_batch in fota_tasks:
                                            task_status = fota_tasks[task_id_in_batch]["status"]
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

        # 更新批量任务的任务列表（在启动线程之前，确保batch_id已存在）
        with batch_fota_tasks_lock:
            if batch_id in batch_fota_tasks:
                batch_fota_tasks[batch_id]["tasks"] = [t["task_id"] for t in task_ids]

        # 启动FOTA任务（在更新任务列表之后）
        for task_info in task_ids:
            task_id = task_info["task_id"]
            server_name = task_info["server_name"]
            server_ip = task_info["server_ip"]
            server_key = task_info["server_key"]
            threading.Thread(target=do_batch_fota, args=(task_id, server_name, server_ip, server_key), daemon=True).start()

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


@bp.route("/batch-fota/progress/<batch_id>")
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


@bp.route("/batch-fota/cancel/<batch_id>", methods=["POST"])
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


@bp.route("/fota/detect-port", methods=["POST"])
def api_fota_detect_port():
    """检测文件名对应的端口"""
    try:
        data = request.get_json()
        server_name = data.get("server_name", "").strip()
        filename = data.get("filename", "").strip()
        
        if not server_name or not filename:
            return jsonify({"ok": False, "error": "参数缺失"}), 400
        
        port, message = fota_service.detect_port(server_name, filename)
        
        if port == 0:
            return jsonify({"ok": False, "error": message}), 400
        
        return jsonify({
            "ok": True,
            "port": port,
            "message": message
        })
    except Exception as e:
        log_srv(f"端口检测异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500
