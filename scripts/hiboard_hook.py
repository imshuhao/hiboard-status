#!/usr/bin/env python3
"""hiboard-status 薄入口：把 Claude Code 会话状态推送到华为负一屏。

全部逻辑在 scripts/hiboard/ 包内；本文件只负责 argv 分发与两条铁律：
hook 路径恒零退出、HIBOARD_SUMMARIZING 递归守卫在读 stdin 前生效。
设计: docs/superpowers/specs/（2026-07-26/27 系列）
API 契约: docs/hiboard-api-spec.md（字段显示映射反直觉，改动前必读）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hiboard import (cmd_push, cmd_status, cmd_test_push, handle_event,  # noqa: E402
                     log, run_flush, run_summarize)


def main(argv) -> int:
    mode = argv[1] if len(argv) >= 2 else ""
    if mode in ("--push", "--test-push"):
        # Claude/用户主动调用的 CLI 模式：失败必须非零，让调用方察觉
        try:
            if mode == "--push":
                return cmd_push(argv[2] if len(argv) > 2 else "")
            return cmd_test_push()
        except Exception as e:
            print(f"推送异常: {type(e).__name__}: {e}")
            return 1
    if mode == "--status":
        try:
            return cmd_status()
        except Exception as e:
            print(f"状态读取异常: {type(e).__name__}: {e}")
            return 0
    try:
        if mode == "--summarize":
            run_summarize(argv[2], argv[3] if len(argv) > 3 else "",
                          float(argv[4]) if len(argv) > 4 else 0.0)
        elif mode == "--flush":
            run_flush(float(argv[2]) if len(argv) > 2 else 0.0)
        else:
            if os.environ.get("HIBOARD_SUMMARIZING"):
                return 0  # 摘要无头会话触发的 hooks：直接忽略，防无限递归
            evt = json.load(sys.stdin)
            handle_event(evt)
    except Exception as e:
        log(f"ERROR {type(e).__name__}: {e}")
    return 0  # 恒 0：hook 旁路绝不打断 Claude Code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
