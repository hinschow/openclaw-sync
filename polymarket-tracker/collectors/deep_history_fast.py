"""深度历史数据采集 - 并发版本，大幅加速"""

import requests
import json
import os
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DATA_API = "https://data-api.polymarket.com"
BATCH_SIZE = 100
MAX_OFFSET = 4000
WORKERS = 6  # 并发数，不要太高避免被限流

print_lock = threading.Lock()
progress = {"done": 0, "total": 0, "trades": 0}


def deep_fetch_trader(trader_info):
    """深度拉取单个交易员的历史交易"""
    wallet = trader_info["wallet"]
    name = trader_info["name"]
    rank = trader_info["rank"]
    all_trades = []
    offset = 0

    while offset < MAX_OFFSET:
        try:
            r = requests.get(f"{DATA_API}/activity", params={
                "user": wallet, "limit": BATCH_SIZE, "offset": offset
            }, timeout=30)
            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                break
            all_trades.extend(data)
            offset += BATCH_SIZE
            time.sleep(0.3)  # slightly more polite with concurrent requests
        except Exception as e:
            with print_lock:
                print(f"  ⚠ #{rank} {name[:20]} offset={offset} error: {e}", flush=True)
            break

    # Tag trades
    for trade in all_trades:
        trade["trader_wallet"] = wallet
        trade["trader_name"] = name
        trade["trader_rank"] = rank

    summary = None
    if all_trades:
        oldest = datetime.fromtimestamp(all_trades[-1]["timestamp"])
        newest = datetime.fromtimestamp(all_trades[0]["timestamp"])
        span = (newest - oldest).days
        summary = {
            "name": name, "rank": rank, "wallet": wallet,
            "records": len(all_trades), "span_days": span,
            "oldest": oldest.isoformat(), "newest": newest.isoformat(),
        }
        with print_lock:
            progress["done"] += 1
            progress["trades"] += len(all_trades)
            print(f"  #{rank:2d} {name[:20]:20s} | {len(all_trades):5d} 条 | {span:3d} 天 | {oldest.strftime('%Y-%m-%d')} ~ {newest.strftime('%Y-%m-%d')} [{progress['done']}/{progress['total']}]", flush=True)
    else:
        with print_lock:
            progress["done"] += 1
            print(f"  #{rank:2d} {name[:20]:20s} | 无数据 [{progress['done']}/{progress['total']}]", flush=True)

    return all_trades, summary


def run_deep_collection():
    """执行并发深度采集"""
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)

    profitable = [t for t in traders if t.get("pnl", 0) > 0]
    progress["total"] = len(profitable)
    print(f"📡 并发深度采集 {len(profitable)} 个盈利交易员 (workers={WORKERS})...\n", flush=True)

    all_trades = []
    all_summaries = []

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(deep_fetch_trader, t): t for t in profitable}
        for future in as_completed(futures):
            trades, summary = future.result()
            all_trades.extend(trades)
            if summary:
                all_summaries.append(summary)

    elapsed = time.time() - start_time

    # Sort summaries by rank
    all_summaries.sort(key=lambda x: x["rank"])

    # Save
    with open(os.path.join(DATA_DIR, "deep_trades.json"), "w") as f:
        json.dump(all_trades, f, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, "deep_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    # Stats
    total_span = 0
    if all_trades:
        oldest = min(t["timestamp"] for t in all_trades)
        newest = max(t["timestamp"] for t in all_trades)
        total_span = (datetime.fromtimestamp(newest) - datetime.fromtimestamp(oldest)).days

    print(f"\n✅ 深度采集完成 ({elapsed:.0f}秒):")
    print(f"  交易员: {len(all_summaries)}")
    print(f"  总交易记录: {len(all_trades)}")
    print(f"  时间跨度: {total_span} 天")
    fsize = os.path.getsize(os.path.join(DATA_DIR, "deep_trades.json"))
    print(f"  数据大小: {fsize / 1024 / 1024:.1f} MB")

    return all_trades, all_summaries


if __name__ == "__main__":
    run_deep_collection()
