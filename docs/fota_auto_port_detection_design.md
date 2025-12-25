# FOTA升级自动端口判断方案设计

## 一、需求分析

### 1.1 当前问题
- 前端有两个按钮：`FOTA 22` 和 `FOTA 9999`
- 用户需要手动选择端口，容易选错
- 批量FOTA也有两个按钮，操作繁琐

### 1.2 目标
- **单个FOTA**: 只有一个"FOTA升级"按钮，根据文件名前缀自动判断端口
- **批量FOTA**: 只有一个"批量升级"按钮，自动判断端口
- **智能提示**: 如果无法判断端口，给出明确的错误提示

### 1.3 配置规则（来自config.json）

```json
{
  "fota_filename_validation": {
    "LP-8650": {
      "22": "LP-ADDC050",
      "9999": "LP-ICHS046"
    },
    "LP-8797": {
      "22": "LP-ADDC0C0",
      "9999": "LP-ICHS050"
    }
  }
}
```

**规则说明**:
- LP-8650系列：文件名包含 `LP-ADDC050` → 22端口，包含 `LP-ICHS046` → 9999端口
- LP-8797系列：文件名包含 `LP-ADDC0C0` → 22端口，包含 `LP-ICHS050` → 9999端口

## 二、技术方案设计

### 2.1 核心函数：自动端口判断（增强版）

#### 2.1.1 后端函数（Python）

```python
def detect_fota_port(server_name: str, filename: str) -> tuple[int, str]:
    """
    根据服务器名称和文件名自动判断FOTA端口
    同时验证文件是否与服务器类型匹配
    
    Args:
        server_name: 服务器名称，如 "LP-8650-1", "LP-8797-1"
        filename: 文件名，如 "LP-ADDC050_v1.0.icsw"
    
    Returns:
        (port, message): 端口号(22或9999)和提示信息
        如果无法判断，返回 (0, error_message)
    """
    # 1. 确定服务器类型
    server_type = None
    if server_name.startswith("LP-8650"):
        server_type = "LP-8650"
    elif server_name.startswith("LP-8797"):
        server_type = "LP-8797"
    else:
        return (0, f"未知的服务器类型: {server_name}")
    
    # 2. 获取该服务器类型的端口配置
    if not fota_filename_validation or server_type not in fota_filename_validation:
        return (0, f"服务器类型 {server_type} 未配置文件名验证规则")
    
    port_config = fota_filename_validation[server_type]
    
    # 3. **关键增强：检查文件名是否与其他服务器类型的前缀匹配**
    #   如果匹配了其他服务器类型的文件，应该提示错误
    all_server_types = list(fota_filename_validation.keys())
    mismatched_types = []
    
    for other_type in all_server_types:
        if other_type == server_type:
            continue  # 跳过当前服务器类型
        
        other_config = fota_filename_validation[other_type]
        for other_prefix in other_config.values():
            if other_prefix in filename:
                mismatched_types.append({
                    "type": other_type,
                    "prefix": other_prefix
                })
    
    # 如果文件名匹配了其他服务器类型的文件，返回错误
    if mismatched_types:
        mismatched_info = ", ".join([f"{m['type']}({m['prefix']})" for m in mismatched_types])
        expected_prefixes = list(port_config.values())
        return (0, f"文件 '{filename}' 不匹配当前服务器类型 {server_type}。\n"
                   f"检测到其他服务器类型的文件: {mismatched_info}\n"
                   f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
    
    # 4. 检查文件名包含哪个端口的前缀（仅检查当前服务器类型）
    detected_ports = []
    for port_str, prefix in port_config.items():
        if prefix in filename:
            detected_ports.append((int(port_str), prefix))
    
    # 5. 判断结果
    if len(detected_ports) == 0:
        # 没有匹配的前缀
        expected_prefixes = list(port_config.values())
        # 检查是否包含任何已知的前缀（用于更友好的错误提示）
        all_prefixes = []
        for st in all_server_types:
            all_prefixes.extend(fota_filename_validation[st].values())
        
        found_prefixes = [p for p in all_prefixes if p in filename]
        if found_prefixes:
            # 找到了前缀，但是不匹配当前服务器类型
            return (0, f"文件 '{filename}' 包含的前缀 '{', '.join(found_prefixes)}' 不匹配服务器类型 {server_type}。\n"
                       f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
        else:
            # 完全没有找到任何已知前缀
            return (0, f"文件 '{filename}' 不包含任何有效的前缀。\n"
                       f"当前服务器 {server_type} 期望的前缀: {', '.join(expected_prefixes)}")
    elif len(detected_ports) == 1:
        # 唯一匹配
        port, prefix = detected_ports[0]
        return (port, f"检测到端口 {port} (前缀: {prefix})")
    else:
        # 多个匹配（理论上不应该发生，但需要处理）
        ports = [str(p) for p, _ in detected_ports]
        return (0, f"文件 '{filename}' 匹配多个端口: {', '.join(ports)}，请检查文件名")
```

