"""
排行榜采集 + 链上交易追踪
从 Polymarket 排行榜抓取 Top 交易员钱包地址，
通过 CLOB API 和 Gamma API 追踪他们的交易行为。
"""

import requests
import json
import re
import os
import time
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def fetch_top_traders():
    """从 Polymarket 排行榜页面抓取 Top 交易员"""
    all_traders = {}

    for sort_param in ["volume", "pnl"]:
        url = f"https://polymarket.com/leaderboard?sort={sort_param}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()

        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL
        )
        if not match:
            continue

        data = json.loads(match.group(1))
        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]

        for q in queries:
            items = q["state"]["data"]
            if not isinstance(items, list) or not items:
                continue

            first = items[0]
            if isinstance(first, dict) and "proxyWallet" in first and "rank" in first:
                for t in items:
                    wallet = t["proxyWallet"].lower()
                    if wallet not in all_traders:
                        all_traders[wallet] = {
                            "wallet": wallet,
                            "name": t.get("name", wallet[:10]),
                            "rank": t.get("rank", 99),
                            "volume": t.get("amount", 0),
                            "pnl": t.get("pnl", 0),
                        }

            # biggestWins 里的地址
            if isinstance(first, dict) and "winRank" in first:
                for t in items:
                    wallet = t.get("proxyWallet", "").lower()
                    if wallet and wallet not in all_traders:
                        all_traders[wallet] = {
                            "wallet": wallet,
                            "name": t.get("userName", wallet[:10]),
                            "rank": t.get("winRank", 99),
                            "volume": 0,
                            "pnl": 0,
                        }

        time.sleep(0.5)

    # 按 volume 排序，取前50
    traders = sorted(all_traders.values(), key=lambda x: x["volume"], reverse=True)[:50]
    for i, t in enumerate(traders):
        t["rank"] = i + 1

    return traders


DATA_API = "https://data-api.polymarket.com"


def fetch_trader_trades(wallet, limit=100):
    """获取交易员的交易记录"""
    trades = []
    try:
        r = requests.get(
            f"{DATA_API}/activity",
            params={"user": wallet, "limit": limit},
            timeout=15,
        )
        if r.status_code == 200:
            trades = r.json()
    except Exception:
        pass
    return trades


def fetch_trader_positions(wallet, limit=100):
    """获取交易员的当前持仓"""
    positions = []
    try:
        r = requests.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "limit": limit},
            timeout=15,
        )
        if r.status_code == 200:
            positions = r.json()
    except Exception:
        pass
    return positions


def fetch_all_trader_data(traders, trades_limit=100, positions_limit=50):
    """批量获取所有交易员的交易记录和持仓"""
    all_trades = []
    all_positions = []

    for i, trader in enumerate(traders):
        wallet = trader["wallet"]

        # 交易记录
        trades = fetch_trader_trades(wallet, limit=trades_limit)
        for t in trades:
            t["trader_wallet"] = wallet
            t["trader_name"] = trader["name"]
            t["trader_rank"] = trader["rank"]
        all_trades.extend(trades)

        # 持仓
        positions = fetch_trader_positions(wallet, limit=positions_limit)
        for p in positions:
            p["trader_wallet"] = wallet
            p["trader_name"] = trader["name"]
            p["trader_rank"] = trader["rank"]
        all_positions.extend(positions)

        if (i + 1) % 10 == 0:
            print(f"    进度: {i+1}/{len(traders)} | 交易: {len(all_trades)} | 持仓: {len(all_positions)}")
        time.sleep(0.3)

    return all_trades, all_positions


def collect_trader_data():
    """完整采集流程"""
    os.makedirs(DATA_DIR, exist_ok=True)

    print("📡 采集排行榜交易员数据...")

    # 1. 获取 Top 交易员
    print("  抓取排行榜...")
    traders = fetch_top_traders()
    print(f"  ✅ 获取到 {len(traders)} 个交易员")

    with open(os.path.join(DATA_DIR, "real_traders.json"), "w") as f:
        json.dump(traders, f, indent=2, ensure_ascii=False)

    # 2. 获取交易员交易记录和持仓
    print("  获取交易员交易记录和持仓...")
    all_trades, all_positions = fetch_all_trader_data(traders)
    print(f"  ✅ 获取到 {len(all_trades)} 条交易记录, {len(all_positions)} 条持仓")

    with open(os.path.join(DATA_DIR, "real_trader_trades.json"), "w") as f:
        json.dump(all_trades, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, "real_trader_positions.json"), "w") as f:
        json.dump(all_positions, f, indent=2, ensure_ascii=False)

    # 3. 汇总统计
    print(f"\n📊 数据汇总:")
    print(f"  交易员: {len(traders)}")
    print(f"  交易记录: {len(all_trades)}")
    print(f"  持仓记录: {len(all_positions)}")
    print(f"  Top 5 交易员:")
    for t in traders[:5]:
        print(f"    #{t['rank']} {t['name'][:20]:20s} | vol: ${t['volume']:>12,.0f} | pnl: ${t['pnl']:>10,.0f}")

    return traders, all_trades, all_positions


if __name__ == "__main__":
    collect_trader_data()
