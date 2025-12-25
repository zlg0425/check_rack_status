# 项目目录结构规范化方案

## 当前项目分析

### 核心功能模块
1. **SSH/SFTP连接管理** (`check_rack_status.py`)
   - 服务器状态检查
   - SSH连接管理
   - SFTP文件上传
   - 远程命令执行
   - MD5计算（本地/远程）
   - FOTA升级执行

2. **Web应用** (`web_ui.py`)
   - Flask Web服务器
   - RESTful API接口
   - 前端HTML/CSS/JavaScript
   - 任务管理（上传、FOTA、批量FOTA）
   - 实时进度推送（SSE）

3. **配置管理** (`config.json`)
   - 服务器列表
   - SSH密钥映射
   - 认证模式配置
   - FOTA配置

### 当前问题
- ❌ 代码文件散落在根目录
- ❌ 日志文件在根目录（`*.log`）
- ❌ 密钥文件在根目录（安全风险）
- ❌ 测试脚本在根目录
- ❌ 文档分散（部分在`reports/`，部分在根目录）
- ❌ 数据文件在根目录（`*.json`）
- ❌ 缺少项目文档（README、requirements.txt等）

## 规范化目录结构方案

```
check_rack_status/
├── README.md                    # 项目说明文档
├── requirements.txt             # Python依赖列表
├── .gitignore                   # Git忽略文件
├── run.py                       # 应用入口文件
│
├── src/                         # 源代码目录
│   ├── __init__.py
│   ├── core/                    # 核心功能模块
│   │   ├── __init__.py
│   │   ├── ssh_manager.py       # SSH/SFTP连接管理（从check_rack_status.py拆分）
│   │   ├── file_upload.py       # 文件上传功能
│   │   ├── fota_manager.py      # FOTA升级管理
│   │   ├── md5_calculator.py    # MD5计算工具
│   │   └── server_checker.py    # 服务器状态检查
│   │
│   ├── web/                     # Web应用模块
│   │   ├── __init__.py
│   │   ├── app.py               # Flask应用主文件（从web_ui.py拆分）
│   │   ├── routes/              # API路由
│   │   │   ├── __init__.py
│   │   │   ├── status.py        # 状态查询API
│   │   │   ├── upload.py         # 文件上传API
│   │   │   ├── fota.py           # FOTA升级API
│   │   │   └── batch_fota.py     # 批量FOTA API
│   │   ├── templates/           # HTML模板（如果需要分离）
│   │   │   └── index.html
│   │   └── static/              # 静态文件（CSS/JS，如果需要分离）
│   │
│   ├── config/                  # 配置管理模块
│   │   ├── __init__.py
│   │   ├── loader.py            # 配置加载器
│   │   └── settings.py          # 配置常量
│   │
│   └── utils/                   # 工具函数
│       ├── __init__.py
│       ├── logger.py            # 日志工具
│       └── validators.py        # 验证工具
│
├── config/                      # 配置文件目录
│   ├── config.json              # 主配置文件
│   └── config.example.json      # 配置示例文件
│
├── keys/                        # SSH密钥文件目录（.gitignore）
│   ├── 8650_rsa2048
│   ├── 8295_rsa2048
│   └── .gitkeep                 # 保持目录存在
│
├── logs/                        # 日志文件目录（.gitignore）
│   ├── fota.log
│   ├── backend.log
│   ├── paramiko.log
│   └── .gitkeep
│
├── data/                        # 数据文件目录
│   ├── fota_timing_stats.json   # FOTA统计
│   └── .gitkeep
│
├── tests/                       # 测试文件目录
│   ├── __init__.py
│   ├── test_ssh_manager.py
│   ├── test_fota_manager.py
│   ├── test_file_upload.py
│   ├── remote_exec_test.py      # 远程执行测试（迁移）
│   └── test_ucm.py              # UCM测试（迁移）
│
├── scripts/                     # 工具脚本目录
│   └── analyze_fota_performance.py  # 性能分析脚本（如果存在）
│
└── docs/                        # 文档目录
    ├── README.md                # 项目说明（或移动到根目录）
    ├── API.md                   # API文档
    ├── DEPLOYMENT.md            # 部署文档
    ├── reports/                 # 分析报告（迁移）
    │   ├── performance_analysis.md
    │   ├── concurrent_users_analysis.md
    │   ├── streaming_upload_performance_analysis.md
    │   └── memory_protection_proposal.md
    └── directory_structure_plan.md  # 本文档
```

