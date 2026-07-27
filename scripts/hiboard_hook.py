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
import re
import subprocess
import sys
import time
import uuid
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_ENDPOINT = ("https://hiboard-claw-drcn.ai.dbankcloud.cn"
                    "/distribution/message/cloud/claw/msg/upload")
CARD_ID = "claude_code_status"
MAX_CARD_UTF16 = 28000
MAX_PROJECT_UTF16 = 3000
MAX_PROMPT_UTF16 = 60
MAX_LAST_SUMMARY_UTF16 = 300
STALE_SECS = 2 * 3600
PRUNE_SECS = 7 * 24 * 3600
SUMMARY_PLACEHOLDER = "（摘要生成中…）"

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
    if limit <= 0:
        return ""
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


def ensure_dir() -> Path:
    """创建数据目录，权限 0700。state/log 含用户指令原文，不应对同机他人可读。"""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        d.chmod(0o700)  # 目录已存在时 mkdir 的 mode 不生效，显式补一次
    except OSError:
        pass
    return d


def chmod_600(p: Path) -> None:
    try:
        p.chmod(0o600)
    except OSError:
        pass


def log(msg: str) -> None:
    try:
        ensure_dir()
        p = log_path()
        try:
            if p.stat().st_size > 512 * 1024:  # 轮转：只留最近 500 行
                lines = p.read_text(encoding="utf-8").splitlines()[-500:]
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
        chmod_600(p)
    except Exception:
        pass  # 日志失败不影响任何流程


# ---------------------------------------------------------------- 状态文件

def mutate_state(mutator) -> dict:
    """flock 串行化：加载 → mutator 就地修改 → 清理过期 → 原子写回。"""
    ensure_dir()
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
            state["sessions"] = {
                sid: s for sid, s in state.get("sessions", {}).items()
                if now - s.get("ts", 0) <= PRUNE_SECS
            }
            tmp = state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            chmod_600(tmp)  # rename 前设权限，避免出现可读窗口
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
    "0000400001": "推送次数达到服务端配额上限，稍后重试（配额疑似按日重置）",
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


