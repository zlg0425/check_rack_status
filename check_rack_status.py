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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
# ========================================================


def load_config(config_path: str = CONFIG_FILE) -> str:
    """从JSON配置文件加载监控参数"""
    global server_dict, key_mapping, group_key_mapping, group_auth_mode
    global ssh_username, ssh_timeout, check_interval, auth_mode_default, version_command, upload_target_dir, fota_target_dir

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


def fetch_version_via_sftp(transport: paramiko.Transport, path: str):
    """尝试通过 SFTP 直接读取版本文件"""
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        with sftp.file(path, "r") as f:
            return True, f.read().decode(errors="ignore").strip()
    except Exception as e:
        return False, str(e)


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


def sftp_upload(server_name: str, ip: str, port: int, target_dir: str, filename: str, data: bytes, progress_callback=None):
    """将文件上传到指定服务器和端口，支持进度回调"""
    auth_mode = resolve_auth_mode(server_name)
    safe_dir = target_dir.rstrip("/") or "/"
    remote_path = posixpath.join(safe_dir, filename)
    total_size = len(data)
    # 根据文件大小动态调整chunk size以提高上传速度
    # 使用更大的chunk size以减少网络往返次数和系统调用开销
    if total_size > 100 * 1024 * 1024:  # >100MB
        chunk_size = 4 * 1024 * 1024  # 4MB，进一步增大以提高速度
    elif total_size > 10 * 1024 * 1024:  # >10MB
        chunk_size = 2 * 1024 * 1024  # 2MB
    else:
        chunk_size = 1024 * 1024  # 1MB

    def write_with_progress(f, data_bytes):
        written = 0
        start_time = time.time()
        last_log_time = start_time
        last_log_bytes = 0
        
        while written < total_size:
            chunk_start_time = time.time()
            chunk = data_bytes[written:written + chunk_size]
            f.write(chunk)
            chunk_end_time = time.time()
            written += len(chunk)
            
            # 每5秒记录一次上传速度和进度
            current_time = time.time()
            if current_time - last_log_time >= 5.0:
                elapsed = current_time - start_time
                speed = written / elapsed if elapsed > 0 else 0
                recent_speed = (written - last_log_bytes) / (current_time - last_log_time) if (current_time - last_log_time) > 0 else 0
                # #region agent log
                try:
                    with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                        import json
                        log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H9","location":"check_rack_status.py:165","message":"sftp_upload progress","data":{"server_name":server_name,"written":written,"total_size":total_size,"progress_pct":int((written/total_size)*100) if total_size > 0 else 0,"avg_speed_mbps":speed/(1024*1024),"recent_speed_mbps":recent_speed/(1024*1024),"chunk_size":chunk_size,"chunk_write_time_ms":(chunk_end_time-chunk_start_time)*1000},"timestamp":int(time.time()*1000)}) + '\n')
                except: pass
                # #endregion
                last_log_time = current_time
                last_log_bytes = written
            
            if progress_callback:
                progress_callback(written, total_size)

    if auth_mode == "none":
        try:
            sock = socket.create_connection((ip, port), timeout=ssh_timeout)
            transport = paramiko.Transport(sock)
            transport.start_client(timeout=ssh_timeout)
            transport.auth_none(ssh_username)

            sftp = paramiko.SFTPClient.from_transport(transport)
            ensure_remote_dir(sftp, safe_dir)
            
            # 使用putfo方法上传，可能比file.write()更高效
            import io
            file_obj = io.BytesIO(data)
            
            def putfo_progress_callback(transferred, total):
                if progress_callback:
                    progress_callback(transferred, total)
            
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H9","location":"check_rack_status.py:198","message":"using putfo method for upload (none auth)","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path,"total_size":total_size},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
            sftp.close()
            transport.close()
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
        
        # 使用putfo方法上传，可能比file.write()更高效
        import io
        file_obj = io.BytesIO(data)
        
        def putfo_progress_callback(transferred, total):
            if progress_callback:
                progress_callback(transferred, total)
        
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H9","location":"check_rack_status.py:222","message":"using putfo method for upload (key auth)","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path,"total_size":total_size},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        
        sftp.putfo(file_obj, remote_path, file_size=total_size, callback=putfo_progress_callback)
        sftp.close()
        transport.close()
        return True, remote_path
    except Exception as e:
        return False, str(e)


