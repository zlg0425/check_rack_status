#!/usr/bin/env python3
"""
基于 Flask 的简易前端，实时展示服务器端口与 SSH 状态。
运行：python web_ui.py
"""

import threading
import time
import uuid
import json
from flask import Flask, jsonify, render_template_string, request, Response, stream_with_context
from check_rack_status import (
    load_config,
    run_checks_once,
    check_interval,
    ssh_username,
    upload_target_dir,
    sftp_upload,
    fota_target_dir,
    md5_bytes,
    remote_md5,
    run_ucm_with_log,
    remote_exists,
    remote_remove,
)

app = Flask(__name__)

status_cache = {
    "data": [],
    "timestamp": 0,
    "error": "",
}
cache_lock = threading.Lock()
FOTA_LOG = "fota.log"
BACKEND_LOG = "backend.log"

# 上传任务进度字典 {task_id: {"progress": 0-100, "status": "uploading|done|error", "result": {...}}}
upload_tasks = {}
upload_tasks_lock = threading.Lock()

# FOTA任务进度字典 {task_id: {"progress": 0-100, "status": "uploading|md5|upgrading|done|error", "step": "...", "result": {...}}}
fota_tasks = {}
fota_tasks_lock = threading.Lock()

# 服务器FOTA执行锁 {server_key: task_id}，防止同一服务器同时执行多个FOTA任务
fota_server_locks = {}
fota_server_locks_lock = threading.Lock()

# 批量FOTA任务字典 {batch_id: {"tasks": [task_id1, task_id2, ...], "status": "running|done|error", "total": N, "completed": M}}
batch_fota_tasks = {}
batch_fota_tasks_lock = threading.Lock()


