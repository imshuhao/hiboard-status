# 包结构重构（v0.2.6）· 设计文档

日期：2026-07-27 · 状态：已批准（方案 A：薄入口 + 包拆分；纯重构不改行为）

## 动机

`scripts/hiboard_hook.py` 已 650 行（测试 734 行），路线图全是加法
（后台推送、--status、多机）。8 个分节注释就是现成的模块边界，趁边界
清晰时拆分。

## 结构

```
scripts/
  hiboard_hook.py     # ~40 行薄入口：argv 分发、恒零保证、递归守卫
  hiboard/
    __init__.py       # 门面：re-export 全部公共名 + 子模块
    const.py          # VERSION、卡片 ID、各长度上限、STALE/PRUNE、占位符
    text.py           # utf16 计长/截断、slugify
    render.py         # STATUS_META、fmt_time、effective_status、render_*
    store.py          # 路径、日志（轮转）、mutate_state、update_project
    push.py           # 配置、错误码表、push_card、do_push、配额熔断
    summarize.py      # transcript 解析、llm_summarize、run_summarize
    events.py         # resolve_project、handle_event、spawn_summarizer
    ondemand.py       # cmd_push、cmd_test_push
tests/
  helpers.py          # TmpDataDirTest、_write_config、_run_hook 等共享件
  test_{text,render,store,push,events,summarize,ondemand}.py  # 镜像拆分
```

## 关键决策

| 决策点 | 结论 |
|---|---|
| 入口路径 | 不变（hooks.json / SKILL.md / setup.md 零改动）；Python 自动把脚本目录加入 sys.path，包放旁边即可 import |
| 依赖方向 | const/text/store 无内部依赖 → render(text) → push(store,render,text) → summarize/events/ondemand（无环） |
| 门面 | `import hiboard as hh` 暴露全部公共名，测试 `hh.` 前缀基本零改动；子模块经 `hh.push` 等可达（mock patch 用） |
| 自我重启路径 | `events.ENTRY = 包父目录/hiboard_hook.py`（不能用模块 `__file__`），加测试钉住 |
| 版本号 | 收敛到 `const.VERSION`，User-Agent 引用之；plugin.json 仍手动同步 |
| 验收 | 纯移动：74 个测试语义不变全部通过；不夹带任何行为变更 |
