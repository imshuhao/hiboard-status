# 全量代码审查修复（v0.2.5）· 记录

日期：2026-07-27 · 来源：subagent 全项目审查（442c8fb..f720a40 + HEAD 全量）

## Critical

**幽灵会话 Start+End 连击绕过守卫**（审查员真机复现）：幽灵的 SessionStart
先接管条目（session_id 变成幽灵），其 SessionEnd 便能通过 v0.2.4 的守卫。
修复：SessionStart 对属于其他会话的既有条目只登记映射、不接管——幽灵由此
彻底隐形（连 running 闪现也没有了），真实新会话在首次 UserPromptSubmit 接管。
v0.2.4 spec 中「幽灵仍会短暂闪现」的已知局限一并消除。

## Important

| 问题 | 修复 |
|---|---|
| 无配额熔断，每回合 ~3 次配额 | `0000400001` 时写入 `quota_blocked_until`（次日零点），do_push 熔断期跳过；Stop 的占位推送在会有摘要进程跟进时省略（每回合 ~2 次）。CLI 模式不受熔断限制 |
| do_push 哈希检查/写回在锁外，多进程可双推、旧盖新 | 渲染+哈希认领移入 flock 临界区（`push_claim`，60 秒过期防进程被杀卡死），推送成功后锁内写回 `last_push_hash` |
| 纯中文主题 slug 全部塌缩为 `topic`，主题卡互相覆盖 | 含非 ASCII 的主题名附加 md5 前 6 位；纯 ASCII 主题 slug 不变（不破坏既有卡） |

## Minor（本轮已修）

push.log 512KB 轮转（留 500 行）；hook 路径推送超时 15s→6s；摘要压掉换行
（防转录原文伪造卡片分节）；推送失败回滚轮转卡位时间戳（带并发占用检查）；
SUMMARY_OUTDATED 日志区分条目缺失；README 注明主机需 macOS/Linux（fcntl）。

## Minor（接受不修）

- 未配置 + stdin 畸形时仍会创建 push.log（保留调试能力）
- 「上轮」行使 单项目预算 3000 实际上限 ~3300（总量 28000 下无害）
- 并发不同内容的推送到达服务端的顺序仍无保证（修复代价是锁内做网络请求，不可接受）

测试 65 → 74。