#### 2.1.2 前端函数（JavaScript）

```javascript
/**
 * 根据文件名和服务器名称自动判断端口
 * @param {string} serverName - 服务器名称
 * @param {string} filename - 文件名
 * @returns {Promise<{port: number, message: string}>} 端口和提示信息
 */
async function detectFotaPort(serverName, filename) {
  try {
    const response = await fetch('/api/fota/detect-port', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        server_name: serverName,
        filename: filename
      })
    });
    
    const data = await response.json();
    if (data.ok && data.port) {
      return { port: data.port, message: data.message || '' };
    } else {
      return { port: 0, message: data.error || '无法判断端口' };
    }
  } catch (error) {
    return { port: 0, message: `检测失败: ${error.message}` };
  }
}
```

### 2.2 API接口设计

#### 2.2.1 新增接口：端口检测

```python
@app.route("/api/fota/detect-port", methods=["POST"])
def api_fota_detect_port():
    """检测文件名对应的端口"""
    try:
        data = request.get_json()
        server_name = data.get("server_name", "").strip()
        filename = data.get("filename", "").strip()
        
        if not server_name or not filename:
            return jsonify({"ok": False, "error": "参数缺失"}), 400
        
        port, message = detect_fota_port(server_name, filename)
        
        if port == 0:
            return jsonify({"ok": False, "error": message}), 400
        
        return jsonify({
            "ok": True,
            "port": port,
            "message": message
        })
    except Exception as e:
        log_srv(f"端口检测异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500
```

#### 2.2.2 修改现有接口：FOTA接口支持自动端口

```python
@app.route("/api/fota", methods=["POST"])
def api_fota():
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = request.form.get("port", "0")  # 允许传入0或空字符串
        file = request.files.get("file")
        
        # 如果端口为0或未提供，尝试自动检测
        if port == "0" or not port:
            if not file or file.filename == "":
                return jsonify({"ok": False, "error": "未选择文件，无法自动检测端口"}), 400
            
            filename = file.filename
            detected_port, message = detect_fota_port(server_name, filename)
            
            if detected_port == 0:
                return jsonify({"ok": False, "error": message}), 400
            
            port = detected_port
            log_fota(f"[{server_name}/{server_ip}] 自动检测端口: {port} ({message})")
        else:
            port = int(port)
        
        # 后续逻辑保持不变...
```

#### 2.2.3 修改批量FOTA接口（增强版）

