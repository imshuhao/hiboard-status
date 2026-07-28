"""Stop 后台摘要：transcript 解析 → LLM（降级：截断）→ 只写 summary 字段。"""

import json
import os
import subprocess
import time

from .const import SUMMARY_MIN_UTF16
from .push import do_push, load_config
from .store import log, mutate_state
from .text import truncate_utf16, utf16_len


def _msg_text(obj: dict) -> str:
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict)
                         and b.get("type") == "text").strip()
    return ""


def last_turn(transcript_path: str):
    """从 transcript（JSONL）提取最后一轮：(用户指令文本, 助手文本)。

    用户文本取「最后一条助手文本之前最近的一条用户文本」，给 LLM 摘要
    提供任务上下文（「修复了 X」而非「完成了任务」）。"""
    cur_user = paired_user = assistant = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                t = obj.get("type")
                if t == "user":
                    text = _msg_text(obj)
                    if text:
                        cur_user = text
                elif t == "assistant":
                    text = _msg_text(obj)
                    if text:
                        assistant, paired_user = text, cur_user
    except OSError:
        return "", ""
    return paired_user, assistant


def last_assistant_text(transcript_path: str) -> str:
    """兼容封装：最后一条 assistant 文本消息。"""
    return last_turn(transcript_path)[1]


def llm_summarize(text: str, cfg: dict, user_prompt: str = ""):
    """调 claude CLI 生成摘要；任何失败返回 None（触发降级）。

    --bare 跳过 hooks/插件加载（结构性防递归 + 提速），--no-session-persistence
    不留假 transcript；旧版 CLI 不识别这两个 flag 时去 flag 重试一次。
    HIBOARD_SUMMARIZING 环境守卫保留为双保险。
    """
    if os.environ.get("HIBOARD_NO_LLM"):
        return None
    prefix = (f"用户的指令是：{truncate_utf16(user_prompt, 500)}\n\n"
              if user_prompt else "")
    prompt = (prefix + "用两三句中文总结这段 AI 助手的工作汇报，"
              "说明做了什么、结果如何。直接输出总结，不要前言：\n\n" + text)
    for extra in (["--bare", "--no-session-persistence"], []):
        try:
            r = subprocess.run(
                ["claude", "-p", *extra,
                 "--model", cfg.get("summaryModel", "haiku"), prompt],
                capture_output=True, text=True,
                env={**os.environ, "HIBOARD_SUMMARIZING": "1"},
                timeout=cfg.get("summaryTimeout", 30))
            out = r.stdout.strip()
            if r.returncode == 0 and out:
                return out
        except (subprocess.TimeoutExpired, OSError):
            return None
    return None


def run_summarize(proj: str, turn_ts: float, user_prompt: str = "",
                  transcript_path: str = "", text: str = "") -> None:
    """--summarize 子模式：后台生成摘要并二次推送。

    文本首选 Stop 载荷的 last_assistant_message（经 stdin 传入 text）；
    载荷缺失（旧版 Claude Code）退回 transcript 解析。summary 语义为
    「最近一个已完成回合的总结」，本函数只写 summary/summary_ts，
    绝不碰格子——写入时机因此永远无害（迟到只是更新「上轮」行）。
    """
    if not text and transcript_path:
        tp_user, text = last_turn(transcript_path)
        user_prompt = user_prompt or tp_user
    summary = None
    if text:
        cfg = load_config() or {}
        collapsed = " ".join(text.split())
        if utf16_len(collapsed) <= cfg.get("summaryMinChars",
                                           SUMMARY_MIN_UTF16):
            summary = collapsed  # 琐碎回合：原文即摘要，不烧 LLM 不等 30 秒
        else:
            summary = llm_summarize(text[-8000:], cfg, user_prompt=user_prompt)
            if summary is None:
                summary = truncate_utf16(text, 500)
                log(f"SUMMARY_FALLBACK {proj}")
    if not summary:
        summary = "（本轮无文本输出）"
    # 压掉换行：截断降级路径是转录原文，行首的 ## / --- 会伪造卡片分节
    summary = " ".join(summary.split())

    applied = []

    def apply(state):
        e = state["projects"].get(proj)
        # 乱序防护：旧回合的摘要不得覆盖新回合的（turn_ts 为 Stop 时刻）
        if e and turn_ts >= e.get("summary_ts", 0):
            e.update({"summary": summary, "summary_ts": turn_ts})
            applied.append(True)

    mutate_state(apply)
    if applied:
        do_push()
    else:
        log(f"SUMMARY_OUTDATED {proj} 已有更新回合的摘要或条目已清理，跳过")
