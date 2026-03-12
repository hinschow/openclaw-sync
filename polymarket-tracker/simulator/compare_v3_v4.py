#!/usr/bin/env python3
"""v3 vs v4 对比回测"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from simulator.strategy_v3_deep import backtest_v3_deep, print_deep_report
from simulator.strategy_v4 import backtest_v4, print_v4_report

def main():
    print("=" * 70, flush=True)
    print("🔬 v3 Deep vs v4 对比回测", flush=True)
    print("=" * 70, flush=True)

    # Run v3
    print("\n>>> Running v3 Deep...", flush=True)
    v3 = backtest_v3_deep()
    print_deep_report(v3)

    # Run v4
    print("\n>>> Running v4...", flush=True)
    v4 = backtest_v4()
    print_v4_report(v4)

    # Comparison
    print("\n" + "=" * 70, flush=True)
    print("📊 v3 vs v4 对比", flush=True)
    print("=" * 70, flush=True)
    
    metrics = [
        ("最终余额", f"${v3['final_balance']}", f"${v4['final_balance']}"),
        ("总盈亏", f"${v3['total_pnl']} ({v3['roi']}%)", f"${v4['total_pnl']} ({v4['roi']}%)"),
        ("最大回撤", "N/A", f"{v4['max_drawdown_pct']}%"),
        ("总交易", str(v3['total_trades']), str(v4['total_trades'])),
        ("有数据交易", str(v3['real_data_trades']), str(v4['real_data_trades'])),
        ("全部胜率", f"{v3['win_rate_all']}%", f"{v4['win_rate_all']}%"),
        ("真实胜率", f"{v3['real_win_rate']}%", f"{v4['real_win_rate']}%"),
        ("真实PnL", f"${v3['real_pnl']}", f"${v4['real_pnl']}"),
        ("精选交易员", str(v3['elite_traders']), str(v4['elite_traders'])),
        ("止损触发", "N/A", str(v4.get('stop_loss_triggered', 0))),
    ]
    
    print(f"{'指标':15s} | {'v3 Deep':25s} | {'v4':25s}", flush=True)
    print("-" * 70, flush=True)
    for name, v3v, v4v in metrics:
        print(f"{name:15s} | {v3v:25s} | {v4v:25s}", flush=True)
    
    # Winner
    if v4['roi'] > v3['roi']:
        diff = v4['roi'] - v3['roi']
        print(f"\n✅ v4 胜出! ROI 提升 {diff:.2f}%", flush=True)
    else:
        diff = v3['roi'] - v4['roi']
        print(f"\n⚠️ v3 仍然更好, ROI 高 {diff:.2f}%", flush=True)
    
    print("=" * 70, flush=True)
    return v3, v4

if __name__ == "__main__":
    main()
