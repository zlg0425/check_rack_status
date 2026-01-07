#!/usr/bin/env python3
"""
集成测试脚本 - 测试完整的业务流程
需要服务端已启动（默认 http://127.0.0.1:8888）
"""

import sys
import os
import time
import json
import requests
import threading
import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 注意：默认端口已更改为8888（与run.py一致）
DEFAULT_API_URL = "http://127.0.0.1:8888"
DEFAULT_TIMEOUT = 30

# 测试结果
test_results = {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "errors": []
}

def log_test(test_name, status, message="", duration=None):
    """记录测试结果"""
    test_results["total"] += 1
    status_symbol = "[OK]" if status == "PASS" else "[FAIL]" if status == "FAIL" else "[SKIP]"
    duration_str = f" ({duration:.2f}s)" if duration else ""
    print(f"{status_symbol} [{status}] {test_name}{duration_str}" + (f": {message}" if message else ""))
    
    if status == "PASS":
        test_results["passed"] += 1
    elif status == "FAIL":
        test_results["failed"] += 1
        if message:
            test_results["errors"].append(f"{test_name}: {message}")
    else:
        test_results["skipped"] += 1

def test_status_workflow(api_url):
    """测试状态查询完整流程"""
    print("\n=== 测试状态查询流程 ===")
    start_time = time.time()
    
    try:
        # 1. 获取状态
        response = requests.get(f"{api_url}/api/status", timeout=DEFAULT_TIMEOUT)
        
        if response.status_code != 200:
            log_test("状态查询流程", "FAIL", f"状态码: {response.status_code}", time.time() - start_time)
            return False
        
        data = response.json()
        
        if not isinstance(data, dict) or 'servers' not in data:
            log_test("状态查询流程", "FAIL", "响应数据格式错误", time.time() - start_time)
            return False
        
        servers = data.get('servers', [])
        
        if len(servers) == 0:
            log_test("状态查询流程", "SKIP", "没有可用的服务器", time.time() - start_time)
            return True
        
        # 2. 验证服务器数据结构
        server = servers[0]
        required_fields = ['server_name', 'server_ip']
        missing_fields = [f for f in required_fields if f not in server]
        
        if missing_fields:
            log_test("状态查询流程", "FAIL", f"缺少字段: {missing_fields}", time.time() - start_time)
            return False
        
        duration = time.time() - start_time
        log_test("状态查询流程", "PASS", f"获取 {len(servers)} 个服务器信息", duration)
        return True
        
    except Exception as e:
        log_test("状态查询流程", "FAIL", str(e), time.time() - start_time)
        return False

def test_concurrent_status_requests(api_url, num_requests=10):
    """测试并发状态请求"""
    print(f"\n=== 测试并发状态请求 ({num_requests} 个请求) ===")
    start_time = time.time()
    
    def make_request(index):
        try:
            response = requests.get(f"{api_url}/api/status", timeout=DEFAULT_TIMEOUT)
            return {
                'index': index,
                'status_code': response.status_code,
                'success': response.status_code == 200,
                'duration': response.elapsed.total_seconds()
            }
        except Exception as e:
            return {
                'index': index,
                'success': False,
                'error': str(e)
            }
    
    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]
        
        successful = sum(1 for r in results if r.get('success', False))
        failed = num_requests - successful
        
        if failed > 0:
            errors = [r.get('error', 'Unknown') for r in results if not r.get('success', False)]
            log_test("并发状态请求", "FAIL", f"{failed}/{num_requests} 失败: {errors[:3]}", time.time() - start_time)
            return False
        
        durations = [r.get('duration', 0) for r in results if r.get('success', False)]
        avg_duration = sum(durations) / len(durations) if durations else 0
        max_duration = max(durations) if durations else 0
        
        duration = time.time() - start_time
        log_test("并发状态请求", "PASS", 
                f"全部成功，平均响应时间: {avg_duration:.3f}s，最大: {max_duration:.3f}s", 
                duration)
        return True
        
    except Exception as e:
        log_test("并发状态请求", "FAIL", str(e), time.time() - start_time)
        return False

