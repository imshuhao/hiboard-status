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


# ---------------------------------------------------------------- 渲染

def render_content(state: dict, now=None) -> str:
    now = time.time() if now is None else now
    items = sorted(state.get("projects", {}).items(),
                   key=lambda kv: kv[1].get("updated_at", 0), reverse=True)
    sections = []
    for name, e in items:
        st = effective_status(e, now)
        emoji, label = STATUS_META[st]
        body = truncate_utf16(e.get("summary") or e.get("prompt") or "",
                              MAX_PROJECT_UTF16)
        ts = fmt_time(e.get("updated_at", now), now=now)
        sections.append(f"## {emoji} {name} — {label}\n`{ts}` {body}".rstrip())
    content = "\n\n---\n\n".join(sections) or "_暂无会话_"
    return truncate_utf16(content, MAX_CARD_UTF16)


def render_summary(state: dict, now=None) -> str:
    """列表态卡片标题（上游字段名 summary，见 API 契约 §6.2）。"""
    now = time.time() if now is None else now
    projects = state.get("projects", {})
    if not projects:
        return "Claude Code"
    running = sum(1 for e in projects.values()
                  if effective_status(e, now) == "running")
    name, latest = max(projects.items(),
                       key=lambda kv: kv[1].get("updated_at", 0))
    _, label = STATUS_META[effective_status(latest, now)]
    parts = ([f"{running} 个会话运行中"] if running else []) + [f"{name} {label}"]
    return truncate_utf16(" · ".join(parts), 60)


# ---------------------------------------------------------------- 路径与日志

def data_dir() -> Path:
    return Path(os.environ.get("HIBOARD_DATA_DIR",
                               str(Path.home() / ".claude" / "hiboard")))


def state_path() -> Path:
    return data_dir() / "state.json"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "push.log"


def log(msg: str) -> None:
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
    except Exception:
        pass  # 日志失败不影响任何流程


# ---------------------------------------------------------------- 状态文件

def mutate_state(mutator) -> dict:
    """flock 串行化：加载 → mutator 就地修改 → 清理过期 → 原子写回。"""
    data_dir().mkdir(parents=True, exist_ok=True)
    with open(data_dir() / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(state_path().read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("state 不是对象")
            except FileNotFoundError:
                state = {}
            except (ValueError, json.JSONDecodeError):
                state_path().rename(state_path().with_suffix(".json.bak"))
                log("WARN state.json 损坏，已备份并重建")
                state = {}
            state.setdefault("projects", {})
            mutator(state)
            now = time.time()
            state["projects"] = {
                n: e for n, e in state["projects"].items()
                if now - e.get("updated_at", 0) <= PRUNE_SECS
            }
            tmp = state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.rename(state_path())
            return state
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def update_project(proj: str, fields: dict) -> dict:
    def m(state):
        state["projects"].setdefault(proj, {}).update(fields)
    return mutate_state(m)
