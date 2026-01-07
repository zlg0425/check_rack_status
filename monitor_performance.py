#!/usr/bin/env python3
"""
性能监控脚本 - 持续监控应用性能指标
支持后台运行、日志记录、告警阈值等功能
"""

import sys
import os
import time
import json
import statistics
import requests
import threading
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
import signal
import argparse

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 默认配置
DEFAULT_API_URL = "http://127.0.0.1:8888"
DEFAULT_INTERVAL = 60  # 默认监控间隔（秒）
DEFAULT_TIMEOUT = 30
DEFAULT_LOG_DIR = "logs"
DEFAULT_METRICS_FILE = "performance_metrics.json"

# 告警阈值（毫秒）
ALERT_THRESHOLDS = {
    "api_response_time_avg": 1000,  # 平均响应时间 > 1秒
    "api_response_time_p95": 2000,  # P95响应时间 > 2秒
    "api_response_time_p99": 5000,  # P99响应时间 > 5秒
    "error_rate": 5.0,  # 错误率 > 5%
    "concurrent_throughput": 100,  # 并发吞吐量 < 100 请求/秒
}

# 监控的API端点
MONITORED_ENDPOINTS = [
    {"path": "/api/status", "method": "GET", "name": "status"},
    # 可以根据需要添加更多端点
]

# 全局变量
running = True
metrics_history = []
metrics_lock = threading.Lock()
log_file = None


def format_time(seconds):
    """格式化时间"""
    if seconds < 0.001:
        return f"{seconds * 1000000:.2f}μs"
    elif seconds < 1:
        return f"{seconds * 1000:.2f}ms"
    else:
        return f"{seconds:.3f}s"


def ensure_log_dir(log_dir):
    """确保日志目录存在"""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return log_dir


def get_log_file_path(log_dir):
    """获取日志文件路径"""
    timestamp = datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir, f"performance_monitor_{timestamp}.log")


def log_message(message, level="INFO"):
    """记录日志消息"""
    global log_file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    
    # 输出到控制台
    print(log_entry)
    
    # 写入日志文件
    if log_file:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
        except Exception as e:
            print(f"写入日志文件失败: {e}")


def check_api_endpoint(api_url, endpoint_info, timeout=DEFAULT_TIMEOUT):
    """检查单个API端点"""
    endpoint_path = endpoint_info["path"]
    method = endpoint_info.get("method", "GET")
    endpoint_name = endpoint_info.get("name", endpoint_path)
    
    response_times = []
    errors = 0
    total_requests = 10  # 每次检查发送10个请求
    
    for i in range(total_requests):
        try:
            start_time = time.perf_counter()
            
            if method == "GET":
                response = requests.get(f"{api_url}{endpoint_path}", timeout=timeout)
            else:
                response = requests.post(f"{api_url}{endpoint_path}", timeout=timeout)
            
            end_time = time.perf_counter()
            response_time = (end_time - start_time) * 1000  # 转换为毫秒
            
            if response.status_code == 200:
                response_times.append(response_time)
            else:
                errors += 1
                log_message(f"{endpoint_name} 请求失败: HTTP {response.status_code}", "WARNING")
                
        except requests.exceptions.Timeout:
            errors += 1
            log_message(f"{endpoint_name} 请求超时", "ERROR")
        except requests.exceptions.ConnectionError:
            errors += 1
            log_message(f"{endpoint_name} 连接错误: 服务器可能未启动", "ERROR")
        except Exception as e:
            errors += 1
            log_message(f"{endpoint_name} 请求异常: {str(e)}", "ERROR")
    
    if len(response_times) == 0:
        return None
    
    return {
        "endpoint": endpoint_name,
        "path": endpoint_path,
        "total_requests": total_requests,
        "successful_requests": len(response_times),
        "errors": errors,
        "error_rate": (errors / total_requests) * 100,
        "response_times": response_times,
        "avg": statistics.mean(response_times),
        "min": min(response_times),
        "max": max(response_times),
        "median": statistics.median(response_times),
        "p95": statistics.quantiles(response_times, n=20)[18] if len(response_times) > 1 else statistics.mean(response_times),
        "p99": statistics.quantiles(response_times, n=100)[98] if len(response_times) > 1 else statistics.mean(response_times),
    }


