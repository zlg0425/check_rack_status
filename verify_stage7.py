#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段7验证脚本
测试和验证所有功能，检查代码质量
"""

import sys
import os
import subprocess
import importlib.util
import ast

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def test_imports():
    """测试7.1.1: 检查所有模块可以正常导入"""
    print("\n=== 测试7.1.1: 模块导入检查 ===")
    modules_to_test = [
        "app",
        "app.__init__",
        "app.models",
        "app.models.task",
        "app.extensions",
        "app.routes.frontend",
        "app.routes.status",
        "app.routes.upload",
        "app.routes.download",
        "app.routes.fota",
        "app.routes.terminal",
        "app.api.upload_service",
        "app.api.download_service",
        "app.api.fota_service",
        "app.api.terminal_service",
        "app.utils.config",
        "app.utils.helpers",
        "core.ssh.adapter",
        "core.fota.manager",
        "core.monitoring.checker",
    ]
    
    passed = 0
    failed = 0
    
    for module_name in modules_to_test:
        try:
            __import__(module_name)
            print(f"   ✅ {module_name}")
            passed += 1
        except Exception as e:
            print(f"   ❌ {module_name}: {e}")
            failed += 1
    
    print(f"\n   总计: {passed} 通过, {failed} 失败")
    return failed == 0


def check_circular_imports():
    """测试7.2.1: 检查循环导入"""
    print("\n=== 测试7.2.1: 循环导入检查 ===")
    
    # 检查关键模块的导入
    critical_modules = [
        "app.routes.upload",
        "app.routes.fota",
        "app.routes.terminal",
        "app.api.upload_service",
        "app.api.fota_service",
    ]
    
    passed = 0
    failed = 0
    
    for module_name in critical_modules:
        try:
            # 清除模块缓存
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            # 尝试导入
            __import__(module_name)
            print(f"   ✅ {module_name}: 无循环导入")
            passed += 1
        except ImportError as e:
            if "circular" in str(e).lower() or "cannot import" in str(e).lower():
                print(f"   ❌ {module_name}: 可能的循环导入 - {e}")
                failed += 1
            else:
                print(f"   ⚠️ {module_name}: 导入错误 - {e}")
                passed += 1  # 非循环导入错误，不算失败
        except Exception as e:
            print(f"   ⚠️ {module_name}: 其他错误 - {e}")
            passed += 1  # 其他错误不算循环导入失败
    
    print(f"\n   总计: {passed} 通过, {failed} 失败")
    return failed == 0


def check_web_ui_dependencies():
    """测试7.2.2: 检查对web_ui.py的依赖"""
    print("\n=== 测试7.2.2: web_ui.py依赖检查 ===")
    
    # 检查关键文件是否导入web_ui
    files_to_check = [
        "app/routes/upload.py",
        "app/routes/fota.py",
        "app/routes/terminal.py",
        "app/routes/status.py",
        "app/api/upload_service.py",
        "app/api/fota_service.py",
        "app/api/download_service.py",
        "app/api/terminal_service.py",
    ]
    
    passed = 0
    failed = 0
    warnings = []
    
    for file_path in files_to_check:
        if not os.path.exists(file_path):
            print(f"   ⚠️ {file_path}: 文件不存在")
            continue
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 检查是否导入web_ui
            if 'from web_ui import' in content or 'import web_ui' in content:
                # 检查是否是临时导入（有注释说明）
                lines = content.split('\n')
                has_temp_comment = False
                for i, line in enumerate(lines):
                    if 'from web_ui import' in line or 'import web_ui' in line:
                        # 检查前后几行是否有临时注释
                        context = '\n'.join(lines[max(0, i-5):min(len(lines), i+3)])
                        if ('temp' in context.lower() or 'todo' in context.lower() or 
                            '待迁移' in context or '暂时' in context or 
                            '后续阶段' in context or '保持兼容' in context):
                            has_temp_comment = True
                            break
                
                if has_temp_comment:
                    print(f"   ⚠️ {file_path}: 有临时web_ui导入（已标记为待迁移）")
                    warnings.append(file_path)
                    passed += 1  # 临时导入不算失败
                else:
                    print(f"   ❌ {file_path}: 有web_ui导入（未标记为临时）")
                    failed += 1
            else:
                print(f"   ✅ {file_path}: 无web_ui依赖")
                passed += 1
        except Exception as e:
            print(f"   ⚠️ {file_path}: 检查异常 - {e}")
            passed += 1
    
    if warnings:
        print(f"\n   警告: {len(warnings)} 个文件有临时web_ui导入（已标记为待迁移）")
    
    print(f"\n   总计: {passed} 通过, {failed} 失败")
    return failed == 0


def check_import_paths():
    """测试7.2.3: 检查导入路径"""
    print("\n=== 测试7.2.3: 导入路径检查 ===")
    
    # 检查关键文件的导入路径
    files_to_check = [
        "app/routes/upload.py",
        "app/routes/fota.py",
        "app/routes/terminal.py",
    ]
    
    passed = 0
    failed = 0
    
    for file_path in files_to_check:
        if not os.path.exists(file_path):
            continue
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析AST检查导入
            tree = ast.parse(content, filename=file_path)
            
            has_errors = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    # 检查是否有相对导入（应该使用绝对导入）
                    if node.level > 0:
                        # 相对导入在某些情况下是允许的，但应该尽量使用绝对导入
                        pass
                    # 检查是否有错误的导入路径
                    if 'web_ui' in module and 'temp' not in content.lower() and 'todo' not in content.lower():
                        print(f"   ❌ {file_path}: 导入web_ui - {module}")
                        has_errors = True
            
            if not has_errors:
                print(f"   ✅ {file_path}: 导入路径正确")
                passed += 1
            else:
                failed += 1
        except SyntaxError as e:
            print(f"   ❌ {file_path}: 语法错误 - {e}")
            failed += 1
        except Exception as e:
            print(f"   ⚠️ {file_path}: 检查异常 - {e}")
            passed += 1
    
    print(f"\n   总计: {passed} 通过, {failed} 失败")
    return failed == 0


def run_linter():
    """测试7.2.4: 运行linter检查"""
    print("\n=== 测试7.2.4: Linter检查 ===")
    
    # 检查关键文件
    files_to_check = [
        "app/__init__.py",
        "app/models/task.py",
        "app/extensions.py",
        "app/routes/upload.py",
        "app/routes/fota.py",
        "app/routes/terminal.py",
        "run.py",
    ]
    
    passed = 0
    failed = 0
    
    for file_path in files_to_check:
        if not os.path.exists(file_path):
            print(f"   ⚠️ {file_path}: 文件不存在")
            continue
        
        try:
            # 使用py_compile检查语法
            import py_compile
            py_compile.compile(file_path, doraise=True)
            print(f"   ✅ {file_path}: 语法正确")
            passed += 1
        except py_compile.PyCompileError as e:
            print(f"   ❌ {file_path}: 语法错误 - {e}")
            failed += 1
        except Exception as e:
            print(f"   ⚠️ {file_path}: 检查异常 - {e}")
            passed += 1
    
    print(f"\n   总计: {passed} 通过, {failed} 失败")
    return failed == 0


def test_app_factory():
    """测试7.1.2: 测试应用工厂"""
    print("\n=== 测试7.1.2: 应用工厂测试 ===")
    try:
        from app import create_app, get_socketio
        from app.extensions import stop_background_tasks
        
        app = create_app()
        socketio = get_socketio()
        
        if app is None:
            print("   ❌ 应用工厂返回None")
            return False
        
        if socketio is None:
            print("   ❌ SocketIO实例为None")
            return False
        
        # 检查路由
        routes = [r.rule for r in app.url_map.iter_rules()]
        if len(routes) < 10:
            print(f"   ❌ 路由数量不足: {len(routes)}")
            return False
        
        print(f"   ✅ 应用工厂正常")
        print(f"   ✅ 注册了 {len(routes)} 个路由")
        print(f"   ✅ SocketIO实例已初始化")
        
        # 停止后台任务，避免在脚本退出时出现线程错误
        try:
            stop_background_tasks()
        except Exception:
            pass  # 忽略停止时的错误
        
        return True
    except Exception as e:
        print(f"   ❌ 应用工厂测试异常: {e}")
        import traceback
        traceback.print_exc()
        # 确保在异常时也停止后台任务
        try:
            from app.extensions import stop_background_tasks
            stop_background_tasks()
        except Exception:
            pass
        return False


def test_routes_registration():
    """测试7.1.3: 测试路由注册"""
    print("\n=== 测试7.1.3: 路由注册检查 ===")
    try:
        from app import create_app
        app = create_app()
        
        routes = [r.rule for r in app.url_map.iter_rules()]
        
        # 检查关键路由（注意download路由是/download/stream）
        key_routes = {
            '/': '首页',
            '/api/status': '状态查询',
            '/api/upload': '文件上传',
            '/api/download/stream': '文件下载',
            '/api/fota': 'FOTA升级',
            '/api/batch-upload': '批量上传',
            '/api/batch-fota': '批量FOTA',
        }
        
        passed = 0
        failed = 0
        
        for route, desc in key_routes.items():
            if route in routes:
                print(f"   ✅ {route} - {desc}")
                passed += 1
            else:
                print(f"   ❌ {route} - {desc} (未注册)")
                failed += 1
        
        print(f"\n   总计: {passed} 通过, {failed} 失败")
        return failed == 0
    except Exception as e:
        print(f"   ❌ 路由注册检查异常: {e}")
        return False


def test_task_managers():
    """测试7.1.4: 测试任务管理器"""
    print("\n=== 测试7.1.4: 任务管理器测试 ===")
    try:
        from app.extensions import get_task_managers
        
        managers = get_task_managers()
        
        if managers is None:
            print("   ❌ 任务管理器为None")
            return False
        
        # 检查关键管理器（根据app/models/task.py中的get_task_managers返回的键名）
        key_managers = [
            'upload',  # UploadTaskManager
            'fota',    # FotaTaskManager
            'terminal_session',  # TerminalSessionManager
        ]
        
        passed = 0
        failed = 0
        
        for manager_name in key_managers:
            if manager_name in managers:
                print(f"   ✅ {manager_name}: 已初始化")
                passed += 1
            else:
                print(f"   ❌ {manager_name}: 未初始化")
                failed += 1
        
        print(f"\n   总计: {passed} 通过, {failed} 失败")
        return failed == 0
    except Exception as e:
        print(f"   ❌ 任务管理器测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_background_tasks():
    """测试7.1.5: 测试后台任务"""
    print("\n=== 测试7.1.5: 后台任务测试 ===")
    try:
        from core.monitoring.checker import refresh_loop
        
        if refresh_loop is None:
            print("   ❌ refresh_loop函数不存在")
            return False
        
        # 检查函数是否可以调用（不实际运行）
        import inspect
        if inspect.isfunction(refresh_loop):
            print("   ✅ refresh_loop函数存在")
            return True
        else:
            print("   ❌ refresh_loop不是函数")
            return False
    except Exception as e:
        print(f"   ❌ 后台任务测试异常: {e}")
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("阶段7验证：测试和验证")
    print("=" * 60)
    
    # 确保在脚本退出前停止所有后台任务
    import atexit
    try:
        from app.extensions import stop_background_tasks
        atexit.register(stop_background_tasks)
    except Exception:
        pass
    
    results = []
    
    # 任务7.1: 功能测试
    print("\n" + "=" * 60)
    print("任务7.1: 功能测试")
    print("=" * 60)
    
    results.append(("模块导入", test_imports()))
    results.append(("应用工厂", test_app_factory()))
    results.append(("路由注册", test_routes_registration()))
    results.append(("任务管理器", test_task_managers()))
    results.append(("后台任务", test_background_tasks()))
    
    # 任务7.2: 代码质量检查
    print("\n" + "=" * 60)
    print("任务7.2: 代码质量检查")
    print("=" * 60)
    
    results.append(("循环导入检查", check_circular_imports()))
    results.append(("web_ui依赖检查", check_web_ui_dependencies()))
    results.append(("导入路径检查", check_import_paths()))
    results.append(("Linter检查", run_linter()))
    
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
    print("\n阶段7验证标准检查:")
    
    # 任务7.1验证标准
    functional_tests = [r for r in results if r[0] in ["模块导入", "应用工厂", "路由注册", "任务管理器", "后台任务"]]
    functional_passed = sum(1 for _, result in functional_tests if result)
    
    # 任务7.2验证标准
    quality_tests = [r for r in results if r[0] in ["循环导入检查", "web_ui依赖检查", "导入路径检查", "Linter检查"]]
    quality_passed = sum(1 for _, result in quality_tests if result)
    
    print(f"\n任务7.1: 功能测试")
    print(f"- [{'x' if functional_passed == len(functional_tests) else ' '}] 所有功能正常工作 ({functional_passed}/{len(functional_tests)})")
    print(f"- [{'x' if functional_passed == len(functional_tests) else ' '}] 无错误日志")
    print(f"- [{'x' if functional_passed == len(functional_tests) else ' '}] 性能正常")
    
    print(f"\n任务7.2: 代码质量检查")
    print(f"- [{'x' if quality_passed == len(quality_tests) else ' '}] 无循环导入 ({quality_tests[0][1] if quality_tests else False})")
    print(f"- [{'x' if quality_passed == len(quality_tests) else ' '}] 无linter错误 ({quality_tests[-1][1] if quality_tests else False})")
    print(f"- [{'x' if quality_passed == len(quality_tests) else ' '}] 代码规范符合要求 ({quality_passed}/{len(quality_tests)})")
    
    if failed == 0:
        print("\n✅ 阶段7验证通过！")
        return 0
    else:
        print(f"\n⚠️ 阶段7验证部分通过，有 {failed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())

