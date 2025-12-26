# SSH Connection Adapter 使用文档

## 概述

`ssh_connection_adapter.py` 是一个 AsyncSSH 2.x 适配层，提供统一的SSH连接接口，基于 AsyncSSH 2.x 实现。

> **注意**：当前实现仅支持 AsyncSSH 2.x，不再支持 1.x 版本

## 核心特性

- ✅ **AsyncSSH 2.x 实现**：基于 AsyncSSH 2.x 的完整实现
- ✅ **统一接口**：提供一致的API，简化SSH连接管理
- ✅ **连接池管理**：支持连接复用，提高性能
- ✅ **健康检查**：自动检测连接健康状态
- ✅ **错误处理**：统一的异常处理和错误信息
- ✅ **便捷函数**：支持装饰器和工厂函数两种使用方式

## 安装依赖

```bash
pip install asyncssh
```

## 快速开始

### 基本使用

```python
import asyncio
from ssh_connection_adapter import SSHConnectionAdapter, ConnectionConfig

async def main():
    # 创建适配器
    adapter = SSHConnectionAdapter()
    
    # 配置连接
    config = ConnectionConfig(
        host='example.com',
        port=22,
        username='admin',
        password='password'
    )
    
    # 建立连接
    conn = await adapter.create_connection(config)
    
    # 执行命令
    result = await adapter.run_command(conn, 'ls -la')
    if result['success']:
        print(result['stdout'])
    else:
        print(f"错误: {result['error']}")
    
    # 关闭连接
    await adapter.close_connection()

asyncio.run(main())
```

### 使用工厂函数

```python
from ssh_connection_adapter import create_ssh_connection

async def main():
    # 简单方式创建连接
    conn = await create_ssh_connection(
        'example.com',
        port=22,
        username='admin',
        password='password'
    )
    
    # 使用连接...
    conn.close()

asyncio.run(main())
```

### 使用装饰器

```python
from ssh_connection_adapter import compatible_ssh_connection

@compatible_ssh_connection
async def get_server_info(conn):
    """获取服务器信息"""
    result = await conn.run('uname -a')
    return result.stdout

async def main():
    # 调用时传入连接参数
    info = await get_server_info('example.com', 
                                 port=22,
                                 username='admin',
                                 password='password')
    print(info)

asyncio.run(main())
```

## API 参考

### ConnectionConfig

连接配置数据类：

```python
@dataclass
class ConnectionConfig:
    host: str                    # 主机地址（必需）
    port: int = 22              # SSH端口
    username: Optional[str] = None      # 用户名
    password: Optional[str] = None      # 密码
    client_keys: Optional[List[str]] = None  # 客户端密钥路径列表
    known_hosts: Optional[str] = None  # known_hosts文件路径
    connect_timeout: int = 10   # 连接超时（秒）
    login_timeout: int = 10     # 登录超时（秒）
    compression: bool = False   # 是否启用压缩
    keepalive_interval: int = 30 # Keepalive间隔（秒）
    keepalive_count_max: int = 3 # Keepalive最大次数
```

### SSHConnectionAdapter

主要适配器类：

#### `__init__(enable_connection_pool=True, max_pool_size=50)`

初始化适配器。

- `enable_connection_pool`: 是否启用连接池（默认True）
- `max_pool_size`: 连接池最大大小（默认50）

#### `async create_connection(config: ConnectionConfig) -> asyncssh.SSHClientConnection`

创建SSH连接。

**参数**:
- `config`: 连接配置

**返回**:
- SSH连接对象

**异常**:
- `ValueError`: 配置错误
- `asyncssh.Error`: SSH连接错误

#### `async run_command(conn, command: str, timeout: int = 30) -> Dict`

执行命令。

**参数**:
- `conn`: SSH连接对象
- `command`: 要执行的命令
- `timeout`: 超时时间（秒）

**返回**:
```python
{
    'success': bool,        # 是否成功
    'stdout': str,          # 标准输出
    'stderr': str,          # 标准错误
    'returncode': int,      # 返回码
    'exit_status': int,     # 退出状态
    'error': str,           # 错误信息（如果失败）
    'error_type': str       # 错误类型（如果失败）
}
```

#### `async open_shell(conn, term_type='xterm-256color', cols=80, rows=24) -> Any`

打开交互式Shell。

**参数**:
- `conn`: SSH连接对象
- `term_type`: 终端类型
- `cols`: 终端列数
- `rows`: 终端行数

**返回**:
- Shell会话对象

#### `async close_connection(connection_key: Optional[str] = None)`

关闭连接。

**参数**:
- `connection_key`: 连接键，如果为None则关闭所有连接

#### `get_connection_info() -> Dict`

获取适配层和连接池信息。