def log_fota(message: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(FOTA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def log_srv(message: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(BACKEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def refresh_loop():
    """后台循环刷新状态缓存"""
    while True:
        try:
            results = run_checks_once()
            with cache_lock:
                status_cache["data"] = results
                status_cache["timestamp"] = time.time()
                status_cache["error"] = ""
        except Exception as e:
            with cache_lock:
                status_cache["error"] = str(e)
            log_srv(f"refresh_loop error: {e}")
        time.sleep(max(5, check_interval))


@app.route("/api/status")
def api_status():
    with cache_lock:
        payload = {
            "timestamp": status_cache["timestamp"],
            "servers": status_cache["data"],
            "error": status_cache["error"],
            "upload_target_dir": upload_target_dir,
            "fota_target_dir": fota_target_dir,
        }
    return jsonify(payload)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = int(request.form.get("port", 0))
        target_dir = request.form.get("target_dir", "").strip() or upload_target_dir
        file = request.files.get("file")

        if not server_name or not server_ip or port not in (22, 9999):
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        task_id = str(uuid.uuid4())
        data = file.read()
        
        with upload_tasks_lock:
            upload_tasks[task_id] = {"progress": 0, "status": "uploading", "result": None}
        
        def progress_cb(loaded, total):
            percent = int((loaded / total) * 100) if total > 0 else 0
            with upload_tasks_lock:
                if task_id in upload_tasks:
                    upload_tasks[task_id]["progress"] = percent
        
        def do_upload():
            try:
                ok, info = sftp_upload(server_name, server_ip, port, target_dir, file.filename, data, progress_cb)
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "done" if ok else "error",
                        "result": {"ok": ok, "path": info if ok else None, "error": info if not ok else None}
                    }
            except Exception as e:
                with upload_tasks_lock:
                    upload_tasks[task_id] = {
                        "progress": 100,
                        "status": "error",
                        "result": {"ok": False, "error": str(e)}
                    }
                log_srv(f"upload exception: {e}")
        
        threading.Thread(target=do_upload, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_srv(f"upload exception: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/api/upload/progress/<task_id>")
def api_upload_progress(task_id):
    """SSE 推送上传进度"""
    def generate():
        last_progress = -1
        while True:
            with upload_tasks_lock:
                task = upload_tasks.get(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task["progress"]
            if current_progress != last_progress:
                yield f"data: {json.dumps({'progress': current_progress, 'status': task['status'], 'result': task['result']})}\n\n"
                last_progress = current_progress
            
            if task["status"] in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/fota", methods=["POST"])
def api_fota():
    try:
        server_name = request.form.get("server_name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        port = int(request.form.get("port", 0))
        file = request.files.get("file")

        if not server_name or not server_ip or port not in (22, 9999):
            log_fota(f"[{server_name}/{server_ip}:{port}] 参数缺失或端口非法")
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not file or file.filename == "":
            log_fota(f"[{server_name}/{server_ip}:{port}] 未选择文件")
            return jsonify({"ok": False, "error": "未选择文件"}), 400

        # 检查服务器是否已有正在执行的FOTA任务
        server_key = f"{server_name}:{server_ip}:{port}"
        with fota_server_locks_lock:
            if server_key in fota_server_locks:
                existing_task = fota_server_locks[server_key]
                # 检查现有任务是否还在执行中
                with fota_tasks_lock:
                    existing_task_info = fota_tasks.get(existing_task)
                    if existing_task_info and existing_task_info["status"] not in ("done", "error"):
                        log_fota(f"[{server_name}/{server_ip}:{port}] 服务器已有正在执行的FOTA任务: {existing_task}")
                        return jsonify({"ok": False, "error": f"服务器 {server_name} 已有正在执行的FOTA任务，请等待完成后再试"}), 409
            
            task_id = str(uuid.uuid4())
            fota_server_locks[server_key] = task_id

        data = file.read()
        local_md5 = md5_bytes(data)
        filename = file.filename
        remote_path = f"{fota_target_dir.rstrip('/')}/{filename}"

        with fota_tasks_lock:
            fota_tasks[task_id] = {"progress": 0, "status": "uploading", "step": "开始上传...", "result": None}

        def update_fota_progress(progress, status, step):
            with fota_tasks_lock:
                if task_id in fota_tasks:
                    fota_tasks[task_id]["progress"] = progress
                    fota_tasks[task_id]["status"] = status
                    fota_tasks[task_id]["step"] = step

        def do_fota():
            try:
                log_fota(f"[{server_name}/{server_ip}:{port}] 开始FOTA，文件={filename}，本地MD5={local_md5}")

                # 上传阶段 (0-70%)
                def upload_progress_cb(loaded, total):
                    percent = int((loaded / total) * 70) if total > 0 else 0
                    update_fota_progress(percent, "uploading", f"上传中... {percent}%")

                need_upload = True
                if remote_exists(server_name, server_ip, port, remote_path):
                    ok_md5_pre, r_md5_pre = remote_md5(server_name, server_ip, port, remote_path)
                    if ok_md5_pre and r_md5_pre == local_md5:
                        log_fota(f"[{server_name}/{server_ip}:{port}] 远端已存在且MD5一致，跳过上传，远端MD5={r_md5_pre}")
                        need_upload = False
                        update_fota_progress(70, "md5", "远端已存在且MD5一致，跳过上传")
                    else:
                        log_fota(f"[{server_name}/{server_ip}:{port}] 远端已有同名文件，MD5不同，远端MD5={r_md5_pre if ok_md5_pre else '未知'}，删除后重传")
                        remote_remove(server_name, server_ip, port, remote_path)

                if need_upload:
                    ok, info = sftp_upload(server_name, server_ip, port, fota_target_dir, filename, data, upload_progress_cb)
                    if not ok:
                        log_fota(f"[{server_name}/{server_ip}:{port}] SCP失败: {info}")
                        with fota_tasks_lock:
                            fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"SCP失败: {info}", "result": {"ok": False, "error": f"SCP失败: {info}"}}
                        with fota_server_locks_lock:
                            if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                                del fota_server_locks[server_key]
                        return

                # MD5校验阶段 (70-90%)
                update_fota_progress(75, "md5", "计算远端MD5...")
                ok_md5, r_md5 = remote_md5(server_name, server_ip, port, remote_path)
                if not ok_md5:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 远端MD5失败: {r_md5}")
                    with fota_tasks_lock:
                        fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"远端MD5失败: {r_md5}", "result": {"ok": False, "error": f"远端MD5失败: {r_md5}"}}
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                    return

                log_fota(f"[{server_name}/{server_ip}:{port}] 上传后MD5校验，本地={local_md5} 远端={r_md5}")
                update_fota_progress(85, "md5", f"MD5校验: 本地={local_md5[:8]}... 远端={r_md5[:8]}...")

                if local_md5 != r_md5:
                    log_fota(f"[{server_name}/{server_ip}:{port}] MD5不一致，本地={local_md5} 远端={r_md5}")
                    with fota_tasks_lock:
                        fota_tasks[task_id] = {"progress": 100, "status": "error", "step": "上传文件不完整&升级失败", "result": {"ok": False, "error": "上传文件不完整&升级失败", "local_md5": local_md5, "remote_md5": r_md5}}
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                    return

                # 升级执行阶段 (90-100%)
                update_fota_progress(90, "upgrading", f"开始执行 lpUCM -i {remote_path}")
                log_fota(f"[{server_name}/{server_ip}:{port}] MD5一致，开始执行 lpUCM -i {remote_path}")

                ok_ucm, info_ucm = run_ucm_with_log(server_name, server_ip, port, remote_path)
                if not ok_ucm:
                    log_fota(f"[{server_name}/{server_ip}:{port}] 升级失败: {info_ucm}")
                    with fota_tasks_lock:
                        fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"升级失败: {info_ucm}", "result": {"ok": False, "error": f"升级失败: {info_ucm}", "local_md5": local_md5, "remote_md5": r_md5}}
                    with fota_server_locks_lock:
                        if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                            del fota_server_locks[server_key]
                    return

                log_fota(f"[{server_name}/{server_ip}:{port}] 升级成功，远端={remote_path} MD5={r_md5}")
                with fota_tasks_lock:
                    fota_tasks[task_id] = {"progress": 100, "status": "done", "step": "升级成功", "result": {"ok": True, "path": remote_path, "local_md5": local_md5, "remote_md5": r_md5, "ucm_output": info_ucm}}
            except Exception as e:
                log_fota(f"FOTA异常: {e}")
                log_srv(f"FOTA异常: {e}")
                with fota_tasks_lock:
                    fota_tasks[task_id] = {"progress": 100, "status": "error", "step": f"服务器异常: {e}", "result": {"ok": False, "error": f"服务器异常: {e}"}}
            finally:
                # 释放服务器锁
                with fota_server_locks_lock:
                    if server_key in fota_server_locks and fota_server_locks[server_key] == task_id:
                        del fota_server_locks[server_key]

        threading.Thread(target=do_fota, daemon=True).start()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        log_fota(f"FOTA异常: {e}")
        log_srv(f"FOTA异常: {e}")
        return jsonify({"ok": False, "error": f"服务器异常: {e}"}), 500


@app.route("/api/fota/progress/<task_id>")
def api_fota_progress(task_id):
    """SSE 推送FOTA进度"""
    def generate():
        last_progress = -1
        while True:
            with fota_tasks_lock:
                task = fota_tasks.get(task_id)
            
            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            
            current_progress = task["progress"]
            if current_progress != last_progress or task["status"] != "uploading":
                yield f"data: {json.dumps({'progress': current_progress, 'status': task['status'], 'step': task['step'], 'result': task['result']})}\n\n"
                last_progress = current_progress
            
            if task["status"] in ("done", "error"):
                break
            
            time.sleep(0.2)
    
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/")
def index():
    html = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>杭州办公室台架服务器监控</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --card-bg: #fff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #e5e7eb;
      --accent1: #2563eb;
      --accent2: #3b82f6;
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: var(--bg); color: var(--text); }
    header { background: linear-gradient(135deg, var(--accent1), var(--accent2)); color: #fff; padding: 16px 24px; box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
    .container { padding: 20px; max-width: 1400px; margin: 0 auto; }
    .card { background: var(--card-bg); border-radius: 12px; box-shadow: 0 10px 30px rgba(15,23,42,0.08); padding: 14px; }
    .table-wrap { overflow: auto; max-width: 100%; }
    table { width: 100%; min-width: 1200px; border-collapse: separate; border-spacing: 0; }
    th, td { padding: 10px 8px; text-align: left; white-space: nowrap; border-bottom: 1px solid #eaeef4; }
    th { font-weight: 600; color: #374151; background: #f8fafc; position: sticky; top: 0; z-index: 1; border-bottom: 1px solid #d9e2ec; }
    tr:hover td { background: #eef2ff; }
    th + th, td + td { border-left: 1px solid #f0f2f6; }
    th.col-name, td.col-name { position: sticky; left: 0; z-index: 3; min-width: 150px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); }
    th.col-ip, td.col-ip { position: sticky; left: 150px; z-index: 3; min-width: 150px; background: #f8fafc; box-shadow: 2px 0 6px rgba(15,23,42,0.05); }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
    .ok { background: #dcfce7; color: #166534; }
    .warn { background: #fee2e2; color: #991b1b; }
    .unknown { background: #e0f2fe; color: #075985; }
    .meta { color: var(--muted); font-size: 14px; margin-top: 6px; }
    .error { color: #b91c1c; margin-bottom: 12px; }
    .btn { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border); background: #eef2ff; color: #1e3a8a; cursor: pointer; font-size: 12px; }
    .btn:hover { background: #e0e7ff; }
    .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; align-items: center; justify-content: center; z-index: 20; }
    .modal { background: #fff; border-radius: 12px; padding: 18px; width: 360px; box-shadow: 0 12px 40px rgba(0,0,0,0.18); }
    .modal h3 { margin: 0 0 12px 0; }
    .modal label { display: block; margin: 10px 0 4px 0; font-size: 13px; color: #374151; }
    .modal input[type="text"], .modal input[type="file"] { width: 100%; }
    .modal-actions { margin-top: 14px; display: flex; gap: 8px; justify-content: flex-end; }
    .progress { margin-top: 8px; font-size: 13px; color: #374151; }
    @media (max-width: 900px) {
      table { min-width: 820px; }
    }
  </style>
</head>
<body>
  <header>
    <h2>杭州办公室台架服务器监控面板</h2>
    <div class="meta" id="updatedAt">加载中...</div>
  </header>
  <div class="container">
    <div class="card">
      <div id="errorBox" class="error"></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="col-name">名称</th>
              <th class="col-ip">IP</th>
              <th>智驾域(22)</th>
              <th>智驾域SSH</th>
              <th>智驾域详情</th>
              <th>座舱域(9999)</th>
              <th>座舱域SSH</th>
              <th>座舱域详情</th>
              <th>智驾域版本</th>
              <th>座舱域版本</th>
              <th>上传</th>
              <th>FOTA</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody id="tableBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="uploadModal">
    <div class="modal">
      <h3>上传文件</h3>
      <div>服务器：<span id="uploadServer"></span></div>
      <div>端口：<span id="uploadPort"></span></div>
      <label>目标目录</label>
      <input type="text" id="targetDir" placeholder="例如 /tmp">
      <label style="margin-top:8px;">选择文件</label>
      <input type="file" id="fileInput">
      <div class="progress" id="uploadProgress"></div>
      <div class="modal-actions">
        <button class="btn" onclick="closeUpload()">取消</button>
        <button class="btn" onclick="uploadFile()">上传</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="fotaModal">
    <div class="modal">
      <h3>FOTA 升级</h3>
      <div>服务器：<span id="fotaServer"></span></div>
      <div>端口：<span id="fotaPort"></span></div>
      <div>目标目录：<span id="fotaTargetText"></span></div>
      <label style="margin-top:8px;">选择升级包</label>
      <input type="file" id="fotaFile">
      <div class="progress" id="fotaProgress"></div>
      <div class="progress" id="fotaStep" style="margin-top:4px;"></div>
      <div class="modal-actions">
        <button class="btn" onclick="closeFota()">取消</button>
        <button class="btn" onclick="startFota()">升级</button>
      </div>
    </div>
  </div>

  <script>
    function tag(text, cls) {
      return '<span class="tag ' + cls + '">' + text + '</span>';
    }

    function statusClass(val) {
      if (val === 'online') return 'ok';
      if (val === '异常' || val === 'offline') return 'warn';
      return 'unknown';
    }

    const sshUser = "{{ ssh_user }}";
    let defaultTarget = "";
    let fotaTarget = "";
    let currentUpload = { name: "", ip: "", port: 22 };
    let currentFota = { name: "", ip: "", port: 22 };

    const backdrop = document.getElementById('uploadModal');
    const targetInput = document.getElementById('targetDir');
    const fileInput = document.getElementById('fileInput');
    const progressBox = document.getElementById('uploadProgress');
    const fotaBackdrop = document.getElementById('fotaModal');
    const fotaFile = document.getElementById('fotaFile');
    const fotaServer = document.getElementById('fotaServer');
    const fotaPort = document.getElementById('fotaPort');
    const fotaTargetText = document.getElementById('fotaTargetText');
    const fotaProgress = document.getElementById('fotaProgress');
    const fotaStep = document.getElementById('fotaStep');

    function formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
      return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
    }

    function formatSpeed(loaded, seconds) {
      const bps = loaded / seconds;
      if (bps < 1024) return bps.toFixed(0) + ' B/s';
      if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + ' KB/s';
      return (bps / 1024 / 1024).toFixed(1) + ' MB/s';
    }

    const uploadServer = document.getElementById('uploadServer');
    const uploadPort = document.getElementById('uploadPort');

    function openUpload(name, ip, port) {
      currentUpload = { name, ip, port };
      uploadServer.textContent = name;
      uploadPort.textContent = port;
      targetInput.value = defaultTarget || '';
      fileInput.value = '';
      progressBox.textContent = '';
      backdrop.style.display = 'flex';
    }

    function closeUpload() {
      backdrop.style.display = 'none';
    }

    function uploadFile() {
      const file = fileInput.files[0];
      const targetDir = (targetInput.value || defaultTarget || '').trim();
      if (!file) {
        alert('请先选择文件');
        return;
      }
      if (!targetDir) {
        alert('请填写目标目录');
        return;
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('server_name', currentUpload.name);
      fd.append('server_ip', currentUpload.ip);
      fd.append('port', currentUpload.port);
      fd.append('target_dir', targetDir);

      const startTs = Date.now();
      const totalSize = file.size;
      progressBox.textContent = '开始上传...';

      fetch('/api/upload', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.task_id) {
          progressBox.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        const es = new EventSource(`/api/upload/progress/${taskId}`);
        
        es.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.error) {
            progressBox.textContent = `失败: ${msg.error}`;
            es.close();
            return;
          }

          const percent = msg.progress || 0;
          const elapsed = Math.max((Date.now() - startTs) / 1000, 0.001);
          const loaded = Math.floor(totalSize * percent / 100);
          const speed = formatSpeed(loaded, elapsed);
          
          if (msg.status === 'uploading') {
            progressBox.textContent = `上传进度: ${percent}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
          } else if (msg.status === 'done') {
            es.close();
            if (msg.result && msg.result.ok) {
              progressBox.textContent = `上传成功: ${msg.result.path}`;
            } else {
              progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
            }
          } else if (msg.status === 'error') {
            es.close();
            progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
          }
        };

        es.onerror = () => {
          es.close();
          progressBox.textContent = '进度监听失败';
        };
      }).catch(err => {
        progressBox.textContent = `上传失败: ${err}`;
      });
    }

    function openFota(name, ip, port) {
      currentFota = { name, ip, port };
      fotaServer.textContent = name;
      fotaPort.textContent = port;
      fotaTargetText.textContent = fotaTarget;
      fotaFile.value = '';
      fotaProgress.textContent = '';
      fotaStep.textContent = '等待选择文件...';
      fotaBackdrop.style.display = 'flex';
    }

    function closeFota() {
      fotaBackdrop.style.display = 'none';
    }

    function startFota() {
      const file = fotaFile.files[0];
      if (!file) {
        alert('请先选择文件');
        return;
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('server_name', currentFota.name);
      fd.append('server_ip', currentFota.ip);
      fd.append('port', currentFota.port);

      const startTs = Date.now();
      const totalSize = file.size;
      fotaStep.textContent = '开始上传...';
      fotaProgress.textContent = '';

      fetch('/api/fota', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.task_id) {
          fotaStep.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        const es = new EventSource(`/api/fota/progress/${taskId}`);
        
        es.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.error) {
            fotaStep.textContent = `失败: ${msg.error}`;
            es.close();
            return;
          }

          const percent = msg.progress || 0;
          fotaStep.textContent = msg.step || '处理中...';

          if (msg.status === 'uploading') {
            const elapsed = Math.max((Date.now() - startTs) / 1000, 0.001);
            const loaded = Math.floor(totalSize * percent / 100);
            const speed = formatSpeed(loaded, elapsed);
            fotaProgress.textContent = `上传进度: ${percent}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
          } else if (msg.status === 'md5') {
            fotaProgress.textContent = `MD5校验中... ${percent}%`;
          } else if (msg.status === 'upgrading') {
            fotaProgress.textContent = `升级执行中... ${percent}%`;
          } else if (msg.status === 'done') {
            es.close();
            if (msg.result && msg.result.ok) {
              fotaStep.textContent = '完成：升级成功';
              fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
            } else {
              fotaStep.textContent = `失败: ${msg.result?.error || '未知错误'}`;
              if (msg.result?.local_md5 && msg.result?.remote_md5) {
                fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
              }
            }
          } else if (msg.status === 'error') {
            es.close();
            fotaStep.textContent = `失败: ${msg.result?.error || '未知错误'}`;
            if (msg.result?.local_md5 && msg.result?.remote_md5) {
              fotaProgress.textContent = `本地MD5: ${msg.result.local_md5} | 远端MD5: ${msg.result.remote_md5}`;
            }
          }
        };

        es.onerror = () => {
          es.close();
          fotaStep.textContent = '进度监听失败';
        };
      }).catch(err => {
        fotaStep.textContent = `升级失败: ${err}`;
      });
    }

    async function loadStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        defaultTarget = data.upload_target_dir || '';
        fotaTarget = data.fota_target_dir || '/opt/data/fota';

        const tbody = document.getElementById('tableBody');
        tbody.innerHTML = '';

        const sorted = (data.servers || []).slice().sort((a, b) =>
          a.server_name.localeCompare(b.server_name, 'zh-Hans-CN-u-nu-latn', { numeric: true })
        );

        sorted.forEach(item => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td class="col-name">${item.server_name}</td>
            <td class="col-ip">${item.server_ip}</td>
            <td>${tag(item.port_22, statusClass(item.port_22))}</td>
            <td>${tag(item.port_22_ssh, statusClass(item.port_22_ssh))}</td>
            <td>${item.port_22_detail || ''}</td>
            <td>${tag(item.port_9999, statusClass(item.port_9999))}</td>
            <td>${tag(item.port_9999_ssh, statusClass(item.port_9999_ssh))}</td>
            <td>${item.port_9999_detail || ''}</td>
            <td>${item.version || item.version_detail || ''}</td>
            <td>${item.version_9999 || item.version_9999_detail || ''}</td>
            <td>
              <button class="btn" onclick="openUpload('${item.server_name}','${item.server_ip}',22)">上传22</button>
              <button class="btn" onclick="openUpload('${item.server_name}','${item.server_ip}',9999)">上传9999</button>
            </td>
            <td>
              <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',22)">FOTA 22</button>
              <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}',9999)">FOTA 9999</button>
            </td>
            <td>${item.timestamp}</td>
          `;
          tbody.appendChild(tr);
        });

        const updatedAt = document.getElementById('updatedAt');
        if (data.timestamp) {
          const date = new Date(data.timestamp * 1000);
          updatedAt.textContent = '最后更新：' + date.toLocaleString();
        } else {
          updatedAt.textContent = '最后更新：等待数据...';
        }

        const errorBox = document.getElementById('errorBox');
        if (data.error) {
          errorBox.textContent = '错误：' + data.error;
        } else {
          errorBox.textContent = '';
        }
      } catch (e) {
        document.getElementById('errorBox').textContent = '请求失败：' + e;
      }
    }

    loadStatus();
    setInterval(loadStatus, 5000);
  </script>
</body>
</html>
    """
    return render_template_string(html, ssh_user=ssh_username or "root")


def start_background():
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    load_config()  # 读取配置
    # 初始化一次数据
    try:
        status_cache["data"] = run_checks_once()
        status_cache["timestamp"] = time.time()
    except Exception as e:
        status_cache["error"] = str(e)

    start_background()
    app.run(host="0.0.0.0", port=5000, debug=False)

