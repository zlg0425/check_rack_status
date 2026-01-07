"""
配置管理模块
"""

import json
import os

# ============== 配置区域 ==============
CONFIG_FILE = "config.json"

# 全局配置变量
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
    # 获取项目根目录（app/utils 的父目录的父目录）
    project_root = os.path.dirname(os.path.dirname(script_dir))
    abs_path = os.path.join(project_root, config_path)

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

    ssh_username = cfg.get("ssh_username", "root") or "root"  # 如果为空字符串，使用默认值 "root"
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
    # 使用模块内的全局变量（避免导入绑定问题）
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


def resolve_key_path(key_path: str) -> str:
    """将相对路径的私钥转换为绝对路径（相对于项目根目录）
    
    Args:
        key_path: 私钥路径（可能是相对路径或绝对路径）
    
    Returns:
        绝对路径
    """
    if not key_path:
        return key_path
    
    # 如果已经是绝对路径，直接返回
    if os.path.isabs(key_path):
        return key_path
    
    # 转换为绝对路径（相对于项目根目录）
    # app/utils/config.py 的父目录的父目录就是项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    resolved_path = os.path.join(project_root, key_path)
    
    # #region agent log
    try:
        import json
        import time
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": "P",
                "location": "app/utils/config.py:resolve_key_path",
                "message": "解析私钥路径",
                "data": {
                    "key_path_input": key_path,
                    "script_dir": script_dir,
                    "project_root": project_root,
                    "resolved_path": resolved_path,
                    "file_exists": os.path.exists(resolved_path)
                },
                "timestamp": int(time.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
    
    return resolved_path


# 为了向后兼容，同时导出 servers_dict（复数）和 server_dict（单数）
servers_dict = server_dict

__all__ = [
    'load_config',
    'server_dict',  # 原始名称
    'servers_dict',  # 别名（向后兼容）
    'key_mapping',
    'group_key_mapping',
    'group_auth_mode',
    'ssh_username',
    'ssh_timeout',
    'check_interval',
    'auth_mode_default',
    'version_command',
    'upload_target_dir',
    'fota_target_dir',
    'fota_filename_validation',
    'resolve_key',
    'resolve_auth_mode',
    'resolve_key_path',
]
