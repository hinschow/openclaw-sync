"""深度历史数据采集 - 拉取交易员尽可能多的历史交易"""

import requests
import json
import os
import sys
import time
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DATA_API = "https://data-api.polymarket.com"
BATCH_SIZE = 100  # 每次请求条数
MAX_OFFSET = 4000  # API 上限约 4000-5000


def deep_fetch_trader(wallet, name="", max_records=MAX_OFFSET):
    """深度拉取单个交易员的历史交易"""
    all_trades = []
    offset = 0

    while offset < max_records:
        try:
            r = requests.get(f"{DATA_API}/activity", params={
                "user": wallet, "limit": BATCH_SIZE, "offset": offset
            }, timeout=15)
            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                break
            all_trades.extend(data)
            offset += BATCH_SIZE
            time.sleep(0.2)
        except Exception:
            break

    return all_trades


def deep_fetch_all_traders(traders, max_records=MAX_OFFSET):
    """批量深度采集所有交易员"""
    all_trades = []
    trader_summary = []

    for i, t in enumerate(traders):
        wallet = t["wallet"]
        name = t["name"]

        trades = deep_fetch_trader(wallet, name, max_records)

        if trades:
            oldest = datetime.fromtimestamp(trades[-1]["timestamp"])
            newest = datetime.fromtimestamp(trades[0]["timestamp"])
            span = (newest - oldest).days

            for trade in trades:
                trade["trader_wallet"] = wallet
                trade["trader_name"] = name
                trade["trader_rank"] = t["rank"]

            all_trades.extend(trades)
            trader_summary.append({
                "name": name, "rank": t["rank"], "wallet": wallet,
                "records": len(trades), "span_days": span,
                "oldest": oldest.isoformat(), "newest": newest.isoformat(),
            })
            print(f"  #{t['rank']:2d} {name[:20]:20s} | {len(trades):5d} 条 | {span:3d} 天 | {oldest.strftime('%Y-%m-%d')} ~ {newest.strftime('%Y-%m-%d')}", flush=True)
        else:
            print(f"  #{t['rank']:2d} {name[:20]:20s} | 无数据", flush=True)

        if (i + 1) % 5 == 0:
            print(f"    --- 进度: {i+1}/{len(traders)} | 总交易: {len(all_trades)} ---", flush=True)

    return all_trades, trader_summary


def run_deep_collection():
    """执行深度采集"""
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)

    # 只采集盈利交易员（节省时间和API调用）
    profitable = [t for t in traders if t.get("pnl", 0) > 0]
    print(f"📡 深度采集 {len(profitable)} 个盈利交易员的历史数据...\n")

    all_trades, summary = deep_fetch_all_traders(profitable)

    # 保存
    with open(os.path.join(DATA_DIR, "deep_trades.json"), "w") as f:
        json.dump(all_trades, f, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, "deep_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 统计
    total_span = 0
    if all_trades:
        oldest = min(t["timestamp"] for t in all_trades)
        newest = max(t["timestamp"] for t in all_trades)
        total_span = (datetime.fromtimestamp(newest) - datetime.fromtimestamp(oldest)).days

    print(f"\n✅ 深度采集完成:")
    print(f"  交易员: {len(summary)}")
    print(f"  总交易记录: {len(all_trades)}")
    print(f"  时间跨度: {total_span} 天")
    print(f"  数据大小: {os.path.getsize(os.path.join(DATA_DIR, 'deep_trades.json')) / 1024 / 1024:.1f} MB")

    return all_trades, summary


if __name__ == "__main__":
    run_deep_collection()
