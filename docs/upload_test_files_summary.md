# 上传功能测试文件总结

本文档总结了所有上传和批量上传相关的测试文件及其测试场景。

## 推荐使用：统一测试脚本

### test_upload_unified.py（推荐）
**类型**: 统一测试脚本，整合所有上传测试
**目的**: 通过参数选择不同的测试模式和类型
**特点**:
- 整合了所有上传相关测试
- 支持函数调用和API调用两种模式
- 支持单文件上传和批量上传测试
- 统一的接口和代码复用

**用法**:
```bash
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
```

**测试项**:
- **函数模式** (--mode function): 10个测试
  - test_single_file_upload - 单个文件上传
  - test_large_file_upload - 大文件上传
  - test_file_overwrite - 文件覆盖上传
  - test_path_validation - 路径验证
  - test_disk_space_check - 磁盘空间检查
  - test_file_exists_check - 文件存在检查
  - test_special_filename - 特殊字符文件名
  - test_empty_file - 空文件上传
  - test_hidden_file - 隐藏文件上传
  - test_progress_callback - 进度回调

- **API模式 - 单文件上传** (--mode api --type single): 4个测试
  - test_single_upload_basic - 基本API上传功能
  - test_single_upload_invalid_params - API参数验证
  - test_single_upload_progress_sse - API进度查询（SSE）
  - test_single_upload_large_file - 大文件API上传

- **API模式 - 批量上传** (--mode api --type batch): 4个测试
  - test_batch_upload_basic - 基本批量上传
  - test_batch_upload_multiple_files - 多文件批量上传
  - test_batch_upload_status - 批量上传状态查询API
  - test_batch_upload_cancel - 批量上传取消API

## 独立测试文件（已整合到统一脚本）

### 1. test_upload_automated.py
**状态**: 已整合到 `test_upload_unified.py --mode function`
**类型**: 功能测试（直接调用函数）
**目的**: 测试 `sftp_upload` 核心函数和辅助函数

### 2. test_upload_api_automated.py
**状态**: 已整合到 `test_upload_unified.py --mode api --type single`
**类型**: API测试
**目的**: 测试 `/api/upload` 和 `/api/upload/progress/<task_id>` 端点

### 3. test_batch_upload_automated.py
**状态**: 已整合到 `test_upload_unified.py --mode api --type batch`
**类型**: 功能测试（通过API）
**目的**: 测试批量上传的完整流程

### 4. test_batch_upload_api_automated.py
**状态**: 已整合到 `test_upload_unified.py --mode api --type batch`
**类型**: API测试
**目的**: 测试批量上传相关的所有API端点

## API端点测试覆盖

### 单文件上传API
- ✅ `/api/upload` (POST) - 基本上传、参数验证、错误处理
- ✅ `/api/upload/progress/<task_id>` (GET, SSE) - 进度查询、SSE推送、任务不存在处理

### 批量上传API
- ✅ `/api/batch-upload` (POST) - 基本批量上传、参数验证（已包含在test_batch_upload_automated.py）
- ✅ `/api/batch-upload/progress/<batch_id>` (GET, SSE) - 进度查询、SSE推送、complete事件
- ✅ `/api/batch-upload/status/<batch_id>` (GET) - 状态查询、任务列表、进度信息
- ✅ `/api/batch-upload/cancel/<batch_id>` (POST) - 取消功能、临时文件清理

## 测试场景覆盖

### 文件相关
- ✅ 正常文件上传
- ✅ 大文件上传（>100MB）
- ✅ 空文件上传
- ✅ 特殊字符文件名
- ✅ 隐藏文件上传
- ✅ 文件覆盖

### 路径和验证
- ✅ 路径验证（防止路径遍历）
- ✅ 磁盘空间检查
- ✅ 文件存在检查

### API功能
- ✅ 参数验证
- ✅ 错误处理
- ✅ 进度查询（SSE）
- ✅ 状态查询
- ✅ 取消功能

### 批量上传
- ✅ 单文件多服务器
- ✅ 多文件单服务器
- ✅ 多文件多服务器
- ✅ 进度跟踪
- ✅ 错误汇总

## 缺失的测试场景（待补充）

### 边界情况测试
- ⚠️ 超大文件上传（>1GB）
- ⚠️ 大量小文件批量上传（>100个）
- ⚠️ 文件名长度限制（>255字符）
- ⚠️ 路径长度限制
- ⚠️ 并发上传测试

### 异常情况测试
- ⚠️ 网络中断恢复
- ⚠️ 连接超时处理
- ⚠️ 临时文件磁盘空间不足
- ⚠️ 临时文件创建失败
- ⚠️ SSE连接断开处理

### 性能测试
- ⚠️ 内存占用测试
- ⚠️ 并发连接数限制
- ⚠️ 上传速度测试

## 运行测试（推荐使用统一脚本）

### 1. 启动Web服务（API模式需要）
```bash
python web_ui.py
```

### 2. 运行统一测试脚本
```bash
# 函数模式：直接调用 sftp_upload 函数
python test_upload_unified.py --mode function

# API模式：通过HTTP请求测试API端点
python test_upload_unified.py --mode api

# API模式 - 只测试单文件上传
python test_upload_unified.py --mode api --type single

# API模式 - 只测试批量上传
python test_upload_unified.py --mode api --type batch

# 运行特定测试
python test_upload_unified.py --mode function --test test_single_file_upload
python test_upload_unified.py --mode api --test test_single_upload_basic

# 指定服务器和API URL
python test_upload_unified.py --mode api --server LP-8650-1 --ip 10.99.19.11 --port 22 --api-url http://127.0.0.1:5000
```

### 3. 使用独立测试文件（不推荐，已整合）
如果需要使用独立的测试文件，可以参考各文件的用法说明。

## 测试数据准备

测试需要以下测试文件（位于 `test_data/test_upload/`）:
- `file1.txt` - 普通文本文件
- `file2.txt` - 普通文本文件
- `large_file.bin` - 大文件（>100MB，可选）
- `small_file.bin` - 小二进制文件
- `empty_file.txt` - 空文件
- `special_chars_file_测试.txt` - 特殊字符文件名
- `.hidden_file` - 隐藏文件

## 注意事项

1. **Web服务必须运行**: API测试需要 `web_ui.py` 运行在 `http://localhost:5000`（或使用 `--base-url` 指定）
2. **服务器配置**: 测试需要有效的服务器配置（`config.json`）
3. **测试数据清理**: 所有测试会自动清理远程测试数据
4. **临时文件**: 测试会创建临时文件，测试完成后自动清理

## 测试结果

所有测试脚本都会输出：
- 测试进度
- 测试结果（通过/失败/跳过）
- 测试摘要（总计、通过、失败、跳过）
- 详细结果列表

测试失败时会返回非零退出码，可用于CI/CD集成。

