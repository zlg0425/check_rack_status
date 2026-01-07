#!/usr/bin/env python3
"""
性能测试脚本 - 测试应用性能指标
需要服务端已启动（默认 http://127.0.0.1:8888）
"""

import sys
import os
import time
import statistics
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
import gc

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 注意：默认端口已更改为8888（与run.py一致）
DEFAULT_API_URL = "http://127.0.0.1:8888"
DEFAULT_TIMEOUT = 30

# 性能指标
performance_metrics = {
    "app_startup_time": None,
    "api_response_times": {},
    "concurrent_performance": {},
    "memory_usage": {},
}

def format_time(seconds):
    """格式化时间"""
    if seconds < 0.001:
        return f"{seconds * 1000000:.2f}μs"
    elif seconds < 1:
        return f"{seconds * 1000:.2f}ms"
    else:
        return f"{seconds:.3f}s"

def test_app_startup_time():
    """测试应用启动时间"""
    print("\n=== 测试应用启动时间 ===")
    
    try:
        # 清理内存
        gc.collect()
        
        startup_times = []
        
        # 多次测试取平均值
        for i in range(5):
            start_time = time.perf_counter()
            
            # 导入并创建应用
            from app import create_app
            app = create_app()
            
            end_time = time.perf_counter()
            startup_time = end_time - start_time
            startup_times.append(startup_time)
            
            # 清理
            del app
            gc.collect()
            
            print(f"  启动 #{i+1}: {format_time(startup_time)}")
        
        avg_time = statistics.mean(startup_times)
        min_time = min(startup_times)
        max_time = max(startup_times)
        std_dev = statistics.stdev(startup_times) if len(startup_times) > 1 else 0
        
        performance_metrics["app_startup_time"] = {
            "avg": avg_time,
            "min": min_time,
            "max": max_time,
            "std_dev": std_dev
        }
        
        print(f"\n平均启动时间: {format_time(avg_time)}")
        print(f"最小启动时间: {format_time(min_time)}")
        print(f"最大启动时间: {format_time(max_time)}")
        print(f"标准差: {format_time(std_dev)}")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] 应用启动时间测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_response_time(api_url, endpoint, method="GET", num_requests=50):
    """测试 API 响应时间"""
    print(f"\n=== 测试 {endpoint} 响应时间 ({num_requests} 次请求) ===")
    
    try:
        response_times = []
        errors = 0
        
        for i in range(num_requests):
            try:
                start_time = time.perf_counter()
                
                if method == "GET":
                    response = requests.get(f"{api_url}{endpoint}", timeout=DEFAULT_TIMEOUT)
                else:
                    response = requests.post(f"{api_url}{endpoint}", timeout=DEFAULT_TIMEOUT)
                
                end_time = time.perf_counter()
                response_time = end_time - start_time
                
                if response.status_code == 200:
                    response_times.append(response_time)
                else:
                    errors += 1
                    
            except Exception as e:
                errors += 1
        
        if len(response_times) == 0:
            print(f"[FAIL] 没有成功的请求")
            return False
        
        avg_time = statistics.mean(response_times)
        min_time = min(response_times)
        max_time = max(response_times)
        median_time = statistics.median(response_times)
        p95_time = statistics.quantiles(response_times, n=20)[18] if len(response_times) > 1 else avg_time
        p99_time = statistics.quantiles(response_times, n=100)[98] if len(response_times) > 1 else avg_time
        
        performance_metrics["api_response_times"][endpoint] = {
            "avg": avg_time,
            "min": min_time,
            "max": max_time,
            "median": median_time,
            "p95": p95_time,
            "p99": p99_time,
            "errors": errors,
            "success_rate": len(response_times) / num_requests * 100
        }
        
        print(f"成功请求: {len(response_times)}/{num_requests} ({len(response_times)/num_requests*100:.1f}%)")
        print(f"平均响应时间: {format_time(avg_time)}")
        print(f"中位数响应时间: {format_time(median_time)}")
        print(f"最小响应时间: {format_time(min_time)}")
        print(f"最大响应时间: {format_time(max_time)}")
        print(f"P95 响应时间: {format_time(p95_time)}")
        print(f"P99 响应时间: {format_time(p99_time)}")
        
        if errors > 0:
            print(f"错误数: {errors}")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] API 响应时间测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_concurrent_performance(api_url, endpoint, num_concurrent=10, num_requests_per_thread=10):
    """测试并发性能"""
    print(f"\n=== 测试并发性能 ({num_concurrent} 并发，每线程 {num_requests_per_thread} 请求) ===")
    
    def make_requests(thread_id):
        response_times = []
        errors = 0
        
        for i in range(num_requests_per_thread):
            try:
                start_time = time.perf_counter()
                response = requests.get(f"{api_url}{endpoint}", timeout=DEFAULT_TIMEOUT)
                end_time = time.perf_counter()
                
                if response.status_code == 200:
                    response_times.append(end_time - start_time)
                else:
                    errors += 1
            except Exception as e:
                errors += 1
        
        return {
            'thread_id': thread_id,
            'response_times': response_times,
            'errors': errors
        }
    
    try:
        start_time = time.perf_counter()
        
        with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
            futures = [executor.submit(make_requests, i) for i in range(num_concurrent)]
            results = [f.result() for f in as_completed(futures)]
        
        end_time = time.perf_counter()
        total_time = end_time - start_time
        
        # 汇总结果
        all_response_times = []
        total_errors = 0
        
        for result in results:
            all_response_times.extend(result['response_times'])
            total_errors += result['errors']
        
        total_requests = num_concurrent * num_requests_per_thread
        successful_requests = len(all_response_times)
        
        if len(all_response_times) == 0:
            print(f"[FAIL] 没有成功的请求")
            return False
        
        avg_time = statistics.mean(all_response_times)
        throughput = successful_requests / total_time
        
        performance_metrics["concurrent_performance"][endpoint] = {
            "total_time": total_time,
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "errors": total_errors,
            "avg_response_time": avg_time,
            "throughput": throughput,
            "requests_per_second": throughput
        }
        
        print(f"总请求数: {total_requests}")
        print(f"成功请求: {successful_requests} ({successful_requests/total_requests*100:.1f}%)")
        print(f"错误数: {total_errors}")
        print(f"总耗时: {format_time(total_time)}")
        print(f"平均响应时间: {format_time(avg_time)}")
        print(f"吞吐量: {throughput:.2f} 请求/秒")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] 并发性能测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_memory_usage():
    """测试内存使用"""
    print("\n=== 测试内存使用 ===")
    
    try:
        process = psutil.Process(os.getpid())
        
        # 获取初始内存
        gc.collect()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # 创建应用
        from app import create_app
        app = create_app()
        
        gc.collect()
        after_app_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # 获取系统内存信息
        system_memory = psutil.virtual_memory()
        
        performance_metrics["memory_usage"] = {
            "initial_mb": initial_memory,
            "after_app_mb": after_app_memory,
            "app_memory_mb": after_app_memory - initial_memory,
            "system_total_gb": system_memory.total / 1024 / 1024 / 1024,
            "system_available_gb": system_memory.available / 1024 / 1024 / 1024,
            "system_percent": system_memory.percent
        }
        
        print(f"初始内存: {initial_memory:.2f} MB")
        print(f"应用后内存: {after_app_memory:.2f} MB")
        print(f"应用占用: {after_app_memory - initial_memory:.2f} MB")
        print(f"系统总内存: {system_memory.total / 1024 / 1024 / 1024:.2f} GB")
        print(f"系统可用内存: {system_memory.available / 1024 / 1024 / 1024:.2f} GB")
        print(f"系统内存使用率: {system_memory.percent:.1f}%")
        
        return True
        
    except ImportError:
        print("[SKIP] psutil 库未安装，跳过内存测试")
        print("安装命令: pip install psutil")
        return True
    except Exception as e:
        print(f"[FAIL] 内存使用测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def print_performance_summary():
    """打印性能摘要"""
    print("\n" + "=" * 60)
    print("Performance Summary")
    print("=" * 60)
    
    if performance_metrics["app_startup_time"]:
        print("\n应用启动时间:")
        startup = performance_metrics["app_startup_time"]
        print(f"  平均: {format_time(startup['avg'])}")
        print(f"  范围: {format_time(startup['min'])} - {format_time(startup['max'])}")
    
    if performance_metrics["api_response_times"]:
        print("\nAPI 响应时间:")
        for endpoint, metrics in performance_metrics["api_response_times"].items():
            print(f"  {endpoint}:")
            print(f"    平均: {format_time(metrics['avg'])}")
            print(f"    中位数: {format_time(metrics['median'])}")
            print(f"    P95: {format_time(metrics['p95'])}")
            print(f"    成功率: {metrics['success_rate']:.1f}%")
    
    if performance_metrics["concurrent_performance"]:
        print("\n并发性能:")
        for endpoint, metrics in performance_metrics["concurrent_performance"].items():
            print(f"  {endpoint}:")
            print(f"    吞吐量: {metrics['throughput']:.2f} 请求/秒")
            print(f"    平均响应时间: {format_time(metrics['avg_response_time'])}")
            print(f"    成功率: {metrics['successful_requests']/metrics['total_requests']*100:.1f}%")
    
    if performance_metrics["memory_usage"]:
        print("\n内存使用:")
        memory = performance_metrics["memory_usage"]
        print(f"  应用占用: {memory.get('app_memory_mb', 0):.2f} MB")
        print(f"  系统内存使用率: {memory.get('system_percent', 0):.1f}%")

def main():
    """主测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='性能测试脚本')
    parser.add_argument('--api-url', default=DEFAULT_API_URL, help=f'API 服务器 URL (默认: {DEFAULT_API_URL})')
    parser.add_argument('--skip-startup', action='store_true', help='跳过启动时间测试')
    parser.add_argument('--skip-memory', action='store_true', help='跳过内存测试')
    parser.add_argument('--requests', type=int, default=50, help='每个 API 的请求数量 (默认: 50)')
    parser.add_argument('--concurrent', type=int, default=10, help='并发数 (默认: 10)')
    
    args = parser.parse_args()
    
    api_url = args.api_url.rstrip('/')
    
    print("=" * 60)
    print("Performance Test Suite")
    print("=" * 60)
    print(f"API URL: {api_url}")
    print(f"Requests per API: {args.requests}")
    print(f"Concurrent: {args.concurrent}")
    print("=" * 60)
    
    # 运行所有测试
    tests = []
    
    if not args.skip_startup:
        tests.append(("应用启动时间", lambda: test_app_startup_time()))
    
    tests.extend([
        ("API 响应时间", lambda: test_api_response_time(api_url, "/api/status", "GET", args.requests)),
        ("并发性能", lambda: test_concurrent_performance(api_url, "/api/status", args.concurrent, args.requests // args.concurrent)),
    ])
    
    if not args.skip_memory:
        tests.append(("内存使用", lambda: test_memory_usage()))
    
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
    
    # 打印性能摘要
    print_performance_summary()
    
    print("\n" + "=" * 60)
    if failed == 0:
        print("[OK] All performance tests completed!")
        return 0
    else:
        print(f"[FAIL] {failed} test(s) failed")
        return 1

if __name__ == '__main__':
    sys.exit(main())

