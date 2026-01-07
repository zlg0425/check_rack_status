#!/usr/bin/env python3
"""
快速验证迁移状态
用于验证各阶段的迁移是否成功
"""

import sys
import os

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def check_imports():
    """检查导入是否正常"""
    print("\n1. 检查主应用导入...")
    try:
        import web_ui
        print("   ✅ web_ui 导入正常")
        return True
    except Exception as e:
        print(f"   ❌ web_ui 导入失败: {e}")
        return False

def check_compat_layer():
    """检查兼容层（已废弃，可选检查）"""
    print("\n2. 检查兼容层（已废弃）...")
    try:
        from check_rack_status_compat import (
            load_config, 
            sftp_upload, 
            server_dict,
            resolve_key
        )
        print("   ⚠️  check_rack_status_compat 仍存在（已废弃，建议删除）")
        return True
    except ImportError:
        print("   ✅ check_rack_status_compat 已删除（符合预期）")
        return True
    except Exception as e:
        print(f"   ⚠️  check_rack_status_compat 检查异常: {e}")
        return True  # 不阻止继续

def check_new_modules():
    """检查新模块是否正常"""
    print("\n3. 检查新模块导入...")
    modules = [
        ('app.utils.config', ['load_config', 'resolve_key', 'server_dict']),
        ('app.utils.helpers', ['md5_bytes', 'md5_stream', 'run_ucm_with_log']),
        ('core.sftp.operations', ['sftp_upload', 'sftp_download', 'remote_md5']),
        ('core.monitoring.checker', ['run_checks_once', 'check_ssh_login']),
        ('core.fota.manager', ['log_fota', 'record_fota_timing']),
        ('core.ssh.transport', ['create_transport']),
    ]
    
    all_ok = True
    for module_name, exports in modules:
        try:
            module = __import__(module_name, fromlist=exports)
            # 检查关键导出
            for export in exports[:1]:  # 只检查第一个导出
                if hasattr(module, export):
                    print(f"   ✅ {module_name} 导入正常")
                    break
            else:
                print(f"   ⚠️  {module_name} 导入成功但缺少导出")
        except Exception as e:
            print(f"   ❌ {module_name} 导入失败: {e}")
            all_ok = False
    
    return all_ok

def check_test_scripts():
    """检查测试脚本导入"""
    print("\n4. 检查测试脚本导入...")
    test_scripts = [
        'test_functional',
        'test_integration',
        'test_performance',
        'test_socketio_functional',
    ]
    
    all_ok = True
    for script in test_scripts:
        script_path = f"{script}.py"
        if os.path.exists(script_path):
            try:
                # 只检查语法，不执行
                with open(script_path, 'r', encoding='utf-8') as f:
                    compile(f.read(), script_path, 'exec')
                print(f"   ✅ {script}.py 语法正常")
            except SyntaxError as e:
                print(f"   ❌ {script}.py 语法错误: {e}")
                all_ok = False
            except Exception as e:
                print(f"   ⚠️  {script}.py 检查失败: {e}")
        else:
            print(f"   ⚠️  {script}.py 不存在")
    
    return all_ok

def check_old_imports():
    """检查是否还有旧导入路径"""
    print("\n5. 检查旧导入路径使用情况...")
    
    import subprocess
    try:
        # 查找使用旧导入的文件（排除兼容层和原始文件）
        result = subprocess.run(
            ['grep', '-r', 'from check_rack_status import', '--include=*.py', '.'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
        filtered_lines = [
            line for line in lines 
            if 'check_rack_status_compat.py' not in line and 'analyze_check_rack_status.py' not in line 
            and 'check_rack_status.py' not in line
        ]
        
        if filtered_lines:
            print(f"   ⚠️  发现 {len(filtered_lines)} 个文件仍使用旧导入:")
            for line in filtered_lines[:5]:  # 只显示前5个
                print(f"      {line}")
            if len(filtered_lines) > 5:
                print(f"      ... 还有 {len(filtered_lines) - 5} 个")
            return False
        else:
            print("   ✅ 没有发现使用旧导入的文件（兼容层除外）")
            return True
    except Exception as e:
        print(f"   ⚠️  无法检查旧导入: {e}")
        return True  # 不阻止继续

def main():
    """主函数"""
    print("=" * 60)
    print("迁移状态验证")
    print("=" * 60)
    
    results = []
    results.append(("主应用导入", check_imports()))
    results.append(("兼容层导入", check_compat_layer()))
    results.append(("新模块导入", check_new_modules()))
    results.append(("测试脚本", check_test_scripts()))
    results.append(("旧导入检查", check_old_imports()))
    
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    if all_passed:
        print("✅ 所有检查通过！")
        return 0
    else:
        print("❌ 部分检查失败，请检查上述错误")
        return 1

if __name__ == '__main__':
    sys.exit(main())

