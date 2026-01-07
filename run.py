#!/usr/bin/env python3
"""
应用启动脚本
使用应用工厂模式启动Flask应用
"""

import argparse
import sys
import os
import atexit
import signal

# 设置输出编码（Windows 兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app import create_app, get_socketio, get_app


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='启动Web服务器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py                    # 使用默认配置启动
  python run.py --port 8888        # 指定端口
  python run.py --host 0.0.0.0     # 指定主机
  python run.py --debug            # 启用调试模式
  python run.py --port 8888 --debug # 组合使用
        """
    )
    
    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='服务器主机地址 (默认: 0.0.0.0)'
    )
    
    parser.add_argument(
        '--port',
        type=int,
        default=8888,
        help='服务器端口 (默认: 8888)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式'
    )
    
    parser.add_argument(
        '--reload',
        action='store_true',
        help='启用自动重载（开发模式）'
    )
    
    return parser.parse_args()


def cleanup():
    """清理资源（在程序退出时调用）"""
    try:
        from app.extensions import stop_background_tasks
        stop_background_tasks()
    except Exception:
        # 忽略清理时的错误（可能是在解释器关闭时）
        pass


def signal_handler(signum, frame):
    """信号处理器（用于优雅退出）"""
    print(f"\n收到信号 {signum}，正在优雅退出...")
    cleanup()
    sys.exit(0)


def main():
    """主函数"""
    args = parse_args()
    
    # 注册清理函数（在程序正常退出时调用）
    atexit.register(cleanup)
    
    # 注册信号处理器（Linux/Unix系统）
    if sys.platform != 'win32':
        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
        except (ValueError, OSError):
            # 某些情况下可能无法注册信号处理器，忽略错误
            pass
    
    # 创建应用配置
    config = {
        'DEBUG': args.debug,
        'SECRET_KEY': 'terminal-secret-key-change-in-production'
    }
    
    # 使用应用工厂创建应用
    print("="*60)
    print("正在启动Web服务器...")
    print("="*60)
    
    try:
        # 加载配置并初始化状态缓存（可选，后台任务会自动更新）
        from app.utils.config import load_config
        from core.monitoring import run_checks_once, status_cache, cache_lock
        import time
        
        load_config()
        
        # 初始化一次状态数据（可选，后台任务会自动更新）
        try:
            print("正在初始化状态缓存...")
            with cache_lock:
                status_cache["data"] = run_checks_once()
                status_cache["timestamp"] = time.time()
                status_cache["error"] = ""
            print("状态缓存初始化完成")
        except Exception as e:
            print(f"警告: 状态缓存初始化失败: {e}")
            with cache_lock:
                status_cache["error"] = str(e)
        
        app = create_app(config=config)
        socketio = get_socketio()
        
        if socketio is None:
            print("错误: SocketIO实例未初始化")
            sys.exit(1)
        
        # 获取异步模式
        async_mode = socketio.async_mode
        
        # 输出启动信息
        print(f"\n服务器配置:")
        print(f"  主机: {args.host}")
        print(f"  端口: {args.port}")
        print(f"  调试模式: {'启用' if args.debug else '禁用'}")
        print(f"  自动重载: {'启用' if args.reload else '禁用'}")
        print(f"  异步模式: {async_mode}")
        print(f"\nWeb服务器启动在 http://{args.host}:{args.port}")
        print(f"支持WebSocket via Flask-SocketIO")
        print("="*60)
        print("按 Ctrl+C 停止服务器\n")
        
        # 启动服务器
        socketio.run(
            app,
            host=args.host,
            port=args.port,
            debug=args.debug,
            use_reloader=args.reload,
            allow_unsafe_werkzeug=True
        )
    except KeyboardInterrupt:
        print("\n\n收到中断信号，正在优雅退出...")
        cleanup()
        sys.exit(0)
    except Exception as e:
        print(f"\n错误: 启动服务器失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

