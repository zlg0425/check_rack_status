"""
工具函数模块
"""

import re
import hashlib
import paramiko
import time
import os
import shlex
import threading

# 从 app.utils.config 导入配置
from app.utils.config import (
    ssh_timeout,
    resolve_key,
    resolve_auth_mode,
    version_command,
)
import app.utils.config as config  # 使用模块引用以获取动态更新的值

# 从 core.ssh.transport 导入 create_transport
from core.ssh.transport import create_transport

# 定义日志文件路径
FOTA_LOG = "logs/fota.log"
BACKEND_LOG = "logs/backend.log"


def natural_key(text: str):
    """将名称拆分为文本和数字块，用于自然排序"""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r'(\d+)', text)]


def is_benign_stderr(msg: str) -> bool:
    """判断 stderr 消息是否为可忽略的警告"""
    if not msg:
        return False
    
    benign_patterns = [
        r"^WARNING:",
        r"^Warning:",
        r"deprecated",
        r"Deprecated",
        r"locale",
        r"LC_ALL",
        r"LANG",
    ]
    
    msg_lower = msg.lower()
    for pattern in benign_patterns:
        if re.search(pattern, msg_lower):
            return True
    
    return False


def filter_benign_stdout(msg: str) -> str:
    """过滤 stdout 中的可忽略警告"""
    if not msg:
        return ""
    
    lines = msg.split("\n")
    filtered_lines = []
    for line in lines:
        if not is_benign_stderr(line):
            filtered_lines.append(line)
    
    return "\n".join(filtered_lines)


def fetch_version_via_sftp(transport: paramiko.Transport, path: str):
    """通过 SFTP 读取版本文件"""
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        with sftp.file(path, "r") as f:
            content = f.read().decode(errors="ignore").strip()
        sftp.close()
        return True, content
    except Exception as e:
        return False, str(e)


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
    transport = None
    session = None
    try:
        transport = create_transport(server_name, ip, port)
        session = transport.open_session(timeout=ssh_timeout)
        session.get_pty()
        session.exec_command(f"cd {shlex.quote(workdir)} && {command}")
        stdout, stderr = _read_all(session)
        exit_code = session.recv_exit_status()
        return exit_code, stdout, stderr
    finally:
        if session:
            try:
                if not session.closed:
                    session.close()
            except Exception:
                pass
        if transport:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 错误
                pass


