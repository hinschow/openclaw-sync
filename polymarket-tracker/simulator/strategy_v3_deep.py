"""
策略 v3 Deep - 使用完整持仓和市场结算数据的精细化回测
数据源：
- data/deep_trades.json: 96k+ 交易记录
- data/deep_positions.json: 12k+ 持仓数据（含 cashPnl/percentPnl）
- data/deep_markets.json: 3k+ 市场详情（含结算价格）
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


def load_deep_data():
    """加载所有深度数据"""
    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)
    with open(os.path.join(DATA_DIR, "deep_trades.json")) as f:
        trades = json.load(f)
    with open(os.path.join(DATA_DIR, "deep_positions.json")) as f:
        positions = json.load(f)
    with open(os.path.join(DATA_DIR, "deep_markets.json")) as f:
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


def build_position_lookup(positions):
    """
    Build lookup: (conditionId, outcome) -> list of position records
    Also build: (conditionId, outcome, wallet) -> position record
    """
    by_condition_outcome = defaultdict(list)
    by_condition_outcome_wallet = {}
    for p in positions:
        cid = p.get("conditionId", "")
        outcome = p.get("outcome", "")
        wallet = p.get("proxyWallet", "")
        key = (cid, outcome)
        by_condition_outcome[key].append(p)
        by_condition_outcome_wallet[(cid, outcome, wallet)] = p
    return by_condition_outcome, by_condition_outcome_wallet


def build_market_lookup(markets_data):
    """
    Build lookup: slug -> market settlement info
    Returns: {slug: {conditionId: {outcome: settlement_price}}}
    Also: {conditionId: {outcome: settlement_price}}
    """
    by_slug = {}
    by_condition = {}
    
    for slug, market_list in markets_data.items():
        for m in market_list:
            cid = m.get("conditionId", "")
            closed = m.get("closed", False)
            resolved = m.get("resolved", False)
            
            if not (closed or resolved):
                continue
            
            # Parse outcomes and prices
            outcomes_str = m.get("outcomes", "")
            prices_str = m.get("outcomePrices", "")
            
            try:
                if isinstance(outcomes_str, str):
                    outcomes = json.loads(outcomes_str) if outcomes_str else []
                else:
                    outcomes = outcomes_str or []
                    
                if isinstance(prices_str, str):
                    prices = json.loads(prices_str) if prices_str else []
                else:
                    prices = prices_str or []
            except:
                continue
            
            if not outcomes or not prices or len(outcomes) != len(prices):
                continue
            
            settlement = {}
            for outcome, price in zip(outcomes, prices):
                try:
                    settlement[outcome] = float(price)
                except:
                    pass
            
            if settlement:
                by_slug[slug] = by_slug.get(slug, {})
                by_slug[slug][cid] = settlement
                by_condition[cid] = settlement
    
    return by_slug, by_condition


def select_elite_traders(traders, positions, min_win_rate=0.50, min_pnl=0):
    """精选交易员：基于 deep_positions 的 cashPnl"""
    wallet_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0, "count": 0})
    
    for p in positions:
        wallet = p.get("proxyWallet", "")
        cash_pnl = p.get("cashPnl", 0) or 0
        wallet_stats[wallet]["count"] += 1
        wallet_stats[wallet]["pnl"] += cash_pnl
        if cash_pnl > 0:
            wallet_stats[wallet]["wins"] += 1
        elif cash_pnl < 0:
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


def generate_v3_signals(trades, elite_wallets, min_traders=1):
    """v3 信号生成"""
    signals = defaultdict(lambda: {
        "traders": {}, "total_usdc": 0, "trades": [],
        "title": "", "outcome": "", "condition_id": "", "slug": ""
    })

    for trade in trades:
        if trade.get("side") != "BUY" or trade.get("type") != "TRADE":
            continue
        wallet = trade.get("trader_wallet", "") or trade.get("proxyWallet", "")
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
            "trader_wallets": list(sig["traders"].keys()),
        })

    result.sort(key=lambda x: x["signal_strength"], reverse=True)
    return result


def compute_pnl(signal, pos_by_co, pos_by_cow, market_by_condition):
    """
    计算信号的真实盈亏，按优先级：
    a. deep_positions 的 cashPnl/percentPnl（优先匹配交易员自己的持仓）
    b. deep_markets 的结算价格
    c. 都没有返回 (None, "no_data")
    """
    cid = signal["condition_id"]
    outcome = signal["outcome"]
    avg_price = signal["avg_price"]
    trader_wallets = signal.get("trader_wallets", [])
    
    # Method A: Try to find position data for this conditionId + outcome
    # First try specific trader wallets
    for wallet in trader_wallets:
        pos = pos_by_cow.get((cid, outcome, wallet))
        if pos and pos.get("percentPnl") is not None and pos["percentPnl"] != 0:
            return pos["percentPnl"] / 100, "position_wallet"
    
    # Then try any position with this conditionId + outcome
    positions = pos_by_co.get((cid, outcome), [])
    if positions:
        # Use average percentPnl from all matching positions
        valid = [p for p in positions if p.get("percentPnl") is not None and p["percentPnl"] != 0]
        if valid:
            avg_pct = sum(p["percentPnl"] for p in valid) / len(valid)
            return avg_pct / 100, "position_avg"
    
    # Method B: Use market settlement data
    settlement = market_by_condition.get(cid, {})
    if settlement and outcome in settlement:
        settlement_price = settlement[outcome]
        if avg_price > 0:
            pnl_pct = (settlement_price - avg_price) / avg_price
            return pnl_pct, "market_settlement"
    
    # Method C: No data
    return None, "no_data"


def backtest_v3_deep(initial_balance=1000, base_amount=10, max_positions=50):
    """v3 Deep 回测 - 使用完整数据"""
    traders, trades, positions, markets = load_deep_data()
    
    print(f"数据加载:", flush=True)
    print(f"  交易员: {len(traders)}", flush=True)
    print(f"  交易记录: {len(trades)}", flush=True)
    print(f"  持仓数据: {len(positions)}", flush=True)
    print(f"  市场数据: {len(markets)} slugs", flush=True)
    
    # Build lookups
    pos_by_co, pos_by_cow = build_position_lookup(positions)
    market_by_slug, market_by_condition = build_market_lookup(markets)
    
    print(f"\n查找表:", flush=True)
    print(f"  持仓 (conditionId, outcome) 组合: {len(pos_by_co)}", flush=True)
    print(f"  已结算市场 conditionId: {len(market_by_condition)}", flush=True)
    
    # Select elite traders
    elite = select_elite_traders(traders, positions, min_win_rate=0.50, min_pnl=0)
    elite_wallets = {t["wallet"]: t["position_win_rate"] for t in elite}
    
    print(f"\n精选交易员: {len(elite)}/{len(traders)}", flush=True)
    for t in elite[:8]:
        print(f"  {t['name'][:25]:25s} | 胜率: {t['position_win_rate']*100:.1f}% | PnL: ${t['position_pnl']:>12,.2f} | 持仓: {t['position_count']}", flush=True)
    
    # Generate signals
    signals = generate_v3_signals(trades, elite_wallets, min_traders=1)
    print(f"\nv3 信号: {len(signals)} 个", flush=True)
    
    # Backtest
    balance = initial_balance
    closed = []
    pnl_sources = defaultdict(int)
    
    for signal in signals:
        if balance < base_amount or len(closed) >= max_positions:
            break
        
        # 动态仓位
        size = base_amount
        if signal["market_type"] == "HIGH_VALUE":
            size *= 1.5
        size *= min(signal["signal_strength"], 3.0)
        size = max(base_amount, min(size, balance * 0.15))
        
        # 计算盈亏
        pnl_pct, source = compute_pnl(signal, pos_by_co, pos_by_cow, market_by_condition)
        pnl_sources[source] += 1
        
        if pnl_pct is not None:
            pnl = size * pnl_pct
        else:
            pnl = 0
        
        balance -= size
        balance += size + pnl
        
        closed.append({
            "title": signal["title"],
            "outcome": signal["outcome"],
            "slug": signal["slug"],
            "market_type": signal["market_type"],
            "entry_price": signal["avg_price"],
            "position_size": round(size, 2),
            "num_traders": signal["num_traders"],
            "avg_win_rate": signal["avg_win_rate"],
            "signal_strength": signal["signal_strength"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 2) if pnl_pct is not None else None,
            "pnl_source": source,
            "won": pnl > 0,
            "real_data": source != "no_data",
        })
    
    # Statistics
    real_trades = [t for t in closed if t["real_data"]]
    wins = [t for t in closed if t["won"]]
    losses = [t for t in closed if t["pnl"] < 0]
    neutral = [t for t in closed if t["pnl"] == 0]
    total_pnl = sum(t["pnl"] for t in closed)
    
    real_wins = [t for t in real_trades if t["won"]]
    real_losses = [t for t in real_trades if t["pnl"] < 0]
    real_pnl = sum(t["pnl"] for t in real_trades)
    
    # 按市场类型统计
    by_type = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0, "real": 0})
    for t in closed:
        mt = t["market_type"]
        by_type[mt]["count"] += 1
        by_type[mt]["pnl"] += t["pnl"]
        if t["won"]:
            by_type[mt]["wins"] += 1
        if t["real_data"]:
            by_type[mt]["real"] += 1
    
    # 按数据源统计
    by_source = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for t in closed:
        src = t["pnl_source"]
        by_source[src]["count"] += 1
        by_source[src]["pnl"] += t["pnl"]
        if t["won"]:
            by_source[src]["wins"] += 1
    
    report = {
        "strategy": "v3 Deep 精选跟单 (完整持仓+市场结算数据)",
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / initial_balance * 100, 2),
        "total_trades": len(closed),
        "real_data_trades": len(real_trades),
        "no_data_trades": len(neutral),
        "wins": len(wins),
        "losses": len(losses),
        "neutral": len(neutral),
        "win_rate_all": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "real_pnl": round(real_pnl, 2),
        "real_wins": len(real_wins),
        "real_losses": len(real_losses),
        "real_win_rate": round(len(real_wins) / max(len(real_wins) + len(real_losses), 1) * 100, 1),
        "elite_traders": len(elite),
        "pnl_sources": dict(pnl_sources),
        "by_type": {k: dict(v) for k, v in by_type.items()},
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "top_trades": sorted(closed, key=lambda x: x["pnl"], reverse=True)[:5],
        "worst_trades": sorted(closed, key=lambda x: x["pnl"])[:5],
        "all_trades": closed,
    }
    return report


def print_deep_report(report):
    print("\n" + "=" * 70, flush=True)
    print(f"📈 {report['strategy']}", flush=True)
    print("=" * 70, flush=True)
    print(f"初始资金: ${report['initial_balance']}", flush=True)
    print(f"最终余额: ${report['final_balance']}", flush=True)
    print(f"总盈亏: ${report['total_pnl']} ({report['roi']}%)", flush=True)
    print(f"精选交易员: {report['elite_traders']} 人", flush=True)
    print(f"\n📊 交易统计:", flush=True)
    print(f"  总交易: {report['total_trades']} 笔", flush=True)
    print(f"  有真实数据: {report['real_data_trades']} 笔", flush=True)
    print(f"  无数据(PnL=0): {report['no_data_trades']} 笔", flush=True)
    print(f"  全部胜率: {report['win_rate_all']}% (胜: {report['wins']} / 负: {report['losses']})", flush=True)
    print(f"  真实数据胜率: {report['real_win_rate']}% (胜: {report['real_wins']} / 负: {report['real_losses']})", flush=True)
    print(f"  真实数据PnL: ${report['real_pnl']}", flush=True)
    
    print(f"\n📊 数据来源分布:", flush=True)
    for src, data in report.get("by_source", {}).items():
        wr = data["wins"] / data["count"] * 100 if data["count"] > 0 else 0
        print(f"  {src:25s} | {data['count']:3d}笔 | 胜率: {wr:.0f}% | PnL: ${data['pnl']:.2f}", flush=True)
    
    print(f"\n📊 按市场类型:", flush=True)
    for mt, data in report.get("by_type", {}).items():
        wr = data["wins"] / data["count"] * 100 if data["count"] > 0 else 0
        print(f"  {mt:12s} | {data['count']}笔 | 胜率: {wr:.0f}% | PnL: ${data['pnl']:.2f} | 有数据: {data['real']}", flush=True)
    
    print(f"\n🏆 最佳交易:", flush=True)
    for t in report.get("top_trades", [])[:5]:
        src = t.get("pnl_source", "?")[:8]
        pnl_str = f"+${t['pnl']:.2f}" if t['pnl'] >= 0 else f"-${abs(t['pnl']):.2f}"
        print(f"  [{src:8s}] {t['title'][:40]:40s} | {t['outcome']:5s} | ${t['position_size']:.0f} | {pnl_str} ({t.get('pnl_pct','?')}%)", flush=True)
    
    print(f"\n💀 最差交易:", flush=True)
    for t in report.get("worst_trades", [])[:5]:
        src = t.get("pnl_source", "?")[:8]
        pnl_str = f"+${t['pnl']:.2f}" if t['pnl'] >= 0 else f"-${abs(t['pnl']):.2f}"
        print(f"  [{src:8s}] {t['title'][:40]:40s} | {t['outcome']:5s} | ${t['position_size']:.0f} | {pnl_str} ({t.get('pnl_pct','?')}%)", flush=True)
    
    print("=" * 70, flush=True)
    return report


if __name__ == "__main__":
    report = backtest_v3_deep()
    print_deep_report(report)