```python
@app.route("/api/batch-fota", methods=["POST"])
def api_batch_fota():
    """批量FOTA接口 - 支持自动端口检测和服务器类型验证"""
    try:
        port = request.form.get("port", "0")  # 允许传入0
        servers_json = request.form.get("servers", "[]")
        file = request.files.get("file")
        
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "未选择文件"}), 400
        
        filename = file.filename
        
        try:
            servers = json.loads(servers_json)
            if not servers or len(servers) == 0:
                return jsonify({"ok": False, "error": "服务器列表为空"}), 400
        except:
            return jsonify({"ok": False, "error": "服务器列表格式错误"}), 400
        
        # **关键增强：验证文件是否与所有服务器类型匹配**
        server_types = set()
        for server in servers:
            server_name = server.get("name", "")
            if server_name.startswith("LP-8650"):
                server_types.add("LP-8650")
            elif server_name.startswith("LP-8797"):
                server_types.add("LP-8797")
        
        if len(server_types) > 1:
            # 混合了不同类型的服务器
            return jsonify({
                "ok": False, 
                "error": f"批量FOTA不支持混合不同服务器类型。\n"
                        f"检测到服务器类型: {', '.join(server_types)}\n"
                        f"请选择相同类型的服务器进行批量升级"
            }), 400
        
        if len(server_types) == 0:
            return jsonify({"ok": False, "error": "无法识别服务器类型"}), 400
        
        # 使用第一个服务器检测端口和验证文件匹配
        first_server = servers[0]
        detected_port, message = detect_fota_port(first_server["name"], filename)
        
        if detected_port == 0:
            return jsonify({"ok": False, "error": message}), 400
        
        # 如果明确传入了端口，验证是否一致
        if port != "0" and port:
            explicit_port = int(port)
            if explicit_port != detected_port:
                return jsonify({
                    "ok": False,
                    "error": f"指定的端口 {explicit_port} 与文件检测到的端口 {detected_port} 不一致。\n"
                            f"检测信息: {message}"
                }), 400
            port = explicit_port
        else:
            port = detected_port
            log_fota(f"[批量FOTA] 自动检测端口: {port} ({message})")
        
        if port not in (22, 9999):
            return jsonify({"ok": False, "error": "端口必须是22或9999"}), 400
        
        # 后续逻辑保持不变...
```

### 2.3 前端修改方案

#### 2.3.1 单个FOTA按钮修改

**修改前**:
```html
<td>
  <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',22)">FOTA 22</button>
  <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',9999)">FOTA 9999</button>
</td>
```

**修改后**:
```html
<td>
  <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}')" 
          ${(item.fota_status_22 || item.fota_status_9999) ? 'disabled style="opacity: 0.5;"' : ''}>
    FOTA升级
  </button>
</td>
```

#### 2.3.2 修改openFota函数（增强版）

```javascript
// 修改前
function openFota(name, ip, port) {
  currentFota = { name, ip, port };
  // ...
}

// 修改后
function openFota(name, ip, port = null) {
  currentFota = { name, ip, port: port || 0 };  // port为0表示自动检测
  const fotaBackdrop = document.getElementById('fotaModal');
  const fotaServer = document.getElementById('fotaServer');
  const fotaFile = document.getElementById('fotaFile');
  const fotaPortHint = document.getElementById('fotaPortHint');  // 新增提示元素
  const fotaStartBtn = document.querySelector('#fotaModal .modal-actions button');  // 升级按钮
  
  fotaServer.textContent = `${name} (${ip})`;
  fotaFile.value = '';
  fotaPortHint.textContent = '请选择文件，系统将自动检测端口并验证文件匹配';
  fotaPortHint.style.color = '#6b7280';
  fotaStartBtn.disabled = false;  // 重置按钮状态
  
  fotaBackdrop.style.display = 'flex';
  
  // 移除之前的事件监听器（如果存在），避免重复绑定
  const newFotaFile = fotaFile.cloneNode(true);
  fotaFile.parentNode.replaceChild(newFotaFile, fotaFile);
  
  // 监听文件选择，自动检测端口和验证文件匹配
  newFotaFile.addEventListener('change', async function() {
    const file = this.files[0];
    if (!file) {
      fotaPortHint.textContent = '请选择文件';
      fotaPortHint.style.color = '#6b7280';
      currentFota.port = 0;
      fotaStartBtn.disabled = false;
      return;
    }
    
    // 显示检测中
    fotaPortHint.textContent = '正在检测端口和验证文件匹配...';
    fotaPortHint.style.color = '#3b82f6';
    fotaStartBtn.disabled = true;  // 检测期间禁用按钮
    
    // 调用检测接口
    const result = await detectFotaPort(name, file.name);
    
    if (result.port > 0) {
      currentFota.port = result.port;
      fotaPortHint.textContent = `✓ 检测到端口: ${result.port} (${result.message})`;
      fotaPortHint.style.color = '#10b981';
      fotaStartBtn.disabled = false;  // 检测成功，启用按钮
    } else {
      currentFota.port = 0;
      // 错误信息可能包含换行，需要格式化显示
      const errorMsg = result.message.replace(/\n/g, '<br>');
      fotaPortHint.innerHTML = `✗ ${errorMsg}`;
      fotaPortHint.style.color = '#ef4444';
      fotaStartBtn.disabled = true;  // 检测失败，禁用按钮
    }
  });
}
```

