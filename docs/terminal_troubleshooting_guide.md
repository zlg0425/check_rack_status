# 网页Terminal连接失败排查指南

## 概述

本文档提供了系统性的排查方法，帮助定位和解决网页Terminal连接失败的问题。

## 排查流程图

```
网页Terminal连接失败
    ↓
第一步: 网络与服务器基础检查
    ├─ 1. 目标服务器可达性与SSH服务状态
    └─ 2. SSH密钥/密码认证
    ↓
第二步: 后端核心逻辑检查
    ├─ 3. AsyncSSH异步连接代码是否正确使用 async/await?
    └─ 4. 捕获并打印具体错误（替换笼统的日志）
    ↓
第三步: 前端与通信链路检查
    ├─ 5. WebSocket连接状态与消息收发
    └─ 6. 浏览器控制台报错与网络面板(NETWORK)
    ↓
第四步: 服务器环境检查
    └─ 防火墙/资源限制等
```

## 详细排查步骤

### 第一步：网络与服务器基础检查

#### 1.1 服务器可达性与SSH服务状态

**命令行测试**：
```bash
# 测试服务器是否可达
ping 10.99.19.11

# 测试SSH端口是否开放
telnet 10.99.19.11 22
# 或使用
nc -zv 10.99.19.11 22
```

**SSH连接测试**：
```bash
# 使用命令行SSH测试连接
ssh -p 22 root@10.99.19.11

# 如果使用密钥认证
ssh -i /path/to/your/private_key -p 22 root@10.99.19.11
```

**预期结果**：
- 如果命令行SSH连接成功，说明网络和SSH服务正常
- 如果失败，检查网络配置、防火墙、SSH服务状态

#### 1.2 SSH密钥/密码认证

**检查密钥文件**：
```bash
# 检查密钥文件是否存在
ls -l /path/to/your/private_key

# 检查密钥文件权限（应该是600）
chmod 600 /path/to/your/private_key

# 测试密钥是否有效
ssh-keygen -y -f /path/to/your/private_key
```

**检查配置文件**：
- 确认 `config.json` 中的服务器配置正确
- 确认 `key_mapping` 或 `group_key_mapping` 配置正确
- 确认 `ssh_username` 配置正确

### 第二步：后端核心逻辑检查

#### 2.1 使用测试脚本隔离测试

**运行测试脚本**：
```bash
# 测试特定服务器
python test_ssh_connection.py LP-8650-1

# 测试另一个服务器
python test_ssh_connection.py LP-8295-1
```

**测试脚本功能**：
1. 创建SSH适配器
2. 建立SSH连接
3. 测试执行命令
4. 创建交互式Shell
5. 测试Shell读写

**预期结果**：
- 如果测试脚本成功，说明SSH连接本身正常，问题可能在WebSocket或前端
- 如果测试脚本失败，会显示详细的错误信息，帮助定位问题

#### 2.2 检查错误日志

**查看服务器日志**：
```bash
# 查看应用日志
tail -f logs/app.log

# 查看终端相关日志
grep "terminal" logs/app.log | tail -20
```

**常见错误类型**：

1. **认证失败**：
   ```
   asyncssh.PermissionDenied: Permission denied
   ```
   - 检查密钥文件路径是否正确
   - 检查密钥文件权限（应该是600）
   - 检查用户名是否正确

2. **连接超时**：
   ```
   asyncssh.TimeoutError: Connection timeout
   ```
   - 检查网络连接
   - 检查防火墙设置
   - 增加 `connect_timeout` 值

3. **主机密钥验证失败**：
   ```
   asyncssh.HostKeyNotVerifiable: Host key not verifiable
   ```
   - 设置 `skip_host_key_check=True`（仅测试环境）
   - 或配置正确的 `known_hosts` 文件

4. **连接被拒绝**：
   ```
   ConnectionRefusedError: Connection refused
   ```
   - 检查SSH服务是否运行
   - 检查端口是否正确
   - 检查防火墙规则

### 第三步：前端与通信链路检查

#### 3.1 WebSocket连接状态

**浏览器开发者工具检查**：

