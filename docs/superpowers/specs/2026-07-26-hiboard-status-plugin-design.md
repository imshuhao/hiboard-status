# hiboard-status —— Claude Code 会话状态推送到华为负一屏 · 设计文档

日期：2026-07-26
状态：待审阅
上游依赖：[HiBoard 推送 API 契约规范](../../hiboard-api-spec.md)（已实测验证，随仓库分发）

## 1. 背景与目标

华为负一屏提供一个可通过 HTTP API 推送 Markdown 卡片的入口（HiBoard 服务动态）。
本插件把 Claude Code 的会话状态接入这张卡片：**运行中可瞄一眼手机看进度，
回合结束后可看到 LLM 生成的工作摘要**。

已确认的产品决策（与用户逐项确认）：

| 决策点 | 结论 |
|---|---|
| 通知形态 | 静默卡片即可，不配额外通知渠道（负一屏 API 不发系统通知，实测确认） |
| 卡片组织 | **全局一张卡**，正文聚合多个项目/会话的状态 |
| 更新粒度 | 回合级 + 等待提醒（SessionStart / UserPromptSubmit / Stop / Notification / SessionEnd） |
| 结果内容 | `claude -p --model haiku` 生成两三句摘要；失败降级为最后一条回复截断 |
| 落地形态 | 正式插件结构，单仓库自带 marketplace，GitHub 分发 |

## 2. 非目标

- 不做双向控制（卡片只读，不能从手机操作 Claude Code）
- 不做通知触达（负一屏无此能力）
- 不支持非华为设备
- 不做多设备/多账号推送

## 3. 架构（已选定：单脚本多入口）

所有 hook 事件调用同一个 `scripts/hiboard_hook.py`，事件名从 stdin JSON 的
`hook_event_name` 读取。备选的常驻 daemon 方案（推送去重最彻底但需管理进程
生命周期）与每 hook 独立脚本方案（逻辑分散）均被否决——每回合 2~3 次推送的
量级配不上它们的复杂度。

### 3.1 数据流

```
hook 触发，stdin 传 JSON（hook_event_name / session_id / cwd / transcript_path）
  → flock 锁定 ~/.claude/hiboard/state.json
  → 更新本会话条目（按项目名为键）
  → 读全量状态，渲染完整 markdown
  → 与上次推送内容 hash 比对，相同则跳过
  → 推送覆盖全局卡（scheduleTaskId = "claude_code_status"）
  → 无论成败 exit 0
```

`Stop` 事件特殊：先同步推一次「已完成 · 摘要生成中」，同时 fork 后台子进程调
`claude -p --model haiku` 生成摘要，完成后再推一次带摘要的版本。终端零阻塞。

摘要输入：从 `transcript_path`（JSONL）提取最后一条 assistant 文本消息，
超过 8,000 字符时取尾部截断，提示词要求输出两三句中文摘要（做了什么、结果如何）。
后台子进程同样走 flock → 渲染 → 推送流程，与并发的其他 hook 触发天然兼容。

### 3.2 仓库结构

```
hiboard-status/                      # GitHub 仓库 = 插件 = marketplace
├── .claude-plugin/
│   ├── plugin.json                  # 插件清单（名称、版本、描述）
│   └── marketplace.json             # marketplace 清单，plugins.source = "./"
├── hooks/
│   └── hooks.json                   # 5 个事件均指向 hiboard_hook.py
├── scripts/
│   └── hiboard_hook.py              # 全部逻辑，~300 行，纯标准库
├── commands/
│   └── setup.md                     # /hiboard-status:setup 引导配置
├── docs/
│   ├── hiboard-api-spec.md          # 上游 API 契约（实测版）
│   └── superpowers/specs/           # 本文档
└── README.md                        # 硬件门槛声明 + 安装 + 授权码指南
```

用户数据与插件分离（插件目录进 git，敏感数据绝不进）：

```
~/.claude/hiboard/
├── config.json      # authCode 等，chmod 600
├── state.json       # 会话聚合状态
└── push.log         # 推送日志
```

## 4. Hook 映射

| 事件 | 卡片状态 | 动作 |
|---|---|---|
| `SessionStart` | 🟢 已启动 | 注册会话，项目名取 cwd basename |
| `UserPromptSubmit` | 🟢 运行中 | 记录本轮指令摘要（前 60 字） |
| `Stop` | ✅ 本轮完成 | 两段推送：占位 → 后台 haiku 摘要 → 更新 |
| `Notification` | 🟡 等待输入 | Claude 空等输入/权限时 |
| `SessionEnd` | ⚪ 已结束 | 标记结束，条目保留供回看 |

## 5. 状态文件 schema

