"""
优化版跟单策略 v2
核心改进：
1. 只跟盈利交易员（按 PnL 筛选）
2. 过滤短期价格预测市场（BTC/ETH Up or Down）
3. 按交易员历史胜率加权
4. 仓位管理：信号越强仓位越大
5. 反向指标：亏损大户集体买入时考虑反向
"""

import json
import os
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# 过滤关键词：这些市场本质是短期赌博，信息优势低
FILTER_KEYWORDS = [
    "up or down", "up/down", "tweets from", "posts from",
    "game 1 winner", "game 2 winner", "game 3 winner",
    "game 4 winner", "game 5 winner",
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


def should_filter_market(title):
    """过滤低质量市场"""
    title_lower = title.lower()
    return any(kw in title_lower for kw in FILTER_KEYWORDS)


def compute_trader_stats(trades, positions):
    """计算每个交易员的历史表现"""
    stats = defaultdict(lambda: {
        "wins": 0, "losses": 0, "total_pnl": 0,
        "total_trades": 0, "profitable_markets": set()
    })

    # 从持仓数据计算胜率
    for p in positions:
        wallet = p.get("trader_wallet", "")
        pnl = p.get("cashPnl", 0)
        if pnl > 0:
            stats[wallet]["wins"] += 1
            stats[wallet]["profitable_markets"].add(p.get("conditionId", ""))
        elif pnl < 0:
            stats[wallet]["losses"] += 1
        stats[wallet]["total_pnl"] += pnl
        stats[wallet]["total_trades"] += 1

    # 计算胜率
    for wallet, s in stats.items():
        total = s["wins"] + s["losses"]
        s["win_rate"] = s["wins"] / total if total > 0 else 0
        s["profitable_markets"] = len(s["profitable_markets"])

    return dict(stats)


def select_profitable_traders(traders, trader_stats, min_win_rate=0.45, min_trades=5):
    """筛选盈利交易员"""
    profitable = []
    for t in traders:
        wallet = t["wallet"]
        stats = trader_stats.get(wallet, {})
        win_rate = stats.get("win_rate", 0)
        total = stats.get("total_trades", 0)
        pnl = t.get("pnl", 0)

        # 条件：正PnL 或 胜率>阈值 且 有足够交易量
        if (pnl > 0 or win_rate >= min_win_rate) and total >= min_trades:
            t["win_rate"] = win_rate
            t["total_pnl_positions"] = stats.get("total_pnl", 0)
            profitable.append(t)

    return profitable


def analyze_smart_consensus(trades, profitable_wallets, trader_stats,
                             min_traders=2, time_window_sec=86400):
    """
    智能共识分析：
    - 只看盈利交易员的交易
    - 过滤低质量市场
    - 按交易员胜率加权信号强度
    """
    signals = defaultdict(lambda: {
        "traders": {}, "total_usdc": 0, "trades": [],
        "title": "", "slug": "", "outcome": "", "condition_id": ""
    })

    buy_trades = [
        t for t in trades
        if t.get("side") == "BUY"
        and t.get("type") == "TRADE"
        and t.get("trader_wallet", "") in profitable_wallets
        and not should_filter_market(t.get("title", ""))
    ]

    for trade in buy_trades:
        ts = trade.get("timestamp", 0)
        day_key = ts // time_window_sec
        key = (trade.get("conditionId", ""), trade.get("outcome", ""), day_key)

        sig = signals[key]
        wallet = trade.get("trader_wallet", "")
        sig["traders"][wallet] = trader_stats.get(wallet, {}).get("win_rate", 0.5)
        sig["total_usdc"] += trade.get("usdcSize", 0)
        sig["trades"].append(trade)
        sig["title"] = trade.get("title", "")
        sig["slug"] = trade.get("slug", "")
        sig["outcome"] = trade.get("outcome", "")
        sig["condition_id"] = trade.get("conditionId", "")

    consensus = []
    for key, sig in signals.items():
        if len(sig["traders"]) >= min_traders:
            prices = [t["price"] for t in sig["trades"] if t.get("price")]
            avg_price = sum(prices) / len(prices) if prices else 0

            # 加权信号强度：交易员数量 × 平均胜率 × 总金额权重
            avg_win_rate = sum(sig["traders"].values()) / len(sig["traders"])
            amount_weight = min(sig["total_usdc"] / 1000, 2.0)  # 金额越大权重越高，上限2x
            strength = len(sig["traders"]) / 20 * avg_win_rate * (1 + amount_weight)

            consensus.append({
                "condition_id": sig["condition_id"],
                "outcome": sig["outcome"],
                "title": sig["title"],
                "slug": sig["slug"],
                "num_traders": len(sig["traders"]),
                "total_usdc": round(sig["total_usdc"], 2),
                "avg_price": round(avg_price, 4),
                "avg_win_rate": round(avg_win_rate, 4),
                "signal_strength": round(strength, 4),
                "timestamp": min(t.get("timestamp", 0) for t in sig["trades"]),
            })

    consensus.sort(key=lambda x: x["signal_strength"], reverse=True)
    return consensus


def analyze_contrarian_signals(trades, losing_wallets, min_traders=3):
    """
    反向指标：亏损大户集体买入时，考虑反向操作
    """
    signals = defaultdict(lambda: {
        "traders": set(), "total_usdc": 0, "trades": [],
        "title": "", "outcome": "", "condition_id": ""
    })

    buy_trades = [
        t for t in trades
        if t.get("side") == "BUY"
        and t.get("type") == "TRADE"
        and t.get("trader_wallet", "") in losing_wallets
        and not should_filter_market(t.get("title", ""))
    ]

    for trade in buy_trades:
        ts = trade.get("timestamp", 0)
        day_key = ts // 86400
        key = (trade.get("conditionId", ""), trade.get("outcome", ""), day_key)
        sig = signals[key]
        sig["traders"].add(trade.get("trader_wallet", ""))
        sig["total_usdc"] += trade.get("usdcSize", 0)
        sig["trades"].append(trade)
        sig["title"] = trade.get("title", "")
        sig["outcome"] = trade.get("outcome", "")
        sig["condition_id"] = trade.get("conditionId", "")

    contrarian = []
    for key, sig in signals.items():
        if len(sig["traders"]) >= min_traders:
            prices = [t["price"] for t in sig["trades"] if t.get("price")]
            avg_price = sum(prices) / len(prices) if prices else 0

            # 反向：他们买 Yes，我们考虑 No
            opposite = "No" if sig["outcome"] == "Yes" else "Yes"

            contrarian.append({
                "condition_id": sig["condition_id"],
                "original_outcome": sig["outcome"],
                "contrarian_outcome": opposite,
                "title": sig["title"],
                "num_losers": len(sig["traders"]),
                "total_usdc": round(sig["total_usdc"], 2),
                "avg_price": round(avg_price, 4),
                "contrarian_price": round(1 - avg_price, 4),
            })

    return contrarian


def backtest_v2(trades, positions, markets, traders,
                initial_balance=1000, trade_amount=10, max_positions=30):
    """v2 策略回测"""
    trader_stats = compute_trader_stats(trades, positions)

    # 筛选盈利交易员
    profitable = select_profitable_traders(traders, trader_stats)
    profitable_wallets = {t["wallet"] for t in profitable}

    # 筛选亏损交易员（用于反向指标）
    losing_wallets = {t["wallet"] for t in traders if t["pnl"] < 0}

    print(f"  盈利交易员: {len(profitable)}/{len(traders)}")
    print(f"  亏损交易员: {len(losing_wallets)}/{len(traders)}")

    # 智能共识信号
    consensus = analyze_smart_consensus(trades, profitable_wallets, trader_stats, min_traders=2)
    print(f"  智能共识信号: {len(consensus)} 个")

    # 反向信号
    contrarian = analyze_contrarian_signals(trades, losing_wallets, min_traders=2)
    print(f"  反向信号: {len(contrarian)} 个")

    # 构建持仓盈亏查找
    position_pnl = {}
    for p in positions:
        key = f"{p.get('conditionId', '')}_{p.get('outcome', '')}"
        position_pnl[key] = p.get("percentPnl", 0)

    balance = initial_balance
    closed_trades = []

    # 执行共识信号
    for signal in consensus:
        if balance < trade_amount or len(closed_trades) >= max_positions:
            break

        entry_price = signal["avg_price"]
        if entry_price <= 0.05 or entry_price >= 0.95:
            continue

        # 动态仓位：信号越强仓位越大
        position_size = trade_amount * min(signal["signal_strength"] * 3, 3.0)
        position_size = min(position_size, balance * 0.1)  # 单笔不超过10%
        position_size = max(position_size, trade_amount)

        pnl_key = f"{signal['condition_id']}_{signal['outcome']}"
        real_pnl_pct = position_pnl.get(pnl_key, None)

        if real_pnl_pct is not None:
            pnl = position_size * real_pnl_pct / 100
        else:
            pnl = 0  # 无数据保守处理

        balance -= position_size
        balance += position_size + pnl

        closed_trades.append({
            "type": "CONSENSUS",
            "title": signal["title"],
            "outcome": signal["outcome"],
            "entry_price": entry_price,
            "position_size": round(position_size, 2),
            "num_traders": signal["num_traders"],
            "avg_win_rate": signal["avg_win_rate"],
            "signal_strength": signal["signal_strength"],
            "pnl": round(pnl, 2),
            "won": pnl > 0,
            "used_real_data": real_pnl_pct is not None,
        })

    # 执行反向信号（更保守，小仓位）
    for signal in contrarian[:10]:
        if balance < trade_amount:
            break

        contrarian_price = signal["contrarian_price"]
        if contrarian_price <= 0.05 or contrarian_price >= 0.95:
            continue

        position_size = trade_amount * 0.5  # 反向信号用半仓

        pnl_key = f"{signal['condition_id']}_{signal['contrarian_outcome']}"
        real_pnl_pct = position_pnl.get(pnl_key, None)

        if real_pnl_pct is not None:
            pnl = position_size * real_pnl_pct / 100
        else:
            pnl = 0

        balance -= position_size
        balance += position_size + pnl

        closed_trades.append({
            "type": "CONTRARIAN",
            "title": signal["title"],
            "outcome": signal["contrarian_outcome"],
            "entry_price": contrarian_price,
            "position_size": round(position_size, 2),
            "num_traders": signal["num_losers"],
            "pnl": round(pnl, 2),
            "won": pnl > 0,
            "used_real_data": real_pnl_pct is not None,
        })

    # 统计
    wins = [t for t in closed_trades if t["won"]]
    losses = [t for t in closed_trades if not t["won"]]
    neutral = [t for t in closed_trades if t["pnl"] == 0]
    total_pnl = sum(t["pnl"] for t in closed_trades)

    consensus_trades = [t for t in closed_trades if t["type"] == "CONSENSUS"]
    contrarian_trades = [t for t in closed_trades if t["type"] == "CONTRARIAN"]

    c_wins = [t for t in consensus_trades if t["won"]]
    ct_wins = [t for t in contrarian_trades if t["won"]]

    report = {
        "strategy": "v2 智能跟单 (盈利交易员 + 市场过滤 + 反向指标)",
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / initial_balance * 100, 2),
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "neutral": len(neutral),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "consensus_trades": len(consensus_trades),
        "consensus_wins": len(c_wins),
        "consensus_pnl": round(sum(t["pnl"] for t in consensus_trades), 2),
        "contrarian_trades": len(contrarian_trades),
        "contrarian_wins": len(ct_wins),
        "contrarian_pnl": round(sum(t["pnl"] for t in contrarian_trades), 2),
        "profitable_traders_used": len(profitable),
        "top_trades": sorted(closed_trades, key=lambda x: x["pnl"], reverse=True)[:5],
        "worst_trades": sorted(closed_trades, key=lambda x: x["pnl"])[:5],
    }
    return report


