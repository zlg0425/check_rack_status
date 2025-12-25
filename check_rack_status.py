#!/usr/bin/env python3
"""
服务器端口连通性与SSH登录监控脚本（多端口独立密钥版）
"""

import json
import os
import posixpath
import re
import socket
import paramiko
import time
import threading
import hashlib
import shlex
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# 抑制 paramiko 的内部异常输出
import sys
if sys.platform == 'win32':
    # Windows: 使用 nul 设备
    try:
        paramiko.util.log_to_file('nul')
    except:
        pass
else:
    # Linux/Mac: 使用 /dev/null
    try:
        paramiko.util.log_to_file('/dev/null')
    except:
        pass
# 设置 paramiko 日志级别为 ERROR，只显示严重错误
logging.getLogger("paramiko").setLevel(logging.ERROR)

# ============== 配置区域（已移至 config.json） ==============
CONFIG_FILE = "config.json"

server_dict = {}
key_mapping = {}
group_key_mapping = {}
group_auth_mode = {}
ssh_username = ""
ssh_timeout = 5
check_interval = 60
auth_mode_default = "key"
version_command = "cat /mnt/etc/version"
upload_target_dir = "/tmp"
fota_target_dir = "/opt/data/fota"
fota_filename_validation = {}
# ========================================================


def load_config(config_path: str = CONFIG_FILE) -> str:
    """从JSON配置文件加载监控参数"""
    global server_dict, key_mapping, group_key_mapping, group_auth_mode
    global ssh_username, ssh_timeout, check_interval, auth_mode_default, version_command, upload_target_dir, fota_target_dir, fota_filename_validation

    script_dir = os.path.dirname(os.path.abspath(__file__))
    abs_path = os.path.join(script_dir, config_path)

    with open(abs_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    server_dict = cfg.get("servers", {})

    raw_key_mapping = cfg.get("key_mapping", {})
    key_mapping = {}
    for port, key in raw_key_mapping.items():
        try:
            key_mapping[int(port)] = key
        except (TypeError, ValueError):
            # 忽略无法转换为整数的端口配置
            continue

    raw_group_key_mapping = cfg.get("group_key_mapping", {})
    group_key_mapping = {}
    for prefix, port_map in raw_group_key_mapping.items():
        parsed = {}
        for port, key in port_map.items():
            try:
                parsed[int(port)] = key
            except (TypeError, ValueError):
                continue
        group_key_mapping[prefix] = parsed

    ssh_username = cfg.get("ssh_username", "root")
    ssh_timeout = int(cfg.get("ssh_timeout", 5))
    check_interval = int(cfg.get("check_interval", 300))
    auth_mode_default = str(cfg.get("auth_mode_default", "key")).lower()
    version_command = cfg.get("version_command", version_command)
    upload_target_dir = cfg.get("upload_target_dir", upload_target_dir)
    fota_target_dir = cfg.get("fota_target_dir", fota_target_dir)
    # 更新字典内容而不是重新赋值，以保持导入引用的有效性
    fota_filename_validation.clear()
    fota_filename_validation.update(cfg.get("fota_filename_validation", {}))

    raw_group_auth_mode = cfg.get("group_auth_mode", {})
    group_auth_mode = {k: str(v).lower() for k, v in raw_group_auth_mode.items()}

    return abs_path


def resolve_key(server_name: str, port: int):
    """根据服务器名称和端口解析私钥路径，优先级：服务器前缀 > 全局端口"""
    # 按前缀匹配（最长前缀优先）
    if group_key_mapping:
        for prefix in sorted(group_key_mapping.keys(), key=len, reverse=True):
            if server_name.startswith(prefix):
                key_path = group_key_mapping[prefix].get(port)
                if key_path:
                    return key_path

    # 回落到全局按端口配置
    return key_mapping.get(port)


def resolve_auth_mode(server_name: str):
    """根据服务器名称解析认证方式，优先级：前缀 > 默认"""
    if group_auth_mode:
        for prefix in sorted(group_auth_mode.keys(), key=len, reverse=True):
            if server_name.startswith(prefix):
                return group_auth_mode[prefix]
    return auth_mode_default


def natural_key(text: str):
    """将名称拆分为文本和数字块，用于自然排序"""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r'(\d+)', text)]


def is_benign_stderr(msg: str) -> bool:
    """判断是否为可忽略的 stderr 警告"""
    if not msg:
        return True
    lower = msg.lower()
    # 忽略主目录不存在的警告（QNX系统常见）
    if "could not chdir to home directory" in lower:
        return True
    # 忽略ldd库加载失败的警告（某些QNX系统可能缺少某些库，但不影响lpUCM执行）
    if "ldd:fatality" in lower and "could not load library" in lower:
        return True
    return False


def filter_benign_stdout(msg: str) -> str:
    """过滤stdout中的可忽略警告，返回过滤后的字符串"""
    if not msg:
        return msg
    lines = msg.split('\n')
    filtered_lines = []
    for line in lines:
        lower = line.lower()
        # 忽略主目录不存在的警告（QNX系统常见，出现在stdout中）
        if "could not chdir to home directory" in lower:
            continue
        filtered_lines.append(line)
    return '\n'.join(filtered_lines).strip()