#### 2.3.3 修改startFota函数（增强版）

```javascript
async function startFota() {
  const file = fotaFile.files[0];
  if (!file) {
    alert('请先选择文件');
    return;
  }
  
  // 如果端口未检测或检测失败，尝试再次检测
  if (currentFota.port === 0) {
    // 尝试再次检测
    const fotaPortHint = document.getElementById('fotaPortHint');
    fotaPortHint.textContent = '正在重新检测端口...';
    fotaPortHint.style.color = '#3b82f6';
    
    const result = await detectFotaPort(currentFota.name, file.name);
    
    if (result.port === 0) {
      alert(`无法确定端口:\n${result.message}`);
      fotaPortHint.innerHTML = `✗ ${result.message.replace(/\n/g, '<br>')}`;
      fotaPortHint.style.color = '#ef4444';
      return;
    }
    
    currentFota.port = result.port;
    fotaPortHint.textContent = `✓ 检测到端口: ${result.port} (${result.message})`;
    fotaPortHint.style.color = '#10b981';
  }
  
  // 后续逻辑保持不变，但使用currentFota.port
  const fd = new FormData();
  fd.append('file', file);
  fd.append('server_name', currentFota.name);
  fd.append('server_ip', currentFota.ip);
  fd.append('port', currentFota.port);  // 使用检测到的端口
  
  // ... 后续代码保持不变
}
```

#### 2.3.4 批量FOTA按钮修改

**修改前**:
```html
<button class="btn" id="batchFotaStart22Btn" onclick="startBatchFota(22)">22端口升级</button>
<button class="btn" id="batchFotaStart9999Btn" onclick="startBatchFota(9999)">9999端口升级</button>
```

**修改后**:
```html
<button class="btn" id="batchFotaStartBtn" onclick="startBatchFota()">批量升级</button>
<div id="batchFotaPortHint" style="margin-top: 8px; font-size: 12px; color: #6b7280;"></div>
```

#### 2.3.5 修改startBatchFota函数（增强版）

