#!/usr/bin/env python3
"""
测试 check_rack_status.py 命令行工具
"""

import sys
import os

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def test_imports():
    """测试导入"""
    print("=" * 60)
    print("测试导入")
    print("=" * 60)
    
    try:
        import check_rack_status
        print("✅ check_rack_status 导入成功")
        
        # 检查必要的函数和变量
        assert hasattr(check_rack_status, 'main'), "缺少 main() 函数"
        assert hasattr(check_rack_status, 'format_report'), "缺少 format_report() 函数"
        assert hasattr(check_rack_status, 'load_config'), "缺少 load_config() 函数"
        assert hasattr(check_rack_status, 'run_checks_once'), "缺少 run_checks_once() 函数"
        
        print("✅ 所有必要的函数和变量都存在")
        return True
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return False

def test_load_config():
    """测试配置加载"""
    print("\n" + "=" * 60)
    print("测试配置加载")
    print("=" * 60)
    
    try:
        import check_rack_status
        config_path = check_rack_status.load_config()
        print(f"✅ 配置加载成功: {config_path}")
        
        # 检查配置变量
        assert hasattr(check_rack_status, 'server_dict'), "缺少 server_dict"
        assert hasattr(check_rack_status, 'check_interval'), "缺少 check_interval"
        assert hasattr(check_rack_status, 'auth_mode_default'), "缺少 auth_mode_default"
        
        print(f"✅ 服务器数量: {len(check_rack_status.server_dict)}")
        print(f"✅ 检查间隔: {check_rack_status.check_interval} 秒")
        print(f"✅ 默认认证模式: {check_rack_status.auth_mode_default}")
        return True
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_format_report():
    """测试报告格式化"""
    print("\n" + "=" * 60)
    print("测试报告格式化")
    print("=" * 60)
    
    try:
        import check_rack_status
        
        # 创建测试数据
        test_results = [
            {
                'server_name': 'LP-8650-TEST',
                'server_ip': '192.168.1.100',
                'port_22': 'online',
                'port_22_ssh': 'online',
                'port_22_detail': '',
                'port_9999': 'online',
                'port_9999_ssh': 'online',
                'port_9999_detail': '',
                'version': '1.0.0',
                'version_9999': '1.0.0',
            }
        ]
        
        report = check_rack_status.format_report(test_results)
        assert report, "报告为空"
        assert 'LP-8650-TEST' in report, "报告中缺少服务器名称"
        assert '192.168.1.100' in report, "报告中缺少服务器IP"
        
        print("✅ 报告格式化成功")
        print("\n示例报告:")
        print("-" * 60)
        print(report[:200] + "..." if len(report) > 200 else report)
        print("-" * 60)
        return True
    except Exception as e:
        print(f"❌ 报告格式化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_run_checks_once():
    """测试运行检查（不实际连接）"""
    print("\n" + "=" * 60)
    print("测试 run_checks_once 函数")
    print("=" * 60)
    
    try:
        import check_rack_status
        check_rack_status.load_config()
        
        # 检查函数是否存在
        assert hasattr(check_rack_status, 'run_checks_once'), "缺少 run_checks_once() 函数"
        
        # 检查函数来源
        import inspect
        source_file = inspect.getfile(check_rack_status.run_checks_once)
        print(f"✅ run_checks_once() 来源: {source_file}")
        
        if 'check_rack_status.py' in source_file:
            print("⚠️  警告: run_checks_once() 仍在 check_rack_status.py 中")
        else:
            print("✅ run_checks_once() 已从新模块导入")
        
        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("check_rack_status.py 命令行工具测试")
    print("=" * 60)
    
    results = []
    
    # 测试导入
    results.append(("导入测试", test_imports()))
    
    # 测试配置加载
    results.append(("配置加载测试", test_load_config()))
    
    # 测试报告格式化
    results.append(("报告格式化测试", test_format_report()))
    
    # 测试 run_checks_once
    results.append(("run_checks_once 测试", test_run_checks_once()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n✅ 所有测试通过！命令行工具可以正常使用。")
        return 0
    else:
        print("\n❌ 部分测试失败，请检查错误信息。")
        return 1

if __name__ == '__main__':
    sys.exit(main())

