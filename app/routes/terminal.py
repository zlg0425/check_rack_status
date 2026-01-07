"""
终端相关路由和 SocketIO 事件处理器模块
"""
import threading
import asyncio
import os
import time
import traceback

from flask import Blueprint, request
from flask_socketio import emit

from app.utils.config import ssh_timeout, resolve_auth_mode, resolve_key, resolve_key_path, load_config
import app.utils.config as config
from core.ssh.transport import create_transport
from core.ssh.adapter import SSHConnectionAdapter, ConnectionConfig

# 从 app.utils.helpers 导入 log_srv
from app.utils.helpers import log_srv

bp = Blueprint('terminal', __name__)

# 从 app.extensions 导入任务管理器和 SocketIO
from app.extensions import get_task_managers, get_socketio

# 检查 SSH 适配器是否可用
try:
    from core.ssh.adapter import SSHConnectionAdapter
    SSH_ADAPTER_AVAILABLE = True
except ImportError:
    SSH_ADAPTER_AVAILABLE = False

# 获取任务管理器
task_managers = get_task_managers()
terminal_session_manager = task_managers['terminal_session']
terminal_event_loop_manager = task_managers['terminal_event_loop']

# 创建增强的会话管理器（基于 AsyncSSH 2.x 方案）
from app.models.terminal_session import EnhancedTerminalSessionManager
enhanced_session_manager = EnhancedTerminalSessionManager(terminal_session_manager)

# 为了兼容性，提供直接访问接口
terminal_sessions = terminal_session_manager._tasks
terminal_sessions_lock = terminal_session_manager._lock
terminal_event_loops = terminal_event_loop_manager._loops
terminal_event_loops_lock = terminal_event_loop_manager._lock

# 获取 SocketIO 实例
socketio = get_socketio()
if socketio is None:
    # 如果 SocketIO 未初始化，创建占位符
    class MockSocketIO:
        def emit(self, *args, **kwargs):
            pass
    
    socketio = MockSocketIO()

def get_or_create_event_loop():
    """获取或创建当前线程的事件循环"""
    return terminal_event_loop_manager.get_or_create_loop()

# SocketIO 事件处理器
# 注意：这些事件处理器需要在应用初始化时注册
# 在 app/__init__.py 中调用 register_terminal_handlers() 来注册

