"""信号生成器 - 将分析结果转化为可执行的交易信号"""

from config import FOLLOW_THRESHOLD, MIN_TRADE_AMOUNT, MAX_TRADE_AMOUNT, MIN_LIQUIDITY


def generate_signals(consensus_signals, whale_signals):
    """
    综合共识信号和鲸鱼信号，生成最终交易信号
    
    评分规则：
    - 共识信号强度 × 0.6
    - 鲸鱼信号加成 × 0.4
    - 流动性过滤
    - 价格合理性过滤（不追高于0.9或低于0.1的市场）
    """
    signal_map = {}

    # 共识信号打分
    for s in consensus_signals:
        key = (s["market_id"], s["side"])
        signal_map[key] = {
            **s,
            "consensus_score": s["signal_strength"],
            "whale_score": 0,
            "final_score": 0,
        }

    # 鲸鱼信号加成
    for w in whale_signals:
        key = (w["market_id"], w["side"])
        if key in signal_map:
            signal_map[key]["whale_score"] += 0.1 * (11 - w["trader_rank"]) / 10
        else:
            signal_map[key] = {
                "market_id": w["market_id"],
                "question": w["question"],
                "side": w["side"],
                "current_price": w["current_price"],
                "token_id": w["token_id"],
                "liquidity": w["liquidity"],
                "num_traders": 1,
                "consensus_score": 0,
                "whale_score": 0.1 * (11 - w["trader_rank"]) / 10,
                "final_score": 0,
            }

    # 计算最终得分 & 过滤
    actionable = []
    for key, signal in signal_map.items():
        signal["final_score"] = round(
            signal["consensus_score"] * 0.6 + signal["whale_score"] * 0.4, 4
        )

        # 过滤条件
        if signal["current_price"] > 0.9 or signal["current_price"] < 0.1:
            continue
        if signal["liquidity"] < MIN_LIQUIDITY:
            continue
        if signal["final_score"] < FOLLOW_THRESHOLD * 0.1:
            continue

        # 计算建议交易金额（信号越强金额越大）
        amount = MIN_TRADE_AMOUNT + (MAX_TRADE_AMOUNT - MIN_TRADE_AMOUNT) * min(signal["final_score"] * 5, 1)
        signal["suggested_amount"] = round(amount, 2)
        actionable.append(signal)

    actionable.sort(key=lambda x: x["final_score"], reverse=True)
    return actionable
