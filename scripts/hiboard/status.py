"""--status 只读诊断：把「cat state.json + tail push.log」变成一条命令。"""

import json
import time
from datetime import datetime

from .const import VERSION
from .push import load_config, status_card_id
from .render import STATUS_META, effective_status, fmt_time
from .store import log_path, state_path


def _load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _today_failures() -> int:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        return sum(1 for line in log_path().read_text(
            encoding="utf-8").splitlines()
            if line.startswith(today) and "PUSH_FAIL" in line)
    except OSError:
        return 0


def cmd_status() -> int:
    print(f"hiboard-status v{VERSION}")
    cfg = load_config()
    if not cfg:
        print("配置：未配置或已禁用（~/.claude/hiboard/config.json）")
    else:
        extra = f"，状态卡 {status_card_id(cfg)}"
        print(f"配置：OK{extra}")

    state = _load_state()
    now = time.time()
    qb = state.get("quota_blocked_until", 0)
    if qb > now:
        print(f"配额熔断中：至 {datetime.fromtimestamp(qb):%m-%d %H:%M} "
              "（服务端按日配额已尽，午夜自动恢复）")
    lp = state.get("last_push_ts")
    if lp:
        print(f"最近成功推送：{fmt_time(lp, now=now)}")
    fails = _today_failures()
    if fails:
        print(f"今日推送失败：{fails} 次（详见 {log_path()}）")

    projects = state.get("projects", {})
    if projects:
        print("项目：")
        for name, e in sorted(projects.items(),
                              key=lambda kv: kv[1].get("updated_at", 0),
                              reverse=True):
            st = effective_status(e, now)
            emoji, label = STATUS_META.get(st, STATUS_META["stale"])
            ts = fmt_time(e.get("updated_at", now), now=now)
            snippet = (e.get("prompt") or e.get("summary") or "")[:40]
            print(f"  {emoji} {name} — {label} @ {ts}  {snippet}")
    else:
        print("项目：无记录")

    topics = state.get("topics", {})
    if topics:
        print("主题卡（永久，无删除接口）：")
        for cid, t in sorted(topics.items(),
                             key=lambda kv: kv[1].get("ts", 0), reverse=True):
            print(f"  「{t.get('topic', '?')}」 → {cid}"
                  f"（推送 {t.get('count', 0)} 次，"
                  f"最近 {fmt_time(t.get('ts', now), now=now)}）")

    slots = state.get("manual_slots", {})
    if slots:
        ages = ", ".join(f"槽{k} {fmt_time(v, now=now)}"
                         for k, v in sorted(slots.items()))
        print(f"轮转卡位：{ages}")
    return 0