def remote_md5(server_name: str, ip: str, port: int, remote_path: str):
    """计算远端文件 MD5
    根据服务器类型选择不同方法，但都计算完整文件的MD5，与本地md5_bytes()方法保持一致：
    - LP-8650系列：通过SFTP读取完整文件内容在本地计算MD5（QNX系统没有md5sum）
    - LP-8797系列：使用md5sum命令获取完整文件的MD5（md5sum默认计算完整文件）
    """
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H1","location":"check_rack_status.py:197","message":"remote_md5 entry","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path,"ssh_username":ssh_username},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion
    
    # 根据服务器类型选择方法
    use_sftp = server_name.startswith("LP-8650")
    
    if use_sftp:
        # LP-8650系列：通过SFTP读取文件内容，在本地计算MD5
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:208","message":"8650 series: using SFTP to calculate MD5","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        try:
            import hashlib
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:225","message":"SFTP creating transport and client","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            transport = create_transport(server_name, ip, port)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:232","message":"SFTP client created, calling stat","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            # 获取文件大小
            file_stat = sftp.stat(remote_path)
            file_size = file_stat.st_size
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:228","message":"SFTP file stat","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path,"file_size":file_size},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            # 读取文件内容并计算MD5
            # 统一使用完整文件MD5计算，与本地MD5计算方式保持一致
            # 不再使用采样MD5，确保与本地hashlib.md5()计算结果一致
            # 根据文件大小动态调整chunk size以提高读取速度
            if file_size > 100 * 1024 * 1024:  # >100MB
                chunk_size = 8 * 1024 * 1024  # 8MB，进一步增大以提高速度
            elif file_size > 10 * 1024 * 1024:  # >10MB
                chunk_size = 4 * 1024 * 1024  # 4MB
            else:
                chunk_size = 1024 * 1024  # 1MB
            
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                    import json
                    log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:317","message":"SFTP MD5 calculation strategy","data":{"server_name":server_name,"file_size":file_size,"chunk_size":chunk_size,"method":"full_file_md5"},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            md5_hash = hashlib.md5()
            total_read = 0
            md5_start_time = time.time()
            
            with sftp.file(remote_path, "rb") as remote_file:
                # 完整读取文件内容计算MD5，与本地md5_bytes()方法保持一致
                while True:
                    chunk = remote_file.read(chunk_size)
                    if not chunk:
                        break
                    md5_hash.update(chunk)
                    total_read += len(chunk)
                    # 每读取50MB记录一次进度（减少日志频率）
                    if total_read % (50 * 1024 * 1024) < chunk_size:
                        elapsed = time.time() - md5_start_time
                        speed = total_read / elapsed if elapsed > 0 else 0
                        # #region agent log
                        try:
                            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                                import json
                                log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H17","location":"check_rack_status.py:335","message":"SFTP reading progress","data":{"server_name":server_name,"total_read":total_read,"file_size":file_size,"progress_pct":int((total_read/file_size)*100) if file_size > 0 else 0,"elapsed_sec":elapsed,"speed_mbps":speed/(1024*1024)},"timestamp":int(time.time()*1000)}) + '\n')
                        except: pass
                        # #endregion
            
            md5_elapsed = time.time() - md5_start_time
            md5_speed = total_read / md5_elapsed if md5_elapsed > 0 else 0
            
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                    import json
                    log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H17","location":"check_rack_status.py:352","message":"SFTP file read completed","data":{"server_name":server_name,"total_read":total_read,"file_size":file_size,"method":"full_file_md5","elapsed_sec":md5_elapsed,"speed_mbps":md5_speed/(1024*1024),"estimated_time_for_1gb_sec":(1024*1024*1024)/(md5_speed) if md5_speed > 0 else 0},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            md5_val = md5_hash.hexdigest()
            sftp.close()
            transport.close()
            
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                    import json
                    log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:358","message":"SFTP MD5 calculation success (full file)","data":{"server_name":server_name,"md5_val":md5_val,"file_size":file_size,"total_read":total_read},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            return True, md5_val
        except Exception as e:
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                    import json
                    log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:371","message":"SFTP MD5 calculation failed","data":{"server_name":server_name,"error":str(e)},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            return False, f"通过SFTP计算MD5失败: {e}"
    else:
        # LP-8797系列：使用md5sum命令获取MD5
        # md5sum命令默认对完整文件计算MD5，与本地md5_bytes()方法保持一致
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                import json
                log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:381","message":"8797 series: using md5sum command (full file)","data":{"server_name":server_name,"ip":ip,"port":port,"remote_path":remote_path,"method":"md5sum_full_file"},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        cmd = f"/ifs/usr/bin/md5sum {shlex.quote(remote_path)}"
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                import json
                log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:389","message":"before run_remote_command md5sum","data":{"server_name":server_name,"cmd":cmd},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        ok, out = run_remote_command(server_name, ip, port, cmd)
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                import json
                log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:397","message":"run_remote_command md5sum result","data":{"server_name":server_name,"ok":ok,"out":out[:200] if out else None,"out_len":len(out) if out else 0},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        
        if not ok:
            return False, out
        
        md5_val = parse_md5_output(out)
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                import json
                log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:409","message":"parse_md5_output result from command","data":{"server_name":server_name,"md5_val":md5_val,"method":"md5sum_full_file"},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        
        if not md5_val:
            return False, f"无法解析md5: {out}"
        
        # 验证MD5格式（32位十六进制字符串）
        if len(md5_val) == 32 and all(c in '0123456789abcdef' for c in md5_val.lower()):
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as log_file:
                    import json
                    log_file.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H16","location":"check_rack_status.py:422","message":"8797 MD5 calculation success (md5sum full file)","data":{"server_name":server_name,"md5_val":md5_val,"method":"md5sum_full_file"},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
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
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H12","location":"check_rack_status.py:240","message":"create_transport entry","data":{"server_name":server_name,"ip":ip,"port":port,"ssh_username":ssh_username},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion
    auth_mode = resolve_auth_mode(server_name)
    transport = paramiko.Transport((ip, port))
    transport.start_client(timeout=ssh_timeout)

    if auth_mode == "none":
        transport.auth_none(ssh_username)
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H12","location":"check_rack_status.py:247","message":"create_transport auth_none success","data":{"ip":ip,"port":port},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        return transport

    key_path = resolve_key(server_name, port)
    if not key_path:
        transport.close()
        raise RuntimeError(f"端口{port}未配置私钥")

    private_key = paramiko.RSAKey.from_private_key_file(key_path)
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H13","location":"check_rack_status.py:256","message":"create_transport before auth_publickey","data":{"ip":ip,"port":port,"ssh_username":ssh_username,"key_path":key_path},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion
    transport.auth_publickey(username=ssh_username, key=private_key)
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H13","location":"check_rack_status.py:256","message":"create_transport auth_publickey success","data":{"ip":ip,"port":port},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion
    return transport


