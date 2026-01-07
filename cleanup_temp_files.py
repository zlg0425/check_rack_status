#!/usr/bin/env python3
"""
清理临时文件脚本
可选执行，用于清理迁移过程中产生的临时文件
"""

import sys
import os
import shutil
from pathlib import Path

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 可删除的临时文件列表
TEMP_FILES_TO_DELETE = [
    'run_new.py',           # 临时入口脚本（当前不使用）
    'setup_structure.py',   # 结构设置脚本（结构已建立）
]

# 保留的文件（作为工具）
FILES_TO_KEEP = [
    'verify_key_config.py',  # 保留作为调试工具
    'verify_migration.py',   # 保留作为验证工具
    'analyze_check_rack_status.py',  # 保留作为分析工具
]

def cleanup_temp_files(dry_run=True):
    """清理临时文件"""
    print("=" * 60)
    print("临时文件清理")
    print("=" * 60)
    
    if dry_run:
        print("模式: 预览模式（不会实际删除文件）")
    else:
        print("模式: 执行模式（将实际删除文件）")
    
    print()
    
    deleted = []
    not_found = []
    errors = []
    
    for filename in TEMP_FILES_TO_DELETE:
        filepath = Path(filename)
        if filepath.exists():
            if dry_run:
                print(f"  [预览] 将删除: {filename}")
                deleted.append(filename)
            else:
                try:
                    filepath.unlink()
                    print(f"  ✅ 已删除: {filename}")
                    deleted.append(filename)
                except Exception as e:
                    print(f"  ❌ 删除失败: {filename} - {e}")
                    errors.append((filename, str(e)))
        else:
            print(f"  ⚠️  文件不存在: {filename}")
            not_found.append(filename)
    
    print()
    print("=" * 60)
    print("清理结果")
    print("=" * 60)
    print(f"已删除/将删除: {len(deleted)} 个文件")
    if not_found:
        print(f"未找到: {len(not_found)} 个文件")
    if errors:
        print(f"错误: {len(errors)} 个文件")
    
    print()
    print("保留的文件（作为工具）:")
    for filename in FILES_TO_KEEP:
        filepath = Path(filename)
        if filepath.exists():
            print(f"  ✅ {filename}")
        else:
            print(f"  ⚠️  {filename} (不存在)")
    
    return len(deleted), len(errors)

def cleanup_old_test_reports(dry_run=True, keep_recent=5):
    """清理旧测试报告"""
    print()
    print("=" * 60)
    print("测试报告清理")
    print("=" * 60)
    
    if dry_run:
        print("模式: 预览模式（不会实际删除文件）")
    else:
        print("模式: 执行模式（将实际删除文件）")
    
    print(f"保留最近: {keep_recent} 个报告")
    print()
    
    reports_dir = Path('test_reports')
    if not reports_dir.exists():
        print("  ⚠️  测试报告目录不存在")
        return 0, 0
    
    # 获取所有报告文件
    report_files = sorted(
        reports_dir.glob('*.md'),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    
    if len(report_files) <= keep_recent:
        print(f"  ✅ 报告数量 ({len(report_files)}) 不超过保留数量 ({keep_recent})，无需清理")
        return 0, 0
    
    # 需要删除的报告
    to_delete = report_files[keep_recent:]
    to_keep = report_files[:keep_recent]
    
    print(f"总报告数: {len(report_files)}")
    print(f"保留: {len(to_keep)} 个")
    print(f"删除: {len(to_delete)} 个")
    print()
    
    print("将保留的报告:")
    for f in to_keep:
        print(f"  ✅ {f.name}")
    
    print()
    print("将删除的报告:")
    deleted = []
    errors = []
    
    for filepath in to_delete:
        if dry_run:
            print(f"  [预览] 将删除: {filepath.name}")
            deleted.append(filepath)
        else:
            try:
                filepath.unlink()
                print(f"  ✅ 已删除: {filepath.name}")
                deleted.append(filepath)
            except Exception as e:
                print(f"  ❌ 删除失败: {filepath.name} - {e}")
                errors.append((filepath, str(e)))
    
    return len(deleted), len(errors)

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='清理临时文件')
    parser.add_argument('--execute', action='store_true', help='实际执行删除（默认是预览模式）')
    parser.add_argument('--keep-reports', type=int, default=5, help='保留的测试报告数量（默认: 5）')
    parser.add_argument('--skip-reports', action='store_true', help='跳过测试报告清理')
    
    args = parser.parse_args()
    
    dry_run = not args.execute
    
    if dry_run:
        print("⚠️  这是预览模式，不会实际删除文件")
        print("   使用 --execute 参数来实际执行删除")
        print()
    
    # 清理临时文件
    temp_deleted, temp_errors = cleanup_temp_files(dry_run)
    
    # 清理旧测试报告
    if not args.skip_reports:
        report_deleted, report_errors = cleanup_old_test_reports(dry_run, args.keep_reports)
    else:
        report_deleted, report_errors = 0, 0
        print("\n跳过测试报告清理")
    
    print()
    print("=" * 60)
    print("总结")
    print("=" * 60)
    print(f"临时文件: {temp_deleted} 个已删除/将删除")
    print(f"测试报告: {report_deleted} 个已删除/将删除")
    
    if temp_errors + report_errors > 0:
        print(f"错误: {temp_errors + report_errors} 个")
        return 1
    
    if dry_run:
        print("\n这是预览模式，未实际删除文件")
        print("使用 --execute 参数来实际执行删除")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())