def test_socketio_multiple_connections(api_url, num_connections=5):
    """测试多个 SocketIO 连接"""
    print(f"\n=== 测试多个 SocketIO 连接 ({num_connections} 个连接) ===")
    start_time = time.time()
    
    def create_connection(index):
        try:
            sio = socketio.Client()
            connected = False
            
            def on_connect():
                nonlocal connected
                connected = True
            
            sio.on('connect', on_connect)
            
            # 从 HTTP URL 提取主机和端口
            if api_url.startswith('http://'):
                url = api_url.replace('http://', '')
            elif api_url.startswith('https://'):
                url = api_url.replace('https://', '')
            else:
                url = api_url
            
            if '/' in url:
                url = url.split('/')[0]
            
            sio.connect(f"http://{url}", wait_timeout=5)
            
            if connected:
                time.sleep(0.5)  # 保持连接一段时间
                sio.disconnect()
                return {'index': index, 'success': True}
            else:
                return {'index': index, 'success': False, 'error': 'Connection timeout'}
                
        except Exception as e:
            return {'index': index, 'success': False, 'error': str(e)}
    
    try:
        with ThreadPoolExecutor(max_workers=num_connections) as executor:
            futures = [executor.submit(create_connection, i) for i in range(num_connections)]
            results = [f.result() for f in as_completed(futures)]
        
        successful = sum(1 for r in results if r.get('success', False))
        failed = num_connections - successful
        
        if failed > 0:
            errors = [r.get('error', 'Unknown') for r in results if not r.get('success', False)]
            log_test("多个 SocketIO 连接", "FAIL", f"{failed}/{num_connections} 失败: {errors[:3]}", time.time() - start_time)
            return False
        
        duration = time.time() - start_time
        log_test("多个 SocketIO 连接", "PASS", f"全部 {num_connections} 个连接成功", duration)
        return True
        
    except ImportError:
        log_test("多个 SocketIO 连接", "SKIP", "python-socketio 库未安装", time.time() - start_time)
        return True
    except Exception as e:
        log_test("多个 SocketIO 连接", "FAIL", str(e), time.time() - start_time)
        return False

def test_api_response_times(api_url):
    """测试 API 响应时间"""
    print("\n=== 测试 API 响应时间 ===")
    
    endpoints = [
        ("/api/status", "GET"),
    ]
    
    results = []
    
    for endpoint, method in endpoints:
        try:
            start_time = time.time()
            
            if method == "GET":
                response = requests.get(f"{api_url}{endpoint}", timeout=DEFAULT_TIMEOUT)
            else:
                response = requests.post(f"{api_url}{endpoint}", timeout=DEFAULT_TIMEOUT)
            
            duration = time.time() - start_time
            
            if response.status_code == 200:
                log_test(f"API 响应时间 {endpoint}", "PASS", f"{duration:.3f}s")
                results.append((endpoint, duration, True))
            else:
                log_test(f"API 响应时间 {endpoint}", "WARN", f"{duration:.3f}s (状态码: {response.status_code})")
                results.append((endpoint, duration, False))
                
        except Exception as e:
            log_test(f"API 响应时间 {endpoint}", "FAIL", str(e))
            results.append((endpoint, None, False))
    
    # 计算平均响应时间
    successful_results = [r for r in results if r[2] and r[1] is not None]
    if successful_results:
        avg_time = sum(r[1] for r in successful_results) / len(successful_results)
        log_test("API 平均响应时间", "PASS", f"{avg_time:.3f}s")
    
    return len(successful_results) > 0

