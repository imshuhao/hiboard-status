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
/plugin install hiboard-status@imshuhao
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
