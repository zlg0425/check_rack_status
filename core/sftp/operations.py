"""
SFTP 操作模块
"""

import os
import posixpath
import socket
import paramiko
import io
import shlex
import hashlib

# 从 app.utils.config 导入配置和认证函数
from app.utils.config import (
    ssh_timeout,
    resolve_key,
    resolve_auth_mode,
    resolve_key_path,
)
import app.utils.config as config  # 使用模块引用以获取动态更新的值

# 从 core.ssh.transport 导入 create_transport
from core.ssh.transport import create_transport

# 从 app.utils.helpers 导入工具函数
from app.utils.helpers import (
    run_remote_command,
    parse_md5_output,
)


def safe_close_transport(transport):
    """安全地关闭 paramiko Transport，避免在解释器关闭时创建新线程"""
    if transport:
        try:
            # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
            if transport.is_active():
                transport.close()
        except (RuntimeError, Exception):
            # 忽略 "can't create new thread at interpreter shutdown" 错误
            pass


def validate_remote_path(path: str):
    """验证远程路径，防止路径遍历攻击
    Args:
        path: 要验证的路径
    Returns:
        (is_valid, error_message): 如果有效返回(True, None)，否则返回(False, 错误信息)
    """
    if not path:
        return False, "路径不能为空"
    
    # 检查路径遍历攻击（../, ..\, 等）
    if ".." in path:
        return False, "路径不能包含 '..'，防止路径遍历攻击"
    
    # 检查绝对路径（允许）
    # 检查特殊字符
    forbidden_chars = ['\x00', '\r', '\n']
    for char in forbidden_chars:
        if char in path:
            return False, f"路径包含非法字符: {repr(char)}"
    
    return True, None


def check_remote_disk_space(sftp: paramiko.SFTPClient, path: str, required_size: int):
    """检查远程磁盘空间
    Args:
        sftp: SFTP客户端
        path: 目标路径
        required_size: 需要的磁盘空间（字节）
    Returns:
        (has_space, error_message): 如果有足够空间返回(True, None)，否则返回(False, 错误信息)
    """
    try:
        # 尝试使用statvfs获取磁盘空间信息
        # 注意：不是所有SFTP服务器都支持statvfs
        try:
            statvfs = sftp.statvfs(path)
            free_space = statvfs.f_bavail * statvfs.f_frsize
            if free_space < required_size:
                return False, f"远程磁盘空间不足，需要 {required_size} 字节，可用 {free_space} 字节"
            return True, None
        except (IOError, AttributeError):
            # statvfs不支持或失败，尝试使用df命令（需要SSH连接）
            # 这里先跳过检查，返回成功
            # 实际应用中可以通过SSH执行df命令来检查
            return True, None
    except Exception as e:
        # 检查失败，但不阻止上传（可能是权限问题或服务器不支持）
        return True, None


