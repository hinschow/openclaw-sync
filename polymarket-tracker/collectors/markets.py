"""市场数据采集 - 获取可交易市场"""

import json
import requests
from config import GAMMA_API, MIN_LIQUIDITY, SIMULATION_MODE


def fetch_active_markets():
    """获取活跃且流动性足够的市场"""
    if SIMULATION_MODE:
        with open("data/mock_markets.json") as f:
            markets = json.load(f)
        return [m for m in markets if m["active"] and m["liquidity"] >= MIN_LIQUIDITY]

    url = f"{GAMMA_API}/markets"
    params = {"active": True, "limit": 100, "order": "volume24hr", "ascending": False}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    markets = resp.json()
    return [m for m in markets if float(m.get("liquidity", 0)) >= MIN_LIQUIDITY]


def get_market_orderbook(token_id):
    """获取市场订单簿"""
    if SIMULATION_MODE:
        return {"bids": [], "asks": [], "mid": 0.5}

    from py_clob_client.client import ClobClient
    client = ClobClient("https://clob.polymarket.com")
    return client.get_order_book(token_id)


if __name__ == "__main__":
    markets = fetch_active_markets()
    print(f"可交易市场: {len(markets)}")
    for m in markets[:5]:
        yes_price = m["outcomes"]["Yes"]["price"]
        print(f"  {m['question'][:50]}... | Yes: {yes_price} | 流动性: ${m['liquidity']}")
