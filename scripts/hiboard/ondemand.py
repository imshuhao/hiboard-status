"""按需推送 CLI（--push / --test-push）。

与 hook 路径不同：这是 Claude/用户主动调用的命令，失败必须 exit 1 让调用方察觉。
"""

import json
import time
from pathlib import Path

from .const import CARD_ID, MAX_CARD_UTF16
from .push import load_config, push_card
from .store import log_path, mutate_state
from .text import slugify_ascii, truncate_utf16, utf16_len


def cmd_push(path: str) -> int:
    """--push 子模式：从 JSON 文件读取内容，推到轮转卡位或主题卡。

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
    chosen = []
    if topic:
        # 主题卡：固定 ID，同主题永远覆盖同一张卡（卡片不可删，慎用）
        card_id = f"claude_code_topic_{slugify_ascii(topic)}"
    else:
        # 轮转卡位：复用最久未用的 manual 槽，卡片总数恒定
        n = max(1, int(cfg.get("manualSlots", 3)))

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
                   "# 配置成功\n\nClaude Code 会话状态将推送到这张卡片。",
                   card_id=CARD_ID)
    print("推送成功，请到负一屏查看" if ok else
          f"推送失败，详见 {log_path()}")
    return 0 if ok else 1
