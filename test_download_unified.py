#!/usr/bin/env python3
"""
下载功能统一自动化测试脚本
整合函数调用和API调用两种测试方式

用法：
    python test_download_unified.py [--mode function|api] [--server SERVER_NAME] [--ip IP] [--port PORT] [--api-url API_URL] [--test TEST_NAME]
    
示例：
    # 使用函数调用方式运行所有测试
    python test_download_unified.py --mode function
    
    # 使用API调用方式运行所有测试（需要先启动web_ui.py）
    python test_download_unified.py --mode api
    
    # 运行特定测试
    python test_download_unified.py --mode function --test test_file_download
    
    # 指定服务器和API URL
    python test_download_unified.py --mode api --server LP-8650-1 --ip 10.99.19.11 --port 22 --api-url http://127.0.0.1:5000
"""

import sys
import os
import time
import tempfile
import shutil
import argparse
import posixpath
import requests
import zipfile
import json
from check_rack_status import load_config, sftp_download, create_transport, ensure_remote_dir, sftp_upload
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

def format_speed(bytes_per_sec):
    """格式化速度"""
    return format_bytes(bytes_per_sec) + "/s"

# ============================================================================
# 通用工具函数
# ============================================================================

def upload_test_data(server_name, server_ip, port, local_test_data_dir, remote_test_base):
    """上传测试数据到远程服务器"""
    print(f"正在上传测试数据到 {server_name} ({server_ip}:{port})...")
    
    try:
        # 创建SFTP连接
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        try:
            # 确保远程目录存在
            ensure_remote_dir(sftp, remote_test_base)
            
            # 上传所有测试文件
            uploaded_files = []
            for root, dirs, files in os.walk(local_test_data_dir):
                for file in files:
                    local_file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_file_path, local_test_data_dir)
                    remote_dir = posixpath.join(remote_test_base, os.path.dirname(relative_path).replace('\\', '/'))
                    remote_file_path = posixpath.join(remote_test_base, relative_path.replace('\\', '/'))
                    
                    # 确保远程目录存在
                    if remote_dir != remote_test_base:
                        ensure_remote_dir(sftp, remote_dir)
                    
                    # 上传文件
                    with open(local_file_path, 'rb') as f:
                        ok, info = sftp_upload(server_name, server_ip, port, 
                                              posixpath.dirname(remote_file_path), 
                                              os.path.basename(remote_file_path), 
                                              data=f.read())
                        if ok:
                            uploaded_files.append(remote_file_path)
                        else:
                            print(f"警告: 无法上传 {local_file_path}: {info}")
            
            # 创建测试目录结构
            test_subdir = posixpath.join(remote_test_base, "nested", "subdir")
            ensure_remote_dir(sftp, test_subdir)
            
            # 上传文件到嵌套目录
            test_file_content = b"nested file content"
            ok, info = sftp_upload(server_name, server_ip, port, test_subdir, "nested_file.txt", data=test_file_content)
            if ok:
                uploaded_files.append(posixpath.join(test_subdir, "nested_file.txt"))
            
            # 创建空文件夹
            empty_dir = posixpath.join(remote_test_base, "empty_dir")
            ensure_remote_dir(sftp, empty_dir)
            
            print(f"✓ 测试数据上传完成，共 {len(uploaded_files)} 个文件")
            return True, uploaded_files
        finally:
            sftp.close()
            transport.close()
    except Exception as e:
        return False, str(e)

def cleanup_remote_directory(server_name, server_ip, port, remote_path):
    """递归删除远程目录"""
    try:
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        def remove_recursive(sftp, path):
            try:
                items = sftp.listdir_attr(path)
                for item in items:
                    item_path = posixpath.join(path, item.filename)
                    if item.st_mode & 0o040000:  # 目录
                        remove_recursive(sftp, item_path)
                    else:  # 文件
                        try:
                            sftp.remove(item_path)
                        except:
                            pass
                sftp.rmdir(path)
            except:
                pass
        
        try:
            remove_recursive(sftp, remote_path)
            print(f"✓ 已清理远程目录: {remote_path}")
        except:
            pass
        
        sftp.close()
        transport.close()
    except Exception as e:
        print(f"警告: 清理远程目录失败: {str(e)}")

