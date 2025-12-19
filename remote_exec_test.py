#!/usr/bin/env python3
"""
简单测试脚本：演示远程执行命令并收集完整输出。
用法示例：
  python remote_exec_test.py --ip 10.0.0.1 --port 22 --name test --cmd "uname -a"
  
注意：8650系列服务器会自动使用私钥登录（根据config.json中的group_auth_mode配置）
"""

import argparse
import os
import check_rack_status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True, help="目标IP")
    parser.add_argument("--port", type=int, default=22, help="端口，默认22")
    parser.add_argument("--name", default="manual", help="服务器名称标识")
    parser.add_argument("--cmd", default="echo hello && uname -a", help="要执行的命令")
    args = parser.parse_args()

    # 加载配置
    check_rack_status.load_config()
    
    # 对于8650系列服务器，验证私钥配置
    if args.name.startswith("LP-8650"):
        auth_mode = check_rack_status.resolve_auth_mode(args.name)
        key_path = check_rack_status.resolve_key(args.name, args.port)
        
        print(f"服务器: {args.name}")
        print(f"认证方式: {auth_mode}")
        print(f"私钥路径: {key_path}")
        
        if auth_mode != "key":
            print(f"警告: 8650服务器应使用私钥登录，但当前配置为: {auth_mode}")
        
        if not key_path:
            print(f"错误: 未找到端口 {args.port} 的私钥配置")
            return
        
        # 检查私钥文件是否存在
        if not os.path.exists(key_path):
            print(f"错误: 私钥文件不存在: {key_path}")
            print(f"提示: 请确保私钥文件在当前目录下")
            return
        
        print(f"私钥文件存在: {key_path}")
        print(f"SSH用户名: {check_rack_status.ssh_username}")
        print("-" * 50)

    print(f"在 {args.name}({args.ip}:{args.port}) 执行: {args.cmd}")
    
    # 如果是lpUCM命令，先检查文件是否存在（H1假设），并测试命令构建
    if "./lpUCM" in args.cmd or "lpUCM" in args.cmd:
        import re
        # 提取文件路径
        file_match = re.search(r'-i\s+([^\s\'"]+)', args.cmd)
        if file_match:
            ucm_file = file_match.group(1)
            print(f"\n检查文件是否存在: {ucm_file}")
            check_cmd = f"test -f {ucm_file} && echo 'FILE_EXISTS' || echo 'FILE_NOT_FOUND'"
            check_code, check_out, check_err = check_rack_status.remote_exec_collect(args.name, args.ip, args.port, check_cmd)
            print(f"文件检查结果: exit={check_code}, output={check_out}")
            if "FILE_NOT_FOUND" in check_out:
                print(f"⚠️  警告: 文件不存在: {ucm_file}")
            elif "FILE_EXISTS" in check_out:
                print(f"✅ 文件存在: {ucm_file}")
            
            # 测试命令是否正确构建和执行
            print(f"\n测试命令构建和执行:")
            print(f"原始命令: {args.cmd}")
            # 测试简单的echo命令来验证shell执行
            test_cmd = "sh -c 'echo TEST_VAR: LD_LIBRARY_PATH=/opt/usr/lib64:/opt/usr/lib:$LD_LIBRARY_PATH && cd /opt/usr/bin && pwd'"
            test_code, test_out, test_err = check_rack_status.remote_exec_collect(args.name, args.ip, args.port, test_cmd)
            print(f"测试命令结果: exit={test_code}")
            print(f"测试输出: {test_out}")
            if test_err:
                print(f"测试错误: {test_err}")
    
    code, out, err = check_rack_status.remote_exec_collect(args.name, args.ip, args.port, args.cmd)
    print(f"\nexit={code}")
    print("stdout:")
    print(out)
    print("stderr:")
    print(err)


if __name__ == "__main__":
    main()
