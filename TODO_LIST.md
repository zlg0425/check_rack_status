# 项目重构待办事项

## ✅ 已完成

### 1. 基础结构
- [x] 创建新的目录结构（app/, core/, frontend/, tests/）
- [x] 创建所有必要的 __init__.py 文件
- [x] 创建应用工厂模式（app/__init__.py）

### 2. SSH 模块迁移
- [x] 迁移 SSH 适配器到 `core/ssh/adapter.py`
- [x] 创建兼容层 `ssh_connection_adapter.py`
- [x] 创建 `core/ssh/transport.py` 兼容层

### 3. 核心模块兼容层
- [x] `core/sftp/operations.py` - SFTP 操作兼容层
- [x] `core/fota/manager.py` - FOTA 管理兼容层
- [x] `core/monitoring/checker.py` - 监控检查兼容层
- [x] `app/utils/config.py` - 配置管理兼容层
- [x] `app/utils/helpers.py` - 工具函数兼容层

### 4. 路由模块迁移
- [x] `app/routes/status.py` - 状态路由（已迁移）
- [x] `app/routes/upload.py` - 上传路由（基础已迁移）
- [x] `app/routes/download.py` - 下载路由（已迁移）
- [ ] `app/routes/fota.py` - FOTA 路由（待迁移）
- [ ] `app/routes/terminal.py` - 终端路由（待迁移）

## ⏳ 进行中

### 5. 完善上传路由
- [x] 单文件上传路由
- [x] 上传进度 SSE 路由
- [ ] 批量上传路由（需要迁移 `do_batch_upload_process` 函数）
- [ ] 批量上传进度 SSE 路由
- [ ] 批量上传状态查询路由
- [ ] 批量上传取消路由
- [ ] 文件夹上传路由
- [ ] 文件夹上传进度 SSE 路由

### 6. 测试脚本更新
- [ ] 更新 `test_upload_unified.py` 支持新模块路径
- [ ] 更新 `test_download_unified.py` 支持新模块路径
- [ ] 更新 `test_terminal_unified.py` 支持新模块路径
- [ ] 确保所有测试脚本向后兼容

## 📋 待完成

### 7. FOTA 路由迁移
- [ ] 单服务器 FOTA 路由
- [ ] FOTA 进度 SSE 路由
- [ ] 批量 FOTA 路由
- [ ] 批量 FOTA 进度 SSE 路由
- [ ] 批量 FOTA 状态查询路由
- [ ] 批量 FOTA 取消路由

### 8. 终端路由迁移
- [ ] SocketIO 事件处理器迁移
- [ ] 终端连接处理
- [ ] 终端输入处理
- [ ] 终端断开处理

### 9. 应用工厂完善
- [ ] 在应用工厂中注册所有路由
- [ ] 注册 SocketIO 事件处理器
- [ ] 测试应用工厂启动

### 10. 前端代码分离
- [ ] 提取 HTML 模板到 `frontend/templates/`
- [ ] 提取 CSS 到 `frontend/static/css/`
- [ ] 提取 JavaScript 到 `frontend/static/js/`

### 11. 实际代码迁移
- [ ] 将 `check_rack_status.py` 中的函数实际移动到对应模块
- [ ] 更新所有导入路径
- [ ] 移除临时兼容层

### 12. 测试和验证
- [ ] 运行所有测试脚本
- [ ] 验证所有功能正常
- [ ] 性能测试
- [ ] 文档更新

## 🔧 兼容性保证

### 当前策略
1. **兼容层** - 所有模块都有兼容层，确保现有导入仍能工作
2. **渐进迁移** - 逐步迁移，不一次性改变所有代码
3. **功能保持** - 确保迁移过程中功能不受影响

### 验证方法
```python
# 检查 SSH 适配器
from ssh_connection_adapter import SSHConnectionAdapter  # ✅ 正常

# 检查核心功能
from check_rack_status import load_config, sftp_upload  # ✅ 正常

# 检查新模块
from core.ssh import SSHConnectionAdapter  # ✅ 正常
from core.sftp import sftp_upload  # ✅ 正常
```

## 📝 下一步优先级

1. **高优先级**
   - 完善上传路由（批量上传、文件夹上传）
   - 更新测试脚本支持新模块路径
   - 迁移 FOTA 路由

2. **中优先级**
   - 迁移终端路由和 SocketIO 处理器
   - 在应用工厂中注册所有路由
   - 测试应用工厂启动

3. **低优先级**
   - 前端代码分离
   - 实际代码迁移（移除兼容层）
   - 文档完善