def test_workflow_integration(api_url):
    """测试工作流集成"""
    print("\n=== 测试工作流集成 ===")
    start_time = time.time()
    
    try:
        # 1. 获取状态
        response = requests.get(f"{api_url}/api/status", timeout=DEFAULT_TIMEOUT)
        
        if response.status_code != 200:
            log_test("工作流集成", "FAIL", "状态查询失败", time.time() - start_time)
            return False
        
        data = response.json()
        servers = data.get('servers', [])
        
        if len(servers) == 0:
            log_test("工作流集成", "SKIP", "没有可用的服务器", time.time() - start_time)
            return True
        
        # 2. 验证服务器数据完整性
        server = servers[0]
        has_required_fields = all(field in server for field in ['server_name', 'server_ip'])
        
        if not has_required_fields:
            log_test("工作流集成", "FAIL", "服务器数据不完整", time.time() - start_time)
            return False
        
        # 3. 测试 SocketIO 连接（如果服务器信息可用）
        try:
            sio = socketio.Client()
            
            if api_url.startswith('http://'):
                url = api_url.replace('http://', '')
            elif api_url.startswith('https://'):
                url = api_url.replace('https://', '')
            else:
                url = api_url
            
            if '/' in url:
                url = url.split('/')[0]
            
            sio.connect(f"http://{url}", wait_timeout=3)
            
            if sio.connected:
                sio.disconnect()
                log_test("工作流集成", "PASS", "状态查询 + SocketIO 连接成功", time.time() - start_time)
                return True
            else:
                log_test("工作流集成", "WARN", "SocketIO 连接失败", time.time() - start_time)
                return True  # SocketIO 连接失败不影响整体流程
                
        except ImportError:
            log_test("工作流集成", "SKIP", "python-socketio 未安装", time.time() - start_time)
            return True
        except Exception:
            log_test("工作流集成", "WARN", "SocketIO 连接异常", time.time() - start_time)
            return True  # SocketIO 连接异常不影响整体流程
        
    except Exception as e:
        log_test("工作流集成", "FAIL", str(e), time.time() - start_time)
        return False

def main():
    """主测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='集成测试脚本')
    parser.add_argument('--api-url', default=DEFAULT_API_URL, help=f'API 服务器 URL (默认: {DEFAULT_API_URL})')
    parser.add_argument('--concurrent-requests', type=int, default=10, help='并发请求数量 (默认: 10)')
    parser.add_argument('--socketio-connections', type=int, default=5, help='SocketIO 连接数量 (默认: 5)')
    
    args = parser.parse_args()
    
    api_url = args.api_url.rstrip('/')
    
    print("=" * 60)
    print("Integration Test Suite")
    print("=" * 60)
    print(f"API URL: {api_url}")
    print(f"Concurrent Requests: {args.concurrent_requests}")
    print(f"SocketIO Connections: {args.socketio_connections}")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("状态查询流程", lambda: test_status_workflow(api_url)),
        ("并发状态请求", lambda: test_concurrent_status_requests(api_url, args.concurrent_requests)),
        ("多个 SocketIO 连接", lambda: test_socketio_multiple_connections(api_url, args.socketio_connections)),
        ("API 响应时间", lambda: test_api_response_times(api_url)),
        ("工作流集成", lambda: test_workflow_integration(api_url)),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] {test_name} 测试异常: {str(e)}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    # 打印测试结果摘要
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Total: {test_results['total']}")
    print(f"Passed: {test_results['passed']}")
    print(f"Failed: {test_results['failed']}")
    print(f"Skipped: {test_results['skipped']}")
    
    if test_results['errors']:
        print("\nErrors:")
        for error in test_results['errors'][:5]:  # 只显示前5个错误
            print(f"  - {error}")
        if len(test_results['errors']) > 5:
            print(f"  ... 还有 {len(test_results['errors']) - 5} 个错误")
    
    if test_results['failed'] == 0:
        print("\n[OK] All integration tests passed!")
        return 0
    else:
        print(f"\n[FAIL] {test_results['failed']} test(s) failed")
        return 1

if __name__ == '__main__':
    sys.exit(main())

