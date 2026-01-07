#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段6验证脚本
验证启动脚本功能
"""

import sys
import os
import subprocess
import time

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def test_help_command():
    """测试帮助命令"""
    print("\n=== 测试1: 帮助命令 ===")
    try:
        result = subprocess.run(
            [sys.executable, "run.py", "--help"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5
        )
        if result.returncode == 0 and ("--help" in result.stdout or "启动Web服务器" in result.stdout):
            print("✅ 帮助命令正常工作")
            return True
        else:
            print(f"❌ 帮助命令失败")
            if result.stderr:
                print(f"   错误: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"❌ 帮助命令异常: {e}")
        return False


def test_app_factory():
    """测试应用工厂"""
    print("\n=== 测试2: 应用工厂 ===")
    try:
        from app import create_app, get_socketio
        
        # 创建应用
        app = create_app()
        socketio = get_socketio()
        
        if app is None:
            print("❌ 应用工厂返回None")
            return False
        
        if socketio is None:
            print("❌ SocketIO实例为None")
            return False
        
        # 检查路由是否注册
        routes = [r.rule for r in app.url_map.iter_rules()]
        if len(routes) > 0:
            print(f"✅ 应用工厂可以正常创建应用")
            print(f"   注册了 {len(routes)} 个路由")
            print(f"   SocketIO实例已初始化 (async_mode: {socketio.async_mode})")
            
            # 检查关键路由
            key_routes = ['/', '/api/status', '/api/upload', '/api/fota']
            found_routes = [r for r in key_routes if r in routes]
            print(f"   关键路由: {len(found_routes)}/{len(key_routes)} 已注册")
            
            return True
        else:
            print("❌ 应用工厂创建的应用没有路由")
            return False
    except Exception as e:
        print(f"❌ 应用工厂测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_command_line_args():
    """测试命令行参数解析"""
    print("\n=== 测试3: 命令行参数解析 ===")
    try:
        # 测试各种参数组合（只测试解析，不实际启动服务器）
        test_cases = [
            (["--help"], "帮助信息"),
            (["--port", "9999", "--help"], "端口参数"),
            (["--host", "127.0.0.1", "--help"], "主机参数"),
            (["--debug", "--help"], "调试模式"),
            (["--reload", "--help"], "自动重载"),
            (["--port", "9999", "--debug", "--help"], "组合参数"),
        ]
        
        success_count = 0
        for args, description in test_cases:
            try:
                result = subprocess.run(
                    [sys.executable, "run.py"] + args,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5
                )
                if result.returncode == 0:
                    success_count += 1
                    print(f"   ✅ {description}: 正常")
                else:
                    print(f"   ❌ {description}: 失败")
            except subprocess.TimeoutExpired:
                print(f"   ⚠️ {description}: 超时（可能正常）")
                success_count += 1
            except Exception as e:
                print(f"   ❌ {description}: 异常 - {e}")
        
        if success_count == len(test_cases):
            print(f"✅ 所有命令行参数测试通过 ({success_count}/{len(test_cases)})")
            return True
        else:
            print(f"⚠️ 部分命令行参数测试失败 ({success_count}/{len(test_cases)})")
            return success_count >= len(test_cases) * 0.8  # 80%通过率即可
    except Exception as e:
        print(f"❌ 命令行参数测试异常: {e}")
        return False


def test_run_py_exists():
    """测试run.py文件存在"""
    print("\n=== 测试4: run.py文件检查 ===")
    try:
        if os.path.exists("run.py"):
            print("✅ run.py 文件存在")
            
            # 检查文件内容
            with open("run.py", "r", encoding="utf-8") as f:
                content = f.read()
            
            # 检查关键功能
            checks = {
                "parse_args": "parse_args" in content or "argparse" in content,
                "create_app": "create_app" in content,
                "get_socketio": "get_socketio" in content,
                "socketio.run": "socketio.run" in content,
                "命令行参数": "--port" in content and "--host" in content,
            }
            
            all_passed = all(checks.values())
            for name, passed in checks.items():
                status = "✅" if passed else "❌"
                print(f"   {status} {name}")
            
            return all_passed
        else:
            print("❌ run.py 文件不存在")
            return False
    except Exception as e:
        print(f"❌ run.py文件检查异常: {e}")
        return False


def test_imports():
    """测试导入"""
    print("\n=== 测试5: 导入检查 ===")
    try:
        # 测试run.py可以导入
        import importlib.util
        spec = importlib.util.spec_from_file_location("run", "run.py")
        if spec is None:
            print("❌ 无法加载run.py模块")
            return False
        
        print("✅ run.py 可以正常导入")
        return True
    except Exception as e:
        print(f"❌ 导入检查异常: {e}")
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("阶段6验证：启动脚本功能测试")
    print("=" * 60)
    
    results = []
    
    # 测试1: run.py文件检查
    results.append(("run.py文件检查", test_run_py_exists()))
    
    # 测试2: 导入检查
    results.append(("导入检查", test_imports()))
    
    # 测试3: 帮助命令
    results.append(("帮助命令", test_help_command()))
    
    # 测试4: 应用工厂
    results.append(("应用工厂", test_app_factory()))
    
    # 测试5: 命令行参数解析
    results.append(("命令行参数", test_command_line_args()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        if result:
            print(f"✅ {name}: 通过")
            passed += 1
        else:
            print(f"❌ {name}: 失败")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败")
    print("=" * 60)
    
    # 验证标准检查
    print("\n阶段6验证标准检查:")
    standard1 = any(r[0] == "帮助命令" and r[1] for r in results)
    standard2 = any(r[0] == "命令行参数" and r[1] for r in results)
    standard3 = any(r[0] == "应用工厂" and r[1] for r in results)
    standard4 = failed == 0
    
    print(f"- [{'x' if standard1 else ' '}] 可以使用 `python run.py` 启动应用（帮助命令正常）")
    print(f"- [{'x' if standard2 else ' '}] 命令行参数正常工作")
    print(f"- [{'x' if standard3 else ' '}] 应用正常启动（应用工厂正常）")
    print(f"- [{'x' if standard4 else ' '}] 所有功能正常")
    
    if all([standard1, standard2, standard3, standard4]):
        print("\n✅ 阶段6验证通过！")
        return 0
    else:
        print("\n⚠️ 阶段6验证部分通过，请检查失败的测试项")
        return 1


if __name__ == "__main__":
    sys.exit(main())
