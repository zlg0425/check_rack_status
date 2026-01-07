#!/usr/bin/env python3
"""
Service 层测试脚本
验证业务逻辑提取是否正确
"""

import sys
import os

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def test_service_imports():
    """测试 service 模块导入"""
    print("\n=== 测试 Service 模块导入 ===")
    
    try:
        # 从 app.api 导入（app.api.__init__.py 已导出）
        from app.api import get_upload_service, UploadService
        print("[OK] 上传服务模块导入成功")
        
        # 测试服务实例
        service = get_upload_service()
        if service is not None:
            print("[OK] 上传服务实例创建成功")
        else:
            print("[FAIL] 上传服务实例为 None")
            return False
        
        # 测试服务方法
        if hasattr(service, 'create_upload_task'):
            print("[OK] create_upload_task 方法存在")
        else:
            print("[FAIL] create_upload_task 方法不存在")
            return False
        
        if hasattr(service, 'execute_upload'):
            print("[OK] execute_upload 方法存在")
        else:
            print("[FAIL] execute_upload 方法不存在")
            return False
        
        if hasattr(service, 'get_task'):
            print("[OK] get_task 方法存在")
        else:
            print("[FAIL] get_task 方法不存在")
            return False
        
        return True
        
    except ImportError as e:
        # 如果从 app.api 导入失败，尝试直接从 upload_service 导入
        try:
            from app.api.upload_service import get_upload_service, UploadService
            print("[OK] 上传服务模块导入成功（直接导入）")
            service = get_upload_service()
            if service is not None:
                print("[OK] 上传服务实例创建成功")
                return True
            else:
                print("[FAIL] 上传服务实例为 None")
                return False
        except Exception as e2:
            print(f"[FAIL] Service 模块导入失败: {str(e2)}")
            import traceback
            traceback.print_exc()
            return False
    except Exception as e:
        print(f"[FAIL] Service 模块导入失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_route_integration():
    """测试路由集成"""
    print("\n=== 测试路由集成 ===")
    
    try:
        from app.routes import upload
        print("[OK] 上传路由模块导入成功")
        
        # 检查路由是否使用服务
        if hasattr(upload, 'upload_service'):
            print("[OK] 路由模块使用上传服务")
        else:
            print("[WARN] 路由模块可能未使用上传服务")
        
        # 检查路由函数是否存在
        if hasattr(upload, 'api_upload'):
            print("[OK] api_upload 路由函数存在")
        else:
            print("[FAIL] api_upload 路由函数不存在")
            return False
        
        if hasattr(upload, 'api_upload_progress'):
            print("[OK] api_upload_progress 路由函数存在")
        else:
            print("[FAIL] api_upload_progress 路由函数不存在")
            return False
        
        return True
        
    except Exception as e:
        print(f"[FAIL] 路由集成测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_service_functionality():
    """测试服务功能"""
    print("\n=== 测试服务功能 ===")
    
    try:
        # 从 app.api 导入（app.api.__init__.py 已导出）
        try:
            from app.api import get_upload_service
        except ImportError:
            from app.api.upload_service import get_upload_service
        
        service = get_upload_service()
        
        # 测试任务管理
        tasks = service.get_all_tasks()
        if isinstance(tasks, dict):
            print(f"[OK] get_all_tasks 返回字典，当前任务数: {len(tasks)}")
        else:
            print("[FAIL] get_all_tasks 返回类型错误")
            return False
        
        # 测试获取不存在的任务
        task = service.get_task("non-existent-task-id")
        if task is None:
            print("[OK] 获取不存在的任务返回 None")
        else:
            print("[FAIL] 获取不存在的任务应返回 None")
            return False
        
        return True
        
    except Exception as e:
        print(f"[FAIL] 服务功能测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("Service Layer Test")
    print("=" * 60)
    
    tests = [
        ("Service 模块导入", test_service_imports),
        ("路由集成", test_route_integration),
        ("服务功能", test_service_functionality),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] {test_name} 测试异常: {str(e)}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Total: {len(tests)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    if failed == 0:
        print("\n[OK] All service layer tests passed!")
        return 0
    else:
        print(f"\n[FAIL] {failed} test(s) failed")
        return 1

if __name__ == '__main__':
    sys.exit(main())

