# 下载功能完整文档

本文档整合了下载功能的所有相关信息，包括场景分析、函数测试和API测试。

## 目录

1. [功能概述](#功能概述)
2. [场景分析](#场景分析)
3. [函数测试](#函数测试)
4. [API测试](#api测试)
5. [快速参考](#快速参考)

---

## 功能概述

下载功能支持从远程服务器下载文件和文件夹到本地。主要特性包括：

- ✅ **文件下载**：支持单个文件下载
- ✅ **文件夹下载**：支持整个文件夹下载（自动打包为ZIP）
- ✅ **流式传输**：大文件流式传输，避免内存溢出
- ✅ **错误处理**：完善的错误处理和资源清理
- ✅ **进度跟踪**：支持进度回调（函数调用方式）
- ✅ **自动清理**：下载失败时自动清理不完整文件

### 两种使用方式

1. **函数调用方式**：直接调用 `sftp_download()` 函数
   - 适用于脚本和自动化任务
   - 支持进度回调
   - 测试脚本：`test_download_automated.py`

2. **HTTP API方式**：通过 `/api/download/stream` 端点
   - 适用于Web前端
   - 浏览器直接下载
   - 测试脚本：`test_download_api_automated.py`

---

## 场景分析

### 1. 远程路径场景

#### 1.1 文件下载
- ✅ 正常文件下载
- ✅ 大文件下载（>1GB）
- ✅ 小文件下载（<1KB）
- ✅ 文件不存在 → **已处理**：返回明确的错误信息
- ✅ 文件权限不足 → **已处理**：检查权限错误并返回明确提示
- ✅ 符号链接 → **已处理**：自动解析符号链接到真实路径
- ⚠️ 特殊字符文件名 → 需要测试：Windows路径限制

#### 1.2 文件夹下载
- ✅ 正常文件夹下载
- ✅ 嵌套文件夹下载
- ✅ 空文件夹下载 → **已处理**：会创建空目录
- ✅ 文件夹不存在 → **已处理**：返回明确的错误信息
- ✅ 文件夹权限不足 → **已处理**：检查权限错误并返回明确提示
- ✅ 文件夹中包含符号链接 → **已处理**：跳过符号链接，避免循环引用
- ⚠️ 文件夹中包含特殊字符文件名 → 需要测试

### 2. 本地路径场景

#### 2.1 路径冲突
- ✅ 本地目录不存在 → **已处理**：自动创建
- ✅ 本地目录已存在 → **已处理**：检查是否为目录
- ✅ 本地路径是文件而不是目录 → **已处理**：返回明确错误
- ✅ 本地文件已存在 → **已处理**：自动覆盖（删除后重新下载）
- ⚠️ 本地文件被占用 → 需要处理：返回错误（Windows会抛出异常）

#### 2.2 路径限制
- ⚠️ Windows路径长度限制（260字符） → 需要测试：Windows长路径支持
- ✅ 磁盘空间不足 → **已处理**：下载前检查磁盘空间（Python 3.3+）
- ✅ 路径权限不足 → **已处理**：捕获OSError并返回明确错误信息

#### 2.3 路径格式
- ✅ Windows路径（D:\path\to\file）
- ✅ Linux/Mac路径（/path/to/file）
- ⚠️ 相对路径 → 需要处理：转换为绝对路径
- ⚠️ 路径中包含特殊字符 → 需要测试

### 3. 网络和连接场景

#### 3.1 连接问题
- ✅ SSH连接失败 → **已处理**：返回错误
- ✅ 认证失败 → **已处理**：返回错误
- ✅ 连接超时 → **已处理**：使用ssh_timeout配置
- ✅ 网络中断 → **已处理**：下载失败时自动清理不完整文件
- ✅ SFTP连接断开 → **已处理**：finally块确保连接关闭

#### 3.2 传输问题
- ⚠️ 传输速度慢 → 需要优化：chunk size调整（当前使用默认）
- ✅ 传输中断 → **已处理**：异常时删除不完整文件
- ⚠️ 文件在传输中被修改 → 需要处理：MD5校验（可选功能）

### 4. 进度和性能场景

#### 4.1 进度回调
- ✅ 正常进度更新 → **已实现**
- ✅ 进度回调异常 → **已处理**：捕获异常，不影响下载
- ⚠️ 大文件夹计算总大小耗时 → 需要优化：异步计算或跳过（当前会阻塞）

#### 4.2 性能问题
- ⚠️ 大文件夹递归下载耗时 → 需要优化：当前串行下载
- ⚠️ 大量小文件下载 → 需要优化：当前逐个下载

### 5. 特殊文件场景

#### 5.1 文件类型
- ✅ 符号链接 → **已处理**：文件夹中跳过，根路径自动解析
- ✅ 硬链接 → **已处理**：作为普通文件下载
- ✅ 设备文件 → **已处理**：跳过（只下载普通文件和目录）
- ✅ 管道文件 → **已处理**：跳过

#### 5.2 文件名
- ⚠️ 特殊字符（空格、引号、换行等） → 需要测试：理论上已支持
- ⚠️ 长文件名 → 需要测试：Windows限制
- ✅ 隐藏文件（以.开头） → **已处理**：正常下载

### 6. 错误恢复场景

#### 6.1 部分下载
- ✅ 下载中断 → **已处理**：自动删除不完整文件
- ⚠️ 部分文件下载失败 → **部分处理**：文件夹下载时，单个文件失败会停止整个下载

#### 6.2 资源清理
- ✅ 连接关闭 → **已处理**：finally块确保关闭
- ✅ 不完整文件清理 → **已处理**：下载失败时自动清理

### 7. 并发场景

#### 7.1 多任务
- ⚠️ 同一文件被多次下载 → 需要处理：文件锁定
- ⚠️ 同一文件夹被多次下载 → 需要处理：目录锁定

### 8. 边界场景

#### 8.1 空值处理
- ✅ 空路径 → 已处理：参数验证
- ⚠️ 空文件夹 → 需要测试
- ⚠️ 0字节文件 → 需要测试

#### 8.2 极端情况
- ⚠️ 非常大的文件（>10GB） → 需要测试：内存和性能
- ⚠️ 非常深的目录结构 → 需要测试：递归深度限制
- ⚠️ 大量文件（>10000个） → 需要测试：性能

---

## 函数测试

### 概述

函数测试直接调用 `sftp_download()` 函数，覆盖了文件下载、文件夹下载、错误处理、特殊文件处理等多个方面。

### 使用方法

```bash
# 使用函数调用方式运行所有测试
python test_download_unified.py --mode function

# 指定服务器信息
python test_download_unified.py --mode function --server LP-8650-1 --ip 10.99.19.11 --port 22

# 运行特定测试
python test_download_unified.py --mode function --test test_file_download

# 保留临时测试目录（用于调试）
python test_download_unified.py --mode function --keep-temp
```

#### 命令行参数（统一脚本）

- `--mode MODE`: 测试模式，`function` 或 `api`（默认: function）
- `--server SERVER_NAME`: 服务器名称（默认: LP-8650-1）
- `--ip IP_ADDRESS`: 服务器IP地址（默认: 10.99.19.11）
- `--port PORT`: 端口号，22 或 9999（默认: 22）
- `--test TEST_NAME`: 运行特定测试（可选）
- `--keep-temp`: 保留临时测试目录（用于调试）

### 测试用例列表

1. **test_file_download** - 正常文件下载
2. **test_directory_download** - 文件夹下载
3. **test_file_not_exists** - 文件不存在错误处理
4. **test_directory_not_exists** - 文件夹不存在错误处理
5. **test_file_overwrite** - 文件覆盖测试
6. **test_local_path_conflict** - 本地路径冲突测试
7. **test_empty_directory** - 空文件夹下载
8. **test_progress_callback** - 进度回调测试
9. **test_symlink_handling** - 符号链接处理测试
10. **test_large_file_download** - 大文件下载测试
11. **test_disk_space_check** - 磁盘空间检查（需要手动测试）

### 测试覆盖范围

#### 文件下载测试
- 正常文件下载
- 大文件下载（>10MB）
- 文件覆盖测试

#### 文件夹下载测试
- 正常文件夹下载
- 嵌套文件夹下载
- 空文件夹下载

#### 错误处理测试
- 文件不存在
- 文件夹不存在
- 路径冲突处理

#### 特殊场景测试
- 符号链接处理
- 进度回调测试
- 磁盘空间检查

### 测试输出示例

```
============================================================
开始运行测试
============================================================
服务器: LP-8650-1 (10.99.19.11:22)
测试数量: 11
============================================================

运行测试: test_file_download
============================================================
测试1: 正常文件下载
============================================================
✓ 测试通过: 文件下载成功
  本地文件: C:\Users\...\download_test_xxx\test_file.txt

运行测试: test_directory_download
============================================================
测试2: 文件夹下载
============================================================
✓ 测试通过: 文件夹下载成功
  本地文件夹: C:\Users\...\download_test_xxx\Elog
  文件数量: 15
  总大小: 2.45 MB
  耗时: 3.21 秒
```

### 注意事项

1. **测试数据**：确保 `test_data/test_download/` 目录存在并包含测试文件
2. **服务器连接**：需要能够访问配置的服务器
3. **自动清理**：测试完成后会自动清理远程测试数据
4. **临时文件**：默认会清理临时测试目录，使用 `--keep-temp` 保留

---

## API测试

### 概述

API测试通过HTTP请求测试 `/api/download/stream` 端点，验证响应头、状态码、内容类型、文件内容等。

### 前置条件

1. **启动web_ui.py服务器**
   ```bash
   python web_ui.py
   ```
   服务器默认运行在 `http://127.0.0.1:5000`

2. **准备测试数据**
   确保 `test_data/test_download/` 目录存在并包含测试文件

### 使用方法

```bash
# 使用API调用方式运行所有测试（需要先启动web_ui.py）
python test_download_unified.py --mode api

# 指定API服务器URL
python test_download_unified.py --mode api --api-url http://127.0.0.1:5000

# 指定服务器信息
python test_download_unified.py --mode api --server LP-8650-1 --ip 10.99.19.11 --port 22

# 运行特定测试
python test_download_unified.py --mode api --test test_file_download

# 保留临时测试目录（用于调试）
python test_download_unified.py --mode api --keep-temp
```

#### 命令行参数（统一脚本）

- `--mode MODE`: 测试模式，`function` 或 `api`（默认: function）
- `--server SERVER_NAME`: 服务器名称（默认: LP-8650-1）
- `--ip IP_ADDRESS`: 服务器IP地址（默认: 10.99.19.11）
- `--port PORT`: 端口号，22 或 9999（默认: 22）
- `--api-url API_URL`: API服务器URL（默认: http://127.0.0.1:5000，仅API模式需要）
- `--test TEST_NAME`: 运行特定测试（可选）
- `--keep-temp`: 保留临时测试目录（用于调试）

### 测试用例列表

1. **test_api_file_download** - API正常文件下载
2. **test_api_directory_download** - API文件夹下载（ZIP格式）
3. **test_api_empty_directory** - API空文件夹下载
4. **test_api_file_not_exists** - API文件不存在错误处理
5. **test_api_directory_not_exists** - API文件夹不存在错误处理
6. **test_api_missing_parameters** - API参数缺失错误处理
7. **test_api_large_file_download** - API大文件下载
8. **test_api_special_characters_filename** - API特殊字符文件名下载（已跳过）
9. **test_api_empty_file_download** - API空文件下载
10. **test_api_content_disposition_header** - API Content-Disposition头验证
11. **test_api_streaming_response** - API流式响应验证
12. **test_api_zip_structure** - API ZIP文件结构验证

### 测试场景详解

#### 1. 文件下载测试

##### test_api_file_download
- **目的**: 验证API可以正常下载文件
- **验证项**:
  - HTTP状态码为200
  - Content-Disposition头包含attachment和filename
  - Content-Type为application/octet-stream
  - 下载的文件存在且不为空

##### test_api_large_file_download
- **目的**: 验证API可以下载大文件（5MB）
- **验证项**:
  - 流式下载正常工作
  - 文件大小正确
  - 下载速度统计

##### test_api_empty_file_download
- **目的**: 验证API可以下载空文件（0字节）
- **验证项**:
  - 下载成功
  - 文件大小为0

##### test_api_special_characters_filename
- **目的**: 验证API可以处理特殊字符文件名
- **状态**: ⚠️ 已跳过（特殊字符文件名在某些环境下可能导致超时）

#### 2. 文件夹下载测试

##### test_api_directory_download
- **目的**: 验证API可以下载文件夹（ZIP格式）
- **验证项**:
  - HTTP状态码为200
  - Content-Type为application/zip
  - Content-Disposition头包含.zip
  - ZIP文件可以正常解压
  - ZIP文件包含预期文件

##### test_api_empty_directory
- **目的**: 验证API可以下载空文件夹
- **验证项**:
  - 下载成功
  - ZIP文件生成（可能为空）

##### test_api_zip_structure
- **目的**: 验证ZIP文件结构正确
- **验证项**:
  - ZIP文件完整性
  - 文件路径结构正确
  - 嵌套目录结构正确

#### 3. 错误处理测试

##### test_api_file_not_exists
- **目的**: 验证文件不存在时的错误处理
- **验证项**:
  - HTTP状态码为400
  - 响应为JSON格式
  - 包含错误信息

##### test_api_directory_not_exists
- **目的**: 验证文件夹不存在时的错误处理
- **验证项**:
  - HTTP状态码为400
  - 响应为JSON格式
  - 包含错误信息

##### test_api_missing_parameters
- **目的**: 验证参数缺失/无效时的错误处理
- **测试用例**:
  - 所有参数缺失
  - 缺少server_ip和port
  - 缺少port
  - 缺少remote_path
  - server_name为空
  - server_ip为空
  - 端口非法（非22或9999）
  - remote_path为空

#### 4. HTTP响应验证

##### test_api_content_disposition_header
- **目的**: 验证Content-Disposition头格式
- **验证项**:
  - 包含attachment
  - 包含filename
  - Content-Type正确

##### test_api_streaming_response
- **目的**: 验证流式响应功能
- **验证项**:
  - 响应支持流式读取
  - 可以分块读取数据

### API端点说明

#### `/api/download/stream`

**请求方法**: GET

**请求参数**:
- `server_name` (必需): 服务器名称
- `server_ip` (必需): 服务器IP地址
- `port` (必需): 端口号（22 或 9999）
- `remote_path` (必需): 远程文件/文件夹路径

**响应**:
- **成功** (200):
  - 文件：`Content-Type: application/octet-stream`
  - 文件夹：`Content-Type: application/zip`
  - `Content-Disposition: attachment; filename="..."` 头
  - 流式响应体

- **错误** (400/500):
  - `Content-Type: application/json`
  - JSON格式：`{"ok": false, "error": "错误信息"}`

**示例**:
```bash
curl "http://127.0.0.1:5000/api/download/stream?server_name=LP-8650-1&server_ip=10.99.19.11&port=22&remote_path=/opt/data/test.txt" -o test.txt
```

### 注意事项

1. **API服务器必须运行**: 测试前确保 `web_ui.py` 正在运行
2. **网络连接**: 确保可以访问远程服务器（SSH/SFTP）
3. **测试数据**: 确保 `test_data/test_download/` 目录存在
4. **权限**: 确保有权限在远程服务器上创建和删除测试目录
5. **时间**: 大文件下载测试可能需要较长时间

---

## 快速参考

### 函数调用方式

```python
from check_rack_status import sftp_download

# 下载文件
ok, info = sftp_download(
    server_name="LP-8650-1",
    ip="10.99.19.11",
    port=22,
    remote_path="/opt/data/file.txt",
    local_path="C:/downloads/file.txt",
    progress_callback=lambda transferred, total: print(f"进度: {transferred}/{total}")
)

# 下载文件夹
ok, info = sftp_download(
    server_name="LP-8650-1",
    ip="10.99.19.11",
    port=22,
    remote_path="/opt/data/folder",
    local_path="C:/downloads/folder"
)
```

### HTTP API方式

```javascript
// JavaScript示例
const downloadUrl = `/api/download/stream?server_name=LP-8650-1&server_ip=10.99.19.11&port=22&remote_path=${encodeURIComponent('/opt/data/file.txt')}`;
window.open(downloadUrl, '_blank');
```

```bash
# curl示例
curl "http://127.0.0.1:5000/api/download/stream?server_name=LP-8650-1&server_ip=10.99.19.11&port=22&remote_path=/opt/data/file.txt" -o file.txt
```

### 运行测试

```bash
# 函数测试
python test_download_unified.py --mode function

# API测试（需要先启动web_ui.py）
python web_ui.py  # 在另一个终端
python test_download_unified.py --mode api
```

### 相关文档

- `docs/download_scenarios.md` - 详细场景分析（本文档已整合）
- `docs/test_download_automated_README.md` - 函数测试详细说明（本文档已整合）
- `docs/test_download_api_automated_README.md` - API测试详细说明（本文档已整合）
- `docs/test_scripts_summary.md` - 所有测试脚本总结

---

**最后更新**: 2024年

