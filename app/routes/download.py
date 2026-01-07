"""
下载相关路由模块
"""
from flask import Blueprint, jsonify, request, Response
import os
import io
import zipfile
import posixpath
import urllib.parse

from core.ssh.transport import create_transport
import paramiko

bp = Blueprint('download', __name__, url_prefix='/api')


@bp.route("/download/stream")
def api_download_stream():
    """流式下载文件或文件夹到浏览器（直接另存为）"""
    try:
        server_name = request.args.get("server_name", "").strip()
        server_ip = request.args.get("server_ip", "").strip()
        port = int(request.args.get("port", 0))
        remote_path = request.args.get("remote_path", "").strip()
        
        if not server_name or not server_ip or port not in (22, 9999):
            return jsonify({"ok": False, "error": "参数缺失或端口非法"}), 400
        if not remote_path:
            return jsonify({"ok": False, "error": "请填写远程文件路径"}), 400
        
        # 建立SFTP连接
        transport = create_transport(server_name, server_ip, port)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        try:
            # 检查是文件还是文件夹
            file_stat = sftp.stat(remote_path)
            is_directory = file_stat.st_mode & 0o040000
            filename = os.path.basename(remote_path) or "download"
            
            if is_directory:
                # 文件夹：打包成ZIP流式传输
                def generate_zip():
                    try:
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                            def add_directory_recursive(sftp, remote_dir, zip_path=""):
                                """递归添加目录到ZIP"""
                                try:
                                    items = sftp.listdir_attr(remote_dir)
                                    for item in items:
                                        remote_item_path = posixpath.join(remote_dir, item.filename)
                                        zip_item_path = posixpath.join(zip_path, item.filename) if zip_path else item.filename
                                        
                                        # 跳过符号链接
                                        if item.st_mode & 0o120000:
                                            try:
                                                sftp.readlink(remote_item_path)
                                                continue  # 真正的符号链接，跳过
                                            except:
                                                pass  # 可能是误判，继续处理
                                        
                                        if item.st_mode & 0o040000:  # 目录
                                            add_directory_recursive(sftp, remote_item_path, zip_item_path)
                                        elif item.st_mode & 0o100000:  # 文件
                                            try:
                                                with sftp.open(remote_item_path, 'rb') as remote_file:
                                                    zip_file.writestr(zip_item_path, remote_file.read())
                                            except Exception as e:
                                                # 文件读取失败，跳过
                                                continue
                                except Exception as e:
                                    # 目录访问失败，跳过
                                    pass
                            
                            add_directory_recursive(sftp, remote_path)
                        
                        zip_buffer.seek(0)
                        chunk_count = 0
                        while True:
                            chunk = zip_buffer.read(8192)  # 8KB chunks
                            if not chunk:
                                break
                            chunk_count += 1
                            if chunk_count % 100 == 0:  # 每100个chunk记录一次（保留用于性能监控）
                                pass
                            yield chunk
                    finally:
                        sftp.close()
                        transport.close()
                
                zip_filename = f"{filename}.zip"
                # 编码文件名以支持中文字符（RFC 5987）
                # filename参数：如果包含非ASCII字符，使用URL编码的ASCII版本
                safe_filename = zip_filename.encode('ascii', 'ignore').decode('ascii') or 'download.zip'
                if safe_filename != zip_filename:
                    # 如果文件名包含非ASCII字符，使用URL编码
                    safe_filename = urllib.parse.quote(zip_filename)
                # filename*参数：使用UTF-8编码（RFC 5987标准）
                encoded_filename = urllib.parse.quote(zip_filename.encode('utf-8'))
                # 使用filename*参数支持UTF-8编码，同时保留filename作为fallback
                content_disposition = f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                return Response(
                    generate_zip(),
                    mimetype='application/zip',
                    headers={
                        'Content-Disposition': content_disposition,
                        'Content-Type': 'application/zip'
                    }
                )
            else:
                # 文件：直接流式传输
                def generate_file():
                    try:
                        remote_file = sftp.open(remote_path, 'rb')
                        while True:
                            chunk = remote_file.read(8192)  # 8KB chunks
                            if not chunk:
                                break
                            yield chunk
                        remote_file.close()
                    finally:
                        sftp.close()
                        transport.close()
                
                # 编码文件名以支持中文字符（RFC 5987）
                # filename参数：如果包含非ASCII字符，使用URL编码的ASCII版本
                safe_filename = filename.encode('ascii', 'ignore').decode('ascii') or 'download'
                if safe_filename != filename:
                    # 如果文件名包含非ASCII字符，使用URL编码
                    safe_filename = urllib.parse.quote(filename)
                # filename*参数：使用UTF-8编码（RFC 5987标准）
                encoded_filename = urllib.parse.quote(filename.encode('utf-8'))
                # 使用filename*参数支持UTF-8编码，同时保留filename作为fallback
                content_disposition = f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                return Response(
                    generate_file(),
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Disposition': content_disposition,
                        'Content-Type': 'application/octet-stream'
                    }
                )
        except IOError as e:
            sftp.close()
            transport.close()
            return jsonify({"ok": False, "error": f"远程路径不存在或无法访问: {str(e)}"}), 400
        except Exception as e:
            if sftp:
                sftp.close()
            if transport:
                transport.close()
            return jsonify({"ok": False, "error": f"服务器异常: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"服务器异常: {str(e)}"}), 500
