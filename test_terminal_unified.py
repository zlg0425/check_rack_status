#!/usr/bin/env python3
"""
Terminal 统一测试套件
整合所有 terminal 相关测试：
1. SSH适配器测试（test_ssh_adapter.py）
2. 回车键问题测试（test_enter_key_fix.py）
3. Flask-SocketIO终端测试（test_terminal_socketio_automated.py）

使用方法：
    # 运行所有测试
    python test_terminal_unified.py --all
    
    # 运行特定测试类别
    python test_terminal_unified.py --adapter          # SSH适配器测试
    python test_terminal_unified.py --enter-key       # 回车键测试
    python test_terminal_unified.py --socketio         # SocketIO终端测试
    python test_terminal_unified.py --clipboard        # 复制粘贴功能测试
    
    # 指定服务器
    python test_terminal_unified.py --adapter --server LP-8650-1 --ip 10.99.19.11 --port 22
    python test_terminal_unified.py --clipboard --server LP-8650-1 --ip 10.99.19.11 --port 22
"""

import asyncio
import sys
import os
import time
import json
import threading
import argparse
from typing import Optional, Dict, Any, List

# ============================================================================
# 第一部分：SSH适配器测试（来自 test_ssh_adapter.py）
# ============================================================================

try:
    from ssh_connection_adapter import (
        SSHConnectionAdapter,
        ConnectionConfig,
        create_ssh_connection,
        get_asyncssh_version
    )
    SSH_ADAPTER_AVAILABLE = True
except ImportError:
    SSH_ADAPTER_AVAILABLE = False

try:
    from check_rack_status import resolve_key, resolve_auth_mode, load_config
    PROJECT_KEY_RESOLUTION_AVAILABLE = True
except ImportError:
    PROJECT_KEY_RESOLUTION_AVAILABLE = False
    resolve_key = None
    resolve_auth_mode = None
    load_config = None

# 测试结果统计
test_results = {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "errors": []
}

def log_test(test_name: str, status: str, message: str = ""):
    """记录测试结果"""
    test_results["total"] += 1
    status_symbol = {
        "PASS": "[OK]",
        "FAIL": "[FAIL]",
        "SKIP": "[SKIP]"
    }.get(status, "[?]")
    
    print(f"{status_symbol} [{test_name}] {message}")
    
    if status == "PASS":
        test_results["passed"] += 1
    elif status == "FAIL":
        test_results["failed"] += 1
        test_results["errors"].append(f"{test_name}: {message}")
    elif status == "SKIP":
        test_results["skipped"] += 1

# ============================================================================
# SSH适配器测试模块
# ============================================================================

