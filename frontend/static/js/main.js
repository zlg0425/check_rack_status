    function tag(text, cls) {
      return '<span class="tag ' + cls + '">' + text + '</span>';
    }

    function statusClass(val) {
      if (val === 'online') return 'ok';
      if (val === '异常' || val === 'offline') return 'warn';
      return 'unknown';
    }

    const sshUser = window.SSH_USER || "root";
    let defaultTarget = "";
    let fotaTarget = "";
    let currentUpload = { name: "", ip: "", port: 22 };
    let currentFota = { name: "", ip: "", port: 22 };
    let currentBatchFotaPort = 0;  // 存储批量FOTA检测到的端口
    let selectedServers = {}; // {server_key: {name, ip}}

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
    const downloadBackdrop = document.getElementById('downloadModal');
    const downloadServer = document.getElementById('downloadServer');
    const downloadPort = document.getElementById('downloadPort');
    const remotePathInput = document.getElementById('remotePath');
    const downloadProgressBox = document.getElementById('downloadProgress');
    let currentDownload = { name: "", ip: "", port: 22 };
    
    // Global error handler
    window.addEventListener('error', function(e) {
      // 静默处理错误
    });

    function openDownload(name, ip, port) {
      currentDownload = { name, ip, port };
      downloadServer.textContent = name;
      downloadPort.textContent = port;
      remotePathInput.value = '';
      downloadProgressBox.textContent = '';
      downloadBackdrop.style.display = 'flex';
    }

    function closeDownload() {
      downloadBackdrop.style.display = 'none';
    }

    function downloadFile() {
      try {
      const remotePath = remotePathInput.value.trim();
      
      if (!remotePath) {
        alert('请填写远程文件路径');
        return;
      }

      downloadProgressBox.textContent = '正在准备下载...';

      // 构建流式下载URL
      const downloadUrl = `/api/download/stream?server_name=${encodeURIComponent(currentDownload.name)}&server_ip=${encodeURIComponent(currentDownload.ip)}&port=${currentDownload.port}&remote_path=${encodeURIComponent(remotePath)}`;
      
      // 先使用HEAD请求检查API是否可用，避免直接打开窗口显示错误
      downloadProgressBox.textContent = '正在检查下载链接...';
      
      // 创建超时控制器
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      
      fetch(downloadUrl, { method: 'HEAD', signal: controller.signal })
        .then(response => {
          clearTimeout(timeoutId);
          
          if (!response.ok) {
            // 如果是错误响应，尝试读取JSON错误信息
            return response.text().then(text => {
              try {
                const errorData = JSON.parse(text);
                throw new Error(errorData.error || `下载失败: HTTP ${response.status}`);
              } catch(e) {
                if (e instanceof Error && e.message.includes('下载失败')) {
                  throw e;
                }
                throw new Error(`下载失败: HTTP ${response.status} ${response.statusText}`);
              }
            });
          }
          
          // 检查Content-Type，如果是JSON，说明是错误响应
          const contentType = response.headers.get('content-type') || '';
          if (contentType.includes('application/json')) {
            return response.text().then(text => {
              try {
                const errorData = JSON.parse(text);
                throw new Error(errorData.error || '下载失败: 服务器返回错误');
              } catch(e) {
                if (e instanceof Error && e.message.includes('下载失败')) {
                  throw e;
                }
                throw new Error('下载失败: 服务器返回错误');
              }
            });
          }
          
          // 成功，使用window.open或<a>标签触发下载
          downloadProgressBox.textContent = '正在准备下载，请稍候...';
          
          // 显示等待提示
          let waitTime = 0;
          const progressInterval = setInterval(() => {
            waitTime += 1;
            if (waitTime <= 5) {
              downloadProgressBox.textContent = `正在准备下载，请稍候... (${waitTime}秒)`;
            } else if (waitTime <= 30) {
              downloadProgressBox.textContent = `正在生成下载文件，请稍候... (${waitTime}秒)`;
            } else {
              downloadProgressBox.textContent = `正在生成下载文件，可能需要较长时间，请耐心等待... (${waitTime}秒)`;
            }
          }, 1000);
          
          // 尝试使用window.open打开下载URL
          const downloadWindow = window.open(downloadUrl, '_blank');
          
          // 如果window.open被阻止，回退到使用<a>标签
          if (!downloadWindow) {
            clearInterval(progressInterval);
            downloadProgressBox.textContent = '正在下载，请在弹出的对话框中选择保存位置...';
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = '';
            link.style.display = 'none';
            document.body.appendChild(link);
            link.click();
            setTimeout(() => {
              document.body.removeChild(link);
              downloadProgressBox.textContent = '下载已开始！如果下载未开始，请检查浏览器下载设置。';
            }, 100);
          } else {
            
            // 监控下载窗口状态
            let checkCount = 0;
            const checkInterval = setInterval(() => {
              checkCount++;
              try {
                // 检查窗口是否已关闭
                if (downloadWindow.closed) {
                  clearInterval(progressInterval);
                  clearInterval(checkInterval);
                  downloadProgressBox.textContent = '下载窗口已关闭。如果下载未完成，请检查浏览器下载设置或重试。';
                  setTimeout(() => {
                    closeDownload();
                  }, 3000);
                  return;
                }
                
                // 每5秒更新一次提示
                if (checkCount % 5 === 0) {
                  downloadProgressBox.textContent = `正在生成下载文件，请保持窗口打开... (${waitTime}秒)`;
                }
                
                // 如果超过60秒，提示用户可能需要更长时间
                if (waitTime > 60) {
                  downloadProgressBox.textContent = `文件较大，生成时间可能较长，请继续等待... (${waitTime}秒)`;
                }
              } catch(e) {
                // 窗口可能已关闭或无法访问
                clearInterval(progressInterval);
                clearInterval(checkInterval);
                downloadProgressBox.textContent = '下载窗口已关闭。如果下载未完成，请检查浏览器下载设置或重试。';
                setTimeout(() => {
                  closeDownload();
                }, 3000);
              }
            }, 1000);
            
            // 设置最大等待时间（5分钟）
            setTimeout(() => {
              clearInterval(progressInterval);
              clearInterval(checkInterval);
              try {
                if (!downloadWindow.closed) {
                  downloadProgressBox.textContent = '下载时间较长，窗口将保持打开。如果下载未开始，请检查浏览器下载设置。';
                  // 不自动关闭窗口，让用户手动关闭
                }
              } catch(e) {
                // 忽略错误
              }
            }, 300000); // 5分钟
          }
        })
        .catch(error => {
          clearTimeout(timeoutId);
          const errorMsg = error.name === 'AbortError' ? '下载超时，请检查网络连接或文件大小' : error.message;
          downloadProgressBox.textContent = `下载失败: ${errorMsg}`;
          alert(`下载失败: ${errorMsg}`);
        });
      
      // 注意：下载进度提示已在fetch的then/catch中处理，这里不需要立即关闭窗口
      } catch(e) {
        alert('下载失败: ' + e.message);
      }
    }

    let uploadMode = 'file';  // 'file' 或 'folder'

    function switchUploadMode(mode) {
      uploadMode = mode;
      const fileInput = document.getElementById('fileInput');
      const folderInput = document.getElementById('folderInput');
      const fileModeBtn = document.getElementById('fileModeBtn');
      const folderModeBtn = document.getElementById('folderModeBtn');
      
      if (mode === 'file') {
        fileInput.style.display = 'block';
        folderInput.style.display = 'none';
        fileModeBtn.style.background = '#3b82f6';
        folderModeBtn.style.background = '#6b7280';
        fileInput.value = '';
        folderInput.value = '';
        updateSelectedFiles();  // 清空显示
      } else {
        fileInput.style.display = 'none';
        folderInput.style.display = 'block';
        fileModeBtn.style.background = '#6b7280';
        folderModeBtn.style.background = '#3b82f6';
        fileInput.value = '';
        folderInput.value = '';
        updateSelectedFiles();  // 清空显示
      }
    }

    function updateSelectedFiles() {
      const fileInput = document.getElementById('fileInput');
      const folderInput = document.getElementById('folderInput');
      const files = uploadMode === 'file' ? fileInput.files : folderInput.files;
      const selectedFilesList = document.getElementById('selectedFilesList');
      const selectedFilesCount = document.getElementById('selectedFilesCount');
      const selectedFilesContent = document.getElementById('selectedFilesContent');
      
      if (!files || files.length === 0) {
        selectedFilesList.style.display = 'none';
        return;
      }
      
      selectedFilesCount.textContent = files.length;
      selectedFilesList.style.display = 'block';
      
      let html = '';
      if (uploadMode === 'folder') {
        // 文件夹选择：显示相对路径，带层级缩进
        const pathMap = new Map();
        for (let i = 0; i < files.length; i++) {
          const file = files[i];
          const relativePath = file.webkitRelativePath || file.name;
          const parts = relativePath.split('/');
          const depth = parts.length - 1;
          
          if (!pathMap.has(depth)) {
            pathMap.set(depth, []);
          }
          pathMap.get(depth).push({
            path: relativePath,
            name: parts[parts.length - 1],
            depth: depth,
            size: formatBytes(file.size)
          });
        }
        
        // 按深度排序并显示
        const sortedDepths = Array.from(pathMap.keys()).sort((a, b) => a - b);
        html = '<div>';
        for (const depth of sortedDepths) {
          const items = pathMap.get(depth);
          items.sort((a, b) => a.path.localeCompare(b.path));
          
          for (const item of items) {
            const indent = '  '.repeat(item.depth);
            const isDirectory = item.path.endsWith('/') || item.depth > 0;
            const icon = isDirectory ? '📁' : '📄';
            const className = isDirectory ? 'file-item file-item-folder' : 'file-item';
            
            html += `
              <div class="${className}">
                <div class="file-name">
                  <span class="file-icon">${icon}</span>
                  <span style="margin-left: ${item.depth * 8}px;">${item.name}</span>
                  <span class="file-size">${item.size}</span>
                </div>
                <div class="file-path">${item.path}</div>
              </div>
            `;
          }
        }
        html += '</div>';
      } else {
        // 多文件选择：显示文件名和大小，带图标
        html = '<div>';
        for (let i = 0; i < files.length; i++) {
          const file = files[i];
          const size = formatBytes(file.size);
          const ext = file.name.split('.').pop().toLowerCase();
          let icon = '📄';
          
          // 根据文件扩展名选择图标
          const iconMap = {
            'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'svg': '🖼️',
            'pdf': '📕', 'doc': '📘', 'docx': '📘', 'xls': '📊', 'xlsx': '📊',
            'zip': '📦', 'rar': '📦', '7z': '📦', 'tar': '📦', 'gz': '📦',
            'mp4': '🎬', 'avi': '🎬', 'mov': '🎬', 'mp3': '🎵', 'wav': '🎵',
            'txt': '📝', 'md': '📝', 'log': '📝',
            'exe': '⚙️', 'msi': '⚙️', 'sh': '⚙️', 'bat': '⚙️',
            'py': '🐍', 'js': '📜', 'html': '🌐', 'css': '🎨', 'json': '📋'
          };
          icon = iconMap[ext] || icon;
          
          html += `
            <div class="file-item">
              <div class="file-name">
                <span class="file-icon">${icon}</span>
                ${file.name}
                <span class="file-size">${size}</span>
              </div>
            </div>
          `;
        }
        html += '</div>';
      }
      
      selectedFilesContent.innerHTML = html;
    }

    function openUpload(name, ip, port) {
      currentUpload = { name, ip, port };
      uploadServer.textContent = name;
      uploadPort.textContent = port;
      targetInput.value = defaultTarget || '';
      const fileInput = document.getElementById('fileInput');
      const folderInput = document.getElementById('folderInput');
      fileInput.value = '';
      folderInput.value = '';
      const selectedFilesList = document.getElementById('selectedFilesList');
      selectedFilesList.style.display = 'none';
      progressBox.textContent = '';
      backdrop.style.display = 'flex';
      // 重置为文件模式
      switchUploadMode('file');
    }

    function closeUpload() {
      backdrop.style.display = 'none';
    }

    async function uploadFile() {
      const fileInput = document.getElementById('fileInput');
      const folderInput = document.getElementById('folderInput');
      const files = uploadMode === 'file' ? fileInput.files : folderInput.files;
      const targetDir = (targetInput.value || defaultTarget || '').trim();
      
      if (!files || files.length === 0) {
        alert('请先选择文件或文件夹');
        return;
      }
      if (!targetDir) {
        alert('请填写目标目录');
        return;
      }

      // 如果是单个文件且是多文件模式，使用原有的单文件上传逻辑
      if (files.length === 1 && uploadMode === 'file') {
        const file = files[0];
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
                let doneText = `上传成功: ${msg.result.path || '成功'}`;
                if (msg.result.exists) {
                  doneText += ' (文件已存在，已覆盖)';
                }
                progressBox.textContent = doneText;
                progressBox.style.color = '#166534';
              } else {
                progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
                progressBox.style.color = '#991b1b';
              }
              setTimeout(() => {
                closeUpload();
              }, 1500);
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
        return;
      }

      // 多个文件上传（多文件或文件夹）
      const fd = new FormData();
      for (let i = 0; i < files.length; i++) {
        fd.append('files', files[i]);
      }
      fd.append('server_name', currentUpload.name);
      fd.append('server_ip', currentUpload.ip);
      fd.append('port', currentUpload.port);
      fd.append('target_dir', targetDir);
      fd.append('preserve_structure', uploadMode === 'folder' ? 'true' : 'false');  // 文件夹模式保持结构

      const startTs = Date.now();
      let totalSize = 0;
      for (let i = 0; i < files.length; i++) {
        totalSize += files[i].size;
      }
      const modeText = uploadMode === 'folder' ? '文件夹' : '多文件';
      progressBox.textContent = `开始上传${modeText} (${files.length} 个文件)...`;

      fetch('/api/upload-folder', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.task_id) {
          progressBox.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        const es = new EventSource(`/api/upload-folder/progress/${taskId}`);
        
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
            const currentFile = msg.current_file || '';
            const fileIndex = msg.file_index || 0;
            const totalFiles = msg.total_files || files.length;
            let progressText = `上传进度: ${percent}% (${fileIndex}/${totalFiles} 文件, ${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
            if (currentFile) {
              progressText += ` - 当前: ${currentFile}`;
            }
            progressBox.textContent = progressText;
          } else if (msg.status === 'done') {
            es.close();
            if (msg.result && msg.result.ok) {
              const successCount = msg.result.success_count || 0;
              const failCount = msg.result.fail_count || 0;
              let doneText = `上传完成: 成功 ${successCount} 个文件`;
              if (failCount > 0) {
                doneText += `，失败 ${failCount} 个文件`;
              }
              progressBox.textContent = doneText;
              progressBox.style.color = failCount > 0 ? '#f59e0b' : '#166534';
            } else {
              progressBox.textContent = `失败: ${msg.result?.error || '未知错误'}`;
              progressBox.style.color = '#991b1b';
            }
            setTimeout(() => {
              closeUpload();
            }, 2000);
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

    /**
     * 根据文件名和服务器名称自动判断端口
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
        
        // 处理HTTP错误状态码
        if (!response.ok) {
          let errorMessage = '检测失败';
          try {
            const errorData = await response.json();
            errorMessage = errorData.error || errorMessage;
          } catch (e) {
            errorMessage = `服务器错误: ${response.status} ${response.statusText}`;
          }
          return { port: 0, message: errorMessage };
        }
        
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

    function openFota(name, ip, port = null) {
      currentFota = { name, ip, port: port || 0 };  // port为0表示自动检测
      const fotaBackdrop = document.getElementById('fotaModal');
      const fotaServer = document.getElementById('fotaServer');
      const fotaFile = document.getElementById('fotaFile');
      const fotaTargetText = document.getElementById('fotaTargetText');
      const fotaPortHint = document.getElementById('fotaPortHint');
      const fotaStartBtn = document.getElementById('fotaStartBtn');
      
      fotaServer.textContent = `${name} (${ip})`;
      fotaTargetText.textContent = fotaTarget;
      fotaFile.value = '';
      fotaPortHint.textContent = '请选择文件，系统将自动检测端口并验证文件匹配';
      fotaPortHint.style.color = '#6b7280';
      fotaStartBtn.disabled = false;
      
      fotaBackdrop.style.display = 'flex';
      
      // 移除之前的事件监听器（如果存在），避免重复绑定
      const newFotaFile = fotaFile.cloneNode(true);
      fotaFile.parentNode.replaceChild(newFotaFile, fotaFile);
      
      // 监听文件选择，自动检测端口和验证文件匹配
      newFotaFile.addEventListener('change', async function() {
        // 每次文件选择时，重新获取按钮引用，确保状态正确
        const fotaStartBtn = document.getElementById('fotaStartBtn');
        const fotaPortHint = document.getElementById('fotaPortHint');
        
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
        fotaStartBtn.disabled = true;
        
        // 调用检测接口
        const result = await detectFotaPort(name, file.name);
        
        if (result.port > 0) {
          currentFota.port = result.port;
          fotaPortHint.textContent = `✓ 检测到端口: ${result.port} (${result.message})`;
          fotaPortHint.style.color = '#10b981';
          fotaStartBtn.disabled = false;
        } else {
          currentFota.port = 0;
          // 错误信息可能包含换行，需要格式化显示
          const errorMsg = result.message.replace(/\\n/g, '<br>');
          fotaPortHint.innerHTML = `✗ ${errorMsg}`;
          fotaPortHint.style.color = '#ef4444';
          fotaStartBtn.disabled = true;
        }
      });
    }

    function closeFota() {
      document.getElementById('fotaModal').style.display = 'none';
    }

    async function startFota() {
      const file = document.getElementById('fotaFile').files[0];
      if (!file) {
        alert('请先选择文件');
        return;
      }
      
      // 文件选择时已经检测过端口，如果端口为0说明检测失败，不允许升级
      if (currentFota.port === 0) {
        const fotaPortHint = document.getElementById('fotaPortHint');
        alert('文件验证失败，请重新选择正确的文件');
        fotaPortHint.style.color = '#ef4444';
        return;
      }

      const fd = new FormData();
      fd.append('file', file);
      fd.append('server_name', currentFota.name);
      fd.append('server_ip', currentFota.ip);
      fd.append('port', currentFota.port);

      const startTs = Date.now();
      const totalSize = file.size;
      const fotaStep = document.getElementById('fotaStep');
      const fotaProgress = document.getElementById('fotaProgress');
      fotaStep.textContent = '开始上传...';
      fotaProgress.textContent = '';

      fetch('/api/fota', {
        method: 'POST',
        body: fd
      }).then(res => {
        // 检查HTTP状态码
        if (!res.ok) {
          return res.json().then(data => {
            throw new Error(data.error || `HTTP ${res.status}: ${res.statusText}`);
          }).catch(() => {
            throw new Error(`HTTP ${res.status}: ${res.statusText}`);
          });
        }
        return res.json();
      }).then(data => {
        if (!data.ok || !data.task_id) {
          fotaStep.textContent = `失败: ${data.error || '未知错误'}`;
          return;
        }

        const taskId = data.task_id;
        let es = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 3;
        let reconnectTimer = null;
        
        function connectSSE() {
          if (es) {
            es.close();
          }
          
          es = new EventSource(`/api/fota/progress/${taskId}`);
          
          es.onmessage = (e) => {
            reconnectAttempts = 0; // 重置重连计数
            if (reconnectTimer) {
              clearTimeout(reconnectTimer);
              reconnectTimer = null;
            }
            
            const msg = JSON.parse(e.data);
            if (msg.error) {
              fotaStep.textContent = `失败: ${msg.error}`;
              es.close();
              return;
            }

            const percent = msg.progress || 0;
            fotaStep.textContent = msg.step || '处理中...';

            if (msg.status === 'checking') {
              fotaProgress.textContent = `检查文件... ${percent}%`;
            } else if (msg.status === 'md5') {
              fotaProgress.textContent = `MD5校验中... ${percent}%`;
            } else if (msg.status === 'uploading') {
              const elapsed = Math.max((Date.now() - startTs) / 1000, 0.001);
              // 上传进度从30%到80%，需要计算实际上传的百分比
              const uploadPercent = Math.max(0, Math.min(100, ((percent - 30) / 50) * 100));
              const loaded = Math.floor(totalSize * uploadPercent / 100);
              const speed = formatSpeed(loaded, elapsed);
              fotaProgress.textContent = `上传进度: ${uploadPercent.toFixed(0)}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
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

          es.onerror = (event) => {
            // EventSource 错误可能是网络问题或连接中断
            if (es.readyState === EventSource.CLOSED) {
              // 连接已关闭，尝试重连
              if (reconnectAttempts < maxReconnectAttempts) {
                reconnectAttempts++;
                fotaStep.textContent = `连接中断，正在重连... (${reconnectAttempts}/${maxReconnectAttempts})`;
                reconnectTimer = setTimeout(() => {
                  connectSSE();
                }, 2000 * reconnectAttempts); // 递增延迟
              } else {
                es.close();
                fotaStep.textContent = '进度监听失败：网络连接中断，请检查网络连接或刷新页面查看任务状态';
                fotaProgress.textContent = '提示：任务可能仍在后台执行，请稍后刷新页面查看结果';
              }
            } else if (es.readyState === EventSource.CONNECTING) {
              // 正在连接，不处理
            } else {
              // 其他错误
              fotaStep.textContent = '进度监听错误，请检查网络连接';
            }
          };
        }
        
        // 初始连接
        connectSSE();
      }).catch(err => {
        let errorMsg = '升级失败';
        if (err.message) {
          errorMsg += ': ' + err.message;
        } else {
          errorMsg += ': ' + err;
        }
        // 检查是否是网络错误
        if (err.message && (err.message.includes('network') || err.message.includes('Network') || err.message.includes('ERR_NETWORK'))) {
          errorMsg += ' (网络连接问题，请检查网络连接后重试)';
        }
        fotaStep.textContent = errorMsg;
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
          const serverKey = `${item.server_name}:${item.server_ip}`;
          // 保持选中状态
          const isSelected = selectedServers[serverKey] ? 'checked' : '';
          
          // 生成FOTA状态显示
          function getFotaStatusHtml(fotaStatus, port) {
            if (!fotaStatus) return '<span style="color: #6b7280;">空闲</span>';
            const statusMap = {
              'checking': { text: '检查文件', color: '#6366f1', icon: '🔎' },
              'md5': { text: 'MD5校验', color: '#f59e0b', icon: '🔍' },
              'uploading': { text: '上传中', color: '#3b82f6', icon: '⬆️' },
              'upgrading': { text: '升级中', color: '#8b5cf6', icon: '⚡' },
              'done': { text: '完成', color: '#10b981', icon: '✅' },
              'error': { text: '失败', color: '#ef4444', icon: '❌' },
              'cancelled': { text: '已取消', color: '#6b7280', icon: '🚫' }
            };
            const statusInfo = statusMap[fotaStatus.status] || { text: fotaStatus.status, color: '#6b7280', icon: '⏳' };
            const progress = fotaStatus.progress || 0;
            return `<span style="color: ${statusInfo.color}; font-weight: 600;" title="${fotaStatus.step || ''}">${statusInfo.icon} ${statusInfo.text} ${progress}%</span>`;
          }
          
          const status22 = getFotaStatusHtml(item.fota_status_22, 22);
          const status9999 = getFotaStatusHtml(item.fota_status_9999, 9999);
          const hasFota = item.fota_status_22 || item.fota_status_9999;
          const statusCell = hasFota 
            ? `<div style="font-size: 11px; line-height: 1.4;">
                 <div>22: ${status22}</div>
                 <div>9999: ${status9999}</div>
               </div>`
            : '<span style="color: #6b7280;">空闲</span>';
          
          tr.innerHTML = `
            <td class="col-checkbox"><input type="checkbox" class="server-checkbox" data-key="${serverKey.replace(/"/g, '&quot;')}" data-name="${item.server_name.replace(/"/g, '&quot;')}" data-ip="${item.server_ip.replace(/"/g, '&quot;')}" ${isSelected} onchange="updateSelectedServers()"></td>
            <td class="col-status">${statusCell}</td>
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
              <button class="btn" onclick="openDownload('${item.server_name}','${item.server_ip}',22)" style="margin-top: 4px;">下载22</button>
              <button class="btn" onclick="openDownload('${item.server_name}','${item.server_ip}',9999)" style="margin-top: 4px;">下载9999</button>
            </td>
            <td>
              <button class="btn" onclick="openFota('${item.server_name}','${item.server_ip}')" ${(item.fota_status_22 || item.fota_status_9999) ? 'disabled style="opacity: 0.5;"' : ''}>FOTA升级</button>
            </td>
            <td>
              <button class="btn" onclick="openTerminal('${item.server_name}','${item.server_ip}',22)" style="background: #8b5cf6; color: #fff; border: none;">22端口</button>
              <button class="btn" onclick="openTerminal('${item.server_name}','${item.server_ip}',9999)" style="background: #8b5cf6; color: #fff; border: none; margin-top: 4px;">9999端口</button>
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

    function toggleSelectAll() {
      const selectAll = document.getElementById('selectAll');
      const checkboxes = document.querySelectorAll('.server-checkbox');
      checkboxes.forEach(cb => {
        cb.checked = selectAll.checked;
        if (selectAll.checked) {
          const key = cb.getAttribute('data-key');
          const name = cb.getAttribute('data-name');
          const ip = cb.getAttribute('data-ip');
          selectedServers[key] = {name, ip};
        } else {
          selectedServers = {};
        }
      });
      updateSelectedCount();
    }

    function updateSelectedServers() {
      selectedServers = {};
      const checkboxes = document.querySelectorAll('.server-checkbox:checked');
      checkboxes.forEach(cb => {
        const key = cb.getAttribute('data-key');
        const name = cb.getAttribute('data-name');
        const ip = cb.getAttribute('data-ip');
        selectedServers[key] = {name, ip};
      });
      updateSelectedCount();
      // 更新全选状态
      const allCheckboxes = document.querySelectorAll('.server-checkbox');
      const selectAll = document.getElementById('selectAll');
      selectAll.checked = allCheckboxes.length > 0 && checkboxes.length === allCheckboxes.length;
    }

    function updateSelectedCount() {
      const count = Object.keys(selectedServers).length;
      document.getElementById('selectedCount').textContent = `已选择: ${count}`;
    }

    // Terminal相关变量 (Flask-SocketIO版本)
    let currentTerminal = null;
    let terminalSocket = null;
    let terminalFitAddon = null;
    let currentTerminalInfo = { name: '', ip: '', port: 22 };

    function openTerminal(name, ip, port) {
      currentTerminalInfo = { name, ip, port };
      const terminalModal = document.getElementById('terminalModal');
      const terminalContainer = document.getElementById('terminalContainer');
      const terminalServerInfo = document.getElementById('terminalServerInfo');
      const terminalStatus = document.getElementById('terminalStatus');
      
      terminalServerInfo.textContent = `${name} (${ip}:${port})`;
      terminalStatus.textContent = '正在连接...';
      terminalModal.style.display = 'flex';
      
      // 清空容器
      terminalContainer.innerHTML = '';
      
      // 初始化xterm
      if (currentTerminal) {
        currentTerminal.dispose();
      }
      
      currentTerminal = new Terminal({
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: {
          background: '#1e1e1e',
          foreground: '#d4d4d4',
          cursor: '#ffffff',
          black: '#000000',
          red: '#cd3131',
          green: '#0dbc79',
          yellow: '#e5e510',
          blue: '#2472c8',
          magenta: '#bc3fbc',
          cyan: '#11a8cd',
          white: '#e5e5e5'
        },
        cursorBlink: true,
        scrollback: 10000,
        convertEol: true,
        // 保持原有的右键行为，不强制改变
        rightClickSelectsWord: true,  // 保持默认行为，允许右键选中单词
      });
      
      // 加载插件（按正确顺序）
      terminalFitAddon = new FitAddon.FitAddon();
      const webLinksAddon = new WebLinksAddon.WebLinksAddon();
      
      // 先加载基础插件
      currentTerminal.loadAddon(terminalFitAddon);
      currentTerminal.loadAddon(webLinksAddon);
      
      // 打开终端
      currentTerminal.open(terminalContainer);
      terminalFitAddon.fit();
      
      // 初始化剪贴板扩展（支持选中即复制和右键粘贴）
      // 使用 @xterm/addon-clipboard 插件
      let clipboardAddon = null;
      try {
        // 检查 ClipboardAddon 是否可用（CDN 加载后可能是全局变量）
        // 可能的全局变量名：ClipboardAddon, xtermAddonClipboard, 或 window.ClipboardAddon
        if (typeof ClipboardAddon !== 'undefined') {
          // 如果 ClipboardAddon 是构造函数
          if (typeof ClipboardAddon === 'function') {
            clipboardAddon = new ClipboardAddon();
          } else if (ClipboardAddon.ClipboardAddon) {
            // 如果 ClipboardAddon 是命名空间对象
            clipboardAddon = new ClipboardAddon.ClipboardAddon();
          } else {
            console.warn('ClipboardAddon 格式不正确');
          }
        } else if (typeof xtermAddonClipboard !== 'undefined' && xtermAddonClipboard.ClipboardAddon) {
          clipboardAddon = new xtermAddonClipboard.ClipboardAddon();
        }
        
        if (clipboardAddon) {
          currentTerminal.loadAddon(clipboardAddon);
          console.log('ClipboardAddon 已加载');
        } else {
          console.warn('ClipboardAddon 不可用，将使用降级方案');
        }
      } catch (e) {
        console.warn('ClipboardAddon 加载失败，将使用降级方案:', e);
      }
      
      // 配置快捷键以避免与 SSH 控制键冲突
      // Ctrl+C 用于发送中断信号到 SSH，Ctrl+Shift+C 用于复制
      // Ctrl+V 用于粘贴，但需要避免在终端中直接粘贴（通过右键或 Ctrl+Shift+V）
      currentTerminal.attachCustomKeyEventHandler((event) => {
        // Ctrl+Shift+C: 复制选中文本
        if (event.ctrlKey && event.shiftKey && event.key === 'C') {
          if (currentTerminal.hasSelection()) {
            const selectedText = currentTerminal.getSelection();
            if (selectedText && selectedText.length > 0) {
              copyToClipboard(selectedText);
            }
          }
          return false; // 阻止默认行为
        }
        
        // Ctrl+Shift+V: 粘贴剪贴板内容
        if (event.ctrlKey && event.shiftKey && event.key === 'V') {
          pasteFromClipboard();
          return false; // 阻止默认行为
        }
        
        // Ctrl+C: 如果没有选中文本，发送中断信号到 SSH（放行）
        // 如果有选中文本，ClipboardAddon 会处理复制
        if (event.ctrlKey && event.key === 'c' && !currentTerminal.hasSelection()) {
          return true; // 放行到 SSH（发送中断信号）
        }
        
        // Ctrl+V: 默认情况下，如果 ClipboardAddon 可用，它会处理粘贴
        // 但我们希望禁用 Ctrl+V，强制使用 Ctrl+Shift+V 或右键粘贴
        if (event.ctrlKey && event.key === 'v') {
          // 如果 ClipboardAddon 可用，它会处理粘贴，但我们仍然阻止默认行为
          // 这样可以避免意外的粘贴操作
          return false; // 阻止默认行为，强制使用 Ctrl+Shift+V 或右键
        }
        
        // 其他按键正常处理
        return true;
      });
      
      // 实现选中即复制功能（包括所有字符）
      // 监听文本选择变化，当用户选中文本时自动复制到剪贴板
      currentTerminal.onSelectionChange(() => {
        if (currentTerminal.hasSelection()) {
          const selectedText = currentTerminal.getSelection();
          // 复制所有选中的字符，包括空格、特殊字符、中文、英文等
          if (selectedText && selectedText.length > 0) {
            copyToClipboard(selectedText);
          }
        }
      });
      
      // 实现右键粘贴功能
      // 监听终端容器的右键点击事件（仅在终端区域内）
      terminalContainer.addEventListener('contextmenu', (e) => {
        // 只在终端区域内右键时才粘贴
        if (e.target === terminalContainer || terminalContainer.contains(e.target)) {
          e.preventDefault(); // 阻止浏览器默认右键菜单
          pasteFromClipboard();
        }
      });
      
      // 辅助函数：复制到剪贴板
      function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).catch(err => {
            console.debug('复制到剪贴板失败:', err);
            // 降级方案
            fallbackCopyToClipboard(text);
          });
        } else {
          fallbackCopyToClipboard(text);
        }
      }
      
      // 降级复制方案
      function fallbackCopyToClipboard(text) {
        try {
          const textArea = document.createElement('textarea');
          textArea.value = text;
          textArea.style.position = 'fixed';
          textArea.style.top = '-9999px';
          textArea.style.left = '-9999px';
          textArea.style.opacity = '0';
          document.body.appendChild(textArea);
          textArea.select();
          textArea.setSelectionRange(0, text.length);
          const success = document.execCommand('copy');
          document.body.removeChild(textArea);
          if (!success) {
            console.debug('execCommand 复制失败');
          }
        } catch (err) {
          console.debug('降级复制方案失败:', err);
        }
      }
      
      // 辅助函数：从剪贴板粘贴
      function pasteFromClipboard() {
        if (navigator.clipboard && navigator.clipboard.readText) {
          navigator.clipboard.readText().then(text => {
            if (text && terminalSocket && terminalSocket.connected) {
              // 通过 SocketIO 发送粘贴的内容到 SSH
              // 一次性发送整个文本，SSH 服务器会正确处理
              terminalSocket.emit('terminal_input', {
                data: text
              });
            } else if (text && currentTerminal) {
              // 如果 SocketIO 未连接，直接写入终端（降级方案）
              currentTerminal.write(text);
            }
          }).catch(err => {
            console.debug('从剪贴板读取失败:', err);
          });
        } else {
          // 降级方案：使用 ClipboardAddon 的粘贴功能（如果可用）
          if (clipboardAddon) {
            // ClipboardAddon 会自动处理粘贴
            console.debug('使用 ClipboardAddon 的粘贴功能');
          } else {
            console.warn('剪贴板 API 不可用，请使用 Ctrl+Shift+V 或手动输入');
          }
        }
      }
      
      // 处理窗口大小变化
      const handleResize = () => {
        terminalFitAddon.fit();
        if (terminalSocket && terminalSocket.connected) {
          const dims = terminalFitAddon.proposeDimensions();
          if (dims) {
            terminalSocket.emit('terminal_resize', {
              cols: dims.cols,
              rows: dims.rows
            });
          }
        }
      };
      
      window.addEventListener('resize', handleResize);
      
      // 连接SocketIO
      connectTerminalSocketIO(name, ip, port);
      
      // 终端输入转发到SocketIO
      // 注意：这个事件处理器会接收所有输入，包括：
      // 1. 键盘输入（正常输入）
      // 2. ClipboardAddon 粘贴的内容（通过 onData 事件）
      // 3. 其他输入源
      // ClipboardAddon 不会干扰这个流程，它只是将粘贴的内容通过 onData 发送
      currentTerminal.onData(data => {
        if (terminalSocket && terminalSocket.connected) {
          terminalSocket.emit('terminal_input', {
            data: data
          });
        }
      });
      
      // 存储resize处理函数以便清理
      currentTerminal._resizeHandler = handleResize;
    }

    function connectTerminalSocketIO(name, ip, port) {
      // 如果已有连接，先断开
      if (terminalSocket) {
        terminalSocket.disconnect();
      }
      
      // 创建SocketIO连接，明确指定传输方式和配置
      // 注意：如果WebSocket失败，会自动降级到polling
      const socketioOptions = {
        // 优先尝试 WebSocket，失败再回退 polling
        transports: ['websocket', 'polling'],
        upgrade: true,
        rememberUpgrade: true,   // 若之前成功升级到 ws，下次直接尝试 ws
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        reconnectionAttempts: 5,
        timeout: 20000,
        forceNew: false,  // 复用连接
        autoConnect: true,
        // 确保与后端默认 Socket.IO 路径一致，避免 404
        path: '/socket.io'
      };
      
      try {
        // 新版统一使用 /terminal namespace（后端已注册）
        terminalSocket = io('/terminal', socketioOptions);
        console.log('SocketIO连接已创建:', terminalSocket);
      } catch (error) {
        console.error('SocketIO连接创建失败:', error);
        document.getElementById('terminalStatus').textContent = '连接失败: ' + error.message;
        document.getElementById('terminalStatus').style.color = '#ef4444';
        return;
      }
      
      terminalSocket.on('connect', () => {
        document.getElementById('terminalStatus').textContent = '已连接';
        document.getElementById('terminalStatus').style.color = '#10b981';
        const transport = terminalSocket.io.engine.transport.name;
        currentTerminal.writeln(`\\x1b[32m*** SocketIO连接已建立 (传输: ${transport}) ***\\x1b[0m\\r\\n`);
        
        // 发送初始尺寸
        let dims = null;
        if (terminalFitAddon) {
          dims = terminalFitAddon.proposeDimensions();
          if (dims) {
            terminalSocket.emit('terminal_resize', {
              cols: dims.cols,
              rows: dims.rows
            });
          }
        }
        
        // 启动SSH会话
        terminalSocket.emit('start_ssh', {
          server_name: name,
          server_ip: ip,
          port: port,
          cols: dims ? dims.cols : 80,
          rows: dims ? dims.rows : 24
        });
      });
      
      // 监听传输升级事件
      terminalSocket.io.on('upgrade', () => {
        const transport = terminalSocket.io.engine.transport.name;
        currentTerminal.writeln(`\\x1b[33m*** 传输已升级到: ${transport} ***\\x1b[0m\\r\\n`);
      });
      
      // 监听传输错误
      terminalSocket.io.on('upgradeError', (error) => {
        console.warn('传输升级失败，使用polling:', error);
        currentTerminal.writeln('\\x1b[33m*** WebSocket升级失败，使用polling传输 ***\\x1b[0m\\r\\n');
      });
      
      terminalSocket.on('connected', (data) => {
        console.log('前端收到connected事件:', data);
        if (currentTerminal && currentTerminal.write) {
          currentTerminal.writeln('\\x1b[32m*** SSH连接已建立 ***\\x1b[0m\\r\\n');
        } else {
          console.error('currentTerminal不存在或没有write方法');
        }
      });
      
      // 添加全局事件监听器用于调试（捕获所有事件）
      if (terminalSocket && typeof terminalSocket.onAny === 'function') {
        terminalSocket.onAny((eventName, ...args) => {
          console.log('前端收到SocketIO事件:', eventName, args);
        });
      }
      
      terminalSocket.on('output', (data) => {
        console.log('收到后端output事件:', data);
        // 终端输出
        if (data && data.data) {
          if (currentTerminal && currentTerminal.write) {
            currentTerminal.write(data.data);
          } else {
            console.error('currentTerminal不存在或没有write方法');
          }
        } else {
          console.warn('output事件数据为空:', data);
        }
      });
      
      terminalSocket.on('error', (data) => {
        
        currentTerminal.writeln(`\\x1b[31m*** 错误: ${data.message || '未知错误'} ***\\x1b[0m\\r\\n`);
        document.getElementById('terminalStatus').textContent = `错误: ${data.message || '未知错误'}`;
        document.getElementById('terminalStatus').style.color = '#ef4444';
      });
      
      terminalSocket.on('disconnected', () => {
        currentTerminal.writeln('\\x1b[33m*** SSH连接已断开 ***\\x1b[0m\\r\\n');
        document.getElementById('terminalStatus').textContent = 'SSH连接已断开';
        document.getElementById('terminalStatus').style.color = '#f59e0b';
      });
      
      terminalSocket.on('disconnect', () => {
        document.getElementById('terminalStatus').textContent = '连接已断开';
        document.getElementById('terminalStatus').style.color = '#6b7280';
        currentTerminal.writeln('\\x1b[33m*** SocketIO连接已关闭 ***\\x1b[0m\\r\\n');
      });
      
      terminalSocket.on('connect_error', (error) => {
        currentTerminal.writeln('\\x1b[31m*** 连接错误 ***\\x1b[0m\\r\\n');
        document.getElementById('terminalStatus').textContent = '连接错误';
        document.getElementById('terminalStatus').style.color = '#ef4444';
        console.error('SocketIO连接错误:', error);
      });
    }

    function terminalClear() {
      if (currentTerminal) {
        currentTerminal.clear();
      }
    }

    function closeTerminal() {
      // 关闭SocketIO连接
      if (terminalSocket) {
        terminalSocket.disconnect();
        terminalSocket = null;
      }
      
      // 移除resize监听器
      if (currentTerminal && currentTerminal._resizeHandler) {
        window.removeEventListener('resize', currentTerminal._resizeHandler);
      }
      
      // 清理终端
      if (currentTerminal) {
        currentTerminal.dispose();
        currentTerminal = null;
      }
      
      // 关闭弹窗
      document.getElementById('terminalModal').style.display = 'none';
    }

    let currentBatchId = null;
    let batchProgressInterval = null;
    let currentBatchUploadId = null;
    let batchUploadProgressInterval = null;

    function openBatchUpload() {
      const count = Object.keys(selectedServers).length;
      if (count === 0) {
        alert('请先选择至少一个服务器');
        return;
      }
      const batchUploadBackdrop = document.getElementById('batchUploadModal');
      const batchUploadServerList = document.getElementById('batchUploadServerList');
      const batchUploadServerCount = document.getElementById('batchUploadServerCount');
      const batchUploadTargetDir = document.getElementById('batchUploadTargetDir');
      const batchUploadFiles = document.getElementById('batchUploadFiles');
      const batchUploadProgress = document.getElementById('batchUploadProgress');
      const batchUploadTasks = document.getElementById('batchUploadTasks');
      const batchUploadStart22Btn = document.getElementById('batchUploadStart22Btn');
      const batchUploadStart9999Btn = document.getElementById('batchUploadStart9999Btn');
      const batchUploadCancelBtn = document.getElementById('batchUploadCancelBtn');

      // 重置状态
      currentBatchUploadId = null;
      if (batchUploadProgressInterval) {
        clearInterval(batchUploadProgressInterval);
        batchUploadProgressInterval = null;
      }

      batchUploadServerCount.textContent = count;
      batchUploadTargetDir.value = defaultTarget || '';
      batchUploadFiles.value = '';
      batchUploadProgress.textContent = '';
      batchUploadTasks.innerHTML = '';
      batchUploadStart22Btn.style.display = 'inline-block';
      batchUploadStart9999Btn.style.display = 'inline-block';
      batchUploadCancelBtn.style.display = 'none';

      let serverListHtml = '';
      for (const key in selectedServers) {
        const srv = selectedServers[key];
        serverListHtml += `<div>${srv.name} (${srv.ip})</div>`;
      }
      batchUploadServerList.innerHTML = serverListHtml;

      batchUploadBackdrop.style.display = 'flex';
    }

    function closeBatchUpload() {
      // 如果正在执行，先取消
      if (currentBatchUploadId) {
        cancelBatchUpload();
      }
      document.getElementById('batchUploadModal').style.display = 'none';
    }

    function cancelBatchUpload() {
      if (!currentBatchUploadId) {
        return;
      }

      const batchUploadCancelBtn = document.getElementById('batchUploadCancelBtn');
      const batchUploadProgress = document.getElementById('batchUploadProgress');
      
      batchUploadCancelBtn.disabled = true;
      batchUploadProgress.textContent = '正在终止任务...';

      fetch(`/api/batch-upload/cancel/${currentBatchUploadId}`, {
        method: 'POST'
      }).then(res => res.json()).then(data => {
        if (data.ok) {
          batchUploadProgress.textContent = '任务已终止: ' + (data.message || '');
          currentBatchUploadId = null;
          batchUploadCancelBtn.style.display = 'none';
          document.getElementById('batchUploadStart22Btn').style.display = 'inline-block';
          document.getElementById('batchUploadStart9999Btn').style.display = 'inline-block';
        } else {
          batchUploadProgress.textContent = '终止失败: ' + (data.error || '未知错误');
          batchUploadCancelBtn.disabled = false;
        }
      }).catch(err => {
        batchUploadProgress.textContent = '终止失败: ' + err;
        batchUploadCancelBtn.disabled = false;
      });
    }

    function startBatchUpload(port) {
      const files = document.getElementById('batchUploadFiles').files;
      if (!files || files.length === 0) {
        alert('请先选择至少一个文件');
        return;
      }

      const targetDir = document.getElementById('batchUploadTargetDir').value.trim();
      if (!targetDir) {
        alert('请填写目标目录');
        return;
      }

      const servers = [];
      for (const key in selectedServers) {
        servers.push(selectedServers[key]);
      }

      const fd = new FormData();
      for (let i = 0; i < files.length; i++) {
        fd.append('files', files[i]);
      }
      fd.append('port', port);
      fd.append('servers', JSON.stringify(servers));
      fd.append('target_dir', targetDir);

      const batchUploadProgress = document.getElementById('batchUploadProgress');
      const batchUploadTasks = document.getElementById('batchUploadTasks');
      const batchUploadStart22Btn = document.getElementById('batchUploadStart22Btn');
      const batchUploadStart9999Btn = document.getElementById('batchUploadStart9999Btn');
      const batchUploadCancelBtn = document.getElementById('batchUploadCancelBtn');

      batchUploadProgress.textContent = `开始批量上传 (${port}端口)...`;
      batchUploadTasks.innerHTML = '';
      batchUploadStart22Btn.style.display = 'none';
      batchUploadStart9999Btn.style.display = 'none';
      batchUploadCancelBtn.style.display = 'inline-block';
      batchUploadCancelBtn.disabled = false;

      fetch('/api/batch-upload', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.batch_id) {
          batchUploadProgress.textContent = `失败: ${data.error || '未知错误'}`;
          batchUploadStart22Btn.style.display = 'inline-block';
          batchUploadStart9999Btn.style.display = 'inline-block';
          return;
        }

        const batchId = data.batch_id;
        currentBatchUploadId = batchId;
        batchUploadProgress.textContent = `批量任务已启动，任务ID: ${batchId}`;

        // 初始化任务列表显示
        const taskMap = {};
        data.tasks.forEach(taskInfo => {
          const taskKey = `${taskInfo.server_name}:${taskInfo.server_ip}:${taskInfo.filename}`;
          const taskId = `upload-task-${taskKey.replace(/[:.]/g, '-')}`;
          taskMap[taskInfo.task_id] = taskId;
          
          const taskContainer = document.createElement('div');
          taskContainer.id = taskId;
          taskContainer.style.marginBottom = '8px';
          taskContainer.style.padding = '6px';
          taskContainer.style.border = '1px solid var(--border)';
          taskContainer.style.borderRadius = '4px';
          taskContainer.style.backgroundColor = '#f8fafc';
          
          const taskHeader = document.createElement('div');
          taskHeader.style.fontWeight = '600';
          taskHeader.style.fontSize = '11px';
          taskHeader.textContent = `${taskInfo.server_name} (${taskInfo.server_ip}) - ${taskInfo.filename}`;
          
          const taskProgress = document.createElement('div');
          taskProgress.id = `${taskId}-progress`;
          taskProgress.className = 'progress';
          taskProgress.style.fontSize = '11px';
          taskProgress.style.marginTop = '4px';
          taskProgress.textContent = '等待开始...';
          
          taskContainer.appendChild(taskHeader);
          taskContainer.appendChild(taskProgress);
          batchUploadTasks.appendChild(taskContainer);
        });

        // 使用EventSource监听进度
        const es = new EventSource(`/api/batch-upload/progress/${batchId}`);
        
        // **双重确认机制：监听明确的完成事件**
        let completionConfirmed = false;
        let completionCheckTimer = null;
        let lastProgressUpdate = Date.now();
        
        // 监听明确的complete事件类型
        es.addEventListener('complete', (e) => {
          const progressData = JSON.parse(e.data);
          console.log('收到完成事件:', progressData);
          handleCompletion(progressData);
        });
        
        // 处理完成逻辑
        function handleCompletion(progressData) {
          if (completionConfirmed) return; // 避免重复处理
          completionConfirmed = true;
          
          // 清除超时检查
          if (completionCheckTimer) {
            clearInterval(completionCheckTimer);
            completionCheckTimer = null;
          }
          
          es.close();
          let progressText = progressData.has_error ? 
            `批量上传完成（成功: ${progressData.success_count || 0}, 失败: ${progressData.error_count || 0}）` : 
            `批量上传完成（成功: ${progressData.success_count || 0}）`;
          
          // 显示错误汇总
          if (progressData.error_summary && progressData.error_summary.length > 0) {
            progressText += `\n失败详情: ${progressData.error_summary.map(e => `${e.server}/${e.filename}: ${e.error}`).join('; ')}`;
          }
          
          batchUploadProgress.textContent = progressText;
          batchUploadStart22Btn.style.display = 'inline-block';
          batchUploadStart9999Btn.style.display = 'inline-block';
          batchUploadCancelBtn.style.display = 'none';
          currentBatchUploadId = null;
        }
        
        // **超时检查机制：如果进度接近100%但没收到完成事件，主动查询**
        completionCheckTimer = setInterval(() => {
          const timeSinceLastUpdate = Date.now() - lastProgressUpdate;
          
          // 如果超过3秒没收到更新，且进度接近100%，主动查询状态
          if (timeSinceLastUpdate > 3000 && !completionConfirmed) {
            checkUploadCompletion();
          }
        }, 2000); // 每2秒检查一次
        
        // 主动查询上传完成状态（使用状态查询API）
        async function checkUploadCompletion() {
          try {
            const response = await fetch(`/api/batch-upload/status/${batchId}`);
            if (!response.ok) return;
            
            const data = await response.json();
            if (data.all_done || data.status === 'completed') {
              // 转换为与SSE消息相同的格式
              const progressData = {
                all_done: data.all_done,
                has_error: data.has_error,
                success_count: data.success_count,
                error_count: data.error_count,
                total_count: data.total_count,
                tasks: data.tasks,
                status: data.status,
                final: true
              };
              handleCompletion(progressData);
            }
          } catch (error) {
            console.error('状态检查失败:', error);
          }
        }
        
        es.onmessage = (e) => {
          lastProgressUpdate = Date.now(); // 更新最后收到消息的时间
          const progressData = JSON.parse(e.data);
          
          if (progressData.error) {
            batchUploadProgress.textContent = `错误: ${progressData.error}`;
            es.close();
            return;
          }

          // **双重确认：检查all_done或status === 'completed'**
          if (progressData.all_done || progressData.status === 'completed' || progressData.final) {
            handleCompletion(progressData);
            return;
          }

          // 更新各个任务的进度
          if (progressData.tasks) {
            let totalProgress = 0;
            let totalTasks = 0;
            let uploadingCount = 0;
            let doneCount = 0;
            let pendingCount = 0;
            
            for (const taskId in progressData.tasks) {
              const taskInfo = progressData.tasks[taskId];
              const displayTaskId = taskMap[taskId];
              if (displayTaskId) {
                const taskProgressDiv = document.getElementById(`${displayTaskId}-progress`);
                if (taskProgressDiv) {
                  const percent = taskInfo.progress || 0;
                  const status = taskInfo.status || 'pending';
                  const result = taskInfo.result;
                  
                  // **关键修复：特殊处理100%进度的情况**
                  // 如果进度是100%，无论状态是什么，都应该检查是否有result字段
                  // 这是因为multiprocessing.Manager.dict的状态更新可能有延迟
                  if (percent === 100) {
                    // 进度100%时，优先检查result字段
                    if (result) {
                      // 有result字段，说明任务已完成
                      if (result.ok) {
                        let doneText = `完成: ${result.path || '成功'}`;
                        if (result.exists) {
                          doneText += ' (文件已存在，已覆盖)';
                        }
                        taskProgressDiv.textContent = doneText;
                        taskProgressDiv.style.color = '#166534';
                        doneCount++;
                      } else {
                        taskProgressDiv.textContent = `失败: ${result.error || '未知错误'}`;
                        taskProgressDiv.style.color = '#991b1b';
                      }
                    } else {
                      // 进度100%但没有result，可能是状态更新延迟
                      // 显示"上传完成，等待确认..."，并触发一次状态查询
                      taskProgressDiv.textContent = `上传完成，等待确认...`;
                      taskProgressDiv.style.color = '#f59e0b';
                      uploadingCount++;
                      // **关键：如果进度100%但没有result，延迟1秒后主动查询一次该任务的状态**
                      setTimeout(async () => {
                        try {
                          const statusResponse = await fetch(`/api/batch-upload/status/${batchId}`);
                          if (statusResponse.ok) {
                            const statusData = await statusResponse.json();
                            const taskStatus = statusData.tasks && statusData.tasks[taskId];
                            if (taskStatus && taskStatus.result) {
                              // 如果查询到result，立即更新显示
                              const result = taskStatus.result;
                              if (result.ok) {
                                let doneText = `完成: ${result.path || '成功'}`;
                                if (result.exists) {
                                  doneText += ' (文件已存在，已覆盖)';
                                }
                                taskProgressDiv.textContent = doneText;
                                taskProgressDiv.style.color = '#166534';
                              } else {
                                taskProgressDiv.textContent = `失败: ${result.error || '未知错误'}`;
                                taskProgressDiv.style.color = '#991b1b';
                              }
                            }
                          }
                        } catch (err) {
                          console.error('查询任务状态失败:', err);
                        }
                      }, 1000); // 延迟1秒查询，给后端时间更新状态
                    }
                  } else if (status === 'uploading') {
                    taskProgressDiv.textContent = `上传中... ${percent}%`;
                    taskProgressDiv.style.color = '#075985';
                    uploadingCount++;
                  } else if (status === 'done') {
                    if (taskInfo.result && taskInfo.result.ok) {
                      let doneText = `完成: ${taskInfo.result.path || '成功'}`;
                      if (taskInfo.result.exists) {
                        doneText += ' (文件已存在，已覆盖)';
                      }
                      taskProgressDiv.textContent = doneText;
                      taskProgressDiv.style.color = '#166534';
                    } else {
                      taskProgressDiv.textContent = `失败: ${taskInfo.result?.error || '未知错误'}`;
                      taskProgressDiv.style.color = '#991b1b';
                    }
                    doneCount++;
                  } else if (status === 'error' || status === 'cancelled') {
                    taskProgressDiv.textContent = status === 'cancelled' ? '已取消' : `错误: ${taskInfo.result?.error || '未知错误'}`;
                    taskProgressDiv.style.color = '#991b1b';
                  } else {
                    taskProgressDiv.textContent = '等待中...';
                    taskProgressDiv.style.color = 'var(--muted)';
                    pendingCount++;
                  }
                  
                  totalProgress += percent;
                  totalTasks++;
                }
              }
            }
            
            
            if (totalTasks > 0) {
              const avgProgress = Math.floor(totalProgress / totalTasks);
              let progressText = `总体进度: ${avgProgress}% (成功: ${progressData.success_count || 0}, 失败: ${progressData.error_count || 0}, 总计: ${progressData.total_count || totalTasks})`;
              batchUploadProgress.textContent = progressText;
            }
          }
        };

        es.onerror = () => {
          // 连接错误时，如果还没确认完成，尝试主动查询
          if (!completionConfirmed) {
            console.warn('SSE连接错误，尝试主动查询状态');
            checkUploadCompletion();
          }
          
          // 如果查询后仍没确认，才关闭连接
          setTimeout(() => {
            if (!completionConfirmed) {
              es.close();
              if (completionCheckTimer) {
                clearInterval(completionCheckTimer);
              }
              if (currentBatchUploadId === batchId) {
                batchUploadProgress.textContent = '连接中断，请刷新页面查看状态';
                batchUploadStart22Btn.style.display = 'inline-block';
                batchUploadStart9999Btn.style.display = 'inline-block';
                batchUploadCancelBtn.style.display = 'none';
                currentBatchUploadId = null;
              }
            }
          }, 5000); // 给5秒时间查询状态
        };
      }).catch(err => {
        batchUploadProgress.textContent = `失败: ${err}`;
        batchUploadStart22Btn.style.display = 'inline-block';
        batchUploadStart9999Btn.style.display = 'inline-block';
      });
    }

    function openBatchFota() {
      const count = Object.keys(selectedServers).length;
      if (count === 0) {
        alert('请先选择至少一个服务器');
        return;
      }
      
      // **关键：在打开弹窗前先检测服务器类型是否一致**
      const servers = [];
      for (const key in selectedServers) {
        servers.push(selectedServers[key]);
      }
      
      const serverTypes = new Set();
      servers.forEach(srv => {
        if (srv.name.startsWith('LP-8650')) {
          serverTypes.add('LP-8650');
        } else if (srv.name.startsWith('LP-8797')) {
          serverTypes.add('LP-8797');
        }
      });
      
      // 如果混合了不同类型的服务器，显示错误提示弹窗
      if (serverTypes.size > 1) {
        const typesList = Array.from(serverTypes).join('、');
        const errorMessage = `批量FOTA不支持混合不同服务器类型。\n\n检测到服务器类型: ${typesList}\n\n请选择相同类型的服务器进行批量升级。`;
        showBatchFotaError(errorMessage);
        return;
      }
      
      // 服务器类型一致，正常打开批量FOTA弹窗
      const batchFotaBackdrop = document.getElementById('batchFotaModal');
      const batchServerList = document.getElementById('batchServerList');
      const batchServerCount = document.getElementById('batchServerCount');
      const batchFotaTargetText = document.getElementById('batchFotaTargetText');
      const batchFotaFile = document.getElementById('batchFotaFile');
      const batchFotaProgress = document.getElementById('batchFotaProgress');
      const batchFotaStep = document.getElementById('batchFotaStep');
      const batchFotaTasks = document.getElementById('batchFotaTasks');
      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');

      // 重置状态
      currentBatchId = null;
      if (batchProgressInterval) {
        clearInterval(batchProgressInterval);
        batchProgressInterval = null;
      }

      batchServerCount.textContent = count;
      batchFotaTargetText.textContent = fotaTarget;
      batchFotaFile.value = '';
      batchFotaProgress.textContent = '';
      batchFotaStep.textContent = '等待选择文件...';
      batchFotaTasks.innerHTML = '';
      batchFotaCancelBtn.style.display = 'none';
      const batchFotaStartBtn = document.getElementById('batchFotaStartBtn');
      const batchFotaPortHint = document.getElementById('batchFotaPortHint');
      batchFotaStartBtn.style.display = 'inline-block';
      batchFotaStartBtn.disabled = false;
      batchFotaPortHint.textContent = '请选择文件，系统将自动检测端口并验证文件匹配';
      batchFotaPortHint.style.color = '#6b7280';
      currentBatchFotaPort = 0;  // 重置端口

      let serverListHtml = '';
      for (const key in selectedServers) {
        const srv = selectedServers[key];
        serverListHtml += `<div>${srv.name} (${srv.ip})</div>`;
      }
      batchServerList.innerHTML = serverListHtml;

      // 移除之前的事件监听器（如果存在），避免重复绑定
      const newBatchFotaFile = batchFotaFile.cloneNode(true);
      batchFotaFile.parentNode.replaceChild(newBatchFotaFile, batchFotaFile);
      
      // 监听文件选择，自动检测端口和验证文件匹配
      newBatchFotaFile.addEventListener('change', async function() {
        const batchFotaPortHint = document.getElementById('batchFotaPortHint');
        const batchFotaStartBtn = document.getElementById('batchFotaStartBtn');
        
        const file = this.files[0];
        if (!file) {
          batchFotaPortHint.textContent = '请选择文件';
          batchFotaPortHint.style.color = '#6b7280';
          currentBatchFotaPort = 0;
          batchFotaStartBtn.disabled = false;
          return;
        }
        
        // 检查服务器类型是否一致（在openBatchFota中已经检查过，这里再次检查以防服务器选择发生变化）
        const servers = [];
        for (const key in selectedServers) {
          servers.push(selectedServers[key]);
        }
        
        if (servers.length === 0) {
          batchFotaPortHint.textContent = '请先选择至少一个服务器';
          batchFotaPortHint.style.color = '#ef4444';
          currentBatchFotaPort = 0;
          batchFotaStartBtn.disabled = true;
          return;
        }
        
        const serverTypes = new Set();
        servers.forEach(srv => {
          if (srv.name.startsWith('LP-8650')) {
            serverTypes.add('LP-8650');
          } else if (srv.name.startsWith('LP-8797')) {
            serverTypes.add('LP-8797');
          }
        });
        
        if (serverTypes.size > 1) {
          // 如果检测到混合类型，关闭批量FOTA弹窗，显示错误提示
          document.getElementById('batchFotaModal').style.display = 'none';
          const typesList = Array.from(serverTypes).join('、');
          const errorMessage = `批量FOTA不支持混合不同服务器类型。\n\n检测到服务器类型: ${typesList}\n\n请选择相同类型的服务器进行批量升级。`;
          showBatchFotaError(errorMessage);
          return;
        }
        
        // 显示检测中
        batchFotaPortHint.textContent = '正在检测端口和验证文件匹配...';
        batchFotaPortHint.style.color = '#3b82f6';
        batchFotaStartBtn.disabled = true;
        
        // 调用检测接口
        const result = await detectFotaPort(servers[0].name, file.name);
        
        if (result.port > 0) {
          currentBatchFotaPort = result.port;
          const serverType = Array.from(serverTypes)[0] || '未知';
          batchFotaPortHint.textContent = `✓ 检测到端口: ${result.port}，服务器类型: ${serverType} (${result.message})`;
          batchFotaPortHint.style.color = '#10b981';
          batchFotaStartBtn.disabled = false;
        } else {
          currentBatchFotaPort = 0;
          // 错误信息可能包含换行，需要格式化显示
          const errorMsg = result.message.replace(/\\n/g, '<br>');
          batchFotaPortHint.innerHTML = `✗ ${errorMsg}`;
          batchFotaPortHint.style.color = '#ef4444';
          batchFotaStartBtn.disabled = true;
        }
      });

      batchFotaBackdrop.style.display = 'flex';
    }

    function showBatchFotaError(message) {
      const errorModal = document.getElementById('batchFotaErrorModal');
      const errorMessageDiv = document.getElementById('batchFotaErrorMessage');
      // 将换行符转换为HTML换行
      errorMessageDiv.innerHTML = message.replace(/\\n/g, '<br>');
      errorModal.style.display = 'flex';
    }

    function closeBatchFotaError() {
      document.getElementById('batchFotaErrorModal').style.display = 'none';
    }

    function closeBatchFota() {
      // 如果正在执行，先取消
      if (currentBatchId && batchProgressInterval) {
        cancelBatchFota();
      }
      document.getElementById('batchFotaModal').style.display = 'none';
    }

    function cancelBatchFota() {
      if (!currentBatchId) {
        return;
      }

      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');
      const batchFotaStep = document.getElementById('batchFotaStep');
      
      batchFotaCancelBtn.disabled = true;
      batchFotaStep.textContent = '正在终止任务...';

      fetch(`/api/batch-fota/cancel/${currentBatchId}`, {
        method: 'POST'
      }).then(res => res.json()).then(data => {
        if (data.ok) {
          batchFotaStep.textContent = '任务已终止: ' + (data.message || '');
          if (batchProgressInterval) {
            clearInterval(batchProgressInterval);
            batchProgressInterval = null;
          }
          currentBatchId = null;
          batchFotaCancelBtn.style.display = 'none';
        } else {
          batchFotaStep.textContent = '终止失败: ' + (data.error || '未知错误');
          batchFotaCancelBtn.disabled = false;
        }
      }).catch(err => {
        batchFotaStep.textContent = '终止失败: ' + err;
        batchFotaCancelBtn.disabled = false;
      });
    }

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

      // 文件选择时已经检测过端口，如果端口为0说明检测失败，不允许升级
      if (currentBatchFotaPort === 0) {
        const batchFotaPortHint = document.getElementById('batchFotaPortHint');
        alert('文件验证失败，请重新选择正确的文件');
        batchFotaPortHint.style.color = '#ef4444';
        return;
      }

      const batchFotaProgress = document.getElementById('batchFotaProgress');
      const batchFotaStep = document.getElementById('batchFotaStep');
      const batchFotaTasks = document.getElementById('batchFotaTasks');
      const batchFotaTaskDetails = document.getElementById('batchFotaTaskDetails');
      const batchFotaCancelBtn = document.getElementById('batchFotaCancelBtn');
      const batchFotaStartBtn = document.getElementById('batchFotaStartBtn');

      const fd = new FormData();
      fd.append('file', file);
      fd.append('port', currentBatchFotaPort);  // 使用检测到的端口
      fd.append('servers', JSON.stringify(servers));

      batchFotaStep.textContent = `开始批量FOTA (${currentBatchFotaPort}端口)...`;
      batchFotaProgress.textContent = '';
      batchFotaTasks.innerHTML = '';
      batchFotaTaskDetails.innerHTML = '';
      batchFotaTaskDetails.style.display = 'block';
      batchFotaCancelBtn.style.display = 'inline-block';
      batchFotaStartBtn.style.display = 'none';

      const totalSize = file.size;
      const taskStartTimes = {}; // {taskKey: startTime}

      fetch('/api/batch-fota', {
        method: 'POST',
        body: fd
      }).then(res => res.json()).then(data => {
        if (!data.ok || !data.batch_id) {
          batchFotaStep.textContent = `失败: ${data.error || '未知错误'}`;
          batchFotaCancelBtn.style.display = 'none';
          batchFotaStartBtn.style.display = 'inline-block';
          return;
        }

        const batchId = data.batch_id;
        currentBatchId = batchId;
        batchFotaStep.textContent = `批量任务已启动，任务ID: ${batchId}`;

        // 初始化任务列表显示 - 每个任务显示详细进度，参考单个FOTA
        servers.forEach(srv => {
          const taskKey = `${srv.name}:${srv.ip}:${currentBatchFotaPort}`;
          const taskId = `task-${taskKey.replace(/:/g, '-')}`;
          taskStartTimes[taskKey] = Date.now();
          
          const taskContainer = document.createElement('div');
          taskContainer.id = taskId;
          taskContainer.style.marginBottom = '12px';
          taskContainer.style.padding = '8px';
          taskContainer.style.border = '1px solid var(--border)';
          taskContainer.style.borderRadius = '6px';
          taskContainer.style.backgroundColor = '#f8fafc';
          
          const taskHeader = document.createElement('div');
          taskHeader.style.fontWeight = '600';
          taskHeader.style.marginBottom = '4px';
          taskHeader.textContent = `${srv.name} (${srv.ip})`;
          
          const taskStep = document.createElement('div');
          taskStep.id = `${taskId}-step`;
          taskStep.className = 'progress';
          taskStep.style.marginTop = '4px';
          taskStep.style.fontSize = '12px';
          taskStep.textContent = '等待开始...';
          
          const taskProgress = document.createElement('div');
          taskProgress.id = `${taskId}-progress`;
          taskProgress.className = 'progress';
          taskProgress.style.fontSize = '12px';
          taskProgress.style.color = 'var(--muted)';
          taskProgress.textContent = '';
          
          taskContainer.appendChild(taskHeader);
          taskContainer.appendChild(taskStep);
          taskContainer.appendChild(taskProgress);
          batchFotaTasks.appendChild(taskContainer);
        });

        // 轮询批量任务进度
        batchProgressInterval = setInterval(() => {
          if (!currentBatchId || currentBatchId !== batchId) {
            clearInterval(batchProgressInterval);
            batchProgressInterval = null;
            return;
          }

          fetch(`/api/batch-fota/progress/${batchId}`)
            .then(res => res.json())
            .then(progressData => {
              if (progressData.error) {
                batchFotaStep.textContent = `错误: ${progressData.error}`;
                clearInterval(progressInterval);
                return;
              }

              const total = progressData.total || servers.length;
              const completed = progressData.completed || 0;
              const status = progressData.status || 'running';

              batchFotaProgress.textContent = `总体进度: ${completed}/${total} (${Math.floor(completed * 100 / total)}%)`;

              // 更新各个任务的详细进度，完全参考单个FOTA的显示方式
              if (progressData.tasks) {
                progressData.tasks.forEach(taskInfo => {
                  const taskKey = taskInfo.server_key || '';
                  const taskId = `task-${taskKey.replace(/:/g, '-')}`;
                  const taskStepDiv = document.getElementById(`${taskId}-step`);
                  const taskProgressDiv = document.getElementById(`${taskId}-progress`);
                  
                  if (taskStepDiv && taskProgressDiv) {
                    const percent = taskInfo.progress || 0;
                    const taskStatus = taskInfo.status || 'unknown';
                    const step = taskInfo.step || '';
                    
                    // 更新步骤信息
                    taskStepDiv.textContent = step || '处理中...';
                    
                    // 根据状态显示详细进度，完全参考单个FOTA
                    if (taskStatus === 'uploading') {
                      const startTime = taskStartTimes[taskKey] || Date.now();
                      const elapsed = Math.max((Date.now() - startTime) / 1000, 0.001);
                      const loaded = Math.floor(totalSize * percent / 100);
                      const speed = formatSpeed(loaded, elapsed);
                      taskProgressDiv.textContent = `上传进度: ${percent}% (${formatBytes(loaded)}/${formatBytes(totalSize)}, ${speed})`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'md5') {
                      taskProgressDiv.textContent = `MD5校验中... ${percent}%`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'upgrading') {
                      taskProgressDiv.textContent = `升级执行中... ${percent}%`;
                      taskProgressDiv.style.color = '#075985';
                    } else if (taskStatus === 'done') {
                      if (taskInfo.result && taskInfo.result.ok) {
                        taskStepDiv.textContent = '完成：升级成功';
                        taskStepDiv.style.color = '#166534';
                        if (taskInfo.local_md5 && taskInfo.remote_md5) {
                          taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                        }
                        taskProgressDiv.style.color = '#166534';
                      } else {
                        taskStepDiv.textContent = `失败: ${taskInfo.error || '未知错误'}`;
                        taskStepDiv.style.color = '#991b1b';
                        if (taskInfo.local_md5 && taskInfo.remote_md5) {
                          taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                        }
                        taskProgressDiv.style.color = '#991b1b';
                      }
                    } else if (taskStatus === 'error' || taskStatus === 'cancelled') {
                      taskStepDiv.textContent = taskStatus === 'cancelled' ? '已取消' : `失败: ${taskInfo.error || '未知错误'}`;
                      taskStepDiv.style.color = '#991b1b';
                      if (taskInfo.local_md5 && taskInfo.remote_md5) {
                        taskProgressDiv.textContent = `本地MD5: ${taskInfo.local_md5} | 远端MD5: ${taskInfo.remote_md5}`;
                      }
                      taskProgressDiv.style.color = '#991b1b';
                    } else {
                      taskProgressDiv.textContent = '';
                    }
                  }
                });
              }

              if (status === 'done' || status === 'error' || status === 'cancelled') {
                clearInterval(batchProgressInterval);
                batchProgressInterval = null;
                batchFotaCancelBtn.style.display = 'none';
                if (status === 'cancelled') {
                  batchFotaStep.textContent = '批量FOTA已取消';
                } else {
                  batchFotaStep.textContent = status === 'done' ? '批量FOTA完成' : '批量FOTA失败';
                }
              }
            })
            .catch(err => {
              batchFotaStep.textContent = `进度查询失败: ${err}`;
              clearInterval(batchProgressInterval);
              batchProgressInterval = null;
              batchFotaCancelBtn.style.display = 'none';
            });
        }, 200); // 改为200ms更新一次，与单个FOTA的SSE更新频率接近
      }).catch(err => {
        batchFotaStep.textContent = `批量FOTA失败: ${err}`;
      });
    }

    // 性能优化：根据标签页可见性调整刷新频率
    let refreshInterval = 5000; // 默认5秒
    let isPageVisible = true;
    let refreshTimer = null;
    
    // 监听页面可见性变化
    document.addEventListener('visibilitychange', () => {
      isPageVisible = !document.hidden;
      if (isPageVisible) {
        // 页面可见时立即刷新一次，然后恢复正常频率
        loadStatus();
        refreshInterval = 5000;
      } else {
        // 页面不可见时降低刷新频率到30秒
        refreshInterval = 30000;
      }
      // 重新启动定时器
      if (refreshTimer) {
        clearTimeout(refreshTimer);
      }
      scheduleNextRefresh();
    });
    
    // 使用动态间隔刷新
    function scheduleNextRefresh() {
      if (refreshTimer) {
        clearTimeout(refreshTimer);
      }
      refreshTimer = setTimeout(() => {
        if (isPageVisible) {
          loadStatus();
        }
        scheduleNextRefresh();
      }, refreshInterval);
    }
    
    // 初始加载
    loadStatus();
    scheduleNextRefresh();