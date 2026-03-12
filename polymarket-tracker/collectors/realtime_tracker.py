"""
模块1: 交易员实时跟单追踪
- 获取精英交易员最近交易
- 对比上次快照识别新交易
- 生成跟单信号：强买入、跟随退出、加仓信心
"""
import json
import os
import time
import requests
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "trader_snapshots.json")
DELAY = 0.3


def log(msg):
    print(msg, flush=True)


def api_get(url, params, timeout=30):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  [realtime_tracker] API error: {url} - {e}")
        return None


def load_snapshots():
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_snapshots(snapshots):
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshots, f, indent=2, ensure_ascii=False)


def fetch_trader_recent(wallet, limit=20, timeout=8):
    """获取单个交易员最近交易"""
    data = api_get(
        "https://data-api.polymarket.com/activity",
        {"user": wallet, "limit": limit},
        timeout=timeout,
    )
    time.sleep(DELAY)
    return data or []


def identify_new_trades(wallet, current_trades, prev_snapshot):
    """对比快照识别新交易"""
    prev_ids = set(prev_snapshot.get(wallet, {}).get("trade_ids", []))
    new_trades = []
    for t in current_trades:
        trade_id = t.get("id") or t.get("transactionHash", "") + str(t.get("timestamp", ""))
        if trade_id and trade_id not in prev_ids:
            new_trades.append(t)
    return new_trades


def build_current_snapshot(wallet, trades):
    """构建当前快照"""
    trade_ids = []
    for t in trades:
        tid = t.get("id") or t.get("transactionHash", "") + str(t.get("timestamp", ""))
        if tid:
            trade_ids.append(tid)
    return {
        "trade_ids": trade_ids[:100],  # 只保留最近100条ID
        "last_check": datetime.now(timezone.utc).isoformat(),
        "trade_count": len(trades),
    }


def analyze_signals(new_trades_by_wallet, elite_traders, open_positions):
    """
    分析新交易生成信号:
    - 强买入信号: ≥3人同时买入同一市场
    - 跟随退出信号: 精英交易员平仓我们持有的市场
    - 加仓信心: 精英交易员加仓
    """
    # 统计每个市场的买入交易员
    market_buys = defaultdict(lambda: {
        "traders": [], "trades": [], "title": "",
        "condition_id": "", "outcome": "", "slug": "",
        "total_usdc": 0,
    })

    # 统计卖出（平仓）
    market_sells = defaultdict(lambda: {
        "traders": [], "trades": [], "title": "",
        "condition_id": "", "outcome": "", "slug": "",
    })

    held_cids = {p.get("condition_id") for p in open_positions}

    for wallet, trades in new_trades_by_wallet.items():
        trader_name = wallet[:10]
        for et in elite_traders:
            if et.get("wallet") == wallet:
                trader_name = et.get("name", "") or et.get("pseudonym", "") or wallet[:10]
                break

        for t in trades:
            if t.get("type") != "TRADE":
                continue
            cid = t.get("conditionId", "")
            outcome = t.get("outcome", "")
            side = t.get("side", "")
            key = (cid, outcome)

            if side == "BUY":
                entry = market_buys[key]
                entry["traders"].append({"wallet": wallet, "name": trader_name})
                entry["trades"].append(t)
                entry["title"] = t.get("title", "")
                entry["condition_id"] = cid
                entry["outcome"] = outcome
                entry["slug"] = t.get("slug", "") or t.get("eventSlug", "")
                entry["total_usdc"] += t.get("usdcSize", 0) or 0
            elif side == "SELL":
                entry = market_sells[key]
                entry["traders"].append({"wallet": wallet, "name": trader_name})
                entry["trades"].append(t)
                entry["title"] = t.get("title", "")
                entry["condition_id"] = cid
                entry["outcome"] = outcome
                entry["slug"] = t.get("slug", "") or t.get("eventSlug", "")

    strong_buy_signals = []
    exit_signals = []
    confidence_signals = []

    # 强买入信号: ≥3人同时买入同一市场
    for key, data in market_buys.items():
        unique_wallets = list({t["wallet"] for t in data["traders"]})
        num_traders = len(unique_wallets)

        if num_traders >= 3:
            prices = [t.get("price", 0) for t in data["trades"] if t.get("price")]
            avg_price = sum(prices) / len(prices) if prices else 0
            strong_buy_signals.append({
                "type": "strong_buy",
                "condition_id": data["condition_id"],
                "outcome": data["outcome"],
                "title": data["title"],
                "slug": data["slug"],
                "num_traders": num_traders,
                "trader_names": [t["name"] for t in data["traders"][:5]],
                "total_usdc": round(data["total_usdc"], 2),
                "avg_price": round(avg_price, 4),
                "weight_multiplier": 2.0,  # 强信号权重x2
            })
        elif num_traders >= 1:
            # 加仓信心信号
            cid = data["condition_id"]
            if cid in held_cids:
                confidence_signals.append({
                    "type": "confidence",
                    "condition_id": cid,
                    "outcome": data["outcome"],
                    "title": data["title"],
                    "num_traders": num_traders,
                    "trader_names": [t["name"] for t in data["traders"][:5]],
                })

    # 跟随退出信号: 精英交易员卖出我们持有的市场
    for key, data in market_sells.items():
        cid = data["condition_id"]
        if cid in held_cids:
            unique_wallets = list({t["wallet"] for t in data["traders"]})
            exit_signals.append({
                "type": "exit",
                "condition_id": cid,
                "outcome": data["outcome"],
                "title": data["title"],
                "num_traders": len(unique_wallets),
                "trader_names": [t["name"] for t in data["traders"][:5]],
            })

    return strong_buy_signals, exit_signals, confidence_signals


