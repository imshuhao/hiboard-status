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


# ---------------------------------------------------------------- 配置与推送

ERR_HINTS = {
    "0000900034": "授权码无效或未关联，请到负一屏重新获取",
    "0000500001": "缺少 x-trace-id header",
    "0000500002": "正文超过 30720 个 UTF-16 码元",
}
CP_HINTS = {
    "82600017": "设备未联网或未登录华为账号",
    "82600013": "负一屏「服务动态」推送开关已关闭",
    "82600005": "服务动态云服务异常，请稍后重试",
}


def load_config():
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return None
    if not cfg.get("authCode"):
        return None
    return cfg


def push_card(cfg: dict, summary: str, content: str) -> bool:
    now = int(time.time())
    body = {"data": {
        "authCode": cfg["authCode"],
        "msgContent": [{
            "msgId":            f"{CARD_ID}_{now}_{uuid.uuid4().hex[:6]}",
            "scheduleTaskId":   CARD_ID,
            "scheduleTaskName": "Claude Code Status",  # 必填但不显示（契约 §6.2）
            "summary":          summary,               # 列表态卡片标题
            "result":           f"最后更新 {datetime.now():%H:%M}",
            "content":          content,
            "source":           "Claude Code",         # 展开态主标题
            "taskFinishTime":   now,
        }],
    }}
    if os.environ.get("HIBOARD_DRY_RUN"):
        log("DRY_RUN " + json.dumps(body, ensure_ascii=False)[:800])
        return True

    req = urllib.request.Request(
        cfg.get("pushServiceUrl", DEFAULT_ENDPOINT), method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent":   "hiboard-status/0.1",
            "x-trace-id":   f"ccs-{now}-{uuid.uuid4().hex[:8]}",  # 必需，非空即可
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"PUSH_FAIL {type(e).__name__}: {e}")
        return False

    code = str(data.get("code", ""))
    if code == "0000000000":
        return True
    desc = data.get("desc") or data.get("message") or ""
    hint = ERR_HINTS.get(code, "")
    if code == "0200100004":
        import re as _re
        m = _re.search(r"Receive error code (\d+) from CP", desc)
        if m:
            hint = CP_HINTS.get(m.group(1), f"未知 CP 错误码 {m.group(1)}")
    log(f"PUSH_FAIL {code} {desc}" + (f" — {hint}" if hint else ""))
    return False


def do_push(state: dict) -> None:
    cfg = load_config()
    if not cfg:
        return
    content = render_content(state)
    summary = render_summary(state)
    digest = hashlib.sha256(f"{summary}\n{content}".encode()).hexdigest()
    if state.get("last_push_hash") == digest:
        return
    if push_card(cfg, summary, content):
        mutate_state(lambda s: s.update({"last_push_hash": digest}))


# ---------------------------------------------------------------- 事件分发

def project_name(cwd: str) -> str:
    return Path(cwd).name or "unknown" if cwd else "unknown"


def spawn_summarizer(evt: dict, proj: str) -> None:
    if os.environ.get("HIBOARD_NO_SUMMARY"):
        return
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--summarize", proj,
         evt.get("transcript_path", "") or ""],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


# ---------------------------------------------------------------- Stop 摘要

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
        r = subprocess.run(
            ["claude", "-p", "--model", cfg.get("summaryModel", "haiku"),
             prompt],
            capture_output=True, text=True,
            timeout=cfg.get("summaryTimeout", 30))
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def run_summarize(proj: str, transcript_path: str) -> None:
    """--summarize 子模式：后台生成摘要并二次推送。"""
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
    state = update_project(proj, {"summary": summary, "status": "done",
                                  "updated_at": time.time()})
    do_push(state)


def handle_event(evt: dict) -> None:
    name = evt.get("hook_event_name", "")
    proj = project_name(evt.get("cwd", ""))
    now = time.time()
    base = {"session_id": evt.get("session_id", ""),
            "cwd": evt.get("cwd", ""), "updated_at": now}

    if name == "SessionStart":
        fields = dict(base, status="running", prompt="", summary="")
    elif name == "UserPromptSubmit":
        p = truncate_utf16(" ".join((evt.get("prompt") or "").split()),
                           MAX_PROMPT_UTF16)
        fields = dict(base, status="running", prompt=f"正在处理：{p}", summary="")
    elif name == "Stop":
        fields = dict(base, status="done", summary="（摘要生成中…）")
    elif name == "Notification":
        fields = dict(base, status="waiting")  # 不动 prompt/summary
    elif name == "SessionEnd":
        fields = dict(base, status="ended")
    else:
        return

    state = update_project(proj, fields)
    do_push(state)
    if name == "Stop":
        spawn_summarizer(evt, proj)


def main(argv) -> int:
    try:
        if len(argv) >= 2 and argv[1] == "--summarize":
            run_summarize(argv[2], argv[3] if len(argv) > 3 else "")
        elif len(argv) >= 2 and argv[1] == "--test-push":
            cfg = load_config()
            if not cfg:
                print("未找到有效配置：请先创建 ~/.claude/hiboard/config.json")
                return 0
            ok = push_card(cfg, "hiboard-status 配置成功 ✅",
                           "# 配置成功\n\nClaude Code 会话状态将推送到这张卡片。")
            print("推送成功，请到负一屏查看" if ok else
                  f"推送失败，详见 {log_path()}")
        else:
            evt = json.load(sys.stdin)
            handle_event(evt)
    except Exception as e:
        log(f"ERROR {type(e).__name__}: {e}")
    return 0  # 恒 0：旁路系统绝不打断 Claude Code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
