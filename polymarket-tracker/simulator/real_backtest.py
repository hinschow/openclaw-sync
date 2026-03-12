"""真实数据回测引擎 - 基于 Polymarket 真实价格历史回测跟单策略"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def load_real_data():
    """加载真实数据"""
    with open(os.path.join(DATA_DIR, "real_markets.json")) as f:
        markets = json.load(f)
    with open(os.path.join(DATA_DIR, "real_histories.json")) as f:
        histories = json.load(f)
    with open(os.path.join(DATA_DIR, "real_activities.json")) as f:
        activities = json.load(f)
    return markets, histories, activities


def backtest_momentum_strategy(histories, activities, initial_balance=1000, 
                                lookback_hours=24, hold_hours=72, 
                                trade_amount=10, max_positions=20):
    """
    动量跟单策略回测
    
    逻辑：当检测到显著买入活动（价格上涨>2%）时跟进买入，
    持有一段时间后按当时价格卖出。
    
    这模拟了"跟踪大户买入行为"的核心思路。
    """
    balance = initial_balance
    positions = []
    closed_trades = []
    daily_snapshots = []

    # 按时间排序所有活动
    sorted_activities = sorted(activities, key=lambda x: x["timestamp"])
    if not sorted_activities:
        return {"error": "无活动数据"}

    # 构建价格查找表: {token_id: [(timestamp, price), ...]}
    price_lookup = {}
    for key, mh in histories.items():
        tid = mh["token_id"]
        price_lookup[tid] = [(h["t"], float(h["p"])) for h in mh["history"] if "t" in h and "p" in h]
        price_lookup[tid].sort(key=lambda x: x[0])

    def get_price_at(token_id, target_ts):
        """获取某时刻的价格（最近的历史价格）"""
        if token_id not in price_lookup:
            return None
        prices = price_lookup[token_id]
        best = None
        for ts, p in prices:
            if ts <= target_ts:
                best = p
            else:
                break
        return best

    def get_future_price(token_id, entry_ts, hours_ahead):
        """获取未来某时刻的价格"""
        if token_id not in price_lookup:
            return None
        prices = price_lookup[token_id]
        # 将 entry_ts 转为秒并加上 hours
        try:
            entry_sec = int(entry_ts)
        except (ValueError, TypeError):
            return None
        target_sec = entry_sec + hours_ahead * 3600
        best = None
        for ts, p in prices:
            try:
                ts_sec = int(ts)
            except (ValueError, TypeError):
                continue
            if ts_sec >= target_sec:
                return p
            best = p
        return best  # 如果没有未来数据，返回最后已知价格

    # 筛选买入信号（价格上涨 > 2%）
    buy_signals = [a for a in sorted_activities if a["direction"] == "BUY" and a["price_change"] > 0.02]

    # 按市场聚合信号（同一市场短时间内多次信号合并）
    consolidated = []
    seen = set()
    for sig in buy_signals:
        ts_str = str(sig['timestamp'])
        key = f"{sig['market_id']}_{sig['outcome']}_{ts_str[:10]}"
        if key not in seen:
            seen.add(key)
            consolidated.append(sig)

    print(f"  买入信号总数: {len(buy_signals)}, 去重后: {len(consolidated)}")

    for signal in consolidated:
        # 检查是否已有该市场的持仓
        existing = [p for p in positions if p["market_id"] == signal["market_id"]]
        if existing:
            continue

        if len(positions) >= max_positions:
            # 尝试平仓最老的持仓
            oldest = min(positions, key=lambda p: p["entry_ts"])
            exit_price = get_future_price(oldest["token_id"], oldest["entry_ts"], hold_hours)
            if exit_price is not None:
                pnl = (exit_price - oldest["entry_price"]) * oldest["shares"]
                balance += oldest["amount"] + pnl
                closed_trades.append({
                    **oldest,
                    "exit_price": round(exit_price, 4),
                    "pnl": round(pnl, 2),
                    "won": pnl > 0,
                })
                positions.remove(oldest)

        if balance < trade_amount:
            continue

        entry_price = signal["price"]
        if entry_price <= 0.05 or entry_price >= 0.95:
            continue  # 跳过极端价格

        shares = trade_amount / entry_price
        positions.append({
            "market_id": signal["market_id"],
            "question": signal["question"],
            "outcome": signal["outcome"],
            "token_id": signal["token_id"],
            "entry_price": entry_price,
            "amount": trade_amount,
            "shares": shares,
            "entry_ts": signal["timestamp"],
        })
        balance -= trade_amount

    # 平仓所有剩余持仓（用最后已知价格）
    for pos in positions[:]:
        exit_price = get_future_price(pos["token_id"], pos["entry_ts"], hold_hours)
        if exit_price is None:
            exit_price = pos["entry_price"]  # 无数据则按入场价
        pnl = (exit_price - pos["entry_price"]) * pos["shares"]
        balance += pos["amount"] + pnl
        closed_trades.append({
            **pos,
            "exit_price": round(exit_price, 4),
            "pnl": round(pnl, 2),
            "won": pnl > 0,
        })

    # 统计
    wins = [t for t in closed_trades if t["won"]]
    losses = [t for t in closed_trades if not t["won"]]
    total_pnl = sum(t["pnl"] for t in closed_trades)
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    report = {
        "strategy": "动量跟单 (Momentum Follow)",
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / initial_balance * 100, 2),
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(len(closed_trades), 1) * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(abs(sum(t["pnl"] for t in wins)) / abs(sum(t["pnl"] for t in losses)) if losses else float('inf'), 2),
        "trade_amount": trade_amount,
        "hold_hours": hold_hours,
        "top_trades": sorted(closed_trades, key=lambda x: x["pnl"], reverse=True)[:5],
        "worst_trades": sorted(closed_trades, key=lambda x: x["pnl"])[:5],
    }
    return report


def print_real_backtest_report(report):
    """打印真实数据回测报告"""
    if "error" in report:
        print(f"❌ {report['error']}")
        return

    print("=" * 60)
    print(f"📈 真实数据回测报告 - {report['strategy']}")
    print("=" * 60)
    print(f"初始资金: ${report['initial_balance']}")
    print(f"最终余额: ${report['final_balance']}")
    print(f"总盈亏: ${report['total_pnl']} ({report['roi']}%)")
    print(f"总交易: {report['total_trades']} 笔")
    print(f"胜/负: {report['wins']}/{report['losses']} (胜率: {report['win_rate']}%)")
    print(f"平均盈利: ${report['avg_win']} | 平均亏损: ${report['avg_loss']}")
    print(f"盈亏比: {report['profit_factor']}")
    print(f"每笔金额: ${report['trade_amount']} | 持仓时间: {report['hold_hours']}h")

    print(f"\n🏆 最佳交易:")
    for t in report.get("top_trades", [])[:3]:
        print(f"  {t['question'][:45]}... | {t['outcome']} | +${t['pnl']}")

    print(f"\n💀 最差交易:")
    for t in report.get("worst_trades", [])[:3]:
        print(f"  {t['question'][:45]}... | {t['outcome']} | ${t['pnl']}")

    print("=" * 60)


if __name__ == "__main__":
    markets, histories, activities = load_real_data()
    print(f"加载数据: {len(markets)} 市场, {len(histories)} 价格历史, {len(activities)} 活动")

    # 回测不同参数
    for hold in [24, 48, 72]:
        print(f"\n--- 持仓 {hold}h ---")
        report = backtest_momentum_strategy(histories, activities, hold_hours=hold)
        print_real_backtest_report(report)
