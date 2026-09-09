# -*- coding: utf-8 -*-
"""S4 真机 E2E 用的哑策略：不依赖 xtquant / miniQMT。

启动后轮询确认全量 QMT 客户端进程（XtItClient.exe）存活，然后把心跳结果写入
`--out` 文件并退出。用于验证「BigQMT READY -> 自动拉起策略 -> 监督」机制本身。
"""

import argparse
import datetime
import os
import subprocess
import sys
import time


def process_running(name):
    """简单进程检测：tasklist 精确匹配（纯标准库，跨解释器可用）。"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return name.lower() in (result.stdout or "").lower()
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="心跳结果文件")
    parser.add_argument("--timeout", type=float, default=30.0, help="等待 QMT 进程超时")
    args = parser.parse_args()

    t0 = time.time()
    ok = False
    while time.time() - t0 < args.timeout:
        if process_running("XtItClient.exe"):
            ok = True
            break
        time.sleep(0.5)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(f"qmt_process_ok={ok}\n")
        fh.write(f"ts={datetime.datetime.now().isoformat()}\n")
        fh.write(f"pid={os.getpid()}\n")
        fh.write(f"wait={time.time() - t0:.1f}\n")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