**返回**:
```python
{
    'asyncssh_version': str,      # asyncssh版本
    'compatibility_mode': str,    # 兼容模式（v1/v2）
    'pool_enabled': bool,         # 是否启用连接池
    'pool_size': int,            # 当前连接池大小
    'max_pool_size': int,        # 最大连接池大小
    'pool_connections': List[str], # 连接键列表
    'connection_metadata': Dict   # 连接元数据
}
```

## 高级用法

### 连接池管理

```python
adapter = SSHConnectionAdapter(enable_connection_pool=True, max_pool_size=100)

# 多次使用同一服务器会自动复用连接
config1 = ConnectionConfig(host='server1.com', username='admin', password='pass')
conn1 = await adapter.create_connection(config1)

config2 = ConnectionConfig(host='server1.com', username='admin', password='pass')
conn2 = await adapter.create_connection(config2)  # 复用conn1

# 手动清理空闲连接
await adapter.cleanup_idle_connections(idle_timeout=300)
```

### 使用密钥认证

```python
config = ConnectionConfig(
    host='example.com',
    port=22,
    username='admin',
    client_keys=['/path/to/private_key']  # 使用密钥而不是密码
)

conn = await adapter.create_connection(config)
```

### 批量操作

```python
servers = [
    {'host': 'server1.com', 'username': 'admin', 'password': 'pass1'},
    {'host': 'server2.com', 'username': 'admin', 'password': 'pass2'},
    {'host': 'server3.com', 'username': 'admin', 'password': 'pass3'},
]

async def process_server(server_info):
    config = ConnectionConfig(**server_info)
    conn = await adapter.create_connection(config)
    result = await adapter.run_command(conn, 'hostname')
    return result['stdout']

# 并发处理多个服务器
results = await asyncio.gather(*[process_server(s) for s in servers])
```

### 错误处理

```python
try:
    config = ConnectionConfig(host='example.com', username='admin', password='wrong')
    conn = await adapter.create_connection(config)
except asyncssh.PermissionDenied:
    print("认证失败")
except asyncssh.Error as e:
    print(f"SSH错误: {e}")
except Exception as e:
    print(f"其他错误: {e}")

# 命令执行错误处理
result = await adapter.run_command(conn, 'some_command')
if not result['success']:
    if result['error_type'] == 'TIMEOUT':
        print("命令执行超时")
    elif result['error_type'] == 'SSH_ERROR':
        print(f"SSH错误: {result['error']}")
```

## 版本兼容性

适配层自动处理以下版本差异：

### AsyncSSH 1.x
- 参数位置相对宽松
- 支持位置参数和关键字参数混合使用

### AsyncSSH 2.x
- 严格要求关键字参数
- API略有变化

适配层会自动检测版本并使用相应的连接方式，无需手动指定。

## 性能优化建议

1. **启用连接池**：对于频繁连接同一服务器的情况，启用连接池可以显著提高性能
2. **合理设置超时**：根据网络情况设置合适的超时时间
3. **批量操作**：使用 `asyncio.gather()` 并发处理多个服务器
4. **定期清理**：定期调用 `cleanup_idle_connections()` 清理空闲连接

## 注意事项

1. **依赖检查**：如果未安装 `asyncssh`，适配器会抛出 `ImportError` 并提供安装提示
2. **连接管理**：使用完连接后记得关闭，或使用上下文管理器
3. **线程安全**：适配器是线程安全的，可以在多线程环境中使用
4. **异步环境**：所有方法都是异步的，必须在异步环境中调用

## 与现有代码集成

如果项目中已经使用 `paramiko`（如 `check_rack_status.py`），可以这样集成：

```python
# 在需要asyncssh功能的地方使用适配器
from ssh_connection_adapter import SSHConnectionAdapter, ConnectionConfig

# 对于需要异步SSH的场景（如Web终端），使用适配器
async def web_terminal_connection(server_name, server_ip, port):
    adapter = SSHConnectionAdapter()
    config = ConnectionConfig(host=server_ip, port=port, username='admin')
    conn = await adapter.create_connection(config)
    return conn

# 对于传统的同步SSH操作，继续使用paramiko
from check_rack_status import create_transport
transport = create_transport(server_name, server_ip, port)
```

## 故障排查

### 问题1: ImportError: asyncssh未安装

**解决方案**:
```bash
pip install asyncssh
```

### 问题2: 连接失败

**检查项**:
1. 主机地址和端口是否正确
2. 用户名和密码/密钥是否正确
3. 网络连接是否正常
4. 防火墙设置

### 问题3: 版本兼容性问题

适配层会自动处理版本差异，如果遇到问题，可以查看日志：
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 示例代码

完整示例请参考 `ssh_connection_adapter.py` 文件末尾的 `example_usage()` 函数。

## 许可证

与项目主许可证一致。