def register_terminal_handlers(socketio_instance):
    """注册终端相关的 SocketIO 事件处理器
    
    Args:
        socketio_instance: SocketIO 实例
    """
    global socketio
    socketio = socketio_instance
    
    @socketio.on('connect')
    def handle_terminal_connect():
        """WebSocket连接建立时触发"""
        session_id = request.sid
        
        # #region agent log
        import json
        import time as time_module
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run1",
                    "hypothesisId": "SESSION_CONNECT",
                    "location": "app/routes/terminal.py:handle_terminal_connect:start",
                    "message": "WebSocket连接建立",
                    "data": {
                        "session_id": session_id,
                        "request_sid": request.sid
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        log_srv(f"[{session_id}] 终端连接建立")
        
        # 完全按照迁移前方案：不显式加入room，Flask-SocketIO会自动处理
        pass
    
    @socketio.on('disconnect')
    def handle_terminal_disconnect():
        """WebSocket连接断开时触发"""
        session_id = request.sid
        
        log_srv(f"终端断开连接: {session_id}")
        
        # 清理SSH会话
        with terminal_sessions_lock:
            if session_id not in terminal_sessions:
                return
            
            session = terminal_sessions[session_id]
            
            try:
                # 1. 关闭 Shell（适配层方式 - 异步）
                ssh_shell = session.get("ssh_shell")
                if ssh_shell:
                    try:
                        # 检查是否已关闭
                        if hasattr(ssh_shell, 'is_closed') and ssh_shell.is_closed():
                            log_srv(f"[{session_id}] Shell已关闭")
                        elif hasattr(ssh_shell, 'is_closing') and ssh_shell.is_closing():
                            log_srv(f"[{session_id}] Shell正在关闭")
                        else:
                            # ShellWrapper的close()是异步方法
                            loop = session.get("loop") or get_or_create_event_loop()
                            close_coro = ssh_shell.close()
                            
                            if asyncio.iscoroutine(close_coro):
                                if loop.is_running():
                                    asyncio.run_coroutine_threadsafe(close_coro, loop)
                                else:
                                    loop.run_until_complete(close_coro)
                            else:
                                log_srv(f"[{session_id}] Shell close()返回非协程: {type(close_coro)}")
                    except Exception as e:
                        log_srv(f"[{session_id}] 关闭shell失败: {e}")
                        log_srv(traceback.format_exc())
                
                # 2. 关闭 SSH 连接（asyncssh方式）
                ssh_conn = session.get("ssh_conn")
                if ssh_conn:
                    try:
                        # asyncssh.SSHClientConnection.close() 是同步方法，返回 None
                        # 直接调用即可，不需要特殊处理
                        if hasattr(ssh_conn, 'is_closed') and ssh_conn.is_closed():
                            log_srv(f"[{session_id}] SSH连接已关闭")
                        elif hasattr(ssh_conn, 'is_closing') and ssh_conn.is_closing():
                            log_srv(f"[{session_id}] SSH连接正在关闭")
                        else:
                            # 直接调用close()，它是同步方法
                            ssh_conn.close()
                            log_srv(f"[{session_id}] SSH连接已关闭")
                    except Exception as e:
                        log_srv(f"[{session_id}] 关闭SSH连接失败: {e}")
                        log_srv(traceback.format_exc())
                
                # 3. 关闭 paramiko 连接（同步方式）
                ssh_channel = session.get("ssh_channel")
                if ssh_channel:
                    try:
                        if not ssh_channel.closed:
                            ssh_channel.close()
                            log_srv(f"[{session_id}] Paramiko通道已关闭")
                    except Exception as e:
                        log_srv(f"[{session_id}] 关闭Paramiko通道失败: {e}")
                
                ssh_transport = session.get("ssh_transport")
                if ssh_transport:
                    try:
                        if ssh_transport.is_active():
                            ssh_transport.close()
                            log_srv(f"[{session_id}] Paramiko传输已关闭")
                    except Exception as e:
                        log_srv(f"[{session_id}] 关闭Paramiko传输失败: {e}")
                
            except Exception as e:
                log_srv(f"[{session_id}] 清理会话时发生异常: {e}")
                log_srv(traceback.format_exc())
            finally:
                # 从会话字典中移除
                if session_id in terminal_sessions:
                    del terminal_sessions[session_id]
                    log_srv(f"[{session_id}] 会话已从字典中移除")
    
@socketio.on('start_ssh')
def handle_start_ssh(data):
    # #region agent log
    import json
    import time as time_module
    session_id = request.sid
    try:
        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "sessionId": session_id,
                "runId": "run2",
                "hypothesisId": "BACKEND_START_SSH_RECEIVED",
                "location": "app/routes/terminal.py:handle_start_ssh:start",
                "message": "后端收到start_ssh事件",
                "data": {
                    "session_id": session_id,
                    "data": data,
                    "has_server_name": 'server_name' in data,
                    "has_server_ip": 'server_ip' in data,
                    "has_port": 'port' in data,
                    "has_cols": 'cols' in data,
                    "has_rows": 'rows' in data
                },
                "timestamp": int(time_module.time() * 1000)
            }) + '\n')
    except Exception:
        pass
    # #endregion
        """前端请求开始一个新的SSH会话"""
        session_id = request.sid
        
        # #region agent log
        import json
        import time as time_module
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run1",
                    "hypothesisId": "TERMINAL_START_SSH",
                    "location": "app/routes/terminal.py:handle_start_ssh:start",
                    "message": "开始SSH会话请求",
                    "data": {"session_id": session_id, "data_type": type(data).__name__},
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 参数验证和提取
        if not isinstance(data, dict):
            emit('error', {'message': f'请求数据格式错误: 期望dict，收到{type(data).__name__}'})
            return
        
        try:
            server_name = data.get('server_name', '').strip()
            server_ip = data.get('server_ip', '').strip()
            port = int(data.get('port', 22))
            cols = int(data.get('cols', 80))
            rows = int(data.get('rows', 24))
        except (ValueError, TypeError) as e:
            emit('error', {'message': f'参数格式错误: {str(e)}'})
            return
        
        if not server_name or not server_ip:
            emit('error', {'message': '缺少服务器信息'})
            return
        
        # 优先使用适配层（异步），如果不可用则回退到paramiko（同步）
        use_adapter = SSH_ADAPTER_AVAILABLE
        
        if use_adapter:
            # 使用适配层（异步方式）
            
            try:
                async def connect_ssh_async():
                    """异步建立SSH连接"""
                    try:
                        # 确保配置已加载
                        load_config()
                        # 使用模块引用以获取动态更新的值
                        current_ssh_username = config.ssh_username
                        # 如果 ssh_username 为空，抛出异常
                        if not current_ssh_username or not current_ssh_username.strip():
                            error_msg = f"SSH用户名未配置：请在 config.json 中设置 'ssh_username' 字段"
                            import logging
                            logging.error(error_msg)
                            raise ValueError(error_msg)
                        
                        final_username = current_ssh_username.strip()
                        
                        # 解析认证信息
                        auth_mode = resolve_auth_mode(server_name)
                        key_path = None
                        if auth_mode == "key":
                            key_path = resolve_key(server_name, port)
                            
                            
                            
                            if key_path and not os.path.isabs(key_path):
                                # 转换为绝对路径（使用 resolve_key_path 函数）
                                key_path = resolve_key_path(key_path)
                                
                                
                        
                        # 创建适配器和配置
                        adapter = SSHConnectionAdapter(enable_connection_pool=False)
                        
                        # 主机密钥验证配置
                        # 方案3：测试环境（当前配置）- 跳过主机密钥验证
                        # 关键：确保 keepalive 配置正确使用（解决回车断开连接问题）
                        conn_config = ConnectionConfig(
                            host=server_ip,
                            port=port,
                            username=final_username,
                            client_keys=[key_path] if key_path else None,
                            connect_timeout=ssh_timeout,
                            skip_host_key_check=True,       # 跳过主机密钥验证（客户端不验证服务器身份）
                            known_hosts=None,               # 不使用 known_hosts 文件
                            keepalive_interval=30,          # 保活间隔30秒（关键：防止连接断开）
                            keepalive_count_max=3          # 最大保活次数
                        )
                        
                        # 建立连接
                        try:
                            
                            ssh_conn = await adapter.create_connection(conn_config)
                            log_srv(f"[{session_id}] SSH连接建立成功: {server_ip}:{port}")
                            
                        except Exception as conn_err:
                            # 捕获并详细记录连接错误
                            error_type = type(conn_err).__name__
                            error_msg = str(conn_err)
                            log_srv(f"[{session_id}] SSH连接失败: {error_type}: {error_msg}")
                            log_srv(f"[{session_id}] 连接参数: host={server_ip}, port={port}, username={final_username}, key_path={key_path}")
                            log_srv(traceback.format_exc())
                            raise
                        
                        # 创建交互式shell
                        try:
                            
                            ssh_shell = await adapter.open_shell(
                                ssh_conn,
                                term_type='xterm-256color',
                                cols=cols,
                                rows=rows
                            )
                            log_srv(f"[{session_id}] Shell创建成功")
                            
                        except Exception as shell_err:
                            # 捕获并详细记录Shell创建错误
                            error_type = type(shell_err).__name__
                            error_msg = str(shell_err)
                            log_srv(f"[{session_id}] Shell创建失败: {error_type}: {error_msg}")
                            log_srv(traceback.format_exc())
                            # 关闭已建立的连接
                            try:
                                await adapter.close_connection()
                            except:
                                pass
                            raise
                        
                        return adapter, ssh_conn, ssh_shell, conn_config
                    except Exception as e:
                        # 最终错误处理：记录完整的错误信息
                        error_type = type(e).__name__
                        error_msg = str(e)
                        log_srv(f"[{session_id}] 异步SSH连接失败: {error_type}: {error_msg}")
                        log_srv(f"[{session_id}] 完整堆栈:")
                        log_srv(traceback.format_exc())
                        
                        raise
                
                # 在新线程中运行异步连接
                def run_async_connection():
                    """在新线程中运行异步连接"""
                    
                    # #region agent log
                    import json
                    import time as time_module
                    try:
                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "sessionId": session_id,
                                "runId": "run1",
                                "hypothesisId": "RUN_ASYNC_START",
                                "location": "app/routes/terminal.py:run_async_connection:start",
                                "message": "run_async_connection开始执行",
                                "data": {
                                    "session_id": session_id,
                                    "thread_id": threading.get_ident()
                                },
                                "timestamp": int(time_module.time() * 1000)
                            }) + '\n')
                    except Exception:
                        pass
                    # #endregion
                    
                    # 使用全局socketio实例（参考迁移前实现）
                    # 迁移前直接使用全局socketio，不需要获取实例
                    loop = get_or_create_event_loop()
                    
                    # #region agent log
                    try:
                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "sessionId": session_id,
                                "runId": "run1",
                                "hypothesisId": "RUN_ASYNC_LOOP",
                                "location": "app/routes/terminal.py:run_async_connection:loop_created",
                                "message": "事件循环已获取",
                                "data": {
                                    "session_id": session_id,
                                    "loop_running": loop.is_running(),
                                    "loop_closed": loop.is_closed()
                                },
                                "timestamp": int(time_module.time() * 1000)
                            }) + '\n')
                    except Exception:
                        pass
                    # #endregion
                    
                    try:
                        # 在新线程中直接使用全局socketio.emit（参考迁移前实现）
                        # 注意：room 参数应该是 session_id（客户端的 session ID）
                        
                        
                        
                        
                        # #region agent log
                        import json
                        import time as time_module
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run1",
                                    "hypothesisId": "RUN_ASYNC_CONNECT",
                                    "location": "app/routes/terminal.py:run_async_connection:before_connect",
                                    "message": "准备调用connect_ssh_async",
                                    "data": {"session_id": session_id},
                                    "timestamp": int(time_module.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        
                        adapter, ssh_conn, ssh_shell, conn_config = loop.run_until_complete(connect_ssh_async())
                        
                        # #region agent log
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run1",
                                    "hypothesisId": "RUN_ASYNC_CONNECT",
                                    "location": "app/routes/terminal.py:run_async_connection:after_connect",
                                    "message": "connect_ssh_async完成",
                                    "data": {
                                        "session_id": session_id,
                                        "has_adapter": adapter is not None,
                                        "has_conn": ssh_conn is not None,
                                        "has_shell": ssh_shell is not None
                                    },
                                    "timestamp": int(time_module.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        
                        # 保存会话（完全按照迁移前方案，不使用增强的会话管理器）
                        with terminal_sessions_lock:
                            terminal_sessions[session_id] = {
                                "ssh_conn": ssh_conn,
                                "ssh_shell": ssh_shell,
                                "adapter": adapter,
                                "server_name": server_name,
                                "server_ip": server_ip,
                                "port": port,
                                "loop": loop
                            }
                        
                        # 发送连接成功消息（完全按照迁移前方案）
                        # #region agent log
                        import json
                        import time as time_module
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run1",
                                    "hypothesisId": "CONNECTED_EMIT",
                                    "location": "app/routes/terminal.py:run_async_connection:before_connected_emit",
                                    "message": "准备发送connected事件",
                                    "data": {
                                        "session_id": session_id,
                                        "socketio_type": type(socketio).__name__,
                                        "socketio_id": id(socketio),
                                        "has_emit_method": hasattr(socketio, 'emit'),
                                        "is_mock": type(socketio).__name__ == "MockSocketIO"
                                    },
                                    "timestamp": int(time_module.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        try:
                            # 恢复room参数：确保事件只发送给正确的客户端
                            # #region agent log
                            import json
                            import time as time_module
                            try:
                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        "sessionId": session_id,
                                        "runId": "run2",
                                        "hypothesisId": "SOCKETIO_EMIT_CONNECTED",
                                        "location": "app/routes/terminal.py:run_async_connection:emit_connected",
                                        "message": "后端发送connected事件",
                                        "data": {
                                            "session_id": session_id,
                                            "room": session_id,
                                            "socketio_type": type(socketio).__name__,
                                            "has_emit": hasattr(socketio, 'emit')
                                        },
                                        "timestamp": int(time_module.time() * 1000)
                                    }) + '\n')
                            except Exception:
                                pass
                            # #endregion
                            socketio.emit('connected', {'session_id': session_id}, room=session_id)
                            # #region agent log
                            try:
                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        "sessionId": session_id,
                                        "runId": "run1",
                                        "hypothesisId": "CONNECTED_EMIT",
                                        "location": "app/routes/terminal.py:run_async_connection:after_connected_emit",
                                        "message": "connected事件已发送",
                                        "data": {
                                            "session_id": session_id,
                                            "room": session_id
                                        },
                                        "timestamp": int(time_module.time() * 1000)
                                    }) + '\n')
                            except Exception:
                                pass
                            # #endregion
                        except Exception as emit_err:
                            # #region agent log
                            try:
                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        "sessionId": session_id,
                                        "runId": "run1",
                                        "hypothesisId": "CONNECTED_EMIT_ERROR",
                                        "location": "app/routes/terminal.py:run_async_connection:connected_emit_error",
                                        "message": "connected事件发送失败",
                                        "data": {
                                            "session_id": session_id,
                                            "error": str(emit_err),
                                            "error_type": type(emit_err).__name__
                                        },
                                        "timestamp": int(time_module.time() * 1000)
                                    }) + '\n')
                            except Exception:
                                pass
                            # #endregion
                            log_srv(f"[{session_id}] 发送connected事件失败: {emit_err}")
                        
                        
                        
                        # 启动异步读取SSH输出
                        async def read_ssh_output_async():
                            """异步读取SSH输出"""
                            # #region agent log
                            import json
                            try:
                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        "sessionId": session_id,
                                        "runId": "run2",
                                        "hypothesisId": "READ_TASK_STARTED",
                                        "location": "app/routes/terminal.py:read_ssh_output_async:start",
                                        "message": "SSH输出读取循环开始",
                                        "data": {
                                            "session_id": session_id,
                                            "thread_id": threading.get_ident(),
                                            "loop_running": loop.is_running() if loop else None,
                                            "ssh_shell_exists": ssh_shell is not None,
                                            "ssh_conn_exists": ssh_conn is not None
                                        },
                                        "timestamp": int(time.time() * 1000)
                                    }) + '\n')
                            except Exception:
                                pass
                            # #endregion
                            log_srv(f"[{session_id}] READ_START: 开始SSH输出读取循环")
                            try:
                                while True:
                                    # 读取shell输出（asyncssh使用read()方法）
                                    try:
                                        # asyncssh的read()方法返回bytes，需要decode
                                        data = await asyncio.wait_for(ssh_shell.read(4096), timeout=0.1)
                                        # #region agent log
                                        try:
                                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                f.write(json.dumps({
                                                    "sessionId": session_id,
                                                    "runId": "run1",
                                                    "hypothesisId": "H2",
                                                    "location": "app/routes/terminal.py:read_ssh_output_async:read_data",
                                                    "message": "读取到SSH数据",
                                                    "data": {
                                                        "session_id": session_id,
                                                        "data_length": len(data) if data else 0,
                                                        "data_type": type(data).__name__,
                                                        "is_bytes": isinstance(data, bytes),
                                                        "is_none": data is None,
                                                        "data_preview": repr(data[:50]) if data and len(data) > 0 else None
                                                    },
                                                    "timestamp": int(time.time() * 1000)
                                                }) + '\n')
                                        except Exception:
                                            pass
                                        # #endregion
                                        if data:
                                            log_srv(f"[{session_id}] READ_DATA: 读取到数据长度={len(data)}, 类型={type(data).__name__}")
                                            # 确保data是bytes类型，如果是str则直接使用（完全按照迁移前方案）
                                            # #region agent log
                                            try:
                                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                    f.write(json.dumps({
                                                        "sessionId": session_id,
                                                        "runId": "run1",
                                                        "hypothesisId": "OUTPUT_EMIT",
                                                        "location": "app/routes/terminal.py:read_ssh_output_async:before_output_emit",
                                                        "message": "准备发送output事件",
                                                        "data": {
                                                            "session_id": session_id,
                                                            "data_length": len(data) if data else 0,
                                                            "data_type": type(data).__name__,
                                                            "socketio_type": type(socketio).__name__,
                                                            "socketio_id": id(socketio),
                                                            "has_emit_method": hasattr(socketio, 'emit'),
                                                            "room": session_id
                                                        },
                                                        "timestamp": int(time.time() * 1000)
                                                    }) + '\n')
                                            except Exception:
                                                pass
                                            # #endregion
                                            try:
                                                if isinstance(data, bytes):
                                                    # 恢复room参数：确保事件只发送给正确的客户端
                                                    # #region agent log
                                                    import json
                                                    import time as time_module
                                                    try:
                                                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                            f.write(json.dumps({
                                                                "sessionId": session_id,
                                                                "runId": "run2",
                                                                "hypothesisId": "SOCKETIO_EMIT_OUTPUT",
                                                                "location": "app/routes/terminal.py:read_ssh_output_async:emit_output_bytes",
                                                                "message": "后端发送output事件(bytes)",
                                                                "data": {
                                                                    "session_id": session_id,
                                                                    "room": session_id,
                                                                    "data_length": len(data),
                                                                    "data_preview": data.decode('utf-8', errors='ignore')[:50],
                                                                    "socketio_type": type(socketio).__name__
                                                                },
                                                                "timestamp": int(time_module.time() * 1000)
                                                            }) + '\n')
                                                    except Exception:
                                                        pass
                                                    # #endregion
                                                    socketio.emit('output', {'data': data.decode('utf-8', errors='ignore')}, room=session_id)
                                                else:
                                                    # 恢复room参数：确保事件只发送给正确的客户端
                                                    # #region agent log
                                                    import json
                                                    import time as time_module
                                                    try:
                                                        with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                            f.write(json.dumps({
                                                                "sessionId": session_id,
                                                                "runId": "run2",
                                                                "hypothesisId": "SOCKETIO_EMIT_OUTPUT",
                                                                "location": "app/routes/terminal.py:read_ssh_output_async:emit_output_str",
                                                                "message": "后端发送output事件(str)",
                                                                "data": {
                                                                    "session_id": session_id,
                                                                    "room": session_id,
                                                                    "data_length": len(data),
                                                                    "data_preview": str(data)[:50],
                                                                    "socketio_type": type(socketio).__name__
                                                                },
                                                                "timestamp": int(time_module.time() * 1000)
                                                            }) + '\n')
                                                    except Exception:
                                                        pass
                                                    # #endregion
                                                    socketio.emit('output', {'data': str(data)}, room=session_id)
                                            except Exception as emit_err:
                                                # #region agent log
                                                try:
                                                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                        f.write(json.dumps({
                                                            "sessionId": session_id,
                                                            "runId": "run1",
                                                            "hypothesisId": "OUTPUT_EMIT_ERROR",
                                                            "location": "app/routes/terminal.py:read_ssh_output_async:output_emit_error",
                                                            "message": "output事件发送失败",
                                                            "data": {
                                                                "session_id": session_id,
                                                                "error": str(emit_err),
                                                                "error_type": type(emit_err).__name__,
                                                                "socketio_type": type(socketio).__name__,
                                                                "is_mock": type(socketio).__name__ == "MockSocketIO"
                                                            },
                                                            "timestamp": int(time.time() * 1000)
                                                        }) + '\n')
                                                except Exception:
                                                    pass
                                                # #endregion
                                                log_srv(f"[{session_id}] 发送output事件失败: {emit_err}")
                                                raise
                                            # #region agent log
                                            try:
                                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                    f.write(json.dumps({
                                                        "sessionId": session_id,
                                                        "runId": "run1",
                                                        "hypothesisId": "OUTPUT_EMIT",
                                                        "location": "app/routes/terminal.py:read_ssh_output_async:after_output_emit",
                                                        "message": "output事件已发送",
                                                        "data": {
                                                            "session_id": session_id,
                                                            "data_length": len(data) if data else 0,
                                                            "room": session_id
                                                        },
                                                        "timestamp": int(time.time() * 1000)
                                                    }) + '\n')
                                            except Exception:
                                                pass
                                            # #endregion
                                            # #region agent log
                                            try:
                                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                                    f.write(json.dumps({
                                                        "sessionId": session_id,
                                                        "runId": "run1",
                                                        "hypothesisId": "H3",
                                                        "location": "app/routes/terminal.py:read_ssh_output_async:after_emit",
                                                        "message": "output事件已发送",
                                                        "data": {
                                                            "session_id": session_id,
                                                            "event_name": "output",
                                                            "room": session_id,
                                                            "data_length": len(data) if data else 0
                                                        },
                                                        "timestamp": int(time.time() * 1000)
                                                    }) + '\n')
                                            except Exception:
                                                pass
                                            # #endregion
                                            log_srv(f"[{session_id}] OUTPUT_SENT: 已发送output事件")
                                    except asyncio.TimeoutError:
                                        # 超时是正常的，继续循环
                                        pass
                                    except Exception as read_error:
                                        # 读取错误，可能连接已关闭（完全按照迁移前方案）
                                        log_srv(f"[{session_id}] SSH读取错误: {read_error}")
                                        break
                                    
                                    # 优化：更准确的连接状态检测
                                    try:
                                        # 检查退出状态（如果shell已退出）
                                        if hasattr(ssh_shell, 'exit_status'):
                                            exit_status = ssh_shell.exit_status()
                                            if exit_status is not None:
                                                log_srv(f"Shell退出状态: {exit_status}，退出读取循环")
                                                break
                                        
                                        # 关键改进：只检查 is_closed()，不检查 is_closing()
                                        if hasattr(ssh_shell, 'is_closed') and ssh_shell.is_closed():
                                            log_srv("Shell已关闭，退出读取循环")
                                            break
                                        
                                    except Exception as check_error:
                                        # 如果检查退出状态失败，继续读取（避免误判）
                                        pass
                                    
                                    await asyncio.sleep(0.01)
                            except Exception as e:
                                # 捕获并记录读取循环的异常
                                error_type = type(e).__name__
                                error_msg = str(e)
                                log_srv(f"[{session_id}] SSH读取循环异常: {error_type}: {error_msg}")
                                log_srv(traceback.format_exc())
                                try:
                                    socketio.emit('error', {
                                        'message': f'SSH读取循环错误: {error_type}',
                                        'error': error_msg,
                                        'error_type': error_type
                                    }, room=session_id)
                                except:
                                    pass
                            finally:
                                try:
                                    socketio.emit('disconnected', {}, room=session_id)
                                except:
                                    pass
                        
                        # 在事件循环中运行读取任务（完全按照迁移前方案：先调用run_coroutine_threadsafe）
                        # #region agent log
                        import json
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run2",
                                    "hypothesisId": "EVENT_LOOP_SCHEDULE_READ",
                                    "location": "app/routes/terminal.py:run_async_connection:schedule_read_task",
                                    "message": "准备启动SSH输出读取任务",
                                    "data": {
                                        "session_id": session_id,
                                        "loop_running": loop.is_running(),
                                        "loop_closed": loop.is_closed(),
                                        "loop_thread_alive": loop_thread.is_alive() if 'loop_thread' in locals() else False
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        log_srv(f"[{session_id}] 准备启动SSH输出读取任务")
                        read_task = asyncio.run_coroutine_threadsafe(read_ssh_output_async(), loop)
                        # #region agent log
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run2",
                                    "hypothesisId": "EVENT_LOOP_SCHEDULE_READ",
                                    "location": "app/routes/terminal.py:run_async_connection:read_task_scheduled",
                                    "message": "SSH输出读取任务已调度",
                                    "data": {
                                        "session_id": session_id,
                                        "read_task_done": read_task.done(),
                                        "read_task_cancelled": read_task.cancelled()
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        # #region agent log
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run1",
                                    "hypothesisId": "H1",
                                    "location": "app/routes/terminal.py:run_async_connection:read_task_started",
                                    "message": "SSH输出读取任务已启动",
                                    "data": {
                                        "session_id": session_id,
                                        "loop_running": loop.is_running()
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        log_srv(f"[{session_id}] SSH输出读取任务已启动")
                        
                        # 启动事件循环（如果还没有运行）（完全按照迁移前方案：后启动事件循环）
                        def run_event_loop():
                            """在新线程中运行事件循环"""
                            try:
                                # #region agent log
                                import json
                                import time as time_module
                                try:
                                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                        f.write(json.dumps({
                                            "sessionId": session_id,
                                            "runId": "run1",
                                            "hypothesisId": "EVENT_LOOP_START",
                                            "location": "app/routes/terminal.py:run_event_loop:start",
                                            "message": "事件循环线程开始运行",
                                            "data": {
                                                "session_id": session_id,
                                                "thread_id": threading.get_ident(),
                                                "loop_running": loop.is_running()
                                            },
                                            "timestamp": int(time_module.time() * 1000)
                                        }) + '\n')
                                except Exception:
                                    pass
                                # #endregion
                                log_srv(f"[{session_id}] 事件循环线程开始运行")
                                loop.run_forever()
                                # #region agent log
                                try:
                                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                        f.write(json.dumps({
                                            "sessionId": session_id,
                                            "runId": "run1",
                                            "hypothesisId": "EVENT_LOOP_STOP",
                                            "location": "app/routes/terminal.py:run_event_loop:stop",
                                            "message": "事件循环线程停止",
                                            "data": {"session_id": session_id},
                                            "timestamp": int(time_module.time() * 1000)
                                        }) + '\n')
                                except Exception:
                                    pass
                                # #endregion
                            except Exception as loop_err:
                                log_srv(f"[{session_id}] 事件循环异常: {loop_err}")
                                # #region agent log
                                try:
                                    with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                        f.write(json.dumps({
                                            "sessionId": session_id,
                                            "runId": "run1",
                                            "hypothesisId": "EVENT_LOOP_ERROR",
                                            "location": "app/routes/terminal.py:run_event_loop:error",
                                            "message": "事件循环异常",
                                            "data": {
                                                "session_id": session_id,
                                                "error": str(loop_err),
                                                "error_type": type(loop_err).__name__
                                            },
                                            "timestamp": int(time_module.time() * 1000)
                                        }) + '\n')
                                except Exception:
                                    pass
                                # #endregion
                        
                        if not loop.is_running():
                            # #region agent log
                            try:
                                with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({
                                        "sessionId": session_id,
                                        "runId": "run1",
                                        "hypothesisId": "EVENT_LOOP_THREAD_START",
                                        "location": "app/routes/terminal.py:run_async_connection:start_loop_thread",
                                        "message": "启动事件循环线程",
                                        "data": {
                                            "session_id": session_id,
                                            "loop_running": loop.is_running(),
                                            "loop_closed": loop.is_closed()
                                        },
                                        "timestamp": int(time.time() * 1000)
                                    }) + '\n')
                            except Exception:
                                pass
                            # #endregion
                            loop_thread = threading.Thread(target=run_event_loop, daemon=True)
                            loop_thread.start()
                        
                    except Exception as e:
                        # 详细记录错误信息
                        error_type = type(e).__name__
                        error_msg = str(e)
                        error_traceback = traceback.format_exc()
                        
                        # 记录到日志
                        log_srv(f"[{session_id}] SSH连接异常: {error_type}: {error_msg}")
                        log_srv(f"[{session_id}] 完整堆栈:")
                        log_srv(error_traceback)
                        
                        try:
                            # 发送详细的错误信息到前端
                            error_detail = {
                                'message': f'SSH连接失败: {error_type}',
                                'error': error_msg,
                                'error_type': error_type,
                                'server_name': server_name,
                                'server_ip': server_ip,
                                'port': port
                            }
                            socketio.emit('error', error_detail, room=session_id)
                            log_srv(f"[{session_id}] 错误信息已发送到前端: {error_detail}")
                        except Exception as emit_err:
                            log_srv(f"[{session_id}] 发送error事件失败: {emit_err}")
                
                # 启动连接线程
                
                conn_thread = threading.Thread(target=run_async_connection, daemon=True)
                conn_thread.start()
                
                
            except Exception as e:
                log_srv(f"启动异步SSH连接失败: {e}")
                log_srv(traceback.format_exc())
                emit('error', {'message': f'连接错误: {str(e)}'})
        
        else:
            # 回退到paramiko（同步方式）
            ssh_channel = None
            ssh_transport = None
            
            try:
                # 建立SSH连接
                ssh_transport = create_transport(server_name, server_ip, port)
                if not ssh_transport:
                    emit('error', {'message': 'SSH连接失败'})
                    return
                
                # 创建交互式shell
                ssh_channel = ssh_transport.open_session()
                ssh_channel.get_pty(term='xterm-256color', width=cols, height=rows)
                ssh_channel.invoke_shell()
                
                # 保存会话
                with terminal_sessions_lock:
                    terminal_sessions[session_id] = {
                        "ssh_channel": ssh_channel,
                        "ssh_transport": ssh_transport,
                        "server_name": server_name,
                        "server_ip": server_ip,
                        "port": port
                    }
                
                # 发送连接成功消息
                emit('connected', {'session_id': session_id})
                
                # 启动线程读取SSH输出
                def read_ssh_output():
                    try:
                        while True:
                            if ssh_channel.recv_ready():
                                data = ssh_channel.recv(4096)
                                if data:
                                    socketio.emit('output', {'data': data.decode('utf-8', errors='ignore')}, room=session_id)
                            elif ssh_channel.exit_status_ready():
                                break
                            time.sleep(0.01)
                    except Exception as e:
                        try:
                            socketio.emit('error', {'message': f'SSH读取错误: {str(e)}'}, room=session_id)
                        except:
                            pass
                    finally:
                        try:
                            socketio.emit('disconnected', {}, room=session_id)
                        except:
                            pass
                
                ssh_thread = threading.Thread(target=read_ssh_output, daemon=True)
                ssh_thread.start()
                
            except Exception as e:
                try:
                    emit('error', {'message': f'连接错误: {str(e)}'})
                except:
                    pass
                # 清理资源
                if ssh_channel:
                    try:
                        ssh_channel.close()
                    except:
                        pass
                if ssh_transport:
                    try:
                        ssh_transport.close()
                    except:
                        pass
    
    @socketio.on('terminal_input')
    def handle_terminal_input(data):
        """接收从前端终端发来的数据（按键）"""
        session_id = request.sid

        # #region agent log
        import json
        import time as time_module
        try:
            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "sessionId": session_id,
                    "runId": "run2",
                    "hypothesisId": "TERMINAL_INPUT_RECEIVED",
                    "location": "app/routes/terminal.py:handle_terminal_input:start",
                    "message": "后端收到terminal_input事件",
                    "data": {
                        "session_id": session_id,
                        "data": data,
                        "data_type": type(data).__name__,
                        "has_data_key": isinstance(data, dict) and 'data' in data,
                        "input_data": data.get('data', '') if isinstance(data, dict) else None,
                        "input_data_repr": repr(data.get('data', '')) if isinstance(data, dict) and 'data' in data else None
                    },
                    "timestamp": int(time_module.time() * 1000)
                }) + '\n')
        except Exception:
            pass
        # #endregion
        
        # 参数验证
        if not isinstance(data, dict):
            try:
                socketio.emit('error', {'message': f'请求数据格式错误: 期望dict，收到{type(data).__name__}'}, room=session_id)
            except:
                pass
            return
        
        input_data = data.get('data', '')
        
        # 重要：记录前端发送的原始数据格式（特别是回车符）
        # 回车键可能发送 '\n'、'\r' 或 '\r\n'，这些都是正常的
        input_repr = repr(input_data)
        has_newline = '\n' in input_data
        has_carriage_return = '\r' in input_data
        
        # 记录包含换行符的数据（用于调试回车键问题）
        if has_newline or has_carriage_return:
            log_srv(f"收到前端输入（包含换行符）: {input_repr}")
        
        # 完全按照迁移前方案：直接从terminal_sessions获取会话
        with terminal_sessions_lock:
            if session_id in terminal_sessions:
                session = terminal_sessions[session_id]
                
                # 适配层方式（异步）
                ssh_shell = session.get("ssh_shell")
                if ssh_shell:
                    try:
                        # 检查shell是否已关闭
                        if hasattr(ssh_shell, 'is_closed') and ssh_shell.is_closed():
                            return

                        # #region agent log
                        import json
                        import time as time_module
                        try:
                            with open('.cursor/debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({
                                    "sessionId": session_id,
                                    "runId": "run2",
                                    "hypothesisId": "INPUT_TO_SSH_SHELL",
                                    "location": "app/routes/terminal.py:handle_terminal_input:before_write",
                                    "message": "准备向SSH shell写入输入",
                                    "data": {
                                        "session_id": session_id,
                                        "input_data": input_data,
                                        "input_data_repr": repr(input_data),
                                        "ssh_shell_type": type(ssh_shell).__name__,
                                        "ssh_shell_closed": hasattr(ssh_shell, 'is_closed') and ssh_shell.is_closed()
                                    },
                                    "timestamp": int(time_module.time() * 1000)
                                }) + '\n')
                        except Exception:
                            pass
                        # #endregion
                        
                        # ShellWrapper的write()现在是异步方法，需要使用await
                        # 确保输入数据是字符串
                        if isinstance(input_data, bytes):
                            input_data = input_data.decode('utf-8', errors='ignore')
                        
                        # 获取事件循环并异步调用write()
                        loop = session.get("loop") or get_or_create_event_loop()
                        
                        write_coro = ssh_shell.write(input_data)
                        
                        if loop.is_running():
                            # 如果事件循环正在运行，使用run_coroutine_threadsafe
                            future = asyncio.run_coroutine_threadsafe(write_coro, loop)
                        else:
                            # 如果事件循环未运行，运行直到完成
                            loop.run_until_complete(write_coro)
                            
                    except Exception as e:
                        # 重要：记录详细的错误信息，包括输入数据
                        error_msg = f"SSH写入错误: {e} (输入数据: {input_repr})"
                        log_srv(error_msg)
                        log_srv(traceback.format_exc())
                        
                        # 重要：不要因为写入失败就断开连接
                        # 只发送错误消息给前端，让用户知道发生了什么
                        try:
                            socketio.emit('error', {'message': f'输入处理失败: {str(e)}'}, room=session_id)
                        except:
                            pass
                        
                        # 不抛出异常，避免触发断开连接逻辑
                        # 如果连接真的有问题，会在读取循环中检测到
                
                # paramiko方式（同步）- 需要检查session是否存在
                if session:
                    ssh_channel = session.get("ssh_channel")
                    if ssh_channel and not ssh_channel.closed:
                        try:
                            ssh_channel.send(input_data)
                        except:
                            pass
                else:
                    log_srv(f"[{session_id}] 会话不存在，无法发送输入数据")
    
    @socketio.on('terminal_resize')
    def handle_terminal_resize(data):
        """调整终端尺寸"""
        session_id = request.sid
        cols = int(data.get('cols', 80))
        rows = int(data.get('rows', 24))
        
        with terminal_sessions_lock:
            if session_id in terminal_sessions:
                session = terminal_sessions[session_id]
                
                # 适配层方式（异步）
                ssh_shell = session.get("ssh_shell")
                if ssh_shell:
                    try:
                        # 检查shell是否已关闭
                        if hasattr(ssh_shell, 'is_closing') and ssh_shell.is_closing():
                            return
                        if hasattr(ssh_shell, 'is_closed') and ssh_shell.is_closed():
                            return
                        
                        # ShellWrapper的change_terminal_size()和resize()现在是异步方法，需要使用await
                        loop = session.get("loop") or get_or_create_event_loop()
                        
                        if hasattr(ssh_shell, 'change_terminal_size'):
                            resize_coro = ssh_shell.change_terminal_size(cols, rows)
                            if loop.is_running():
                                asyncio.run_coroutine_threadsafe(resize_coro, loop)
                            else:
                                loop.run_until_complete(resize_coro)
                        elif hasattr(ssh_shell, 'resize'):
                            resize_coro = ssh_shell.resize(cols, rows)
                            if loop.is_running():
                                asyncio.run_coroutine_threadsafe(resize_coro, loop)
                            else:
                                loop.run_until_complete(resize_coro)
                        else:
                            log_srv(f"警告: shell对象不支持终端尺寸调整方法")
                    except Exception as e:
                        log_srv(f"调整终端大小失败（适配层）: {e}")
                        log_srv(traceback.format_exc())
                
                # paramiko方式（同步）
                ssh_channel = session.get("ssh_channel")
                if ssh_channel and not ssh_channel.closed:
                    try:
                        ssh_channel.resize_pty(width=cols, height=rows)
                    except:
                        pass
