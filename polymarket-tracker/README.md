# Polymarket 跟单交易工具

跟踪 Polymarket Top 50 交易员行为，提炼策略，自动小额交易验证。

## 架构

```
├── config.py          # 配置（API keys, 参数）
├── data/              # 数据存储
├── collectors/        # 数据采集
│   ├── leaderboard.py # 排行榜 Top 50 交易员
│   ├── trades.py      # 交易员交易记录
│   └── markets.py     # 可交易市场
├── strategy/          # 策略引擎
│   ├── analyzer.py    # 交易行为分析
│   └── signals.py     # 跟单信号生成
├── executor/          # 交易执行
│   └── trader.py      # 下单（模拟/实盘）
├── simulator/         # 模拟数据 & 回测
│   ├── mock_data.py   # 模拟数据生成
│   └── backtest.py    # 策略回测
└── main.py            # 入口
```

## 阶段

1. **Phase 1**: 数据采集 + 模拟数据验证
2. **Phase 2**: 策略回测
3. **Phase 3**: 小额实盘
