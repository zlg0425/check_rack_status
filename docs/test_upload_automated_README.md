# 上传功能自动化测试说明

本文档说明如何使用上传功能的自动化测试脚本。

## 测试脚本

### 1. `test_upload_automated.py` - 单文件上传测试

测试单个文件上传的各种场景，包括：
- 正常文件上传
- 大文件上传
- 文件覆盖上传
- 路径验证（防止路径遍历攻击）
- 磁盘空间检查
- 文件存在检查
- 特殊字符文件名
- 空文件上传
- 隐藏文件上传
- 进度回调功能

#### 用法

```bash
# 运行所有测试
python test_upload_automated.py

# 运行特定测试
python test_upload_automated.py --test test_single_file_upload

# 指定服务器
python test_upload_automated.py --server LP-8650-1 --ip 10.99.19.11 --port 22

# 保留临时文件
python test_upload_automated.py --keep-temp
```

#### 测试用例列表

- `test_single_file_upload` - 单个文件上传
- `test_large_file_upload` - 大文件上传（5MB）
- `test_file_overwrite` - 文件覆盖上传
- `test_path_validation` - 路径验证测试
- `test_disk_space_check` - 磁盘空间检查
- `test_file_exists_check` - 文件存在检查
- `test_special_filename` - 特殊字符文件名
- `test_empty_file` - 空文件上传
- `test_hidden_file` - 隐藏文件上传
- `test_progress_callback` - 进度回调功能

### 2. `test_batch_upload_automated.py` - 批量上传测试

测试批量上传功能的各种场景，包括：
- 基本批量上传（单文件多服务器）
- 多文件批量上传
- 单文件多服务器批量上传
- 空文件批量上传
- 大文件批量上传
- 无效参数测试
- 进度跟踪测试

**注意**: 此测试脚本需要 `web_ui.py` 正在运行，因为它通过HTTP API进行测试。

#### 用法

```bash
# 确保 web_ui.py 正在运行
python web_ui.py

# 在另一个终端运行测试
python test_batch_upload_automated.py

# 运行特定测试
python test_batch_upload_automated.py --test test_batch_upload_basic

# 指定服务器和Web服务地址
python test_batch_upload_automated.py --server LP-8650-1 --ip 10.99.19.11 --port 22 --base-url http://localhost:5000
```

#### 测试用例列表

- `test_batch_upload_basic` - 基本批量上传
- `test_batch_upload_multiple_files` - 多文件批量上传
- `test_batch_upload_multiple_servers` - 多服务器批量上传
- `test_batch_upload_empty_file` - 空文件批量上传
- `test_batch_upload_large_file` - 大文件批量上传
- `test_batch_upload_invalid_params` - 无效参数测试
- `test_batch_upload_progress_tracking` - 进度跟踪测试

## 测试数据

测试数据位于 `test_data/test_upload/` 目录：

- `file1.txt` - 普通文本文件（用于基本测试）
- `file2.txt` - 普通文本文件（用于多文件测试）
- `small_file.bin` - 小二进制文件
- `large_file.bin` - 大二进制文件（5MB，用于大文件测试）
- `empty_file.txt` - 空文件（用于空文件测试）
- `.hidden_file` - 隐藏文件（用于隐藏文件测试）
- `special_chars_file_测试.txt` - 包含特殊字符和中文的文件名

### 创建测试数据

测试数据已经预创建在 `test_data/test_upload/` 目录中。如果需要重新创建：

```bash
# 创建目录
mkdir -p test_data/test_upload

# 创建普通文件
echo "这是测试文件1" > test_data/test_upload/file1.txt
echo "这是测试文件2" > test_data/test_upload/file2.txt

# 创建空文件
touch test_data/test_upload/empty_file.txt

# 创建隐藏文件
echo "隐藏文件内容" > test_data/test_upload/.hidden_file

# 创建特殊字符文件名（Windows PowerShell）
echo "特殊字符文件" > "test_data/test_upload/special_chars_file_测试.txt"

# 创建大文件（5MB，Python）
python -c "with open('test_data/test_upload/large_file.bin', 'wb') as f: f.write(b'0' * (5 * 1024 * 1024))"
```

## 测试环境要求

1. **Python环境**
   - Python 3.6+
   - 安装依赖：`paramiko`, `requests`（批量上传测试需要）

2. **服务器配置**
   - 在 `config.json` 中配置了可用的服务器
   - 服务器需要支持SFTP连接
   - 确保有足够的磁盘空间（特别是大文件测试）

3. **网络连接**
   - 能够访问配置的服务器
   - 批量上传测试需要能够访问Web服务（默认 `http://localhost:5000`）

## 测试结果

测试脚本会输出详细的测试结果，包括：
- 每个测试用例的执行状态（通过/失败/跳过）
- 测试结果摘要
- 详细的错误信息（如果失败）

### 退出码

- `0` - 所有测试通过
- `1` - 有测试失败

## 注意事项

1. **远程测试目录**
   - 测试会在远程服务器创建临时目录（格式：`/opt/data/test_upload_<timestamp>` 或 `/opt/data/test_batch_upload_<timestamp>`）
   - **测试完成后会自动清理远程测试目录**（包括单文件上传测试和批量上传测试）
   - 清理功能会递归删除测试目录及其所有内容

2. **大文件测试**
   - 大文件测试需要较长时间（取决于网络速度）
   - 确保有足够的磁盘空间

3. **并发测试**
   - 批量上传测试会并发上传多个文件
   - 注意服务器的并发连接限制

4. **测试数据清理**
   - 单文件上传测试会自动清理远程测试目录
   - 批量上传测试不会自动清理，需要手动清理或等待自动清理

## 故障排除

### 连接失败

如果测试失败并显示连接错误：
1. 检查服务器配置是否正确
2. 检查网络连接
3. 检查SSH密钥配置
4. 检查服务器是否可访问

### 权限错误

如果测试失败并显示权限错误：
1. 检查远程目录是否有写权限
2. 检查SSH用户权限

### 磁盘空间不足

如果测试失败并显示磁盘空间不足：
1. 清理远程服务器上的文件
2. 选择有更多空间的目录

### Web服务不可用（批量上传测试）

如果批量上传测试失败并显示Web服务不可用：
1. 确保 `web_ui.py` 正在运行
2. 检查 `--base-url` 参数是否正确
3. 检查防火墙设置

## 示例输出

```
============================================================
上传功能自动化测试
============================================================
服务器: LP-8650-1 (10.99.19.11:22)
临时目录: /tmp/upload_test_1234567890
远程测试目录: /opt/data/test_upload_1234567890
============================================================

运行测试: test_single_file_upload

============================================================
测试1: 单个文件上传
============================================================
✓ 测试通过: 文件上传成功
  远程文件: /opt/data/test_upload_1234567890/uploaded_file1.txt
  文件大小: 0.05 KB

============================================================
测试结果摘要
============================================================
总计: 10
通过: 10 ✓
失败: 0 ✗
跳过: 0 ⚠
```

