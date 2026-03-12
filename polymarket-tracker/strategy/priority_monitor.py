"""
分级监控系统
根据持仓状态动态决定检查频率，节省 API 调用

优先级定义:
  HIGH   - 每1小时检查
  MEDIUM - 每4小时检查
  LOW    - 每天检查1次
"""
import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
MONITOR_STATE_PATH = os.path.join(DATA_DIR, "monitor_state.json")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "trader_snapshots.json")

GAMMA_API = "https://gamma-api.polymarket.com"
DELAY = 0.3

# 优先级常量
HIGH = "high"      # 每1小时
MEDIUM = "medium"  # 每4小时
LOW = "low"        # 每天1次

# 检查间隔（秒）
INTERVALS = {
    HIGH: 3600,       # 1小时
    MEDIUM: 14400,    # 4小时
    LOW: 86400,       # 24小时
}

# 美国活跃时段加速因子（UTC 13:00-05:00）
US_ACTIVE_SPEEDUP = 0.5  # 间隔缩短50%

# 止损止盈阈值
STOP_LOSS_PCT = -0.30
TAKE_PROFIT_PCT = 0.50


def log(msg):
    print(msg, flush=True)


def api_get(url, params, timeout=30):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  [priority_monitor] API error: {url} - {e}")
        return None


