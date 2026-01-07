"""
FOTA 管理模块
提供 FOTA 升级相关的功能，包括日志记录、端口检测、耗时统计等
"""

import json
import os
import time
import threading

from app.utils.config import fota_filename_validation
# run_ucm_with_log 已迁移到 app.utils.helpers，但为了向后兼容，这里也导出
from app.utils.helpers import run_ucm_with_log

# FOTA 日志文件路径
FOTA_LOG = "logs/fota.log"
FOTA_TIMING_STATS_FILE = "fota_timing_stats.json"

# FOTA耗时统计锁
fota_timing_stats_lock = threading.Lock()


def log_fota(message: str):
    """记录 FOTA 日志"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        # 确保日志目录存在
        log_dir = os.path.dirname(FOTA_LOG)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        with open(FOTA_LOG, "a", encoding="utf-8") as f:
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


def detect_fota_port(server_name: str, filename: str) -> tuple[int, str]:
    """
    根据服务器名称和文件名自动判断FOTA端口
    同时验证文件是否与服务器类型匹配
    
    Args:
        server_name: 服务器名称，如 "LP-8650-1", "LP-8797-1"
        filename: 文件名，如 "LP-ADDC050_v1.0.icsw"
    
    Returns:
        (port, message): 端口号(22或9999)和提示信息
        如果无法判断，返回 (0, error_message)
    """
    # 1. 确定服务器类型
    server_type = None
    if server_name.startswith("LP-8650"):
        server_type = "LP-8650"
    elif server_name.startswith("LP-8797"):
        server_type = "LP-8797"
    else:
        return (0, f"未知的服务器类型: {server_name}")
    
    # 2. 获取该服务器类型的端口配置
    if not fota_filename_validation or server_type not in fota_filename_validation:
        return (0, f"服务器类型 {server_type} 未配置文件名验证规则")
    
    port_config = fota_filename_validation[server_type]
    
    # 3. **关键增强：检查文件名是否与其他服务器类型的前缀匹配**
    #   如果匹配了其他服务器类型的文件，应该提示错误
    all_server_types = list(fota_filename_validation.keys())
    mismatched_types = []
    
    for other_type in all_server_types:
        if other_type == server_type:
            continue  # 跳过当前服务器类型
        
        other_config = fota_filename_validation[other_type]
        for other_prefix in other_config.values():
            if other_prefix in filename:
                mismatched_types.append({
                    "type": other_type,
                    "prefix": other_prefix
                })
    
    # 如果文件名匹配了其他服务器类型的文件，返回错误
    if mismatched_types:
        mismatched_info = ", ".join([f"{m['type']}({m['prefix']})" for m in mismatched_types])
        expected_prefixes = list(port_config.values())
        return (0, f"文件 '{filename}' 不匹配当前服务器类型 {server_type}。\n"
                   f"检测到其他服务器类型的文件: {mismatched_info}\n"
                   f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
    
    # 4. 检查文件名包含哪个端口的前缀（仅检查当前服务器类型）
    detected_ports = []
    for port_str, prefix in port_config.items():
        if prefix in filename:
            detected_ports.append((int(port_str), prefix))
    
    # 5. 判断结果
    if len(detected_ports) == 0:
        # 没有匹配的前缀
        expected_prefixes = list(port_config.values())
        # 检查是否包含任何已知的前缀（用于更友好的错误提示）
        all_prefixes = []
        for st in all_server_types:
            all_prefixes.extend(fota_filename_validation[st].values())
        
        found_prefixes = [p for p in all_prefixes if p in filename]
        if found_prefixes:
            # 找到了前缀，但是不匹配当前服务器类型
            return (0, f"文件 '{filename}' 包含的前缀 '{', '.join(found_prefixes)}' 不匹配服务器类型 {server_type}。\n"
                       f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
        else:
            # 完全没有找到任何已知前缀
            return (0, f"文件 '{filename}' 不包含任何有效的前缀。\n"
                       f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
    elif len(detected_ports) == 1:
        # 唯一匹配
        port, prefix = detected_ports[0]
        return (port, f"检测到端口 {port} (前缀: {prefix})")
    else:
        # 多个匹配（理论上不应该发生，但需要处理）
        ports = [str(p) for p, _ in detected_ports]
        return (0, f"文件 '{filename}' 匹配多个端口: {', '.join(ports)}，请检查文件名")


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


__all__ = [
    'log_fota',
    'load_fota_timing_stats',
    'save_fota_timing_stats',
    'record_fota_timing',
    'detect_fota_port',
    'get_avg_fota_timing',
    'run_ucm_with_log',
    'fota_timing_stats_lock',
]
