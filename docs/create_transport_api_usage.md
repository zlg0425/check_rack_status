# `create_transport` API 使用说明

## 函数签名

```python
def create_transport(server_name: str, ip: str, port: int) -> paramiko.Transport
```

## 参数说明

- **`server_name`** (str): 服务器名称，用于：
  - 解析认证模式（通过 `resolve_auth_mode(server_name)`）
  - 解析密钥路径（通过 `resolve_key(server_name, port)`）
- **`ip`** (str): 服务器IP地址
- **`port`** (int): SSH端口（22 或 9999）

## 返回值

- **`paramiko.Transport`**: 已认证的SSH传输对象

## 使用示例

### ✅ 正确用法

```python
from check_rack_status import create_transport

# 基本用法
transport = create_transport("LP-8650-1", "10.99.19.11", 22)

# 使用SFTP
sftp = paramiko.SFTPClient.from_transport(transport)
try:
    # 使用SFTP进行操作
    files = sftp.listdir("/tmp")
finally:
    sftp.close()
    transport.close()
```

### ❌ 错误用法

```python
# 错误1: 参数顺序错误
transport = create_transport("10.99.19.11", 22, "LP-8650-1")  # ❌

# 错误2: 参数数量错误（传入了认证信息）
transport = create_transport("10.99.19.11", 22, "root", "/path/to/key", "key")  # ❌

# 错误3: 手动解析认证信息（不需要）
auth_mode = resolve_auth_mode(server_name)
key_path = resolve_key(server_name, port)
transport = create_transport(server_ip, port, ssh_username, key_path, auth_mode)  # ❌
```

## 内部实现

`create_transport` 函数内部会自动：

1. **解析认证模式**：
   ```python
   auth_mode = resolve_auth_mode(server_name)
   ```

2. **解析密钥路径**（如果使用密钥认证）：
   ```python
   key_path = resolve_key(server_name, port)
   ```

3. **创建Transport并认证**：
   ```python
   transport = paramiko.Transport((ip, port))
   transport.start_client(timeout=ssh_timeout)
   
   if auth_mode == "key":
       private_key = paramiko.RSAKey.from_private_key_file(key_path)
       transport.auth_publickey(username=ssh_username, key=private_key)
   elif auth_mode == "none":
       transport.auth_none(ssh_username)
   ```

## 注意事项

1. **不要手动传入认证信息**：函数会根据 `server_name` 和 `port` 自动解析
2. **使用全局变量**：函数使用全局变量 `ssh_username` 和 `ssh_timeout`
3. **资源清理**：使用完Transport后记得关闭：
   ```python
   transport.close()
   ```

## 常见错误

### 错误1: 参数顺序错误

```python
# ❌ 错误
transport = create_transport(server_ip, port, server_name)

# ✅ 正确
transport = create_transport(server_name, server_ip, port)
```

### 错误2: 参数数量错误

```python
# ❌ 错误 - 传入了5个参数
transport = create_transport(server_ip, port, ssh_username, key_path, auth_mode)

# ✅ 正确 - 只传入3个参数
transport = create_transport(server_name, server_ip, port)
```

### 错误3: 手动解析认证信息

```python
# ❌ 错误 - 不需要手动解析
auth_mode = resolve_auth_mode(server_name)
key_path = resolve_key(server_name, port)
transport = create_transport(server_ip, port, ssh_username, key_path, auth_mode)

# ✅ 正确 - 函数内部会自动解析
transport = create_transport(server_name, server_ip, port)
```

## 修复历史

### 2025-12-25
- 修复了 `web_ui.py` 中3处错误的 `create_transport` 调用
- 修复了 `test_terminal_socketio_automated.py` 中1处错误的调用
- 修复了 `test_terminal_socketio_automated.py` 中1处错误的调用

## 相关函数

- `resolve_auth_mode(server_name)`: 解析服务器认证模式
- `resolve_key(server_name, port)`: 解析服务器密钥路径
- `sftp_upload()`: 使用Transport进行文件上传
- `sftp_download()`: 使用Transport进行文件下载

