# 下载功能自动化测试脚本使用说明

## 概述

`test_download_automated.py` 是一个全面的自动化测试脚本，用于测试下载功能的各种场景。它覆盖了文件下载、文件夹下载、错误处理、特殊文件处理等多个方面。

## 功能特性

### 测试覆盖范围

1. **文件下载测试**
   - 正常文件下载
   - 大文件下载（>10MB）
   - 文件覆盖测试

2. **文件夹下载测试**
   - 正常文件夹下载
   - 嵌套文件夹下载
   - 空文件夹下载

3. **错误处理测试**
   - 文件不存在
   - 文件夹不存在
   - 路径冲突处理

4. **特殊场景测试**
   - 符号链接处理
   - 进度回调测试
   - 磁盘空间检查（需要手动测试）

## 使用方法

### 基本用法

```bash
# 运行所有测试（使用默认服务器）
python test_download_automated.py

# 指定服务器信息
python test_download_automated.py --server LP-8650-1 --ip 10.99.19.11 --port 22

# 运行特定测试
python test_download_automated.py --test test_file_download

# 保留临时测试目录（用于调试）
python test_download_automated.py --keep-temp
```

### 命令行参数

- `--server SERVER_NAME`: 服务器名称（默认: LP-8650-1）
- `--ip IP_ADDRESS`: 服务器IP地址（默认: 10.99.19.11）
- `--port PORT`: 端口号，22 或 9999（默认: 22）
- `--test TEST_NAME`: 运行特定测试（可选）
- `--keep-temp`: 保留临时测试目录（用于调试）

### 可用测试列表

1. `test_file_download` - 正常文件下载
2. `test_directory_download` - 文件夹下载
3. `test_file_not_exists` - 文件不存在错误处理
4. `test_directory_not_exists` - 文件夹不存在错误处理
5. `test_file_overwrite` - 文件覆盖测试
6. `test_local_path_conflict` - 本地路径冲突测试
7. `test_empty_directory` - 空文件夹下载
8. `test_progress_callback` - 进度回调测试
9. `test_symlink_handling` - 符号链接处理测试
10. `test_large_file_download` - 大文件下载测试
11. `test_disk_space_check` - 磁盘空间检查（需要手动测试）

## 测试输出示例

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

...

============================================================
测试结果摘要
============================================================
总计: 11
通过: 9 ✓
失败: 0 ✗
跳过: 2 ⚠

详细结果:
  ✓ test_file_download: PASSED
  ✓ test_directory_download: PASSED
  ✓ test_file_not_exists: PASSED
  ...
```

## 测试场景说明

### 1. 正常文件下载 (test_file_download)

测试基本的文件下载功能，包括：
- 创建测试文件（如果远程不存在）
- 下载文件到本地
- 验证文件存在性和完整性

### 2. 文件夹下载 (test_directory_download)

测试文件夹递归下载功能，包括：
- 下载整个文件夹
- 统计文件数量和总大小
- 验证文件夹结构

### 3. 文件不存在 (test_file_not_exists)

测试错误处理，验证：
- 返回正确的错误信息
- 不创建本地文件
- 错误信息包含"不存在"或"not found"

### 4. 文件覆盖 (test_file_overwrite)

测试文件覆盖功能，验证：
- 已存在的文件被正确覆盖
- 文件大小发生变化

### 5. 进度回调 (test_progress_callback)

测试进度回调功能，验证：
- 进度更新正常
- 最终进度为100%

### 6. 符号链接处理 (test_symlink_handling)

测试符号链接处理，验证：
- 符号链接被正确解析
- 下载的是真实文件内容

### 7. 大文件下载 (test_large_file_download)

测试大文件下载性能，包括：
- 查找大文件（>10MB）
- 下载并计算速度
- 显示下载统计信息

## 注意事项

1. **测试环境要求**
   - 需要有效的SSH连接配置
   - 需要远程服务器上有测试文件/文件夹
   - 需要足够的本地磁盘空间

2. **跳过测试**
   - 某些测试如果条件不满足会自动跳过（例如：找不到大文件）
   - 跳过的测试不会影响整体测试结果

3. **临时目录**
   - 测试会在临时目录中创建测试文件
   - 默认情况下测试结束后会自动清理
   - 使用 `--keep-temp` 可以保留临时目录用于调试

4. **测试时间**
   - 文件夹下载测试可能需要较长时间
   - 大文件下载测试取决于文件大小和网络速度

5. **错误处理**
   - 测试失败会显示详细的错误信息
   - 可以查看异常堆栈跟踪进行调试

## 扩展测试

如果需要添加新的测试用例，可以：

1. 在脚本中添加新的测试函数
2. 函数签名：`def test_xxx(server_name, server_ip, port, test_dir)`
3. 在 `all_tests` 字典中注册新测试
4. 使用 `assert` 语句进行断言

示例：

```python
def test_custom_scenario(server_name, server_ip, port, test_dir):
    """测试自定义场景"""
    print("\n" + "="*60)
    print("测试: 自定义场景")
    print("="*60)
    
    try:
        # 测试逻辑
        ok, info = sftp_download(...)
        assert ok, f"测试失败: {info}"
        print("✓ 测试通过")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {str(e)}")
        return False

# 在 all_tests 字典中添加
all_tests = {
    ...
    "test_custom_scenario": test_custom_scenario,
}
```

## 故障排除

### 问题1: 连接失败

**症状**: 测试无法连接到服务器

**解决方案**:
- 检查服务器IP和端口是否正确
- 检查SSH配置和密钥文件
- 检查网络连接

### 问题2: 权限错误

**症状**: 测试返回权限错误

**解决方案**:
- 检查远程路径的访问权限
- 检查SSH用户权限
- 确认测试文件/文件夹存在

### 问题3: 测试文件不存在

**症状**: 某些测试被跳过

**解决方案**:
- 这是正常行为，测试会自动跳过不满足条件的测试
- 可以手动创建测试文件/文件夹

### 问题4: 磁盘空间不足

**症状**: 下载失败，提示磁盘空间不足

**解决方案**:
- 清理本地磁盘空间
- 使用 `--keep-temp` 查看临时目录位置
- 手动清理临时文件

## 与场景分析文档的对应关系

测试脚本与 `docs/download_scenarios.md` 中的场景对应关系：

| 场景文档 | 测试函数 |
|---------|---------|
| 正常文件下载 | test_file_download |
| 文件夹下载 | test_directory_download |
| 文件不存在 | test_file_not_exists |
| 文件夹不存在 | test_directory_not_exists |
| 文件覆盖 | test_file_overwrite |
| 路径冲突 | test_local_path_conflict |
| 空文件夹 | test_empty_directory |
| 进度回调 | test_progress_callback |
| 符号链接 | test_symlink_handling |
| 大文件下载 | test_large_file_download |
| 磁盘空间检查 | test_disk_space_check |

## 持续集成建议

可以将此测试脚本集成到CI/CD流程中：

```yaml
# 示例 GitHub Actions 配置
- name: Run Download Tests
  run: |
    python test_download_automated.py \
      --server ${{ secrets.TEST_SERVER }} \
      --ip ${{ secrets.TEST_IP }} \
      --port 22
```

## 贡献

如果发现新的测试场景或需要改进现有测试，请：
1. 添加新的测试函数
2. 更新本文档
3. 更新场景分析文档