def check_remote_file_exists(sftp: paramiko.SFTPClient, remote_path: str):
    """检查远程文件是否存在
    Args:
        sftp: SFTP客户端
        remote_path: 远程文件路径
    Returns:
        (exists, is_directory, error_message): 
            exists: 文件是否存在
            is_directory: 如果是目录返回True
            error_message: 错误信息（如果检查失败）
    """
    try:
        stat = sftp.stat(remote_path)
        is_directory = stat.st_mode & 0o040000
        return True, is_directory, None
    except IOError:
        return False, False, None
    except Exception as e:
        return False, False, str(e)


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_path: str):
    """确保远端目录存在"""
    parts = [p for p in remote_path.split("/") if p]
    current = ""
    for part in parts:
        current = current + "/" + part
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def sftp_upload(server_name: str, ip: str, port: int, target_dir: str, filename: str, data=None, progress_callback=None, stream=None, file_size=None, check_disk_space=True, check_file_exists=True):
    """将文件上传到指定服务器和端口，支持进度回调
    统一使用流式上传模式，支持stream参数（文件流）或data参数（bytes，内部转换为流）
    
    Args:
        server_name: 服务器名称
        ip: 服务器IP
        port: 端口
        target_dir: 目标目录
        filename: 文件名
        data: 文件数据（bytes），可选，如果提供则转换为流
        progress_callback: 进度回调函数
        stream: 文件流对象，优先使用
        file_size: 文件大小（字节），必需
        check_disk_space: 是否检查磁盘空间（默认True）
        check_file_exists: 是否检查文件是否存在（默认True，返回信息但不阻止上传）
    Returns:
        (success, result): success为True时result是文件路径，为False时result是错误信息或文件存在信息
            如果check_file_exists=True且文件存在，返回(True, {"path": remote_path, "exists": True, "is_directory": False})
    """
    # #region agent log
    import json
    import time as time_module
    try:
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": "system",
                "runId": "run1",
                "hypothesisId": "SFTP_UPLOAD",
                "location": "core/sftp/operations.py:sftp_upload:start",
                "message": "sftp_upload函数调用",
                "data": {
                    "server_name": server_name,
                    "ip": ip,
                    "port": port,
                    "filename": filename,
                    "has_stream": stream is not None,
                    "has_data": data is not None,
                    "file_size": file_size
                },
                "timestamp": int(time_module.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
    # 验证路径，防止路径遍历攻击
    path_valid, path_error = validate_remote_path(target_dir)
    if not path_valid:
        return False, f"目标目录路径无效: {path_error}"
    
    path_valid, path_error = validate_remote_path(filename)
    if not path_valid:
        return False, f"文件名无效: {path_error}"
    
    auth_mode = resolve_auth_mode(server_name)
    safe_dir = target_dir.rstrip("/") or "/"
    remote_path = posixpath.join(safe_dir, filename)
    
    # 统一为流式模式：优先使用stream，如果提供data则转换为流
    if stream is not None:
        # 流式模式
        if file_size is None:
            return False, "流式模式需要提供file_size参数"
        total_size = file_size
        file_obj = stream
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "SFTP_UPLOAD_STREAM_CHECK",
                    "location": "core/sftp/operations.py:sftp_upload:stream_check",
                    "message": "检查文件流状态",
                    "data": {
                        "server_name": server_name,
                        "ip": ip,
                        "port": port,
                        "filename": filename,
                        "file_size": file_size,
                        "stream_type": type(stream).__name__,
                        "stream_seekable": stream.seekable() if hasattr(stream, 'seekable') else "unknown",
                        "stream_tell": stream.tell() if hasattr(stream, 'tell') else "unknown",
                        "stream_closed": stream.closed if hasattr(stream, 'closed') else "unknown"
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 确保文件流位置在开头（如果支持seek）
        if hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
            current_pos = file_obj.tell()
            if current_pos != 0:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": "system",
                            "runId": "run1",
                            "hypothesisId": "SFTP_UPLOAD_STREAM_SEEK",
                            "location": "core/sftp/operations.py:sftp_upload:stream_seek",
                            "message": "重置文件流位置到开头",
                            "data": {
                                "server_name": server_name,
                                "ip": ip,
                                "port": port,
                                "filename": filename,
                                "current_pos": current_pos
                            },
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                file_obj.seek(0)
    elif data is not None:
        # 如果提供data参数，转换为流（向后兼容）
        total_size = len(data)
        file_obj = io.BytesIO(data)
        if file_size is not None and file_size != total_size:
            # 如果同时提供了file_size，使用file_size（可能更准确）
            total_size = file_size
    else:
        return False, "必须提供stream或data参数"
    
    # 根据文件大小动态调整chunk size以提高上传速度
    # 使用更大的chunk size以减少网络往返次数和系统调用开销
    if total_size > 100 * 1024 * 1024:  # >100MB
        chunk_size = 4 * 1024 * 1024  # 4MB，进一步增大以提高速度
    elif total_size > 10 * 1024 * 1024:  # >10MB
        chunk_size = 2 * 1024 * 1024  # 2MB
    else:
        chunk_size = 1024 * 1024  # 1MB
    
    def putfo_progress_callback(transferred, total):
        if progress_callback:
            try:
                progress_callback(transferred, total)
            except Exception as e:
                # 如果进度回调抛出异常（如取消），重新抛出以停止上传
                if "任务已取消" in str(e) or "取消" in str(e):
                    raise
                # 其他异常忽略，继续上传
                pass

    if auth_mode == "none":
        try:
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(config.ssh_username)
            
            sftp = paramiko.SFTPClient.from_transport(transport)
            ensure_remote_dir(sftp, safe_dir)
            
            # 检查文件是否存在
            file_exists_info = None
            if check_file_exists:
                exists, is_dir, error = check_remote_file_exists(sftp, remote_path)
                if exists:
                    if is_dir:
                        return False, f"目标路径是目录而不是文件: {remote_path}"
                    file_exists_info = {"exists": True, "is_directory": False}
            
            # 检查磁盘空间
            if check_disk_space:
                has_space, space_error = check_remote_disk_space(sftp, safe_dir, total_size)
                if not has_space:
                    sftp.close()
                    safe_close_transport(transport)
                    return False, space_error
            
            # 在调用putfo之前，再次确保文件对象位置在开头（防止在检查磁盘空间等操作中位置被改变）
            if hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
                current_pos = file_obj.tell()
                if current_pos != 0:
                    # #region agent log
                    try:
                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "sessionId": "system",
                                "runId": "run1",
                                "hypothesisId": "SFTP_UPLOAD_FINAL_SEEK",
                                "location": "core/sftp/operations.py:sftp_upload:final_seek",
                                "message": "在putfo调用前再次重置文件流位置",
                                "data": {
                                    "server_name": server_name,
                                    "ip": ip,
                                    "port": port,
                                    "filename": filename,
                                    "current_pos": current_pos
                                },
                                "timestamp": int(time_module.time() * 1000)
                            }) + '\n')
                    except Exception:
                        pass
                    # #endregion
                    file_obj.seek(0)
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_BEFORE_PUTFO",
                        "location": "core/sftp/operations.py:sftp_upload:before_putfo",
                        "message": "准备调用putfo上传",
                        "data": {
                            "server_name": server_name,
                            "ip": ip,
                            "port": port,
                            "filename": filename,
                            "remote_path": remote_path,
                            "total_size": total_size,
                            "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown",
                            "file_obj_seekable": file_obj.seekable() if hasattr(file_obj, 'seekable') else "unknown"
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            # 使用putfo方法上传，支持流式上传
            try:
                sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            except Exception as putfo_error:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": "system",
                            "runId": "run1",
                            "hypothesisId": "SFTP_UPLOAD_PUTFO_ERROR",
                            "location": "core/sftp/operations.py:sftp_upload:putfo_error",
                            "message": "putfo上传失败",
                            "data": {
                                "server_name": server_name,
                                "ip": ip,
                                "port": port,
                                "filename": filename,
                                "remote_path": remote_path,
                                "total_size": total_size,
                                "error": str(putfo_error),
                                "error_type": type(putfo_error).__name__,
                                "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown"
                            },
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                raise
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_AFTER_PUTFO",
                        "location": "core/sftp/operations.py:sftp_upload:after_putfo",
                        "message": "putfo上传完成",
                        "data": {
                            "server_name": server_name,
                            "ip": ip,
                            "port": port,
                            "filename": filename,
                            "remote_path": remote_path,
                            "total_size": total_size
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            sftp.close()
            safe_close_transport(transport)
            
            if file_exists_info:
                return True, {"path": remote_path, "exists": True, "is_directory": False}
            return True, remote_path
        except Exception as e:
            return False, str(e)

    key_path = resolve_key(server_name, port)
    if not key_path:
        return False, f"端口{port}未配置私钥"
    
    # 将相对路径转换为绝对路径
    key_path = resolve_key_path(key_path)
    
    if not os.path.exists(key_path):
        return False, f"私钥文件不存在: {key_path}"

    try:
        private_key = paramiko.RSAKey.from_private_key_file(key_path)
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
        transport.auth_publickey(username=config.ssh_username, key=private_key)

        sftp = paramiko.SFTPClient.from_transport(transport)
        ensure_remote_dir(sftp, safe_dir)
        
        # 检查文件是否存在
        file_exists_info = None
        if check_file_exists:
            exists, is_dir, error = check_remote_file_exists(sftp, remote_path)
            if exists:
                if is_dir:
                    sftp.close()
                    safe_close_transport(transport)
                    return False, f"目标路径是目录而不是文件: {remote_path}"
                file_exists_info = {"exists": True, "is_directory": False}
        
        # 检查磁盘空间
        if check_disk_space:
            has_space, space_error = check_remote_disk_space(sftp, safe_dir, total_size)
            if not has_space:
                sftp.close()
                safe_close_transport(transport)
                return False, space_error
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "SFTP_UPLOAD_BEFORE_PUTFO",
                    "location": "core/sftp/operations.py:sftp_upload:before_putfo",
                    "message": "准备调用putfo上传",
                    "data": {
                        "server_name": server_name,
                        "ip": ip,
                        "port": port,
                        "filename": filename,
                        "remote_path": remote_path,
                        "total_size": total_size,
                        "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown",
                        "file_obj_seekable": file_obj.seekable() if hasattr(file_obj, 'seekable') else "unknown"
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 使用putfo方法上传，支持流式上传
        try:
            sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
        except Exception as putfo_error:
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_PUTFO_ERROR",
                        "location": "core/sftp/operations.py:sftp_upload:putfo_error",
                        "message": "putfo上传失败",
                        "data": {
                            "server_name": server_name,
                            "ip": ip,
                            "port": port,
                            "filename": filename,
                            "remote_path": remote_path,
                            "total_size": total_size,
                            "error": str(putfo_error),
                            "error_type": type(putfo_error).__name__,
                            "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown"
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            raise
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "SFTP_UPLOAD_AFTER_PUTFO",
                    "location": "core/sftp/operations.py:sftp_upload:after_putfo",
                    "message": "putfo上传完成",
                    "data": {
                        "server_name": server_name,
                        "ip": ip,
                        "port": port,
                        "filename": filename,
                        "remote_path": remote_path,
                        "total_size": total_size
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        sftp.close()
        safe_close_transport(transport)
        
        if file_exists_info:
            return True, {"path": remote_path, "exists": True, "is_directory": False}
        return True, remote_path
    except Exception as e:
        return False, str(e)


def sftp_download(server_name: str, ip: str, port: int, remote_path: str, local_path: str, progress_callback=None):
    """从指定服务器和端口下载文件或文件夹，支持进度回调
    Args:
        server_name: 服务器名称
        ip: 服务器IP
        port: 端口
        remote_path: 远程文件或文件夹路径
        local_path: 本地保存路径（文件）或目录（文件夹）
        progress_callback: 进度回调函数 callback(transferred, total)
    Returns:
        (success: bool, info: str) - 成功返回(True, 本地路径)，失败返回(False, 错误信息)
    """
    auth_mode = resolve_auth_mode(server_name)
    
    def getfo_progress_callback(transferred, total):
        if progress_callback:
            progress_callback(transferred, total)
    
    # 用于跟踪文件夹下载的总进度
    total_transferred = [0]  # 使用列表以便在嵌套函数中修改
    total_size = [0]  # 总大小
    
    def calculate_total_size(sftp, remote_path):
        """递归计算文件夹总大小"""
        total = 0
        try:
            file_stat = sftp.stat(remote_path)
            if file_stat.st_mode & 0o040000:  # 目录
                try:
                    items = sftp.listdir_attr(remote_path)
                    for item in items:
                        # 跳过符号链接和设备文件
                        if item.st_mode & 0o120000:  # 符号链接
                            continue
                        item_path = posixpath.join(remote_path, item.filename)
                        if item.st_mode & 0o040000:  # 子目录
                            total += calculate_total_size(sftp, item_path)
                        elif item.st_mode & 0o100000:  # 普通文件
                            total += item.st_size
                        # 其他类型文件跳过
                except IOError:
                    # 无法访问目录，跳过
                    pass
            else:  # 文件
                total = file_stat.st_size
        except Exception:
            # 计算大小失败，返回0（不影响下载，只是进度可能不准确）
            pass
        return total
    
    def download_file(sftp, remote_file_path, local_file_path, file_size=None):
        """下载单个文件"""
        # 检查本地文件是否已存在
        if os.path.exists(local_file_path):
            if os.path.isdir(local_file_path):
                raise Exception(f"本地路径是目录而不是文件: {local_file_path}")
            # 文件已存在，删除后重新下载（覆盖策略）
            try:
                os.remove(local_file_path)
            except Exception as e:
                raise Exception(f"无法删除已存在的文件 {local_file_path}: {str(e)}")
        
        # 确保本地目录存在
        local_dir = os.path.dirname(local_file_path)
        if local_dir:
            if os.path.exists(local_dir):
                # 如果路径已存在，检查是否是目录
                if not os.path.isdir(local_dir):
                    raise Exception(f"本地路径已存在但不是目录: {local_dir}")
            else:
                # 路径不存在，创建目录
                try:
                    os.makedirs(local_dir, exist_ok=True)
                except OSError as e:
                    # 检查是否是权限错误
                    if e.errno == 13:  # Permission denied
                        raise Exception(f"权限不足，无法创建本地目录 {local_dir}")
                    elif e.errno == 28:  # No space left on device
                        raise Exception(f"磁盘空间不足，无法创建本地目录 {local_dir}")
                    else:
                        raise Exception(f"无法创建本地目录 {local_dir}: {str(e)}")
        
        file_last_transferred = [0]  # 当前文件已传输的字节数
        local_file_handle = None
        
        def file_progress_callback(transferred, total):
            # 更新总进度
            if progress_callback and total_size[0] > 0:
                try:
                    # 计算当前文件已传输的增量
                    delta = transferred - file_last_transferred[0]
                    file_last_transferred[0] = transferred
                    total_transferred[0] += delta
                    progress_callback(total_transferred[0], total_size[0])
                except Exception:
                    # 进度回调异常不应中断下载
                    pass
        
        file_last_transferred[0] = 0
        
        try:
            # 检查磁盘空间（如果知道文件大小）
            if file_size:
                try:
                    import shutil
                    stat = shutil.disk_usage(local_dir if local_dir else os.path.dirname(os.path.abspath(local_file_path)))
                    if stat.free < file_size:
                        raise Exception(f"磁盘空间不足，需要 {file_size} 字节，可用 {stat.free} 字节")
                except ImportError:
                    # Python < 3.3 不支持 shutil.disk_usage，跳过检查
                    pass
            
            local_file_handle = open(local_file_path, 'wb')
            try:
                sftp.getfo(remote_file_path, local_file_handle, callback=file_progress_callback)
            except Exception as e:
                # 下载失败，删除不完整的文件
                local_file_handle.close()
                local_file_handle = None
                try:
                    if os.path.exists(local_file_path):
                        os.remove(local_file_path)
                except:
                    pass
                raise
            finally:
                if local_file_handle:
                    local_file_handle.close()
        except IOError as e:
            # 如果文件打开失败，可能是目录不存在，再次尝试创建
            if local_file_handle:
                try:
                    local_file_handle.close()
                except:
                    pass
            if local_dir and not os.path.exists(local_dir):
                try:
                    os.makedirs(local_dir, exist_ok=True)
                    local_file_handle = open(local_file_path, 'wb')
                    try:
                        sftp.getfo(remote_file_path, local_file_handle, callback=file_progress_callback)
                    except Exception as e2:
                        local_file_handle.close()
                        if os.path.exists(local_file_path):
                            try:
                                os.remove(local_file_path)
                            except:
                                pass
                        raise Exception(f"下载文件失败 {remote_file_path}: {str(e2)}")
                    finally:
                        if local_file_handle:
                            local_file_handle.close()
                except Exception as e2:
                    raise Exception(f"无法创建文件 {local_file_path}: {str(e2)}")
            else:
                raise
    
    def download_directory(sftp, remote_dir_path, local_dir_path):
        """递归下载文件夹"""
        # 确保本地目录存在
        if os.path.exists(local_dir_path):
            # 如果路径已存在，检查是否是目录
            if not os.path.isdir(local_dir_path):
                return False, f"本地路径已存在但不是目录: {local_dir_path}"
        else:
            # 路径不存在，创建目录
            try:
                os.makedirs(local_dir_path, exist_ok=True)
            except Exception as e:
                return False, f"无法创建本地目录 {local_dir_path}: {str(e)}"
        
        # 列出远程目录内容
        try:
            items = sftp.listdir_attr(remote_dir_path)
        except IOError as e:
            return False, f"无法访问远程目录: {str(e)}"
        
        for item in items:
            remote_item_path = posixpath.join(remote_dir_path, item.filename)
            local_item_path = os.path.join(local_dir_path, item.filename)
            
            # 处理符号链接（0o120000）
            if item.st_mode & 0o120000:  # 符号链接
                # 尝试验证是否真的是符号链接
                try:
                    real_path = sftp.readlink(remote_item_path)
                    # 如果readlink成功，说明是真正的符号链接，跳过
                    continue
                except:
                    # readlink失败，可能是文件模式问题，尝试作为普通文件处理
                    # 某些系统可能错误地将普通文件识别为符号链接
                    # 尝试直接下载
                    try:
                        download_file(sftp, remote_item_path, local_item_path, item.st_size)
                    except Exception as e:
                        # 如果下载失败，跳过
                        continue
            
            if item.st_mode & 0o040000:  # 目录
                # 递归下载子目录
                ok, info = download_directory(sftp, remote_item_path, local_item_path)
                if not ok:
                    return False, info
            elif item.st_mode & 0o100000:  # 普通文件
                # 下载文件
                try:
                    download_file(sftp, remote_item_path, local_item_path, item.st_size)
                except Exception as e:
                    return False, f"下载文件失败 {remote_item_path}: {str(e)}"
            # 其他类型的文件（设备文件、管道等）跳过
        
        return True, local_dir_path
    
    if auth_mode == "none":
        transport = None
        sftp = None
        try:
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(config.ssh_username)
            
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 检查远程路径是文件还是目录
            try:
                # 先使用lstat检查（不跟随符号链接）
                try:
                    file_stat = sftp.lstat(remote_path)
                except:
                    # 如果lstat失败，使用stat
                    file_stat = sftp.stat(remote_path)
                
                is_directory = file_stat.st_mode & 0o040000  # 检查是否是目录
                is_symlink = file_stat.st_mode & 0o120000  # 检查是否是符号链接
                
                # 如果是符号链接，尝试解析真实路径
                if is_symlink:
                    try:
                        real_path = sftp.readlink(remote_path)
                        if not posixpath.isabs(real_path):
                            # 相对路径，转换为绝对路径
                            real_path = posixpath.join(posixpath.dirname(remote_path), real_path)
                        # 重新检查真实路径
                        file_stat = sftp.stat(real_path)
                        is_directory = file_stat.st_mode & 0o040000
                        remote_path = real_path  # 使用真实路径
                    except Exception:
                        # 无法解析符号链接，可能是误判（某些系统可能错误地将普通文件识别为符号链接）
                        # 如果readlink失败，尝试直接下载文件
                        # 不返回错误，继续作为普通文件处理
                        pass
            except IOError as e:
                error_msg = str(e)
                if "Permission denied" in error_msg or "permission" in error_msg.lower():
                    return False, f"权限不足，无法访问远程路径: {remote_path}"
                elif "No such file" in error_msg or "not found" in error_msg.lower():
                    return False, f"远程路径不存在: {remote_path}"
                else:
                    return False, f"远程路径无法访问: {remote_path} ({error_msg})"
            
            # 如果是文件夹，先计算总大小
            if is_directory:
                if progress_callback:
                    total_size[0] = calculate_total_size(sftp, remote_path)
                    if total_size[0] > 0:
                        progress_callback(0, total_size[0])  # 初始化进度
                # 下载文件夹
                ok, info = download_directory(sftp, remote_path, local_path)
                if ok:
                    return True, local_path
                else:
                    return False, info
            else:
                # 下载文件
                if progress_callback:
                    total_size[0] = file_stat.st_size
                download_file(sftp, remote_path, local_path, file_stat.st_size)
                return True, local_path
            
        except Exception as e:
            return False, str(e)
        finally:
            if sftp:
                sftp.close()
            safe_close_transport(transport)
    else:
        # key模式
        key_path = resolve_key(server_name, port)
        if not key_path:
            return False, f"端口{port}未配置私钥"
        
        # 将相对路径转换为绝对路径
        key_path = resolve_key_path(key_path)
        
        if not os.path.exists(key_path):
            return False, f"私钥文件不存在: {key_path}"
        
        transport = None
        sftp = None
        try:
            private_key = paramiko.RSAKey.from_private_key_file(key_path)
            transport = paramiko.Transport((ip, port))
            transport.start_client(timeout=ssh_timeout)
            transport.auth_publickey(username=config.ssh_username, key=private_key)
            
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 检查远程路径是文件还是目录
            try:
                # 先使用lstat检查（不跟随符号链接）
                try:
                    file_stat = sftp.lstat(remote_path)
                except:
                    # 如果lstat失败，使用stat
                    file_stat = sftp.stat(remote_path)
                
                is_directory = file_stat.st_mode & 0o040000  # 检查是否是目录
                is_symlink = file_stat.st_mode & 0o120000  # 检查是否是符号链接
                
                # 如果是符号链接，尝试解析真实路径
                if is_symlink:
                    try:
                        real_path = sftp.readlink(remote_path)
                        if not posixpath.isabs(real_path):
                            # 相对路径，转换为绝对路径
                            real_path = posixpath.join(posixpath.dirname(remote_path), real_path)
                        # 重新检查真实路径
                        file_stat = sftp.stat(real_path)
                        is_directory = file_stat.st_mode & 0o040000
                        remote_path = real_path  # 使用真实路径
                    except Exception:
                        # 无法解析符号链接，可能是误判（某些系统可能错误地将普通文件识别为符号链接）
                        # 如果readlink失败，尝试直接下载文件
                        # 不返回错误，继续作为普通文件处理
                        pass
            except IOError as e:
                error_msg = str(e)
                if "Permission denied" in error_msg or "permission" in error_msg.lower():
                    return False, f"权限不足，无法访问远程路径: {remote_path}"
                elif "No such file" in error_msg or "not found" in error_msg.lower():
                    return False, f"远程路径不存在: {remote_path}"
                else:
                    return False, f"远程路径无法访问: {remote_path} ({error_msg})"
            
            # 如果是文件夹，先计算总大小
            if is_directory:
                if progress_callback:
                    total_size[0] = calculate_total_size(sftp, remote_path)
                    if total_size[0] > 0:
                        progress_callback(0, total_size[0])  # 初始化进度
                # 下载文件夹
                ok, info = download_directory(sftp, remote_path, local_path)
                if ok:
                    return True, local_path
                else:
                    return False, info
            else:
                # 下载文件
                if progress_callback:
                    total_size[0] = file_stat.st_size
                download_file(sftp, remote_path, local_path, file_stat.st_size)
                return True, local_path
            
        except Exception as e:
            return False, str(e)
        finally:
            if sftp:
                sftp.close()
            safe_close_transport(transport)


def remote_md5(server_name: str, ip: str, port: int, remote_path: str):
    """计算远端文件 MD5
    根据服务器类型选择不同方法：
    - LP-8650系列：通过SFTP采样读取文件内容在本地计算MD5（部分计算，加快速度）
    - LP-8797系列：使用md5sum命令获取完整文件的MD5（完整计算）
    """
    
    # 根据服务器类型选择方法
    use_sftp = server_name.startswith("LP-8650")
    
    if use_sftp:
        # LP-8650系列：通过SFTP采样读取文件内容，在本地计算MD5（部分计算）
        try:
            transport = create_transport(server_name, ip, port)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 获取文件大小
            file_stat = sftp.stat(remote_path)
            file_size = file_stat.st_size
            
            # 对于小文件（<10MB），完整读取计算MD5
            if file_size < 10 * 1024 * 1024:
                chunk_size = 1024 * 1024  # 1MB
                md5_hash = hashlib.md5()
                with sftp.file(remote_path, "rb") as remote_file:
                    while True:
                        chunk = remote_file.read(chunk_size)
                        if not chunk:
                            break
                        md5_hash.update(chunk)
                md5_val = md5_hash.hexdigest()
            else:
                # 对于大文件，使用采样MD5（部分计算）
                sample_size = 1024 * 1024  # 1MB
                md5_hash = hashlib.md5()
                
                with sftp.file(remote_path, "rb") as remote_file:
                    # 1. 读取开头1MB
                    chunk = remote_file.read(sample_size)
                    if chunk:
                        md5_hash.update(chunk)
                    
                    # 2. 读取中间1MB（文件中间位置）
                    if file_size > sample_size * 2:
                        mid_start = (file_size - sample_size) // 2
                        remote_file.seek(mid_start)
                        chunk = remote_file.read(sample_size)
                        if chunk:
                            md5_hash.update(chunk)
                    
                    # 3. 读取结尾1MB
                    if file_size > sample_size:
                        remote_file.seek(file_size - sample_size)
                        chunk = remote_file.read(sample_size)
                        if chunk:
                            md5_hash.update(chunk)
                    
                    # 4. 将实际文件大小也加入MD5计算，增加唯一性
                    # 注意：这里使用file_stat.st_size（实际文件大小），确保与本地MD5计算一致
                    md5_hash.update(str(file_size).encode())
                
                md5_val = md5_hash.hexdigest()
            
            sftp.close()
            safe_close_transport(transport)
            
            return True, md5_val
        except Exception as e:
            return False, f"通过SFTP计算MD5失败: {e}"
    else:
        # LP-8797系列：使用md5sum命令获取MD5
        # md5sum命令默认对完整文件计算MD5，与本地md5_bytes()方法保持一致
        cmd = f"/ifs/usr/bin/md5sum {shlex.quote(remote_path)}"
        ok, out = run_remote_command(server_name, ip, port, cmd)
        
        if not ok:
            return False, out
        
        md5_val = parse_md5_output(out)
        
        if not md5_val:
            return False, f"无法解析md5: {out}"
        
        # 验证MD5格式（32位十六进制字符串）
        if len(md5_val) == 32 and all(c in '0123456789abcdef' for c in md5_val.lower()):
            return True, md5_val
        else:
            return False, f"MD5格式无效: {md5_val}"


def remote_exists(server_name: str, ip: str, port: int, remote_path: str):
    """检查远端文件是否存在"""
    transport = None
    sftp = None
    try:
        transport = create_transport(server_name, ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.stat(remote_path)
        return True
    except FileNotFoundError:
        return False
    except IOError:
        return False
    except Exception:
        return False
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        safe_close_transport(transport)


def remote_remove(server_name: str, ip: str, port: int, remote_path: str):
    """删除远端文件"""
    transport = None
    sftp = None
    try:
        transport = create_transport(server_name, ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.remove(remote_path)
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        safe_close_transport(transport)


def sftp_upload_with_cancel(server_name: str, ip: str, port: int, target_dir: str, filename: str, data=None, progress_callback=None, batch_id=None, task_id=None, stream=None, file_size=None, batch_fota_tasks=None, batch_fota_tasks_lock=None, fota_transports=None, fota_transports_lock=None):
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
        batch_fota_tasks: 批量FOTA任务字典（可选，如果不提供则从app.routes.fota导入）
        batch_fota_tasks_lock: 批量FOTA任务锁（可选，如果不提供则从app.routes.fota导入）
        fota_transports: FOTA任务transport字典（可选，如果不提供则从app.routes.fota导入）
        fota_transports_lock: FOTA任务transport锁（可选，如果不提供则从app.routes.fota导入）
    
    Returns:
        (ok, info, transport, sftp): ok为True时info是文件路径，transport和sftp是连接对象；ok为False时info是错误信息
    """
    import time as time_module
    import json
    
    # #region agent log
    try:
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": "system",
                "runId": "run1",
                "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL",
                "location": "core/sftp/operations.py:sftp_upload_with_cancel:start",
                "message": "sftp_upload_with_cancel函数调用",
                "data": {
                    "server_name": server_name,
                    "ip": ip,
                    "port": port,
                    "filename": filename,
                    "batch_id": batch_id,
                    "task_id": task_id,
                    "has_stream": stream is not None,
                    "has_data": data is not None,
                    "file_size": file_size
                },
                "timestamp": int(time_module.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
    
    # 如果没有提供batch_fota_tasks等参数，尝试从app.routes.fota导入
    if batch_fota_tasks is None or batch_fota_tasks_lock is None:
        try:
            from app.routes.fota import batch_fota_tasks as imported_batch_fota_tasks, batch_fota_tasks_lock as imported_batch_fota_tasks_lock
            if batch_fota_tasks is None:
                batch_fota_tasks = imported_batch_fota_tasks
            if batch_fota_tasks_lock is None:
                batch_fota_tasks_lock = imported_batch_fota_tasks_lock
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL",
                        "location": "core/sftp/operations.py:sftp_upload_with_cancel:import_batch_fota",
                        "message": "从app.routes.fota导入batch_fota_tasks",
                        "data": {
                            "batch_id": batch_id,
                            "imported": True
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
        except ImportError:
            # 如果导入失败，使用None（取消检查将被禁用）
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL",
                        "location": "core/sftp/operations.py:sftp_upload_with_cancel:import_batch_fota_failed",
                        "message": "从app.routes.fota导入batch_fota_tasks失败",
                        "data": {
                            "batch_id": batch_id
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            pass
    
    if fota_transports is None or fota_transports_lock is None:
        try:
            from app.routes.fota import fota_transports as imported_fota_transports, fota_transports_lock as imported_fota_transports_lock
            if fota_transports is None:
                fota_transports = imported_fota_transports
            if fota_transports_lock is None:
                fota_transports_lock = imported_fota_transports_lock
        except ImportError:
            # 如果导入失败，使用None（transport引用保存将被禁用）
            pass
    
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
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_STREAM_CHECK",
                    "location": "core/sftp/operations.py:sftp_upload_with_cancel:stream_check",
                    "message": "检查文件流状态",
                    "data": {
                        "server_name": server_name,
                        "ip": ip,
                        "port": port,
                        "filename": filename,
                        "file_size": file_size,
                        "stream_type": type(stream).__name__,
                        "stream_seekable": stream.seekable() if hasattr(stream, 'seekable') else "unknown",
                        "stream_tell": stream.tell() if hasattr(stream, 'tell') else "unknown",
                        "stream_closed": stream.closed if hasattr(stream, 'closed') else "unknown"
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 确保文件流位置在开头（如果支持seek）
        if hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
            current_pos = file_obj.tell()
            if current_pos != 0:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": "system",
                            "runId": "run1",
                            "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_STREAM_SEEK",
                            "location": "core/sftp/operations.py:sftp_upload_with_cancel:stream_seek",
                            "message": "重置文件流位置到开头",
                            "data": {
                                "server_name": server_name,
                                "ip": ip,
                                "port": port,
                                "filename": filename,
                                "current_pos": current_pos
                            },
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                file_obj.seek(0)
    elif data is not None:
        # 如果提供data参数，转换为流（向后兼容）
        total_size = len(data)
        file_obj = io.BytesIO(data)
        if file_size is not None and file_size != total_size:
            # 如果同时提供了file_size，使用file_size（可能更准确）
            total_size = file_size
    else:
        return False, "必须提供stream或data参数", None, None

    transport = None
    sftp = None
    
    try:
        if auth_mode == "none":
            # 使用模块引用获取 ssh_username（确保使用load_config()后的值）
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(config.ssh_username)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 确保目录存在
            ensure_remote_dir(sftp, safe_dir)
            
            # 保存transport引用以便终止时关闭
            if task_id and fota_transports is not None and fota_transports_lock is not None:
                with fota_transports_lock:
                    fota_transports[task_id] = {"transport": transport, "sftp": sftp}
            
            # 使用putfo方法上传，支持流式上传
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
                if batch_id and batch_fota_tasks is not None and batch_fota_tasks_lock is not None:
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
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_BEFORE_PUTFO",
                        "location": "core/sftp/operations.py:sftp_upload_with_cancel:before_putfo",
                        "message": "准备调用putfo上传",
                        "data": {
                            "server_name": server_name,
                            "ip": ip,
                            "port": port,
                            "filename": filename,
                            "remote_path": remote_path,
                            "total_size": total_size,
                            "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown",
                            "file_obj_seekable": file_obj.seekable() if hasattr(file_obj, 'seekable') else "unknown"
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            try:
                sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            except Exception as putfo_error:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": "system",
                            "runId": "run1",
                            "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_PUTFO_ERROR",
                            "location": "core/sftp/operations.py:sftp_upload_with_cancel:putfo_error",
                            "message": "putfo上传失败",
                            "data": {
                                "server_name": server_name,
                                "ip": ip,
                                "port": port,
                                "filename": filename,
                                "remote_path": remote_path,
                                "total_size": total_size,
                                "error": str(putfo_error),
                                "error_type": type(putfo_error).__name__,
                                "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown"
                            },
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                raise
            
            # 上传完成后清理引用（但保持连接打开，因为可能还需要用于MD5校验）
            return True, remote_path, transport, sftp
        else:
            key_path = resolve_key(server_name, port)
            if not key_path:
                return False, f"端口{port}未配置私钥", None, None
            
            # 将相对路径转换为绝对路径
            key_path = resolve_key_path(key_path)
            
            if not os.path.exists(key_path):
                return False, f"私钥文件不存在: {key_path}", None, None
            
            try:
                private_key = paramiko.RSAKey.from_private_key_file(key_path)
            except Exception as e:
                return False, f"无法加载密钥文件 {key_path}: {str(e)}", None, None
            
            transport = paramiko.Transport((ip, port))
            transport.start_client(timeout=ssh_timeout)
            # 使用模块引用获取 ssh_username（确保使用load_config()后的值）
            try:
                transport.auth_publickey(username=config.ssh_username, key=private_key)
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
            if task_id and fota_transports is not None and fota_transports_lock is not None:
                with fota_transports_lock:
                    fota_transports[task_id] = {"transport": transport, "sftp": sftp}
            
            # 使用putfo方法上传，支持流式上传
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
                if batch_id and batch_fota_tasks is not None and batch_fota_tasks_lock is not None:
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
            
            # #region agent log
            try:
                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        "sessionId": "system",
                        "runId": "run1",
                        "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_BEFORE_PUTFO",
                        "location": "core/sftp/operations.py:sftp_upload_with_cancel:before_putfo",
                        "message": "准备调用putfo上传",
                        "data": {
                            "server_name": server_name,
                            "ip": ip,
                            "port": port,
                            "filename": filename,
                            "remote_path": remote_path,
                            "total_size": total_size,
                            "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown",
                            "file_obj_seekable": file_obj.seekable() if hasattr(file_obj, 'seekable') else "unknown"
                        },
                        "timestamp": int(time_module.time() * 1000)
                    }) + '\n')
            except Exception:
                pass
            # #endregion
            
            try:
                sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            except Exception as putfo_error:
                # #region agent log
                try:
                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            "sessionId": "system",
                            "runId": "run1",
                            "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL_PUTFO_ERROR",
                            "location": "core/sftp/operations.py:sftp_upload_with_cancel:putfo_error",
                            "message": "putfo上传失败",
                            "data": {
                                "server_name": server_name,
                                "ip": ip,
                                "port": port,
                                "filename": filename,
                                "remote_path": remote_path,
                                "total_size": total_size,
                                "error": str(putfo_error),
                                "error_type": type(putfo_error).__name__,
                                "file_obj_tell": file_obj.tell() if hasattr(file_obj, 'tell') else "unknown"
                            },
                            "timestamp": int(time_module.time() * 1000)
                        }) + '\n')
                except Exception:
                    pass
                # #endregion
                raise
            
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
                safe_close_transport(transport)
            except:
                pass
        # 清理transport引用
        if task_id and fota_transports is not None and fota_transports_lock is not None:
            with fota_transports_lock:
                if task_id in fota_transports:
                    del fota_transports[task_id]
        
        # #region agent log
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": "system",
                    "runId": "run1",
                    "hypothesisId": "SFTP_UPLOAD_WITH_CANCEL",
                    "location": "core/sftp/operations.py:sftp_upload_with_cancel:exception",
                    "message": "sftp_upload_with_cancel发生异常",
                    "data": {
                        "server_name": server_name,
                        "ip": ip,
                        "port": port,
                        "filename": filename,
                        "batch_id": batch_id,
                        "task_id": task_id,
                        "error_type": type(e).__name__,
                        "error_message": error_msg
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        if "任务已取消" in error_msg:
            return False, "任务已取消", None, None
        return False, error_msg, None, None


__all__ = [
    'sftp_upload',
    'sftp_download',
    'ensure_remote_dir',
    'remote_exists',
    'remote_remove',
    'remote_md5',
    'validate_remote_path',
    'check_remote_disk_space',
    'check_remote_file_exists',
    'sftp_upload_with_cancel',
]
