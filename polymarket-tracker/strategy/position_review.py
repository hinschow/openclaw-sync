"""
智能换仓策略模块 - Position Review & Swap
评估现有持仓质量，发现更优机会时主动换仓。

评估维度：
1. 价格动量：入场后价格变化趋势（停滞=低分）
2. 持仓时间衰减：持有越久且无进展，分数越低
3. 预期收益：当前价格到目标价的空间
4. 交易量：近期交易量是否萎缩

换仓规则：
- 新机会分 > 弱持仓分 + SWAP_SCORE_THRESHOLD 才换
- 同一 position 24h 内不重复换仓
- 每次最多换 MAX_SWAPS_PER_CHECK 笔
"""
import time
from datetime import datetime, timezone, timedelta

# ── 策略常量（方便后续调整）──
SWAP_SCORE_THRESHOLD = 20       # 新机会需高出弱仓20分才换
SWAP_COOLDOWN_HOURS = 24        # 同一仓位换仓冷却期
MAX_SWAPS_PER_CHECK = 2         # 每次检查最多换仓笔数
MIN_HOLD_HOURS = 6              # 至少持有6小时才考虑换
STALE_PRICE_THRESHOLD = 0.02    # 价格变动<2%视为停滞
STALE_HOURS = 12                # 停滞超过12小时降分
WEAK_SCORE_THRESHOLD = 40       # hold_score 低于此值视为弱仓
THEME_CONCENTRATION_PENALTY = 15 # v13.1: 主题超限时额外扣分


def log(msg):
    print(msg, flush=True)


def _parse_iso(iso_str):
    """安全解析 ISO 时间字符串"""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _hours_since(iso_str):
    """计算从 iso_str 到现在经过的小时数"""
    dt = _parse_iso(iso_str)
    if not dt:
        return 0
    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0, delta.total_seconds() / 3600)


