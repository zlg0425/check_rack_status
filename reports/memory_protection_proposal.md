# 内存保护方案设计

## 问题分析

当前代码将整个文件加载到内存中：
- 上传：`data = file.read()` (第477行)
- FOTA：`data = file.read()` (第571行)
- 批量FOTA：`data = file.read()` (第844行)

**30个并发用户的内存问题：**
- 单个FOTA：100MB文件 × 30用户 = 3GB
- 批量FOTA（10台服务器）：100MB文件 × 30用户 × 10服务器 = 30GB
- 这会导致系统内存耗尽

## 解决方案选项

### 方案1：配置化限制（推荐）

**优点：**
- 灵活，可以根据实际需求调整
- 可以区分FOTA和普通上传
- 不影响现有功能

**实现：**
- 在 `config.json` 中添加配置项
- 默认值较大（如2GB），不限制正常使用
- 可以根据服务器内存动态调整

**配置示例：**
```json
{
  "max_file_size_upload": 2147483648,  // 2GB，普通上传限制
  "max_file_size_fota": 1073741824,   // 1GB，FOTA限制（更严格，因为批量FOTA会复制）
  "max_file_size_batch_fota": 536870912, // 500MB，批量FOTA限制（最严格）
  "enable_memory_protection": true,    // 是否启用内存保护
  "max_concurrent_upload_memory": 10737418240  // 10GB，所有并发上传的总内存限制
}
```

### 方案2：动态限制（更智能）

**优点：**
- 根据当前系统内存和并发任务数动态调整
- 不会硬性拒绝，而是给出警告
- 更智能的资源管理

**实现：**
- 监控当前内存使用
- 监控当前并发任务数
- 动态计算可用内存
- 超过阈值时给出警告，但不拒绝（如果系统资源充足）

### 方案3：软限制（最灵活）

**优点：**
- 不硬性拒绝，只给出警告
- 用户可以自行决定是否继续
- 完全不影响现有功能

**实现：**
- 检查文件大小
- 如果超过阈值，返回警告信息
- 前端显示警告，用户可以选择继续或取消
- 如果系统资源充足，允许继续

## 推荐方案：方案1 + 方案3组合

### 实现策略

1. **配置化限制**（方案1）
   - 在 `config.json` 中添加可配置的限制
   - 默认值较大，不限制正常使用

2. **软限制检查**（方案3）
   - 检查文件大小
   - 超过限制时：
     - 如果当前并发任务少，给出警告但允许继续
     - 如果当前并发任务多，拒绝请求并提示稍后重试

3. **动态内存监控**（方案2的部分功能）
   - 监控当前并发任务数和总内存使用
   - 根据实际情况调整限制

## 具体实现建议

### 1. 添加配置项到 config.json

```json
{
  "memory_protection": {
    "enabled": true,
    "max_file_size_upload_mb": 2048,      // 2GB，普通上传
    "max_file_size_fota_mb": 1024,        // 1GB，单个FOTA
    "max_file_size_batch_fota_mb": 512,   // 500MB，批量FOTA
    "max_total_memory_mb": 10240,         // 10GB，所有并发任务总内存
    "warn_threshold_percent": 80           // 80%时警告
  }
}
```

### 2. 实现逻辑

```python
def check_file_size_limit(file_size, operation_type, current_concurrent_tasks):
    """检查文件大小限制"""
    config = load_memory_protection_config()
    
    if not config.get("enabled", True):
        return True, None  # 未启用，不限制
    
    # 获取对应操作的限制
    if operation_type == "upload":
        max_size = config.get("max_file_size_upload_mb", 2048) * 1024 * 1024
    elif operation_type == "fota":
        max_size = config.get("max_file_size_fota_mb", 1024) * 1024 * 1024
    elif operation_type == "batch_fota":
        max_size = config.get("max_file_size_batch_fota_mb", 512) * 1024 * 1024
    else:
        return True, None
    
    # 检查文件大小
    if file_size > max_size:
        # 检查当前并发任务数
        if current_concurrent_tasks < 5:
            # 并发任务少，给出警告但允许继续
            return True, f"警告：文件大小 ({file_size / 1024 / 1024:.1f}MB) 超过推荐限制 ({max_size / 1024 / 1024:.0f}MB)，可能影响系统性能"
        else:
            # 并发任务多，拒绝请求
            return False, f"文件大小 ({file_size / 1024 / 1024:.1f}MB) 超过限制 ({max_size / 1024 / 1024:.0f}MB)，当前系统负载较高，请稍后重试或减小文件大小"
    
    return True, None
```

### 3. 前端处理

- 如果返回警告，前端显示警告信息
- 用户可以选择继续或取消
- 如果返回错误，前端直接显示错误信息

## 总结

**不会硬性限制您的文件上传**，而是：

1. **可配置的限制**：默认值较大（2GB），可以根据需要调整
2. **智能检查**：根据当前系统负载动态决定是否允许
3. **警告机制**：超过限制时给出警告，但不强制拒绝（如果系统资源充足）
4. **保护机制**：只有在系统负载高时才拒绝，保护系统稳定性

这样既保护了系统稳定性，又不会影响正常使用。