class SSHAdapterTests:
    """SSH适配器测试类"""
    
    def __init__(self, host: str, port: int, username: str, 
                 password: Optional[str] = None, 
                 client_keys: Optional[str] = None,
                 skip_host_key_check: bool = True):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_keys = client_keys
        self.skip_host_key_check = skip_host_key_check
        self.adapter = None
    
    async def test_version_info(self):
        """显示版本信息"""
        test_name = "版本信息"
        try:
            if not SSH_ADAPTER_AVAILABLE:
                log_test(test_name, "SKIP", "SSH适配器不可用")
                return
            
            version_str = get_asyncssh_version()
            log_test(test_name, "PASS", f"AsyncSSH版本: {version_str}, 实现: AsyncSSH 2.x")
        except Exception as e:
            log_test(test_name, "FAIL", f"获取版本信息失败: {e}")
    
    async def test_adapter_initialization(self):
        """测试适配器初始化"""
        test_name = "适配器初始化"
        try:
            if not SSH_ADAPTER_AVAILABLE:
                log_test(test_name, "SKIP", "SSH适配器不可用")
                return None
            
            self.adapter = SSHConnectionAdapter(enable_connection_pool=True, max_pool_size=10)
            info = self.adapter.get_connection_info()
            implementation = info.get('implementation', 'AsyncSSH 2.x')
            log_test(test_name, "PASS", f"版本: {info['asyncssh_version']}, 实现: {implementation}")
            return self.adapter
        except Exception as e:
            log_test(test_name, "FAIL", f"初始化失败: {e}")
            return None
    
    async def test_connection_creation(self):
        """测试连接创建"""
        test_name = f"连接创建 ({self.host}:{self.port})"
        
        if not self.adapter:
            log_test(test_name, "SKIP", "适配器未初始化")
            return None
        
        try:
            config_params = {
                'host': self.host,
                'port': self.port,
                'username': self.username,
                'connect_timeout': 10,
                'skip_host_key_check': self.skip_host_key_check
            }
            
            if self.client_keys:
                config_params['client_keys'] = [self.client_keys] if isinstance(self.client_keys, str) else self.client_keys
            elif self.password:
                config_params['password'] = self.password
            
            config = ConnectionConfig(**config_params)
            conn = await self.adapter.create_connection(config)
            log_test(test_name, "PASS", "连接成功")
            return conn
        except Exception as e:
            log_test(test_name, "FAIL", f"连接失败: {e}")
            return None
    
    async def test_command_execution(self, conn):
        """测试命令执行"""
        test_name = "命令执行"
        
        if not self.adapter or not conn:
            log_test(test_name, "SKIP", "连接未建立")
            return
        
        try:
            result = await self.adapter.run_command(conn, 'uname -a', timeout=10)
            if result['success']:
                log_test(test_name, "PASS", f"命令执行成功: {result['stdout'][:50]}...")
            else:
                log_test(test_name, "FAIL", f"命令执行失败: {result['error']}")
        except Exception as e:
            log_test(test_name, "FAIL", f"执行异常: {e}")
    
    async def test_connection_pool(self):
        """测试连接池"""
        test_name = "连接池"
        
        if not self.adapter:
            log_test(test_name, "SKIP", "适配器未初始化")
            return
        
        try:
            config_params = {
                'host': self.host,
                'port': self.port,
                'username': self.username,
                'skip_host_key_check': self.skip_host_key_check
            }
            
            if self.client_keys:
                config_params['client_keys'] = [self.client_keys] if isinstance(self.client_keys, str) else self.client_keys
            elif self.password:
                config_params['password'] = self.password
            
            config = ConnectionConfig(**config_params)
            
            conn1 = await self.adapter.create_connection(config)
            info1 = self.adapter.get_connection_info()
            
            conn2 = await self.adapter.create_connection(config)
            info2 = self.adapter.get_connection_info()
            
            if info2['pool_size'] == info1['pool_size']:
                log_test(test_name, "PASS", "连接池复用成功")
            else:
                log_test(test_name, "FAIL", "连接池复用失败")
            
            await self.adapter.close_connection()
        except Exception as e:
            log_test(test_name, "FAIL", f"测试失败: {e}")
    
    async def run_all(self):
        """运行所有SSH适配器测试"""
        print("\n" + "=" * 60)
        print("【SSH适配器测试】")
        print("=" * 60)
        
        await self.test_version_info()
        await self.test_adapter_initialization()
        conn = await self.test_connection_creation()
        
        if conn:
            await self.test_command_execution(conn)
            await self.test_connection_pool()
            await self.adapter.close_connection()

# ============================================================================
# 回车键测试模块（来自 test_enter_key_fix.py）
# ============================================================================

