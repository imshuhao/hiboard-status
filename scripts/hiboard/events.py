"""hook 事件分发：会话归属解析、多会话格子状态机、后台摘要与推送拉起。

格子模型（设计 2026-07-28）：每个项目条目内 cells[session_id] 独立记录
各会话的 status/prompt，格子生于首次 UserPromptSubmit、死于 SessionEnd
或超龄清理——幽灵会话（从不提交指令）因此天然隐形，无需 ownership 守卫。
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .const import (CELL_PRUNE_SECS, DEBOUNCE_SECS, FLUSH_CLAIM_SECS,
                    MAX_PROMPT_UTF16, REGISTRY_GRACE_SECS,
                    SUMMARY_PLACEHOLDER)
from .push import do_push, load_config
from .store import mutate_state
from .text import truncate_utf16

# 自我重启必须指回入口脚本（本模块的 __file__ 不可执行为 hook 入口）
ENTRY = Path(__file__).resolve().parent.parent / "hiboard_hook.py"

# Notification 事件里真正代表「等待用户」的类型（缺失该字段按等待处理）
WAITING_TYPES = {"permission_prompt", "idle_prompt",
                 "elicitation_dialog", "agent_needs_input"}


def _spawn_detached(cmd, stdin_text: str = None) -> None:
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if stdin_text is not None:
        kw["stdin"] = subprocess.PIPE
    if os.name == "posix":
        kw["start_new_session"] = True
    else:  # Windows：DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kw["creationflags"] = 0x00000008 | 0x00000200
    p = subprocess.Popen(cmd, **kw)
    if stdin_text is not None:
        try:  # 文本已截尾 8000 字符，远小于管道缓冲，写入不阻塞
            p.stdin.write(stdin_text.encode("utf-8"))
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass


def project_name(cwd: str) -> str:
    return Path(cwd).name or "unknown" if cwd else "unknown"


def resolve_project(state: dict, evt: dict, name: str) -> str:
    """项目归属钉死在会话启动目录：SessionStart 记录 session→项目映射，
    后续事件按 session_id 查表——会话中途 cd 不再造成归属漂移。
    查不到映射（插件启用前已开始的会话）退回按当前 cwd 推断。"""
    sid = evt.get("session_id", "")
    sessions = state.setdefault("sessions", {})
    if name == "SessionStart":
        proj = project_name(evt.get("cwd", ""))
        if sid:
            sessions[sid] = {"proj": proj, "ts": time.time()}
        return proj
    if sid and sid in sessions:
        sessions[sid]["ts"] = time.time()
        return sessions[sid]["proj"]
    return project_name(evt.get("cwd", ""))


def live_session_ids():
    """~/.claude/sessions/<pid>.json 活会话注册表（未文档化，best-effort）。

    返回存活会话 id 集合；任何异常返回 None（调用方退回纯 stale 清理）。
    """
    if os.environ.get("HIBOARD_NO_REGISTRY"):
        return None
    try:
        ids = set()
        for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            sid, pid = d.get("sessionId"), d.get("pid")
            if sid and pid:
                try:
                    os.kill(pid, 0)
                    ids.add(sid)
                except OSError:
                    pass
        return ids
    except Exception:
        return None


def prune_cells(entry: dict, now: float, live) -> None:
    """删除超龄格子；注册表可读时删除已死会话的格子（留新格宽限期）。"""
    cells = entry.get("cells")
    if not cells:
        return
    for sid in list(cells):
        age = now - cells[sid].get("updated_at", 0)
        if age > CELL_PRUNE_SECS:
            del cells[sid]
        elif (live is not None and sid not in live
              and age > REGISTRY_GRACE_SECS):
            del cells[sid]


def _cells_of(entry: dict) -> dict:
    """取格子表；首次进入格子模型时清除 legacy 扁平字段。"""
    if "cells" not in entry:
        for k in ("status", "prompt", "session_id"):
            entry.pop(k, None)
    return entry.setdefault("cells", {})


def spawn_summarizer(evt: dict, proj: str, turn_ts: float,
                     user_prompt: str = "") -> None:
    if os.environ.get("HIBOARD_NO_SUMMARY"):
        return
    # 首选 Stop 载荷自带的 last_assistant_message（经 stdin 传递）；
    # 载荷缺失（旧版 Claude Code）退回 transcript 解析
    text = (evt.get("last_assistant_message") or "")[-8000:]
    transcript = "" if text else (evt.get("transcript_path", "") or "")
    _spawn_detached(
        [sys.executable, str(ENTRY), "--summarize", proj, str(turn_ts),
         user_prompt, transcript],
        stdin_text=text)


def request_push() -> None:
    """hook 路径的推送请求：不在 hook 进程里做网络 IO。

    锁内认领 flush_claim（过期自愈），认领成功才拉起分离的 --flush 进程；
    合并窗口内的连发事件由同一个 flusher 一次推完。HIBOARD_NO_FLUSH=1
    退回同步推送（测试与逃生门）。"""
    if os.environ.get("HIBOARD_NO_FLUSH"):
        do_push()
        return
    claimed = []

    def claim(state):
        c = state.get("flush_claim") or {}
        if time.time() - c.get("ts", 0) < FLUSH_CLAIM_SECS:
            return  # 已有 flusher 在途，它会带上本次变更
        state["flush_claim"] = {"ts": time.time()}
        claimed.append(True)

    mutate_state(claim)
    if claimed:
        cfg = load_config() or {}
        _spawn_detached([sys.executable, str(ENTRY), "--flush",
                         str(cfg.get("pushDebounce", DEBOUNCE_SECS))])


def handle_event(evt: dict) -> None:
    if not load_config():
        return  # 未配置即零副作用：不更新状态、不 spawn、不推送（设计 §9）
    name = evt.get("hook_event_name", "")
    if name not in ("SessionStart", "UserPromptSubmit", "Stop",
                    "Notification", "SessionEnd"):
        return
    sid = evt.get("session_id", "")
    now = time.time()
    live = live_session_ids() if name != "SessionStart" else None
    result = {}

    def m(state):
        proj = resolve_project(state, evt, name)
        result["proj"] = proj
        if name == "SessionStart":
            return  # 只登记映射；格子生于首次 UserPromptSubmit（幽灵隐形）
        entry = state["projects"].get(proj)
        if entry is None:
            if name in ("Notification", "SessionEnd"):
                result["skip"] = True
                return  # 没有格子的会话不值得为这两类事件建条目
            entry = state["projects"][proj] = {}
        cells = _cells_of(entry)
        prune_cells(entry, now, live)

        if name == "UserPromptSubmit":
            p = truncate_utf16(" ".join((evt.get("prompt") or "").split()),
                               MAX_PROMPT_UTF16)
            cells[sid] = {"status": "running", "prompt": p, "updated_at": now}
        elif name == "Stop":
            cell = cells.setdefault(sid, {"prompt": ""})
            cell.update(status="done", updated_at=now)
            result["prompt"] = cell.get("prompt", "")
            # 项目级摘要占位（summary 语义与乱序防护沿用 2026-07-27 设计）
            entry.update(summary=SUMMARY_PLACEHOLDER, summary_ts=now)
        elif name == "Notification":
            ntype = evt.get("notification_type")
            if (ntype and ntype not in WAITING_TYPES) or sid not in cells:
                result["skip"] = True
                return
            cells[sid].update(status="waiting", updated_at=now)
        else:  # SessionEnd：删本格即可（reason 可能只是 /clear 或切换会话）
            if sid not in cells:
                result["skip"] = True
                return
            del cells[sid]
        entry["updated_at"] = now
        entry["cwd"] = evt.get("cwd", "")

    mutate_state(m)
    if result.get("skip") or name == "SessionStart":
        return
    if name == "Stop" and not os.environ.get("HIBOARD_NO_SUMMARY"):
        # 省一次配额：占位推送略过，摘要进程 10-30 秒内必推（含降级路径）
        spawn_summarizer(evt, result["proj"], now, result.get("prompt", ""))
        return
    request_push()
    if name == "Stop":
        spawn_summarizer(evt, result["proj"], now, result.get("prompt", ""))