def load_monitor_state():
    """加载监控状态"""
    if os.path.exists(MONITOR_STATE_PATH):
        try:
            with open(MONITOR_STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_monitor_state(state):
    """保存监控状态"""
    with open(MONITOR_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def fetch_market_end_date(slug):
    """从 gamma API 获取市场到期时间"""
    if not slug:
        return None
    data = api_get(GAMMA_API + "/markets", {"slug": slug, "limit": 1})
    time.sleep(DELAY)
    if data and len(data) > 0:
        end_date_str = data[0].get("endDateIso") or data[0].get("end_date_iso")
        if end_date_str:
            try:
                dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                # 确保有时区信息
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
    return None


def get_hours_to_expiry(position, end_date_cache=None):
    """计算距离到期的小时数，使用缓存避免重复 API 调用"""
    slug = position.get("slug", "")
    cid = position.get("condition_id", "")

    # 先查缓存
    if end_date_cache is not None:
        if cid in end_date_cache:
            end_date = end_date_cache[cid]
            if end_date:
                now = datetime.now(timezone.utc)
                delta = end_date - now
                return delta.total_seconds() / 3600
            return None

    # 从 API 获取
    end_date = fetch_market_end_date(slug)
    if end_date_cache is not None and cid:
        end_date_cache[cid] = end_date

    if end_date:
        now = datetime.now(timezone.utc)
        delta = end_date - now
        return delta.total_seconds() / 3600
    return None


def calc_pnl_pct(position, current_price=None):
    """计算当前盈亏百分比"""
    entry_price = position.get("entry_price", 0)
    if entry_price <= 0:
        return 0
    if current_price is None:
        current_price = position.get("current_price", entry_price)
    return (current_price - entry_price) / entry_price


def check_elite_trader_activity(position):
    """检查是否有精英交易员近期交易该市场"""
    cid = position.get("condition_id", "")
    if not cid:
        return False

    if not os.path.exists(SNAPSHOT_PATH):
        return False

    try:
        with open(SNAPSHOT_PATH) as f:
            snapshots = json.load(f)
    except Exception:
        return False

    # 检查快照中是否有交易员在最近24小时内有活动
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    for wallet, snap in snapshots.items():
        last_check_str = snap.get("last_check", "")
        if not last_check_str:
            continue
        try:
            last_check = datetime.fromisoformat(last_check_str)
            # 确保有时区信息
            if last_check.tzinfo is None:
                last_check = last_check.replace(tzinfo=timezone.utc)
            if last_check > cutoff and snap.get("trade_count", 0) > 0:
                return True
        except Exception:
            continue

    return False


def is_near_threshold(pnl_pct):
    """检查价格是否在关键阈值附近"""
    # 接近止损: -25% ~ -30%
    if -0.30 <= pnl_pct <= -0.25:
        return True
    # 接近止盈: +40% ~ +50%
    if 0.40 <= pnl_pct <= 0.50:
        return True
    return False


def classify_position(position, current_price=None, end_date_cache=None):
    """
    给持仓打优先级标签

    🔴 HIGH（每1小时）:
    - 距离到期 < 48小时
    - 当前盈亏波动 > ±15%
    - 有精英交易员近期活跃交易
    - 价格在关键阈值附近（接近止损或止盈）

    🟡 MEDIUM（每4小时）:
    - 普通持仓，价格相对稳定
    - 距离到期 > 48小时 且 < 2周

    🟢 LOW（每天1次）:
    - 长期持仓，距离到期 > 2周
    - 价格几乎没变化（波动 < 5%）
    """
    reasons = []

    # 1. 计算盈亏
    pnl_pct = calc_pnl_pct(position, current_price)

    # 2. 距离到期时间
    hours_to_expiry = get_hours_to_expiry(position, end_date_cache)

    # 3. HIGH 条件检查
    if hours_to_expiry is not None and hours_to_expiry < 48:
        reasons.append(f"到期<48h({hours_to_expiry:.0f}h)")
        return HIGH, reasons

    if abs(pnl_pct) > 0.15:
        reasons.append(f"盈亏波动大({pnl_pct:+.1%})")
        return HIGH, reasons

    if is_near_threshold(pnl_pct):
        reasons.append(f"接近阈值({pnl_pct:+.1%})")
        return HIGH, reasons

    if check_elite_trader_activity(position):
        reasons.append("精英交易员活跃")
        return HIGH, reasons

    # 4. MEDIUM 条件
    if hours_to_expiry is not None and hours_to_expiry < 336:  # 2周 = 336小时
        reasons.append(f"中期到期({hours_to_expiry:.0f}h)")
        return MEDIUM, reasons

    if abs(pnl_pct) >= 0.05:
        reasons.append(f"中等波动({pnl_pct:+.1%})")
        return MEDIUM, reasons

    # 5. LOW - 默认
    reasons.append("稳定持仓")
    return LOW, reasons


def is_us_active_hours(current_hour_utc):
    """判断是否在美国活跃时段 (UTC 13:00-05:00)"""
    return current_hour_utc >= 13 or current_hour_utc < 5


def should_check_now(position_cid, priority, last_check_time, current_time_utc=None):
    """
    根据优先级和上次检查时间，决定现在是否需要检查
    美国活跃时段间隔缩短50%
    """
    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)

    current_hour = current_time_utc.hour

    # 获取基础间隔
    interval = INTERVALS.get(priority, INTERVALS[MEDIUM])

    # 美国活跃时段加速
    if is_us_active_hours(current_hour):
        interval = int(interval * US_ACTIVE_SPEEDUP)

    # 如果没有上次检查记录，需要检查
    if not last_check_time:
        return True

    # 解析上次检查时间
    try:
        if isinstance(last_check_time, str):
            last_check = datetime.fromisoformat(last_check_time)
        elif isinstance(last_check_time, (int, float)):
            last_check = datetime.fromtimestamp(last_check_time, tz=timezone.utc)
        else:
            return True
    except Exception:
        return True

    # 确保时区
    if last_check.tzinfo is None:
        last_check = last_check.replace(tzinfo=timezone.utc)

    elapsed = (current_time_utc - last_check).total_seconds()
    return elapsed >= interval


def classify_all_positions(positions, end_date_cache=None):
    """
    批量分类所有持仓，返回分类结果和需要检查的持仓列表
    注意：首次运行不调用 API 获取到期时间（太慢），仅用本地数据分类

    Returns:
        results: dict[cid] = {"priority": str, "reasons": list, "should_check": bool}
        to_check: list of positions that need checking now
        skipped: list of positions that can be skipped
        stats: {"high": int, "medium": int, "low": int, "checking": int, "skipped": int}
    """
    if end_date_cache is None:
        end_date_cache = {}

    # 预加载 trader_snapshots 一次，避免每个持仓都读文件
    trader_active = False
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH) as f:
                snapshots = json.load(f)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            for wallet, snap in snapshots.items():
                last_check_str = snap.get("last_check", "")
                if not last_check_str:
                    continue
                try:
                    lc = datetime.fromisoformat(last_check_str)
                    if lc.tzinfo is None:
                        lc = lc.replace(tzinfo=timezone.utc)
                    if lc > cutoff and snap.get("trade_count", 0) > 0:
                        trader_active = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

    state = load_monitor_state()
    now = datetime.now(timezone.utc)
    results = {}
    to_check = []
    skipped = []
    stats = {"high": 0, "medium": 0, "low": 0, "checking": 0, "skipped": 0}

    for pos in positions:
        cid = pos.get("condition_id", "")
        if not cid:
            to_check.append(pos)
            stats["checking"] += 1
            continue

        # 快速分类（不调用 API）
        priority, reasons = _classify_fast(pos, trader_active)
        stats[priority] += 1

        # 获取上次检查时间
        pos_state = state.get(cid, {})
        last_check = pos_state.get("last_check")

        # 判断是否需要检查
        needs_check = should_check_now(cid, priority, last_check, now)

        results[cid] = {
            "priority": priority,
            "reasons": reasons,
            "should_check": needs_check,
            "last_check": last_check,
        }

        if needs_check:
            to_check.append(pos)
            stats["checking"] += 1
        else:
            skipped.append(pos)
            stats["skipped"] += 1

    return results, to_check, skipped, stats


