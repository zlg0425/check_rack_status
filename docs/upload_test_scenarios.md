# 上传功能测试场景清单

本文档列出了所有上传和批量上传相关的功能、API和测试场景。

## 一、核心功能函数

### 1. `sftp_upload` (check_rack_status.py)
**功能**: 单文件上传核心函数
**参数**:
- `server_name`, `ip`, `port`: 服务器信息
- `target_dir`, `filename`: 目标路径
- `data` / `stream`: 文件数据或流
- `file_size`: 文件大小
- `progress_callback`: 进度回调
- `check_disk_space`: 是否检查磁盘空间
- `check_file_exists`: 是否检查文件存在

**测试场景**:
- ✅ 正常文件上传
- ✅ 大文件上传（>100MB）
- ✅ 文件覆盖
- ✅ 路径验证（防止路径遍历）
- ✅ 磁盘空间检查
- ✅ 文件存在检查
- ✅ 特殊字符文件名
- ✅ 空文件上传
- ✅ 隐藏文件上传
- ✅ 进度回调
- ⚠️ 流式上传（stream参数）
- ⚠️ 数据上传（data参数）
- ⚠️ 认证模式（key/none）
- ⚠️ 动态chunk size调整

### 2. `do_batch_upload_process` (web_ui.py)
**功能**: 批量上传单个任务的进程函数
**特点**: 使用multiprocessing.Process独立运行

**测试场景**:
- ✅ 进程独立运行
- ✅ 进度更新到共享字典
- ✅ 状态更新（pending -> uploading -> done/error）
- ✅ 取消机制
- ✅ 异常处理
- ⚠️ 配置加载（子进程需要load_config）

### 3. `sftp_upload_with_cancel` (web_ui.py)
**功能**: 支持取消的上传包装函数
**测试场景**:
- ⚠️ 取消标志检查
- ⚠️ 取消异常处理

## 二、API端点

### 1. `/api/upload` (POST)
**功能**: 单文件上传API
**参数**: `server_name`, `server_ip`, `port`, `target_dir`, `file`

**测试场景**:
- ✅ 正常上传
- ✅ 参数验证（缺失参数、非法端口）
- ✅ 文件验证（未选择文件）
- ✅ 临时文件处理
- ✅ 流式上传
- ✅ 任务ID生成
- ⚠️ 临时文件磁盘空间检查
- ⚠️ 临时文件清理
- ⚠️ 异常处理

### 2. `/api/upload/progress/<task_id>` (GET, SSE)
**功能**: 单文件上传进度查询（SSE）
**测试场景**:
- ✅ 进度更新推送
- ✅ 心跳机制（每0.5秒）
- ✅ 状态变化推送
- ✅ 任务不存在处理
- ✅ 任务完成推送
- ⚠️ SSE连接保持活跃
- ⚠️ 连接断开处理

### 3. `/api/batch-upload` (POST)
**功能**: 批量上传API
**参数**: `port`, `servers` (JSON), `files` (多文件), `target_dir`

**测试场景**:
- ✅ 基本批量上传（单文件多服务器）
- ✅ 多文件批量上传
- ✅ 多服务器批量上传
- ✅ 参数验证（端口、服务器列表、文件列表）
- ✅ 临时文件处理（多个文件）
- ✅ 任务创建（每个文件×每个服务器）
- ✅ 进程启动（multiprocessing.Process）
- ✅ Manager.dict初始化
- ⚠️ 临时文件磁盘空间检查（总大小）
- ⚠️ 临时文件清理（异常时）
- ⚠️ 空文件列表处理
- ⚠️ 空服务器列表处理

### 4. `/api/batch-upload/progress/<batch_id>` (GET, SSE)
**功能**: 批量上传进度查询（SSE）
**测试场景**:
- ✅ 进度更新推送
- ✅ 心跳机制（每0.5秒，强制每0.1秒检查）
- ✅ 任务状态更新
- ✅ 完成确认（3次发送）
- ✅ `event: complete`事件类型
- ✅ 任务不存在处理
- ✅ 任务已取消处理
- ✅ Manager.dict转换（dict()）
- ⚠️ SSE连接保持活跃
- ⚠️ 100%进度但状态延迟的处理
- ⚠️ 所有任务完成检测

### 5. `/api/batch-upload/status/<batch_id>` (GET)
**功能**: 批量上传状态查询（主动查询API）
**测试场景**:
- ✅ 状态查询
- ✅ 任务列表返回
- ✅ 进度和状态信息
- ✅ 任务不存在处理
- ✅ 已取消任务处理
- ⚠️ Manager.dict转换

### 6. `/api/batch-upload/cancel/<batch_id>` (POST)
**功能**: 取消批量上传
**测试场景**:
- ✅ 取消标志设置
- ✅ 临时文件清理
- ✅ 任务不存在处理
- ✅ 已取消任务处理
- ⚠️ 进程取消检查（在progress_cb中）

## 三、辅助函数

### 1. `validate_remote_path` (check_rack_status.py)
**功能**: 验证远程路径，防止路径遍历攻击
**测试场景**:
- ✅ 正常路径
- ✅ 路径遍历攻击（../）
- ✅ Windows路径遍历（..\\）
- ✅ 特殊字符处理