## 模块拆分方案

### 1. `check_rack_status.py` → `src/core/` 模块

#### `ssh_manager.py`
- `create_transport()` - 创建SSH传输
- `run_remote_command()` - 执行远程命令
- `check_ssh_login()` - SSH登录检查
- `resolve_auth_mode()` - 解析认证模式
- `resolve_key()` - 解析密钥路径

#### `file_upload.py`
- `sftp_upload()` - SFTP上传
- `ensure_remote_dir()` - 确保远程目录存在

#### `fota_manager.py`
- `run_ucm_with_log()` - 执行UCM升级
- `remote_exists()` - 检查远程文件是否存在
- `remote_remove()` - 删除远程文件
- `remote_md5()` - 计算远程MD5

#### `md5_calculator.py`
- `md5_bytes()` - 计算本地文件MD5
- `md5_stream()` - 计算流MD5
- `md5_bytes_sampled()` - 采样MD5计算
- `md5_stream_sampled()` - 流采样MD5
- `create_md5_calculating_stream()` - 创建MD5计算流
- `create_tee_stream()` - 创建Tee流

#### `server_checker.py`
- `run_checks_once()` - 执行一次检查
- `check_port()` - 端口检查
- `load_config()` - 加载配置（或移到config模块）

### 2. `web_ui.py` → `src/web/` 模块

#### `app.py`
- Flask应用初始化
- 全局变量和锁
- 日志工具函数

#### `routes/status.py`
- `/api/status` - 状态查询接口

#### `routes/upload.py`
- `/api/upload` - 文件上传接口
- `/api/upload/progress/<task_id>` - 上传进度接口
- `sftp_upload_with_cancel()` - 带取消功能的上传

#### `routes/fota.py`
- `/api/fota` - 单服务器FOTA接口
- `/api/fota/progress/<task_id>` - FOTA进度接口
- FOTA任务管理逻辑

#### `routes/batch_fota.py`
- `/api/batch-fota` - 批量FOTA接口
- `/api/batch-fota/progress/<batch_id>` - 批量进度接口
- `/api/batch-fota/cancel/<batch_id>` - 取消批量任务接口

### 3. `config/` 模块

#### `loader.py`
- `load_config()` - 加载配置文件
- 配置验证

#### `settings.py`
- 配置常量定义
- 默认值

## 迁移步骤

### 阶段1：创建目录结构
1. 创建所有新目录
2. 创建 `__init__.py` 文件
3. 创建 `.gitignore` 文件

### 阶段2：迁移配置文件
1. 移动 `config.json` → `config/config.json`
2. 创建 `config/config.example.json`
3. 更新代码中的配置路径

### 阶段3：迁移密钥和日志
1. 移动密钥文件 → `keys/`
2. 移动日志文件 → `logs/`
3. 更新代码中的路径

### 阶段4：拆分核心模块
1. 拆分 `check_rack_status.py` → `src/core/` 各模块
2. 更新导入路径
3. 测试功能是否正常

### 阶段5：拆分Web模块
1. 拆分 `web_ui.py` → `src/web/` 各模块
2. 更新导入路径
3. 测试Web功能是否正常

### 阶段6：迁移测试和文档
1. 移动测试文件 → `tests/`
2. 移动文档 → `docs/`
3. 创建 `README.md` 和 `requirements.txt`

### 阶段7：创建入口文件
1. 创建 `run.py` 作为应用入口
2. 更新启动方式

## 注意事项

1. **向后兼容**：保持API接口不变，只重构内部结构
2. **逐步迁移**：分阶段进行，每个阶段完成后测试
3. **版本控制**：使用Git分支管理重构过程
4. **文档更新**：及时更新相关文档

