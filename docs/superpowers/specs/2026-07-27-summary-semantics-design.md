# 摘要语义重构（v0.2.3）· 设计文档

日期：2026-07-27 · 状态：已批准（方案 A；用户确认跳过独立 plan 直接实现）

## 问题

高频会话中摘要几乎全被丢弃：Stop 后台摘要需 10-30 秒，用户在此窗口内提交
新指令即清空占位符，迟到摘要按 v0.2.2 竞态守卫只能丢弃（实测 9/9 全 stale）。
用户诉求：活跃会话也要「知道跑到哪了」。

## 根因与方案

`summary` 字段身兼两职（done 正文 + 可被清空的过期数据），竞态是两职打架
的产物。方案：**改语义而非加字段**——`summary` 重定义为「最近一个已完成
回合的总结」，则摘要永远不会「迟到」，守卫失去存在土壤，整个删除。

否决项：显式 last_summary 双字段（同效果但多一套清空规则，臃肿版 A）；
回合小日志（卡片高度膨胀，违背「瞄一眼」初衷）。

## 决策记录

| 决策点 | 结论 |
|---|---|
| 写入方 | `summary` 只有两个写入方：Stop（占位符）与摘要进程（正文） |
| 清空 | UserPromptSubmit / SessionStart / SessionEnd / Notification 一律不碰 summary（SessionStart 保留跨会话摘要，经用户确认） |
| 摘要进程 | 只写 `summary`/`summary_ts`，绝不碰 status / prompt / updated_at |
| 乱序防护 | Stop 时刻 `turn_ts` 随 spawn 传入，写入条件 `turn_ts >= state.summary_ts`；缺省 0 表示总是可写 |
| 渲染 | running/waiting 正文下追加 `↳ 上轮：<summary>`（截 300 码元，硬换行 `  \n`，渲染器不支持时优雅降级为同行）；done/ended/stale 不变 |
| 排序/stale | 迟到摘要不更新 updated_at，不影响项目排序与 stale 判定 |
| 过期 | 摘要随项目 7 天清理消失，无独立过期逻辑 |
| 推送 | 摘要落地触发一次哈希变化推送——把「上轮」行刷上卡片即需求本身 |

## 测试

改造 v0.2.2 守卫测试（丢弃 → 落入上轮行、不碰 status），新增：乱序拒写、
running 渲染上轮行、UserPromptSubmit/SessionStart 保留摘要。