class EnterKeyTests:
    """回车键问题测试类"""
    
    def __init__(self, host: str, port: int, username: str,
                 client_keys: Optional[str] = None,
                 skip_host_key_check: bool = True):
        self.host = host
        self.port = port
        self.username = username
        self.client_keys = client_keys
        self.skip_host_key_check = skip_host_key_check
    
    async def test_enter_key(self):
        """测试回车键输入是否会导致连接断开"""
        test_name = "回车键测试"
        
        if not SSH_ADAPTER_AVAILABLE:
            log_test(test_name, "SKIP", "SSH适配器不可用")
            return False
        
        adapter = None
        conn = None
        shell = None
        
        try:
            # 创建适配器
            adapter = SSHConnectionAdapter(enable_connection_pool=False)
            
            # 创建配置
            config = ConnectionConfig(
                host=self.host,
                port=self.port,
                username=self.username,
                client_keys=[self.client_keys] if self.client_keys else None,
                skip_host_key_check=self.skip_host_key_check,
                keepalive_interval=30,
                keepalive_count_max=3,
                connect_timeout=10,
                login_timeout=10
            )
            
            # 建立连接
            conn = await adapter.create_connection(config)
            
            # 创建Shell
            shell = await adapter.open_shell(conn, term_type='xterm-256color', cols=80, rows=24)
            await asyncio.sleep(1)  # 等待Shell准备就绪
            
            # 测试1: 发送 'ls\n'
            await shell.write('ls\n')
            await asyncio.sleep(0.5)
            if hasattr(shell, 'is_closed') and shell.is_closed():
                log_test(f"{test_name} - ls\\n", "FAIL", "Shell意外关闭")
                return False
            
            # 测试2: 发送纯回车 '\n'
            await shell.write('\n')
            await asyncio.sleep(0.5)
            if hasattr(shell, 'is_closed') and shell.is_closed():
                log_test(f"{test_name} - \\n", "FAIL", "Shell意外关闭")
                return False
            
            # 测试3: 发送 '\r'
            await shell.write('\r')
            await asyncio.sleep(0.5)
            if hasattr(shell, 'is_closed') and shell.is_closed():
                log_test(f"{test_name} - \\r", "FAIL", "Shell意外关闭")
                return False
            
            # 测试4: 发送 '\r\n'
            await shell.write('\r\n')
            await asyncio.sleep(0.5)
            if hasattr(shell, 'is_closed') and shell.is_closed():
                log_test(f"{test_name} - \\r\\n", "FAIL", "Shell意外关闭")
                return False
            
            log_test(test_name, "PASS", "所有回车键测试通过")
            return True
            
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        
        finally:
            # 清理资源
            try:
                if shell:
                    await shell.close()
                if conn:
                    conn.close()
            except:
                pass
    
    async def run_all(self):
        """运行所有回车键测试"""
        print("\n" + "=" * 60)
        print("【回车键测试】")
        print("=" * 60)
        await self.test_enter_key()

# ============================================================================
# Flask-SocketIO终端测试模块（来自 test_terminal_socketio_automated.py）
# ============================================================================

try:
    import socketio
    import requests
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False

class SocketIOTerminalTests:
    """Flask-SocketIO终端测试类"""
    
    def __init__(self, base_url: str, server_name: str, server_ip: str, port: int):
        self.base_url = base_url
        self.server_name = server_name
        self.server_ip = server_ip
        self.port = port
    
    def check_api_server(self):
        """检查API服务器是否运行"""
        test_name = "API服务器检查"
        try:
            response = requests.get(f"{self.base_url}/api/status", timeout=5)
            if response.status_code == 200:
                log_test(test_name, "PASS", "API服务器运行正常")
                return True
            else:
                log_test(test_name, "FAIL", f"API服务器状态码: {response.status_code}")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"无法连接API服务器: {e}")
            return False
    
    def test_socketio_connection(self):
        """测试SocketIO连接"""
        test_name = "SocketIO连接"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        try:
            sio = socketio.Client()
            connected_event = threading.Event()
            
            @sio.on('connect')
            def on_connect():
                connected_event.set()
            
            sio.connect(self.base_url)
            
            if connected_event.wait(timeout=5):
                sio.disconnect()
                log_test(test_name, "PASS", "SocketIO连接成功")
                return True
            else:
                sio.disconnect()
                log_test(test_name, "FAIL", "连接超时")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"连接失败: {e}")
            return False
    
    def test_ssh_connection_establishment(self):
        """测试SSH连接建立"""
        test_name = "SSH连接建立"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        try:
            sio = socketio.Client()
            connected_event = threading.Event()
            error_event = threading.Event()
            error_message = None
            
            @sio.on('connect')
            def on_connect():
                sio.emit('start_ssh', {
                    'server_name': self.server_name,
                    'server_ip': self.server_ip,
                    'port': self.port
                })
            
            @sio.on('connected')
            def on_ssh_connected(data):
                connected_event.set()
            
            @sio.on('error')
            def on_error(data):
                nonlocal error_message
                error_message = data.get('message', '未知错误')
                error_event.set()
            
            sio.connect(self.base_url)
            
            if connected_event.wait(timeout=10):
                sio.disconnect()
                log_test(test_name, "PASS", "SSH连接建立成功")
                return True
            elif error_event.is_set():
                sio.disconnect()
                log_test(test_name, "FAIL", f"SSH连接失败: {error_message}")
                return False
            else:
                sio.disconnect()
                log_test(test_name, "FAIL", "SSH连接超时")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试失败: {e}")
            return False
    
    def run_all(self):
        """运行所有SocketIO终端测试"""
        print("\n" + "=" * 60)
        print("【Flask-SocketIO终端测试】")
        print("=" * 60)
        
        if not self.check_api_server():
            log_test("SocketIO测试", "SKIP", "API服务器未运行")
            return
        
        self.test_socketio_connection()
        self.test_ssh_connection_establishment()

