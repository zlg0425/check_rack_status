#!/usr/bin/env python3
"""
简单测试脚本：演示远程执行命令并收集完整输出。
用法示例：
  python remote_exec_test.py --ip 10.0.0.1 --port 22 --name test --cmd "uname -a"
"""

import argparse

from check_rack_status import (
    load_config,
    remote_exec_collect,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True, help="目标IP")
    parser.add_argument("--port", type=int, default=22, help="端口，默认22")
    parser.add_argument("--name", default="manual", help="服务器名称标识")
    parser.add_argument("--cmd", default="echo hello && uname -a", help="要执行的命令")
    args = parser.parse_args()

    load_config()

    print(f"在 {args.name}({args.ip}:{args.port}) 执行: {args.cmd}")
    code, out, err = remote_exec_collect(args.name, args.ip, args.port, args.cmd)
    print(f"exit={code}")
    print("stdout:")
    print(out)
    print("stderr:")
    print(err)


if __name__ == "__main__":
    main()