### 2. `check_remote_disk_space` (check_rack_status.py)
**功能**: 检查远程磁盘空间
**测试场景**:
- ✅ 磁盘空间充足
- ✅ 磁盘空间不足
- ✅ 超大文件检查
- ⚠️ statvfs调用异常

### 3. `check_remote_file_exists` (check_rack_status.py)
**功能**: 检查远程文件是否存在
**测试场景**:
- ✅ 文件存在
- ✅ 文件不存在
- ✅ 目录存在（应返回错误）
- ⚠️ 权限问题

### 4. `ensure_remote_dir` (check_rack_status.py)
**功能**: 确保远程目录存在
**测试场景**:
- ✅ 目录已存在
- ✅ 目录不存在（自动创建）
- ✅ 嵌套目录创建
- ⚠️ 权限不足

### 5. `get_batch_upload_manager` (web_ui.py)
**功能**: 获取或创建批量上传管理器
**测试场景**:
- ✅ 延迟初始化
- ✅ Windows兼容性（spawn方法）
- ✅ Manager重用

## 四、前端功能

### 1. 单文件上传 (`uploadFile()`)
**测试场景**:
- ✅ 文件选择
- ✅ 表单提交
- ✅ 进度显示（SSE）
- ✅ 完成提示
- ✅ 错误处理
- ⚠️ 文件已存在提示

### 2. 批量上传 (`startBatchUpload()`)
**测试场景**:
- ✅ 多文件选择
- ✅ 多服务器选择
- ✅ 任务列表初始化
- ✅ SSE进度监听
- ✅ `complete`事件监听
- ✅ 双重确认机制
- ✅ 超时检查
- ✅ 主动查询机制
- ✅ 100%进度特殊处理
- ✅ 错误汇总显示
- ⚠️ 取消功能
- ⚠️ 连接错误处理

## 五、测试覆盖情况

### 已有测试（test_upload_automated.py）
1. ✅ test_single_file_upload - 单个文件上传
2. ✅ test_large_file_upload - 大文件上传
3. ✅ test_file_overwrite - 文件覆盖上传
4. ✅ test_path_validation - 路径验证
5. ✅ test_disk_space_check - 磁盘空间检查
6. ✅ test_file_exists_check - 文件存在检查
7. ✅ test_special_filename - 特殊字符文件名
8. ✅ test_empty_file - 空文件上传
9. ✅ test_hidden_file - 隐藏文件上传
10. ✅ test_progress_callback - 进度回调

### 已有测试（test_batch_upload_automated.py）
1. ✅ test_batch_upload_basic - 基本批量上传
2. ✅ test_batch_upload_multiple_files - 多文件批量上传
3. ✅ test_batch_upload_multiple_servers - 多服务器批量上传
4. ✅ test_batch_upload_empty_file - 空文件批量上传
5. ✅ test_batch_upload_large_file - 大文件批量上传
6. ✅ test_batch_upload_invalid_params - 无效参数测试
7. ✅ test_batch_upload_progress_tracking - 进度跟踪测试

### 缺失的测试场景

#### API测试
1. ⚠️ `/api/upload` - 临时文件磁盘空间不足
2. ⚠️ `/api/upload` - 临时文件创建失败
3. ⚠️ `/api/upload/progress/<task_id>` - SSE连接断开
4. ⚠️ `/api/batch-upload` - 临时文件总大小超限
5. ⚠️ `/api/batch-upload/progress/<batch_id>` - SSE连接断开
6. ⚠️ `/api/batch-upload/status/<batch_id>` - 各种状态查询
7. ⚠️ `/api/batch-upload/cancel/<batch_id>` - 取消功能测试

#### 功能测试
1. ⚠️ 流式上传（stream参数）vs 数据上传（data参数）
2. ⚠️ 认证模式测试（key vs none）
3. ⚠️ 动态chunk size调整
4. ⚠️ 进程取消机制
5. ⚠️ Manager.dict序列化
6. ⚠️ 并发上传测试
7. ⚠️ 网络中断恢复
8. ⚠️ 超大文件上传（>1GB）
9. ⚠️ 大量小文件批量上传（>100个）
10. ⚠️ 嵌套目录创建

#### 边界测试
1. ⚠️ 文件名长度限制（>255字符）
2. ⚠️ 路径长度限制
3. ⚠️ 并发连接数限制
4. ⚠️ 内存占用测试
5. ⚠️ 临时文件数量限制

## 六、测试文件规划

### 1. test_upload_api_automated.py（新增）
**目的**: 测试单文件上传API端点
**测试项**:
- API参数验证
- API正常上传
- API进度查询（SSE）
- API错误处理
- API临时文件处理

### 2. test_batch_upload_api_automated.py（新增）
**目的**: 测试批量上传API端点
**测试项**:
- API参数验证
- API批量上传
- API进度查询（SSE）
- API状态查询
- API取消功能
- API错误处理

### 3. test_upload_edge_cases.py（新增）
**目的**: 测试边界情况和极端场景
**测试项**:
- 超大文件上传
- 大量小文件批量上传
- 文件名长度限制
- 路径长度限制
- 并发上传
- 网络中断恢复

### 4. test_upload_integration.py（新增）
**目的**: 集成测试，测试完整流程
**测试项**:
- 端到端上传流程
- 前端+后端集成
- SSE连接稳定性
- 进度更新准确性

