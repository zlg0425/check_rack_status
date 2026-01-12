"""
Flask 应用工厂
"""
import logging
import os
from flask import Flask, jsonify
from flask_socketio import SocketIO

# 全局变量（将在 create_app 中初始化）
app = None
socketio = None


def create_app(config=None):
    """
    创建 Flask 应用实例（应用工厂模式）
    
    Args:
        config: 配置对象或字典
        
    Returns:
        Flask 应用实例
    """
    global app, socketio
    
    # 配置模板和静态文件路径
    template_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'templates')
    static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'static')
    
    # 确保路径存在
    if not os.path.exists(template_folder):
        raise RuntimeError(f"模板文件夹不存在: {template_folder}")
    if not os.path.exists(static_folder):
        raise RuntimeError(f"静态文件夹不存在: {static_folder}")
    
    # 创建 Flask 应用，配置静态文件 URL 路径为 '/static'
    app = Flask(__name__, 
                template_folder=template_folder, 
                static_folder=static_folder,
                static_url_path='/static')
    
    # 加载应用配置（Flask配置）
    if config:
        app.config.update(config)
    else:
        app.config['SECRET_KEY'] = 'terminal-secret-key-change-in-production'
    
    # 配置日志
    _configure_logging(app)
    
    # 加载项目配置（config.json）
    from app.utils.config import load_config
    load_config()
    
    # 初始化 SocketIO
    async_mode = _get_async_mode()
    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode=async_mode,
        logger=False,
        engineio_logger=False,
        ping_timeout=60,
        ping_interval=25,
        max_http_buffer_size=1e8,
        allow_upgrades=True,
        transports=['websocket', 'polling']
    )
    
    # 初始化扩展（SocketIO、任务管理器等）
    from app.extensions import init_extensions, start_background_tasks
    init_extensions(socketio)
    
    # ⚠️ 关键修复：先注册SocketIO事件处理器，再导入路由模块
    # 这样可以确保terminal模块中的socketio变量在导入时就能获取到正确的实例
    from app.routes.terminal import register_terminal_handlers
    register_terminal_handlers(socketio)
    
    # 注册路由（在SocketIO处理器注册之后）
    from app.routes import status, upload, download, fota, terminal, frontend
    app.register_blueprint(frontend.bp)  # 前端路由（首页）
    app.register_blueprint(status.bp)
    app.register_blueprint(upload.bp)
    app.register_blueprint(download.bp)
    app.register_blueprint(fota.bp)
    app.register_blueprint(terminal.bp)
    
    # 注册错误处理器（在路由注册之后）
    # 注意：Flask 会自动处理静态文件路由，静态文件请求不会到达错误处理器
    # 但如果静态文件不存在，Flask 会返回 404，然后错误处理器会被调用
    _register_error_handlers(app)
    
    # 启动后台任务（状态刷新循环）
    start_background_tasks()
    
    
    return app


def _configure_logging(app):
    """配置应用日志"""
    # 确保日志目录存在
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 配置Flask日志
    if not app.debug:
        # 生产环境：只记录WARNING及以上级别
        app.logger.setLevel(logging.WARNING)
    else:
        # 开发环境：记录所有级别
        app.logger.setLevel(logging.DEBUG)
    
    # 创建文件处理器（可选）
    # 注意：项目已有自己的日志系统（log_srv, log_fota），这里只配置Flask内置日志
    # 如果需要统一日志系统，可以在这里添加文件处理器


def _register_error_handlers(app):
    """注册错误处理器"""
    
    @app.errorhandler(400)
    def bad_request(error):
        """400错误处理"""
        from flask import request
        
        # 记录错误日志
        from app.utils.helpers import log_srv
        log_srv(f"400错误: 路径={request.path}, 方法={request.method}, 错误={error}")
        
        # API请求返回JSON
        if request.path.startswith('/api/'):
            return jsonify({"ok": False, "error": f"请求格式错误: {str(error)}"}), 400
        
        # 其他请求返回JSON
        return jsonify({"ok": False, "error": f"请求格式错误: {str(error)}"}), 400
    
    @app.errorhandler(404)
    def not_found(error):
        """404错误处理"""
        from flask import request
        
        # 静态文件请求：如果静态文件不存在，Flask 会返回 404
        # 对于静态文件 404，返回简单的文本响应（浏览器可以正常显示）
        if request.path.startswith('/static/'):
            from flask import make_response
            response = make_response('Not Found', 404)
            response.headers['Content-Type'] = 'text/plain; charset=utf-8'
            return response
        
        # API请求返回JSON
        if request.path.startswith('/api/'):
            return jsonify({"ok": False, "error": "资源未找到"}), 404
        
        # 其他请求（如页面路由）返回JSON
        # 注意：如果首页路由未注册，这里会返回 JSON，但浏览器期望 HTML
        # 所以前端路由应该已经注册了，这里只是兜底
        return jsonify({"ok": False, "error": "资源未找到"}), 404
    
    @app.errorhandler(405)
    def method_not_allowed(error):
        """405错误处理"""
        return jsonify({"ok": False, "error": "请求方法不允许"}), 405
    
    @app.errorhandler(500)
    def internal_error(error):
        """500错误处理"""
        # 记录错误日志
        from app.utils.helpers import log_srv
        log_srv(f"服务器内部错误: {error}")
        app.logger.error(f"服务器内部错误: {error}", exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
    
    @app.errorhandler(Exception)
    def handle_exception(error):
        """处理所有未捕获的异常"""
        # 记录错误日志
        from app.utils.helpers import log_srv
        log_srv(f"未处理的异常: {error}")
        app.logger.error(f"未处理的异常: {error}", exc_info=True)
        return jsonify({"ok": False, "error": f"服务器异常: {str(error)}"}), 500


def _get_async_mode():
    """获取 SocketIO 的异步模式"""
    try:
        import eventlet
        return 'eventlet'
    except ImportError:
        try:
            import gevent
            return 'gevent'
        except ImportError:
            return 'threading'


def get_app():
    """获取当前应用实例"""
    return app


def get_socketio():
    """获取 SocketIO 实例"""
    return socketio