```javascript
async function startBatchFota() {
  const file = document.getElementById('batchFotaFile').files[0];
  if (!file) {
    alert('请先选择文件');
    return;
  }
  
  const servers = [];
  for (const key in selectedServers) {
    servers.push(selectedServers[key]);
  }
  
  if (servers.length === 0) {
    alert('请先选择至少一个服务器');
    return;
  }
  
  const batchFotaPortHint = document.getElementById('batchFotaPortHint');
  const batchFotaStartBtn = document.getElementById('batchFotaStartBtn');
  
  // **增强：先检查服务器类型是否一致**
  const serverTypes = new Set();
  servers.forEach(srv => {
    if (srv.name.startsWith('LP-8650')) {
      serverTypes.add('LP-8650');
    } else if (srv.name.startsWith('LP-8797')) {
      serverTypes.add('LP-8797');
    }
  });
  
  if (serverTypes.size > 1) {
    const typesList = Array.from(serverTypes).join(', ');
    batchFotaPortHint.textContent = `✗ 批量FOTA不支持混合不同服务器类型。检测到: ${typesList}`;
    batchFotaPortHint.style.color = '#ef4444';
    batchFotaStartBtn.disabled = true;
    return;
  }
  
  // 自动检测端口（使用第一个服务器）
  batchFotaPortHint.textContent = '正在检测端口和验证文件匹配...';
  batchFotaPortHint.style.color = '#3b82f6';
  batchFotaStartBtn.disabled = true;
  
  const result = await detectFotaPort(servers[0].name, file.name);
  
  if (result.port === 0) {
    batchFotaPortHint.textContent = `✗ ${result.message}`;
    batchFotaPortHint.style.color = '#ef4444';
    batchFotaStartBtn.disabled = true;
    // 显示详细错误提示
    alert(`文件验证失败:\n${result.message}`);
    return;
  }
  
  const port = result.port;
  const serverType = Array.from(serverTypes)[0] || '未知';
  batchFotaPortHint.textContent = `✓ 检测到端口: ${port}，服务器类型: ${serverType} (${result.message})`;
  batchFotaPortHint.style.color = '#10b981';
  batchFotaStartBtn.disabled = false;
  
  // 后续逻辑，使用检测到的port
  const fd = new FormData();
  fd.append('file', file);
  fd.append('port', port);  // 使用检测到的端口
  fd.append('servers', JSON.stringify(servers));
  
  // ... 后续代码保持不变
}
```

### 2.4 模态框HTML修改

#### 2.4.1 单个FOTA模态框

```html
<div class="modal-backdrop" id="fotaModal">
  <div class="modal">
    <h3>FOTA升级</h3>
    <div id="fotaServer" style="margin-bottom: 12px; font-weight: 600;"></div>
    <label>选择文件:</label>
    <input type="file" id="fotaFile" accept=".icsw,.bin">
    <!-- 新增：端口检测提示 -->
    <div id="fotaPortHint" style="margin-top: 8px; font-size: 12px; color: #6b7280;">
      请选择文件，系统将自动检测端口
    </div>
    <div id="fotaStep" class="progress"></div>
    <div id="fotaProgress" class="progress"></div>
    <div class="modal-actions">
      <button class="btn" onclick="startFota()">升级</button>
      <button class="btn" onclick="closeFota()">取消</button>
    </div>
  </div>
</div>
```

#### 2.4.2 批量FOTA模态框

```html
<div class="modal-backdrop" id="batchFotaModal">
  <div class="modal" style="width: 500px;">
    <h3>批量FOTA升级</h3>
    <div>服务器数量: <span id="batchServerCount">0</span></div>
    <div id="batchServerList" style="max-height: 150px; overflow-y: auto; margin: 8px 0;"></div>
    <label>目标目录:</label>
    <div id="batchFotaTargetText" style="margin-bottom: 8px;"></div>
    <label>选择文件:</label>
    <input type="file" id="batchFotaFile" accept=".icsw,.bin">
    <!-- 新增：端口检测提示 -->
    <div id="batchFotaPortHint" style="margin-top: 8px; font-size: 12px; color: #6b7280;"></div>
    <div id="batchFotaStep" class="progress"></div>
    <div id="batchFotaProgress" class="progress"></div>
    <div id="batchFotaTasks"></div>
    <div class="modal-actions">
      <button class="btn" id="batchFotaStartBtn" onclick="startBatchFota()">批量升级</button>
      <button class="btn" id="batchFotaCancelBtn" onclick="cancelBatchFota()" style="display: none;">取消</button>
      <button class="btn" onclick="closeBatchFota()">关闭</button>
    </div>
  </div>
</div>
```

## 三、实施步骤

### 3.1 阶段1：后端实现（1-2天）

1. **实现detect_fota_port函数**
   - 在 `check_rack_status.py` 或 `web_ui.py` 中实现
   - 添加单元测试

2. **新增API接口**
   - 实现 `/api/fota/detect-port` 接口
   - 测试接口功能