def check_concurrent_performance(api_url, endpoint_info, num_concurrent=5, num_requests_per_thread=5):
    """检查并发性能"""
    endpoint_path = endpoint_info["path"]
    method = endpoint_info.get("method", "GET")
    endpoint_name = endpoint_info.get("name", endpoint_path)
    
    def make_requests(thread_id):
        response_times = []
        errors = 0
        
        for i in range(num_requests_per_thread):
            try:
                start_time = time.perf_counter()
                
                if method == "GET":
                    response = requests.get(f"{api_url}{endpoint_path}", timeout=DEFAULT_TIMEOUT)
                else:
                    response = requests.post(f"{api_url}{endpoint_path}", timeout=DEFAULT_TIMEOUT)
                
                end_time = time.perf_counter()
                
                if response.status_code == 200:
                    response_times.append((end_time - start_time) * 1000)  # 转换为毫秒
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
            return None
        
        avg_time = statistics.mean(all_response_times)
        throughput = successful_requests / total_time
        
        return {
            "endpoint": endpoint_name,
            "total_time": total_time,
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "errors": total_errors,
            "error_rate": (total_errors / total_requests) * 100,
            "avg_response_time": avg_time,
            "throughput": throughput,
            "requests_per_second": throughput
        }
        
    except Exception as e:
        log_message(f"并发性能测试失败: {str(e)}", "ERROR")
        return None


def get_memory_usage():
    """获取内存使用情况"""
    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        system_memory = psutil.virtual_memory()
        
        return {
            "process_memory_mb": memory_info.rss / 1024 / 1024,
            "process_memory_percent": process.memory_percent(),
            "system_total_gb": system_memory.total / 1024 / 1024 / 1024,
            "system_available_gb": system_memory.available / 1024 / 1024 / 1024,
            "system_used_percent": system_memory.percent,
        }
    except ImportError:
        return None
    except Exception as e:
        log_message(f"获取内存使用失败: {str(e)}", "WARNING")
        return None


def check_alerts(metrics):
    """检查告警阈值"""
    alerts = []
    
    for endpoint_metrics in metrics.get("api_metrics", []):
        endpoint_name = endpoint_metrics.get("endpoint", "unknown")
        
        # 检查平均响应时间
        avg_time = endpoint_metrics.get("avg", 0)
        if avg_time > ALERT_THRESHOLDS["api_response_time_avg"]:
            alerts.append({
                "level": "WARNING",
                "endpoint": endpoint_name,
                "metric": "平均响应时间",
                "value": f"{avg_time:.2f}ms",
                "threshold": f"{ALERT_THRESHOLDS['api_response_time_avg']}ms",
            })
        
        # 检查P95响应时间
        p95_time = endpoint_metrics.get("p95", 0)
        if p95_time > ALERT_THRESHOLDS["api_response_time_p95"]:
            alerts.append({
                "level": "WARNING",
                "endpoint": endpoint_name,
                "metric": "P95响应时间",
                "value": f"{p95_time:.2f}ms",
                "threshold": f"{ALERT_THRESHOLDS['api_response_time_p95']}ms",
            })
        
        # 检查P99响应时间
        p99_time = endpoint_metrics.get("p99", 0)
        if p99_time > ALERT_THRESHOLDS["api_response_time_p99"]:
            alerts.append({
                "level": "CRITICAL",
                "endpoint": endpoint_name,
                "metric": "P99响应时间",
                "value": f"{p99_time:.2f}ms",
                "threshold": f"{ALERT_THRESHOLDS['api_response_time_p99']}ms",
            })
        
        # 检查错误率
        error_rate = endpoint_metrics.get("error_rate", 0)
        if error_rate > ALERT_THRESHOLDS["error_rate"]:
            alerts.append({
                "level": "CRITICAL",
                "endpoint": endpoint_name,
                "metric": "错误率",
                "value": f"{error_rate:.2f}%",
                "threshold": f"{ALERT_THRESHOLDS['error_rate']}%",
            })
    
    # 检查并发性能
    concurrent_metrics = metrics.get("concurrent_metrics")
    if concurrent_metrics:
        throughput = concurrent_metrics.get("throughput", 0)
        if throughput < ALERT_THRESHOLDS["concurrent_throughput"]:
            alerts.append({
                "level": "WARNING",
                "endpoint": concurrent_metrics.get("endpoint", "unknown"),
                "metric": "并发吞吐量",
                "value": f"{throughput:.2f} 请求/秒",
                "threshold": f"{ALERT_THRESHOLDS['concurrent_throughput']} 请求/秒",
            })
    
    return alerts


