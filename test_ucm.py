#!/usr/bin/env python3
"""
简单测试：调用 run_ucm_with_log。
使用前请在 config.json 配好服务器和认证。
用法示例：
  python test_ucm.py --server LP-8797-1 --ip 10.99.19.1 --port 22 --file /opt/data/fota/xxx.icsw
"""

import argparse

from check_rack_status import load_config, run_ucm_with_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, help="服务器名称标识（与 config 中前缀匹配）")
    parser.add_argument("--ip", required=True, help="目标IP")
    parser.add_argument("--port", type=int, default=22, help="端口，默认22")
    parser.add_argument("--file", required=True, help="待升级文件的远端绝对路径")
    parser.add_argument("--log", default="ucm_test.log", help="本地日志文件，默认 ucm_test.log")
    parser.add_argument("--tail-wait", type=int, default=2, help="A结束后继续收集日志秒数")
    args = parser.parse_args()

    load_config()

    ok, info = run_ucm_with_log(
        args.server,
        args.ip,
        args.port,
        args.file,
        log_file=args.log,
        tail_wait=args.tail_wait,
    )

    if ok:
        print("升级触发成功")
        if info:
            print("输出：", info)
    else:
        print("升级失败：", info)


if __name__ == "__main__":
    main()