def fetch_version_via_sftp(transport: paramiko.Transport, path: str):
    """尝试通过 SFTP 直接读取版本文件"""
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        with sftp.file(path, "r") as f:
            return True, f.read().decode(errors="ignore").strip()
    except Exception as e:
        return False, str(e)


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
    import io
    
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
            transport.auth_none(ssh_username)
            
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
                    transport.close()
                    return False, space_error
            
            # 使用putfo方法上传，支持流式上传
            sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            sftp.close()
            transport.close()
            
            if file_exists_info:
                return True, {"path": remote_path, "exists": True, "is_directory": False}
            return True, remote_path
        except Exception as e:
            return False, str(e)

    key_path = resolve_key(server_name, port)
    if not key_path:
        return False, f"端口{port}未配置私钥"

    try:
        private_key = paramiko.RSAKey.from_private_key_file(key_path)
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
        transport.auth_publickey(username=ssh_username, key=private_key)

        sftp = paramiko.SFTPClient.from_transport(transport)
        ensure_remote_dir(sftp, safe_dir)
        
        # 检查文件是否存在
        file_exists_info = None
        if check_file_exists:
            exists, is_dir, error = check_remote_file_exists(sftp, remote_path)
            if exists:
                if is_dir:
                    sftp.close()
                    transport.close()
                    return False, f"目标路径是目录而不是文件: {remote_path}"
                file_exists_info = {"exists": True, "is_directory": False}
        
        # 检查磁盘空间
        if check_disk_space:
            has_space, space_error = check_remote_disk_space(sftp, safe_dir, total_size)
            if not has_space:
                sftp.close()
                transport.close()
                return False, space_error
        
        # 使用putfo方法上传，支持流式上传
        sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
        sftp.close()
        transport.close()
        
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
            transport.auth_none(ssh_username)
            
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
            if transport:
                transport.close()
    else:
        # key模式
        key_path = resolve_key(server_name, port)
        if not key_path:
            return False, f"端口{port}未配置私钥"
        
        transport = None
        sftp = None
        try:
            private_key = paramiko.RSAKey.from_private_key_file(key_path)
            transport = paramiko.Transport((ip, port))
            transport.start_client(timeout=ssh_timeout)
            transport.auth_publickey(username=ssh_username, key=private_key)
            
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
            if transport:
                transport.close()


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
            import hashlib
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
            transport.close()
            
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
    try:
        transport = create_transport(server_name, ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.stat(remote_path)
        sftp.close()
        transport.close()
        return True
    except FileNotFoundError:
        return False
    except IOError:
        return False
    except Exception:
        return False


def remote_remove(server_name: str, ip: str, port: int, remote_path: str):
    """删除远端文件"""
    try:
        transport = create_transport(server_name, ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.remove(remote_path)
        sftp.close()
        transport.close()
        return True, ""
    except Exception as e:
        return False, str(e)


def create_transport(server_name: str, ip: str, port: int) -> paramiko.Transport:
    """按认证模式创建并返回已认证的 Transport"""
    auth_mode = resolve_auth_mode(server_name)
    transport = None
    try:
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
    except Exception as e:
        if transport:
            try:
                transport.close()
            except:
                pass
        raise

    try:
        if auth_mode == "none":
            transport.auth_none(ssh_username)
            return transport

        key_path = resolve_key(server_name, port)
        if not key_path:
            transport.close()
            raise RuntimeError(f"端口{port}未配置私钥")
        private_key = paramiko.RSAKey.from_private_key_file(key_path)
        try:
            transport.auth_publickey(username=ssh_username, key=private_key)
        except paramiko.ssh_exception.SSHException as auth_err:
            # 如果认证失败且连接已关闭，提供更明确的错误信息
            if "No existing session" in str(auth_err) or not transport.is_alive():
                transport.close()
                raise paramiko.ssh_exception.SSHException(f"Connection closed by remote host during authentication: {auth_err}")
            raise
        return transport
    except Exception as e:
        if transport:
            try:
                transport.close()
            except:
                pass
        raise


def run_ucm_with_log(server_name: str, ip: str, port: int, ucm_file: str, log_file: str = None, tail_wait: int = 2):
    """启动日志会话B，2秒后启动A执行 lpUCM，A 结束后再等待 tail_wait 秒再停止日志。返回 (ok, info)。"""
    import os
    import time
    
    if log_file is None:
        # 确保日志目录存在
        log_dir = "logs/ucm_log"
        os.makedirs(log_dir, exist_ok=True)
        # 将时间戳转换为具体时间格式：YYYYMMDD_HHMMSS
        time_str = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        log_file = os.path.join(log_dir, f"ucm_{server_name}_{ip}_{port}_{time_str}.log")
    else:
        # 如果指定了日志文件，检查是否为相对路径（只有文件名）
        log_dir = os.path.dirname(log_file)
        if not log_dir:
            # 只有文件名，使用默认日志目录
            log_dir = "logs/ucm_log"
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, log_file)
        else:
            # 有目录路径，确保目录存在
            os.makedirs(log_dir, exist_ok=True)
    log_stop = threading.Event()
    log_err = []

    def tail_log():
        try:
            transport_b = create_transport(server_name, ip, port)
            session_b = transport_b.open_session(timeout=ssh_timeout)
            # 根据服务器类型选择不同的grep路径
            # 8650系列：使用 /ifs/bin/grep
            # 8797系列：使用 /ifs/usr/bin/grep
            if server_name.startswith("LP-8650"):
                grep_path = "/ifs/bin/grep"
            else:
                grep_path = "/ifs/usr/bin/grep"
            logview_cmd = f"LD_LIBRARY_PATH=/opt/usr/lib:/opt/usr/lib64 /opt/usr/bin/logview -w | {grep_path} UCM"
            session_b.exec_command(logview_cmd)
            log_bytes = 0
            with open(log_file, "ab") as lf:
                while not log_stop.is_set():
                    if session_b.recv_ready():
                        chunk = session_b.recv(4096)
                        if chunk:
                            lf.write(chunk)
                            lf.flush()
                            log_bytes += len(chunk)
                            try:
                                print(chunk.decode(errors="ignore"), end="")
                            except Exception:
                                pass
                    if session_b.recv_stderr_ready():
                        chunk = session_b.recv_stderr(4096)
                        if chunk:
                            lf.write(chunk)
                            lf.flush()
                            log_bytes += len(chunk)
                            try:
                                print(chunk.decode(errors="ignore"), end="")
                            except Exception:
                                pass
                    time.sleep(0.1)
            session_b.close()
            transport_b.close()
        except Exception as e:
            log_err.append(str(e))

    t = threading.Thread(target=tail_log, daemon=True)
    t.start()

    # 等待B稳定
    time.sleep(2)

    try:
        transport_a = create_transport(server_name, ip, port)
        session_a = transport_a.open_session(timeout=ssh_timeout)
        # 8650系列需要LD_LIBRARY_PATH包含/ifs/lib64/camera（libFUCamera.so的路径）和/ifs/lib64
        # 8797系列也需要LD_LIBRARY_PATH
        if server_name.startswith("LP-8650"):
            # 8650系列：libFUCamera.so在/ifs/lib64/camera/下，需要包含/ifs/lib64/camera和/ifs/lib64
            ucm_cmd = f"LD_LIBRARY_PATH=/ifs/lib64/camera:/ifs/lib64:/opt/usr/lib64:/opt/usr/lib /opt/usr/bin/lpUCM -i {shlex.quote(ucm_file)}"
        else:
            ucm_cmd = f"LD_LIBRARY_PATH=/opt/usr/lib:/opt/usr/lib64 /opt/usr/bin/lpUCM -i {shlex.quote(ucm_file)}"
        session_a.exec_command(ucm_cmd)
        # 读取全部输出
        stdout_data, stderr_data = _read_all(session_a)
        exit_code = session_a.recv_exit_status()
        session_a.close()
        transport_a.close()
    except Exception as e:
        log_stop.set()
        t.join(timeout=2)
        return False, f"执行 lpUCM 失败: {e}"

    # A结束后等待 tail_wait 秒，再停止日志
    time.sleep(max(tail_wait, 0))
    log_stop.set()
    t.join(timeout=2)

    if log_err:
        return False, f"日志会话异常: {log_err[0]}"

    success_marker = "swdl_lun_switch_bank:399"
    stderr_lower = stderr_data.lower() if stderr_data else ""
    success_in_stderr = success_marker in stderr_lower if stderr_lower else False

    # 过滤可忽略的stderr警告
    if stderr_data and is_benign_stderr(stderr_data):
        # 如果stderr只包含可忽略的警告，且命令成功执行，则视为成功
        if exit_code == 0 or success_in_stderr:
            return True, stdout_data.strip() or stderr_data.strip()
        # 如果命令失败，但stderr只包含可忽略的警告，检查是否有其他错误信息
        # 过滤掉可忽略的警告后，如果还有其他错误信息，则返回失败
        filtered_stderr = "\n".join([line for line in stderr_data.split("\n") if not is_benign_stderr(line)])
        if filtered_stderr.strip():
            return False, f"lpUCM stderr: {filtered_stderr.strip()}"
        # 如果过滤后没有其他错误信息，且exit_code不为0，检查是否有成功标记
        if success_in_stderr:
            return True, stdout_data.strip() or stderr_data.strip()
        return False, f"lpUCM执行失败，退出码: {exit_code}"

    if exit_code == 0 or success_in_stderr:
        return True, stdout_data.strip() or stderr_data.strip()

    # 非0退出且无成功标记，则视为失败，返回stderr
    return False, f"lpUCM stderr: {stderr_data.strip()}"


def md5_bytes(data: bytes, server_name: str = None) -> str:
    """计算字节数据的MD5值
    对于8650系列使用采样MD5（部分计算）以加快速度
    对于8797系列使用完整MD5计算
    
    Args:
        data: 字节数据
        server_name: 服务器名称，用于判断使用哪种MD5计算方式
    """
    import hashlib
    
    # 如果指定了服务器名称且是8650系列，使用采样MD5
    if server_name and server_name.startswith("LP-8650"):
        return md5_bytes_sampled(data)
    else:
        # 8797系列或其他情况使用完整MD5
        return hashlib.md5(data).hexdigest()


def md5_bytes_sampled(data: bytes) -> str:
    """使用采样方式计算MD5（部分计算），用于8650系列加快速度
    采样策略：读取文件的开头、中间、结尾部分，以及文件大小，计算MD5
    这样可以快速验证文件是否一致，同时保持一定的准确性
    """
    import hashlib
    
    data_len = len(data)
    
    # 如果文件很小（<10MB），直接计算完整MD5
    if data_len < 10 * 1024 * 1024:
        return hashlib.md5(data).hexdigest()
    
    # 采样大小：每个采样点读取1MB
    sample_size = 1024 * 1024  # 1MB
    
    md5_hash = hashlib.md5()
    
    # 1. 读取开头1MB
    md5_hash.update(data[:sample_size])
    
    # 2. 读取中间1MB（文件中间位置）
    if data_len > sample_size * 2:
        mid_start = (data_len - sample_size) // 2
        md5_hash.update(data[mid_start:mid_start + sample_size])
    
    # 3. 读取结尾1MB
    if data_len > sample_size:
        md5_hash.update(data[-sample_size:])
    
    # 4. 将文件大小也加入MD5计算，增加唯一性
    md5_hash.update(str(data_len).encode())
    
    return md5_hash.hexdigest()


def md5_stream(stream, server_name: str = None, file_size: int = None) -> str:
    """从文件流计算MD5值（流式版本）
    对于8650系列使用采样MD5（部分计算）以加快速度
    对于8797系列使用完整MD5计算
    
    Args:
        stream: 文件流对象（支持read，可选支持seek）
        server_name: 服务器名称，用于判断使用哪种MD5计算方式
        file_size: 文件大小（字节），如果为None则从流中获取
    """
    import hashlib
    import io
    
    # 如果流不支持seek，使用流式MD5计算（完整读取）
    if not hasattr(stream, 'seek') or not hasattr(stream, 'tell'):
        # 流式MD5计算（不支持seek的流，只能完整读取）
        return md5_stream_noseek(stream, server_name, file_size)
    
    # 保存当前位置
    try:
        original_pos = stream.tell()
    except:
        original_pos = 0
    
    try:
        # 获取文件大小
        if file_size is None:
            try:
                stream.seek(0, io.SEEK_END)
                file_size = stream.tell()
                stream.seek(0)
            except:
                # 如果无法seek，需要完整读取
                return md5_stream_noseek(stream, server_name, file_size)
        
        # 如果指定了服务器名称且是8650系列，使用采样MD5
        if server_name and server_name.startswith("LP-8650"):
            return md5_stream_sampled(stream, file_size)
        else:
            # 8797系列或其他情况使用完整MD5
            stream.seek(0)
            md5_hash = hashlib.md5()
            chunk_size = 8 * 1024 * 1024  # 8MB chunks for efficiency
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                md5_hash.update(chunk)
            return md5_hash.hexdigest()
    finally:
        # 恢复原始位置
        try:
            stream.seek(original_pos)
        except:
            pass


def md5_stream_noseek(stream, server_name: str = None, file_size: int = None) -> str:
    """从不可seek的流计算MD5值（流式版本）
    对于8650系列使用采样MD5（部分计算）以加快速度
    对于8797系列使用完整MD5计算
    
    Args:
        stream: 文件流对象（只支持read，不支持seek）
        server_name: 服务器名称，用于判断使用哪种MD5计算方式
        file_size: 文件大小（字节），如果为None则从流中读取直到结束
    """
    import hashlib
    
    # 如果指定了服务器名称且是8650系列，使用采样MD5
    if server_name and server_name.startswith("LP-8650"):
        return md5_stream_noseek_sampled(stream, file_size)
    else:
        # 8797系列或其他情况使用完整MD5
        md5_hash = hashlib.md5()
        chunk_size = 8 * 1024 * 1024  # 8MB chunks for efficiency
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            md5_hash.update(chunk)
        return md5_hash.hexdigest()


def md5_stream_noseek_sampled(stream, file_size: int = None) -> str:
    """从不可seek的流使用采样方式计算MD5（部分计算），用于8650系列
    由于流不支持seek，需要完整读取并缓存采样部分
    
    Args:
        stream: 文件流对象（只支持read，不支持seek）
        file_size: 文件大小（字节），如果为None则从流中读取直到结束
    """
    import hashlib
    
    # 采样大小：每个采样点读取1MB
    sample_size = 1024 * 1024  # 1MB
    
    # 如果文件大小未知，需要先读取整个流
    if file_size is None:
        # 读取整个流到内存（对于不支持seek的流，这是必要的）
        data = stream.read()
        file_size = len(data)
        return md5_bytes_sampled(data)
    
    # 如果文件很小（<10MB），直接计算完整MD5
    if file_size < 10 * 1024 * 1024:
        md5_hash = hashlib.md5()
        chunk_size = 8 * 1024 * 1024  # 8MB chunks
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    # 对于大文件，需要缓存采样部分
    md5_hash = hashlib.md5()
    samples = {}  # 存储采样位置的数据
    current_pos = 0
    
    # 需要读取的位置
    read_positions = [
        0,  # 开头
        (file_size - sample_size) // 2,  # 中间
        file_size - sample_size  # 结尾
    ]
    
    # 读取整个流，提取采样部分
    chunk_size = 8 * 1024 * 1024  # 8MB chunks
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        
        chunk_start = current_pos
        chunk_end = current_pos + len(chunk)
        
        # 检查这个chunk是否包含需要采样的位置
        for pos in read_positions:
            if pos >= chunk_start and pos < chunk_end:
                # 提取采样部分
                offset_in_chunk = pos - chunk_start
                sample_data = chunk[offset_in_chunk:offset_in_chunk + sample_size]
                if len(sample_data) > 0:
                    samples[pos] = sample_data
        
        current_pos = chunk_end
    
    # 按顺序更新MD5
    for pos in sorted(samples.keys()):
        md5_hash.update(samples[pos])
    
    # 将文件大小也加入MD5计算
    md5_hash.update(str(file_size).encode())
    
    return md5_hash.hexdigest()


def md5_stream_sampled(stream, file_size: int) -> str:
    """使用采样方式从流计算MD5（部分计算），用于8650系列加快速度
    采样策略：读取文件的开头、中间、结尾部分，以及文件大小，计算MD5
    
    Args:
        stream: 文件流对象（支持read和seek）
        file_size: 文件大小（字节）
    """
    import hashlib
    import io
    
    # 如果文件很小（<10MB），直接计算完整MD5
    if file_size < 10 * 1024 * 1024:
        stream.seek(0)
        md5_hash = hashlib.md5()
        chunk_size = 8 * 1024 * 1024  # 8MB chunks
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    # 采样大小：每个采样点读取1MB
    sample_size = 1024 * 1024  # 1MB
    
    md5_hash = hashlib.md5()
    
    # 1. 读取开头1MB
    stream.seek(0)
    chunk = stream.read(sample_size)
    if chunk:
        md5_hash.update(chunk)
    
    # 2. 读取中间1MB（文件中间位置）
    if file_size > sample_size * 2:
        mid_start = (file_size - sample_size) // 2
        stream.seek(mid_start)
        chunk = stream.read(sample_size)
        if chunk:
            md5_hash.update(chunk)
    
    # 3. 读取结尾1MB
    if file_size > sample_size:
        stream.seek(file_size - sample_size)
        chunk = stream.read(sample_size)
        if chunk:
            md5_hash.update(chunk)
    
    # 4. 将文件大小也加入MD5计算，增加唯一性
    md5_hash.update(str(file_size).encode())
    
    return md5_hash.hexdigest()


def create_tee_stream(stream, target_stream, server_name: str = None, file_size: int = None):
    """创建一个tee式包装流，在读取数据时同时写入目标流并计算MD5
    用于8650系列：保存到临时文件的同时计算MD5（只需读取一次）
    
    Args:
        stream: 原始文件流（Flask的file.stream）
        target_stream: 目标流（临时文件）
        server_name: 服务器名称，用于判断使用哪种MD5计算方式
        file_size: 文件大小（字节）
    
    Returns:
        (包装流对象, MD5计算器对象)
    """
    import hashlib
    
    class TeeMD5Stream:
        """tee式包装流，在读取数据时同时写入目标流并计算MD5"""
        def __init__(self, original_stream, target_stream, server_name, file_size, temp_file_path=None):
            self.original_stream = original_stream
            self.target_stream = target_stream
            self.server_name = server_name
            self.file_size = file_size  # 期望大小（用于初始化）
            self.bytes_read = 0
            self.temp_file_path = temp_file_path  # 临时文件路径，用于重新读取采样数据
            
            # 如果是8650系列且需要采样MD5，需要缓存采样部分
            self.is_8650_sampled = server_name and server_name.startswith("LP-8650") and file_size and file_size >= 10 * 1024 * 1024
            if self.is_8650_sampled:
                self.sample_size = 1024 * 1024  # 1MB
                self.samples = {}  # 存储采样位置的数据，key为位置，value为采样数据
                # 采样位置会在读取完成后基于实际大小重新计算
                self.read_positions = [
                    0,  # 开头
                    (file_size - self.sample_size) // 2,  # 中间（基于期望大小，用于提取）
                    file_size - self.sample_size  # 结尾（基于期望大小，用于提取）
                ]
            else:
                # 8797完整MD5或其他情况（虽然这个函数主要用于8650）
                self.md5_hash = hashlib.md5()
                self.samples = None
                self.read_positions = None
        
        def read(self, size=-1):
            """读取数据，同时写入目标流并计算MD5"""
            chunk = self.original_stream.read(size)
            if not chunk:
                return chunk
            
            # 写入目标流（临时文件）
            self.target_stream.write(chunk)
            
            chunk_start = self.bytes_read
            chunk_end = self.bytes_read + len(chunk)
            
            # 如果是8650采样MD5，需要提取采样部分
            if self.is_8650_sampled:
                # 基于期望大小计算采样位置，提取采样数据
                # 注意：如果实际文件大小小于期望大小，某些采样位置可能超出实际文件
                # 这会在get_md5()中基于实际大小重新计算采样位置时处理
                for pos in self.read_positions:
                    if pos >= chunk_start and pos < chunk_end:
                        offset_in_chunk = pos - chunk_start
                        sample_data = chunk[offset_in_chunk:offset_in_chunk + self.sample_size]
                        if len(sample_data) > 0:
                            self.samples[pos] = sample_data
            else:
                # 完整MD5，直接更新
                self.md5_hash.update(chunk)
            
            self.bytes_read += len(chunk)
            return chunk
        
        def get_md5(self):
            """获取计算完成的MD5值"""
            if self.is_8650_sampled:
                # 8650采样MD5：按顺序更新MD5
                # 重新计算采样位置，基于实际读取的字节数（确保与远端MD5计算一致）
                actual_size = self.bytes_read
                actual_read_positions = [
                    0,  # 开头
                    (actual_size - self.sample_size) // 2 if actual_size > self.sample_size * 2 else 0,  # 中间
                    actual_size - self.sample_size if actual_size > self.sample_size else 0  # 结尾
                ]
                
                md5_hash = hashlib.md5()
                # 按实际采样位置顺序更新MD5
                # 如果实际位置在samples中，直接使用；否则从临时文件重新读取
                import os
                for pos in actual_read_positions:
                    if pos in self.samples:
                        # 使用已提取的采样数据
                        md5_hash.update(self.samples[pos])
                    elif self.temp_file_path and os.path.exists(self.temp_file_path):
                        # 采样位置不在已提取的数据中，从临时文件重新读取
                        try:
                            with open(self.temp_file_path, 'rb') as f:
                                f.seek(pos)
                                sample_data = f.read(self.sample_size)
                                if len(sample_data) > 0:
                                    md5_hash.update(sample_data)
                        except Exception as e:
                            # 如果读取失败，跳过该采样点
                            pass
                
                # 将实际读取的字节数也加入MD5计算（使用bytes_read而不是file_size，确保与实际文件大小一致）
                md5_hash.update(str(actual_size).encode())
                result = md5_hash.hexdigest()
                return result
            else:
                # 完整MD5
                result = self.md5_hash.hexdigest()
                return result
        
        def close(self):
            """关闭原始流和目标流"""
            if hasattr(self.original_stream, 'close'):
                self.original_stream.close()
            if hasattr(self.target_stream, 'close'):
                self.target_stream.close()
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            self.close()
    
    # 获取临时文件路径（如果target_stream是文件对象）
    temp_file_path = None
    if hasattr(target_stream, 'name'):
        temp_file_path = target_stream.name
    
    # 创建包装流
    wrapped_stream = TeeMD5Stream(stream, target_stream, server_name, file_size, temp_file_path)
    
    return wrapped_stream, wrapped_stream


def create_md5_calculating_stream(stream, server_name: str = None, file_size: int = None):
    """创建一个包装流，在上传过程中同时计算MD5
    用于8797系列：边上传边计算MD5，最后与远端md5sum结果对比
    
    Args:
        stream: 原始文件流
        server_name: 服务器名称，用于判断使用哪种MD5计算方式
        file_size: 文件大小（字节）
    
    Returns:
        包装流对象，支持read()和get_md5()方法
    """
    import hashlib
    
    class MD5CalculatingStream:
        """包装流，在读取数据时同时计算MD5"""
        def __init__(self, original_stream, server_name, file_size):
            self.original_stream = original_stream
            self.server_name = server_name
            self.file_size = file_size
            self.bytes_read = 0
            
            # 8797系列使用完整MD5计算
            self.md5_hash = hashlib.md5()
        
        def read(self, size=-1):
            """读取数据并同时计算MD5"""
            chunk = self.original_stream.read(size)
            if not chunk:
                return chunk
            
            # 直接更新MD5（8797使用完整MD5）
            self.md5_hash.update(chunk)
            self.bytes_read += len(chunk)
            return chunk
        
        def get_md5(self):
            """获取计算完成的MD5值"""
            result = self.md5_hash.hexdigest()
            return result
        
        def close(self):
            """关闭原始流"""
            if hasattr(self.original_stream, 'close'):
                self.original_stream.close()
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            self.close()
    
    # 创建包装流
    wrapped_stream = MD5CalculatingStream(stream, server_name, file_size)
    
    return wrapped_stream


def parse_md5_output(out: str) -> str:
    """从 md5sum 输出解析 md5"""
    if not out:
        return ""
    return out.strip().split()[0]


def _read_all(session: paramiko.Channel) -> tuple[str, str]:
    """阻塞读取直到远端命令结束，返回(stdout, stderr)"""
    stdout_chunks = []
    stderr_chunks = []
    while not session.exit_status_ready():
        if session.recv_ready():
            stdout_chunks.append(session.recv(4096))
        if session.recv_stderr_ready():
            stderr_chunks.append(session.recv_stderr(4096))
        time.sleep(0.05)
    # 结束后再读残余
    while session.recv_ready():
        stdout_chunks.append(session.recv(4096))
    while session.recv_stderr_ready():
        stderr_chunks.append(session.recv_stderr(4096))
    stdout_raw = b"".join(stdout_chunks).decode(errors="ignore")
    stderr_raw = b"".join(stderr_chunks).decode(errors="ignore")
    # 过滤stdout中的可忽略警告
    stdout_filtered = filter_benign_stdout(stdout_raw).strip()
    return stdout_filtered, stderr_raw.strip()


def remote_exec_collect(server_name: str, ip: str, port: int, command: str, workdir: str = "/"):
    """
    执行远程命令并收集完整输出，返回 (exit_code, stdout, stderr)。
    适合调试/验证远端输出。
    """
    transport = create_transport(server_name, ip, port)
    session = transport.open_session(timeout=ssh_timeout)
    session.get_pty()
    session.exec_command(f"cd {shlex.quote(workdir)} && {command}")
    stdout, stderr = _read_all(session)
    exit_code = session.recv_exit_status()
    session.close()
    transport.close()
    return exit_code, stdout, stderr


def remote_shell_exec(server_name: str, ip: str, port: int, command: str, workdir: str = "/", timeout: int = 300, idle_timeout: int = 30):
    """
    使用 invoke_shell 交互方式执行命令，适合长日志。
    返回 (exit_code| -1 超时, stdout, stderr, reason)
    """
    transport = create_transport(server_name, ip, port)
    chan = transport.open_session(timeout=ssh_timeout)
    chan.get_pty()
    chan.invoke_shell()

    send_cmd = f"cd {shlex.quote(workdir)} && {command}\nexit\n"
    chan.send(send_cmd)

    stdout_chunks = []
    stderr_chunks = []
    start = time.time()
    last_data = time.time()

    while True:
        if chan.recv_ready():
            stdout_chunks.append(chan.recv(4096))
            last_data = time.time()
        if chan.recv_stderr_ready():
            stderr_chunks.append(chan.recv_stderr(4096))
            last_data = time.time()

        if chan.exit_status_ready():
            break

        now = time.time()
        if now - start > timeout:
            chan.close()
            transport.close()
            return -1, b"".join(stdout_chunks).decode(errors="ignore"), b"".join(stderr_chunks).decode(errors="ignore"), "timeout"
        if now - last_data > idle_timeout:
            chan.close()
            transport.close()
            return -1, b"".join(stdout_chunks).decode(errors="ignore"), b"".join(stderr_chunks).decode(errors="ignore"), "idle_timeout"
        time.sleep(0.05)

    # 结束后再读残余
    while chan.recv_ready():
        stdout_chunks.append(chan.recv(4096))
    while chan.recv_stderr_ready():
        stderr_chunks.append(chan.recv_stderr(4096))

    exit_code = chan.recv_exit_status()
    chan.close()
    transport.close()

    return exit_code, b"".join(stdout_chunks).decode(errors="ignore"), b"".join(stderr_chunks).decode(errors="ignore"), ""


def run_remote_command(server_name: str, ip: str, port: int, command: str):
    """根据认证模式执行远程命令，返回 (success, output_or_error)；使用 pty 并等待结束读取全部输出"""
    auth_mode = resolve_auth_mode(server_name)

    # 若是简单的 cat 路径，优先尝试 SFTP 读取，避免 shell 环境问题
    cat_prefix = "cat "
    if command.startswith(cat_prefix) and len(command.split()) == 2:
        file_path = command[len(cat_prefix):].strip()
    else:
        file_path = None

    if auth_mode == "none":
        try:
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(ssh_username)

            if file_path:
                ok, data = fetch_version_via_sftp(transport, file_path)
                if ok:
                    transport.close()
                    return True, data
                # SFTP 失败则继续走 shell

            session = transport.open_session(timeout=ssh_timeout)
            session.get_pty()  # 申请伪终端保证输出完整
            session.exec_command(f"cd / && {command}")
            output, err_out = _read_all(session)
            session.close()
            transport.close()

            if err_out and not is_benign_stderr(err_out):
                return False, err_out
            return True, output or err_out
        except Exception as e:
            return False, str(e)

    # key 模式
    key_path = resolve_key(server_name, port)
    if not key_path:
        return False, f"端口{port}未配置私钥"

    transport = None
    try:
        private_key = paramiko.RSAKey.from_private_key_file(key_path)

        # Transport 方式，便于先尝试 SFTP 再执行命令
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
        # 检查连接是否仍然有效
        if not transport.is_authenticated() and not transport.is_alive():
            transport.close()
            raise paramiko.ssh_exception.SSHException("Connection closed by remote host after start_client")
        try:
            transport.auth_publickey(username=ssh_username, key=private_key)
        except paramiko.ssh_exception.SSHException as auth_err:
            # 如果认证失败且连接已关闭，提供更明确的错误信息
            if "No existing session" in str(auth_err) or not transport.is_alive():
                transport.close()
                raise paramiko.ssh_exception.SSHException(f"Connection closed by remote host during authentication: {auth_err}")
            raise

        if file_path:
            ok, data = fetch_version_via_sftp(transport, file_path)
            if ok:
                transport.close()
                return True, data
            # SFTP 失败则继续走 shell

        session = transport.open_session(timeout=ssh_timeout)
        session.get_pty()  # 申请伪终端保证输出完整
        session.exec_command(f"cd / && {command}")
        output, err_out = _read_all(session)
        session.close()
        transport.close()
        if err_out and not is_benign_stderr(err_out):
            return False, err_out
        return True, output or err_out
    except Exception as e:
        # 确保在异常时关闭transport
        if transport:
            try:
                transport.close()
            except:
                pass
        return False, str(e)


def check_ssh_login_none(ip, port):
    """尝试无密码/无私钥的 SSH 连接"""
    try:
        sock = socket.create_connection((ip, port), timeout=ssh_timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=ssh_timeout)
        transport.auth_none(ssh_username)

        session = transport.open_session(timeout=ssh_timeout)
        session.exec_command('echo "SSH test"')
        output = session.recv(1024).decode().strip()
        session.close()
        transport.close()
        return True, f"端口{port} SSH登录成功（免认证）"
    except paramiko.AuthenticationException:
        return False, f"端口{port} SSH免认证失败"
    except Exception as e:
        return False, f"端口{port} SSH免认证错误: {str(e)}"

def check_port(ip, port):
    """检查指定IP的端口是否开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def check_ssh_login(server_name, ip, port):
    """使用指定端口对应的密钥尝试SSH登录"""
    try:
        auth_mode = resolve_auth_mode(server_name)
        if auth_mode == "none":
            return check_ssh_login_none(ip, port)

        # 获取该端口对应的私钥路径
        key_path = resolve_key(server_name, port)
        if not key_path:
            return False, f"端口{port}未配置私钥"
        
        # 加载对应端口的私钥
        private_key = paramiko.RSAKey.from_private_key_file(key_path)
        
        # 使用指定端口连接，对临时性错误进行重试（优化：减少重试次数和间隔）
        max_retries = 2  # 优化：从3次减少到2次，提高响应速度
        last_error = None
        for attempt in range(max_retries):
            client = None
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                client.connect(
                    hostname=ip,
                    port=port,  # 关键：指定端口号
                    username=ssh_username,
                    pkey=private_key,
                    timeout=ssh_timeout,
                    banner_timeout=30
                )
                
                stdin, stdout, stderr = client.exec_command('echo "SSH test"', timeout=5)
                output = stdout.read().decode().strip()
                
                client.close()
                return True, f"端口{port} SSH登录成功"
            except (paramiko.SSHException, OSError, socket.error) as e:
                last_error = e
                error_msg = str(e)
                # 确保关闭失败的连接
                if client:
                    try:
                        client.close()
                    except:
                        pass
                
                # 快速失败：对于明显的认证失败或连接拒绝，不重试
                if isinstance(e, paramiko.AuthenticationException):
                    return False, f"端口{port} SSH认证失败（密钥不匹配）"
                if "Connection refused" in error_msg or "Connection reset" in error_msg:
                    return False, f"端口{port} SSH连接被拒绝: {error_msg}"
                
                # 如果是临时性连接错误，进行重试
                if ("No existing session" in error_msg or 
                    "Connection closed" in error_msg or 
                    "Error reading SSH protocol banner" in error_msg or
                    "WinError 10038" in error_msg or
                    isinstance(e, OSError) or
                    isinstance(e, socket.error)):
                    if attempt < max_retries - 1:
                        # 优化：缩短重试间隔，从1-1.5秒减少到0.5秒
                        import time
                        time.sleep(0.5)
                        continue
                    # 重试失败，返回友好的错误信息
                    retry_error_msg = f"端口{port} SSH连接临时性错误（已重试{max_retries}次）: {error_msg}"
                    return False, retry_error_msg
                # 其他SSH错误直接返回
                return False, f"端口{port} SSH连接错误: {error_msg}"
            except Exception as e:
                # 确保关闭失败的连接
                if client:
                    try:
                        client.close()
                    except:
                        pass
                # 非SSH异常，直接返回（不重试）
                return False, f"端口{port} 其他错误: {str(e)}"
        
        # 所有重试都失败
        if last_error:
            return False, f"端口{port} SSH连接错误（已重试{max_retries}次）: {str(last_error)}"
        return False, f"端口{port} SSH连接失败"
    except paramiko.AuthenticationException:
        return False, f"端口{port} SSH认证失败（密钥不匹配）"
    except paramiko.SSHException as e:
        error_msg = str(e)
        if "No existing session" in error_msg or "Connection closed" in error_msg:
            return False, f"端口{port} SSH连接临时性错误: {error_msg}"
        return False, f"端口{port} SSH连接错误: {str(e)}"
    except Exception as e:
        return False, f"端口{port} 其他错误: {str(e)}"

def check_single_server(server_name, server_ip):
    """检查单个服务器的完整状态"""
    results = {
        'server_name': server_name,
        'server_ip': server_ip,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'port_22': 'offline',
        'port_22_ssh': 'N/A',
        'port_22_detail': '',
        'port_9999': 'offline',
        'port_9999_ssh': 'N/A',
        'port_9999_detail': '',
        'version': '',
        'version_detail': '',
        'version_9999': '',
        'version_9999_detail': ''
    }
    
    try:
        # 检查22端口连通性和SSH
        if check_port(server_ip, 22):
            results['port_22'] = 'online'
            try:
                ssh_success, ssh_detail = check_ssh_login(server_name, server_ip, 22)
                results['port_22_ssh'] = 'online' if ssh_success else 'offline'
                results['port_22_detail'] = ssh_detail
                if ssh_success and version_command:
                    ok, ver = run_remote_command(server_name, server_ip, 22, version_command)
                    if ok:
                        results['version'] = ver
                    else:
                        results['version_detail'] = ver
            except Exception as e:
                # 静默处理SSH连接错误，避免输出到控制台
                results['port_22_ssh'] = 'offline'
                results['port_22_detail'] = f"SSH检查异常: {str(e)}"
        
        # 检查9999端口连通性和SSH
        if check_port(server_ip, 9999):
            results['port_9999'] = 'online'
            try:
                ssh_success, ssh_detail = check_ssh_login(server_name, server_ip, 9999)
                results['port_9999_ssh'] = 'online' if ssh_success else 'offline'
                results['port_9999_detail'] = ssh_detail
                if ssh_success and version_command:
                    ok, ver = run_remote_command(server_name, server_ip, 9999, version_command)
                    if ok:
                        results['version_9999'] = ver
                    else:
                        results['version_9999_detail'] = ver
            except Exception as e:
                # 静默处理SSH连接错误，避免输出到控制台
                results['port_9999_ssh'] = 'offline'
                results['port_9999_detail'] = f"SSH检查异常: {str(e)}"
    except Exception as e:
        # 捕获所有未预期的异常，避免程序崩溃
        results['port_22_detail'] = f"检查异常: {str(e)}"
        results['port_9999_detail'] = f"检查异常: {str(e)}"
    
    return results

def format_report(results_list):
    """格式化监控报告"""
    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append(f"服务器监控报告 - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 70)
    
    for result in results_list:
        report_lines.append(f"\n{result['server_name']} ({result['server_ip']})")
        
        # 22端口（智驾域）状态
        report_lines.append(f"  智驾域(22): {result['port_22']:7} | SSH: {result['port_22_ssh']:7}")
        if result['port_22_detail'] and result['port_22_ssh'] == 'offline':
            report_lines.append(f"      详情: {result['port_22_detail']}")
        
        # 9999端口（座舱域）状态
        report_lines.append(f"  座舱域(9999): {result['port_9999']:6} | SSH: {result['port_9999_ssh']:7}")
        if result['port_9999_detail'] and result['port_9999_ssh'] == 'offline':
            report_lines.append(f"      详情: {result['port_9999_detail']}")
        
        # 22 版本信息（智驾域）
        if result.get('version'):
            report_lines.append(f"  智驾域版本: {result['version']}")
        elif result.get('version_detail'):
            report_lines.append(f"  智驾域版本获取失败: {result['version_detail']}")
        
        # 9999 版本信息（座舱域）
        if result.get('version_9999'):
            report_lines.append(f"  座舱域版本: {result['version_9999']}")
        elif result.get('version_9999_detail'):
            report_lines.append(f"  座舱域版本获取失败: {result['version_9999_detail']}")
    
    report_lines.append("\n" + "=" * 70)
    return "\n".join(report_lines)


def run_checks_once():
    """执行一次全量检查并返回结果列表，带总超时保护"""
    all_results = []
    
    # 动态计算并发数：20-30之间，但不超过服务器数量
    server_count = len(server_dict)
    max_workers = min(max(20, server_count // 5), 30, server_count)
    
    # 总超时保护：根据服务器数量动态调整，最少3分钟，最多5分钟
    # 估算：每台服务器最多60秒，考虑并发，总超时 = (服务器数 / 并发数) * 60秒 + 缓冲
    estimated_time = (server_count / max_workers) * 60 + 60  # 额外60秒缓冲
    total_timeout = min(max(180, int(estimated_time)), 300)  # 3-5分钟之间
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_server = {
            executor.submit(check_single_server, name, ip): (name, ip)
            for name, ip in server_dict.items()
        }

        # 使用超时机制处理所有future
        completed_count = 0
        for future in as_completed(future_to_server):
            # 检查总超时
            elapsed = time.time() - start_time
            if elapsed > total_timeout:
                # 超时：取消未完成的任务并记录超时错误
                for remaining_future in future_to_server:
                    if not remaining_future.done():
                        remaining_future.cancel()
                        server_name, server_ip = future_to_server[remaining_future]
                        all_results.append({
                            'server_name': server_name,
                            'server_ip': server_ip,
                            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                            'port_22': 'offline',
                            'port_22_ssh': '超时',
                            'port_22_detail': f"检查超时（总超时{total_timeout}秒）",
                            'port_9999': 'offline',
                            'port_9999_ssh': '超时',
                            'port_9999_detail': f"检查超时（总超时{total_timeout}秒）"
                        })
                break
            
            try:
                # 使用较短的超时避免单个future阻塞太久
                remaining_timeout_for_future = max(0.1, total_timeout - elapsed)
                result = future.result(timeout=min(remaining_timeout_for_future, 10.0))
                all_results.append(result)
                completed_count += 1
            except TimeoutError:
                server_name, server_ip = future_to_server[future]
                all_results.append({
                    'server_name': server_name,
                    'server_ip': server_ip,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'port_22': 'offline',
                    'port_22_ssh': '超时',
                    'port_22_detail': f"检查超时",
                    'port_9999': 'offline',
                    'port_9999_ssh': '超时',
                    'port_9999_detail': f"检查超时"
                })
            except Exception as e:
                server_name, server_ip = future_to_server[future]
                all_results.append({
                    'server_name': server_name,
                    'server_ip': server_ip,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'port_22': 'offline',
                    'port_22_ssh': '异常',
                    'port_22_detail': f"检查异常: {str(e)}",
                    'port_9999': 'offline',
                    'port_9999_ssh': '异常',
                    'port_9999_detail': f"检查异常: {str(e)}"
                })

    # 按服务器名称自然排序（避免按 IP 或字典序混乱）
    all_results.sort(key=lambda x: natural_key(x['server_name']))
    return all_results


def main():
    """主监控循环"""
    try:
        config_path = load_config()
        print(f"配置文件已加载: {config_path}")
    except FileNotFoundError:
        print(f"未找到配置文件: {CONFIG_FILE}")
        return
    except json.JSONDecodeError as e:
        print(f"配置文件格式错误: {e}")
        return

    print(f"开始监控 {len(server_dict)} 台服务器...")
    print(f"默认认证模式: {auth_mode_default}")
    print("=" * 50)
    
    while True:
        try:
            all_results = run_checks_once()
            
            # 输出报告
            report = format_report(all_results)
            print(report)
            
            # 保存日志
            with open('server_monitor.log', 'a') as f:
                f.write(report + "\n\n")
            
            if check_interval > 0:
                time.sleep(check_interval)
            else:
                break
                
        except KeyboardInterrupt:
            print("\n监控已停止")
            break
        except Exception as e:
            print(f"监控异常: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()