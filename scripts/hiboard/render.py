"""状态卡渲染。字段显示映射反直觉（summary=列表标题），见 api-spec §6.2。"""

import time
from datetime import datetime

from .const import (MAX_CARD_UTF16, MAX_LAST_SUMMARY_UTF16,
                    MAX_PROJECT_UTF16, STALE_SECS)
from .text import truncate_utf16

STATUS_META = {
    "running": ("🟢", "运行中"),
    "waiting": ("🟡", "等待输入"),
    "done":    ("✅", "本轮完成"),
    "ended":   ("⚪", "已结束"),
    "stale":   ("⚪", "状态未知"),
}


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


def render_content(state: dict, now=None) -> str:
    now = time.time() if now is None else now
    items = sorted(state.get("projects", {}).items(),
                   key=lambda kv: kv[1].get("updated_at", 0), reverse=True)
    sections = []
    for name, e in items:
        st = effective_status(e, now)
        emoji, label = STATUS_META.get(st, STATUS_META["stale"])
        prompt = e.get("prompt") or ""
        summary = e.get("summary") or ""  # 语义：最近一个已完成回合的总结
        if st == "running":
            body = f"正在处理：{prompt}" if prompt else ""
        elif st == "waiting":
            body = prompt
        elif st == "done":
            body = summary or prompt
        else:  # ended / stale：不再展示「正在处理」类瞬时信息
            body = summary
        body = truncate_utf16(body, MAX_PROJECT_UTF16)
        if st in ("running", "waiting") and summary:
            # 硬换行（行尾两空格）；渲染器不支持时降级为同行显示，仍可读
            body += ("  \n" if body else "") + "↳ 上轮：" + \
                truncate_utf16(summary, MAX_LAST_SUMMARY_UTF16)
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
    _, label = STATUS_META.get(effective_status(latest, now),
                               STATUS_META["stale"])
    parts = ([f"{running} 个会话运行中"] if running else []) + [f"{name} {label}"]
    return truncate_utf16(" · ".join(parts), 60)