def push_card(cfg: dict, summary: str, content: str, *,
              card_id: str = CARD_ID, source: str = "Claude Code",
              result: str = None, task_name: str = "Claude Code Status",
              timeout: float = 15) -> bool:
    now = int(time.time())
    body = {"data": {
        "authCode": cfg["authCode"],
        "msgContent": [{
            "msgId":            f"{card_id}_{now}_{uuid.uuid4().hex[:6]}",
            "scheduleTaskId":   card_id,
            "scheduleTaskName": task_name,             # 必填但不显示（契约 §6.2）
            "summary":          summary,               # 列表态卡片标题
            "result":           result or f"最后更新 {datetime.now():%H:%M}",
            "content":          content,
            "source":           source,                # 展开态主标题
            "taskFinishTime":   now,
        }],
    }}
    if os.environ.get("HIBOARD_DRY_RUN"):
        masked = json.loads(json.dumps(body))  # 深拷贝，不动原 body
        masked["data"]["authCode"] = "***"     # 日志绝不落授权码明文
        log("DRY_RUN " + json.dumps(masked, ensure_ascii=False)[:800])
        return True

    req = urllib.request.Request(
        cfg.get("pushServiceUrl", DEFAULT_ENDPOINT), method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent":   "hiboard-status/0.2.5",
            "x-trace-id":   f"ccs-{now}-{uuid.uuid4().hex[:8]}",  # 必需，非空即可
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
        m = re.search(r"Receive error code (\d+) from CP", desc)
        if m:
            hint = CP_HINTS.get(m.group(1), f"未知 CP 错误码 {m.group(1)}")
    log(f"PUSH_FAIL {code} {desc}" + (f" — {hint}" if hint else ""))
    if code == "0000400001":
        # 配额熔断：按日配额已尽（午夜重置，实测见 api-spec §5.2）。
        # 记入 state 后 do_push 在午夜前不再尝试；CLI 模式不受熔断限制
        try:
            until = next_midnight()
            mutate_state(lambda s: s.update({"quota_blocked_until": until}))
        except Exception:
            pass
    return False


def next_midnight() -> float:
    dt = datetime.fromtimestamp(time.time())
    return datetime(dt.year, dt.month, dt.day).timestamp() + 86400


def do_push() -> None:
    """渲染并推送状态卡。渲染与哈希认领在锁内完成：并发进程（多会话 hook、
    后台摘要）对同一内容只推一次；认领 60 秒过期，进程被杀不会永久卡住。
    配额熔断期间（quota_blocked_until）自动推送直接跳过，省掉必败的请求。"""
    cfg = load_config()
    if not cfg:
        return
    payload = {}

    def claim(state):
        if state.get("quota_blocked_until", 0) > time.time():
            return
        content = render_content(state)
        summary = render_summary(state)
        digest = hashlib.sha256(f"{summary}\n{content}".encode()).hexdigest()
        if state.get("last_push_hash") == digest:
            return
        c = state.get("push_claim") or {}
        if c.get("digest") == digest and time.time() - c.get("ts", 0) < 60:
            return  # 另一进程正在推同样的内容
        state["push_claim"] = {"digest": digest, "ts": time.time()}
        payload.update(digest=digest, summary=summary, content=content)

    mutate_state(claim)
    if not payload:
        return
    ok = push_card(cfg, payload["summary"], payload["content"], timeout=6)

    def finish(state):
        if (state.get("push_claim") or {}).get("digest") == payload["digest"]:
            state.pop("push_claim", None)
        if ok:
            state["last_push_hash"] = payload["digest"]

    mutate_state(finish)


# ---------------------------------------------------------------- 事件分发

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
        [sys.executable, os.path.abspath(__file__), "--summarize", proj,
         evt.get("transcript_path", "") or "", str(turn_ts)],
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
    running 卡片的「上轮」行），v0.2.2 的占位符守卫随之删除。
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


# ---------------------------------------------------------------- 按需推送

def slugify_ascii(name: str) -> str:
    """主题名 → 稳定 slug。含非 ASCII 字符时附加短哈希——否则所有中文主题
    会塌缩成同一个 ID 互相覆盖，而主题卡永久存在，塌缩无法挽回。"""
    s = "".join(c if c.isascii() and c.isalnum() else "_" for c in name).lower()
    s = re.sub(r"_+", "_", s).strip("_")
    if not name.isascii():
        h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
        s = f"{s}_{h}" if s else h
    return s or "topic"


def cmd_push(path: str) -> int:
    """--push 子模式：从 JSON 文件读取内容，推到轮转卡位或主题卡。

    与 hook 路径不同：这是 Claude 主动调用的 CLI，失败必须 exit 1 让调用方察觉。
    输入 JSON: {summary*, content*, source?, result?, topic?}
    """
    cfg = load_config()
    if not cfg:
        print("未找到有效配置：请先运行 /hiboard-status:setup")
        return 1
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读取推送文件失败: {type(e).__name__}: {e}")
        return 1
    summary = (data.get("summary") or "").strip()
    content = data.get("content") or ""
    if not summary or not content.strip():
        print("summary 与 content 均为必填字段")
        return 1
    if utf16_len(content) > MAX_CARD_UTF16:
        print(f"content 过长：{utf16_len(content)} > {MAX_CARD_UTF16} "
              "UTF-16 码元（注意 emoji 计 2），请精简后重试")
        return 1

    topic = (data.get("topic") or "").strip()
    if topic:
        # 主题卡：固定 ID，同主题永远覆盖同一张卡（卡片不可删，慎用）
        card_id = f"claude_code_topic_{slugify_ascii(topic)}"
    else:
        # 轮转卡位：复用最久未用的 manual 槽，卡片总数恒定
        n = max(1, int(cfg.get("manualSlots", 3)))
        chosen = []

        def choose(state):
            slots = state.setdefault("manual_slots", {})
            keys = [str(i) for i in range(1, n + 1)]
            k = min(keys, key=lambda x: slots.get(x, 0))
            chosen.append({"k": k, "old": slots.get(k), "new": time.time()})
            slots[k] = chosen[0]["new"]

        mutate_state(choose)
        card_id = f"claude_code_manual_{chosen[0]['k']}"

    ok = push_card(cfg, truncate_utf16(summary, 60), content,
                   card_id=card_id,
                   source=(data.get("source") or "Claude Code 推送").strip(),
                   result=(data.get("result") or "推送完成").strip(),
                   task_name="Claude Code Push")
    if ok:
        print(f"推送成功（卡片 {card_id}）")
        return 0
    if not topic:
        # 推送失败回滚卡位时间戳，不烧掉轮转位置；若期间已被并发推送
        # 再次占用（时间戳不再是我们写的值）则不动
        c = chosen[0]

        def restore(state):
            slots = state.setdefault("manual_slots", {})
            if slots.get(c["k"]) == c["new"]:
                if c["old"] is None:
                    slots.pop(c["k"], None)
                else:
                    slots[c["k"]] = c["old"]

        mutate_state(restore)
    print(f"推送失败，详见 {log_path()}")
    return 1


def cmd_test_push() -> int:
    cfg = load_config()
    if not cfg:
        print("未找到有效配置：请先创建 ~/.claude/hiboard/config.json")
        return 1
    ok = push_card(cfg, "hiboard-status 配置成功 ✅",
                   "# 配置成功\n\nClaude Code 会话状态将推送到这张卡片。")
    print("推送成功，请到负一屏查看" if ok else
          f"推送失败，详见 {log_path()}")
    return 0 if ok else 1


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
    try:
        if mode == "--summarize":
            run_summarize(argv[2], argv[3] if len(argv) > 3 else "",
                          float(argv[4]) if len(argv) > 4 else 0.0)
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
