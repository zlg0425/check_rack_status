"""
服务器状态检查模块
提供服务器端口连通性和SSH登录监控功能
"""

import socket
import paramiko
import time
import threading
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.utils.config import (
    ssh_timeout,
    check_interval,
    version_command,
    resolve_key,
    resolve_auth_mode,
    resolve_key_path,
)
import app.utils.config as config  # 使用模块引用以获取动态更新的值
from app.utils.helpers import run_remote_command, natural_key

# 状态缓存（用于 web_ui.py）
status_cache = {
    "data": [],
    "timestamp": 0,
    "error": "",
}
cache_lock = threading.Lock()

# 后台任务控制标志
_refresh_loop_stop_event = threading.Event()
_refresh_loop_thread = None

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

def check_ssh_login_none(ip, port):
    """尝试无密码/无私钥的 SSH 连接"""
    transport = None
    session = None
    sock = None
    try:
        
        sock = socket.create_connection((ip, port), timeout=ssh_timeout)
        
        transport = paramiko.Transport(sock)
        # 关键修复：将套接字的所有权转移给 transport，避免手动关闭套接字
        # transport 会在关闭时自动关闭套接字
        sock = None  # 不再手动关闭套接字
        
        
        transport.start_client(timeout=ssh_timeout)
        transport.auth_none(config.ssh_username)

        session = transport.open_session(timeout=ssh_timeout)
        session.exec_command('echo "SSH test"')
        output = session.recv(1024).decode().strip()
        return True, f"端口{port} SSH登录成功（免认证）"
    except paramiko.AuthenticationException:
        return False, f"端口{port} SSH免认证失败"
    except (OSError, socket.error) as e:
        # 关键修复：捕获套接字错误，避免在解释器关闭时抛出
        error_code = getattr(e, 'winerror', None) or getattr(e, 'errno', None)
        if error_code == 10038:  # WinError 10038: 在一个非套接字上尝试了一个操作
            return False, f"端口{port} SSH连接错误（套接字已关闭）"
        return False, f"端口{port} SSH连接错误: {str(e)}"
    except Exception as e:
        return False, f"端口{port} SSH免认证错误: {str(e)}"
    finally:
        # 关键修复：正确的清理顺序
        # 1. 先关闭 session
        if session:
            try:
                if not session.closed:
                    session.close()
            except (OSError, RuntimeError, Exception):
                # 忽略解释器关闭时的错误
                pass
        # 2. 再关闭 transport（这会自动关闭套接字）
        if transport:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport.is_active():
                    transport.close()
            except (RuntimeError, OSError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 和套接字错误
                pass
        # 3. 如果 transport 创建失败，手动关闭套接字
        if sock:
            try:
                sock.close()
            except (OSError, Exception):
                pass

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
        
        # 将相对路径转换为绝对路径
        key_path = resolve_key_path(key_path)
        
        if not os.path.exists(key_path):
            return False, f"私钥文件不存在: {key_path}"
        
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
                    username=config.ssh_username,
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

def run_checks_once():
    """执行一次全量检查并返回结果列表，带总超时保护"""
    all_results = []
    
    # 动态计算并发数：20-30之间，但不超过服务器数量
    # 使用模块引用以获取动态更新的值
    server_count = len(config.server_dict)
    
    # 如果没有服务器，直接返回空列表
    if server_count == 0:
        return all_results
    
    max_workers = min(max(20, server_count // 5), 30, server_count)
    
    # 确保 max_workers 至少为 1，避免除以零
    if max_workers == 0:
        max_workers = 1
    
    # 总超时保护：根据服务器数量动态调整，最少3分钟，最多5分钟
    # 估算：每台服务器最多60秒，考虑并发，总超时 = (服务器数 / 并发数) * 60秒 + 缓冲
    estimated_time = (server_count / max_workers) * 60 + 60  # 额外60秒缓冲
    total_timeout = min(max(180, int(estimated_time)), 300)  # 3-5分钟之间
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_server = {
            executor.submit(check_single_server, name, ip): (name, ip)
            for name, ip in config.server_dict.items()
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

def refresh_loop():
    """后台循环刷新状态缓存（用于 web_ui.py）"""
    from app.utils.helpers import log_srv
    while not _refresh_loop_stop_event.is_set():
        try:
            results = run_checks_once()
            with cache_lock:
                status_cache["data"] = results
                status_cache["timestamp"] = time.time()
                status_cache["error"] = ""
        except Exception as e:
            # 如果是在解释器关闭时发生的错误，忽略它
            if "interpreter shutdown" in str(e).lower() or "cannot schedule" in str(e).lower():
                break
            with cache_lock:
                status_cache["error"] = str(e)
            log_srv(f"refresh_loop error: {e}")
        
        # 使用 wait 而不是 sleep，以便可以立即响应停止信号
        if _refresh_loop_stop_event.wait(timeout=max(5, check_interval)):
            # 收到停止信号，退出循环
            break

def stop_refresh_loop():
    """停止后台刷新循环"""
    global _refresh_loop_thread
    _refresh_loop_stop_event.set()
    if _refresh_loop_thread and _refresh_loop_thread.is_alive():
        _refresh_loop_thread.join(timeout=2)

__all__ = [
    'check_port',
    'check_ssh_login',
    'check_ssh_login_none',
    'check_single_server',
    'run_checks_once',
    'refresh_loop',
    'stop_refresh_loop',
    'status_cache',
    'cache_lock',
]