def run_ucm_with_log(server_name: str, ip: str, port: int, ucm_file: str, log_file: str = None, tail_wait: int = 2):
    """启动日志会话B，2秒后启动A执行 lpUCM，A 结束后再等待 tail_wait 秒再停止日志。返回 (ok, info)。"""
    if log_file is None:
        import time
        log_file = f"ucm_{server_name}_{ip}_{port}_{int(time.time())}.log"
    log_stop = threading.Event()
    log_err = []

    def tail_log():
        try:
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H6","location":"check_rack_status.py:397","message":"tail_log starting logview","data":{"server_name":server_name,"ip":ip,"port":port,"log_file":log_file},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
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
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H6","location":"check_rack_status.py:530","message":"tail_log executing logview command","data":{"server_name":server_name,"ip":ip,"port":port,"logview_cmd":logview_cmd,"grep_path":grep_path},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
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
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H6","location":"check_rack_status.py:432","message":"tail_log completed","data":{"server_name":server_name,"ip":ip,"port":port,"log_file":log_file,"log_bytes":log_bytes},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
        except Exception as e:
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H6","location":"check_rack_status.py:437","message":"tail_log exception","data":{"server_name":server_name,"ip":ip,"port":port,"error":str(e)},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            log_err.append(str(e))

    t = threading.Thread(target=tail_log, daemon=True)
    t.start()

    # 等待B稳定
    time.sleep(2)

    try:
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H7","location":"check_rack_status.py:450","message":"run_ucm creating transport_a","data":{"server_name":server_name,"ip":ip,"port":port,"ucm_file":ucm_file},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        transport_a = create_transport(server_name, ip, port)
        session_a = transport_a.open_session(timeout=ssh_timeout)
        # 8650系列不需要LD_LIBRARY_PATH，8797系列需要
        if server_name.startswith("LP-8650"):
            ucm_cmd = f"/opt/usr/bin/lpUCM -i {shlex.quote(ucm_file)}"
        else:
            ucm_cmd = f"LD_LIBRARY_PATH=/opt/usr/lib:/opt/usr/lib64 /opt/usr/bin/lpUCM -i {shlex.quote(ucm_file)}"
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H7","location":"check_rack_status.py:457","message":"run_ucm executing lpUCM command","data":{"server_name":server_name,"ip":ip,"port":port,"ucm_cmd":ucm_cmd},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        session_a.exec_command(ucm_cmd)
        # 读取全部输出
        stdout_data, stderr_data = _read_all(session_a)
        exit_code = session_a.recv_exit_status()
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H7","location":"check_rack_status.py:465","message":"run_ucm command completed","data":{"server_name":server_name,"ip":ip,"port":port,"exit_code":exit_code,"stdout_len":len(stdout_data) if stdout_data else 0,"stderr_len":len(stderr_data) if stderr_data else 0,"stderr_preview":stderr_data[:200] if stderr_data else None},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        session_a.close()
        transport_a.close()
    except Exception as e:
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H7","location":"check_rack_status.py:473","message":"run_ucm exception","data":{"server_name":server_name,"ip":ip,"port":port,"ucm_file":ucm_file,"error":str(e)},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
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


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


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
    return (
        b"".join(stdout_chunks).decode(errors="ignore").strip(),
        b"".join(stderr_chunks).decode(errors="ignore").strip(),
    )


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
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:444","message":"run_remote_command entry","data":{"server_name":server_name,"ip":ip,"port":port,"command":command,"ssh_username":ssh_username},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion
    auth_mode = resolve_auth_mode(server_name)
    # #region agent log
    try:
        with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H5","location":"check_rack_status.py:446","message":"auth_mode resolved","data":{"auth_mode":auth_mode},"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
    # #endregion

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
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H7","location":"check_rack_status.py:471","message":"command executed","data":{"command":command,"output_len":len(output) if output else 0,"err_out_len":len(err_out) if err_out else 0,"err_out":err_out[:200] if err_out else None},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            session.close()
            transport.close()

            if err_out and not is_benign_stderr(err_out):
                return False, err_out
            return True, output or err_out
        except Exception as e:
            # #region agent log
            try:
                with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H8","location":"check_rack_status.py:479","message":"auth_mode none exception","data":{"error":str(e),"error_type":type(e).__name__},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            return False, str(e)

    # key 模式
    key_path = resolve_key(server_name, port)
    if not key_path:
        return False, f"端口{port}未配置私钥"

    try:
        private_key = paramiko.RSAKey.from_private_key_file(key_path)

        # Transport 方式，便于先尝试 SFTP 再执行命令
        transport = paramiko.Transport((ip, port))
        transport.start_client(timeout=ssh_timeout)
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H9","location":"check_rack_status.py:493","message":"before auth_publickey","data":{"ip":ip,"port":port,"ssh_username":ssh_username,"key_path":key_path},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        transport.auth_publickey(username=ssh_username, key=private_key)
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H9","location":"check_rack_status.py:493","message":"auth_publickey success","data":{"ip":ip,"port":port},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion

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
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H10","location":"check_rack_status.py:504","message":"command executed key mode","data":{"command":command,"output_len":len(output) if output else 0,"err_out_len":len(err_out) if err_out else 0,"err_out":err_out[:200] if err_out else None},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
        session.close()
        transport.close()
        if err_out and not is_benign_stderr(err_out):
            return False, err_out
        return True, output or err_out
    except Exception as e:
        # #region agent log
        try:
            with open(r'd:\Core\python_pj\other_script\ssh_script\check_rack_status\.cursor\debug.log', 'a', encoding='utf-8') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"H11","location":"check_rack_status.py:511","message":"key mode exception","data":{"error":str(e),"error_type":type(e).__name__},"timestamp":int(time.time()*1000)}) + '\n')
        except: pass
        # #endregion
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
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # 使用指定端口连接
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
    except paramiko.AuthenticationException:
        return False, f"端口{port} SSH认证失败（密钥不匹配）"
    except paramiko.SSHException as e:
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
    
    # 检查22端口连通性和SSH
    if check_port(server_ip, 22):
        results['port_22'] = 'online'
        ssh_success, ssh_detail = check_ssh_login(server_name, server_ip, 22)
        results['port_22_ssh'] = 'online' if ssh_success else 'offline'
        results['port_22_detail'] = ssh_detail
        if ssh_success and version_command:
            ok, ver = run_remote_command(server_name, server_ip, 22, version_command)
            if ok:
                results['version'] = ver
            else:
                results['version_detail'] = ver
    
    # 检查9999端口连通性和SSH
    if check_port(server_ip, 9999):
        results['port_9999'] = 'online'
        ssh_success, ssh_detail = check_ssh_login(server_name, server_ip, 9999)
        results['port_9999_ssh'] = 'online' if ssh_success else 'offline'
        results['port_9999_detail'] = ssh_detail
        if ssh_success and version_command:
            ok, ver = run_remote_command(server_name, server_ip, 9999, version_command)
            if ok:
                results['version_9999'] = ver
            else:
                results['version_9999_detail'] = ver
    
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
    """执行一次全量检查并返回结果列表"""
    all_results = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_server = {
            executor.submit(check_single_server, name, ip): (name, ip)
            for name, ip in server_dict.items()
        }

        for future in as_completed(future_to_server):
            try:
                result = future.result()
                all_results.append(result)
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