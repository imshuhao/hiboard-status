# hiboard-status 插件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code 插件：通过 hooks 把会话状态与回合摘要推送到华为负一屏的一张聚合卡片上。

**Architecture:** 单脚本多入口——5 个 hook 事件都调 `scripts/hiboard_hook.py`，脚本内部：flock 更新 `~/.claude/hiboard/state.json` → 渲染全量 markdown → hash 去重 → 推送覆盖固定卡片。`Stop` 事件额外 fork 后台子进程调 `claude -p` 生成摘要后二次推送。

**Tech Stack:** Python 3（纯标准库，运行时零第三方依赖）、unittest（测试）、Claude Code plugin 结构（plugin.json / marketplace.json / hooks.json / commands）。

## Global Constraints

- 脚本运行时只用 Python 标准库；测试只用 `unittest`（`python3 -m unittest`）
- hook 入口**永远 exit 0**，任何异常只写日志，绝不打断 Claude Code
- 所有可见文本长度用 UTF-16 码元计（`utf16_len`），不用 `len()`——上游 API 按 UTF-16 码元限长 30,720
- 卡片 ID 固定 `claude_code_status`；整卡 content ≤ 28,000 码元，单项目摘要 ≤ 3,000 码元
- 用户数据目录 `~/.claude/hiboard/`，可用环境变量 `HIBOARD_DATA_DIR` 覆盖（测试用）
- **真实授权码绝不进仓库**（开发期使用的测试码已泄露作废，文档与测试一律用占位符 `TESTCODE12345`）
- API 契约以 `docs/hiboard-api-spec.md` 为准（该文档为实测结论），字段显示映射反直觉：`summary`=列表标题、`source`=展开态主标题、`scheduleTaskName` 不显示但必填
- 卡片 Markdown 禁用任务列表（`- [x]`）与表格对齐标记（渲染端不支持）

---

### Task 1: 插件清单三件套

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Create: `hooks/hooks.json`
- Create: `.gitignore`

**Interfaces:**
- Produces: hooks.json 将 5 个事件（SessionStart / UserPromptSubmit / Stop / Notification / SessionEnd）全部指向 `${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py`（Task 6 实现该入口）

- [ ] **Step 1: 写 plugin.json**

```json
{
  "name": "hiboard-status",
  "version": "0.1.0",
  "description": "把 Claude Code 会话状态与回合摘要推送到华为负一屏（HiBoard）聚合卡片",
  "author": { "name": "imshuhao" }
}
```

- [ ] **Step 2: 写 marketplace.json**

```json
{
  "name": "hiboard-status",
  "owner": { "name": "imshuhao" },
  "plugins": [
    {
      "name": "hiboard-status",
      "source": "./",
      "description": "把 Claude Code 会话状态与回合摘要推送到华为负一屏（HiBoard）聚合卡片"
    }
  ]
}
```

- [ ] **Step 3: 写 hooks/hooks.json**

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py\"" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py\"" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py\"" } ] }
    ],
    "Notification": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py\"" } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py\"" } ] }
    ]
  }
}
```

- [ ] **Step 4: 写 .gitignore**

```
__pycache__/
*.pyc
.DS_Store
```

- [ ] **Step 5: 验证三个 JSON 可解析**

Run: `python3 -m json.tool .claude-plugin/plugin.json > /dev/null && python3 -m json.tool .claude-plugin/marketplace.json > /dev/null && python3 -m json.tool hooks/hooks.json > /dev/null && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin hooks .gitignore
git commit -m "feat: 插件清单（plugin/marketplace/hooks）"
```

---

### Task 2: 纯函数——UTF-16 长度、截断、时间格式、状态判定

**Files:**
- Create: `scripts/hiboard_hook.py`
- Create: `tests/test_hiboard_hook.py`

**Interfaces:**
- Produces:
  - `utf16_len(s: str) -> int`
  - `truncate_utf16(s: str, limit: int) -> str`（截断加 `…`，结果 ≤ limit 码元）
  - `fmt_time(ts: float, now: float | None = None) -> str`（当天 `HH:MM`，跨天 `MM-DD HH:MM`）
  - `effective_status(entry: dict, now: float | None = None) -> str`（`running|waiting` 超 2h → `"stale"`）
  - 模块常量：`CARD_ID`、`MAX_CARD_UTF16=28000`、`MAX_PROJECT_UTF16=3000`、`STALE_SECS=7200`、`PRUNE_SECS=604800`、`STATUS_META`

- [ ] **Step 1: 写失败测试**

`tests/test_hiboard_hook.py`：

```python
import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hiboard_hook as hh


