"""
策略 v3 - 基于数据洞察的精细化跟单
核心发现：
1. 体育/电竞市场大户集体巨亏（-4900万），绝对不能跟
2. 政治市场胜率68%但PnL为负（大户仓位管理差），可以小仓位跟
3. 价格预测市场胜率~50%但PnL为正（说明赢的时候赚得多）
4. 高胜率交易员（>55%）只有7人，应该重点跟这些人
5. anoin123 是最赚钱的（+78万），sovereign2013 胜率80%

策略：
- 只跟高胜率+盈利的精选交易员（~7人）
- 过滤体育/电竞市场
- 过滤短期价格预测
- 政治/事件市场加权
- 动态仓位管理
"""

import json
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# 必须过滤的市场类型
BLACKLIST_KEYWORDS = [
    "up or down", "up/down",
    "tweets from", "posts from",
    "game 1 winner", "game 2 winner", "game 3 winner",
    "game 4 winner", "game 5 winner",
]

# 体育/电竞关键词（过滤）
SPORTS_KEYWORDS = [
    "win on 2026", "win on 2025", "vs.", "vs ",
    "nba", "nfl", "nhl", "mlb", "ncaa", "lol:", "t20",
    "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "copa del", "ligue 1",
    "open:", "grand prix", "world cup",
]

# 高价值市场关键词（加权）
HIGH_VALUE_KEYWORDS = [
    "shutdown", "trump", "election", "president", "congress",
    "fed", "interest rate", "inflation", "recession",
    "war", "invasion", "nato", "sanctions",
    "etf", "approve", "ban",
]


def load_data():
    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)
    with open(os.path.join(DATA_DIR, "real_trader_trades.json")) as f:
        trades = json.load(f)
    with open(os.path.join(DATA_DIR, "real_trader_positions.json")) as f:
        positions = json.load(f)
    with open(os.path.join(DATA_DIR, "real_markets.json")) as f:
        markets = json.load(f)
    return traders, trades, positions, markets


def classify_market(title):
    title_lower = title.lower()
    if any(kw in title_lower for kw in BLACKLIST_KEYWORDS):
        return "BLACKLIST"
    if any(kw in title_lower for kw in SPORTS_KEYWORDS):
        return "SPORTS"
    if any(kw in title_lower for kw in HIGH_VALUE_KEYWORDS):
        return "HIGH_VALUE"
    return "NORMAL"


def select_elite_traders(traders, trades, positions, min_win_rate=0.50, min_pnl=0):
    """精选交易员：高胜率 + 正盈利"""
    # 计算每个交易员的持仓表现
    wallet_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0, "count": 0})
    for p in positions:
        wallet = p.get("trader_wallet", "")
        pnl = p.get("cashPnl", 0)
        wallet_stats[wallet]["count"] += 1
        wallet_stats[wallet]["pnl"] += pnl
        if pnl > 0:
            wallet_stats[wallet]["wins"] += 1
        elif pnl < 0:
            wallet_stats[wallet]["losses"] += 1

    elite = []
    for t in traders:
        w = t["wallet"]
        stats = wallet_stats.get(w, {})
        total = stats.get("wins", 0) + stats.get("losses", 0)
        if total < 3:
            continue
        win_rate = stats.get("wins", 0) / total
        pnl = stats.get("pnl", 0)

        if win_rate >= min_win_rate and pnl >= min_pnl:
            elite.append({
                **t,
                "position_win_rate": round(win_rate, 4),
                "position_pnl": round(pnl, 2),
                "position_count": total,
            })

    elite.sort(key=lambda x: x["position_pnl"], reverse=True)
    return elite


def generate_v3_signals(trades, elite_wallets, min_traders=2):
    """v3 信号生成"""
    signals = defaultdict(lambda: {
        "traders": {}, "total_usdc": 0, "trades": [],
        "title": "", "outcome": "", "condition_id": "", "slug": ""
    })

    for trade in trades:
        if trade.get("side") != "BUY" or trade.get("type") != "TRADE":
            continue
        wallet = trade.get("trader_wallet", "")
        if wallet not in elite_wallets:
            continue

        title = trade.get("title", "")
        market_type = classify_market(title)
        if market_type in ("BLACKLIST", "SPORTS"):
            continue

        ts = trade.get("timestamp", 0)
        day_key = ts // 86400
        key = (trade.get("conditionId", ""), trade.get("outcome", ""), day_key)

        sig = signals[key]
        sig["traders"][wallet] = elite_wallets[wallet]
        sig["total_usdc"] += trade.get("usdcSize", 0)
        sig["trades"].append(trade)
        sig["title"] = title
        sig["outcome"] = trade.get("outcome", "")
        sig["condition_id"] = trade.get("conditionId", "")
        sig["slug"] = trade.get("slug", "")
        sig["market_type"] = market_type

    result = []
    for key, sig in signals.items():
        if len(sig["traders"]) < min_traders:
            continue

        prices = [t["price"] for t in sig["trades"] if t.get("price")]
        avg_price = sum(prices) / len(prices) if prices else 0
        if avg_price <= 0.05 or avg_price >= 0.95:
            continue

        avg_wr = sum(sig["traders"].values()) / len(sig["traders"])
        market_type = sig.get("market_type", "NORMAL")
        type_multiplier = 1.5 if market_type == "HIGH_VALUE" else 1.0

        strength = (
            len(sig["traders"]) / 10
            * avg_wr
            * type_multiplier
            * min(sig["total_usdc"] / 500, 2.0)
        )

        result.append({
            "condition_id": sig["condition_id"],
            "outcome": sig["outcome"],
            "title": sig["title"],
            "slug": sig["slug"],
            "market_type": market_type,
            "num_traders": len(sig["traders"]),
            "total_usdc": round(sig["total_usdc"], 2),
            "avg_price": round(avg_price, 4),
            "avg_win_rate": round(avg_wr, 4),
            "signal_strength": round(strength, 4),
        })

    result.sort(key=lambda x: x["signal_strength"], reverse=True)
    return result


