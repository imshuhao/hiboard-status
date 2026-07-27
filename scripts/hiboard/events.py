"""hook 事件分发：会话归属解析、状态机写入、后台摘要拉起。"""

import os
import subprocess
import sys
import time
from pathlib import Path

from .const import MAX_PROMPT_UTF16, SUMMARY_PLACEHOLDER
from .push import do_push, load_config
from .store import mutate_state
from .text import truncate_utf16

# 自我重启必须指回入口脚本（本模块的 __file__ 不可执行为 hook 入口）
ENTRY = Path(__file__).resolve().parent.parent / "hiboard_hook.py"


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


def spawn_summarizer(evt: dict, proj: str, turn_ts: float) -> None:
    if os.environ.get("HIBOARD_NO_SUMMARY"):
        return
    subprocess.Popen(
        [sys.executable, str(ENTRY), "--summarize", proj,
         evt.get("transcript_path", "") or "", str(turn_ts)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def handle_event(evt: dict) -> None:
    if not load_config():
        return  # 未配置即零副作用：不更新状态、不 spawn、不推送（设计 §9）
    name = evt.get("hook_event_name", "")
    if name not in ("SessionStart", "UserPromptSubmit", "Stop",
                    "Notification", "SessionEnd"):
        return
    now = time.time()
    result = {}

    def m(state):
        proj = resolve_project(state, evt, name)
        result["proj"] = proj
        entry = state["projects"].get(proj)
        base = {"session_id": evt.get("session_id", ""),
                "cwd": evt.get("cwd", ""), "updated_at": now}

        # summary 只有 Stop（占位符）和摘要进程会写，其余事件一律保留——
        # 它是「最近完成回合的总结」，跨指令、跨会话有效（设计 2026-07-27）
        if name == "SessionStart":
            # 幽灵会话防护（v0.2.5）：条目属于其他会话时只登记映射、不接管。
            # 桌面 App 秒开秒关的幽灵会话由此彻底隐形——它的 SessionEnd 也会
            # 因 session_id 不匹配被拦；真实新会话在首次 UserPromptSubmit 接管
            if entry is not None and entry.get("session_id") \
                    and entry["session_id"] != base["session_id"]:
                result["skip"] = True
                return
            fields = dict(base, status="running", prompt="")
        elif name == "UserPromptSubmit":
            p = truncate_utf16(" ".join((evt.get("prompt") or "").split()),
                               MAX_PROMPT_UTF16)
            fields = dict(base, status="running", prompt=p)  # 前缀由渲染层按状态添加
        elif name == "Stop":
            fields = dict(base, status="done", summary=SUMMARY_PLACEHOLDER,
                          summary_ts=now)
        elif name == "Notification":
            fields = dict(base, status="waiting")
        else:  # SessionEnd 守卫：只有记录在案的会话能把项目翻成「已结束」，
            # 桌面 App 回收进程产生的幽灵会话不得覆盖正主的状态
            if entry is None or (entry.get("session_id")
                                 and entry["session_id"] != base["session_id"]):
                result["skip"] = True
                return
            fields = dict(base, status="ended")
        state["projects"].setdefault(proj, {}).update(fields)

    mutate_state(m)
    if result.get("skip"):
        return
    if name == "Stop" and not os.environ.get("HIBOARD_NO_SUMMARY"):
        # 省一次配额：占位推送略过，摘要进程 10-30 秒内必推（含 LLM 失败的
        # 截断降级路径）；每回合配额消耗从 ~3 次降到 ~2 次
        spawn_summarizer(evt, result["proj"], now)
        return
    do_push()
    if name == "Stop":
        spawn_summarizer(evt, result["proj"], now)
