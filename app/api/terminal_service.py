"""
终端服务模块
处理终端连接的业务逻辑
"""

import threading
from typing import Optional, Dict, Any

# 终端服务主要用于管理终端会话
# 实际的终端逻辑在 app/routes/terminal.py 中通过 SocketIO 处理


class TerminalService:
    """终端服务类"""
    
    def __init__(self):
        """初始化终端服务"""
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.sessions_lock = threading.Lock()
    
    def create_session(self, session_id: str, server_name: str, server_ip: str, port: int) -> None:
        """
        创建终端会话
        
        Args:
            session_id: 会话ID
            server_name: 服务器名称
            server_ip: 服务器IP
            port: 端口
        """
        with self.sessions_lock:
            self.sessions[session_id] = {
                "server_name": server_name,
                "server_ip": server_ip,
                "port": port,
                "status": "connecting"
            }
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话信息
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话信息字典，如果不存在则返回None
        """
        with self.sessions_lock:
            return self.sessions.get(session_id)
    
    def remove_session(self, session_id: str) -> None:
        """
        移除会话
        
        Args:
            session_id: 会话ID
        """
        with self.sessions_lock:
            if session_id in self.sessions:
                del self.sessions[session_id]


# 全局服务实例
_terminal_service = None
_terminal_service_lock = threading.Lock()


def get_terminal_service() -> TerminalService:
    """获取终端服务实例（单例模式）"""
    global _terminal_service
    if _terminal_service is None:
        with _terminal_service_lock:
            if _terminal_service is None:
                _terminal_service = TerminalService()
    return _terminal_service

