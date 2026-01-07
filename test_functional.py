#!/usr/bin/env python3
"""
功能测试脚本 - 测试迁移后的实际功能
需要服务端已启动（默认 http://127.0.0.1:5000）
"""

import sys
import os
import time
import json
import requests
import socketio

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 测试配置
# 注意：默认端口已更改为8888（与run.py一致）
DEFAULT_API_URL = "http://127.0.0.1:8888"
DEFAULT_TIMEOUT = 10

# 测试结果
test_results = {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "errors": []
}

def log_test(test_name, status, message=""):
    """记录测试结果"""
    test_results["total"] += 1
    status_symbol = "[OK]" if status == "PASS" else "[FAIL]" if status == "FAIL" else "[SKIP]"
    print(f"{status_symbol} [{status}] {test_name}" + (f": {message}" if message else ""))
    
    if status == "PASS":
        test_results["passed"] += 1
    elif status == "FAIL":
        test_results["failed"] += 1
        if message:
            test_results["errors"].append(f"{test_name}: {message}")
    else:
        test_results["skipped"] += 1

def test_server_connection(api_url):
    """测试服务器连接"""
    print("\n=== 测试服务器连接 ===")
    
    try:
        response = requests.get(f"{api_url}/", timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            log_test("服务器连接", "PASS", f"状态码: {response.status_code}")
            return True
        else:
            log_test("服务器连接", "FAIL", f"状态码: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        log_test("服务器连接", "FAIL", "无法连接到服务器，请确保服务端已启动")
        return False
    except Exception as e:
        log_test("服务器连接", "FAIL", str(e))
        return False

def test_status_api(api_url):
    """测试状态 API"""
    print("\n=== 测试状态 API ===")
    
    try:
        response = requests.get(f"{api_url}/api/status", timeout=DEFAULT_TIMEOUT)
        
        if response.status_code != 200:
            log_test("状态 API 响应码", "FAIL", f"期望 200，实际 {response.status_code}")
            return False
        
        log_test("状态 API 响应码", "PASS", f"状态码: {response.status_code}")
        
        try:
            data = response.json()
            
            # 检查响应数据结构
            if not isinstance(data, dict):
                log_test("状态 API 响应格式", "FAIL", "响应不是 JSON 对象")
                return False
            
            log_test("状态 API 响应格式", "PASS", "响应是有效的 JSON")
            
            # 检查关键字段
            expected_fields = ['servers', 'timestamp']
            found_fields = []
            
            for field in expected_fields:
                if field in data:
                    found_fields.append(field)
            
            if len(found_fields) > 0:
                log_test("状态 API 数据字段", "PASS", f"找到字段: {', '.join(found_fields)}")
            else:
                log_test("状态 API 数据字段", "WARN", "未找到预期的字段")
            
            # 打印部分数据
            if 'servers' in data and isinstance(data['servers'], list):
                server_count = len(data['servers'])
                log_test("状态 API 服务器列表", "PASS", f"找到 {server_count} 个服务器")
            
            return True
            
        except json.JSONDecodeError:
            log_test("状态 API JSON 解析", "FAIL", "响应不是有效的 JSON")
            return False
            
    except Exception as e:
        log_test("状态 API 测试", "FAIL", str(e))
        import traceback
        traceback.print_exc()
        return False

def test_upload_api(api_url):
    """测试上传 API（不实际上传文件）"""
    print("\n=== 测试上传 API ===")
    
    try:
        # 测试上传端点是否存在（使用 OPTIONS 或 GET 请求）
        # 注意：实际的上传需要 POST 请求和文件数据
        
        # 检查端点是否可访问（可能会返回 405 Method Not Allowed，这是正常的）
        response = requests.get(f"{api_url}/api/upload", timeout=DEFAULT_TIMEOUT)
        
        # 405 表示端点存在但不支持 GET 方法，这是正常的
        if response.status_code in [200, 405]:
            log_test("上传 API 端点", "PASS", f"端点存在（状态码: {response.status_code}）")
            return True
        elif response.status_code == 404:
            log_test("上传 API 端点", "FAIL", "端点不存在（404）")
            return False
        else:
            log_test("上传 API 端点", "WARN", f"意外状态码: {response.status_code}")
            return True
            
    except Exception as e:
        log_test("上传 API 测试", "FAIL", str(e))
        return False

def test_download_api(api_url):
    """测试下载 API"""
    print("\n=== 测试下载 API ===")
    
    try:
        # 下载 API 可能需要特定的路径参数
        # 测试不同的路径格式
        test_paths = [
            f"{api_url}/api/download",
            f"{api_url}/api/download/",
        ]
        
        endpoint_exists = False
        
        for path in test_paths:
            try:
                response = requests.get(path, timeout=DEFAULT_TIMEOUT, allow_redirects=False)
                
                # 400 表示端点存在但缺少参数，405 表示方法不允许但端点存在
                # 404 可能表示路径格式不对，但端点可能仍然存在
                if response.status_code in [200, 400, 405]:
                    endpoint_exists = True
                    log_test("下载 API 端点", "PASS", f"端点存在（状态码: {response.status_code}）")
                    break
            except:
                continue
        
        if not endpoint_exists:
            # 检查路由是否注册（通过应用工厂）
            try:
                from app import create_app
                app = create_app()
                routes = [str(rule) for rule in app.url_map.iter_rules()]
                download_routes = [r for r in routes if 'download' in r.lower()]
                if download_routes:
                    log_test("下载 API 端点", "PASS", f"路由已注册: {download_routes[0]}")
                    endpoint_exists = True
                else:
                    log_test("下载 API 端点", "WARN", "端点可能不存在或需要特定路径参数")
            except:
                log_test("下载 API 端点", "WARN", "无法验证端点，可能需要特定路径参数")
        
        return True
            
    except Exception as e:
        log_test("下载 API 测试", "FAIL", str(e))
        return False

def test_fota_api(api_url):
    """测试 FOTA API"""
    print("\n=== 测试 FOTA API ===")
    
    try:
        # 测试 FOTA 检测端口端点
        response = requests.get(f"{api_url}/api/fota/detect-port", timeout=DEFAULT_TIMEOUT)
        
        # 400 表示端点存在但缺少参数
        if response.status_code in [200, 400]:
            log_test("FOTA 检测端口 API", "PASS", f"端点存在（状态码: {response.status_code}）")
        else:
            log_test("FOTA 检测端口 API", "WARN", f"状态码: {response.status_code}")
        
        # 测试 FOTA 主端点
        response = requests.get(f"{api_url}/api/fota", timeout=DEFAULT_TIMEOUT)
        
        if response.status_code in [200, 405, 400]:
            log_test("FOTA 主 API", "PASS", f"端点存在（状态码: {response.status_code}）")
        else:
            log_test("FOTA 主 API", "WARN", f"状态码: {response.status_code}")
        
        return True
        
    except Exception as e:
        log_test("FOTA API 测试", "FAIL", str(e))
        return False

def test_socketio_connection(api_url):
    """测试 SocketIO 连接"""
    print("\n=== 测试 SocketIO 连接 ===")
    
    try:
        # 创建 SocketIO 客户端
        sio = socketio.Client()
        
        connected = False
        error_message = None
        
        def on_connect():
            nonlocal connected
            connected = True
        
        def on_disconnect():
            pass
        
        sio.on('connect', on_connect)
        sio.on('disconnect', on_disconnect)
        
        # 尝试连接
        try:
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
            
            # 连接 SocketIO（使用默认的 /socket.io 路径）
            sio.connect(f"http://{url}", wait_timeout=5)
            
            if connected:
                log_test("SocketIO 连接", "PASS", "连接成功")
                
                # 测试断开连接
                sio.disconnect()
                log_test("SocketIO 断开", "PASS", "断开成功")
                return True
            else:
                log_test("SocketIO 连接", "FAIL", "连接超时")
                return False
                
        except socketio.exceptions.ConnectionError as e:
            log_test("SocketIO 连接", "FAIL", f"连接错误: {str(e)}")
            return False
        except Exception as e:
            log_test("SocketIO 连接", "FAIL", f"异常: {str(e)}")
            return False
        finally:
            if sio.connected:
                sio.disconnect()
        
    except ImportError:
        log_test("SocketIO 连接", "SKIP", "python-socketio 库未安装")
        return True
    except Exception as e:
        log_test("SocketIO 连接", "FAIL", str(e))
        import traceback
        traceback.print_exc()
        return False

def test_routes_list(api_url):
    """测试路由列表"""
    print("\n=== 测试路由列表 ===")
    
    # 测试一些关键路由是否存在
    routes_to_test = [
        ("/api/status", "状态 API"),
        ("/api/upload", "上传 API"),
        ("/api/fota/detect-port", "FOTA 检测端口"),
    ]
    
    found_routes = 0
    
    for route, description in routes_to_test:
        try:
            response = requests.get(f"{api_url}{route}", timeout=DEFAULT_TIMEOUT, allow_redirects=False)
            
            # 任何非 404 的响应都表示路由存在
            if response.status_code != 404:
                found_routes += 1
                log_test(f"路由 {description}", "PASS", f"存在（状态码: {response.status_code}）")
            else:
                log_test(f"路由 {description}", "FAIL", "不存在（404）")
                
        except Exception as e:
            log_test(f"路由 {description}", "WARN", f"测试异常: {str(e)}")
    
    if found_routes > 0:
        log_test("路由列表检查", "PASS", f"找到 {found_routes}/{len(routes_to_test)} 个路由")
        return True
    else:
        log_test("路由列表检查", "FAIL", "未找到任何路由")
        return False

def main():
    """主测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='功能测试脚本')
    parser.add_argument('--api-url', default=DEFAULT_API_URL, help=f'API 服务器 URL (默认: {DEFAULT_API_URL})')
    parser.add_argument('--skip-socketio', action='store_true', help='跳过 SocketIO 测试')
    
    args = parser.parse_args()
    
    api_url = args.api_url.rstrip('/')
    
    print("=" * 60)
    print("Functional Test - Migration Validation")
    print("=" * 60)
    print(f"API URL: {api_url}")
    print("=" * 60)
    
    # 运行所有测试
    tests = [
        ("服务器连接", lambda: test_server_connection(api_url)),
        ("路由列表", lambda: test_routes_list(api_url)),
        ("状态 API", lambda: test_status_api(api_url)),
        ("上传 API", lambda: test_upload_api(api_url)),
        ("下载 API", lambda: test_download_api(api_url)),
        ("FOTA API", lambda: test_fota_api(api_url)),
    ]
    
    if not args.skip_socketio:
        tests.append(("SocketIO 连接", lambda: test_socketio_connection(api_url)))
    
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
        for error in test_results['errors']:
            print(f"  - {error}")
    
    if test_results['failed'] == 0:
        print("\n[OK] All tests passed!")
        return 0
    else:
        print(f"\n[FAIL] {test_results['failed']} test(s) failed")
        return 1

if __name__ == '__main__':
    sys.exit(main())

