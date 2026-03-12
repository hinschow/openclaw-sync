"""真实数据采集器 - 从 Polymarket API 获取真实市场和交易数据"""

import requests
import json
import time
import os
from datetime import datetime, timedelta

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def fetch_real_markets(limit=100, min_volume=10000):
    """获取真实活跃市场，按24h交易量排序"""
    all_markets = []
    offset = 0
    batch = 50

    while offset < limit:
        r = requests.get(f"{GAMMA_API}/markets", params={
            "limit": batch,
            "offset": offset,
            "active": True,
            "closed": False,
            "order": "volume24hr",
            "ascending": False,
        }, timeout=15)
        r.raise_for_status()
        batch_data = r.json()
        if not batch_data:
            break
        all_markets.extend(batch_data)
        offset += batch
        time.sleep(0.3)

    # 过滤低交易量市场
    filtered = []
    for m in all_markets:
        vol = float(m.get("volumeNum", 0) or 0)
        liq = float(m.get("liquidityNum", 0) or 0)
        if vol >= min_volume and not m.get("closed"):
            prices = json.loads(m.get("outcomePrices", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
            token_ids = json.loads(m.get("clobTokenIds", "[]"))

            outcome_data = {}
            for i, outcome in enumerate(outcomes):
                outcome_data[outcome] = {
                    "price": float(prices[i]) if i < len(prices) else 0,
                    "token_id": token_ids[i] if i < len(token_ids) else "",
                }

            filtered.append({
                "id": m["id"],
                "condition_id": m.get("conditionId", ""),
                "question": m["question"],
                "slug": m.get("slug", ""),
                "category": m.get("category", ""),
                "end_date": m.get("endDate", ""),
                "active": True,
                "liquidity": liq,
                "volume": vol,
                "volume_24h": float(m.get("volume24hr", 0) or 0),
                "outcomes": outcome_data,
            })

    return filtered


def fetch_market_history(token_id, interval="max", fidelity=60):
    """获取市场价格历史"""
    r = requests.get(f"{CLOB_API}/prices-history", params={
        "market": token_id,
        "interval": interval,
        "fidelity": fidelity,
    }, timeout=15)
    r.raise_for_status()
    return r.json().get("history", [])


def fetch_leaderboard_from_page():
    """
    从 Polymarket 网站抓取排行榜数据
    排行榜 API 不公开，通过页面 JS 数据提取
    """
    import re
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    r = requests.get("https://polymarket.com/leaderboard", headers=headers, timeout=15)
    r.raise_for_status()

    # 尝试从 __NEXT_DATA__ 提取
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return data
        except json.JSONDecodeError:
            pass
    return None


def fetch_recent_activity(market_id, token_id):
    """获取市场近期交易活动（通过价格变动推断）"""
    history = fetch_market_history(token_id, interval="1w", fidelity=5)
    if not history:
        return []

    activities = []
    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]
        price_change = float(curr.get("p", 0)) - float(prev.get("p", 0))

        if abs(price_change) > 0.02:  # 价格变动超过2%视为有意义的交易活动
            activities.append({
                "timestamp": curr.get("t", ""),
                "price": float(curr.get("p", 0)),
                "price_change": round(price_change, 4),
                "direction": "BUY" if price_change > 0 else "SELL",
            })

    return activities


def collect_all_real_data():
    """采集所有真实数据"""
    print("📡 正在采集 Polymarket 真实数据...")

    # 1. 获取活跃市场
    print("  获取活跃市场...")
    markets = fetch_real_markets(limit=100, min_volume=10000)
    print(f"  ✅ 获取到 {len(markets)} 个活跃市场")

    # 2. 获取每个市场的价格历史（用于回测）
    print("  获取价格历史...")
    market_histories = {}
    for i, market in enumerate(markets[:50]):  # 限制前50个
        for outcome, data in market["outcomes"].items():
            if data["token_id"]:
                try:
                    history = fetch_market_history(data["token_id"], interval="max", fidelity=60)
                    if history:
                        market_histories[f"{market['id']}_{outcome}"] = {
                            "market_id": market["id"],
                            "question": market["question"],
                            "outcome": outcome,
                            "token_id": data["token_id"],
                            "history": history,
                        }
                except Exception as e:
                    print(f"    ⚠️ 跳过 {market['question'][:30]}... ({e})")
                time.sleep(0.2)
        if (i + 1) % 10 == 0:
            print(f"    进度: {i+1}/{min(len(markets), 50)}")

    print(f"  ✅ 获取到 {len(market_histories)} 条价格历史")

    # 3. 获取市场活动（价格变动推断交易行为）
    print("  分析市场活动...")
    all_activities = []
    for key, mh in market_histories.items():
        history = mh["history"]
        for i in range(1, len(history)):
            prev_p = float(history[i-1].get("p", 0))
            curr_p = float(history[i].get("p", 0))
            change = curr_p - prev_p
            if abs(change) > 0.01:
                all_activities.append({
                    "market_id": mh["market_id"],
                    "question": mh["question"],
                    "outcome": mh["outcome"],
                    "token_id": mh["token_id"],
                    "timestamp": history[i].get("t", ""),
                    "price": curr_p,
                    "price_change": round(change, 4),
                    "direction": "BUY" if change > 0 else "SELL",
                })

    print(f"  ✅ 检测到 {len(all_activities)} 次显著价格变动")

    # 保存数据
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(os.path.join(DATA_DIR, "real_markets.json"), "w") as f:
        json.dump(markets, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, "real_histories.json"), "w") as f:
        json.dump(market_histories, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, "real_activities.json"), "w") as f:
        json.dump(all_activities, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 真实数据采集完成，已保存到 {DATA_DIR}/")
    return markets, market_histories, all_activities


if __name__ == "__main__":
    collect_all_real_data()