def backtest_v3(trades, positions, markets, traders,
                initial_balance=1000, base_amount=10, max_positions=30):
    """v3 回测"""
    elite = select_elite_traders(traders, trades, positions, min_win_rate=0.50, min_pnl=0)
    elite_wallets = {t["wallet"]: t["position_win_rate"] for t in elite}

    print(f"  精选交易员: {len(elite)}/{len(traders)}")
    for t in elite[:5]:
        print(f"    {t['name'][:20]:20s} | 胜率: {t['position_win_rate']*100:.1f}% | PnL: ${t['position_pnl']:>12,.2f}")

    # 精选交易员少，降低共识门槛到1人（单人信号也有价值，因为已经筛选过交易员了）
    signals = generate_v3_signals(trades, elite_wallets, min_traders=1)
    print(f"  v3 信号: {len(signals)} 个")

    # 持仓盈亏查找
    position_pnl = {}
    for p in positions:
        key = f"{p.get('conditionId', '')}_{p.get('outcome', '')}"
        position_pnl[key] = p.get("percentPnl", 0)

    balance = initial_balance
    closed = []

    for signal in signals:
        if balance < base_amount or len(closed) >= max_positions:
            break

        # 动态仓位
        size = base_amount
        if signal["market_type"] == "HIGH_VALUE":
            size *= 1.5
        size *= min(signal["signal_strength"], 3.0)
        size = max(base_amount, min(size, balance * 0.15))

        pnl_key = f"{signal['condition_id']}_{signal['outcome']}"
        real_pnl_pct = position_pnl.get(pnl_key, None)

        if real_pnl_pct is not None:
            pnl = size * real_pnl_pct / 100
        else:
            pnl = 0

        balance -= size
        balance += size + pnl

        closed.append({
            "title": signal["title"],
            "outcome": signal["outcome"],
            "market_type": signal["market_type"],
            "entry_price": signal["avg_price"],
            "position_size": round(size, 2),
            "num_traders": signal["num_traders"],
            "avg_win_rate": signal["avg_win_rate"],
            "signal_strength": signal["signal_strength"],
            "pnl": round(pnl, 2),
            "won": pnl > 0,
            "real_data": real_pnl_pct is not None,
        })

    wins = [t for t in closed if t["won"]]
    losses = [t for t in closed if t["pnl"] < 0]
    neutral = [t for t in closed if t["pnl"] == 0]
    total_pnl = sum(t["pnl"] for t in closed)

    # 按市场类型统计
    by_type = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for t in closed:
        mt = t["market_type"]
        by_type[mt]["count"] += 1
        by_type[mt]["pnl"] += t["pnl"]
        if t["won"]:
            by_type[mt]["wins"] += 1

    report = {
        "strategy": "v3 精选跟单 (精英交易员 + 市场分类 + 动态仓位)",
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / initial_balance * 100, 2),
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "neutral": len(neutral),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "elite_traders": len(elite),
        "by_type": dict(by_type),
        "top_trades": sorted(closed, key=lambda x: x["pnl"], reverse=True)[:5],
        "worst_trades": sorted(closed, key=lambda x: x["pnl"])[:5],
    }
    return report


def print_v3_report(report):
    print("=" * 60)
    print(f"📈 {report['strategy']}")
    print("=" * 60)
    print(f"初始资金: ${report['initial_balance']}")
    print(f"最终余额: ${report['final_balance']}")
    print(f"总盈亏: ${report['total_pnl']} ({report['roi']}%)")
    print(f"精选交易员: {report['elite_traders']} 人")
    print(f"总交易: {report['total_trades']} 笔 (胜: {report['wins']} / 负: {report['losses']} / 平: {report['neutral']})")
    print(f"胜率: {report['win_rate']}%")

    print(f"\n📊 按市场类型:")
    for mt, data in report.get("by_type", {}).items():
        wr = data["wins"] / data["count"] * 100 if data["count"] > 0 else 0
        print(f"  {mt:12s} | {data['count']}笔 | 胜率: {wr:.0f}% | PnL: ${data['pnl']:.2f}")

    print(f"\n🏆 最佳交易:")
    for t in report.get("top_trades", [])[:5]:
        r = "✓" if t.get("real_data") else "~"
        print(f"  [{r}] {t['title'][:40]}... | {t['outcome']} | ${t['position_size']} | +${t['pnl']}")

    print(f"\n💀 最差交易:")
    for t in report.get("worst_trades", [])[:5]:
        r = "✓" if t.get("real_data") else "~"
        print(f"  [{r}] {t['title'][:40]}... | {t['outcome']} | ${t['position_size']} | ${t['pnl']}")
    print("=" * 60)


if __name__ == "__main__":
    traders, trades, positions, markets = load_data()
    print(f"数据: {len(traders)} 交易员 | {len(trades)} 交易 | {len(positions)} 持仓 | {len(markets)} 市场\n")
    report = backtest_v3(trades, positions, markets, traders)
    print_v3_report(report)
