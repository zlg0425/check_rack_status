#!/usr/bin/env python3
"""
测试前端代码分离
验证模板、CSS 和 JavaScript 文件是否正确提取和配置
"""

import sys
import os

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def test_file_exists(filepath, description):
    """测试文件是否存在"""
    if os.path.exists(filepath):
        print(f"[OK] {description}: {filepath}")
        return True
    else:
        print(f"[FAIL] {description}: {filepath} 不存在")
        return False

def test_app_configuration():
    """测试应用配置"""
    print("\n=== 测试应用配置 ===")
    
    try:
        from app import create_app
        app = create_app()
        
        # 检查模板文件夹
        if app.template_folder:
            template_path = os.path.abspath(app.template_folder)
            if os.path.exists(template_path):
                print(f"[OK] 模板文件夹: {template_path}")
            else:
                print(f"[FAIL] 模板文件夹不存在: {template_path}")
                return False
        else:
            print("[FAIL] 模板文件夹未配置")
            return False
        
        # 检查静态文件夹
        if app.static_folder:
            static_path = os.path.abspath(app.static_folder)
            if os.path.exists(static_path):
                print(f"[OK] 静态文件夹: {static_path}")
            else:
                print(f"[FAIL] 静态文件夹不存在: {static_path}")
                return False
        else:
            print("[FAIL] 静态文件夹未配置")
            return False
        
        # 检查前端路由
        routes = [r.rule for r in app.url_map.iter_rules() if r.endpoint.startswith('frontend')]
        if routes:
            print(f"[OK] 前端路由: {routes}")
        else:
            print("[FAIL] 前端路由未注册")
            return False
        
        return True
        
    except Exception as e:
        print(f"[FAIL] 应用配置测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("Frontend Separation Test")
    print("=" * 60)
    
    # 测试文件存在性
    print("\n=== 测试文件存在性 ===")
    
    files_to_check = [
        ("frontend/templates/index.html", "HTML 模板"),
        ("frontend/static/css/main.css", "CSS 样式"),
        ("frontend/static/js/main.js", "JavaScript 代码"),
        ("app/routes/frontend.py", "前端路由模块"),
    ]
    
    all_exist = True
    for filepath, description in files_to_check:
        if not test_file_exists(filepath, description):
            all_exist = False
    
    if not all_exist:
        print("\n[FAIL] 部分文件不存在")
        return 1
    
    # 测试应用配置
    if not test_app_configuration():
        print("\n[FAIL] 应用配置测试失败")
        return 1
    
    # 测试文件内容
    print("\n=== 测试文件内容 ===")
    
    # 检查 HTML 模板
    try:
        with open('frontend/templates/index.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
            if 'url_for' in html_content and 'static' in html_content:
                print("[OK] HTML 模板包含 Flask url_for 引用")
            else:
                print("[WARN] HTML 模板可能缺少 Flask url_for 引用")
    except Exception as e:
        print(f"[FAIL] 读取 HTML 模板失败: {str(e)}")
        return 1
    
    # 检查 CSS
    try:
        with open('frontend/static/css/main.css', 'r', encoding='utf-8') as f:
            css_content = f.read()
            if len(css_content) > 100:
                print(f"[OK] CSS 文件大小: {len(css_content)} 字符")
            else:
                print("[WARN] CSS 文件可能过小")
    except Exception as e:
        print(f"[FAIL] 读取 CSS 文件失败: {str(e)}")
        return 1
    
    # 检查 JavaScript
    try:
        with open('frontend/static/js/main.js', 'r', encoding='utf-8') as f:
            js_content = f.read()
            if 'window.SSH_USER' in js_content or 'sshUser' in js_content:
                print(f"[OK] JavaScript 文件大小: {len(js_content)} 字符，包含必要的变量")
            else:
                print("[WARN] JavaScript 文件可能缺少必要的变量")
    except Exception as e:
        print(f"[FAIL] 读取 JavaScript 文件失败: {str(e)}")
        return 1
    
    print("\n" + "=" * 60)
    print("[OK] All frontend separation tests passed!")
    print("=" * 60)
    return 0

if __name__ == '__main__':
    sys.exit(main())

