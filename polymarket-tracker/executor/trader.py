"""交易执行器 - 模拟/实盘下单"""

import json
import time
from datetime import datetime
from config import SIMULATION_MODE, CLOB_API, CHAIN_ID, PRIVATE_KEY, FUNDER_ADDRESS


class Trader:
    def __init__(self):
        self.portfolio = {"balance": 1000.0, "positions": [], "history": []}
        self._load_portfolio()

    def _load_portfolio(self):
        try:
            with open("data/portfolio.json") as f:
                self.portfolio = json.load(f)
        except FileNotFoundError:
            self._save_portfolio()

    def _save_portfolio(self):
        with open("data/portfolio.json", "w") as f:
            json.dump(self.portfolio, f, indent=2, ensure_ascii=False)

    def execute_signals(self, signals, max_trades=5):
        """执行交易信号"""
        results = []
        for signal in signals[:max_trades]:
            if self.portfolio["balance"] < signal["suggested_amount"]:
                print(f"⚠️ 余额不足，跳过: {signal['question'][:40]}...")
                continue

            result = self._place_order(signal)
            results.append(result)

        self._save_portfolio()
        return results

    def _place_order(self, signal):
        """下单"""
        amount = signal["suggested_amount"]
        price = signal["current_price"]
        shares = round(amount / price, 4)

        if SIMULATION_MODE:
            return self._simulate_order(signal, amount, price, shares)
        else:
            return self._real_order(signal, amount, price, shares)

    def _simulate_order(self, signal, amount, price, shares):
        """模拟下单"""
        order = {
            "id": f"sim_{int(time.time()*1000)}",
            "market_id": signal["market_id"],
            "question": signal["question"],
            "side": signal["side"],
            "price": price,
            "amount": amount,
            "shares": shares,
            "score": signal["final_score"],
            "status": "FILLED",
            "timestamp": datetime.now().isoformat(),
            "mode": "SIMULATION",
        }

        self.portfolio["balance"] -= amount
        self.portfolio["positions"].append(order)
        self.portfolio["history"].append(order)

        print(f"✅ [模拟] {signal['side']} {signal['question'][:40]}... @ {price} | ${amount} | {shares} shares")
        return order

    def _real_order(self, signal, amount, price, shares):
        """真实下单（需要账户配置）"""
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        client = ClobClient(CLOB_API, key=PRIVATE_KEY, chain_id=CHAIN_ID, funder=FUNDER_ADDRESS)
        client.set_api_creds(client.create_or_derive_api_creds())

        order_args = MarketOrderArgs(
            token_id=signal["token_id"],
            amount=amount,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed = client.create_market_order(order_args)
        resp = client.post_order(signed, OrderType.FOK)
        return resp

    def get_portfolio_summary(self):
        """投资组合摘要"""
        total_invested = sum(p["amount"] for p in self.portfolio["positions"])
        return {
            "balance": round(self.portfolio["balance"], 2),
            "positions": len(self.portfolio["positions"]),
            "total_invested": round(total_invested, 2),
            "history_count": len(self.portfolio["history"]),
        }
