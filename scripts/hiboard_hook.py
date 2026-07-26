#!/usr/bin/env python3
"""hiboard-status: 把 Claude Code 会话状态推送到华为负一屏。

所有 hook 事件共用本入口，事件名从 stdin JSON 的 hook_event_name 读取。
设计: docs/superpowers/specs/2026-07-26-hiboard-status-plugin-design.md
API 契约: docs/hiboard-api-spec.md（字段显示映射反直觉，改动前必读）
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_ENDPOINT = ("https://hiboard-claw-drcn.ai.dbankcloud.cn"
                    "/distribution/message/cloud/claw/msg/upload")
CARD_ID = "claude_code_status"
MAX_CARD_UTF16 = 28000
MAX_PROJECT_UTF16 = 3000
MAX_PROMPT_UTF16 = 60
STALE_SECS = 2 * 3600
PRUNE_SECS = 7 * 24 * 3600

STATUS_META = {
    "running": ("🟢", "运行中"),
    "waiting": ("🟡", "等待输入"),
    "done":    ("✅", "本轮完成"),
    "ended":   ("⚪", "已结束"),
    "stale":   ("⚪", "状态未知"),
}


# ---------------------------------------------------------------- 纯函数

def utf16_len(s: str) -> int:
    """按服务端语义计长：UTF-16 码元数，emoji 计 2。"""
    return len(s.encode("utf-16-le")) // 2


def truncate_utf16(s: str, limit: int) -> str:
    """截断到 limit 个 UTF-16 码元以内，超长加 …（… 本身占 1 码元）。"""
    if utf16_len(s) <= limit:
        return s
    cut = s.encode("utf-16-le")[: max(limit - 1, 0) * 2]
    # 若切口落在代理对中间，decode(ignore) 会丢弃孤立代理项
    return cut.decode("utf-16-le", "ignore").rstrip() + "…"


def fmt_time(ts: float, now=None) -> str:
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%m-%d %H:%M") if now - ts >= 86400 else dt.strftime("%H:%M")


def effective_status(entry: dict, now=None) -> str:
    now = time.time() if now is None else now
    st = entry.get("status", "ended")
    if st in ("running", "waiting") and now - entry.get("updated_at", 0) > STALE_SECS:
        return "stale"
    return st
