"""排行榜数据采集 - 获取 Top 50 交易员"""

import json
import requests
from config import GAMMA_API, TOP_TRADERS_COUNT, SIMULATION_MODE


def fetch_leaderboard(limit=TOP_TRADERS_COUNT):
    """从 Gamma API 获取排行榜"""
    if SIMULATION_MODE:
        with open("data/mock_traders.json") as f:
            return json.load(f)[:limit]

    # 真实 API 调用
    url = f"{GAMMA_API}/leaderboard"
    params = {"limit": limit}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_trader_positions(trader_id):
    """获取交易员当前持仓"""
    if SIMULATION_MODE:
        with open("data/mock_trades.json") as f:
            trades = json.load(f)
        return [t for t in trades if t["trader_id"] == trader_id]

    # 真实 API: 通过链上数据或 Gamma API 查询
    url = f"{GAMMA_API}/positions"
    params = {"user": trader_id}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    traders = fetch_leaderboard()
    print(f"Top {len(traders)} 交易员:")
    for t in traders[:5]:
        print(f"  #{t['rank']} {t['username']} | 胜率: {t['win_rate']} | 利润: ${t['profit']}")