def save_metrics(metrics, metrics_file):
    """保存性能指标到文件"""
    try:
        # 读取现有指标
        if os.path.exists(metrics_file):
            with open(metrics_file, 'r', encoding='utf-8') as f:
                all_metrics = json.load(f)
        else:
            all_metrics = []
        
        # 添加时间戳
        metrics["timestamp"] = datetime.now().isoformat()
        
        # 添加到历史记录
        all_metrics.append(metrics)
        
        # 只保留最近1000条记录
        if len(all_metrics) > 1000:
            all_metrics = all_metrics[-1000:]
        
        # 保存到文件
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(all_metrics, f, indent=2, ensure_ascii=False)
        
    except Exception as e:
        log_message(f"保存性能指标失败: {str(e)}", "ERROR")


def monitor_cycle(api_url, interval, metrics_file):
    """执行一次监控周期"""
    log_message("=" * 60)
    log_message(f"开始性能监控周期 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    metrics = {
        "timestamp": datetime.now().isoformat(),
        "api_metrics": [],
        "concurrent_metrics": None,
        "memory_metrics": None,
    }
    
    # 检查API端点
    for endpoint_info in MONITORED_ENDPOINTS:
        log_message(f"检查端点: {endpoint_info['path']}")
        endpoint_metrics = check_api_endpoint(api_url, endpoint_info)
        if endpoint_metrics:
            metrics["api_metrics"].append(endpoint_metrics)
            
            # 打印结果
            log_message(f"  {endpoint_metrics['endpoint']}:")
            log_message(f"    成功请求: {endpoint_metrics['successful_requests']}/{endpoint_metrics['total_requests']} ({100 - endpoint_metrics['error_rate']:.1f}%)")
            log_message(f"    平均响应时间: {endpoint_metrics['avg']:.2f}ms")
            log_message(f"    中位数响应时间: {endpoint_metrics['median']:.2f}ms")
            log_message(f"    P95响应时间: {endpoint_metrics['p95']:.2f}ms")
            log_message(f"    P99响应时间: {endpoint_metrics['p99']:.2f}ms")
            if endpoint_metrics['errors'] > 0:
                log_message(f"    错误数: {endpoint_metrics['errors']}", "WARNING")
        else:
            log_message(f"  {endpoint_info['path']}: 检查失败", "ERROR")
    
    # 检查并发性能（只检查第一个端点）
    if MONITORED_ENDPOINTS:
        log_message("检查并发性能")
        concurrent_metrics = check_concurrent_performance(api_url, MONITORED_ENDPOINTS[0])
        if concurrent_metrics:
            metrics["concurrent_metrics"] = concurrent_metrics
            log_message(f"  吞吐量: {concurrent_metrics['throughput']:.2f} 请求/秒")
            log_message(f"  平均响应时间: {concurrent_metrics['avg_response_time']:.2f}ms")
            log_message(f"  成功率: {(1 - concurrent_metrics['error_rate']/100)*100:.1f}%")
    
    # 获取内存使用
    memory_metrics = get_memory_usage()
    if memory_metrics:
        metrics["memory_metrics"] = memory_metrics
        log_message(f"进程内存: {memory_metrics['process_memory_mb']:.2f} MB ({memory_metrics['process_memory_percent']:.2f}%)")
        log_message(f"系统内存使用率: {memory_metrics['system_used_percent']:.1f}%")
    
    # 检查告警
    alerts = check_alerts(metrics)
    if alerts:
        log_message("=" * 60)
        log_message("⚠️  性能告警:", "WARNING")
        for alert in alerts:
            log_message(f"  [{alert['level']}] {alert['endpoint']} - {alert['metric']}: {alert['value']} (阈值: {alert['threshold']})", alert['level'])
        log_message("=" * 60)
    
    # 保存指标
    save_metrics(metrics, metrics_file)
    
    # 保存到历史记录
    with metrics_lock:
        metrics_history.append(metrics)
        if len(metrics_history) > 100:
            metrics_history.pop(0)
    
    log_message(f"监控周期完成，{interval}秒后进行下一次检查")
    log_message("")


