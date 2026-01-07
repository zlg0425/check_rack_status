# 性能监控脚本快速开始

## 快速开始

### 1. 启动服务器

```bash
python web_ui.py
```

### 2. 运行性能监控（单次测试）

```bash
# 单次监控，快速查看当前性能
python monitor_performance.py --once
```

### 3. 持续监控

```bash
# 每60秒监控一次（默认）
python monitor_performance.py

# 每30秒监控一次
python monitor_performance.py --interval 30
```

### 4. 查看性能报告

```bash
# 生成并查看性能报告
python monitor_performance.py --report

# 保存报告到文件
python monitor_performance.py --report --report-file performance_report.txt
```

## 常用命令

```bash
# 基本监控（后台运行）
python monitor_performance.py --interval 60 > /dev/null 2>&1 &

# 单次检查
python monitor_performance.py --once

# 生成报告
python monitor_performance.py --report

# 自定义API地址
python monitor_performance.py --api-url http://192.168.1.100:8888
```

## 输出文件

- **日志文件**: `logs/performance_monitor_YYYYMMDD.log`
- **性能指标**: `performance_metrics.json`
- **报告文件**: 使用 `--report-file` 指定

## 查看日志

```bash
# Windows
type logs\performance_monitor_20251230.log

# Linux/Mac
tail -f logs/performance_monitor_20251230.log
```

## 停止监控

按 `Ctrl+C` 停止前台运行的监控脚本。

