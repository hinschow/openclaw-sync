"""基于真实交易员数据的跟单回测"""

import json
import os
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def load_trader_data():
    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)
    with open(os.path.join(DATA_DIR, "real_trader_trades.json")) as f:
        trades = json.load(f)
    with open(os.path.join(DATA_DIR, "real_trader_positions.json")) as f:
        positions = json.load(f)
    with open(os.path.join(DATA_DIR, "real_markets.json")) as f:
        markets = json.load(f)
    return traders, trades, positions, markets


def analyze_trader_consensus(trades, min_traders=3, time_window_sec=86400):
    """
    共识分析：找出多个 Top 交易员在同一市场同方向操作的信号
    基于真实交易记录
    """
    # 按市场+方向+时间窗口聚合
    signals = defaultdict(lambda: {
        "traders": set(), "total_usdc": 0, "trades": [],
        "title": "", "slug": "", "outcome": "", "avg_price": 0
    })

    buy_trades = [t for t in trades if t.get("side") == "BUY" and t.get("type") == "TRADE"]

    for trade in buy_trades:
        ts = trade.get("timestamp", 0)
        # 按天分组
        day_key = ts // time_window_sec
        key = (trade.get("conditionId", ""), trade.get("outcome", ""), day_key)

        sig = signals[key]
        sig["traders"].add(trade.get("trader_wallet", ""))
        sig["total_usdc"] += trade.get("usdcSize", 0)
        sig["trades"].append(trade)
        sig["title"] = trade.get("title", "")
        sig["slug"] = trade.get("slug", "")
        sig["outcome"] = trade.get("outcome", "")

    # 筛选多人共识
    consensus = []
    for key, sig in signals.items():
        if len(sig["traders"]) >= min_traders:
            prices = [t["price"] for t in sig["trades"] if t.get("price")]
            avg_price = sum(prices) / len(prices) if prices else 0
            ranks = [t.get("trader_rank", 50) for t in sig["trades"]]
            avg_rank = sum(ranks) / len(ranks)

            consensus.append({
                "condition_id": key[0],
                "outcome": sig["outcome"],
                "title": sig["title"],
                "slug": sig["slug"],
                "num_traders": len(sig["traders"]),
                "total_usdc": round(sig["total_usdc"], 2),
                "avg_price": round(avg_price, 4),
                "avg_rank": round(avg_rank, 1),
                "signal_strength": round(len(sig["traders"]) / 41 * (1 - avg_rank / 50), 4),
                "timestamp": min(t.get("timestamp", 0) for t in sig["trades"]),
            })

    consensus.sort(key=lambda x: x["signal_strength"], reverse=True)
    return consensus


def backtest_consensus_strategy(trades, positions, markets,
                                 initial_balance=1000, trade_amount=10,
                                 min_traders=2, max_positions=20):
    """
    基于真实数据的共识跟单回测
    
    逻辑：
    1. 找到多个 Top 交易员同时买入的市场
    2. 跟进买入
    3. 用持仓数据中的 cashPnl 评估结果
    """
    consensus = analyze_trader_consensus(trades, min_traders=min_traders)

    balance = initial_balance
    open_positions = []
    closed_trades = []

    # 构建市场当前价格查找
    market_prices = {}
    for m in markets:
        for outcome_name, outcome_data in m.get("outcomes", {}).items():
            market_prices[f"{m['id']}_{outcome_name}"] = outcome_data.get("price", 0)

    # 构建持仓盈亏查找（用真实持仓数据评估）
    position_pnl = {}
    for p in positions:
        key = f"{p.get('conditionId', '')}_{p.get('outcome', '')}"
        pnl_pct = p.get("percentPnl", 0)
        position_pnl[key] = pnl_pct

    for signal in consensus:
        if balance < trade_amount:
            break
        if len(open_positions) >= max_positions:
            continue

        entry_price = signal["avg_price"]
        if entry_price <= 0.05 or entry_price >= 0.95:
            continue

        # 查找该市场的真实盈亏
        pnl_key = f"{signal['condition_id']}_{signal['outcome']}"
        real_pnl_pct = position_pnl.get(pnl_key, None)

        if real_pnl_pct is not None:
            # 用真实盈亏百分比
            pnl = trade_amount * real_pnl_pct / 100
        else:
            # 无真实数据，用价格变动估算（保守估计）
            # 假设持有到现在，用当前市场价格
            current_price = entry_price  # 默认不变
            for m in markets:
                if signal["slug"] and signal["slug"] == m.get("slug"):
                    outcomes = m.get("outcomes", {})
                    if signal["outcome"] in outcomes:
                        current_price = outcomes[signal["outcome"]].get("price", entry_price)
                        break

            shares = trade_amount / entry_price
            pnl = (current_price - entry_price) * shares

        balance -= trade_amount
        balance += trade_amount + pnl

        closed_trades.append({
            "title": signal["title"],
            "outcome": signal["outcome"],
            "entry_price": entry_price,
            "num_traders": signal["num_traders"],
            "signal_strength": signal["signal_strength"],
            "pnl": round(pnl, 2),
            "won": pnl > 0,
            "used_real_data": real_pnl_pct is not None,
        })

    # 统计
    wins = [t for t in closed_trades if t["won"]]
    losses = [t for t in closed_trades if not t["won"]]
    total_pnl = sum(t["pnl"] for t in closed_trades)

    report = {
        "strategy": f"共识跟单 (min_traders={min_traders})",
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / initial_balance * 100, 2),
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(len(closed_trades), 1) * 100, 1),
        "avg_win": round(sum(t["pnl"] for t in wins) / max(len(wins), 1), 2),
        "avg_loss": round(sum(t["pnl"] for t in losses) / max(len(losses), 1), 2),
        "consensus_signals": len(consensus),
        "top_trades": sorted(closed_trades, key=lambda x: x["pnl"], reverse=True)[:5],
        "worst_trades": sorted(closed_trades, key=lambda x: x["pnl"])[:5],
    }
    return report


def print_report(report):
    print("=" * 60)
    print(f"📈 {report['strategy']}")
    print("=" * 60)
    print(f"初始资金: ${report['initial_balance']}")
    print(f"最终余额: ${report['final_balance']}")
    print(f"总盈亏: ${report['total_pnl']} ({report['roi']}%)")
    print(f"共识信号: {report['consensus_signals']} 个")
    print(f"执行交易: {report['total_trades']} 笔")
    print(f"胜/负: {report['wins']}/{report['losses']} (胜率: {report['win_rate']}%)")
    print(f"平均盈利: ${report['avg_win']} | 平均亏损: ${report['avg_loss']}")

    print(f"\n🏆 最佳交易:")
    for t in report.get("top_trades", [])[:3]:
        real = "✓真实" if t.get("used_real_data") else "~估算"
        print(f"  [{real}] {t['title'][:40]}... | {t['outcome']} | {t['num_traders']}人 | +${t['pnl']}")

    print(f"\n💀 最差交易:")
    for t in report.get("worst_trades", [])[:3]:
        real = "✓真实" if t.get("used_real_data") else "~估算"
        print(f"  [{real}] {t['title'][:40]}... | {t['outcome']} | {t['num_traders']}人 | ${t['pnl']}")
    print("=" * 60)


if __name__ == "__main__":
    traders, trades, positions, markets = load_trader_data()
    print(f"数据: {len(traders)} 交易员 | {len(trades)} 交易 | {len(positions)} 持仓 | {len(markets)} 市场\n")

    for min_t in [2, 3, 5]:
        report = backtest_consensus_strategy(trades, positions, markets, min_traders=min_t)
        print_report(report)
        print()