def run_remote_command(server_name: str, ip: str, port: int, command: str):
    """根据认证模式执行远程命令，返回 (success, output_or_error)；使用 pty 并等待结束读取全部输出"""
    import socket
    
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
            transport.auth_none(config.ssh_username)

            if file_path:
                ok, data = fetch_version_via_sftp(transport, file_path)
                if ok:
                    try:
                        if transport.is_active():
                            transport.close()
                    except (RuntimeError, Exception):
                        pass
                    return True, data
                # SFTP 失败则继续走 shell

            session = transport.open_session(timeout=ssh_timeout)
            session.get_pty()  # 申请伪终端保证输出完整
            session.exec_command(f"cd / && {command}")
            output, err_out = _read_all(session)
            session.close()
            try:
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                pass

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
            transport.auth_publickey(username=config.ssh_username, key=private_key)
        except paramiko.ssh_exception.SSHException as auth_err:
            # 如果认证失败且连接已关闭，提供更明确的错误信息
            if "No existing session" in str(auth_err) or not transport.is_alive():
                transport.close()
                raise paramiko.ssh_exception.SSHException(f"Connection closed by remote host during authentication: {auth_err}")
            raise

        if file_path:
            ok, data = fetch_version_via_sftp(transport, file_path)
            if ok:
                try:
                    if transport.is_active():
                        transport.close()
                except (RuntimeError, Exception):
                    pass
                return True, data
            # SFTP 失败则继续走 shell

        session = transport.open_session(timeout=ssh_timeout)
        session.get_pty()  # 申请伪终端保证输出完整
        session.exec_command(f"cd / && {command}")
        output, err_out = _read_all(session)
        session.close()
        try:
            if transport.is_active():
                transport.close()
        except (RuntimeError, Exception):
            pass
        if err_out and not is_benign_stderr(err_out):
            return False, err_out
        return True, output or err_out
    except Exception as e:
        # 确保在异常时关闭transport
        if transport:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport.is_active():
                    transport.close()
            except (RuntimeError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 错误
                pass
        return False, str(e)


def log_srv(message: str):
    """记录后端服务日志"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.exists(os.path.dirname(BACKEND_LOG)):
        os.makedirs(os.path.dirname(BACKEND_LOG), exist_ok=True)
    with open(BACKEND_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def log_fota(message: str):
    """记录FOTA日志"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.exists(os.path.dirname(FOTA_LOG)):
        os.makedirs(os.path.dirname(FOTA_LOG), exist_ok=True)
    with open(FOTA_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")
    try:
        # 确保日志目录存在
        log_dir = os.path.dirname(BACKEND_LOG)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        with open(BACKEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception as e:
        print(f"Error writing to backend log: {e}")


# 注意：remote_md5, remote_exists, remote_remove 已在 core/sftp/operations.py 中定义
# 如果需要使用这些函数，请直接从 core.sftp 导入

def run_ucm_with_log(server_name: str, ip: str, port: int, ucm_file: str, log_file: str = None, tail_wait: int = 2):
    """启动日志会话B，2秒后启动A执行 lpUCM，A 结束后再等待 tail_wait 秒再停止日志。返回 (ok, info)。"""
    
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
        transport_b = None
        session_b = None
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
        except Exception as e:
            log_err.append(str(e))
        finally:
            # 确保资源被正确关闭，避免在解释器关闭时创建新线程
            if session_b:
                try:
                    if not session_b.closed:
                        session_b.close()
                except Exception:
                    pass
            if transport_b:
                try:
                    # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                    if transport_b.is_active():
                        transport_b.close()
                except (RuntimeError, Exception):
                    # 忽略 "can't create new thread at interpreter shutdown" 错误
                    pass

    t = threading.Thread(target=tail_log, daemon=True)
    t.start()

    # 等待B稳定
    time.sleep(2)

    transport_a = None
    session_a = None
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
    except Exception as e:
        log_stop.set()
        t.join(timeout=2)
        return False, f"执行 lpUCM 失败: {e}"
    finally:
        # 确保资源被正确关闭
        if session_a:
            try:
                if not session_a.closed:
                    session_a.close()
            except Exception:
                pass
        if transport_a:
            try:
                # 检查 transport 是否仍然活跃，避免在解释器关闭时创建新线程
                if transport_a.is_active():
                    transport_a.close()
            except (RuntimeError, Exception):
                # 忽略 "can't create new thread at interpreter shutdown" 错误
                pass

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
    # 如果指定了服务器名称且是8650系列，使用采样MD5
    if server_name and server_name.startswith("LP-8650"):
        return md5_bytes_sampled(data)
    else:
        # 8797系列或其他情况使用完整MD5
        return hashlib.md5(data).hexdigest()


def md5_bytes_sampled(data: bytes) -> str:
    """采样计算字节数据的 MD5 值（用于大文件）"""
    total_size = len(data)
    sample_size = 1024 * 1024  # 1MB
    
    md5_hash = hashlib.md5()
    
    # 1. 读取开头1MB
    if total_size > 0:
        md5_hash.update(data[:min(sample_size, total_size)])
    
    # 2. 读取中间1MB（文件中间位置）
    if total_size > sample_size * 2:
        mid_start = (total_size - sample_size) // 2
        md5_hash.update(data[mid_start:mid_start + sample_size])
    
    # 3. 读取结尾1MB
    if total_size > sample_size:
        md5_hash.update(data[-sample_size:])
    
    # 4. 将实际文件大小也加入MD5计算，增加唯一性
    md5_hash.update(str(total_size).encode())
    
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
        (file_size - 1024 * 1024) // 2,  # 中间
        file_size - 1024 * 1024  # 结尾
    ]
    
    sample_size = 1024 * 1024  # 1MB
    
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
    """采样计算流的 MD5 值（重置流位置，用于大文件）"""
    sample_size = 1024 * 1024  # 1MB
    md5_hash = hashlib.md5()
    
    # 1. 读取开头1MB
    stream.seek(0)
    chunk = stream.read(min(sample_size, file_size))
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
    
    # 4. 将实际文件大小也加入MD5计算，增加唯一性
    md5_hash.update(str(file_size).encode())
    
    # 重置流位置
    stream.seek(0)
    
    return md5_hash.hexdigest()


def create_tee_stream(stream, target_stream, server_name: str = None, file_size: int = None):
    """创建一个tee式包装流，在读取数据时同时写入目标流并计算MD5
    用于在上传文件时同时计算MD5和写入目标流
    
    注意：对于8650系列，由于使用采样MD5，需要在读取完成后从临时文件重新计算MD5
    以确保使用实际文件大小而不是期望大小
    
    Args:
        stream: 源流对象
        target_stream: 目标流对象（如文件对象）
        server_name: 服务器名称（用于MD5计算方式选择）
        file_size: 文件大小（字节）
    """
    from app.utils.helpers import md5_stream_noseek
    
    class TeeStream:
        def __init__(self, source, target, server_name=None, file_size=None):
            self.source = source
            self.target = target
            self.server_name = server_name
            self.file_size = file_size
            self.md5_hash = None
            self.md5_val = None
            self.pos = 0
            self.bytes_read = 0  # 实际读取的字节数
            self.is_8650 = server_name and server_name.startswith("LP-8650")
            # 对于8650系列，标记需要从文件重新计算MD5
            self.need_recalculate_md5 = self.is_8650
        
        def read(self, size=-1):
            data = self.source.read(size)
            if data:
                if self.target:
                    self.target.write(data)
                    self.target.flush()
                
                self.bytes_read += len(data)
                
                # 8797系列：使用完整MD5计算（边读边算）
                if not self.is_8650:
                    if self.md5_hash is None:
                        import hashlib
                        self.md5_hash = hashlib.md5()
                    self.md5_hash.update(data)
                
                self.pos += len(data)
            return data
        
        def seek(self, pos, whence=0):
            if whence == 0:
                self.pos = pos
            elif whence == 1:
                self.pos += pos
            elif whence == 2:
                if self.file_size:
                    self.pos = self.file_size + pos
            if hasattr(self.source, 'seek'):
                return self.source.seek(pos, whence)
            return 0
        
        def tell(self):
            return self.pos
        
        def get_md5(self):
            """获取计算出的MD5值
            
            注意：对于8650系列，此方法返回None，表示需要从临时文件重新计算MD5
            调用者应该使用 md5_stream() 从临时文件重新计算
            """
            if self.is_8650:
                # 8650系列：返回None，表示需要从文件重新计算
                # 这样可以确保使用实际文件大小而不是期望大小
                return None
            else:
                # 8797系列：返回完整MD5
                if self.md5_hash:
                    return self.md5_hash.hexdigest()
                return None
        
        def get_bytes_read(self):
            """获取实际读取的字节数"""
            return self.bytes_read
    
    tee_stream = TeeStream(stream, target_stream, server_name, file_size)
    return tee_stream, tee_stream  # 返回两个值以保持与旧代码的兼容性


def create_md5_calculating_stream(stream, server_name: str = None, file_size: int = None):
    """创建一个计算MD5的流包装器
    用于在读取流时同时计算MD5值
    
    Args:
        stream: 源流对象
        server_name: 服务器名称（用于MD5计算方式选择）
        file_size: 文件大小（字节）
    """
    class MD5CalculatingStream:
        def __init__(self, source, server_name=None, file_size=None):
            self.source = source
            self.server_name = server_name
            self.file_size = file_size
            self.md5_hash = None
            self.md5_val = None
            self.pos = 0
            self.total_read = 0
        
        def read(self, size=-1):
            data = self.source.read(size)
            if data:
                if self.md5_hash is None:
                    import hashlib
                    self.md5_hash = hashlib.md5()
                self.md5_hash.update(data)
                self.total_read += len(data)
                self.pos += len(data)
            return data
        
        def seek(self, pos, whence=0):
            # 如果流不支持 seek，回退到完整读取
            if not hasattr(self.source, 'seek'):
                return 0
            if whence == 0:
                self.pos = pos
            elif whence == 1:
                self.pos += pos
            elif whence == 2:
                if self.file_size:
                    self.pos = self.file_size + pos
            return self.source.seek(pos, whence)
        
        def tell(self):
            return self.pos
        
        def get_md5(self):
            """获取计算出的MD5值"""
            if self.md5_hash:
                return self.md5_hash.hexdigest()
            return None
    
    return MD5CalculatingStream(stream, server_name, file_size)


# 兼容旧接口的惰性导入包装，避免循环依赖（core.sftp.operations / core.fota <-> helpers）
def _lazy_sftp_upload(*args, **kwargs):
    from core.sftp.operations import sftp_upload as _impl
    return _impl(*args, **kwargs)


def _lazy_ensure_remote_dir(*args, **kwargs):
    from core.sftp.operations import ensure_remote_dir as _impl
    return _impl(*args, **kwargs)


def _lazy_validate_remote_path(*args, **kwargs):
    from core.sftp.operations import validate_remote_path as _impl
    return _impl(*args, **kwargs)


def _lazy_check_remote_disk_space(*args, **kwargs):
    from core.sftp.operations import check_remote_disk_space as _impl
    return _impl(*args, **kwargs)


def _lazy_check_remote_file_exists(*args, **kwargs):
    from core.sftp.operations import check_remote_file_exists as _impl
    return _impl(*args, **kwargs)


def _lazy_detect_fota_port(*args, **kwargs):
    from core.fota import detect_fota_port as _impl
    return _impl(*args, **kwargs)


def _lazy_record_fota_timing(*args, **kwargs):
    from core.fota import record_fota_timing as _impl
    return _impl(*args, **kwargs)


def _lazy_get_avg_fota_timing(*args, **kwargs):
    from core.fota import get_avg_fota_timing as _impl
    return _impl(*args, **kwargs)


# 所有函数已迁移完成，不再需要从 check_rack_status 导入

__all__ = [
    'create_transport',
    'sftp_upload',
    'ensure_remote_dir',
    'validate_remote_path',
    'check_remote_disk_space',
    'check_remote_file_exists',
    'detect_fota_port',
    'record_fota_timing',
    'get_avg_fota_timing',
    'md5_bytes',
    'md5_bytes_sampled',
    'md5_stream',
    'md5_stream_noseek',
    'md5_stream_noseek_sampled',
    'md5_stream_sampled',
    'create_md5_calculating_stream',
    'create_tee_stream',
    'natural_key',
    'is_benign_stderr',
    'filter_benign_stdout',
    'fetch_version_via_sftp',
    'parse_md5_output',
    '_read_all',
    'remote_exec_collect',
    'run_remote_command',
    'log_srv',
    'log_fota',
    # 注意：remote_md5, remote_exists, remote_remove 在 core/sftp/operations.py 中
    'run_ucm_with_log',
]

# 将兼容名称绑定到惰性包装函数
sftp_upload = _lazy_sftp_upload
ensure_remote_dir = _lazy_ensure_remote_dir
validate_remote_path = _lazy_validate_remote_path
check_remote_disk_space = _lazy_check_remote_disk_space
check_remote_file_exists = _lazy_check_remote_file_exists
detect_fota_port = _lazy_detect_fota_port
record_fota_timing = _lazy_record_fota_timing
get_avg_fota_timing = _lazy_get_avg_fota_timing
