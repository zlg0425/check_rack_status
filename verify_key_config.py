#!/usr/bin/env python3
"""
验证私钥配置是否正确从 config.json 加载
"""

import sys
import os

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.utils.config import load_config, resolve_key, resolve_key_path, resolve_auth_mode
import app.utils.config as config

def main():
    print("=" * 60)
    print("私钥配置验证")
    print("=" * 60)
    
    # 加载配置
    load_config()
    
    print("\n1. 配置加载检查")
    print("-" * 60)
    print(f"key_mapping: {config.key_mapping}")
    print(f"group_key_mapping: {config.group_key_mapping}")
    print(f"group_auth_mode: {config.group_auth_mode}")
    
    print("\n2. LP-8650 服务器私钥解析")
    print("-" * 60)
    servers_8650 = ['LP-8650-1', 'LP-8650-2', 'LP-8650-3', 'LP-8650-20', 'LP-8650-21']
    for srv in servers_8650:
        key22 = resolve_key(srv, 22)
        key9999 = resolve_key(srv, 9999)
        auth_mode = resolve_auth_mode(srv)
        print(f"{srv:15s} | port 22: {key22:15s} | port 9999: {key9999:15s} | auth: {auth_mode}")
    
    print("\n3. LP-8797 服务器私钥解析")
    print("-" * 60)
    servers_8797 = ['LP-8797-1', 'LP-8797-2', 'LP-8797-4', 'LP-8797-5', 'LP-8797-6']
    for srv in servers_8797:
        key22 = resolve_key(srv, 22)
        key9999 = resolve_key(srv, 9999)
        auth_mode = resolve_auth_mode(srv)
        print(f"{srv:15s} | port 22: {key22:15s} | port 9999: {key9999:15s} | auth: {auth_mode}")
    
    print("\n4. 私钥文件存在性检查")
    print("-" * 60)
    all_keys = set()
    for srv in servers_8650 + servers_8797:
        key22 = resolve_key(srv, 22)
        key9999 = resolve_key(srv, 9999)
        if key22:
            all_keys.add(key22)
        if key9999:
            all_keys.add(key9999)
    
    for key in sorted(all_keys):
        abs_path = resolve_key_path(key)
        exists = os.path.exists(abs_path)
        status = "✓" if exists else "✗"
        print(f"{status} {key:15s} -> {abs_path}")
        if not exists:
            print(f"  WARNING: 私钥文件不存在！")
    
    print("\n5. 验证结果")
    print("-" * 60)
    all_exist = all(os.path.exists(resolve_key_path(key)) for key in all_keys)
    if all_exist:
        print("✓ 所有私钥文件都存在")
    else:
        print("✗ 部分私钥文件不存在")
    
    # 测试实际 SSH 连接（可选）
    print("\n6. SSH 连接测试（LP-8650-1）")
    print("-" * 60)
    try:
        from core.monitoring.checker import check_ssh_login
        result22, msg22 = check_ssh_login('LP-8650-1', '10.99.19.11', 22)
        result9999, msg9999 = check_ssh_login('LP-8650-1', '10.99.19.11', 9999)
        print(f"Port 22:  {'✓' if result22 else '✗'} {msg22}")
        print(f"Port 9999: {'✓' if result9999 else '✗'} {msg9999}")
    except Exception as e:
        print(f"测试失败: {e}")
    
    print("\n" + "=" * 60)

if __name__ == '__main__':
    main()

