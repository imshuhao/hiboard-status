# hiboard-status

把 Claude Code 的会话状态推送到**华为负一屏**：运行中瞄一眼手机看进度，
回合结束后看到 AI 生成的工作摘要。

> **硬件门槛：需要华为手机 + 负一屏（HiBoard）。** 其他设备无法使用本插件。
> 主机 macOS/Linux 完整支持；Windows 做了兼容处理但未经真机验证，
> 且需保证 `python3` 在 PATH 中。

## 效果

负一屏上一张聚合卡片，显示所有项目的会话状态：

```
## 🟢 dev-api — 运行中
15:40 正在处理：修复 token 刷新逻辑…
↳ 上轮：排查出 401 来自过期 refresh token，已定位到中间件。

## ✅ hiboard-plugin — 本轮完成
15:38 修复了 auth 模块的并发刷新竞态，改动 3 个文件，测试全部通过。
```

运行中的项目也能看到上一轮做了什么（「上轮」行）——高频对话不丢进度。

卡片是静默的（负一屏 API 不发系统通知），适合「主动瞄一眼」而非「等提醒」。

同一目录开多个会话也能如实显示——每个会话一行，等待输入的排最前：

```
## 🟡 fortrip-ai — 2 个会话
自 14:10 🟡 要继续吗？
自 14:02 🟢 正在处理：修复登录重定向
↳ 上轮：重构了 token 刷新逻辑，测试通过。
```

## 按需推送

除了自动状态卡，还可以让 Claude 把任意内容推到负一屏——直接说：

> 把这份日报推到负一屏

推送内容默认写入 **3 个轮转卡位**（保留最近 3 次，自动复用最旧的，永不堆积）；
周期性内容（如每日日报）可指定主题固定一张卡。详见
[skills/push-to-hiboard/SKILL.md](skills/push-to-hiboard/SKILL.md)。

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
| `summaryMinChars` | `120` | 回复不超过此长度（UTF-16 码元）时原文即摘要，不调 LLM |
| `manualSlots` | `3` | 按需推送的轮转卡位数量 |
| `pushDebounce` | `3` | 状态推送合并窗口（秒），连发事件只推最终状态 |
| `cardSuffix` | 空 | 多台电脑共用一个华为账号时设为各机标识（如 hostname），各机各卡 |
| `enabled` | `true` | 全局开关 |

## 诊断

```bash
python3 ~/.claude/plugins/marketplaces/imshuhao/scripts/hiboard_hook.py --status
```

一条命令查看：配置状态、配额熔断、各项目状态、名下的永久主题卡、
轮转卡位与今日推送失败数。

## 上游 API

负一屏推送接口的完整契约（实测版）见
[docs/hiboard-api-spec.md](docs/hiboard-api-spec.md)。

## License

Apache 2.0
