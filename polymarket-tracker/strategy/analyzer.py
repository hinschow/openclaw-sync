"""策略分析器 - 分析 Top 交易员行为，提炼跟单策略"""

import json
from collections import defaultdict
from datetime import datetime, timedelta


def analyze_consensus(trades, markets, time_window_hours=24, min_traders=3):
    """
    共识分析：找出多个头部交易员在同一市场同方向操作的信号
    
    逻辑：如果 N 个 Top 交易员在时间窗口内对同一市场做出相同方向的交易，
    则认为这是一个强信号。
    """
    cutoff = datetime.now() - timedelta(hours=time_window_hours)
    recent_trades = [
        t for t in trades
        if datetime.fromisoformat(t["timestamp"]) > cutoff and t["action"] == "BUY"
    ]

    # 按市场+方向聚合
    market_signals = defaultdict(lambda: {"traders": set(), "total_amount": 0, "trades": []})

    for trade in recent_trades:
        key = (trade["market_id"], trade["side"])
        signal = market_signals[key]
        signal["traders"].add(trade["trader_id"])
        signal["total_amount"] += trade["amount"]
        signal["trades"].append(trade)

    # 筛选达到阈值的信号
    consensus_signals = []
    for (market_id, side), signal in market_signals.items():
        if len(signal["traders"]) >= min_traders:
            market_info = next((m for m in markets if m["id"] == market_id), None)
            if not market_info:
                continue

            # 计算信号强度：参与交易员数量 × 平均胜率权重
            avg_rank = sum(t["trader_rank"] for t in signal["trades"]) / len(signal["trades"])
            strength = len(signal["traders"]) / 50 * (1 - avg_rank / 50)

            consensus_signals.append({
                "market_id": market_id,
                "question": market_info["question"],
                "side": side,
                "current_price": market_info["outcomes"][side]["price"],
                "token_id": market_info["outcomes"][side]["token_id"],
                "num_traders": len(signal["traders"]),
                "total_amount": round(signal["total_amount"], 2),
                "signal_strength": round(strength, 4),
                "liquidity": market_info["liquidity"],
            })

    consensus_signals.sort(key=lambda x: x["signal_strength"], reverse=True)
    return consensus_signals


def analyze_whale_moves(trades, markets, min_amount=500, top_n_traders=10):
    """
    鲸鱼追踪：跟踪 Top 10 交易员的大额交易
    """
    whale_trades = [
        t for t in trades
        if t["trader_rank"] <= top_n_traders and t["amount"] >= min_amount and t["action"] == "BUY"
    ]

    signals = []
    for trade in whale_trades:
        market_info = next((m for m in markets if m["id"] == trade["market_id"]), None)
        if not market_info:
            continue
        signals.append({
            "market_id": trade["market_id"],
            "question": market_info["question"],
            "side": trade["side"],
            "current_price": market_info["outcomes"][trade["side"]]["price"],
            "token_id": market_info["outcomes"][trade["side"]]["token_id"],
            "trader_rank": trade["trader_rank"],
            "amount": trade["amount"],
            "liquidity": market_info["liquidity"],
            "timestamp": trade["timestamp"],
        })

    signals.sort(key=lambda x: x["amount"], reverse=True)
    return signals


def generate_report(consensus, whales):
    """生成策略分析报告"""
    report = []
    report.append("=" * 60)
    report.append("📊 Polymarket 跟单策略分析报告")
    report.append("=" * 60)

    report.append(f"\n🤝 共识信号 (多交易员同方向): {len(consensus)} 个")
    for i, s in enumerate(consensus[:10]):
        report.append(f"  {i+1}. {s['question'][:45]}...")
        report.append(f"     方向: {s['side']} @ {s['current_price']} | 交易员: {s['num_traders']} | 强度: {s['signal_strength']}")

    report.append(f"\n🐋 鲸鱼信号 (Top10大额交易): {len(whales)} 个")
    for i, w in enumerate(whales[:10]):
        report.append(f"  {i+1}. {w['question'][:45]}...")
        report.append(f"     方向: {w['side']} @ {w['current_price']} | Rank#{w['trader_rank']} | ${w['amount']}")

    return "\n".join(report)