class TmpDataDirTest(unittest.TestCase):
    """基类：每个测试用独立临时数据目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HIBOARD_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("HIBOARD_DATA_DIR", None)
        self._tmp.cleanup()


class TestPureHelpers(unittest.TestCase):
    def test_utf16_len(self):
        self.assertEqual(hh.utf16_len("abc"), 3)
        self.assertEqual(hh.utf16_len("中文"), 2)
        self.assertEqual(hh.utf16_len("🚀"), 2)  # 非 BMP 占 2 码元

    def test_truncate_within_limit_unchanged(self):
        self.assertEqual(hh.truncate_utf16("hello", 10), "hello")

    def test_truncate_over_limit(self):
        out = hh.truncate_utf16("a" * 100, 10)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(hh.utf16_len(out), 10)

    def test_truncate_never_splits_surrogate_pair(self):
        out = hh.truncate_utf16("🚀" * 100, 11)  # 奇数限额落在代理对中间
        self.assertLessEqual(hh.utf16_len(out), 11)
        out.encode("utf-8")  # 若截出孤立代理项这里会抛 UnicodeEncodeError

    def test_fmt_time_today_vs_older(self):
        now = time.time()
        self.assertNotIn("-", hh.fmt_time(now, now=now))
        self.assertIn("-", hh.fmt_time(now - 2 * 86400, now=now))

    def test_effective_status_stale(self):
        now = time.time()
        fresh = {"status": "running", "updated_at": now}
        old = {"status": "running", "updated_at": now - 3 * 3600}
        done_old = {"status": "done", "updated_at": now - 3 * 3600}
        self.assertEqual(hh.effective_status(fresh, now), "running")
        self.assertEqual(hh.effective_status(old, now), "stale")
        self.assertEqual(hh.effective_status(done_old, now), "done")  # 非活跃态不降级


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/shuhao/Codebase/projects/hiboard-status && python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'hiboard_hook'`

- [ ] **Step 3: 写实现**

`scripts/hiboard_hook.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（6 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: 纯函数——UTF-16 计长/截断、时间格式、状态判定"
```

---

### Task 3: 卡片渲染

**Files:**
- Modify: `scripts/hiboard_hook.py`（追加到文件末尾）
- Modify: `tests/test_hiboard_hook.py`（追加测试类）

**Interfaces:**
- Consumes: Task 2 的 `utf16_len` / `truncate_utf16` / `fmt_time` / `effective_status` / `STATUS_META` / `MAX_*` 常量
- Produces:
  - `render_content(state: dict, now: float | None = None) -> str`（整卡 markdown，≤ 28,000 码元）
  - `render_summary(state: dict, now: float | None = None) -> str`（列表态标题一句话）

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_hiboard_hook.py`：

```python
def _mkstate(**projects):
    return {"projects": projects}


class TestRender(unittest.TestCase):
    def test_render_content_orders_by_updated_desc(self):
        now = time.time()
        state = _mkstate(
            older={"status": "done", "summary": "旧的", "updated_at": now - 600},
            newer={"status": "running", "prompt": "新的", "updated_at": now},
        )
        out = hh.render_content(state, now=now)
        self.assertLess(out.index("newer"), out.index("older"))
        self.assertIn("🟢 newer — 运行中", out)
        self.assertIn("✅ older — 本轮完成", out)

    def test_render_content_prefers_summary_over_prompt(self):
        now = time.time()
        state = _mkstate(p={"status": "done", "summary": "摘要文本",
                            "prompt": "指令文本", "updated_at": now})
        out = hh.render_content(state, now=now)
        self.assertIn("摘要文本", out)
        self.assertNotIn("指令文本", out)

    def test_render_content_truncates_long_project(self):
        now = time.time()
        state = _mkstate(p={"status": "done", "summary": "长" * 5000,
                            "updated_at": now})
        out = hh.render_content(state, now=now)
        self.assertLessEqual(hh.utf16_len(out), hh.MAX_CARD_UTF16)
        self.assertIn("…", out)

    def test_render_content_empty_state(self):
        self.assertIn("暂无会话", hh.render_content({"projects": {}}))

    def test_render_summary_counts_running_and_latest(self):
        now = time.time()
        state = _mkstate(
            a={"status": "running", "updated_at": now - 10},
            b={"status": "done", "updated_at": now},
        )
        s = hh.render_summary(state, now=now)
        self.assertIn("1 个会话运行中", s)
        self.assertIn("b", s)

    def test_render_summary_empty(self):
        self.assertEqual(hh.render_summary({"projects": {}}), "Claude Code")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest tests.test_hiboard_hook.TestRender -v 2>&1 | tail -3`
Expected: `AttributeError: module 'hiboard_hook' has no attribute 'render_content'`

- [ ] **Step 3: 追加实现**

追加到 `scripts/hiboard_hook.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（12 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: 卡片渲染——聚合正文与列表态标题"
```

---

### Task 4: 状态文件管理（flock + 原子写 + 损坏恢复 + 清理）

**Files:**
- Modify: `scripts/hiboard_hook.py`（追加）
- Modify: `tests/test_hiboard_hook.py`（追加）

**Interfaces:**
- Consumes: Task 2 常量 `PRUNE_SECS`
- Produces:
  - `data_dir() -> Path`（读 `HIBOARD_DATA_DIR` 环境变量，默认 `~/.claude/hiboard`）
  - `state_path() / config_path() / log_path() -> Path`
  - `log(msg: str) -> None`（追加带时间戳一行到 push.log，自身绝不抛异常）
  - `mutate_state(mutator: Callable[[dict], None]) -> dict`（flock → 加载 → mutator 就地改 → 清理过期 → 原子写 → 返回新状态）
  - `update_project(proj: str, fields: dict) -> dict`（便捷封装：合并字段到 `state["projects"][proj]`）

- [ ] **Step 1: 追加失败测试**

```python
class TestState(TmpDataDirTest):
    def test_update_project_creates_and_merges(self):
        hh.update_project("demo", {"status": "running", "updated_at": time.time()})
        state = hh.update_project("demo", {"status": "done"})
        self.assertEqual(state["projects"]["demo"]["status"], "done")
        self.assertIn("updated_at", state["projects"]["demo"])  # 旧字段保留

    def test_corrupted_state_recovers_with_backup(self):
        hh.data_dir().mkdir(parents=True, exist_ok=True)
        hh.state_path().write_text("{not json", encoding="utf-8")
        state = hh.update_project("demo", {"status": "running",
                                           "updated_at": time.time()})
        self.assertIn("demo", state["projects"])
        self.assertTrue(hh.state_path().with_suffix(".json.bak").exists())

    def test_prunes_entries_older_than_7_days(self):
        old = time.time() - hh.PRUNE_SECS - 60
        hh.update_project("ancient", {"status": "ended", "updated_at": old})
        state = hh.update_project("fresh", {"status": "running",
                                            "updated_at": time.time()})
        self.assertNotIn("ancient", state["projects"])
        self.assertIn("fresh", state["projects"])

    def test_state_survives_roundtrip(self):
        hh.update_project("demo", {"status": "running", "updated_at": 1.0})
        on_disk = json.loads(hh.state_path().read_text(encoding="utf-8"))
        self.assertEqual(on_disk["projects"]["demo"]["status"], "running")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest tests.test_hiboard_hook.TestState -v 2>&1 | tail -3`
Expected: `AttributeError: ... has no attribute 'update_project'`

- [ ] **Step 3: 追加实现**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（16 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: 状态文件管理——flock/原子写/损坏恢复/过期清理"
```

---

### Task 5: 配置加载与推送客户端

**Files:**
- Modify: `scripts/hiboard_hook.py`（追加）
- Modify: `tests/test_hiboard_hook.py`（追加）

**Interfaces:**
- Consumes: Task 3 `render_content` / `render_summary`；Task 4 `config_path` / `log` / `mutate_state`
- Produces:
  - `load_config() -> dict | None`（缺文件/坏 JSON/`enabled:false`/缺 authCode → None）
  - `push_card(cfg: dict, summary: str, content: str) -> bool`（环境变量 `HIBOARD_DRY_RUN=1` 时只写日志不发网络请求）
  - `do_push(state: dict) -> None`（渲染 → 与 `state["last_push_hash"]` 比对去重 → 推送 → 成功后写回 hash）
  - 错误码映射常量 `ERR_HINTS` / `CP_HINTS`（取自 API 契约 §5）

- [ ] **Step 1: 追加失败测试**

```python
def _write_config(**over):
    cfg = {"authCode": "TESTCODE12345", "enabled": True}
    cfg.update(over)
    hh.data_dir().mkdir(parents=True, exist_ok=True)
    hh.config_path().write_text(json.dumps(cfg), encoding="utf-8")


class TestConfigAndPush(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        super().tearDown()

    def test_load_config_missing_file(self):
        self.assertIsNone(hh.load_config())

    def test_load_config_disabled(self):
        _write_config(enabled=False)
        self.assertIsNone(hh.load_config())

    def test_load_config_no_authcode(self):
        _write_config(authCode="")
        self.assertIsNone(hh.load_config())

    def test_load_config_ok(self):
        _write_config()
        self.assertEqual(hh.load_config()["authCode"], "TESTCODE12345")

    def test_push_card_dry_run_logs_payload(self):
        _write_config()
        ok = hh.push_card(hh.load_config(), "标题", "# 正文")
        self.assertTrue(ok)
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertIn("DRY_RUN", logtext)
        self.assertIn(hh.CARD_ID, logtext)
        self.assertIn('"data"', logtext)  # 外层 data 包装存在

    def test_do_push_without_config_is_noop(self):
        state = hh.update_project("p", {"status": "running",
                                        "updated_at": time.time()})
        hh.do_push(state)  # 不应抛异常
        self.assertFalse(hh.log_path().exists()
                         and "DRY_RUN" in hh.log_path().read_text(encoding="utf-8"))

    def test_do_push_dedupes_identical_content(self):
        _write_config()
        state = hh.update_project("p", {"status": "done", "summary": "x",
                                        "updated_at": 1700000000.0})
        hh.do_push(state)
        state = json.loads(hh.state_path().read_text(encoding="utf-8"))
        hh.do_push(state)  # 内容未变，应跳过
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertEqual(logtext.count("DRY_RUN"), 1)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest tests.test_hiboard_hook.TestConfigAndPush -v 2>&1 | tail -3`
Expected: `AttributeError: ... has no attribute 'load_config'`

- [ ] **Step 3: 追加实现**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（23 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: 配置加载与推送客户端——dry-run/去重/错误码映射"
```

---

### Task 6: 事件分发与主入口

**Files:**
- Modify: `scripts/hiboard_hook.py`（追加）
- Modify: `tests/test_hiboard_hook.py`（追加）

**Interfaces:**
- Consumes: Task 4 `update_project`；Task 5 `do_push` / `load_config` / `push_card`；Task 2 `truncate_utf16` / `MAX_PROMPT_UTF16`
- Produces:
  - `project_name(cwd: str) -> str`
  - `handle_event(evt: dict) -> None`（5 个事件分发；未知事件忽略）
  - `spawn_summarizer(evt: dict, proj: str) -> None`（`HIBOARD_NO_SUMMARY=1` 时跳过；Task 7 实现被调用的 `--summarize` 模式）
  - `main(argv) -> int`（stdin 读事件 JSON；`--summarize` / `--test-push` 子模式；**恒返回 0**）
  - 脚本可执行入口 `if __name__ == "__main__"`

- [ ] **Step 1: 追加失败测试**

```python
def _run_hook(evt: dict, extra_env=None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HIBOARD_DRY_RUN"] = "1"
    env["HIBOARD_NO_SUMMARY"] = "1"  # 分发测试不真起后台摘要进程
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "hiboard_hook.py")],
        input=json.dumps(evt), text=True, capture_output=True, env=env)


class TestDispatch(TmpDataDirTest):
    def _state(self):
        return json.loads(hh.state_path().read_text(encoding="utf-8"))

    def test_session_start_registers_running(self):
        _write_config()
        r = _run_hook({"hook_event_name": "SessionStart",
                       "session_id": "s1", "cwd": "/tmp/myproj"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "running")

    def test_user_prompt_truncates_and_sets_running(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "改" * 200})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "running")
        self.assertLessEqual(hh.utf16_len(e["prompt"]),
                             hh.MAX_PROMPT_UTF16 + len("正在处理："))

    def test_stop_sets_done_with_placeholder(self):
        _write_config()
        _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "done")
        self.assertIn("摘要生成中", e["summary"])

    def test_notification_sets_waiting_keeps_prompt(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "跑测试"})
        _run_hook({"hook_event_name": "Notification", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "waiting")
        self.assertIn("跑测试", e["prompt"])  # Notification 不清空既有内容

    def test_session_end_sets_ended(self):
        _write_config()
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "ended")

    def test_garbage_stdin_exits_zero(self):
        env = os.environ.copy()
        r = subprocess.run([sys.executable, str(SCRIPTS / "hiboard_hook.py")],
                           input="not json", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_unknown_event_exits_zero(self):
        r = _run_hook({"hook_event_name": "SomethingNew", "cwd": "/tmp/x"})
        self.assertEqual(r.returncode, 0)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest tests.test_hiboard_hook.TestDispatch -v 2>&1 | tail -3`
Expected: FAIL——脚本无 `main`，stdin 事件被忽略后 `state.json` 不存在（FileNotFoundError）

- [ ] **Step 3: 追加实现**

```python
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
```

注意：`main` 引用了 `run_summarize`（Task 7 实现）。本任务先加占位以保持可运行：

```python
def run_summarize(proj: str, transcript_path: str) -> None:
    """Task 7 实现真实逻辑；当前占位保证 --summarize 不崩。"""
    log(f"SUMMARIZE_STUB {proj}")
```

（放在 `handle_event` 之前。Task 7 会替换此函数体。）

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（30 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: 事件分发与主入口——5 事件处理/恒零退出/--test-push"
```

---

### Task 7: Stop 摘要——transcript 提取、haiku 调用、降级

**Files:**
- Modify: `scripts/hiboard_hook.py`（替换 `run_summarize` 占位，新增 `last_assistant_text` / `llm_summarize`）
- Modify: `tests/test_hiboard_hook.py`（追加）

**Interfaces:**
- Consumes: Task 4 `update_project` / `log`；Task 5 `do_push` / `load_config`；Task 2 `truncate_utf16`
- Produces:
  - `last_assistant_text(transcript_path: str) -> str`（JSONL 中最后一条 assistant 文本；文件缺失/无文本 → `""`）
  - `llm_summarize(text: str, cfg: dict) -> str | None`（调 `claude -p --model <summaryModel>`；`HIBOARD_NO_LLM=1`、超时、非零退出 → None）
  - `run_summarize(proj: str, transcript_path: str) -> None`（摘要或降级截断 → 更新状态 → 推送）

- [ ] **Step 1: 追加失败测试**

```python
def _write_transcript(path: Path, texts):
    """构造 Claude Code transcript JSONL：texts 为 assistant 文本列表。"""
    lines = []
    for t in texts:
        lines.append(json.dumps({"type": "user",
                                 "message": {"role": "user", "content": "q"}}))
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": t}]}}))
    path.write_text("\n".join(lines), encoding="utf-8")


class TestSummarize(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"
        os.environ["HIBOARD_NO_LLM"] = "1"  # 强制走降级路径

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        os.environ.pop("HIBOARD_NO_LLM", None)
        super().tearDown()

    def test_last_assistant_text_picks_last(self):
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["第一条", "最后一条"])
        self.assertEqual(hh.last_assistant_text(str(p)), "最后一条")

    def test_last_assistant_text_string_content(self):
        p = Path(self._tmp.name) / "t.jsonl"
        p.write_text(json.dumps({"type": "assistant",
                                 "message": {"content": "纯字符串"}}),
                     encoding="utf-8")
        self.assertEqual(hh.last_assistant_text(str(p)), "纯字符串")

    def test_last_assistant_text_missing_file(self):
        self.assertEqual(hh.last_assistant_text("/nonexistent/x.jsonl"), "")

    def test_last_assistant_text_skips_garbage_lines(self):
        p = Path(self._tmp.name) / "t.jsonl"
        p.write_text("garbage\n" + json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "ok"}]}}),
            encoding="utf-8")
        self.assertEqual(hh.last_assistant_text(str(p)), "ok")

    def test_run_summarize_fallback_truncation(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": "（摘要生成中…）",
                                   "updated_at": time.time()})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["工作汇报正文" * 200])
        hh.run_summarize("demo", str(p))
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertNotIn("摘要生成中", e["summary"])
        self.assertLessEqual(hh.utf16_len(e["summary"]), 500)

    def test_run_summarize_empty_transcript(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": "（摘要生成中…）",
                                   "updated_at": time.time()})
        hh.run_summarize("demo", "/nonexistent/x.jsonl")
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertEqual(e["summary"], "（本轮无文本输出）")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest tests.test_hiboard_hook.TestSummarize -v 2>&1 | tail -3`
Expected: `AttributeError: ... has no attribute 'last_assistant_text'`

- [ ] **Step 3: 实现（替换 Task 6 的 `run_summarize` 占位，并在其上方新增两个函数）**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（36 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add scripts/hiboard_hook.py tests/test_hiboard_hook.py
git commit -m "feat: Stop 摘要——transcript 提取/claude -p 调用/降级截断"
```

---

### Task 8: setup 命令与 README

**Files:**
- Create: `commands/setup.md`
- Modify: `README.md`（覆盖占位内容）

**Interfaces:**
- Consumes: Task 6 的 `--test-push` 子模式

- [ ] **Step 1: 写 commands/setup.md**

````markdown
---
description: 配置负一屏推送（获取授权码、创建配置、试推验证）
---

引导用户完成 hiboard-status 的首次配置。按顺序执行：

## 1. 确认前提

先问用户是否有华为手机且已开启负一屏。没有则说明本插件无法使用，结束。

## 2. 引导获取授权码

请用户在手机上操作：

负一屏 → 左上角头像 → 「我的」→ 右上角设置 → 动态管理 → 关联账号 → 找到「Claw 智能体」→ 获取授权码

授权码是 10-20 位字母数字。提醒用户：授权码即推送凭证，泄露者可向其负一屏推送任意内容。

## 3. 创建配置文件

拿到授权码后，写入 `~/.claude/hiboard/config.json`：

```json
{
  "authCode": "<用户提供的授权码>",
  "summaryModel": "haiku",
  "summaryTimeout": 30,
  "enabled": true
}
```

然后执行 `chmod 600 ~/.claude/hiboard/config.json`。

## 4. 试推验证

运行：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hiboard_hook.py" --test-push
```

- 成功：让用户在负一屏确认出现「配置成功」卡片，配置完成。
- 失败：读 `~/.claude/hiboard/push.log` 最后几行。常见错误：
  - `0000900034` 授权码无效 → 回到第 2 步重新获取
  - `82600013` 推送开关关闭 → 负一屏 → 动态管理 → AI 任务完成通知 → 开启两个开关
  - `82600017` 手机未联网或未登录华为账号

## 5. 说明行为

配置完成后告诉用户：此后每个 Claude Code 会话会自动更新负一屏那张卡片
（开始/运行中/等待输入/完成+摘要）；卡片是静默的，不会发通知；
全局关闭可把 config.json 的 `enabled` 改为 `false`。
````

- [ ] **Step 2: 覆盖 README.md**

````markdown
# hiboard-status

把 Claude Code 的会话状态推送到**华为负一屏**：运行中瞄一眼手机看进度，
回合结束后看到 AI 生成的工作摘要。

> **硬件门槛：需要华为手机 + 负一屏（HiBoard）。** 其他设备无法使用本插件。

## 效果

负一屏上一张聚合卡片，显示所有项目的会话状态：

```
## 🟢 dev-api — 运行中
15:40 正在处理：修复 token 刷新逻辑…

## ✅ hiboard-plugin — 本轮完成
15:38 修复了 auth 模块的并发刷新竞态，改动 3 个文件，测试全部通过。
```

卡片是静默的（负一屏 API 不发系统通知），适合「主动瞄一眼」而非「等提醒」。

## 安装

```
/plugin marketplace add imshuhao/hiboard-status
/plugin install hiboard-status@hiboard-status
```

装好后运行 `/hiboard-status:setup`，按引导获取授权码并完成配置。

## 数据流向声明

启用后，以下数据会发送到华为云端点
`hiboard-claw-drcn.ai.dbankcloud.cn`（中国·东莞）：

- 你的负一屏授权码（认证用）
- 项目目录名、会话状态、本轮指令的前 60 字
- 回合摘要（由本机 `claude -p` 生成，或最后一条回复的截断）

不想推送某些内容时，把 `~/.claude/hiboard/config.json` 的 `enabled`
设为 `false` 即可全局关闭。

## 配置项（`~/.claude/hiboard/config.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `authCode` | 必填 | 负一屏授权码 |
| `pushServiceUrl` | 华为官方端点 | 一般无需改动 |
| `summaryModel` | `haiku` | 生成回合摘要的模型 |
| `summaryTimeout` | `30` | 摘要生成超时（秒），超时降级为截断 |
| `enabled` | `true` | 全局开关 |

## 上游 API

负一屏推送接口的完整契约（实测版）见
[docs/hiboard-api-spec.md](docs/hiboard-api-spec.md)。

## License

MIT
````

- [ ] **Step 3: 验证 setup.md frontmatter 可解析**

Run: `head -3 commands/setup.md`
Expected: 输出以 `---` 开头且含 `description:` 行

- [ ] **Step 4: Commit**

```bash
git add commands/setup.md README.md
git commit -m "docs: setup 引导命令与 README（安装/数据流向/配置）"
```

---

### Task 9: 本地安装与端到端验证

**Files:**
- 无新文件；真机验证 + push GitHub

前置：需要用户提供**新的**授权码（旧测试码已泄露，本任务开始前提醒用户在负一屏重新生成）。

- [ ] **Step 1: 全量测试通过**

Run: `python3 -m unittest tests.test_hiboard_hook -v 2>&1 | tail -3`
Expected: `OK`（36 个测试）

- [ ] **Step 2: 手工模拟一轮事件（dry-run）**

```bash
export HIBOARD_DATA_DIR=$(mktemp -d) HIBOARD_DRY_RUN=1 HIBOARD_NO_SUMMARY=1
mkdir -p "$HIBOARD_DATA_DIR"
echo '{"authCode":"TESTCODE12345","enabled":true}' > "$HIBOARD_DATA_DIR/config.json"
echo '{"hook_event_name":"SessionStart","session_id":"s1","cwd":"/tmp/demo"}' | python3 scripts/hiboard_hook.py
echo '{"hook_event_name":"Stop","session_id":"s1","cwd":"/tmp/demo","transcript_path":"/nonexistent"}' | python3 scripts/hiboard_hook.py
cat "$HIBOARD_DATA_DIR/push.log" | grep -c DRY_RUN
unset HIBOARD_DATA_DIR HIBOARD_DRY_RUN HIBOARD_NO_SUMMARY
```

Expected: grep 输出 `2`（SessionStart 与 Stop 各推一次）

- [ ] **Step 3: 本地安装插件**

在 Claude Code 中执行：

```
/plugin marketplace add /Users/shuhao/Codebase/projects/hiboard-status
/plugin install hiboard-status@hiboard-status
```

Expected: 安装成功，`/hooks` 中可见 5 个事件挂载

- [ ] **Step 4: 用户配置真实授权码**

提醒用户先在负一屏**重新生成**授权码，然后运行 `/hiboard-status:setup` 完成配置与试推。
Expected: 用户手机负一屏出现「配置成功」卡片

- [ ] **Step 5: 真实会话端到端**

新开一个 Claude Code 会话，随便执行一个小任务后结束回合。
Expected: 负一屏卡片依次出现「运行中」→「本轮完成 + 摘要」；
`~/.claude/hiboard/push.log` 无 `PUSH_FAIL`

- [ ] **Step 6: Push GitHub**

```bash
git push origin main
```

Expected: 推送成功；此后任何人可用 `/plugin marketplace add imshuhao/hiboard-status` 安装
