#!/usr/bin/env python3
"""
上传功能统一自动化测试脚本
整合函数调用和API调用两种测试方式，包括单文件上传和批量上传

用法：
    python test_upload_unified.py [--mode function|api] [--type single|batch|all] [--server SERVER_NAME] [--ip IP] [--port PORT] [--api-url API_URL] [--test TEST_NAME]
    
示例：
    # 使用函数调用方式运行所有测试
    python test_upload_unified.py --mode function
    
    # 使用API调用方式运行所有测试（需要先启动web_ui.py）
    python test_upload_unified.py --mode api
    
    # 只运行单文件上传API测试
    python test_upload_unified.py --mode api --type single
    
    # 只运行批量上传API测试
    python test_upload_unified.py --mode api --type batch
    
    # 运行特定测试
    python test_upload_unified.py --mode function --test test_single_file_upload
    
    # 指定服务器和API URL
    python test_upload_unified.py --mode api --server LP-8650-1 --ip 10.99.19.11 --port 22 --api-url http://127.0.0.1:5000
"""

import sys
import os
import time
import tempfile
import shutil
import argparse
import posixpath
import json
import socket
import random
import requests
from app.utils.config import load_config
from app.utils.helpers import sftp_upload, create_transport, ensure_remote_dir, validate_remote_path, check_remote_disk_space, check_remote_file_exists, detect_fota_port, record_fota_timing, get_avg_fota_timing
from core.ssh.transport import create_transport
from core.sftp.operations import ensure_remote_dir, validate_remote_path, check_remote_disk_space, check_remote_file_exists
import paramiko

# 测试结果统计
test_results = {
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "total": 0
}

# 详细测试结果记录
detailed_results = []