def _classify_fast(position, trader_active=False):
    """
    快速分类（不调用 API），基于本地数据
    """
    reasons = []
    pnl_pct = calc_pnl_pct(position)

    # HIGH 条件
    if abs(pnl_pct) > 0.15:
        reasons.append(f"盈亏波动大({pnl_pct:+.1%})")
        return HIGH, reasons

    if is_near_threshold(pnl_pct):
        reasons.append(f"接近阈值({pnl_pct:+.1%})")
        return HIGH, reasons

    if trader_active:
        # 检查该持仓的交易员是否在活跃列表中
        trader_names = position.get("trader_names", [])
        if trader_names and any(n for n in trader_names if n):
            reasons.append("精英交易员活跃")
            return HIGH, reasons

    # 根据市场名称推断到期时间
    market_name = position.get("market", "").lower()
    entry_date_str = position.get("entry_date", "")

    # 短期市场关键词
    short_term_markers = [
        "february", "feb ", "march 15", "march 31",
        "by february", "by march",
        "up or down", "opening weekend",
    ]
    for marker in short_term_markers:
        if marker in market_name:
            reasons.append(f"短期市场({marker})")
            return HIGH, reasons

    # MEDIUM 条件
    if abs(pnl_pct) >= 0.05:
        reasons.append(f"中等波动({pnl_pct:+.1%})")
        return MEDIUM, reasons

    # 中期市场关键词
    mid_term_markers = [
        "june", "april", "may", "2026 winter olympics",
        "academy awards", "lec 2026",
    ]
    for marker in mid_term_markers:
        if marker in market_name:
            reasons.append(f"中期市场({marker})")
            return MEDIUM, reasons

    # LOW - 默认
    reasons.append("稳定持仓")
    return LOW, reasons


def update_monitor_state(checked_cids, classifications):
    """更新已检查持仓的状态"""
    state = load_monitor_state()
    now_str = datetime.now(timezone.utc).isoformat()

    for cid in checked_cids:
        cls = classifications.get(cid, {})
        state[cid] = {
            "priority": cls.get("priority", MEDIUM),
            "reasons": cls.get("reasons", []),
            "last_check": now_str,
        }

    # 清理不再持有的仓位
    active_cids = set(checked_cids) | set(state.keys())
    # 保留所有，让 live_sim 在需要时清理

    save_monitor_state(state)
    return state