1. 打开浏览器开发者工具（F12）
2. 切换到 **Network（网络）** 标签
3. 筛选 **WS（WebSocket）**
4. 刷新页面并尝试连接终端
5. 查看WebSocket连接状态：
   - **状态码 101**：连接成功
   - **状态码 400/404**：连接失败
   - **红色**：连接错误

**检查WebSocket消息**：
- 在Network面板中点击WebSocket连接
- 查看 **Messages（消息）** 标签
- 检查是否有 `connected`、`error`、`output` 等事件

#### 3.2 浏览器控制台检查

**查看JavaScript错误**：
- 打开 **Console（控制台）** 标签
- 查看是否有红色错误信息
- 常见错误：
  - `WebSocket connection failed`
  - `Socket.IO connection error`
  - `xterm.js error`

**检查前端代码**：
- 确认前端正确连接到WebSocket端点
- 确认事件监听器正确注册
- 确认消息格式正确

### 第四步：服务器环境检查

#### 4.1 防火墙/安全组

**检查防火墙规则**：
```bash
# Linux
sudo iptables -L -n | grep 22
sudo firewall-cmd --list-all

# Windows
netsh advfirewall firewall show rule name=all | findstr SSH
```

**检查安全组**（如果使用云服务器）：
- 确认22端口对运行后端程序的机器开放
- 确认没有IP白名单限制

#### 4.2 SSH服务配置

**检查SSH服务配置**：
```bash
# 查看SSH服务状态
systemctl status sshd

# 查看SSH配置
sudo cat /etc/ssh/sshd_config | grep -E "MaxSessions|MaxStartups|LoginGraceTime"
```

**常见限制**：
- `MaxSessions`：最大会话数
- `MaxStartups`：最大并发连接数
- `LoginGraceTime`：登录超时时间

## 错误处理改进

### 后端错误处理

当前实现已改进错误处理，会：
1. 捕获并记录详细的错误信息（错误类型、错误消息、堆栈）
2. 发送详细的错误信息到前端
3. 记录到日志文件和调试日志

### 前端错误处理

前端应该监听 `error` 事件并显示详细信息：

```javascript
socket.on('error', (data) => {
    console.error('SSH连接错误:', data);
    // 显示错误信息给用户
    term.write(`\r\n[错误] ${data.message}\r\n`);
    if (data.error) {
        term.write(`详情: ${data.error}\r\n`);
    }
});
```

## 快速诊断命令

### 1. 测试SSH连接
```bash
python test_ssh_connection.py <server_name>
```

### 2. 检查配置
```bash
python -c "from app.utils.config import load_config, server_dict; load_config(); print(server_dict)"
```

### 3. 检查密钥路径
```bash
python -c "from app.utils.config import resolve_key_path; print(resolve_key_path('8650_rsa2048'))"
```

### 4. 查看日志
```bash
tail -f logs/app.log | grep terminal
```

## 常见问题解决方案

### 问题1：连接超时

**可能原因**：
- 网络延迟高
- 防火墙阻塞
- SSH服务未运行

**解决方案**：
1. 增加 `connect_timeout` 值
2. 检查网络连接
3. 检查防火墙规则
4. 确认SSH服务运行

### 问题2：认证失败

**可能原因**：
- 密钥文件路径错误
- 密钥文件权限不正确
- 用户名错误

**解决方案**：
1. 使用 `test_ssh_connection.py` 测试连接
2. 检查密钥文件路径和权限
3. 确认 `config.json` 中的用户名配置

### 问题3：WebSocket连接失败

**可能原因**：
- 后端服务未启动
- 端口被占用
- 前端连接地址错误

**解决方案**：
1. 检查后端服务是否运行
2. 检查端口是否被占用
3. 确认前端连接地址正确

## 获取帮助

如果以上步骤都无法解决问题，请提供以下信息：

1. **测试脚本输出**：运行 `test_ssh_connection.py` 的完整输出
2. **错误日志**：`logs/app.log` 中的相关错误信息
3. **浏览器控制台**：浏览器开发者工具中的错误信息
4. **网络面板**：WebSocket连接的详细信息
5. **服务器信息**：目标服务器的SSH配置和网络环境