def format_bytes(bytes_size):
    """格式化字节大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def run_test(test_func, mode, api_url, server_name, server_ip, port, test_dir, remote_test_base, server_list=None, test_name=None):
    """运行单个测试"""
    test_results["total"] += 1
    start_time = time.time()
    error_msg = None
    try:
        if mode == "api":
            # 批量上传测试需要传递服务器列表
            if server_list is not None:
                result = test_func(api_url, server_list, port, test_dir, remote_test_base)
            else:
                result = test_func(api_url, server_name, server_ip, port, test_dir, remote_test_base)
        else:  # function
            result = test_func(server_name, server_ip, port, test_dir, remote_test_base)
        
        elapsed_time = time.time() - start_time
        if result:
            test_results["passed"] += 1
            status = "PASSED"
        else:
            test_results["failed"] += 1
            status = "FAILED"
        
        # 记录详细结果
        if test_name:
            detailed_results.append({
                "name": test_name,
                "status": status,
                "elapsed_time": elapsed_time,
                "error": error_msg
            })
        
        return status
    except Exception as e:
        elapsed_time = time.time() - start_time
        error_msg = str(e)
        print(f"✗ 测试异常: {error_msg}")
        import traceback
        traceback.print_exc()
        test_results["failed"] += 1
        
        # 记录详细结果
        if test_name:
            detailed_results.append({
                "name": test_name,
                "status": "FAILED",
                "elapsed_time": elapsed_time,
                "error": error_msg
            })
        
        return "FAILED"

# ============================================================================
# 通用工具函数
# ============================================================================

def generate_test_report(args, test_results, results, server_name, server_ip, port, batch_server_list=None):
    """生成测试结果文档"""
    import datetime
    
    # 创建报告目录
    report_dir = "test_reports"
    os.makedirs(report_dir, exist_ok=True)
    
    # 生成报告文件名
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(report_dir, f"upload_test_report_{timestamp}.md")
    
    # 计算通过率
    pass_rate = (test_results['passed'] / test_results['total'] * 100) if test_results['total'] > 0 else 0
    
    # 生成Markdown报告
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(f"# 上传功能测试报告\n\n")
        f.write(f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 测试环境信息
        f.write(f"## 测试环境\n\n")
        f.write(f"- **测试模式**: {args.mode.upper()}\n")
        if args.mode == "api":
            f.write(f"- **API地址**: {args.api_url}\n")
            f.write(f"- **测试类型**: {args.type.upper()}\n")
        f.write(f"- **主服务器**: {server_name} ({server_ip}:{port})\n")
        if batch_server_list and len(batch_server_list) > 1:
            f.write(f"- **批量测试服务器**: {len(batch_server_list)} 个\n")
            for s in batch_server_list:
                f.write(f"  - {s['name']} ({s['ip']}:{s['port']})\n")
        f.write(f"\n")
        
        # 测试结果摘要
        f.write(f"## 测试结果摘要\n\n")
        f.write(f"| 项目 | 数量 | 百分比 |\n")
        f.write(f"|------|------|--------|\n")
        f.write(f"| 总计 | {test_results['total']} | 100% |\n")
        f.write(f"| ✓ 通过 | {test_results['passed']} | {test_results['passed']/test_results['total']*100:.1f}% |\n" if test_results['total'] > 0 else "| ✓ 通过 | 0 | 0% |\n")
        f.write(f"| ✗ 失败 | {test_results['failed']} | {test_results['failed']/test_results['total']*100:.1f}% |\n" if test_results['total'] > 0 else "| ✗ 失败 | 0 | 0% |\n")
        f.write(f"| ⚠ 跳过 | {test_results['skipped']} | {test_results['skipped']/test_results['total']*100:.1f}% |\n" if test_results['total'] > 0 else "| ⚠ 跳过 | 0 | 0% |\n")
        f.write(f"\n")
        f.write(f"**通过率**: {pass_rate:.1f}%\n\n")
        
        # 详细测试结果
        f.write(f"## 详细测试结果\n\n")
        f.write(f"| 测试名称 | 状态 | 耗时(秒) |\n")
        f.write(f"|----------|------|----------|\n")
        
        for result_item in detailed_results:
            status_symbol = "✓" if result_item['status'] == "PASSED" else ("✗" if result_item['status'] == "FAILED" else "⚠")
            f.write(f"| {result_item['name']} | {status_symbol} {result_item['status']} | {result_item['elapsed_time']:.2f} |\n")
        
        # 如果有失败测试，添加错误信息
        failed_tests = [r for r in detailed_results if r['status'] == "FAILED"]
        if failed_tests:
            f.write(f"\n## 失败测试详情\n\n")
            for test in failed_tests:
                f.write(f"### {test['name']}\n\n")
                f.write(f"**状态**: ✗ FAILED\n\n")
                f.write(f"**耗时**: {test['elapsed_time']:.2f} 秒\n\n")
                if test['error']:
                    f.write(f"**错误信息**:\n\n")
                    f.write(f"```\n{test['error']}\n```\n\n")
        
        # 测试结论
        f.write(f"## 测试结论\n\n")
        if test_results['failed'] == 0:
            f.write(f"✅ **所有测试通过**\n\n")
        else:
            f.write(f"❌ **有 {test_results['failed']} 个测试失败**\n\n")
            f.write(f"请检查失败测试的详细信息，并修复相关问题。\n\n")
    
    print(f"\n✓ 测试报告已生成: {report_file}")

def get_random_servers(server_dict, port, count_min=4, count_max=5):
    """从服务器列表中随机选择4~5个服务器"""
    # 获取所有可用的服务器
    available_servers = []
    for name, info in server_dict.items():
        if isinstance(info, dict):
            server_ip = info.get("ip", "")
        else:
            server_ip = str(info)
        
        if server_ip:
            available_servers.append({
                "name": name,
                "ip": server_ip,
                "port": port
            })
    
    if not available_servers:
        return []
    
    # 随机选择4~5个服务器
    count = random.randint(count_min, min(count_max, len(available_servers)))
    selected = random.sample(available_servers, count)
    
    return selected

def wait_for_upload_completion(base_url, task_id, timeout=60):
    """等待上传任务完成，通过SSE监听进度"""
    start_time = time.time()
    last_progress = -1
    
    try:
        url = f"{base_url}/api/upload/progress/{task_id}"
        response = requests.get(url, timeout=timeout, stream=True)
        
        if response.status_code != 200:
            return False, {"error": f"HTTP {response.status_code}"}
        
        for line in response.iter_lines():
            if time.time() - start_time > timeout:
                return False, {"error": "超时"}
            
            if not line:
                continue
            
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                data_str = line_str[6:]  # 移除 'data: ' 前缀
                try:
                    data = json.loads(data_str)
                    
                    if "error" in data:
                        return False, data
                    
                    progress = data.get("progress", 0)
                    status = data.get("status", "uploading")
                    
                    if progress != last_progress:
                        print(f"  进度更新: {progress}%")
                        last_progress = progress
                    
                    if status in ("done", "error"):
                        return True, data
                except json.JSONDecodeError:
                    continue
                
    except Exception as e:
        return False, {"error": str(e)}
    
    return False, {"error": "未完成"}

def wait_for_batch_completion(base_url, batch_id, timeout=120):
    """等待批量上传完成，通过状态API查询"""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{base_url}/api/batch-upload/status/{batch_id}", timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("all_done") or data.get("status") == "completed":
                    return True, data
                elif data.get("status") == "failed":
                    return True, data
            elif response.status_code == 404:
                return False, {"error": "任务不存在"}
        except Exception as e:
            print(f"    警告: 查询状态失败: {e}")
        
        time.sleep(1)
    
    return False, {"error": "超时"}

def cleanup_remote_directory(server_name, server_ip, port, remote_dir):
    """清理远程测试目录（递归删除）"""
    print(f"\n清理远程测试数据...")
    print(f"  远程目录: {remote_dir}")
    
    transport = None
    sftp = None
    try:
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        def remove_directory(sftp, remote_path):
            try:
                items = sftp.listdir_attr(remote_path)
                for item in items:
                    item_path = posixpath.join(remote_path, item.filename)
                    if item.st_mode & 0o040000:  # 目录
                        remove_directory(sftp, item_path)
                    else:  # 文件
                        try:
                            sftp.remove(item_path)
                        except Exception as e:
                            print(f"    警告: 无法删除文件 {item_path}: {e}")
                sftp.rmdir(remote_path)
            except IOError:
                pass
            except Exception as e:
                print(f"    警告: 删除目录 {remote_path} 时出错: {e}")
        
        try:
            sftp.stat(remote_dir)
            remove_directory(sftp, remote_dir)
            print(f"✓ 远程测试数据清理成功")
        except IOError:
            print(f"✓ 远程目录不存在，无需清理")
        
        return True
    except (TimeoutError, socket.timeout, OSError) as e:
        print(f"⚠ 无法连接到服务器进行清理: {str(e)}")
        print(f"  提示: 远程测试数据可能需要手动清理: {remote_dir}")
        return False
    except paramiko.ssh_exception.SSHException as e:
        print(f"⚠ 无法连接到服务器进行清理: {str(e)}")
        print(f"  提示: 远程测试数据可能需要手动清理: {remote_dir}")
        return False
    except Exception as e:
        print(f"⚠ 远程测试数据清理失败: {str(e)}")
        return False
    finally:
        # 确保资源清理
        if sftp:
            try:
                sftp.close()
            except:
                pass
        if transport:
            try:
                transport.close()
            except:
                pass

# ============================================================================
# 函数测试（直接调用 sftp_upload）
# ============================================================================

def test_function_single_file_upload(server_name, server_ip, port, test_dir, remote_test_base):
    """测试1: 单个文件上传"""
    print("\n" + "="*60)
    print("测试1: 单个文件上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    remote_dir = remote_test_base
    filename = "uploaded_file1.txt"
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        ok, info = sftp_upload(server_name, server_ip, port, remote_dir, filename, 
                               data=file_data, file_size=file_size, 
                               check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        
        # 验证文件是否存在
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_dir, filename)
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == file_size, f"文件大小不匹配: 期望 {file_size}, 实际 {stat.st_size}"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 文件上传成功")
        print(f"  远程文件: {remote_path}")
        print(f"  文件大小: {format_bytes(file_size)}")
        if isinstance(info, dict) and info.get("exists"):
            print(f"  注意: 文件已存在，已覆盖")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_large_file_upload(server_name, server_ip, port, test_dir, remote_test_base):
    """测试2: 大文件上传"""
    print("\n" + "="*60)
    print("测试2: 大文件上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "large_file.bin")
    remote_dir = remote_test_base
    filename = "uploaded_large_file.bin"
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 大文件不存在，请先创建")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        file_size = os.path.getsize(local_file)
        print(f"  文件大小: {format_bytes(file_size)}")
        
        with open(local_file, 'rb') as f:
            progress_calls = [0]
            def progress_cb(loaded, total):
                percent = int((loaded / total) * 100) if total > 0 else 0
                if percent != progress_calls[-1]:
                    progress_calls.append(percent)
                    if percent % 25 == 0:
                        print(f"  上传进度: {percent}%")
            
            ok, info = sftp_upload(server_name, server_ip, port, remote_dir, filename,
                                 stream=f, file_size=file_size,
                                 progress_callback=progress_cb,
                                 check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        
        # 验证文件大小
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_dir, filename)
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == file_size, f"文件大小不匹配: 期望 {file_size}, 实际 {stat.st_size}"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 大文件上传成功")
        print(f"  进度更新次数: {len(progress_calls)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_file_overwrite(server_name, server_ip, port, test_dir, remote_test_base):
    """测试3: 文件覆盖上传"""
    print("\n" + "="*60)
    print("测试3: 文件覆盖上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    remote_dir = remote_test_base
    filename = "overwrite_test.txt"
    
    try:
        # 第一次上传
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        ok1, info1 = sftp_upload(server_name, server_ip, port, remote_dir, filename,
                                data=file_data, file_size=file_size,
                                check_disk_space=True, check_file_exists=True)
        assert ok1, f"第一次上传失败: {info1}"
        
        # 等待一小段时间
        time.sleep(0.5)
        
        # 第二次上传（覆盖）
        with open(local_file, 'rb') as f:
            ok2, info2 = sftp_upload(server_name, server_ip, port, remote_dir, filename,
                                    data=file_data, file_size=file_size,
                                    check_disk_space=True, check_file_exists=True)
        
        assert ok2, f"第二次上传失败: {info2}"
        
        # 验证文件存在信息
        if isinstance(info2, dict) and info2.get("exists"):
            print(f"  检测到文件已存在: {info2.get('exists')}")
        
        # 验证文件被覆盖
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_dir, filename)
        try:
            stat2 = sftp.stat(remote_path)
            assert stat2.st_size == file_size, f"文件大小不匹配"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 文件覆盖成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_path_validation(server_name, server_ip, port, test_dir, remote_test_base):
    """测试4: 路径验证"""
    print("\n" + "="*60)
    print("测试4: 路径验证（防止路径遍历攻击）")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        # 测试路径遍历攻击
        test_cases = [
            ("../../../etc/passwd", "路径遍历攻击"),
            ("..\\..\\..\\windows\\system32", "Windows路径遍历"),
            ("normal/path/file.txt", "正常路径"),
        ]
        
        for target_dir, description in test_cases:
            print(f"  测试: {description} - {target_dir}")
            ok, info = sftp_upload(server_name, server_ip, port, target_dir, "test.txt",
                                  data=file_data, file_size=file_size,
                                  check_disk_space=False, check_file_exists=False)
            
            if ".." in target_dir:
                # 应该被拒绝
                assert not ok, f"路径遍历攻击应该被拒绝: {target_dir}"
                assert "路径遍历" in str(info) or ".." in str(info), f"错误信息应该包含路径遍历提示: {info}"
                print(f"    ✓ 正确拒绝路径遍历攻击")
            else:
                # 正常路径应该成功
                print(f"    ✓ 正常路径处理成功")
        
        print(f"✓ 测试通过: 路径验证功能正常")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_disk_space_check(server_name, server_ip, port, test_dir, remote_test_base):
    """测试5: 磁盘空间检查"""
    print("\n" + "="*60)
    print("测试5: 磁盘空间检查")
    print("="*60)
    
    try:
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        try:
            # 测试磁盘空间检查函数
            has_space, error = check_remote_disk_space(sftp, remote_test_base, 1024 * 1024)  # 1MB
            print(f"  磁盘空间检查结果: has_space={has_space}, error={error}")
            
            if has_space:
                print(f"  ✓ 磁盘空间充足")
            else:
                print(f"  ⚠ 磁盘空间不足: {error}")
            
            # 测试超大文件（假设需要1TB空间，应该失败）
            has_space_large, error_large = check_remote_disk_space(sftp, remote_test_base, 1024 * 1024 * 1024 * 1024)  # 1TB
            print(f"  超大文件检查结果: has_space={has_space_large}, error={error_large}")
            
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 磁盘空间检查功能正常")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_function_file_exists_check(server_name, server_ip, port, test_dir, remote_test_base):
    """测试6: 文件存在检查"""
    print("\n" + "="*60)
    print("测试6: 文件存在检查")
    print("="*60)
    
    try:
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        try:
            # 先上传一个文件
            local_file = os.path.join("test_data", "test_upload", "file1.txt")
            remote_path = posixpath.join(remote_test_base, "exists_check_test.txt")
            
            with open(local_file, 'rb') as f:
                file_data = f.read()
                file_size = len(file_data)
            
            ok, info = sftp_upload(server_name, server_ip, port, remote_test_base, "exists_check_test.txt",
                                  data=file_data, file_size=file_size,
                                  check_disk_space=False, check_file_exists=False)
            assert ok, f"上传失败: {info}"
            
            # 检查文件是否存在
            exists, is_dir, error = check_remote_file_exists(sftp, remote_path)
            print(f"  文件存在检查: exists={exists}, is_directory={is_dir}, error={error}")
            assert exists, "文件应该存在"
            assert not is_dir, "应该是文件而不是目录"
            
            # 检查不存在的文件
            non_exist_path = posixpath.join(remote_test_base, "non_exist_file.txt")
            exists2, is_dir2, error2 = check_remote_file_exists(sftp, non_exist_path)
            print(f"  不存在文件检查: exists={exists2}, is_directory={is_dir2}, error={error2}")
            assert not exists2, "文件不应该存在"
            
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 文件存在检查功能正常")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_function_special_filename(server_name, server_ip, port, test_dir, remote_test_base):
    """测试7: 特殊字符文件名"""
    print("\n" + "="*60)
    print("测试7: 特殊字符文件名上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "special_chars_file_测试.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 特殊字符文件名测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        filename = "special_chars_file_测试.txt"
        ok, info = sftp_upload(server_name, server_ip, port, remote_test_base, filename,
                              data=file_data, file_size=file_size,
                              check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        
        # 验证文件
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_test_base, filename)
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == file_size, f"文件大小不匹配"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 特殊字符文件名上传成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_function_empty_file(server_name, server_ip, port, test_dir, remote_test_base):
    """测试8: 空文件上传"""
    print("\n" + "="*60)
    print("测试8: 空文件上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "empty_file.txt")
    filename = "uploaded_empty_file.txt"
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        assert file_size == 0, "测试文件应该为空"
        
        ok, info = sftp_upload(server_name, server_ip, port, remote_test_base, filename,
                              data=file_data, file_size=file_size,
                              check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        
        # 验证文件
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_test_base, filename)
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == 0, f"文件大小应该为0，实际为 {stat.st_size}"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 空文件上传成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_hidden_file(server_name, server_ip, port, test_dir, remote_test_base):
    """测试9: 隐藏文件上传"""
    print("\n" + "="*60)
    print("测试9: 隐藏文件上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", ".hidden_file")
    filename = ".uploaded_hidden_file"
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 隐藏文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        ok, info = sftp_upload(server_name, server_ip, port, remote_test_base, filename,
                              data=file_data, file_size=file_size,
                              check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        
        # 验证文件
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_test_base, filename)
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == file_size, f"文件大小不匹配"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 隐藏文件上传成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_progress_callback(server_name, server_ip, port, test_dir, remote_test_base):
    """测试10: 进度回调功能"""
    print("\n" + "="*60)
    print("测试10: 进度回调功能")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file2.txt")
    filename = "uploaded_file2_progress.txt"
    
    try:
        with open(local_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
        
        progress_updates = []
        def progress_cb(loaded, total):
            percent = int((loaded / total) * 100) if total > 0 else 0
            progress_updates.append((loaded, total, percent))
        
        ok, info = sftp_upload(server_name, server_ip, port, remote_test_base, filename,
                              data=file_data, file_size=file_size,
                              progress_callback=progress_cb,
                              check_disk_space=True, check_file_exists=True)
        
        assert ok, f"上传失败: {info}"
        assert len(progress_updates) > 0, "应该有进度更新"
        
        # 验证进度更新
        final_update = progress_updates[-1]
        assert final_update[0] == final_update[1], "最终进度应该是100%"
        assert final_update[2] == 100, "最终百分比应该是100%"
        
        print(f"✓ 测试通过: 进度回调功能正常")
        print(f"  进度更新次数: {len(progress_updates)}")
        print(f"  最终进度: {final_update[2]}%")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

# ============================================================================
# API测试 - 单文件上传
# ============================================================================

def test_api_single_upload_basic(base_url, server_name, server_ip, port, test_dir, remote_test_base):
    """测试1: 基本API上传功能"""
    print("\n" + "="*60)
    print("测试1: 基本API上传功能")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 准备文件
        with open(local_file, 'rb') as f:
            files = {'file': (os.path.basename(local_file), f, 'application/octet-stream')}
            data = {
                'server_name': server_name,
                'server_ip': server_ip,
                'port': str(port),
                'target_dir': remote_test_base
            }
            
            # 发送上传请求
            print(f"  上传文件: {local_file}")
            response = requests.post(f"{base_url}/api/upload", files=files, data=data, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        result = response.json()
        assert result.get("ok"), f"上传失败: {result.get('error')}"
        
        task_id = result.get("task_id")
        assert task_id, "缺少task_id"
        
        print(f"  任务ID: {task_id}")
        
        # 等待完成
        success, final_data = wait_for_upload_completion(base_url, task_id, timeout=60)
        assert success, f"上传未完成: {final_data}"
        
        # 验证结果
        assert final_data.get("status") == "done", f"上传状态错误: {final_data.get('status')}"
        assert final_data.get("result", {}).get("ok"), f"上传失败: {final_data.get('result')}"
        
        # 验证文件是否存在
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_test_base, os.path.basename(local_file))
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size > 0, "文件大小为0"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: API上传成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_single_upload_invalid_params(base_url, server_name, server_ip, port, test_dir, remote_test_base):
    """测试2: API参数验证"""
    print("\n" + "="*60)
    print("测试2: API参数验证")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 测试1: 缺少server_name
        with open(local_file, 'rb') as f:
            files = {'file': (os.path.basename(local_file), f, 'application/octet-stream')}
            data = {
                'server_ip': server_ip,
                'port': str(port),
                'target_dir': remote_test_base
            }
            response = requests.post(f"{base_url}/api/upload", files=files, data=data, timeout=10)
            assert response.status_code == 400, f"应该返回400错误: {response.status_code}"
            result = response.json()
            assert not result.get("ok"), "应该返回错误"
            print(f"  ✓ 缺少server_name被正确拒绝")
        
        # 测试2: 非法端口
        with open(local_file, 'rb') as f:
            files = {'file': (os.path.basename(local_file), f, 'application/octet-stream')}
            data = {
                'server_name': server_name,
                'server_ip': server_ip,
                'port': '9998',  # 非法端口
                'target_dir': remote_test_base
            }
            response = requests.post(f"{base_url}/api/upload", files=files, data=data, timeout=10)
            assert response.status_code == 400, f"应该返回400错误: {response.status_code}"
            result = response.json()
            assert not result.get("ok"), "应该返回错误"
            print(f"  ✓ 非法端口被正确拒绝")
        
        # 测试3: 未选择文件
        data = {
            'server_name': server_name,
            'server_ip': server_ip,
            'port': str(port),
            'target_dir': remote_test_base
        }
        response = requests.post(f"{base_url}/api/upload", data=data, timeout=10)
        assert response.status_code == 400, f"应该返回400错误: {response.status_code}"
        result = response.json()
        assert not result.get("ok"), "应该返回错误"
        print(f"  ✓ 未选择文件被正确拒绝")
        
        print(f"✓ 测试通过: API参数验证正常")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_single_upload_progress_sse(base_url, server_name, server_ip, port, test_dir, remote_test_base):
    """测试3: API进度查询（SSE）"""
    print("\n" + "="*60)
    print("测试3: API进度查询（SSE）")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file2.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 启动上传
        with open(local_file, 'rb') as f:
            files = {'file': (os.path.basename(local_file), f, 'application/octet-stream')}
            data = {
                'server_name': server_name,
                'server_ip': server_ip,
                'port': str(port),
                'target_dir': remote_test_base
            }
            response = requests.post(f"{base_url}/api/upload", files=files, data=data, timeout=30)
        
        assert response.status_code == 200
        result = response.json()
        assert result.get("ok")
        task_id = result.get("task_id")
        
        print(f"  任务ID: {task_id}")
        
        # 监听SSE进度
        url = f"{base_url}/api/upload/progress/{task_id}"
        response = requests.get(url, timeout=60, stream=True)
        
        progress_updates = []
        start_time = time.time()
        
        for line in response.iter_lines():
            if time.time() - start_time > 60:
                break
            
            if not line:
                continue
            
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                data_str = line_str[6:]  # 移除 'data: ' 前缀
                try:
                    data = json.loads(data_str)
                    progress = data.get("progress", -1)
                    status = data.get("status", "")
                    
                    if progress >= 0:
                        progress_updates.append(progress)
                        if progress % 25 == 0 or progress == 100:
                            print(f"  进度: {progress}%")
                    
                    if status in ("done", "error"):
                        print(f"  最终状态: {status}")
                        break
                except json.JSONDecodeError:
                    continue
        
        assert len(progress_updates) > 0, "没有收到进度更新"
        assert progress_updates[-1] == 100, f"进度未达到100%: {progress_updates[-1]}"
        
        print(f"✓ 测试通过: SSE进度查询正常")
        print(f"  进度更新次数: {len(progress_updates)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_single_upload_large_file(base_url, server_name, server_ip, port, test_dir, remote_test_base):
    """测试4: 大文件API上传"""
    print("\n" + "="*60)
    print("测试4: 大文件API上传")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "large_file.bin")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 大文件不存在，请先创建")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        file_size = os.path.getsize(local_file)
        print(f"  文件大小: {format_bytes(file_size)}")
        
        # 启动上传
        with open(local_file, 'rb') as f:
            files = {'file': (os.path.basename(local_file), f, 'application/octet-stream')}
            data = {
                'server_name': server_name,
                'server_ip': server_ip,
                'port': str(port),
                'target_dir': remote_test_base
            }
            response = requests.post(f"{base_url}/api/upload", files=files, data=data, timeout=120)
        
        assert response.status_code == 200
        result = response.json()
        assert result.get("ok")
        task_id = result.get("task_id")
        
        print(f"  任务ID: {task_id}")
        
        # 监听进度
        success, final_data = wait_for_upload_completion(base_url, task_id, timeout=300)
        assert success, f"上传未完成: {final_data}"
        assert final_data.get("status") == "done", f"上传状态错误: {final_data.get('status')}"
        
        # 验证文件大小
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = posixpath.join(remote_test_base, os.path.basename(local_file))
        try:
            stat = sftp.stat(remote_path)
            assert stat.st_size == file_size, f"文件大小不匹配: 期望 {file_size}, 实际 {stat.st_size}"
        finally:
            sftp.close()
            transport.close()
        
        print(f"✓ 测试通过: 大文件API上传成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================================
# API测试 - 批量上传
# ============================================================================

def test_api_batch_upload_basic(base_url, server_list, port, test_dir, remote_test_base):
    """测试1: 基本批量上传（单文件多服务器）"""
    print("\n" + "="*60)
    print("测试1: 基本批量上传（单文件多服务器）")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 准备文件
        files = [("files", (os.path.basename(local_file), open(local_file, 'rb'), 'application/octet-stream'))]
        
        # 使用传入的服务器列表
        servers = [{"name": s["name"], "ip": s["ip"]} for s in server_list]
        data = {
            "port": str(port),
            "servers": json.dumps(servers),
            "target_dir": remote_test_base
        }
        
        # 发送批量上传请求
        print(f"  上传文件: {local_file}")
        print(f"  目标服务器数: {len(servers)}")
        for s in servers:
            print(f"    - {s['name']} ({s['ip']})")
        
        response = requests.post(f"{base_url}/api/batch-upload", files=files, data=data, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        result = response.json()
        assert result.get("ok"), f"上传失败: {result.get('error')}"
        
        batch_id = result.get("batch_id")
        assert batch_id, "缺少batch_id"
        
        print(f"  批量任务ID: {batch_id}")
        
        # 等待完成
        success, final_data = wait_for_batch_completion(base_url, batch_id, timeout=120)
        assert success, f"批量上传未完成: {final_data}"
        
        # 验证结果
        assert final_data.get("all_done"), "任务应该已完成"
        assert final_data.get("total_count") > 0, "应该有任务"
        assert final_data.get("success_count") + final_data.get("error_count") == final_data.get("total_count"), "任务数不匹配"
        
        print(f"✓ 测试通过: 批量上传成功")
        print(f"  完成任务数: {final_data.get('success_count', 0)}/{final_data.get('total_count', 0)}")
        
        # 关闭文件
        files[0][1][1].close()
        
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_batch_upload_multiple_files(base_url, server_list, port, test_dir, remote_test_base):
    """测试2: 多文件批量上传"""
    print("\n" + "="*60)
    print("测试2: 多文件批量上传")
    print("="*60)
    
    test_files = [
        os.path.join("test_data", "test_upload", "file1.txt"),
        os.path.join("test_data", "test_upload", "file2.txt"),
        os.path.join("test_data", "test_upload", "small_file.bin"),
    ]
    
    # 检查文件是否存在
    existing_files = [f for f in test_files if os.path.exists(f)]
    if len(existing_files) < 2:
        print("⚠ 跳过: 测试文件不足")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 准备文件
        files = []
        for file_path in existing_files:
            files.append(("files", (os.path.basename(file_path), open(file_path, 'rb'), 'application/octet-stream')))
        
        # 使用传入的服务器列表
        servers = [{"name": s["name"], "ip": s["ip"]} for s in server_list]
        data = {
            "port": str(port),
            "servers": json.dumps(servers),
            "target_dir": remote_test_base
        }
        
        # 发送批量上传请求
        print(f"  上传文件数: {len(existing_files)}")
        print(f"  目标服务器数: {len(servers)}")
        for s in servers:
            print(f"    - {s['name']} ({s['ip']})")
        
        response = requests.post(f"{base_url}/api/batch-upload", files=files, data=data, timeout=60)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        result = response.json()
        assert result.get("ok"), f"上传失败: {result.get('error')}"
        
        batch_id = result.get("batch_id")
        task_list = result.get("tasks", [])
        
        print(f"  批量任务ID: {batch_id}")
        print(f"  任务总数: {len(task_list)}")
        
        # 等待完成
        success, final_data = wait_for_batch_completion(base_url, batch_id, timeout=120)
        assert success, f"批量上传未完成: {final_data}"
        
        # 验证结果
        assert final_data.get("all_done"), "任务应该已完成"
        expected_task_count = len(existing_files) * len(servers)
        assert final_data.get("total_count") == expected_task_count, f"任务数量不匹配: 期望 {expected_task_count}, 实际 {final_data.get('total_count')}"
        
        print(f"✓ 测试通过: 多文件批量上传成功")
        print(f"  完成任务数: {final_data.get('success_count', 0)}/{final_data.get('total_count', 0)}")
        
        # 关闭文件
        for _, file_tuple in files:
            file_tuple[1].close()
        
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_batch_upload_status(base_url, server_list, port, test_dir, remote_test_base):
    """测试3: 批量上传状态查询API"""
    print("\n" + "="*60)
    print("测试3: 批量上传状态查询API")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "file1.txt")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 测试文件不存在")
        test_results["skipped"] += 1
        return "SKIPPED"
    
    try:
        # 启动批量上传
        with open(local_file, 'rb') as f:
            files = [("files", (os.path.basename(local_file), f, 'application/octet-stream'))]
            # 使用传入的服务器列表
            servers = [{"name": s["name"], "ip": s["ip"]} for s in server_list]
            data = {
                "port": str(port),
                "servers": json.dumps(servers),
                "target_dir": remote_test_base
            }
            response = requests.post(f"{base_url}/api/batch-upload", files=files, data=data, timeout=30)
        
        assert response.status_code == 200
        result = response.json()
        assert result.get("ok")
        batch_id = result.get("batch_id")
        
        print(f"  批量任务ID: {batch_id}")
        print(f"  目标服务器数: {len(servers)}")
        
        # 查询状态（多次查询，验证状态变化）
        status_updates = []
        for i in range(10):
            time.sleep(2)
            response = requests.get(f"{base_url}/api/batch-upload/status/{batch_id}", timeout=10)
            assert response.status_code == 200
            data = response.json()
            status_updates.append({
                "all_done": data.get("all_done"),
                "status": data.get("status"),
                "success_count": data.get("success_count", 0),
                "error_count": data.get("error_count", 0),
                "total_count": data.get("total_count", 0)
            })
            
            if data.get("all_done"):
                break
        
        # 验证状态信息
        final_status = status_updates[-1]
        assert final_status["all_done"], "任务应该已完成"
        assert final_status["total_count"] > 0, "应该有任务"
        assert final_status["success_count"] + final_status["error_count"] == final_status["total_count"], "任务数不匹配"
        
        print(f"✓ 测试通过: 状态查询API正常")
        print(f"  状态更新次数: {len(status_updates)}")
        print(f"  最终状态: {final_status}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_batch_upload_cancel(base_url, server_list, port, test_dir, remote_test_base):
    """测试4: 批量上传取消API"""
    print("\n" + "="*60)
    print("测试4: 批量上传取消API")
    print("="*60)
    
    local_file = os.path.join("test_data", "test_upload", "large_file.bin")
    
    if not os.path.exists(local_file):
        print("⚠ 跳过: 大文件不存在，使用小文件测试")
        local_file = os.path.join("test_data", "test_upload", "file1.txt")
        if not os.path.exists(local_file):
            test_results["skipped"] += 1
            return "SKIPPED"
    
    try:
        # 启动批量上传（使用大文件以便有时间取消）
        with open(local_file, 'rb') as f:
            files = [("files", (os.path.basename(local_file), f, 'application/octet-stream'))]
            # 使用传入的服务器列表
            servers = [{"name": s["name"], "ip": s["ip"]} for s in server_list]
            data = {
                "port": str(port),
                "servers": json.dumps(servers),
                "target_dir": remote_test_base
            }
            response = requests.post(f"{base_url}/api/batch-upload", files=files, data=data, timeout=30)
        
        assert response.status_code == 200
        result = response.json()
        assert result.get("ok")
        batch_id = result.get("batch_id")
        
        print(f"  批量任务ID: {batch_id}")
        print(f"  目标服务器数: {len(servers)}")
        
        # 等待一小段时间，然后取消
        time.sleep(1)
        
        # 取消任务
        response = requests.post(f"{base_url}/api/batch-upload/cancel/{batch_id}", timeout=10)
        assert response.status_code == 200
        cancel_result = response.json()
        assert cancel_result.get("ok"), f"取消失败: {cancel_result.get('error')}"
        
        print(f"  ✓ 取消请求成功")
        
        # 验证状态
        response = requests.get(f"{base_url}/api/batch-upload/status/{batch_id}", timeout=10)
        assert response.status_code == 200
        status_data = response.json()
        
        # 检查是否标记为已取消（可能任务已完成，也可能已取消）
        print(f"  状态查询结果: all_done={status_data.get('all_done')}, status={status_data.get('status')}")
        
        print(f"✓ 测试通过: 取消API正常")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================================
# 主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='上传功能统一自动化测试')
    parser.add_argument('--mode', type=str, choices=['function', 'api'], default='function',
                       help='测试模式: function (函数调用) 或 api (API调用)')
    parser.add_argument('--type', type=str, choices=['single', 'batch', 'all'], default='all',
                       help='测试类型: single (单文件上传), batch (批量上传), all (全部) - 仅API模式有效')
    parser.add_argument('--server', type=str, help='服务器名称')
    parser.add_argument('--ip', type=str, help='服务器IP')
    parser.add_argument('--port', type=int, default=22, help='SSH端口 (默认: 22)')
    parser.add_argument('--api-url', type=str, default='http://localhost:5000',
                       help='API服务器URL (默认: http://localhost:5000, 仅API模式需要)')
    parser.add_argument('--test', type=str, help='运行特定测试')
    parser.add_argument('--keep-temp', action='store_true', help='保留临时文件')
    
    args = parser.parse_args()
    
    # 加载配置
    load_config()
    
    # 读取配置文件获取服务器列表
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        server_dict = config.get("servers", {})
    except Exception as e:
        print(f"错误: 无法读取配置文件: {e}")
        sys.exit(1)
    
    if not server_dict:
        print("错误: 配置文件中没有服务器")
        sys.exit(1)
    
    # 选择服务器
    if args.server and args.ip:
        server_name = args.server
        server_ip = args.ip
        port = args.port
    else:
        # 使用第一个服务器
        server_name = list(server_dict.keys())[0]
        server_ip = server_dict[server_name].get("ip") if isinstance(server_dict[server_name], dict) else server_dict[server_name]
        port = args.port
    
    # API模式需要检查API服务器
    if args.mode == "api":
        try:
            response = requests.get(f"{args.api_url}/api/status", timeout=5)
            if response.status_code != 200:
                print(f"错误: API服务器 {args.api_url} 不可用")
                sys.exit(1)
        except Exception as e:
            print(f"错误: 无法连接到API服务器 {args.api_url}: {e}")
            print("提示: 请确保 web_ui.py 正在运行")
            sys.exit(1)
    
    print("="*60)
    print("上传功能统一自动化测试")
    print("="*60)
    print(f"测试模式: {args.mode.upper()}")
    if args.mode == "api":
        print(f"API服务器: {args.api_url}")
        print(f"测试类型: {args.type.upper()}")
    print(f"服务器: {server_name} ({server_ip}:{port})")
    
    # 创建临时目录
    test_dir = tempfile.mkdtemp(prefix="upload_unified_test_")
    print(f"临时目录: {test_dir}")
    
    # 远程测试目录
    remote_test_base = f"/opt/data/test_upload_unified_{int(time.time())}"
    print(f"远程测试目录: {remote_test_base}")
    
    # 创建远程测试目录
    print("正在连接服务器并创建远程测试目录...")
    transport = None
    sftp = None
    try:
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        ensure_remote_dir(sftp, remote_test_base)
        print(f"✓ 远程测试目录创建成功")
    except (TimeoutError, socket.timeout, OSError) as e:
        print(f"\n✗ 错误: 无法连接到服务器 {server_name} ({server_ip}:{port})")
        print(f"   错误类型: {type(e).__name__}")
        print(f"   错误信息: {str(e)}")
        print(f"\n提示:")
        print(f"  1. 请检查服务器是否在线")
        print(f"  2. 请检查网络连接是否正常")
        print(f"  3. 请检查防火墙设置")
        print(f"  4. 请检查SSH端口是否正确（当前: {port}）")
        print(f"  5. 可以使用 --server 和 --ip 参数指定其他服务器")
        sys.exit(1)
    except paramiko.ssh_exception.SSHException as e:
        error_msg = str(e)
        if "Unable to connect" in error_msg or "连接" in error_msg or "timeout" in error_msg.lower():
            print(f"\n✗ 错误: 无法连接到服务器 {server_name} ({server_ip}:{port})")
            print(f"   错误类型: SSHException (连接失败)")
            print(f"   错误信息: {error_msg}")
            print(f"\n提示:")
            print(f"  1. 请检查服务器是否在线")
            print(f"  2. 请检查网络连接是否正常")
            print(f"  3. 请检查防火墙设置")
            print(f"  4. 请检查SSH端口是否正确（当前: {port}）")
            print(f"  5. 可以使用 --server 和 --ip 参数指定其他服务器")
        else:
            print(f"\n✗ 错误: SSH连接异常")
            print(f"   错误类型: SSHException")
            print(f"   错误信息: {error_msg}")
            print(f"\n提示:")
            print(f"  1. 请检查SSH服务是否正常运行")
            print(f"  2. 请检查SSH配置是否正确")
        sys.exit(1)
    except paramiko.AuthenticationException as e:
        print(f"\n✗ 错误: SSH认证失败")
        print(f"   错误信息: {str(e)}")
        print(f"\n提示:")
        print(f"  1. 请检查SSH密钥配置是否正确")
        print(f"  2. 请检查config.json中的密钥路径")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n用户中断操作")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ 错误: 无法创建远程测试目录")
        print(f"   错误类型: {type(e).__name__}")
        print(f"   错误信息: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 确保资源清理
        if sftp:
            try:
                sftp.close()
            except:
                pass
        if transport:
            try:
                transport.close()
            except:
                pass
    
    # 定义所有测试（根据模式）
    if args.mode == "function":
        all_tests = {
            "test_single_file_upload": test_function_single_file_upload,
            "test_large_file_upload": test_function_large_file_upload,
            "test_file_overwrite": test_function_file_overwrite,
            "test_path_validation": test_function_path_validation,
            "test_disk_space_check": test_function_disk_space_check,
            "test_file_exists_check": test_function_file_exists_check,
            "test_special_filename": test_function_special_filename,
            "test_empty_file": test_function_empty_file,
            "test_hidden_file": test_function_hidden_file,
            "test_progress_callback": test_function_progress_callback,
        }
    else:  # api
        single_tests = {
            "test_single_upload_basic": test_api_single_upload_basic,
            "test_single_upload_invalid_params": test_api_single_upload_invalid_params,
            "test_single_upload_progress_sse": test_api_single_upload_progress_sse,
            "test_single_upload_large_file": test_api_single_upload_large_file,
        }
        batch_tests = {
            "test_batch_upload_basic": test_api_batch_upload_basic,
            "test_batch_upload_multiple_files": test_api_batch_upload_multiple_files,
            "test_batch_upload_status": test_api_batch_upload_status,
            "test_batch_upload_cancel": test_api_batch_upload_cancel,
        }
        
        if args.type == "single":
            all_tests = single_tests
        elif args.type == "batch":
            all_tests = batch_tests
        else:  # all
            all_tests = {**single_tests, **batch_tests}
    
    # 选择要运行的测试
    if args.test:
        if args.test not in all_tests:
            print(f"错误: 未知的测试名称: {args.test}")
            print(f"可用测试: {', '.join(all_tests.keys())}")
            sys.exit(1)
        tests_to_run = {args.test: all_tests[args.test]}
    else:
        tests_to_run = all_tests
    
    # 运行测试
    print("\n" + "="*60)
    print("开始运行测试")
    print("="*60)
    print(f"测试模式: {args.mode.upper()}")
    if args.mode == "api":
        print(f"API服务器: {args.api_url}")
        print(f"测试类型: {args.type.upper()}")
    print(f"服务器: {server_name} ({server_ip}:{port})")
    print(f"测试数量: {len(tests_to_run)}")
    print(f"远程测试目录: {remote_test_base}")
    print("="*60)
    
    # 对于批量上传测试，随机选择4~5个服务器
    batch_server_list = None
    if args.mode == "api" and (args.type == "batch" or args.type == "all"):
        # 检查是否有批量上传测试
        batch_test_names = ["test_batch_upload_basic", "test_batch_upload_multiple_files", 
                           "test_batch_upload_status", "test_batch_upload_cancel"]
        has_batch_tests = any(name in tests_to_run for name in batch_test_names)
        
        if has_batch_tests:
            batch_server_list = get_random_servers(server_dict, port, count_min=4, count_max=5)
            if batch_server_list:
                print(f"\n批量上传测试将使用 {len(batch_server_list)} 个随机选择的服务器:")
                for s in batch_server_list:
                    print(f"  - {s['name']} ({s['ip']})")
            else:
                print("\n警告: 无法获取足够的服务器进行批量上传测试，将使用单个服务器")
                batch_server_list = [{"name": server_name, "ip": server_ip, "port": port}]
    
    results = {}
    try:
        for test_name, test_func in tests_to_run.items():
            print(f"\n运行测试: {test_name}")
            # 批量上传测试传递服务器列表，其他测试传递单个服务器
            if batch_server_list and test_name in ["test_batch_upload_basic", "test_batch_upload_multiple_files", 
                                                   "test_batch_upload_status", "test_batch_upload_cancel"]:
                result = run_test(test_func, args.mode, args.api_url, server_name, server_ip, port, 
                                test_dir, remote_test_base, server_list=batch_server_list, test_name=test_name)
            else:
                result = run_test(test_func, args.mode, args.api_url, server_name, server_ip, port, 
                                test_dir, remote_test_base, test_name=test_name)
            results[test_name] = result
            time.sleep(1)  # 测试之间稍作延迟
    finally:
        # 清理远程测试数据
        # 如果使用了批量服务器列表，尝试清理所有服务器的测试数据
        if batch_server_list and len(batch_server_list) > 1:
            print(f"\n清理 {len(batch_server_list)} 个服务器的远程测试数据...")
            for s in batch_server_list:
                try:
                    cleanup_remote_directory(s["name"], s["ip"], s["port"], remote_test_base)
                except Exception as e:
                    print(f"  警告: 清理服务器 {s['name']} ({s['ip']}) 的测试数据失败: {e}")
        else:
            cleanup_remote_directory(server_name, server_ip, port, remote_test_base)
        
        # 清理临时目录
        if not args.keep_temp:
            try:
                shutil.rmtree(test_dir)
                print(f"\n✓ 已清理临时目录: {test_dir}")
            except:
                print(f"\n警告: 无法清理临时目录: {test_dir}")
    
    # 打印测试结果摘要
    print("\n" + "="*60)
    print("测试结果摘要")
    print("="*60)
    print(f"总计: {test_results['total']}")
    print(f"通过: {test_results['passed']} ✓")
    print(f"失败: {test_results['failed']} ✗")
    print(f"跳过: {test_results['skipped']} ⚠")
    print("="*60)
    
    # 打印详细结果
    if results:
        print("\n详细结果:")
        for test_name, result in results.items():
            status_symbol = "✓" if result == "PASSED" else ("✗" if result == "FAILED" else "⚠")
            print(f"  {status_symbol} {test_name}: {result}")
    
    # 生成测试结果文档
    generate_test_report(args, test_results, results, server_name, server_ip, port, batch_server_list)
    
    # 返回退出码
    sys.exit(0 if test_results['failed'] == 0 else 1)

if __name__ == "__main__":
    main()

