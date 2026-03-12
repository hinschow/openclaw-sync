# OpenClaw Sync 快速上手（给另一个实例）

本仓库用于快速复刻当前 OpenClaw 的工作能力：
- 早报/晚报
- Polymarket 模拟盘日报
- skills 与脚本
- 运行方式与交接文档

## 1. 拉取仓库

```bash
git clone git@github.com:hinschow/openclaw-sync.git
cd openclaw-sync
```

## 2. 部署到 OpenClaw workspace

如果目标机器默认 workspace 是 `~/.openclaw/workspace`：

```bash
rsync -av --delete ./ ~/.openclaw/workspace/
```

> 或直接把本仓库作为 workspace 使用（按你的部署习惯）。

## 3. 必要配置

编辑 `~/.openclaw/openclaw.json`，至少确认：

- `channels.telegram.enabled = true`
- `channels.telegram.botToken` 已配置
- `tools.profile` 不要是仅消息模式（需可执行脚本）
- `cron.enabled = true`

改完重启：

```bash
openclaw gateway restart
```

## 4. 验证关键任务

查看任务：

```bash
openclaw gateway call cron.list --json
```

手动触发模拟盘日报：

```bash
openclaw gateway call cron.run --json --timeout 120000 --params '{"id":"9f51721b-a66c-4496-aad6-bcf7f04f3ac6"}'
```

手动触发早报：

```bash
openclaw gateway call cron.run --json --timeout 180000 --params '{"id":"d390ffb5-4bd1-40e0-aef4-cfe820c48aed"}'
```

手动触发晚报：

```bash
openclaw gateway call cron.run --json --timeout 180000 --params '{"id":"c58302eb-54d8-4750-9fd6-460d4faa9506"}'
```

## 5. 关键文档

- `docs/openclaw-sync-handover.md`：交接说明（任务、逻辑、方法）
- `docs/cron-jobs.snapshot.json`：当前定时任务快照
- `AGENTS.md` / `SOUL.md` / `USER.md` / `MEMORY.md`：行为与长期上下文

## 6. 常见问题

### Q1: 报 "Delivering to Telegram requires target <chatId>"
说明用了 `delivery.announce` 但未配置 target。当前仓库采用 `delivery.mode=none` + 任务内 `message tool` 发送，避免版本兼容问题。

### Q2: 报无命令执行能力
检查 `tools.profile`，别用纯 `messaging`。需要允许脚本执行。

### Q3: cron.run 超时
默认 10s 不够，增加 `--timeout` 到 120000/180000。

## 7. 安全建议

- 不要把 `~/.openclaw/openclaw.json` 直接入库（含 token/key）
- 发现 `.token`、`.env`、密钥文件立即加入 `.gitignore`
- 对外分享前先做敏感词扫描
