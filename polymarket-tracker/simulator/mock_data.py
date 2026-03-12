"""模拟数据生成器 - 生成 Top 50 交易员和市场数据用于策略验证"""

import random
import json
import time
from datetime import datetime, timedelta

CATEGORIES = ["Politics", "Crypto", "Sports", "Finance", "Tech", "World Events"]
OUTCOMES = ["Yes", "No"]


def generate_mock_traders(count=50):
    """生成模拟交易员数据"""
    traders = []
    for i in range(count):
        win_rate = random.uniform(0.45, 0.85)
        total_trades = random.randint(50, 2000)
        volume = random.uniform(10000, 5000000)
        traders.append({
            "id": f"0x{random.randbytes(20).hex()}",
            "rank": i + 1,
            "username": f"trader_{i+1}",
            "profit": round(volume * (win_rate - 0.5) * random.uniform(0.1, 0.5), 2),
            "volume": round(volume, 2),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "markets_traded": random.randint(5, 100),
            "last_active": (datetime.now() - timedelta(hours=random.randint(0, 48))).isoformat(),
        })
    traders.sort(key=lambda x: x["profit"], reverse=True)
    for i, t in enumerate(traders):
        t["rank"] = i + 1
    return traders


def generate_mock_markets(count=30):
    """生成模拟市场数据"""
    markets = []
    questions = [
        "Will BTC exceed $100k by end of Q1 2026?",
        "Will the Fed cut rates in March 2026?",
        "Will ETH flip BTC market cap in 2026?",
        "Will Trump win 2028 Republican primary?",
        "Will China invade Taiwan by 2027?",
        "Will OpenAI release GPT-6 in 2026?",
        "Will Tesla stock reach $500 by June 2026?",
        "Will there be a US recession in 2026?",
        "Will Solana ETF be approved in 2026?",
        "Will gold reach $3000/oz in 2026?",
        "Will Ukraine join NATO by 2027?",
        "Will Apple release AR glasses in 2026?",
        "Will inflation drop below 2% in 2026?",
        "Will SpaceX land humans on Mars by 2030?",
        "Will the S&P 500 reach 7000 in 2026?",
        "Will a major bank adopt Bitcoin reserves?",
        "Will EU ban TikTok in 2026?",
        "Will Japan raise interest rates again?",
        "Will Dogecoin reach $1 in 2026?",
        "Will there be a major cyber attack on US infrastructure?",
        "Will India's GDP growth exceed 8% in 2026?",
        "Will Netflix stock double in 2026?",
        "Will a new COVID variant cause lockdowns?",
        "Will the US debt ceiling be raised?",
        "Will Ethereum switch to a new consensus?",
        "Will oil prices drop below $50/barrel?",
        "Will Argentina dollarize fully?",
        "Will North Korea conduct nuclear test?",
        "Will global temperatures hit new record?",
        "Will AI replace 10% of jobs by 2027?",
    ]
    for i in range(min(count, len(questions))):
        yes_price = round(random.uniform(0.05, 0.95), 2)
        markets.append({
            "id": f"market_{i+1}",
            "condition_id": f"0x{random.randbytes(32).hex()}",
            "question": questions[i],
            "category": random.choice(CATEGORIES),
            "end_date": (datetime.now() + timedelta(days=random.randint(7, 365))).isoformat(),
            "active": True,
            "liquidity": round(random.uniform(5000, 500000), 2),
            "volume": round(random.uniform(10000, 2000000), 2),
            "volume_24h": round(random.uniform(100, 50000), 2),
            "outcomes": {
                "Yes": {"price": yes_price, "token_id": f"token_yes_{i+1}"},
                "No": {"price": round(1 - yes_price, 2), "token_id": f"token_no_{i+1}"},
            },
        })
    return markets


def generate_mock_trades(traders, markets, trades_per_trader=20):
    """生成模拟交易记录"""
    all_trades = []
    for trader in traders:
        num_trades = random.randint(5, trades_per_trader)
        for _ in range(num_trades):
            market = random.choice(markets)
            side = random.choice(OUTCOMES)
            price = market["outcomes"][side]["price"]
            # 好的交易员倾向于买低卖高
            if trader["win_rate"] > 0.6:
                if side == "Yes" and price < 0.4:
                    price_adj = price * random.uniform(0.8, 1.0)
                elif side == "No" and price < 0.4:
                    price_adj = price * random.uniform(0.8, 1.0)
                else:
                    price_adj = price * random.uniform(0.9, 1.1)
            else:
                price_adj = price * random.uniform(0.85, 1.15)

            price_adj = max(0.01, min(0.99, round(price_adj, 2)))
            amount = round(random.uniform(10, 1000), 2)

            trade_time = datetime.now() - timedelta(
                hours=random.randint(0, 168),
                minutes=random.randint(0, 59)
            )

            all_trades.append({
                "trader_id": trader["id"],
                "trader_rank": trader["rank"],
                "market_id": market["id"],
                "question": market["question"],
                "side": side,
                "action": random.choice(["BUY", "SELL"]),
                "price": price_adj,
                "amount": amount,
                "shares": round(amount / price_adj, 2),
                "timestamp": trade_time.isoformat(),
            })

    all_trades.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_trades


def save_mock_data(data_dir="data"):
    """生成并保存所有模拟数据"""
    traders = generate_mock_traders(50)
    markets = generate_mock_markets(30)
    trades = generate_mock_trades(traders, markets)

    with open(f"{data_dir}/mock_traders.json", "w") as f:
        json.dump(traders, f, indent=2, ensure_ascii=False)

    with open(f"{data_dir}/mock_markets.json", "w") as f:
        json.dump(markets, f, indent=2, ensure_ascii=False)

    with open(f"{data_dir}/mock_trades.json", "w") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)

    print(f"✅ 模拟数据已生成:")
    print(f"   交易员: {len(traders)}")
    print(f"   市场: {len(markets)}")
    print(f"   交易记录: {len(trades)}")
    return traders, markets, trades


if __name__ == "__main__":
    save_mock_data()
