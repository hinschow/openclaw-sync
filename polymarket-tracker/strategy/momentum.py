"""
模块2: 价格动量分析
- 追踪持仓价格历史趋势
- 提前止损（连续5次不利方向 且 亏损>10%）
- 部分止盈（快速上涨+20%）
- 到期前止盈（<48小时且盈利>5%）
"""
import json
import os
import time
import requests
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
PRICE_HISTORY_PATH = os.path.join(DATA_DIR, "price_history.json")
DELAY = 0.3


def log(msg):
    print(msg, flush=True)


def api_get(url, params, timeout=30):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  [momentum] API error: {url} - {e}")
        return None


def load_price_history():
    if os.path.exists(PRICE_HISTORY_PATH):
        try:
            with open(PRICE_HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_price_history(history):
    with open(PRICE_HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def fetch_current_price(slug):
    """通过 slug 获取市场当前价格和到期时间"""
    data = api_get(
        "https://gamma-api.polymarket.com/markets",
        {"slug": slug, "limit": 10}
    )
    time.sleep(DELAY)
    if not data:
        return None

    results = {}
    for m in data:
        cid = m.get("conditionId", "")
        try:
            outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
            prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices", [])
            price_map = {}
            for outcome, price in zip(outcomes, prices):
                try:
                    price_map[outcome] = float(price)
                except (ValueError, TypeError):
                    pass
            if cid and price_map:
                results[cid] = {
                    "prices": price_map,
                    "end_date": m.get("endDate", ""),
                    "closed": m.get("closed", False),
                    "resolved": m.get("resolved", False),
                }
        except Exception:
            pass
    return results


def record_price(history, cid, outcome, current_price):
    """记录价格到历史"""
    key = f"{cid}_{outcome}"
    if key not in history:
        history[key] = {"prices": [], "timestamps": []}

    now_ts = datetime.now(timezone.utc).isoformat()
    history[key]["prices"].append(current_price)
    history[key]["timestamps"].append(now_ts)

    # 只保留最近20条记录
    if len(history[key]["prices"]) > 20:
        history[key]["prices"] = history[key]["prices"][-20:]
        history[key]["timestamps"] = history[key]["timestamps"][-20:]


def check_adverse_trend(history, cid, outcome, entry_price, current_price):
    """
    v12: 硬止损 -5%，不再要求连续下跌。
    亏损超过5%立即触发止损，避免小亏变大亏。
    """
    if entry_price <= 0:
        return False
    loss_pct = (current_price - entry_price) / entry_price
    # v12: 硬止损 -5%（之前要求连续5次下跌 + 亏损>10%，太宽松）
    if loss_pct < -0.05:
        return True
    return False


def check_rapid_gain(entry_price, current_price, threshold=0.12):
    """v12: 从20%降到12%，更早锁定利润，改善盈亏比"""
    if entry_price <= 0:
        return False
    gain_pct = (current_price - entry_price) / entry_price
    return gain_pct >= threshold


def check_near_expiry(end_date_str, hours=48):
    """检查市场是否距离到期<48小时（v9: 从24h扩大到48h）"""
    if not end_date_str:
        return False
    try:
        # 尝试多种日期格式
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
            try:
                end_date = datetime.strptime(end_date_str, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return False

        now = datetime.now(timezone.utc)
        remaining = (end_date - now).total_seconds()
        return 0 < remaining < hours * 3600
    except Exception:
        return False


def analyze_momentum(open_positions):
    """
    分析所有持仓的价格动量
    返回: (momentum_exits, partial_takes, expiry_takes, stats)
    """
    log("  [momentum] 开始价格动量分析...")
    history = load_price_history()

    momentum_exits = []      # 趋势恶化，提前止损
    partial_takes = []       # 快速上涨，部分止盈
    expiry_takes = []        # 到期前止盈

    # 收集需要查价的 slug
    slugs_needed = {}
    for pos in open_positions:
        slug = pos.get("slug", "")
        if slug:
            slugs_needed[slug] = True

    # 批量获取价格
    market_data = {}
    for slug in slugs_needed:
        result = fetch_current_price(slug)
        if result:
            market_data.update(result)

    adverse_count = 0
    rapid_gain_count = 0

    for pos in open_positions:
        cid = pos.get("condition_id", "")
        outcome = pos.get("outcome", "")
        entry_price = pos.get("entry_price", 0)
        slug = pos.get("slug", "")

        mkt = market_data.get(cid)
        if not mkt:
            continue

        current_price = mkt["prices"].get(outcome, entry_price)
        end_date = mkt.get("end_date", "")

        # 记录价格
        record_price(history, cid, outcome, current_price)

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # 规则1: v12 硬止损 -5% → 立即止损
        if check_adverse_trend(history, cid, outcome, entry_price, current_price):
            adverse_count += 1
            momentum_exits.append({
                "type": "momentum_exit",
                "condition_id": cid,
                "outcome": outcome,
                "title": pos.get("market", ""),
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct * 100, 2),
                "reason": "hard_stop_loss_5pct",
            })
            continue  # 已标记退出，不再检查其他规则

        # 规则2: v12 快速上涨超过+12% → 部分止盈（卖出50%）
        if check_rapid_gain(entry_price, current_price, 0.12):
            rapid_gain_count += 1
            partial_takes.append({
                "type": "partial_take",
                "condition_id": cid,
                "outcome": outcome,
                "title": pos.get("market", ""),
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct * 100, 2),
                "sell_ratio": 0.5,  # 卖出50%
                "reason": "rapid_gain_12pct",
            })

        # 规则3: 距离到期<48小时且盈利>5% → 全部止盈（v9: 48h+5%门槛）
        if check_near_expiry(end_date) and pnl_pct > 0.05:
            expiry_takes.append({
                "type": "expiry_take",
                "condition_id": cid,
                "outcome": outcome,
                "title": pos.get("market", ""),
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct * 100, 2),
                "reason": "near_expiry_profitable",
            })

    save_price_history(history)

    stats = {
        "positions_checked": len(open_positions),
        "adverse_trends": adverse_count,
        "rapid_gains": rapid_gain_count,
        "momentum_exits": len(momentum_exits),
        "partial_takes": len(partial_takes),
        "expiry_takes": len(expiry_takes),
    }

    log(f"  [momentum] 完成: {len(momentum_exits)}个趋势恶化, {len(partial_takes)}个快速上涨, {len(expiry_takes)}个到期止盈")
    return momentum_exits, partial_takes, expiry_takes, stats
