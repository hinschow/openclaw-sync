# OpenClaw 同步与交接文档

更新时间：2026-03-12 (UTC)

## 1) 当前项目概览

本工作区主要包含以下模块：

- `polymarket-tracker/`：模拟盘与策略项目（核心交易/回测/持仓数据）
- `scripts/`：日报与辅助脚本（如 `enhanced_news.py`）
- `skills/`：已安装/开发中的技能目录
- `AGENTS.md / SOUL.md / USER.md / MEMORY.md`：助手行为与长期记忆配置
- `docs/cron-jobs.snapshot.json`：当前网关定时任务快照（由 `openclaw gateway call cron.list --json` 导出）

## 2) 早报 / 晚报 / 模拟盘日报

### 2.1 早报任务
- Cron ID: `d390ffb5-4bd1-40e0-aef4-cfe820c48aed`
- 名称：早报 - 金融&区块链&政治军事新闻
- 时间：每天 08:00 (Asia/Shanghai)
- 逻辑：执行 `scripts/enhanced_news.py` + `web_fetch` 补充来源 + 发送 Telegram

### 2.2 晚报任务
- Cron ID: `c58302eb-54d8-4750-9fd6-460d4faa9506`
- 名称：晚报 - 金融&区块链&政治军事新闻 (19:00)
- 时间：每天 19:00 (Asia/Shanghai)
- 逻辑：执行 `scripts/enhanced_news.py` + `web_fetch` 补充来源 + 发送 Telegram

### 2.3 模拟盘日报
- Cron ID: `9f51721b-a66c-4496-aad6-bcf7f04f3ac6`
- 名称：Polymarket 模拟盘日报
- 时间：每天 10:00 (Asia/Shanghai)
- 逻辑：读取 `polymarket-tracker/data/sim_portfolio.json`，生成资产/胜率/持仓摘要并发送 Telegram

## 3) 当前定时任务与配置

- 全量任务快照：`docs/cron-jobs.snapshot.json`
- 当前 cron 发送模式：`delivery.mode = none`（由任务内调用 `message tool` 发 Telegram）
- 说明：该模式避免了某些版本下 `announce` 缺少 target 字段导致的投递错误

## 4) 当前技能与工具组织

### 4.1 本地技能目录
- 路径：`skills/`
- 用途：策略监控、行情信息、自动化任务等

### 4.2 运行相关文档
- `AGENTS.md`：工作规范与安全边界
- `SOUL.md`：助手风格（高效、直接、中文优先）
- `TOOLS.md`：本地工具备注（可持续补充）

## 5) 当前“思考/执行方式”（供另一实例学习）

> 这是可迁移的工作方法，不包含平台私有系统提示。

1. **先定位链路再修复**：区分“任务未触发 / 执行失败 / 投递失败”三层。
2. **默认低 token 输出**：固定模板、固定字段，减少冗长解释。
3. **优先稳定性**：定时任务失败时，先恢复可用，再做结构优化。
4. **多任务分层**：主会话负责沟通与调度；长任务用后台/子会话。
5. **可观测优先**：每次改动后保留可核对证据（状态输出/快照文件）。

## 6) GitHub 同步建议（给另一个 OpenClaw）

建议同步以下内容：

- 项目代码：`polymarket-tracker/`, `scripts/`, `skills/`
- 运行文档：`AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `MEMORY.md`
- 交接文档：`docs/openclaw-sync-handover.md`, `docs/cron-jobs.snapshot.json`

建议忽略：
- 会话缓存、日志、临时文件、密钥

可参考 `.gitignore`（已添加）来避免误传敏感文件。

## 7) 安全注意

- `openclaw.json` 可能包含 token/API key，**不建议直接入库**。
- 若曾外发终端输出，建议轮换相关 token/API key。
