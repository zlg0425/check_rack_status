#!/usr/bin/env python3
"""
分析 check_rack_status.py 中的函数，检查哪些已迁移，哪些未迁移
"""

import sys
import os
import inspect
import ast

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 已迁移的函数列表（从兼容层和新模块中获取）
MIGRATED_FUNCTIONS = {
    # 配置相关
    'load_config': 'app.utils.config',
    'resolve_key': 'app.utils.config',
    'resolve_auth_mode': 'app.utils.config',
    
    # SFTP相关
    'sftp_upload': 'core.sftp.operations',
    'sftp_download': 'core.sftp.operations',
    'remote_md5': 'core.sftp.operations',
    'remote_exists': 'core.sftp.operations',
    'remote_remove': 'core.sftp.operations',
    'validate_remote_path': 'core.sftp.operations',
    'check_remote_disk_space': 'core.sftp.operations',
    'check_remote_file_exists': 'core.sftp.operations',
    'ensure_remote_dir': 'core.sftp.operations',
    
    # SSH Transport
    'create_transport': 'core.ssh.transport',
    
    # FOTA相关
    'run_ucm_with_log': 'app.utils.helpers',
    
    # 监控相关
    'run_checks_once': 'core.monitoring.checker',
    'check_port': 'core.monitoring.checker',
    'check_ssh_login': 'core.monitoring.checker',
    'check_ssh_login_none': 'core.monitoring.checker',
    'check_single_server': 'core.monitoring.checker',
    
    # 工具函数
    'md5_bytes': 'app.utils.helpers',
    'md5_bytes_sampled': 'app.utils.helpers',
    'md5_stream': 'app.utils.helpers',
    'md5_stream_noseek': 'app.utils.helpers',
    'md5_stream_noseek_sampled': 'app.utils.helpers',
    'md5_stream_sampled': 'app.utils.helpers',
    'create_tee_stream': 'app.utils.helpers',
    'create_md5_calculating_stream': 'app.utils.helpers',
    'natural_key': 'app.utils.helpers',
    'is_benign_stderr': 'app.utils.helpers',
    'filter_benign_stdout': 'app.utils.helpers',
    'fetch_version_via_sftp': 'app.utils.helpers',
    'parse_md5_output': 'app.utils.helpers',
    'remote_exec_collect': 'app.utils.helpers',
    'run_remote_command': 'app.utils.helpers',
}

def get_functions_in_file(filepath):
    """获取文件中定义的所有函数"""
    functions = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
    except Exception as e:
        print(f"解析文件失败: {e}")
    
    return functions

def check_function_location(func_name):
    """检查函数实际源代码位置"""
    try:
        # 尝试从新模块导入
        for module_path in MIGRATED_FUNCTIONS.values():
            try:
                module = __import__(module_path, fromlist=[func_name])
                if hasattr(module, func_name):
                    func = getattr(module, func_name)
                    source_file = inspect.getfile(func)
                    if 'check_rack_status.py' not in source_file:
                        return source_file
            except (ImportError, AttributeError):
                continue
        
        # 尝试从兼容层导入
        try:
            from check_rack_status_compat import __dict__ as compat_dict
            if func_name in compat_dict:
                return 'check_rack_status_compat.py (from new modules)'
        except ImportError:
            pass
        
        return None
    except Exception as e:
        return f"Error: {e}"

def main():
    print("=" * 60)
    print("check_rack_status.py 函数迁移状态分析")
    print("=" * 60)
    
    filepath = 'check_rack_status.py'
    if not os.path.exists(filepath):
        print(f"文件不存在: {filepath}")
        return 1
    
    # 获取文件中定义的所有函数
    functions = get_functions_in_file(filepath)
    
    print(f"\n文件中定义的函数总数: {len(functions)}")
    print("\n函数迁移状态:")
    print("-" * 60)
    
    migrated = []
    not_migrated = []
    unknown = []
    
    for func_name in sorted(functions):
        if func_name == 'main':  # main函数通常不迁移
            continue
        
        if func_name in MIGRATED_FUNCTIONS:
            new_location = check_function_location(func_name)
            if new_location and 'check_rack_status.py' not in new_location:
                migrated.append((func_name, MIGRATED_FUNCTIONS[func_name]))
                print(f"✅ {func_name:30s} -> {MIGRATED_FUNCTIONS[func_name]}")
            else:
                unknown.append(func_name)
                print(f"⚠️  {func_name:30s} -> 已标记迁移但位置未知")
        else:
            not_migrated.append(func_name)
            print(f"❌ {func_name:30s} -> 未迁移")
    
    print("\n" + "=" * 60)
    print("统计汇总")
    print("=" * 60)
    print(f"已迁移: {len(migrated)} 个函数")
    print(f"未迁移: {len(not_migrated)} 个函数")
    print(f"未知状态: {len(unknown)} 个函数")
    
    if not_migrated:
        print("\n未迁移的函数:")
        for func_name in not_migrated:
            print(f"  - {func_name}")
    
    if unknown:
        print("\n状态未知的函数:")
        for func_name in unknown:
            print(f"  - {func_name}")
    
    print("\n" + "=" * 60)
    print("建议")
    print("=" * 60)
    
    if not_migrated:
        print("⚠️  发现未迁移的函数，建议:")
        print("  1. 检查这些函数是否仍在使用")
        print("  2. 如果不再使用，可以考虑删除")
        print("  3. 如果仍在使用，需要迁移到对应模块")
    else:
        print("✅ 所有函数都已迁移或标记为已迁移")
        print("   注意: check_rack_status.py 保留作为兼容层或历史记录")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())

