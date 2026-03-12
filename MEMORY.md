# MEMORY.md - 长期记忆

## 基本信息
- 我叫超级大脑 🧠
- 用户：Andre (@andrewingxx, Telegram id: 5689003327)，时区 GMT+8
- 风格：中文为主，效率工具型，简洁直接
- 首次上线：2026-02-13

## 重要教训
- 长时间任务（数据采集、回测）必须用 sessions_spawn 子 agent 执行，不要在主 session poll
- 主 session 只负责对话和结果转发，保持 context 轻量
- Andre 非常在意 token 消耗，任何持续运行的内容都要放后台/子 agent
- 云逸 API 有单点故障风险：2026-03-01 宕机18小时（model_not_supported + service_unavailable）
- cron 里搜索工具不一定可用（无 Brave key），新闻风控改用 web_fetch 直接抓新闻源
- Andre 核心要求：主动关注持仓相关新闻，不要被动等止损
- Musk 推文套利策略是最大亏损来源（16笔亏$147），需要关掉

## 定时任务
- 早报 (08:00 GMT+8): d390ffb5-4bd1-40e0-aef4-cfe820c48aed
- 晚报 (19:00 GMT+8): c58302eb-54d8-4750-9fd6-460d4faa9506（曾因 billing error 失败）
- 模拟盘日报 (10:00 GMT+8): 9f51721b-a66c-4496-aad6-bcf7f04f3ac6

## 进行中的项目
- Polymarket 跟单交易工具：v10 策略运行中
- 核心方向：短线为主(70%)，长线为辅(20%)，快进快出赚差价
- v5模块: 实时跟单 + 价格动量分析 + 事件驱动过滤
- v8新增: 短线三策略（催化剂/回归/到期收割）+ 高交易量扫描
- 教训: 体育盘绝对不碰（亏了$728），大仓位要控制（$50上限）
- 模拟盘: 每2-4小时检查，10:00发完整日报
- 下一步: 短线比例提高到70%，长线精简到10笔

## 已安装技能
- polymarket-odds, crypto-market-data, crypto-gold-monitor
- cls-news-scraper, ai-news-collectors, openclaw-backup, deep-scraper