def print_v2_report(report):
    print("=" * 60)
    print(f"📈 {report['strategy']}")
    print("=" * 60)
    print(f"初始资金: ${report['initial_balance']}")
    print(f"最终余额: ${report['final_balance']}")
    print(f"总盈亏: ${report['total_pnl']} ({report['roi']}%)")
    print(f"盈利交易员: {report['profitable_traders_used']} 人")
    print(f"总交易: {report['total_trades']} 笔 (胜: {report['wins']} / 负: {report['losses']} / 平: {report['neutral']})")
    print(f"胜率: {report['win_rate']}% (排除平局)")
    print(f"\n  共识策略: {report['consensus_trades']}笔 | 胜{report['consensus_wins']} | PnL: ${report['consensus_pnl']}")
    print(f"  反向策略: {report['contrarian_trades']}笔 | 胜{report['contrarian_wins']} | PnL: ${report['contrarian_pnl']}")

    print(f"\n🏆 最佳交易:")
    for t in report.get("top_trades", [])[:5]:
        real = "✓" if t.get("used_real_data") else "~"
        print(f"  [{real}][{t['type'][:4]}] {t['title'][:35]}... | {t['outcome']} | ${t['position_size']} | +${t['pnl']}")

    print(f"\n💀 最差交易:")
    for t in report.get("worst_trades", [])[:5]:
        real = "✓" if t.get("used_real_data") else "~"
        print(f"  [{real}][{t['type'][:4]}] {t['title'][:35]}... | {t['outcome']} | ${t['position_size']} | ${t['pnl']}")
    print("=" * 60)


if __name__ == "__main__":
    traders, trades, positions, markets = load_data()
    print(f"数据: {len(traders)} 交易员 | {len(trades)} 交易 | {len(positions)} 持仓 | {len(markets)} 市场\n")
    report = backtest_v2(trades, positions, markets, traders)
    print_v2_report(report)
