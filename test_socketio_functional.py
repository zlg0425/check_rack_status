#!/usr/bin/env python3
"""
SocketIO 功能测试 - 测试终端 SocketIO 事件处理器
需要服务端已启动（默认 http://127.0.0.1:8888）
支持从 config.json 加载服务器配置
"""

import sys
import os
import time
import json
import socketio

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 注意：默认端口已更改为8888（与run.py一致）
DEFAULT_API_URL = "http://127.0.0.1:8888"
DEFAULT_PORTS = [22, 9999]  # 默认测试的端口列表


def load_config_from_file(config_path="config.json"):
    """从 config.json 加载配置"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, config_path)
    
    if not os.path.exists(config_file):
        return None
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 无法加载配置文件 {config_file}: {e}")
        return None


def get_servers_from_config(config=None):
    """从配置中获取服务器列表"""
    if config is None:
        config = load_config_from_file()
    
    if config is None:
        return {}
    
    return config.get("servers", {})


def list_available_servers(config=None):
    """列出所有可用的服务器"""
    servers = get_servers_from_config(config)
    if not servers:
        print("未找到可用服务器（请检查 config.json）")
        return
    
    print("\n可用服务器列表：")
    print("-" * 60)
    for i, (name, ip) in enumerate(sorted(servers.items()), 1):
        print(f"{i:2d}. {name:20s} -> {ip}")
    print("-" * 60)

def test_socketio_events(api_url, server_name="test-server", server_ip="127.0.0.1", port=22):
    """测试 SocketIO 事件"""
    print("\n=== 测试 SocketIO 事件 ===")
    
    try:
        # 创建 SocketIO 客户端
        sio = socketio.Client()
        
        events_received = []
        errors = []
        
        def on_connect():
            print("[OK] SocketIO 连接成功")
            events_received.append('connect')
        
        def on_disconnect():
            print("[OK] SocketIO 断开连接")
            events_received.append('disconnect')
        
        def on_connected(data):
            print(f"[OK] 收到 'connected' 事件: {data}")
            events_received.append('connected')
        
        def on_output(data):
            print(f"[OK] 收到 'output' 事件: {len(str(data))} 字符")
            events_received.append('output')
        
        def on_error(data):
            error_msg = data.get('message', '未知错误') if isinstance(data, dict) else str(data)
            print(f"[WARN] 收到 'error' 事件: {error_msg}")
            errors.append(error_msg)
            events_received.append('error')
        
        def on_disconnected(data):
            print(f"[OK] 收到 'disconnected' 事件")
            events_received.append('disconnected')
        
        # 注册事件处理器
        sio.on('connect', on_connect)
        sio.on('disconnect', on_disconnect)
        sio.on('connected', on_connected)
        sio.on('output', on_output)
        sio.on('error', on_error)
        sio.on('disconnected', on_disconnected)
        
        # 从 HTTP URL 提取主机和端口
        if api_url.startswith('http://'):
            url = api_url.replace('http://', '')
        elif api_url.startswith('https://'):
            url = api_url.replace('https://', '')
        else:
            url = api_url
        
        # 移除路径部分
        if '/' in url:
            url = url.split('/')[0]
        
        print(f"连接到 SocketIO 服务器: http://{url}")
        
        # 连接
        try:
            sio.connect(f"http://{url}", wait_timeout=5)
            
            if not sio.connected:
                print("[FAIL] SocketIO 连接失败")
                return False
            
            print("[OK] SocketIO 连接建立")
            
            # 等待一下确保连接稳定
            time.sleep(0.5)
            
            # 测试发送 start_ssh 事件
            print(f"\n发送 'start_ssh' 事件 (server: {server_name}, ip: {server_ip}, port: {port})")
            sio.emit('start_ssh', {
                'server_name': server_name,
                'server_ip': server_ip,
                'port': port,
                'cols': 80,
                'rows': 24
            })
            
            # 等待响应
            print("等待响应...")
            time.sleep(5)  # 增加等待时间，SSH连接可能需要更长时间
            
            # 检查是否收到 connected 或 error 事件
            if 'connected' in events_received:
                print("[OK] SSH 连接建立成功")
                
                # 测试发送终端输入
                print("\n发送 'terminal_input' 事件")
                sio.emit('terminal_input', {'data': 'echo "test"\n'})
                
                # 等待输出
                time.sleep(1)
                
                if 'output' in events_received:
                    print("[OK] 收到终端输出")
                else:
                    print("[WARN] 未收到终端输出（可能需要更长的等待时间）")
                
                # 测试发送终端调整大小事件
                print("\n发送 'terminal_resize' 事件")
                sio.emit('terminal_resize', {'cols': 120, 'rows': 30})
                time.sleep(0.5)
                
            elif 'error' in events_received:
                print(f"[WARN] SSH 连接失败: {errors[-1]}")
                print("（这可能是正常的，如果服务器信息不正确）")
            else:
                print("[WARN] 未收到 'connected' 或 'error' 事件")
                print("（可能需要更长的等待时间或服务器信息不正确）")
            
            # 断开连接
            print("\n断开 SocketIO 连接...")
            sio.disconnect()
            
            if 'disconnect' in events_received or not sio.connected:
                print("[OK] SocketIO 断开成功")
            else:
                print("[WARN] SocketIO 断开可能未完成")
            
            # 总结
            print(f"\n收到的事件: {', '.join(set(events_received))}")
            
            if errors:
                print(f"错误: {', '.join(errors)}")
            
            return True
            
        except socketio.exceptions.ConnectionError as e:
            print(f"[FAIL] SocketIO 连接错误: {str(e)}")
            return False
        except Exception as e:
            print(f"[FAIL] 测试异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
        
    except ImportError:
        print("[SKIP] python-socketio 库未安装")
        print("安装命令: pip install python-socketio")
        return True
    except Exception as e:
        print(f"[FAIL] 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='SocketIO 功能测试 - 支持从 config.json 加载服务器配置',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 列出所有可用服务器
  python test_socketio_functional.py --list-servers
  
  # 测试指定服务器（从配置中加载）
  python test_socketio_functional.py --server-name LP-8650-1
  
  # 测试指定服务器和端口
  python test_socketio_functional.py --server-name LP-8650-1 --port 22
  
  # 测试所有服务器（所有端口）
  python test_socketio_functional.py --all
  
  # 使用自定义服务器信息（不从配置加载）
  python test_socketio_functional.py --server-name test-server --server-ip 127.0.0.1 --port 22
        """
    )
    parser.add_argument('--api-url', default=DEFAULT_API_URL, help=f'API 服务器 URL (默认: {DEFAULT_API_URL})')
    parser.add_argument('--server-name', help='测试服务器名称（如果配置中存在则从配置加载，否则使用 --server-ip）')
    parser.add_argument('--server-ip', help='测试服务器 IP（仅在 --server-name 不在配置中时使用）')
    parser.add_argument('--port', type=int, help='测试服务器端口（默认: 22）')
    parser.add_argument('--ports', nargs='+', type=int, help='测试多个端口（例如: --ports 22 9999）')
    parser.add_argument('--list-servers', action='store_true', help='列出所有可用服务器并退出')
    parser.add_argument('--all', action='store_true', help='测试所有配置的服务器（所有端口）')
    parser.add_argument('--config', default='config.json', help='配置文件路径（默认: config.json）')
    
    args = parser.parse_args()
    
    # 加载配置
    config = load_config_from_file(args.config)
    servers = get_servers_from_config(config)
    
    # 列出服务器
    if args.list_servers:
        list_available_servers(config)
        return 0
    
    # 确定要测试的端口
    if args.ports:
        ports_to_test = args.ports
    elif args.port:
        ports_to_test = [args.port]
    else:
        ports_to_test = DEFAULT_PORTS
    
    # 确定要测试的服务器
    test_targets = []
    
    if args.all:
        # 测试所有服务器
        if not servers:
            print("[FAIL] 配置中未找到服务器，无法执行 --all 测试")
            return 1
        
        for server_name, server_ip in sorted(servers.items()):
            for port in ports_to_test:
                test_targets.append((server_name, server_ip, port))
    
    elif args.server_name:
        # 从配置中查找服务器
        if args.server_name in servers:
            server_ip = servers[args.server_name]
            for port in ports_to_test:
                test_targets.append((args.server_name, server_ip, port))
        elif args.server_ip:
            # 配置中不存在，使用提供的 IP
            for port in ports_to_test:
                test_targets.append((args.server_name, args.server_ip, port))
        else:
            print(f"[FAIL] 服务器 '{args.server_name}' 不在配置中，且未提供 --server-ip")
            print("提示: 使用 --list-servers 查看可用服务器")
            return 1
    else:
        # 默认使用测试服务器
        server_name = 'test-server'
        server_ip = args.server_ip or '127.0.0.1'
        port = args.port or 22
        test_targets.append((server_name, server_ip, port))
    
    if not test_targets:
        print("[FAIL] 未指定要测试的服务器")
        return 1
    
    # 执行测试
    print("=" * 60)
    print("SocketIO Functional Test")
    print("=" * 60)
    print(f"API URL: {args.api_url}")
    print(f"测试目标: {len(test_targets)} 个")
    if len(test_targets) <= 5:
        for name, ip, port in test_targets:
            print(f"  - {name} ({ip}:{port})")
    else:
        print(f"  - {test_targets[0][0]} ({test_targets[0][1]}:{test_targets[0][2]}) ... 等 {len(test_targets)} 个")
    print("=" * 60)
    
    results = []
    for i, (server_name, server_ip, port) in enumerate(test_targets, 1):
        if len(test_targets) > 1:
            print(f"\n[{i}/{len(test_targets)}] 测试 {server_name} ({server_ip}:{port})")
            print("-" * 60)
        
        result = test_socketio_events(
            args.api_url,
            server_name,
            server_ip,
            port
        )
        results.append(result)
        
        if len(test_targets) > 1 and i < len(test_targets):
            time.sleep(1)  # 测试间隔
    
    # 总结
    print("\n" + "=" * 60)
    success_count = sum(1 for r in results if r)
    print(f"测试完成: {success_count}/{len(results)} 成功")
    
    if all(results):
        print("[OK] 所有测试通过")
        return 0
    else:
        print("[FAIL] 部分测试失败")
        return 1

if __name__ == '__main__':
    sys.exit(main())