def evaluate_position(position, current_price=None, market_data=None, theme_counts=None):
    """
    评估单个持仓的持有价值，返回 hold_score (0-100)。
    分数越高越值得继续持有，越低越应该考虑换仓。

    参数:
        position: 持仓字典
        current_price: 当前价格（可选，优先使用）
        market_data: 市场附加数据（可选，含 volume 等）
        theme_counts: 主题持仓数统计（可选，用于集中度惩罚）

    返回:
        dict: {
            "hold_score": int (0-100),
            "details": { 各维度分数 },
            "reason": str
        }
    """
    entry_price = position.get("entry_price", 0)
    if current_price is None:
        current_price = position.get("current_price", entry_price)

    # ── 维度1: 价格动量 (0-30分) ──
    # 入场后价格朝有利方向移动 = 高分，停滞或反向 = 低分
    if entry_price > 0:
        price_change_pct = (current_price - entry_price) / entry_price
    else:
        price_change_pct = 0

    if price_change_pct >= 0.10:
        momentum_score = 30  # 涨幅>10%，动量很好
    elif price_change_pct >= 0.05:
        momentum_score = 25
    elif price_change_pct >= 0.02:
        momentum_score = 20  # 小幅上涨
    elif price_change_pct >= -0.02:
        # 停滞区间 (-2% ~ +2%)
        momentum_score = 12
    elif price_change_pct >= -0.05:
        momentum_score = 8
    elif price_change_pct >= -0.10:
        momentum_score = 5
    else:
        momentum_score = 2  # 大幅下跌

    # ── 维度2: 持仓时间衰减 (0-25分) ──
    # 持有时间越长且无进展，分数越低
    hold_hours = _hours_since(position.get("opened_at"))

    if hold_hours < 6:
        time_score = 25  # 刚开仓，给足时间
    elif hold_hours < 12:
        time_score = 22
    elif hold_hours < 24:
        # 12-24h: 如果价格停滞则开始扣分
        if abs(price_change_pct) < STALE_PRICE_THRESHOLD:
            time_score = 14  # 停滞
        else:
            time_score = 20
    elif hold_hours < 48:
        if abs(price_change_pct) < STALE_PRICE_THRESHOLD:
            time_score = 8   # 长时间停滞
        else:
            time_score = 16
    elif hold_hours < 72:
        if abs(price_change_pct) < STALE_PRICE_THRESHOLD:
            time_score = 4
        else:
            time_score = 12
    else:
        # >72h: 除非盈利明显，否则低分
        if price_change_pct >= 0.05:
            time_score = 10
        elif abs(price_change_pct) < STALE_PRICE_THRESHOLD:
            time_score = 2
        else:
            time_score = 6

    # 停滞时间额外惩罚
    if abs(price_change_pct) < STALE_PRICE_THRESHOLD and hold_hours > STALE_HOURS:
        stale_penalty = min(10, int((hold_hours - STALE_HOURS) / 6))  # 每6h多扣1分
        time_score = max(0, time_score - stale_penalty)

    # ── 维度3: 预期收益空间 (0-25分) ──
    # 当前价格到目标价还有多少空间
    target_profit = position.get("target_profit", 0.40)
    if entry_price > 0:
        target_price = entry_price * (1 + target_profit)
        if current_price < target_price:
            remaining_upside = (target_price - current_price) / current_price
        else:
            remaining_upside = 0  # 已达目标
    else:
        remaining_upside = 0

    if remaining_upside >= 0.30:
        expected_score = 25  # 还有30%+空间
    elif remaining_upside >= 0.20:
        expected_score = 22
    elif remaining_upside >= 0.10:
        expected_score = 18
    elif remaining_upside >= 0.05:
        expected_score = 12
    elif remaining_upside > 0:
        expected_score = 8
    else:
        expected_score = 5  # 已接近或超过目标

    # ── 维度4: 交易量/活跃度 (0-20分) ──
    volume_score = 14  # 默认中等分数（无数据时不过度惩罚）

    if market_data:
        volume_24h = market_data.get("volume_24h", 0)
        volume_total = market_data.get("volume_total", 0)

        if volume_24h > 0 and volume_total > 0:
            volume_ratio = volume_24h / volume_total if volume_total > 0 else 0
            if volume_ratio >= 0.05:
                volume_score = 20  # 近24h交易量占比高，活跃
            elif volume_ratio >= 0.02:
                volume_score = 16
            elif volume_ratio >= 0.01:
                volume_score = 12
            else:
                volume_score = 6   # 交易量萎缩
        elif volume_24h == 0:
            volume_score = 4  # 完全无交易量

    # ── 汇总 ──
    hold_score = momentum_score + time_score + expected_score + volume_score
    hold_score = max(0, min(100, hold_score))

    # v13.1: 主题集中度惩罚
    theme_penalty = 0
    if theme_counts:
        try:
            # 动态导入避免循环依赖
            import sys, os
            sim_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "simulator")
            if sim_dir not in sys.path:
                sys.path.insert(0, sim_dir)
            from live_sim import _get_position_theme, MAX_POSITIONS_PER_THEME
            theme = _get_position_theme(position.get("market", ""))
            if theme and theme_counts.get(theme, 0) > MAX_POSITIONS_PER_THEME:
                theme_penalty = THEME_CONCENTRATION_PENALTY
                hold_score = max(0, hold_score - theme_penalty)
        except Exception:
            pass

    # 生成原因描述
    reasons = []
    if momentum_score <= 8:
        reasons.append("价格走势不利")
    if time_score <= 8:
        reasons.append("持仓时间过长且停滞")
    if expected_score <= 8:
        reasons.append("预期收益空间小")
    if volume_score <= 6:
        reasons.append("交易量萎缩")
    if theme_penalty > 0:
        reasons.append("主题过度集中")

    reason = "、".join(reasons) if reasons else "持仓状态正常"

    return {
        "hold_score": hold_score,
        "details": {
            "momentum": momentum_score,
            "time_decay": time_score,
            "expected_return": expected_score,
            "volume": volume_score,
        },
        "reason": reason,
        "price_change_pct": round(price_change_pct * 100, 2),
        "hold_hours": round(hold_hours, 1),
    }