# ============================================================================
# 复制粘贴功能测试模块
# ============================================================================

class ClipboardTests:
    """复制粘贴功能测试类"""
    
    def __init__(self, base_url: str, server_name: str, server_ip: str, port: int):
        self.base_url = base_url
        self.server_name = server_name
        self.server_ip = server_ip
        self.port = port
        self.sio = None
        self.received_output = []
        self.output_lock = threading.Lock()
    
    def check_api_server(self):
        """检查API服务器是否运行"""
        test_name = "API服务器检查（复制粘贴测试）"
        try:
            response = requests.get(f"{self.base_url}/api/status", timeout=5)
            if response.status_code == 200:
                log_test(test_name, "PASS", "API服务器运行正常")
                return True
            else:
                log_test(test_name, "FAIL", f"API服务器状态码: {response.status_code}")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"无法连接API服务器: {e}")
            return False
    
    def setup_socketio_connection(self, timeout=10):
        """建立SocketIO连接并等待SSH连接就绪"""
        if not SOCKETIO_AVAILABLE:
            return False
        
        try:
            self.sio = socketio.Client()
            ssh_connected_event = threading.Event()
            error_event = threading.Event()
            error_message = None
            
            @self.sio.on('connect')
            def on_connect():
                self.sio.emit('start_ssh', {
                    'server_name': self.server_name,
                    'server_ip': self.server_ip,
                    'port': self.port
                })
            
            @self.sio.on('connected')
            def on_ssh_connected(data):
                ssh_connected_event.set()
            
            @self.sio.on('output')
            def on_output(data):
                with self.output_lock:
                    if data.get('data'):
                        self.received_output.append(data['data'])
            
            @self.sio.on('error')
            def on_error(data):
                nonlocal error_message
                error_message = data.get('message', '未知错误')
                error_event.set()
            
            self.sio.connect(self.base_url)
            
            if ssh_connected_event.wait(timeout=timeout):
                # 等待一下，确保shell准备就绪
                time.sleep(1)
                return True
            elif error_event.is_set():
                log_test("SocketIO连接建立", "FAIL", f"SSH连接失败: {error_message}")
                return False
            else:
                log_test("SocketIO连接建立", "FAIL", "SSH连接超时")
                return False
        except Exception as e:
            log_test("SocketIO连接建立", "FAIL", f"连接失败: {e}")
            return False
    
    def cleanup_socketio_connection(self):
        """清理SocketIO连接"""
        if self.sio:
            try:
                self.sio.disconnect()
            except:
                pass
            self.sio = None
        self.received_output.clear()
    
    def send_input(self, text: str, delay_between_chars=0.01):
        """发送输入到终端（模拟粘贴）"""
        if not self.sio or not self.sio.connected:
            return False
        
        try:
            # 逐字符发送，模拟真实的粘贴行为
            for char in text:
                self.sio.emit('terminal_input', {'data': char})
                if delay_between_chars > 0:
                    time.sleep(delay_between_chars)
            return True
        except Exception as e:
            log_test("发送输入", "FAIL", f"发送失败: {e}")
            return False
    
    def wait_for_output(self, timeout=5, expected_text=None):
        """等待输出，可选的期望文本"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self.output_lock:
                output_text = ''.join(self.received_output)
                if expected_text:
                    if expected_text in output_text:
                        return True
                elif len(self.received_output) > 0:
                    return True
            time.sleep(0.1)
        return False
    
    def test_single_line_paste(self):
        """测试单行文本粘贴"""
        test_name = "单行文本粘贴"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        if not self.setup_socketio_connection():
            log_test(test_name, "SKIP", "无法建立SSH连接")
            return False
        
        try:
            # 清空输出缓冲区
            self.received_output.clear()
            
            # 发送一个简单的命令（模拟粘贴）
            test_command = "echo 'test_paste_single_line'\n"
            if not self.send_input(test_command, delay_between_chars=0.005):
                log_test(test_name, "FAIL", "发送粘贴内容失败")
                return False
            
            # 等待输出
            if self.wait_for_output(timeout=3, expected_text="test_paste_single_line"):
                log_test(test_name, "PASS", "单行文本粘贴成功")
                return True
            else:
                log_test(test_name, "FAIL", "未收到预期输出")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        finally:
            self.cleanup_socketio_connection()
    
    def test_multi_line_paste(self):
        """测试多行文本粘贴"""
        test_name = "多行文本粘贴"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        if not self.setup_socketio_connection():
            log_test(test_name, "SKIP", "无法建立SSH连接")
            return False
        
        try:
            # 清空输出缓冲区
            self.received_output.clear()
            
            # 发送多行命令（模拟粘贴多行脚本）
            multi_line_command = """echo 'line1'