3. **修改现有API**
   - 修改 `/api/fota` 支持自动端口检测
   - 修改 `/api/batch-fota` 支持自动端口检测
   - 保持向后兼容（如果明确传入端口，优先使用）

### 3.2 阶段2：前端实现（1-2天）

1. **修改单个FOTA**
   - 合并两个按钮为一个
   - 实现端口自动检测
   - 添加端口提示显示

2. **修改批量FOTA**
   - 合并两个按钮为一个
   - 实现端口自动检测
   - 添加端口提示显示

3. **UI优化**
   - 优化提示信息显示
   - 添加加载状态
   - 错误提示优化

### 3.3 阶段3：测试和优化（1天）

1. **功能测试**
   - 测试各种文件名组合
   - 测试错误情况处理
   - 测试边界情况

2. **用户体验优化**
   - 优化提示信息
   - 优化错误处理
   - 添加操作指引

## 四、错误处理方案

### 4.1 无法检测端口的情况

1. **文件与服务器类型不匹配（新增）**
   - 场景：LP-8650选择了LP-8797的文件，或反之
   - 提示：明确说明文件属于哪个服务器类型，当前服务器期望什么类型
   - 建议：选择匹配当前服务器类型的文件
   - 示例：
     ```
     文件 'LP-ADDC0C0_v1.0.icsw' 不匹配当前服务器类型 LP-8650。
     检测到其他服务器类型的文件: LP-8797(LP-ADDC0C0)
     当前服务器 LP-8650 期望的前缀: LP-ADDC050, LP-ICHS046
     ```

2. **文件名不包含任何有效前缀**
   - 提示：显示期望的前缀列表
   - 建议：检查文件名是否正确

3. **服务器类型未知**
   - 提示：服务器类型不在配置中
   - 建议：检查服务器名称格式

4. **配置缺失**
   - 提示：服务器类型未配置验证规则
   - 建议：检查config.json配置

5. **文件名匹配多个端口**
   - 提示：文件名匹配多个端口（理论上不应该发生）
   - 建议：检查文件名或配置

6. **批量FOTA混合服务器类型（新增）**
   - 场景：选择了LP-8650和LP-8797混合的服务器列表
   - 提示：批量FOTA不支持混合不同服务器类型
   - 建议：选择相同类型的服务器进行批量升级

### 4.2 用户操作流程

#### 单个FOTA流程
```
1. 用户点击"FOTA升级"按钮
   ↓
2. 打开模态框，显示"请选择文件"
   ↓
3. 用户选择文件
   ↓
4. 系统自动检测端口和验证文件匹配
   - 成功：显示"✓ 检测到端口: 22 (前缀: LP-ADDC050)"
   - 失败（文件不匹配）：显示"✗ 文件不匹配当前服务器类型，详细错误信息"
   - 失败（其他）：显示"✗ 错误信息"
   ↓
5. 用户点击"升级"按钮
   - 如果检测成功：开始升级
   - 如果检测失败：提示错误，不允许升级，按钮禁用或显示错误提示
```

#### 批量FOTA流程
```
1. 用户选择多个服务器，点击"批量FOTA升级"
   ↓
2. 打开批量FOTA模态框
   ↓
3. 用户选择文件
   ↓
4. 系统验证：
   a) 检查服务器类型是否一致（不能混合LP-8650和LP-8797）
   b) 检测端口和验证文件匹配
   ↓
5. 显示检测结果
   - 成功：显示"✓ 检测到端口: 22，服务器类型: LP-8650"
   - 失败（混合类型）：显示"✗ 批量FOTA不支持混合不同服务器类型"
   - 失败（文件不匹配）：显示"✗ 文件不匹配服务器类型"
   ↓
6. 用户点击"批量升级"按钮
   - 如果验证成功：开始批量升级
   - 如果验证失败：提示错误，不允许升级
```

## 五、向后兼容性

### 5.1 API兼容性

- **保持现有API接口不变**
- **新增参数可选**：`port` 参数可以为0或空，表示自动检测
- **明确传入端口时优先使用**：如果用户明确传入22或9999，直接使用，不进行检测