```json
{
  "last_push_hash": "sha256…",
  "projects": {
    "dev-api": {
      "session_id": "…",
      "cwd": "/Users/shuhao/Codebase/projects/dev-api",
      "status": "running | waiting | done | ended",
      "prompt": "本轮指令前 60 字",
      "summary": "haiku 摘要或降级截断（markdown）",
      "updated_at": 1785050000
    }
  }
}
```

- **按项目名为键**：同项目开第二个会话顶掉第一个的显示（换取卡片按项目组织）。
- **僵尸态**：`running`/`waiting` 超 2 小时未更新 → 渲染为「⚪ 状态未知」；
  超 7 天的条目从文件清除。
- **并发**：`fcntl.flock` 串行化读改写；写入用 temp + rename 保证原子。

## 6. 卡片渲染

推送字段（依据 API spec §6.2 的显示映射，字段名与显示位置不对应，勿凭直觉改）：

| 字段 | 值 |
|---|---|
| `scheduleTaskId` | 固定 `claude_code_status` |
| `summary` | 列表态标题，动态生成：`2 个会话运行中 · dev-api 刚完成` |
| `source` | `Claude Code`（展开态主标题） |
| `result` | `最后更新 15:42`（展开态副标题） |
| `content` | 见下 |
| `scheduleTaskName` | `Claude Code Status`（不显示，仍必填） |

`content` 格式：

```markdown
## 🟢 dev-api — 运行中
`15:40` 正在处理：修复 token 刷新逻辑…

---

## ✅ hiboard-plugin — 本轮完成
`15:38` 修复了 auth 模块的并发刷新竞态，改动 3 个文件，测试全部通过。
```

预算：单项目摘要 ≤ 3,000 UTF-16 码元，整卡 ≤ 28,000（上限 30,720，留余量）。
长度一律用 `utf16_len()`（`len(s.encode("utf-16-le")) // 2`）计算，不用 `len()`。
项目按 `updated_at` 倒序。Markdown 避免任务列表与表格对齐语法（渲染端不支持）。

## 7. 配置

`~/.claude/hiboard/config.json`（chmod 600）：

```json
{
  "authCode": "<必填>",
  "pushServiceUrl": "(可选，默认华为端点)",
  "summaryModel": "haiku",
  "summaryTimeout": 30,
  "enabled": true
}
```

`enabled: false` = 全局关闭开关，无需卸载插件。

**安全注意**：本项目开发期间使用的测试授权码已在对话/shell 历史中暴露，
文档与示例中不得出现真实授权码；用户配置时应引导其获取新码。

## 8. setup 命令

`commands/setup.md` 提供 `/hiboard-status:setup`，引导完成：

1. 提示负一屏取码路径（负一屏 → 头像 → 我的 → 设置 → 动态管理 → 关联账号 → Claw 智能体）
2. 创建 `~/.claude/hiboard/config.json` 并 `chmod 600`
3. 试推一张验证卡；失败时按 API spec §5 的错误码映射给出准确指引
   （如 `0000900034` → 授权码无效，重新获取）

## 9. 错误处理

| 情形 | 行为 |
|---|---|
| 推送失败（网络/错误码） | 写 `push.log`，exit 0，绝不打断 Claude Code |
| haiku 摘要失败/超时（30s） | 降级：transcript 最后一条 assistant 消息截断 |
| 配置缺失/无 authCode | 静默退出（未配置时插件零副作用） |
| `state.json` 损坏 | 改名 `.bak` 保留，重建空状态 |
| API 错误码 | 按 spec §5 映射写日志（`0000500002` 超长、`0000900034` 无效码等） |

原则：**旁路系统，任何故障不得影响主流程。**

## 10. 分发

单仓库自带 marketplace（同 `pbakaus/impeccable` 模式）。用户安装：

```
/plugin marketplace add imshuhao/hiboard-status
/plugin install hiboard-status@imshuhao
```

README 必须包含：华为手机硬件门槛（第一行）、安装两条命令、授权码获取流程、
数据流向声明（任务摘要将发送到华为云端点，含隐私提示）。

版本号维护在 `plugin.json`，用户通过 marketplace update 升级。
稳定后可选提交 `anthropics/claude-plugins-community`。

## 11. 测试

1. **单元**：伪造各事件 stdin JSON 管道给脚本，`--dry-run` 断言状态文件与渲染输出
2. **契约**：真推一次到负一屏（`claude_code_status` 首推即验证）
3. **端到端**：装插件开真实会话，验证 SessionStart → Stop 全链路 + 手机肉眼确认

## 12. 已知限制

- 静默：用户不会被提醒，需主动查看负一屏
- 卡片删不掉（API 无删除接口）：本插件固定单卡设计已将此影响降到最小
- 同项目多会话只显示最新一个
- `Notification` hook 在 bypassPermissions 模式下主要由空闲等待触发，权限等待较少见
