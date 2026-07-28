# 同目录多会话（格子模型）· v0.4.0 · 设计文档

日期：2026-07-28 · 状态：已批准（探索先行：本机实证 + 官方 hooks/sessions 文档）

## 问题

同目录并发会话共享一个项目条目，「最后写者赢」：A 在跑、B 回合结束，
卡片显示「本轮完成」——状态经常撒谎。实测用户此刻就有 2 个会话并行于
同一目录，需求真实。

## 探索结论（设计依据）

1. transcript 位于 `~/.claude/projects/<slug>/<sid>.jsonl`；`--resume` 沿用
   id，`/clear` 铸新 id，`/compact` 不换 id 但触发 `SessionStart(source=compact)`；
   `SessionEnd(reason∈{clear,resume,logout,…})` ≠「用户走了」
2. hook 载荷无任何 interactive/helper 标识字段；幽灵会话（无 transcript、
   无用户回合）唯一鲁棒过滤器 = 「格子生于首次 UserPromptSubmit」
3. `~/.claude/sessions/<pid>.json` 活会话注册表（退出即删，pid 可校验）；
   `-p` 会话也标 interactive → 只可作活性信号，未文档化 → best-effort
4. Stop 载荷自带 `last_assistant_message` → transcript 解析降级为 fallback
5. `claude -p` 支持 `--bare --no-session-persistence`（本机 2.1.212 已验证）

## 设计

### 状态模型

```
projects[proj] = {
  cells: { sid: {status: running|waiting|done, prompt, updated_at} },
  summary, summary_ts,        # 项目级不变
  updated_at, cwd
}
```

- 格子**生于首次 UserPromptSubmit**（SessionStart 只登记 session→项目映射）
- Stop → 本格 done（无格则建）；Notification → 本格 waiting（仅当
  `notification_type` 缺失或 ∈ {permission_prompt, idle_prompt,
  elicitation_dialog, agent_needs_input}，且格子已存在）
- SessionEnd → 删本格（无格 no-op）。v0.2.5 两个 ownership 守卫**退役**
- 格子清理：>2h stale 不渲染；>24h 删除；注册表可读时，sid 不在活表且
  格龄 >60s 即删（读失败静默退回纯 stale；`HIBOARD_NO_REGISTRY=1` 禁用）
- ~~旧扁平条目按 legacy 路径渲染~~（0.4.1 用户裁定移除：无 cells 条目
  一律按「无活格」处理——有摘要显示已结束，否则仅标题行）

### 渲染

- 项目头部状态 = 活格子按 **waiting > running > done** 取最高优先；
  多格标「N 个会话」；无活格 → 有 summary 按「已结束」显示，否则不显示格行
- 格子分行（≤3，优先级+新近排序），多格时行首加各自 emoji；
  running/waiting 行时间戳带「自」前缀；「↳ 上轮」行沿用（头部非 done 时）
- 列表标题「N 个会话运行中」改为跨项目 running 格子计数

### 摘要链路

- Stop 时文本取载荷 `last_assistant_message`（截尾 8000 字符）经 stdin 管道
  传给摘要进程；用户上下文 = 本格 prompt 经 argv；载荷缺失时退回 transcript
  解析（`last_turn` 保留为 fallback）
- `claude -p` 加 `--bare --no-session-persistence`（不写假 transcript、
  结构性防递归）；旧版 CLI 不识别 flag 时去 flag 重试一次；
  `HIBOARD_SUMMARIZING` 环境守卫保留为双保险
- `--summarize` argv 变更：`<proj> <turn_ts> <user_prompt> [transcript_path]`
  + stdin 文本（内部接口，无兼容负担）

### 验收

现有 92 测试语义等价通过（多会话相关的重写）+ 新增：双会话并行状态互不
覆盖、幽灵 Start+End 零痕迹、waiting 优先级、legacy 渲染回退、注册表清理、
stdin 摘要链路、--bare 降级重试。