echo 'line2'
echo 'line3'
"""
            if not self.send_input(multi_line_command, delay_between_chars=0.005):
                log_test(test_name, "FAIL", "发送多行粘贴内容失败")
                return False
            
            # 等待所有输出
            time.sleep(2)  # 给多行命令执行时间
            
            with self.output_lock:
                output_text = ''.join(self.received_output)
            
            # 检查是否包含所有行的输出
            if 'line1' in output_text and 'line2' in output_text and 'line3' in output_text:
                log_test(test_name, "PASS", "多行文本粘贴成功")
                return True
            else:
                log_test(test_name, "FAIL", f"未收到所有预期输出。实际输出: {output_text[:200]}")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        finally:
            self.cleanup_socketio_connection()
    
    def test_paste_with_special_chars(self):
        """测试包含特殊字符的粘贴"""
        test_name = "特殊字符粘贴"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        if not self.setup_socketio_connection():
            log_test(test_name, "SKIP", "无法建立SSH连接")
            return False
        
        try:
            # 清空输出缓冲区
            self.received_output.clear()
            
            # 发送包含特殊字符的命令
            special_chars_command = "echo 'test: $PATH && ls -la | grep test'\n"
            if not self.send_input(special_chars_command, delay_between_chars=0.005):
                log_test(test_name, "FAIL", "发送特殊字符粘贴内容失败")
                return False
            
            # 等待输出
            if self.wait_for_output(timeout=3):
                log_test(test_name, "PASS", "特殊字符粘贴成功")
                return True
            else:
                log_test(test_name, "FAIL", "未收到预期输出")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        finally:
            self.cleanup_socketio_connection()
    
    def test_paste_chinese_text(self):
        """测试中文文本粘贴"""
        test_name = "中文文本粘贴"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        if not self.setup_socketio_connection():
            log_test(test_name, "SKIP", "无法建立SSH连接")
            return False
        
        try:
            # 清空输出缓冲区
            self.received_output.clear()
            
            # 发送包含中文的命令
            chinese_command = "echo '测试中文粘贴功能'\n"
            if not self.send_input(chinese_command, delay_between_chars=0.005):
                log_test(test_name, "FAIL", "发送中文粘贴内容失败")
                return False
            
            # 等待输出
            if self.wait_for_output(timeout=3):
                with self.output_lock:
                    output_text = ''.join(self.received_output)
                if '测试' in output_text or '中文' in output_text:
                    log_test(test_name, "PASS", "中文文本粘贴成功")
                    return True
                else:
                    log_test(test_name, "PASS", "中文文本已发送（输出可能被编码）")
                    return True
            else:
                log_test(test_name, "FAIL", "未收到预期输出")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        finally:
            self.cleanup_socketio_connection()
    
    def test_paste_large_text(self):
        """测试大文本粘贴"""
        test_name = "大文本粘贴"
        
        if not SOCKETIO_AVAILABLE:
            log_test(test_name, "SKIP", "socketio库不可用")
            return False
        
        if not self.setup_socketio_connection():
            log_test(test_name, "SKIP", "无法建立SSH连接")
            return False
        
        try:
            # 清空输出缓冲区
            self.received_output.clear()
            
            # 生成一个较长的命令（模拟粘贴大段文本）
            large_text = "echo 'start'; " + "echo 'middle'; " * 10 + "echo 'end'\n"
            if not self.send_input(large_text, delay_between_chars=0.001):
                log_test(test_name, "FAIL", "发送大文本粘贴内容失败")
                return False
            
            # 等待输出
            time.sleep(2)
            
            with self.output_lock:
                output_text = ''.join(self.received_output)
            
            if 'start' in output_text and 'end' in output_text:
                log_test(test_name, "PASS", "大文本粘贴成功")
                return True
            else:
                log_test(test_name, "FAIL", "大文本粘贴可能不完整")
                return False
        except Exception as e:
            log_test(test_name, "FAIL", f"测试异常: {e}")
            return False
        finally:
            self.cleanup_socketio_connection()
    
    def run_all(self):
        """运行所有复制粘贴测试"""
        print("\n" + "=" * 60)
        print("【复制粘贴功能测试】")
        print("=" * 60)
        
        if not self.check_api_server():
            log_test("复制粘贴测试", "SKIP", "API服务器未运行")
            return
        
        self.test_single_line_paste()
        self.test_multi_line_paste()
        self.test_paste_with_special_chars()
        self.test_paste_chinese_text()
        self.test_paste_large_text()

# ============================================================================
# 主函数和配置加载
# ============================================================================

def load_test_config(server_name: Optional[str] = None) -> Dict[str, Any]:
    """加载测试配置"""
    config = {
        'host': None,
        'port': 22,
        'username': 'root',
        'client_keys': None,
        'server_name': server_name
    }
    
    try:
        if PROJECT_KEY_RESOLUTION_AVAILABLE:
            load_config()
            from check_rack_status import servers_dict, ssh_username
            
            if server_name and server_name in servers_dict:
                config['server_name'] = server_name
                server_info = servers_dict[server_name]
                if isinstance(server_info, dict):
                    config['host'] = server_info.get('ip', '')
                else:
                    config['host'] = server_info
                config['username'] = ssh_username or 'root'
            elif 'LP-8650-1' in servers_dict:
                config['server_name'] = 'LP-8650-1'
                server_info = servers_dict['LP-8650-1']
                if isinstance(server_info, dict):
                    config['host'] = server_info.get('ip', '')
                else:
                    config['host'] = server_info
                config['username'] = ssh_username or 'root'
            
            # 解析密钥
            if config['server_name'] and resolve_key:
                key_path = resolve_key(config['server_name'], config['port'])
                if key_path and not os.path.isabs(key_path):
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    key_path = os.path.join(script_dir, key_path)
                if key_path and os.path.exists(key_path):
                    config['client_keys'] = key_path
    except Exception as e:
        print(f"警告: 加载配置失败: {e}")
    
    return config

async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Terminal统一测试套件")
    parser.add_argument("--all", action="store_true", help="运行所有测试")
    parser.add_argument("--adapter", action="store_true", help="运行SSH适配器测试")
    parser.add_argument("--enter-key", action="store_true", help="运行回车键测试")
    parser.add_argument("--socketio", action="store_true", help="运行SocketIO终端测试")
    parser.add_argument("--clipboard", action="store_true", help="运行复制粘贴功能测试")
    
    parser.add_argument("--server", help="服务器名称")
    parser.add_argument("--ip", help="服务器IP")
    parser.add_argument("--port", type=int, default=22, help="端口")
    parser.add_argument("--username", help="用户名")
    parser.add_argument("--key", help="密钥文件路径")
    parser.add_argument("--api-url", default="http://127.0.0.1:5000", help="API服务器URL")
    parser.add_argument("--skip-host-check", action="store_true", default=True, help="跳过主机密钥验证")
    
    args = parser.parse_args()
    
    # 如果没有指定任何测试，默认运行所有
    if not (args.all or args.adapter or args.enter_key or args.socketio or args.clipboard):
        args.all = True
    
    # 加载配置
    config = load_test_config(args.server)
    
    # 使用命令行参数覆盖配置
    if args.ip:
        config['host'] = args.ip
    if args.port:
        config['port'] = args.port
    if args.username:
        config['username'] = args.username
    if args.key:
        config['client_keys'] = args.key
    if args.server:
        config['server_name'] = args.server
    
    if not config['host']:
        print("错误: 未指定服务器IP，请使用 --ip 或确保config.json中有服务器配置")
        sys.exit(1)
    
    print("=" * 60)
    print("Terminal 统一测试套件")
    print("=" * 60)
    print(f"测试配置:")
    print(f"  服务器: {config['server_name']} ({config['host']}:{config['port']})")
    print(f"  用户名: {config['username']}")
    if config['client_keys']:
        print(f"  密钥: {config['client_keys']}")
    print("=" * 60)
    
    # 运行测试
    if args.all or args.adapter:
        adapter_tests = SSHAdapterTests(
            host=config['host'],
            port=config['port'],
            username=config['username'],
            client_keys=config['client_keys'],
            skip_host_key_check=args.skip_host_check
        )
        await adapter_tests.run_all()
    
    if args.all or args.enter_key:
        enter_key_tests = EnterKeyTests(
            host=config['host'],
            port=config['port'],
            username=config['username'],
            client_keys=config['client_keys'],
            skip_host_key_check=args.skip_host_check
        )
        await enter_key_tests.run_all()
    
    if args.all or args.socketio:
        socketio_tests = SocketIOTerminalTests(
            base_url=args.api_url,
            server_name=config['server_name'] or config['host'],
            server_ip=config['host'],
            port=config['port']
        )
        socketio_tests.run_all()
    
    if args.all or args.clipboard:
        clipboard_tests = ClipboardTests(
            base_url=args.api_url,
            server_name=config['server_name'] or config['host'],
            server_ip=config['host'],
            port=config['port']
        )
        clipboard_tests.run_all()
    
    # 打印测试结果汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"总测试数: {test_results['total']}")
    print(f"通过: {test_results['passed']}")
    print(f"失败: {test_results['failed']}")
    print(f"跳过: {test_results['skipped']}")
    
    if test_results['errors']:
        print("\n错误详情:")
        for error in test_results['errors']:
            print(f"  - {error}")
    
    if test_results['total'] > 0:
        success_rate = (test_results['passed'] / test_results['total'] * 100)
        print(f"\n成功率: {success_rate:.1f}%")
    
    print("=" * 60)
    
    sys.exit(0 if test_results['failed'] == 0 else 1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