### 5.2 配置兼容性

- **使用现有配置**：复用 `fota_filename_validation` 配置
- **无需新增配置项**

## 六、测试用例

### 6.1 正常情况

| 服务器类型 | 文件名 | 期望端口 | 测试结果 |
|-----------|--------|---------|---------|
| LP-8650-1 | LP-ADDC050_v1.0.icsw | 22 | ✓ |
| LP-8650-1 | LP-ICHS046_v1.0.icsw | 9999 | ✓ |
| LP-8797-1 | LP-ADDC0C0_v1.0.icsw | 22 | ✓ |
| LP-8797-1 | LP-ICHS050_v1.0.icsw | 9999 | ✓ |

### 6.2 错误情况 - 文件与服务器类型不匹配

| 服务器类型 | 文件名 | 期望结果 |
|-----------|--------|---------|
| LP-8650-1 | LP-ADDC0C0_v1.0.icsw | 错误：文件是LP-8797的，不匹配LP-8650 |
| LP-8650-1 | LP-ICHS050_v1.0.icsw | 错误：文件是LP-8797的，不匹配LP-8650 |
| LP-8797-1 | LP-ADDC050_v1.0.icsw | 错误：文件是LP-8650的，不匹配LP-8797 |
| LP-8797-1 | LP-ICHS046_v1.0.icsw | 错误：文件是LP-8650的，不匹配LP-8797 |

### 6.3 其他错误情况

| 服务器类型 | 文件名 | 期望结果 |
|-----------|--------|---------|
| LP-8650-1 | invalid_file.icsw | 错误：不包含任何有效前缀 |
| LP-8650-1 | LP-ADDC050_LP-ICHS046.icsw | 错误：匹配多个端口（同一服务器类型） |
| LP-9999-1 | LP-ADDC050_v1.0.icsw | 错误：未知服务器类型 |

### 6.4 批量FOTA特殊情况

| 场景 | 服务器列表 | 文件名 | 期望结果 |
|------|-----------|--------|---------|
| 混合服务器类型 | LP-8650-1, LP-8797-1 | LP-ADDC050_v1.0.icsw | 错误：文件只匹配LP-8650，不匹配LP-8797 |
| 混合服务器类型 | LP-8650-1, LP-8797-1 | LP-ADDC0C0_v1.0.icsw | 错误：文件只匹配LP-8797，不匹配LP-8650 |
| 单一服务器类型 | LP-8650-1, LP-8650-2 | LP-ADDC050_v1.0.icsw | ✓ 成功：所有服务器都是LP-8650类型 |
| 单一服务器类型 | LP-8797-1, LP-8797-2 | LP-ICHS050_v1.0.icsw | ✓ 成功：所有服务器都是LP-8797类型 |

## 七、风险评估

### 7.1 技术风险：低

- ✅ 逻辑简单，易于实现
- ✅ 复用现有配置
- ✅ 向后兼容

### 7.2 业务风险：低

- ✅ 错误提示明确
- ✅ 用户操作流程清晰
- ✅ 保持现有功能不变

### 7.3 实施风险：低

- ✅ 代码修改量小
- ✅ 测试用例明确
- ✅ 可以分阶段实施

## 八、总结

### 8.1 优势

1. **用户体验提升**：减少操作步骤，降低错误率
2. **智能化**：自动判断端口，减少人工选择
3. **向后兼容**：不影响现有功能
4. **易于维护**：逻辑清晰，代码简洁

### 8.2 实施建议

1. **先实现后端**：确保端口检测逻辑正确
2. **再实现前端**：基于稳定的后端API
3. **充分测试**：覆盖各种文件名组合
4. **用户培训**：告知新的操作方式

### 8.3 预计工作量

- **后端开发**：1-2天
- **前端开发**：1-2天
- **测试优化**：1天
- **总计**：3-5天

---

**设计日期**: 2025-12-24  
**版本**: v1.0

