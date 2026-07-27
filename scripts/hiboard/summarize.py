"""Stop 后台摘要：transcript 解析 → LLM（降级：截断）→ 只写 summary 字段。"""

import json
import os
import subprocess
import time

from .push import do_push, load_config
from .store import log, mutate_state
from .text import truncate_utf16


def last_assistant_text(transcript_path: str) -> str:
    """从 Claude Code transcript（JSONL）提取最后一条 assistant 文本消息。"""
    best = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if obj.get("type") != "assistant":
                    continue
                content = (obj.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(b.get("text", "") for b in content
                                     if isinstance(b, dict)
                                     and b.get("type") == "text")
                else:
                    continue
                if text.strip():
                    best = text.strip()
    except OSError:
        return ""
    return best


def llm_summarize(text: str, cfg: dict):
    """调 claude CLI 生成摘要；任何失败返回 None（触发降级）。"""
    if os.environ.get("HIBOARD_NO_LLM"):
        return None
    prompt = ("用两三句中文总结这段 AI 助手的工作汇报，"
              "说明做了什么、结果如何。直接输出总结，不要前言：\n\n" + text)
    try:
        # HIBOARD_SUMMARIZING 会传导给无头会话触发的本插件 hooks，
        # 使其直接退出——否则无头会话的 Stop 又会 spawn 摘要，无限递归。
        r = subprocess.run(
            ["claude", "-p", "--model", cfg.get("summaryModel", "haiku"),
             prompt],
            capture_output=True, text=True,
            env={**os.environ, "HIBOARD_SUMMARIZING": "1"},
            timeout=cfg.get("summaryTimeout", 30))
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def run_summarize(proj: str, transcript_path: str, turn_ts: float) -> None:
    """--summarize 子模式：后台生成摘要并二次推送。

    summary 语义为「最近一个已完成回合的总结」，本函数只写 summary/summary_ts，
    绝不碰 status/prompt/updated_at——写入时机因此永远无害（迟到只是更新
    running 卡片的「上轮」行）。
    """
    text = last_assistant_text(transcript_path)
    summary = None
    if text:
        cfg = load_config() or {}
        summary = llm_summarize(text[-8000:], cfg)
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
