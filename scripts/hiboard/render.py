"""状态卡渲染。字段显示映射反直觉（summary=列表标题），见 api-spec §6.2。

格子模型：每项目分行展示各会话格子（≤MAX_CELLS_RENDER），头部状态按
waiting > running > done 取最高优先——「有会话在等你」比「有会话在跑」
更需要被一眼看见。无 cells 的旧扁平条目走 legacy 路径，7 天自然淘汰。
"""

import time
from datetime import datetime

from .const import (MAX_CARD_UTF16, MAX_CELLS_RENDER, MAX_LAST_SUMMARY_UTF16,
                    MAX_PROJECT_UTF16, STALE_SECS)
from .text import truncate_utf16

STATUS_META = {
    "running": ("🟢", "运行中"),
    "waiting": ("🟡", "等待输入"),
    "done":    ("✅", "本轮完成"),
    "ended":   ("⚪", "已结束"),
    "stale":   ("⚪", "状态未知"),
}
_CELL_PRIORITY = {"waiting": 0, "running": 1, "done": 2}


def fmt_time(ts: float, now=None) -> str:
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%m-%d %H:%M") if now - ts >= 86400 else dt.strftime("%H:%M")


def live_cells(entry: dict, now=None):
    """按优先级+新近排序的活格子 [(sid, cell)]；stale 格子不渲染。"""
    now = time.time() if now is None else now
    cells = entry.get("cells") or {}
    fresh = [(sid, c) for sid, c in cells.items()
             if now - c.get("updated_at", 0) <= STALE_SECS
             and c.get("status") in _CELL_PRIORITY]
    fresh.sort(key=lambda kv: (_CELL_PRIORITY[kv[1]["status"]],
                               -kv[1].get("updated_at", 0)))
    return fresh


def display_status(entry: dict, now=None) -> str:
    """项目展示状态：活格子最高优先；无活格即「已结束」。"""
    cells = live_cells(entry, now)
    return cells[0][1]["status"] if cells else "ended"


def _cell_line(cell: dict, entry: dict, single: bool, now) -> str:
    st = cell["status"]
    ts = fmt_time(cell.get("updated_at", now), now=now)
    prompt = cell.get("prompt") or ""
    if st == "running":
        txt = f"正在处理：{prompt}" if prompt else ""
        ts = f"自 {ts}"
    elif st == "waiting":
        txt = prompt
        ts = f"自 {ts}"
    else:  # done：单格时项目摘要即正文（与单会话时代一致），多格只标指令
        txt = (entry.get("summary") or prompt) if single else \
            (prompt or "本轮完成")
    emoji = "" if single else STATUS_META[st][0] + " "
    return f"`{ts}` {emoji}{txt}".rstrip()


def render_content(state: dict, now=None) -> str:
    now = time.time() if now is None else now
    items = sorted(state.get("projects", {}).items(),
                   key=lambda kv: kv[1].get("updated_at", 0), reverse=True)
    sections = []
    for name, e in items:
        st = display_status(e, now)
        emoji, label = STATUS_META.get(st, STATUS_META["stale"])
        summary = e.get("summary") or ""
        cells = live_cells(e, now)
        if len(cells) > 1:
            label = f"{len(cells)} 个会话"
        lines = [_cell_line(c, e, len(cells) == 1, now)
                 for _, c in cells[:MAX_CELLS_RENDER]]
        if not lines:  # 无活格：有摘要显示摘要（已结束），否则仅标题行
            ts = fmt_time(e.get("updated_at", now), now=now)
            lines = [f"`{ts}` {summary}".rstrip()] if summary else []
        body = truncate_utf16("  \n".join(lines), MAX_PROJECT_UTF16)
        if st in ("running", "waiting") and summary:
            # 硬换行（行尾两空格）；渲染器不支持时降级为同行显示，仍可读
            body += ("  \n" if body else "") + "↳ 上轮：" + \
                truncate_utf16(summary, MAX_LAST_SUMMARY_UTF16)
        sections.append(f"## {emoji} {name} — {label}\n{body}".rstrip())
    content = "\n\n---\n\n".join(sections) or "_暂无会话_"
    return truncate_utf16(content, MAX_CARD_UTF16)


def _running_count(entry: dict, now) -> int:
    return sum(1 for _, c in live_cells(entry, now)
               if c["status"] == "running")


def render_summary(state: dict, now=None) -> str:
    """列表态卡片标题（上游字段名 summary，见 API 契约 §6.2）。"""
    now = time.time() if now is None else now
    projects = state.get("projects", {})
    if not projects:
        return "Claude Code"
    running = sum(_running_count(e, now) for e in projects.values())
    name, latest = max(projects.items(),
                       key=lambda kv: kv[1].get("updated_at", 0))
    _, label = STATUS_META.get(display_status(latest, now),
                               STATUS_META["stale"])
    parts = ([f"{running} 个会话运行中"] if running else []) + [f"{name} {label}"]
    return truncate_utf16(" · ".join(parts), 60)