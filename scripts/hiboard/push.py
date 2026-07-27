"""配置加载与推送（含哈希去重、并发认领、配额熔断）。"""

import hashlib
import json
import os
import re
import time
import urllib.request
import uuid
from datetime import datetime

from .const import CARD_ID, DEFAULT_ENDPOINT, VERSION
from .render import render_content, render_summary
from .store import config_path, log, log_path, mutate_state

ERR_HINTS = {
    "0000900034": "授权码无效或未关联，请到负一屏重新获取",
    "0000500001": "缺少 x-trace-id header",
    "0000500002": "正文超过 30720 个 UTF-16 码元",
    "0000400001": "推送次数达到服务端配额上限，稍后重试（配额按日重置）",
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


def next_midnight() -> float:
    dt = datetime.fromtimestamp(time.time())
    return datetime(dt.year, dt.month, dt.day).timestamp() + 86400


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
            "User-Agent":   f"hiboard-status/{VERSION}",
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