def check_api_server(api_url):
    """检查API服务器是否运行"""
    try:
        response = requests.get(f"{api_url}/api/status", timeout=5)
        return response.status_code == 200
    except:
        return False

# ============================================================================
# 函数调用方式测试（直接调用sftp_download）
# ============================================================================

def test_function_file_download(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试1: 正常文件下载"""
    print("\n" + "="*60)
    print("函数测试1: 正常文件下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "test_file.txt")
    local_path = os.path.join(test_dir, "test_file.txt")
    
    try:
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        
        assert ok, f"下载失败: {info}"
        assert os.path.exists(local_path), "本地文件不存在"
        assert os.path.isfile(local_path), "本地路径不是文件"
        
        print(f"✓ 测试通过: 文件下载成功")
        print(f"  本地文件: {local_path}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_directory_download(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试2: 文件夹下载"""
    print("\n" + "="*60)
    print("函数测试2: 文件夹下载")
    print("="*60)
    
    remote_path = remote_test_base
    local_path = os.path.join(test_dir, "test_download")
    
    try:
        start_time = time.time()
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        elapsed = time.time() - start_time
        
        assert ok, f"下载失败: {info}"
        assert os.path.exists(local_path), "本地文件夹不存在"
        assert os.path.isdir(local_path), "本地路径不是文件夹"
        
        # 统计下载的文件数量
        file_count = 0
        total_size = 0
        for root, dirs, files in os.walk(local_path):
            file_count += len(files)
            for file in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, file))
                except:
                    pass
        
        print(f"✓ 测试通过: 文件夹下载成功")
        print(f"  本地文件夹: {local_path}")
        print(f"  文件数量: {file_count}")
        print(f"  总大小: {format_bytes(total_size)}")
        print(f"  耗时: {elapsed:.2f} 秒")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_file_not_exists(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试3: 文件不存在"""
    print("\n" + "="*60)
    print("函数测试3: 文件不存在")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nonexistent_file_12345.txt")
    local_path = os.path.join(test_dir, "nonexistent_file.txt")
    
    try:
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        
        assert not ok, "应该返回失败"
        assert "不存在" in info or "not found" in info.lower(), f"错误信息不正确: {info}"
        assert not os.path.exists(local_path), "不应该创建本地文件"
        
        print(f"✓ 测试通过: 正确处理文件不存在的情况")
        print(f"  错误信息: {info}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_directory_not_exists(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试4: 文件夹不存在"""
    print("\n" + "="*60)
    print("函数测试4: 文件夹不存在")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nonexistent_dir_12345")
    local_path = os.path.join(test_dir, "nonexistent_dir")
    
    try:
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        
        assert not ok, "应该返回失败"
        assert "不存在" in info or "not found" in info.lower(), f"错误信息不正确: {info}"
        assert not os.path.exists(local_path), "不应该创建本地文件夹"
        
        print(f"✓ 测试通过: 正确处理文件夹不存在的情况")
        print(f"  错误信息: {info}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_file_overwrite(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试5: 文件覆盖"""
    print("\n" + "="*60)
    print("函数测试5: 文件覆盖")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "test_file.txt")
    local_path = os.path.join(test_dir, "overwrite_test.txt")
    
    try:
        # 先创建一个本地文件
        with open(local_path, 'w') as f:
            f.write("Old content")
        
        old_size = os.path.getsize(local_path)
        
        # 执行下载（应该覆盖）
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        
        assert ok, f"下载失败: {info}"
        assert os.path.exists(local_path), "本地文件应该存在"
        new_size = os.path.getsize(local_path)
        assert new_size != old_size, "文件应该被覆盖"
        
        print(f"✓ 测试通过: 文件覆盖成功")
        print(f"  旧文件大小: {old_size} 字节")
        print(f"  新文件大小: {new_size} 字节")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_empty_directory(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试6: 空文件夹下载"""
    print("\n" + "="*60)
    print("函数测试6: 空文件夹下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "empty_dir")
    local_path = os.path.join(test_dir, "empty_dir")
    
    try:
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        
        assert ok, f"下载失败: {info}"
        assert os.path.exists(local_path), "本地文件夹应该存在"
        assert os.path.isdir(local_path), "本地路径应该是文件夹"
        
        # 检查文件夹是否为空
        items = os.listdir(local_path)
        assert len(items) == 0, f"文件夹应该为空，但包含 {len(items)} 个项目"
        
        print(f"✓ 测试通过: 空文件夹下载成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_progress_callback(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试7: 进度回调"""
    print("\n" + "="*60)
    print("函数测试7: 进度回调")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "large_file.bin")
    local_path = os.path.join(test_dir, "progress_test.bin")
    
    progress_updates = []
    
    def progress_callback(transferred, total):
        progress_updates.append((transferred, total))
        percent = (transferred / total * 100) if total > 0 else 0
        print(f"\r  进度: {percent:.1f}% ({format_bytes(transferred)}/{format_bytes(total)})", end="", flush=True)
    
    try:
        ok, info = sftp_download(
            server_name, server_ip, port, 
            remote_path, local_path,
            progress_callback=progress_callback
        )
        print()  # 换行
        
        assert ok, f"下载失败: {info}"
        assert len(progress_updates) > 0, "应该有进度更新"
        
        # 检查最后一个进度更新应该是100%
        last_transferred, last_total = progress_updates[-1]
        assert last_transferred == last_total, "最后进度应该是100%"
        
        print(f"✓ 测试通过: 进度回调正常")
        print(f"  进度更新次数: {len(progress_updates)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

def test_function_large_file_download(server_name, server_ip, port, test_dir, remote_test_base):
    """函数测试8: 大文件下载"""
    print("\n" + "="*60)
    print("函数测试8: 大文件下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "large_file.bin")
    local_path = os.path.join(test_dir, "large_file.bin")
    
    try:
        # 检查文件是否存在
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            file_stat = sftp.stat(remote_path)
            file_size = file_stat.st_size
        except IOError:
            print("⚠ 跳过测试: 大文件不存在")
            sftp.close()
            transport.close()
            return None
        sftp.close()
        transport.close()
        
        print(f"  下载大文件: {remote_path} ({format_bytes(file_size)})")
        
        # 执行下载
        start_time = time.time()
        ok, info = sftp_download(server_name, server_ip, port, remote_path, local_path)
        elapsed = time.time() - start_time
        
        assert ok, f"下载失败: {info}"
        assert os.path.exists(local_path), "本地文件应该存在"
        
        downloaded_size = os.path.getsize(local_path)
        speed = downloaded_size / elapsed if elapsed > 0 else 0
        
        print(f"✓ 测试通过: 大文件下载成功")
        print(f"  文件大小: {format_bytes(downloaded_size)}")
        print(f"  耗时: {elapsed:.2f} 秒")
        print(f"  平均速度: {format_speed(speed)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

# ============================================================================
# API调用方式测试（通过HTTP请求）
# ============================================================================

def test_api_file_download(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试1: 正常文件下载"""
    print("\n" + "="*60)
    print("API测试1: 正常文件下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "test_file.txt")
    local_path = os.path.join(test_dir, "downloaded_file.txt")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        assert 'Content-Disposition' in response.headers, "缺少Content-Disposition头"
        assert 'attachment' in response.headers['Content-Disposition'], "Content-Disposition格式错误"
        assert response.headers['Content-Type'] == 'application/octet-stream', f"Content-Type错误: {response.headers['Content-Type']}"
        
        # 保存文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        assert os.path.exists(local_path), "下载的文件不存在"
        assert os.path.getsize(local_path) > 0, "下载的文件为空"
        
        print(f"✓ 测试通过: 文件下载成功")
        print(f"  文件大小: {format_bytes(os.path.getsize(local_path))}")
        print(f"  Content-Disposition: {response.headers['Content-Disposition']}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_directory_download(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试2: 文件夹下载（ZIP格式）"""
    print("\n" + "="*60)
    print("API测试2: 文件夹下载（ZIP格式）")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nested")
    local_path = os.path.join(test_dir, "downloaded_dir.zip")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=60)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        assert response.headers['Content-Type'] == 'application/zip', f"Content-Type错误: {response.headers['Content-Type']}"
        assert 'Content-Disposition' in response.headers, "缺少Content-Disposition头"
        assert '.zip' in response.headers['Content-Disposition'], "ZIP文件名格式错误"
        
        # 保存ZIP文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        assert os.path.exists(local_path), "下载的ZIP文件不存在"
        assert os.path.getsize(local_path) > 0, "下载的ZIP文件为空"
        
        # 验证ZIP文件内容
        with zipfile.ZipFile(local_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            assert len(file_list) > 0, "ZIP文件为空"
            assert 'nested_file.txt' in file_list or any('nested_file.txt' in f for f in file_list), "ZIP文件缺少预期文件"
        
        print(f"✓ 测试通过: 文件夹下载成功")
        print(f"  ZIP文件大小: {format_bytes(os.path.getsize(local_path))}")
        print(f"  ZIP文件包含 {len(file_list)} 个文件/目录")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_empty_directory(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试3: 空文件夹下载"""
    print("\n" + "="*60)
    print("API测试3: 空文件夹下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "empty_dir")
    local_path = os.path.join(test_dir, "empty_dir.zip")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        assert response.headers['Content-Type'] == 'application/zip', f"Content-Type错误: {response.headers['Content-Type']}"
        
        # 保存ZIP文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        assert os.path.exists(local_path), "下载的ZIP文件不存在"
        
        print(f"✓ 测试通过: 空文件夹下载成功")
        print(f"  ZIP文件大小: {format_bytes(os.path.getsize(local_path))}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_file_not_exists(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试4: 文件不存在错误处理"""
    print("\n" + "="*60)
    print("API测试4: 文件不存在错误处理")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nonexistent_file.txt")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, timeout=30)
        
        assert response.status_code == 400, f"HTTP状态码错误: {response.status_code}，期望400"
        assert response.headers['Content-Type'] == 'application/json', f"Content-Type错误: {response.headers['Content-Type']}"
        
        data = response.json()
        assert 'ok' in data, "响应缺少ok字段"
        assert data['ok'] == False, "ok字段应为False"
        assert 'error' in data, "响应缺少error字段"
        assert len(data['error']) > 0, "错误信息为空"
        
        print(f"✓ 测试通过: 错误处理正确")
        print(f"  错误信息: {data['error']}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_directory_not_exists(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试5: 文件夹不存在错误处理"""
    print("\n" + "="*60)
    print("API测试5: 文件夹不存在错误处理")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nonexistent_dir")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, timeout=30)
        
        assert response.status_code == 400, f"HTTP状态码错误: {response.status_code}，期望400"
        data = response.json()
        assert data['ok'] == False, "ok字段应为False"
        assert 'error' in data, "响应缺少error字段"
        
        print(f"✓ 测试通过: 错误处理正确")
        print(f"  错误信息: {data['error']}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_missing_parameters(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试6: 参数缺失错误处理"""
    print("\n" + "="*60)
    print("API测试6: 参数缺失错误处理")
    print("="*60)
    
    test_cases = [
        # (params, expected_status, description)
        ({}, 400, "所有参数缺失"),
        ({"server_name": server_name}, 400, "缺少server_ip和port"),
        ({"server_name": server_name, "server_ip": server_ip}, 400, "缺少port"),
        ({"server_name": server_name, "server_ip": server_ip, "port": port}, 400, "缺少remote_path"),
        ({"server_name": "", "server_ip": server_ip, "port": port, "remote_path": "/tmp/test"}, 400, "server_name为空"),
        ({"server_name": server_name, "server_ip": "", "port": port, "remote_path": "/tmp/test"}, 400, "server_ip为空"),
        ({"server_name": server_name, "server_ip": server_ip, "port": 80, "remote_path": "/tmp/test"}, 400, "端口非法（80）"),
        ({"server_name": server_name, "server_ip": server_ip, "port": 22, "remote_path": ""}, 400, "remote_path为空"),
    ]
    
    try:
        url = f"{api_url}/api/download/stream"
        passed = 0
        
        for params, expected_status, description in test_cases:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == expected_status:
                passed += 1
                print(f"  ✓ {description}: HTTP {response.status_code}")
            else:
                print(f"  ✗ {description}: HTTP {response.status_code} (期望 {expected_status})")
        
        assert passed == len(test_cases), f"部分测试用例失败: {passed}/{len(test_cases)}"
        print(f"✓ 测试通过: 所有参数验证测试通过")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_large_file_download(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试7: 大文件下载"""
    print("\n" + "="*60)
    print("API测试7: 大文件下载")
    print("="*60)
    
    # 创建一个大文件（5MB）
    large_file_size = 5 * 1024 * 1024
    large_file_content = b"0" * large_file_size
    remote_path = posixpath.join(remote_test_base, "large_file.bin")
    local_path = os.path.join(test_dir, "large_file.bin")
    
    try:
        # 上传大文件
        print(f"  正在上传大文件 ({format_bytes(large_file_size)})...")
        ok, info = sftp_upload(server_name, server_ip, port, 
                              posixpath.dirname(remote_path), 
                              os.path.basename(remote_path), 
                              data=large_file_content)
        if not ok:
            print(f"  ⚠ 跳过测试: 无法上传大文件: {info}")
            return None
        
        # 调用API
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        print(f"  正在下载大文件...")
        start_time = time.time()
        response = requests.get(url, params=params, stream=True, timeout=120)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        
        # 流式保存文件
        downloaded_size = 0
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded_size += len(chunk)
        
        elapsed_time = time.time() - start_time
        speed = downloaded_size / elapsed_time if elapsed_time > 0 else 0
        
        assert os.path.exists(local_path), "下载的文件不存在"
        assert os.path.getsize(local_path) == large_file_size, f"文件大小不匹配: {os.path.getsize(local_path)} != {large_file_size}"
        
        print(f"✓ 测试通过: 大文件下载成功")
        print(f"  文件大小: {format_bytes(downloaded_size)}")
        print(f"  下载时间: {elapsed_time:.2f}秒")
        print(f"  平均速度: {format_bytes(speed)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_empty_file_download(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试8: 空文件下载"""
    print("\n" + "="*60)
    print("API测试8: 空文件下载")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "empty_file.txt")
    local_path = os.path.join(test_dir, "empty_file.txt")
    
    try:
        # 上传空文件
        ok, info = sftp_upload(server_name, server_ip, port, 
                              posixpath.dirname(remote_path), 
                              os.path.basename(remote_path), 
                              data=b"")
        if not ok:
            print(f"  ⚠ 跳过测试: 无法上传空文件: {info}")
            return None
        
        # 调用API
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        
        # 保存文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        assert os.path.exists(local_path), "下载的文件不存在"
        assert os.path.getsize(local_path) == 0, "空文件大小不为0"
        
        print(f"✓ 测试通过: 空文件下载成功")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_content_disposition_header(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试9: Content-Disposition头验证"""
    print("\n" + "="*60)
    print("API测试9: Content-Disposition头验证")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "test_file.txt")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        assert 'Content-Disposition' in response.headers, "缺少Content-Disposition头"
        
        content_disposition = response.headers['Content-Disposition']
        assert 'attachment' in content_disposition, "Content-Disposition缺少attachment"
        assert 'filename' in content_disposition, "Content-Disposition缺少filename"
        
        assert 'Content-Type' in response.headers, "缺少Content-Type头"
        content_type = response.headers['Content-Type']
        assert content_type in ['application/octet-stream', 'application/zip'], f"Content-Type错误: {content_type}"
        
        print(f"✓ 测试通过: Content-Disposition头格式正确")
        print(f"  Content-Disposition: {content_disposition}")
        print(f"  Content-Type: {content_type}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_streaming_response(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试10: 流式响应验证"""
    print("\n" + "="*60)
    print("API测试10: 流式响应验证")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "test_file.txt")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        assert response.status_code == 200, f"HTTP状态码错误: {response.status_code}"
        assert response.raw, "响应不是流式响应"
        
        # 验证可以分块读取
        chunk_count = 0
        total_size = 0
        for chunk in response.iter_content(chunk_size=1024):
            chunk_count += 1
            total_size += len(chunk)
            if chunk_count >= 10:  # 只读取前10个chunk
                break
        
        assert chunk_count > 0, "无法读取响应块"
        
        print(f"✓ 测试通过: 流式响应正常")
        print(f"  读取块数: {chunk_count}")
        print(f"  读取大小: {format_bytes(total_size)}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_api_zip_structure(api_url, server_name, server_ip, port, test_dir, remote_test_base):
    """API测试11: ZIP文件结构验证"""
    print("\n" + "="*60)
    print("API测试11: ZIP文件结构验证")
    print("="*60)
    
    remote_path = posixpath.join(remote_test_base, "nested")
    local_path = os.path.join(test_dir, "nested_structure.zip")
    
    try:
        url = f"{api_url}/api/download/stream"
        params = {
            "server_name": server_name,
            "server_ip": server_ip,
            "port": port,
            "remote_path": remote_path
        }
        
        response = requests.get(url, params=params, stream=True, timeout=60)
        
        # 保存ZIP文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # 验证ZIP文件结构
        with zipfile.ZipFile(local_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            
            assert len(file_list) > 0, "ZIP文件为空"
            
            # 验证ZIP文件包含预期文件
            has_nested_file = any('nested_file.txt' in f for f in file_list)
            assert has_nested_file, "ZIP文件缺少nested_file.txt"
            
            # 验证ZIP文件完整性
            bad_file = zip_ref.testzip()
            assert bad_file is None, f"ZIP文件损坏: {bad_file}"
        
        print(f"✓ 测试通过: ZIP文件结构正确")
        print(f"  ZIP文件包含 {len(file_list)} 个文件/目录")
        print(f"  文件列表: {', '.join(file_list[:5])}{'...' if len(file_list) > 5 else ''}")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================================
# 测试运行器
# ============================================================================

def run_test(test_func, mode, api_url, server_name, server_ip, port, test_dir, remote_test_base, test_name=None):
    """运行单个测试"""
    test_results["total"] += 1
    start_time = time.time()
    error_msg = None
    try:
        if mode == "function":
            result = test_func(server_name, server_ip, port, test_dir, remote_test_base)
        else:  # api
            result = test_func(api_url, server_name, server_ip, port, test_dir, remote_test_base)
        
        elapsed_time = time.time() - start_time
        
        if result is None:
            test_results["skipped"] += 1
            status = "SKIPPED"
        elif result:
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
        test_results["failed"] += 1
        print(f"✗ 测试异常: {error_msg}")
        import traceback
        traceback.print_exc()
        
        # 记录详细结果
        if test_name:
            detailed_results.append({
                "name": test_name,
                "status": "FAILED",
                "elapsed_time": elapsed_time,
                "error": error_msg
            })
        
        return "FAILED"

def generate_test_report(args, test_results, results, server_name, server_ip, port):
    """生成测试结果文档"""
    import datetime
    
    # 创建报告目录
    report_dir = "test_reports"
    os.makedirs(report_dir, exist_ok=True)
    
    # 生成报告文件名
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(report_dir, f"download_test_report_{timestamp}.md")
    
    # 计算通过率
    pass_rate = (test_results['passed'] / test_results['total'] * 100) if test_results['total'] > 0 else 0
    
    # 生成Markdown报告
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(f"# 下载功能测试报告\n\n")
        f.write(f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 测试环境信息
        f.write(f"## 测试环境\n\n")
        f.write(f"- **测试模式**: {args.mode.upper()}\n")
        if args.mode == "api":
            f.write(f"- **API地址**: {args.api_url}\n")
        f.write(f"- **服务器**: {server_name} ({server_ip}:{port})\n")
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

def main():
    parser = argparse.ArgumentParser(description="下载功能统一自动化测试")
    parser.add_argument("--mode", choices=["function", "api"], default="function", 
                       help="测试模式: function=函数调用, api=HTTP API调用")
    parser.add_argument("--server", default="LP-8650-1", help="服务器名称")
    parser.add_argument("--ip", default="10.99.19.11", help="服务器IP")
    parser.add_argument("--port", type=int, default=22, help="端口 (22 或 9999)")
    parser.add_argument("--api-url", default="http://127.0.0.1:5000", help="API服务器URL（仅API模式需要）")
    parser.add_argument("--test", help="运行特定测试")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时测试目录")
    
    args = parser.parse_args()
    
    # API模式需要检查服务器
    if args.mode == "api":
        print("正在检查API服务器...")
        if not check_api_server(args.api_url):
            print(f"错误: API服务器未运行或无法访问: {args.api_url}")
            print("请先启动web_ui.py服务器")
            sys.exit(1)
        print(f"✓ API服务器运行正常: {args.api_url}")
    
    # 加载配置
    print("正在加载配置...")
    load_config()
    print("配置加载完成")
    
    # 创建临时测试目录
    test_dir = tempfile.mkdtemp(prefix=f"download_{args.mode}_test_")
    print(f"\n测试目录: {test_dir}")
    
    # 准备测试数据路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_test_data_dir = os.path.join(script_dir, "test_data", "test_download")
    remote_test_base = f"/opt/data/test_download_{args.mode}_{int(time.time())}"
    
    # 检查本地测试数据是否存在
    if not os.path.exists(local_test_data_dir):
        print(f"错误: 测试数据目录不存在: {local_test_data_dir}")
        print("请确保 test_data/test_download/ 目录存在并包含测试文件")
        sys.exit(1)
    
    # 上传测试数据到远程服务器
    upload_ok, upload_info = upload_test_data(args.server, args.ip, args.port, local_test_data_dir, remote_test_base)
    if not upload_ok:
        print(f"错误: 无法上传测试数据: {upload_info}")
        sys.exit(1)
    
    # 定义所有测试（根据模式）
    if args.mode == "function":
        all_tests = {
            "test_file_download": test_function_file_download,
            "test_directory_download": test_function_directory_download,
            "test_file_not_exists": test_function_file_not_exists,
            "test_directory_not_exists": test_function_directory_not_exists,
            "test_file_overwrite": test_function_file_overwrite,
            "test_empty_directory": test_function_empty_directory,
            "test_progress_callback": test_function_progress_callback,
            "test_large_file_download": test_function_large_file_download,
        }
    else:  # api
        all_tests = {
            "test_file_download": test_api_file_download,
            "test_directory_download": test_api_directory_download,
            "test_empty_directory": test_api_empty_directory,
            "test_file_not_exists": test_api_file_not_exists,
            "test_directory_not_exists": test_api_directory_not_exists,
            "test_missing_parameters": test_api_missing_parameters,
            "test_large_file_download": test_api_large_file_download,
            "test_empty_file_download": test_api_empty_file_download,
            "test_content_disposition_header": test_api_content_disposition_header,
            "test_streaming_response": test_api_streaming_response,
            "test_zip_structure": test_api_zip_structure,
        }
    
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
    print(f"服务器: {args.server} ({args.ip}:{args.port})")
    print(f"测试数量: {len(tests_to_run)}")
    print(f"远程测试数据目录: {remote_test_base}")
    print("="*60)
    
    results = {}
    try:
        for test_name, test_func in tests_to_run.items():
            print(f"\n运行测试: {test_name}")
            result = run_test(test_func, args.mode, args.api_url, args.server, args.ip, args.port, test_dir, remote_test_base, test_name=test_name)
            results[test_name] = result
    finally:
        # 清理远程测试数据
        cleanup_remote_directory(args.server, args.ip, args.port, remote_test_base)
        
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
    print(f"跳过: {test_results['skipped']} -")
    print("="*60)
    
    # 打印详细结果
    if results:
        print("\n详细结果:")
        for test_name, result in results.items():
            status_symbol = "✓" if result == "PASSED" else ("✗" if result == "FAILED" else "-")
            print(f"  {status_symbol} {test_name}: {result}")
    
    # 生成测试结果文档
    generate_test_report(args, test_results, results, args.server, args.ip, args.port)
    
    # 返回退出码
    sys.exit(0 if test_results['failed'] == 0 else 1)

if __name__ == "__main__":
    main()

