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
