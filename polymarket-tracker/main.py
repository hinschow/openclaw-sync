"""Polymarket 跟单交易工具 - 主入口"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulator.mock_data import save_mock_data
from collectors.leaderboard import fetch_leaderboard
from collectors.markets import fetch_active_markets
from collectors.trades import fetch_all_recent_trades
from strategy.analyzer import analyze_consensus, analyze_whale_moves, generate_report
from strategy.signals import generate_signals
from executor.trader import Trader
from simulator.backtest import backtest_strategy, print_backtest_report


def run_pipeline():
    """运行完整流程"""
    print("🧠 Polymarket 跟单交易工具")
    print("=" * 60)

    # Step 1: 生成模拟数据
    print("\n📦 Step 1: 生成模拟数据...")
    traders, markets, trades = save_mock_data()

    # Step 2: 获取数据
    print("\n📡 Step 2: 加载交易员和市场数据...")
    top_traders = fetch_leaderboard()
    active_markets = fetch_active_markets()
    all_trades = fetch_all_recent_trades(top_traders)
    print(f"   交易员: {len(top_traders)} | 市场: {len(active_markets)} | 交易: {len(all_trades)}")

    # Step 3: 分析策略
    print("\n🔍 Step 3: 分析交易行为...")
    consensus = analyze_consensus(all_trades, active_markets, time_window_hours=168, min_traders=3)
    whales = analyze_whale_moves(all_trades, active_markets, min_amount=200, top_n_traders=10)
    print(generate_report(consensus, whales))

    # Step 4: 生成信号
    print("\n📊 Step 4: 生成交易信号...")
    signals = generate_signals(consensus, whales)
    print(f"   可执行信号: {len(signals)} 个")
    for i, s in enumerate(signals[:5]):
        print(f"   {i+1}. {s['question'][:45]}... | {s['side']} @ {s['current_price']} | 得分: {s['final_score']} | 建议: ${s['suggested_amount']}")

    # Step 5: 模拟执行
    print("\n💰 Step 5: 模拟执行交易...")
    trader = Trader()
    results = trader.execute_signals(signals, max_trades=5)
    summary = trader.get_portfolio_summary()
    print(f"\n   投资组合: 余额 ${summary['balance']} | 持仓 {summary['positions']} 笔 | 已投 ${summary['total_invested']}")

    # Step 6: 回测
    print("\n📈 Step 6: 策略回测 (30天)...")
    report = backtest_strategy(trades, active_markets, initial_balance=1000, days=30)
    print_backtest_report(report)

    print("\n✅ 流程完成！")


if __name__ == "__main__":
    run_pipeline()