def generate_report(metrics_file, output_file=None):
    """生成性能报告"""
    if not os.path.exists(metrics_file):
        print(f"性能指标文件不存在: {metrics_file}")
        return
    
    try:
        with open(metrics_file, 'r', encoding='utf-8') as f:
            all_metrics = json.load(f)
        
        if not all_metrics:
            print("没有性能指标数据")
            return
        
        # 统计最近24小时的数据
        now = datetime.now()
        recent_metrics = []
        for m in all_metrics:
            try:
                metric_time = datetime.fromisoformat(m.get("timestamp", ""))
                if (now - metric_time).total_seconds() < 24 * 3600:
                    recent_metrics.append(m)
            except:
                continue
        
        if not recent_metrics:
            print("最近24小时内没有性能指标数据")
            return
        
        # 生成报告
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("性能监控报告")
        report_lines.append("=" * 80)
        report_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"数据范围: 最近24小时 ({len(recent_metrics)} 条记录)")
        report_lines.append("")
        
        # 统计API性能
        if recent_metrics:
            endpoint_stats = {}
            for m in recent_metrics:
                for api_metric in m.get("api_metrics", []):
                    endpoint = api_metric.get("endpoint", "unknown")
                    if endpoint not in endpoint_stats:
                        endpoint_stats[endpoint] = {
                            "response_times": [],
                            "error_rates": [],
                            "count": 0,
                        }
                    endpoint_stats[endpoint]["response_times"].append(api_metric.get("avg", 0))
                    endpoint_stats[endpoint]["error_rates"].append(api_metric.get("error_rate", 0))
                    endpoint_stats[endpoint]["count"] += 1
            
            report_lines.append("API性能统计:")
            report_lines.append("-" * 80)
            for endpoint, stats in endpoint_stats.items():
                if stats["response_times"]:
                    avg_response = statistics.mean(stats["response_times"])
                    max_response = max(stats["response_times"])
                    avg_error_rate = statistics.mean(stats["error_rates"])
                    report_lines.append(f"  {endpoint}:")
                    report_lines.append(f"    检查次数: {stats['count']}")
                    report_lines.append(f"    平均响应时间: {avg_response:.2f}ms")
                    report_lines.append(f"    最大响应时间: {max_response:.2f}ms")
                    report_lines.append(f"    平均错误率: {avg_error_rate:.2f}%")
                    report_lines.append("")
        
        report_lines.append("=" * 80)
        
        report_text = "\n".join(report_lines)
        
        # 输出到控制台
        print(report_text)
        
        # 保存到文件
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report_text)
            print(f"\n报告已保存到: {output_file}")
        
    except Exception as e:
        print(f"生成报告失败: {str(e)}")
        import traceback
        traceback.print_exc()


def signal_handler(signum, frame):
    """信号处理器"""
    global running
    log_message("收到停止信号，正在退出...", "INFO")
    running = False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='性能监控脚本')
    parser.add_argument('--api-url', default=DEFAULT_API_URL, help=f'API 服务器 URL (默认: {DEFAULT_API_URL})')
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL, help=f'监控间隔（秒）(默认: {DEFAULT_INTERVAL})')
    parser.add_argument('--log-dir', default=DEFAULT_LOG_DIR, help=f'日志目录 (默认: {DEFAULT_LOG_DIR})')
    parser.add_argument('--metrics-file', default=DEFAULT_METRICS_FILE, help=f'性能指标文件 (默认: {DEFAULT_METRICS_FILE})')
    parser.add_argument('--report', action='store_true', help='生成性能报告并退出')
    parser.add_argument('--report-file', help='报告输出文件')
    parser.add_argument('--once', action='store_true', help='只执行一次监控周期')
    
    args = parser.parse_args()
    
    api_url = args.api_url.rstrip('/')
    log_dir = ensure_log_dir(args.log_dir)
    global log_file
    log_file = get_log_file_path(log_dir)
    metrics_file = args.metrics_file
    
    # 如果只是生成报告
    if args.report:
        generate_report(metrics_file, args.report_file)
        return 0
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    log_message("=" * 60)
    log_message("性能监控脚本启动")
    log_message("=" * 60)
    log_message(f"API URL: {api_url}")
    log_message(f"监控间隔: {args.interval}秒")
    log_message(f"日志文件: {log_file}")
    log_message(f"指标文件: {metrics_file}")
    log_message("按 Ctrl+C 停止监控")
    log_message("")
    
    # 执行监控循环
    try:
        if args.once:
            # 只执行一次
            monitor_cycle(api_url, args.interval, metrics_file)
        else:
            # 持续监控
            while running:
                monitor_cycle(api_url, args.interval, metrics_file)
                
                # 等待指定间隔
                for _ in range(args.interval):
                    if not running:
                        break
                    time.sleep(1)
    
    except KeyboardInterrupt:
        log_message("收到中断信号，正在退出...", "INFO")
    except Exception as e:
        log_message(f"监控过程发生异常: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        return 1
    
    log_message("性能监控脚本已停止")
    return 0


if __name__ == '__main__':
    sys.exit(main())

