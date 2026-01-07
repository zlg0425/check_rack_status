#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH连接测试脚本
用于隔离测试 AsyncSSH 2.x 连接，排除 WebSocket 和前端的影响

使用方法:
    python test_ssh_connection.py [server_name]
    
示例:
    python test_ssh_connection.py LP-8650-1
    python test_ssh_connection.py LP-8295-1
"""

import asyncio
import sys
import os
import traceback
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.utils.config import (
    load_config, resolve_auth_mode, resolve_key, resolve_key_path,
    ssh_timeout
)
from core.ssh.adapter import SSHConnectionAdapter, ConnectionConfig


async def test_ssh_connection(server_name: str = None):
    """
    测试SSH连接
    
    Args:
        server_name: 服务器名称，如果为None则从命令行参数获取
    """
    # 加载配置
    load_config()
    
    # 获取SSH用户名（从模块导入，避免作用域问题）
    from app.utils.config import ssh_username as config_username
    final_username = config_username if config_username and config_username.strip() else "root"
    
    # 从命令行参数获取服务器名称
    if server_name is None:
        if len(sys.argv) > 1:
            server_name = sys.argv[1]
        else:
            print("错误: 请提供服务器名称")
            print("使用方法: python test_ssh_connection.py <server_name>")
            print("示例: python test_ssh_connection.py LP-8650-1")
            return
    
    # 获取服务器配置
    from app.utils.config import server_dict
    if server_name not in server_dict:
        print(f"错误: 服务器 '{server_name}' 不在配置中")
        print(f"可用的服务器: {', '.join(server_dict.keys())}")
        return
    
    server_info = server_dict[server_name]
    
    # 处理两种配置格式：
    # 1. 字符串格式: "10.99.19.11" (当前config.json格式)
    # 2. 字典格式: {"ip": "10.99.19.11", "port": 22} (扩展格式)
    if isinstance(server_info, dict):
        server_ip = server_info.get('ip', '')
        port = server_info.get('port', 22)
    else:
        # 字符串格式，默认端口22
        server_ip = str(server_info)
        port = 22
    
    if not server_ip:
        print(f"错误: 服务器 '{server_name}' 没有配置IP地址")
        return
    
    print(f"\n{'='*60}")
    print(f"测试SSH连接: {server_name}")
    print(f"目标服务器: {server_ip}:{port}")
    print(f"{'='*60}\n")
    
    # 解析认证信息
    auth_mode = resolve_auth_mode(server_name)
    key_path = None
    password = None
    
    if auth_mode == "key":
        key_path = resolve_key(server_name, port)
        if key_path and not os.path.isabs(key_path):
            key_path = resolve_key_path(key_path)
        print(f"认证方式: 密钥认证")
        print(f"密钥路径: {key_path}")
        if key_path and os.path.exists(key_path):
            print(f"[OK] 密钥文件存在")
        else:
            print(f"[ERROR] 密钥文件不存在: {key_path}")
            return
    else:
        password = "***"  # 不显示密码
        print(f"认证方式: 密码认证")
    
    # 显示用户名
    if final_username != config_username:
        print(f"[WARN] 配置中用户名为空，使用默认用户名: {final_username}")
    
    print(f"用户名: {final_username}")
    
    # 创建连接配置
    conn_config = ConnectionConfig(
        host=server_ip,
        port=port,
        username=final_username,
        client_keys=[key_path] if key_path else None,
        password=password if auth_mode == "password" else None,
        connect_timeout=ssh_timeout,
        skip_host_key_check=True,  # 测试环境跳过主机密钥验证
        known_hosts=None,
        keepalive_interval=30,
        keepalive_count_max=3
    )
    print(f"连接超时: {ssh_timeout}秒")
    print(f"Keepalive: {conn_config.keepalive_interval}秒")
    print(f"\n开始连接...\n")
    
    adapter = None
    conn = None
    shell = None
    
    try:
        # 步骤1: 创建适配器
        print("步骤1: 创建SSH适配器...")
        adapter = SSHConnectionAdapter(enable_connection_pool=False)
        print("[OK] 适配器创建成功\n")
        
        # 步骤2: 建立SSH连接
        print("步骤2: 建立SSH连接...")
        try:
            conn = await adapter.create_connection(conn_config)
            print("[OK] SSH连接成功\n")
        except Exception as e:
            print(f"[ERROR] SSH连接失败")
            print(f"错误类型: {type(e).__name__}")
            print(f"错误信息: {e}")
            print(f"\n详细堆栈:")
            traceback.print_exc()
            return
        
        # 步骤3: 测试执行命令
        print("步骤3: 测试执行命令...")
        try:
            result = await adapter.run_command(conn, 'echo "Hello from SSH test"')
            if result['success']:
                print(f"[OK] 命令执行成功")
                print(f"输出: {result['stdout'].strip()}")
                print(f"退出码: {result['exit_status']}\n")
            else:
                print(f"[ERROR] 命令执行失败")
                print(f"错误: {result.get('error', 'Unknown error')}")
                print(f"错误类型: {result.get('error_type', 'Unknown')}\n")
        except Exception as e:
            print(f"[ERROR] 命令执行异常")
            print(f"错误类型: {type(e).__name__}")
            print(f"错误信息: {e}")
            traceback.print_exc()
            print()
        
        # 步骤4: 创建交互式Shell
        print("步骤4: 创建交互式Shell...")
        try:
            shell = await adapter.create_interactive_shell(
                conn,
                term_type='xterm-256color',
                cols=80,
                rows=24
            )
            print("[OK] Shell创建成功\n")
            
            # 步骤5: 测试Shell读写
            print("步骤5: 测试Shell读写...")
            try:
                # 写入测试命令
                await shell.write('echo "Shell test successful"\n')
                await asyncio.sleep(0.5)  # 等待输出
                
                # 读取输出
                output = await adapter.read_from_shell(shell, 4096, timeout=2.0)
                if output:
                    print(f"[OK] Shell读取成功")
                    print(f"输出: {output.decode('utf-8', errors='ignore')[:200]}")
                else:
                    print("[WARN] Shell读取超时（可能是正常的）")
                print()
            except Exception as e:
                print(f"[ERROR] Shell读写测试失败")
                print(f"错误类型: {type(e).__name__}")
                print(f"错误信息: {e}")
                traceback.print_exc()
                print()
            
        except Exception as e:
            print(f"[ERROR] Shell创建失败")
            print(f"错误类型: {type(e).__name__}")
            print(f"错误信息: {e}")
            print(f"\n详细堆栈:")
            traceback.print_exc()
            print()
        
        print(f"{'='*60}")
        print("[OK] 所有测试完成")
        print(f"{'='*60}\n")
        
    except KeyboardInterrupt:
        print("\n\n用户中断测试")
    except Exception as e:
        print(f"\n[ERROR] 未预期的错误")
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {e}")
        print(f"\n详细堆栈:")
        traceback.print_exc()
    finally:
        # 清理资源
        print("清理资源...")
        try:
            if shell:
                await adapter.close_shell(shell)
                print("[OK] Shell已关闭")
        except Exception as e:
            print(f"[WARN] 关闭Shell失败: {e}")
        
        try:
            if conn:
                await adapter.close_connection()
                print("[OK] SSH连接已关闭")
        except Exception as e:
            print(f"[WARN] 关闭SSH连接失败: {e}")
        
        print("\n测试结束")


if __name__ == "__main__":
    try:
        asyncio.run(test_ssh_connection())
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n致命错误: {e}")
        traceback.print_exc()
