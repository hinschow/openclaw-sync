"""交易记录采集"""

import json
import requests
from config import GAMMA_API, SIMULATION_MODE


def fetch_trader_trades(trader_id, limit=100):
    """获取指定交易员的交易记录"""
    if SIMULATION_MODE:
        with open("data/mock_trades.json") as f:
            trades = json.load(f)
        return [t for t in trades if t["trader_id"] == trader_id][:limit]

    # 真实 API: Polygon 链上交易 + CLOB API
    url = f"{GAMMA_API}/trades"
    params = {"user": trader_id, "limit": limit}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_all_recent_trades(traders, limit_per_trader=50):
    """批量获取所有跟踪交易员的近期交易"""
    all_trades = []
    for trader in traders:
        trades = fetch_trader_trades(trader["id"], limit=limit_per_trader)
        all_trades.extend(trades)
    all_trades.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_trades