def run_realtime_tracking(elite_traders, open_positions):
    """
    主入口：获取精英交易员最新交易，识别新信号
    内存优化版：分批采集，每批5人，批间gc.collect()
    返回: (strong_buy_signals, exit_signals, confidence_signals, stats)
    """
    import gc

    BATCH_SIZE = 5
    TRADE_LIMIT = 20

    log("  [realtime_tracker] 开始采集交易员最新交易...")
    prev_snapshots = load_snapshots()
    new_snapshots = {}
    new_trades_by_wallet = {}
    total_new = 0
    fetched = 0
    total = len(elite_traders)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(0, total, BATCH_SIZE):
        batch = elite_traders[batch_idx:batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1

        for t in batch:
            wallet = t.get("wallet", "")
            if not wallet:
                continue

            trades = fetch_trader_recent(wallet, limit=TRADE_LIMIT, timeout=8)
            if not trades:
                continue

            fetched += 1
            new_trades = identify_new_trades(wallet, trades, prev_snapshots)
            if new_trades:
                new_trades_by_wallet[wallet] = new_trades
                total_new += len(new_trades)

            new_snapshots[wallet] = build_current_snapshot(wallet, trades)
            del trades

        log(f"    [批次 {batch_num}/{total_batches}] 采集 {len(batch)} 人 | 有数据: {fetched} | 新交易: {total_new}")
        del batch
        gc.collect()

    save_snapshots(new_snapshots)
    log(f"  [realtime_tracker] 完成: {fetched}个交易员有数据, {total_new}条新交易")

    strong_buys, exits, confidence = analyze_signals(
        new_trades_by_wallet, elite_traders, open_positions
    )

    # 释放中间数据
    del new_trades_by_wallet
    gc.collect()

    stats = {
        "traders_checked": total,
        "traders_with_data": fetched,
        "new_trades": total_new,
        "strong_buy_signals": len(strong_buys),
        "exit_signals": len(exits),
        "confidence_signals": len(confidence),
    }

    log(f"  [realtime_tracker] 信号: {len(strong_buys)}个强买入, {len(exits)}个退出, {len(confidence)}个加仓信心")
    return strong_buys, exits, confidence, stats