def find_swap_candidates(weak_positions, new_opportunities):
    """
    从弱持仓和新机会中找出换仓建议。

    参数:
        weak_positions: list of (position, evaluation) 弱持仓及其评估
        new_opportunities: list of dict 新机会信号，需含 "opportunity_score" 字段

    返回:
        list of dict: 换仓建议列表，每项含:
            - weak_position: 要平掉的弱仓
            - new_opportunity: 要开的新仓
            - score_diff: 分差
            - reason: 换仓原因
    """
    now = datetime.now(timezone.utc)
    swap_candidates = []

    # 按 hold_score 升序排列弱仓（最弱的优先换）
    weak_sorted = sorted(weak_positions, key=lambda x: x[1]["hold_score"])

    # 按 opportunity_score 降序排列新机会（最好的优先匹配）
    opps_sorted = sorted(new_opportunities, key=lambda x: x.get("opportunity_score", 0), reverse=True)

    used_opps = set()  # 已匹配的新机会 condition_id

    for pos, evaluation in weak_sorted:
        if len(swap_candidates) >= MAX_SWAPS_PER_CHECK:
            break

        hold_score = evaluation["hold_score"]
        cid = pos.get("condition_id", "")

        # 检查持仓时间是否满足最低要求
        hold_hours = _hours_since(pos.get("opened_at"))
        if hold_hours < MIN_HOLD_HOURS:
            continue

        # 检查换仓冷却期
        cooldown_until = _parse_iso(pos.get("swap_cooldown_until"))
        if cooldown_until and now < cooldown_until:
            continue

        # 寻找匹配的新机会
        for opp in opps_sorted:
            opp_cid = opp.get("condition_id", "")
            if opp_cid in used_opps:
                continue
            if opp_cid == cid:
                continue  # 不换到同一个市场

            opp_score = opp.get("opportunity_score", 0)
            score_diff = opp_score - hold_score

            if score_diff >= SWAP_SCORE_THRESHOLD:
                swap_candidates.append({
                    "weak_position": pos,
                    "weak_score": hold_score,
                    "weak_reason": evaluation["reason"],
                    "new_opportunity": opp,
                    "new_score": opp_score,
                    "score_diff": score_diff,
                    "reason": f"弱仓({hold_score}分: {evaluation['reason']}) → 新机会({opp_score}分), 分差{score_diff}",
                })
                used_opps.add(opp_cid)
                break  # 这个弱仓已匹配，处理下一个

    return swap_candidates


def score_opportunity(signal, market_prices=None):
    """
    给新机会信号打分 (0-100)，用于与弱仓 hold_score 比较。

    参数:
        signal: 信号字典（来自 generate_signals 或 scan_short_term_signals）
        market_prices: 市场价格字典（可选）

    返回:
        int: opportunity_score (0-100)
    """
    score = 50  # 基础分

    # 交易员共识加分
    num_traders = signal.get("num_traders", 0)
    if num_traders >= 5:
        score += 20
    elif num_traders >= 3:
        score += 15
    elif num_traders >= 2:
        score += 10
    elif num_traders >= 1:
        score += 5

    # 信号强度加分
    strength = signal.get("signal_strength", 0)
    if strength >= 1.5:
        score += 10
    elif strength >= 1.0:
        score += 7
    elif strength >= 0.5:
        score += 4

    # 市场类型加分
    market_type = signal.get("market_type", "NORMAL")
    if market_type == "HIGH_VALUE":
        score += 8

    # 价格位置加分（中间价位更好）
    avg_price = signal.get("avg_price", 0.5)
    if 0.20 <= avg_price <= 0.80:
        score += 5
    if 0.30 <= avg_price <= 0.70:
        score += 3

    # 总金额加分
    total_usdc = signal.get("total_usdc", 0)
    if total_usdc >= 1000:
        score += 5
    elif total_usdc >= 500:
        score += 3

    # confidence 加分（短线信号）
    confidence = signal.get("confidence", 0)
    if confidence >= 0.8:
        score += 8
    elif confidence >= 0.6:
        score += 5

    return max(0, min(100, score))
