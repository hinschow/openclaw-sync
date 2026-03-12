#!/usr/bin/env python3
"""
每日模拟盘系统 - Live Simulation v8
从 Polymarket API 实时采集数据，生成跟单信号，管理模拟持仓。

v8 新增：
- 短线高频交易策略 (short_term)
- short_check 轻量模式
- 短线/长线持仓分类管理
- 止盈/止损/超时独立参数

用法:
  python3 live_sim.py full        — 完整运行（默认）
  python3 live_sim.py check       — 轻量检查持仓 + 扫描新信号
  python3 live_sim.py short_check — 只检查短线持仓止盈止损
"""
import gc
import json
import os
import signal as signal_mod
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

MAX_OPEN_POSITIONS = 15  # v12: 40→15 集中仓位

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SIM_PORTFOLIO_PATH = os.path.join(DATA_DIR, "sim_portfolio.json")

# ── v12: Telegram 直发 ──
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5689003327")


def notify_telegram(text):
    """直接通过 Telegram Bot API 发送消息，不依赖 agent"""
    if not TELEGRAM_BOT_TOKEN:
        log("  [telegram] TELEGRAM_BOT_TOKEN 未设置，跳过通知")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        # Telegram 消息限制 4096 字符
        if len(text) > 4000:
            text = text[:3950] + "\n...(截断)"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if resp.status_code == 200:
            log("  [telegram] 通知已发送")
            return True
        else:
            log(f"  [telegram] 发送失败: {resp.status_code} {resp.text[:100]}")
            return False
    except Exception as e:
        log(f"  [telegram] 发送异常: {e}")
        return False
TRADERS_PATH = os.path.join(DATA_DIR, "expanded_traders.json")

INITIAL_BALANCE = 1000.0
BASE_AMOUNT = 30.0           # v7: 最小仓位从$10提高到$30
MAX_AMOUNT = 50.0            # v9: 最大仓位$80→$50（降低大仓位风险）
STOP_LOSS_PCT = -0.30
TAKE_PROFIT_PCT = 0.40       # v9: 长线止盈 +50%→+40%（更早锁定）
MAX_POSITION_PCT = 0.15      # v7: 基础仓位15%资金
STRONG_POSITION_PCT = 0.20   # v9: 强信号25%→20%资金
MIN_TRADER_CONSENSUS = 3     # v7: 至少3个交易员共识
STRONG_SIGNAL_TRADERS = 5    # v7: ≥5人为强信号
DELAY = 0.2

# ── 分批处理参数 ──
BATCH_POSITIONS = 15       # 持仓检查每批数量
BATCH_SLUGS = 10           # 价格查询每批 slug 数
BATCH_TRADERS = 10         # 交易员采集每批数量
BATCH_EVENTS = 5           # 事件过滤每批市场数
API_TIMEOUT = 10           # 单个 API 请求超时（秒）
BATCH_TIMEOUT = 60         # 单批处理超时（秒）
TOTAL_TIMEOUT = 480        # 总运行超时（秒）


class TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("总运行超时，保存当前状态并退出")


def set_total_timeout(seconds):
    """设置总运行超时保护"""
    try:
        signal_mod.signal(signal_mod.SIGALRM, _timeout_handler)
        signal_mod.alarm(seconds)
    except (AttributeError, OSError):
        pass  # Windows 不支持 SIGALRM


def clear_timeout():
    try:
        signal_mod.alarm(0)
    except (AttributeError, OSError):
        pass


# ── v5: 导入新模块 ──
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from collectors.realtime_tracker import run_realtime_tracking
    V5_REALTIME = True
except ImportError as e:
    V5_REALTIME = False
    print(f"  [v5] realtime_tracker 不可用: {e}", flush=True)

try:
    from strategy.momentum import analyze_momentum
    V5_MOMENTUM = True
except ImportError as e:
    V5_MOMENTUM = False
    print(f"  [v5] momentum 不可用: {e}", flush=True)

try:
    from strategy.event_filter import batch_filter_signals
    V5_EVENT_FILTER = True
except ImportError as e:
    V5_EVENT_FILTER = False
    print(f"  [v5] event_filter 不可用: {e}", flush=True)

try:
    from strategy.priority_monitor import (
        classify_all_positions, update_monitor_state,
        HIGH, MEDIUM, LOW
    )
    V6_PRIORITY = True
except ImportError as e:
    V6_PRIORITY = False
    print(f"  [v6] priority_monitor 不可用: {e}", flush=True)

# v8: 短线策略模块
try:
    from strategy.short_term import scan_short_term_signals
    V8_SHORT_TERM = True
except ImportError as e:
    V8_SHORT_TERM = False
    print(f"  [v8] short_term 不可用: {e}", flush=True)

# v12: 智能换仓模块
try:
    from strategy.position_review import (
        evaluate_position, find_swap_candidates, score_opportunity,
        SWAP_SCORE_THRESHOLD, SWAP_COOLDOWN_HOURS, MAX_SWAPS_PER_CHECK,
        MIN_HOLD_HOURS, WEAK_SCORE_THRESHOLD,
    )
    V12_SWAP = True
except ImportError as e:
    V12_SWAP = False
    print(f"  [v12] position_review 不可用: {e}", flush=True)

# ── v12: 保守风控参数 ──
SHORT_TERM_SIZE_MIN = 30     # v12: 短线最小$30
SHORT_TERM_SIZE_MAX = 40     # v12: 短线最大$40
LONG_TERM_SIZE_MIN = 30
LONG_TERM_SIZE_MAX = 50
SHORT_TERM_BUDGET_PCT = 0.70
LONG_TERM_BUDGET_PCT = 0.20
SAFETY_CUSHION_PCT = 0.10
MAX_LONG_POSITIONS = 5         # v12: 长线最多5笔
MAX_SHORT_POSITIONS = 10       # v12: 短线最多10笔
AGGRESSIVE_CASH_THRESHOLD = 0.50   # v12: 余额>50%才考虑加仓
AGGRESSIVE_SCAN_THRESHOLD = 0.60   # v12: 余额>60%才主动扫描
AGGRESSIVE_SHORT_SIZE_MAX = 40     # v12: 短线上限$40
PROACTIVE_PROBE_SIZE = 20          # v12: 试探仓位$20

# ── v12: 每日亏损熔断 ──
DAILY_LOSS_LIMIT = 50.0            # 单日最大亏损 $50
DAILY_LOSS_LIMIT_PCT = 0.05        # 或余额的 5%

# ── v13: 主题去重限制 ──
MAX_POSITIONS_PER_THEME = 2        # v13.1: 同一主题最多持有2笔（从3收紧）
THEME_KEYWORDS = {
    "iran_strike": ["us strikes iran", "strike iran", "us or israel strike iran"],
    "khamenei": ["khamenei"],
    "trump": ["trump"],
    "fed_rate": ["fed", "interest rate", "federal reserve"],
    "russia_ukraine": ["russia", "ukraine", "ceasefire"],
    "netanyahu": ["netanyahu"],
    "china_taiwan": ["china", "taiwan"],
    "starmer": ["starmer"],
    "venezuela": ["machado", "venezuela"],
}

def check_daily_loss_circuit_breaker(portfolio):
    """
    v12: 检查今日已实现亏损是否超过熔断线。
    返回 True 表示触发熔断，应停止开新仓。
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_realized_loss = 0.0
    for trade in portfolio.get("trade_history", []):
        trade_date = trade.get("date", "")[:10]
        if trade_date == today:
            pnl = trade.get("pnl", 0)
            if pnl < 0:
                today_realized_loss += pnl  # negative number

    # 动态熔断线：取 $50 和余额5% 中较小的
    balance = portfolio.get("balance", 0)
    limit = min(DAILY_LOSS_LIMIT, balance * DAILY_LOSS_LIMIT_PCT)
    if abs(today_realized_loss) >= limit:
        log(f"  🚨 [v12] 每日亏损熔断触发！今日已亏 ${abs(today_realized_loss):.2f} >= 限额 ${limit:.2f}")
        log(f"  🚨 [v12] 停止所有新开仓，仅执行平仓/止损")
        return True
    return False


def _get_position_theme(title):
    """提取持仓的主题标签"""
    t = title.lower()
    for theme, keywords in THEME_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return theme
    return None

def _count_theme_positions(portfolio):
    """统计当前各主题的持仓数"""
    theme_counts = {}
    for pos in portfolio.get("open_positions", []):
        theme = _get_position_theme(pos.get("market", ""))
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
    return theme_counts

def _theme_allows_new(portfolio, title):
    """检查该主题是否还能开新仓"""
    theme = _get_position_theme(title)
    if not theme:
        return True  # 无主题标签的不限制
    counts = _count_theme_positions(portfolio)
    return counts.get(theme, 0) < MAX_POSITIONS_PER_THEME

# ── 从 strategy_v4 复制的核心过滤逻辑 ──

BLACKLIST_KEYWORDS = [
    "up or down", "up/down",
    "tweets from", "posts from",
    "game 1 winner", "game 2 winner", "game 3 winner",
    "game 4 winner", "game 5 winner",
]

SPREAD_KEYWORDS = ["spread:", "spread -"]

SPORTS_KEYWORDS = [
    "win on 2026-", "win on 2025-", "vs.", "vs ",
    "nba", "nfl", "nhl", "mlb", "ncaa", "lol:", "t20",
    "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "copa del", "ligue 1",
    "open:", "grand prix", "world cup",
    # v7: 扩展体育/电竞过滤
    "sports", "esports", "football", "soccer", "basketball",
    "tennis", "cricket", "boxing", "mma",
    "lec", "blast", "epl",
    "six nations", "rugby", "ice hockey",
    "biathlon", "eurovision",
    # v8: 更多体育/电竞关键词
    "championships", "dubai", "atp", "wta", "grand slam",
    "wimbledon", "roland garros", "australian open", "us open",
    "europa league", "olympics", "medal",
    "f1", "formula", "nascar", "golf", "pga", "tour",
    "ufc", "wwe", "super bowl", "stanley cup",
    "world series", "march madness",
    "dota", "csgo", "valorant", "league of legends", "overwatch",
    "rio open", "medellín", "medellin",
    " fc ", "fc ", "united ", "city ",
]

# v7: 短线加密涨跌盘
CRYPTO_SHORT_KEYWORDS = [
    "up or down", "o/u",
    "btc-updown", "eth-updown", "sol-updown",
]

SHORT_TERM_KEYWORDS = [
    "highest temperature", "lowest temperature",
    "weather", "°f on", "°c on",
    "price at", "price on", "close above", "close below",
    "between", "exactly",
]

HIGH_VALUE_KEYWORDS = [
    "shutdown", "trump", "election", "president", "congress",
    "fed", "interest rate", "inflation", "recession",
    "war", "invasion", "nato", "sanctions",
    "etf", "approve", "ban",
]


def log(msg):
    print(msg, flush=True)


def classify_market(title):
    t = title.lower()
    if any(kw in t for kw in BLACKLIST_KEYWORDS):
        return "BLACKLIST"
    if any(kw in t for kw in SPREAD_KEYWORDS):
        return "SPREAD"
    if any(kw in t for kw in SPORTS_KEYWORDS):
        return "SPORTS"
    if any(kw in t for kw in CRYPTO_SHORT_KEYWORDS):
        return "CRYPTO_SHORT"
    if any(kw in t for kw in SHORT_TERM_KEYWORDS):
        return "SHORT_TERM"
    if any(kw in t for kw in HIGH_VALUE_KEYWORDS):
        return "HIGH_VALUE"
    return "NORMAL"


def api_get(url, params, timeout=API_TIMEOUT):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  API error: {url} - {e}")
        return None


# ── 数据采集 ──

def load_traders():
    """流式加载交易员数据，减少内存峰值"""
    try:
        import ijson
        traders = []
        with open(TRADERS_PATH, "rb") as f:
            for item in ijson.items(f, "item"):
                traders.append(item)
        return traders
    except ImportError:
        # fallback: 标准加载
        with open(TRADERS_PATH) as f:
            traders = json.load(f)
        return traders


def select_elite_traders(traders):
    """筛选精选交易员：PnL > 0 的交易员，按PnL排序取前MAX_TRACKED_TRADERS个。"""
    MAX_TRACKED_TRADERS = 30
    elite = []
    for t in traders:
        pnl = t.get("pnl", 0)
        source = t.get("source", "")
        trade_count = t.get("trade_count", 0)
        if source == "original" and pnl > 0:
            elite.append(t)
        elif source == "discovered" and trade_count >= 5:
            elite.append(t)
    # 按PnL降序排序，取前30个
    elite.sort(key=lambda x: x.get("pnl", 0), reverse=True)
    if len(elite) > MAX_TRACKED_TRADERS:
        log(f"  精选交易员: {len(elite)}个符合条件，截取前{MAX_TRACKED_TRADERS}个（按PnL排序）")
        elite = elite[:MAX_TRACKED_TRADERS]
    log(f"  精选交易员: {len(elite)}/{len(traders)}")
    return elite


def fetch_trader_activities(elite_traders, hours=24):
    """分批获取精选交易员最近交易活动，每批5个，批间 gc.collect()"""
    BATCH = 5
    LIMIT_PER_TRADER = 20
    cutoff = int(time.time()) - hours * 3600
    all_activities = []
    fetched = 0
    total = len(elite_traders)
    total_batches = (total + BATCH - 1) // BATCH

    for batch_idx in range(0, total, BATCH):
        batch = elite_traders[batch_idx:batch_idx + BATCH]
        batch_num = batch_idx // BATCH + 1
        batch_activities = []
        batch_fetched = 0

        for t in batch:
            wallet = t["wallet"]
            activities = api_get(
                "https://data-api.polymarket.com/activity",
                {"user": wallet, "limit": LIMIT_PER_TRADER},
                timeout=8,
            )
            time.sleep(DELAY)

            if not activities:
                continue

            recent = []
            for a in activities:
                ts = a.get("timestamp", 0)
                if ts >= cutoff and a.get("side") == "BUY" and a.get("type") == "TRADE":
                    recent.append(a)

            if recent:
                batch_activities.extend(recent)
                batch_fetched += 1

            # 释放单次响应
            del activities, recent

        fetched += batch_fetched
        all_activities.extend(batch_activities)
        log(f"    [交易员批次 {batch_num}/{total_batches}] 采集 {len(batch)} 人，有活动 {batch_fetched}，交易 {len(batch_activities)} 条")

        # 释放批次内存
        del batch, batch_activities
        gc.collect()

        # 批间休息
        if batch_idx + BATCH < total:
            time.sleep(1)

    log(f"  获取了 {fetched} 个交易员的活动，共 {len(all_activities)} 条近{hours}h BUY交易")
    return all_activities


def fetch_market_prices(slug_to_cids):
    """分批获取市场当前价格，每批 BATCH_SLUGS 个 slug。"""
    market_prices = {}
    unique_slugs = list(slug_to_cids.keys())
    total = len(unique_slugs)
    log(f"  需要查询 {total} 个市场价格 (by slug)，分 {(total + BATCH_SLUGS - 1) // BATCH_SLUGS} 批...")

    for batch_idx in range(0, total, BATCH_SLUGS):
        batch = unique_slugs[batch_idx:batch_idx + BATCH_SLUGS]
        batch_num = batch_idx // BATCH_SLUGS + 1
        total_batches = (total + BATCH_SLUGS - 1) // BATCH_SLUGS
        batch_found = 0

        for slug in batch:
            if not slug:
                continue
            data = api_get(
                "https://gamma-api.polymarket.com/markets",
                {"slug": slug, "limit": 10}
            )
            time.sleep(DELAY)

            if data:
                for m in data:
                    cid = m.get("conditionId", "")
                    try:
                        outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
                        prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices", [])
                        closed = m.get("closed", False)
                        resolved = m.get("resolved", False)
                        active = m.get("active", True)

                        price_map = {}
                        for outcome, price in zip(outcomes, prices):
                            try:
                                price_map[outcome] = float(price)
                            except:
                                pass

                        if cid and price_map:
                            market_prices[cid] = {
                                "prices": price_map,
                                "closed": closed,
                                "resolved": resolved,
                                "active": active,
                                "question": m.get("question", ""),
                                "slug": m.get("slug", ""),
                            }
                            batch_found += 1
                    except Exception as e:
                        log(f"    解析市场 {slug[:30]}... 失败: {e}")

        log(f"    [价格批次 {batch_num}/{total_batches}] 查询 {len(batch)} 个 slug，获取 {batch_found} 个价格")
        # 释放批次临时变量
        del batch
        gc.collect()

    log(f"  获取了 {len(market_prices)} 个市场的价格")
    return market_prices


# ── 信号生成 ──

def generate_signals(activities, elite_wallets):
    """从最近活动生成跟单信号"""
    sig_map = defaultdict(lambda: {
        "traders": {},
        "total_usdc": 0,
        "trades": [],
        "title": "",
        "outcome": "",
        "condition_id": "",
        "slug": "",
    })

    for a in activities:
        wallet = a.get("proxyWallet", "")
        if wallet not in elite_wallets:
            continue

        title = a.get("title", "")
        market_type = classify_market(title)
        if market_type in ("BLACKLIST", "SPORTS", "SPREAD", "SHORT_TERM", "CRYPTO_SHORT"):
            continue

        cid = a.get("conditionId", "")
        outcome = a.get("outcome", "")
        key = (cid, outcome)

        usdc = a.get("usdcSize", 0) or 0

        sig = sig_map[key]
        sig["traders"][wallet] = elite_wallets[wallet]
        sig["total_usdc"] += usdc
        sig["trades"].append(a)
        sig["title"] = title
        sig["outcome"] = outcome
        sig["condition_id"] = cid
        sig["slug"] = a.get("slug", "") or a.get("eventSlug", "")
        sig["market_type"] = market_type

    signals = []
    for key, sig in sig_map.items():
        prices = [t.get("price", 0) for t in sig["trades"] if t.get("price")]
        avg_price = sum(prices) / len(prices) if prices else 0

        if avg_price <= 0.05 or avg_price >= 0.95:
            continue

        num_traders = len(sig["traders"])
        market_type = sig.get("market_type", "NORMAL")
        type_multiplier = 1.5 if market_type == "HIGH_VALUE" else 1.0

        strength = (
            num_traders / 5
            * type_multiplier
            * min(sig["total_usdc"] / 500, 2.0)
        )

        signals.append({
            "condition_id": sig["condition_id"],
            "outcome": sig["outcome"],
            "title": sig["title"],
            "slug": sig["slug"],
            "market_type": market_type,
            "num_traders": num_traders,
            "total_usdc": round(sig["total_usdc"], 2),
            "avg_price": round(avg_price, 4),
            "signal_strength": round(strength, 4),
            "trader_wallets": list(sig["traders"].keys()),
            "trader_names": [
                next((t.get("name", "") or t.get("pseudonym", "") for t in sig["trades"] if t.get("proxyWallet") == w), w[:10])
                for w in list(sig["traders"].keys())[:5]
            ],
        })

    signals.sort(key=lambda x: x["signal_strength"], reverse=True)
    return signals


# ── 模拟盘管理 ──

def load_portfolio():
    if os.path.exists(SIM_PORTFOLIO_PATH):
        with open(SIM_PORTFOLIO_PATH) as f:
            return json.load(f)
    return {
        "balance": INITIAL_BALANCE,
        "open_positions": [],
        "closed_positions": [],
        "daily_log": [],
    }


def save_portfolio(portfolio):
    with open(SIM_PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, ensure_ascii=False, separators=(",", ":"))


def check_existing_positions(portfolio, market_prices):
    """分批检查已有持仓：结算、止损、止盈、未实现盈亏。每批 BATCH_POSITIONS 笔。"""
    all_closed_today = []
    all_stop_losses = []
    all_take_profits = []
    total_unrealized_pnl = 0
    still_open = []

    positions = portfolio["open_positions"]
    total = len(positions)
    total_batches = (total + BATCH_POSITIONS - 1) // BATCH_POSITIONS if total > 0 else 0

    for batch_idx in range(0, max(total, 1), BATCH_POSITIONS):
        if batch_idx >= total:
            break
        batch = positions[batch_idx:batch_idx + BATCH_POSITIONS]
        batch_num = batch_idx // BATCH_POSITIONS + 1

        batch_stop = 0
        batch_tp = 0
        batch_settled = 0
        batch_unrealized = 0

        for pos in batch:
            cid = pos["condition_id"]
            outcome = pos["outcome"]
            entry_price = pos["entry_price"]
            size = pos["size"]

            market = market_prices.get(cid)

            if market:
                current_price = market["prices"].get(outcome, entry_price)
                is_closed = market.get("closed", False) or market.get("resolved", False)

                if is_closed:
                    pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
                    pnl = size * pnl_pct
                    pos["exit_price"] = current_price
                    pos["pnl"] = round(pnl, 2)
                    pos["pnl_pct"] = round(pnl_pct * 100, 2)
                    pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    pos["exit_reason"] = "settled"
                    portfolio["balance"] += size + pnl
                    portfolio["closed_positions"].append(pos)
                    all_closed_today.append(pos)
                    batch_settled += 1
                    continue

                pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

                if pnl_pct <= STOP_LOSS_PCT:
                    pnl = size * pnl_pct
                    pos["exit_price"] = current_price
                    pos["pnl"] = round(pnl, 2)
                    pos["pnl_pct"] = round(pnl_pct * 100, 2)
                    pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    pos["exit_reason"] = "stop_loss"
                    portfolio["balance"] += size + pnl
                    portfolio["closed_positions"].append(pos)
                    all_stop_losses.append(pos)
                    batch_stop += 1
                    continue

                if pnl_pct >= TAKE_PROFIT_PCT:
                    pnl = size * pnl_pct
                    pos["exit_price"] = current_price
                    pos["pnl"] = round(pnl, 2)
                    pos["pnl_pct"] = round(pnl_pct * 100, 2)
                    pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    pos["exit_reason"] = "take_profit"
                    portfolio["balance"] += size + pnl
                    portfolio["closed_positions"].append(pos)
                    all_take_profits.append(pos)
                    batch_tp += 1
                    continue

                u_pnl = size * pnl_pct
                pos["current_price"] = current_price
                pos["unrealized_pnl"] = round(u_pnl, 2)
                batch_unrealized += u_pnl
                still_open.append(pos)
            else:
                pos["unrealized_pnl"] = 0
                still_open.append(pos)

        total_unrealized_pnl += batch_unrealized
        log(f"    [批次 {batch_num}/{total_batches}] 检查 {len(batch)} 笔，止损 {batch_stop}，止盈 {batch_tp}，结算 {batch_settled}")

        # 每批处理完立即保存，防止中途崩溃丢失
        portfolio["open_positions"] = still_open + positions[batch_idx + BATCH_POSITIONS:]
        save_portfolio(portfolio)

        # 释放批次内存
        del batch
        gc.collect()

    portfolio["open_positions"] = still_open
    return all_closed_today, all_stop_losses, all_take_profits, round(total_unrealized_pnl, 2)


# ── v5: 动量退出执行 ──

def execute_momentum_exits(portfolio, momentum_exits, partial_takes, expiry_takes, market_prices):
    """执行动量分析产生的退出/部分止盈"""
    v5_momentum_closed = []
    v5_partial_closed = []
    v5_expiry_closed = []
    still_open = []

    exit_cids = {e["condition_id"] for e in momentum_exits}
    partial_cids = {e["condition_id"] for e in partial_takes}
    expiry_cids = {e["condition_id"] for e in expiry_takes}

    for pos in portfolio["open_positions"]:
        cid = pos["condition_id"]
        outcome = pos["outcome"]
        entry_price = pos["entry_price"]
        size = pos["size"]

        market = market_prices.get(cid)
        current_price = market["prices"].get(outcome, entry_price) if market else entry_price

        # 动量退出（趋势恶化）
        if cid in exit_cids:
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            pnl = size * pnl_pct
            pos["exit_price"] = current_price
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 2)
            pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pos["exit_reason"] = "momentum_exit"
            portfolio["balance"] += size + pnl
            portfolio["closed_positions"].append(pos)
            v5_momentum_closed.append(pos)
            continue

        # 到期前止盈（全部）
        if cid in expiry_cids:
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            pnl = size * pnl_pct
            pos["exit_price"] = current_price
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 2)
            pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pos["exit_reason"] = "expiry_take_profit"
            portfolio["balance"] += size + pnl
            portfolio["closed_positions"].append(pos)
            v5_expiry_closed.append(pos)
            continue

        # 部分止盈（卖出50%）
        if cid in partial_cids:
            sell_size = round(size * 0.5, 2)
            keep_size = round(size - sell_size, 2)
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            pnl = sell_size * pnl_pct
            # 记录部分平仓
            partial_pos = dict(pos)
            partial_pos["size"] = sell_size
            partial_pos["exit_price"] = current_price
            partial_pos["pnl"] = round(pnl, 2)
            partial_pos["pnl_pct"] = round(pnl_pct * 100, 2)
            partial_pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            partial_pos["exit_reason"] = "partial_take_profit"
            portfolio["balance"] += sell_size + pnl
            portfolio["closed_positions"].append(partial_pos)
            v5_partial_closed.append(partial_pos)
            # 保留剩余仓位
            pos["size"] = keep_size
            u_pnl = keep_size * pnl_pct
            pos["current_price"] = current_price
            pos["unrealized_pnl"] = round(u_pnl, 2)
            still_open.append(pos)
            continue

        still_open.append(pos)

    portfolio["open_positions"] = still_open
    return v5_momentum_closed, v5_partial_closed, v5_expiry_closed


# ── v5: 跟随退出执行 ──

def execute_follow_exits(portfolio, exit_signals, market_prices):
    """执行跟随退出信号"""
    follow_closed = []
    still_open = []
    exit_cids = {e["condition_id"] for e in exit_signals}

    for pos in portfolio["open_positions"]:
        cid = pos["condition_id"]
        outcome = pos["outcome"]
        entry_price = pos["entry_price"]
        size = pos["size"]

        if cid in exit_cids:
            market = market_prices.get(cid)
            current_price = market["prices"].get(outcome, entry_price) if market else entry_price
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            pnl = size * pnl_pct
            pos["exit_price"] = current_price
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 2)
            pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pos["exit_reason"] = "follow_exit"
            portfolio["balance"] += size + pnl
            portfolio["closed_positions"].append(pos)
            follow_closed.append(pos)
        else:
            still_open.append(pos)

    portfolio["open_positions"] = still_open
    return follow_closed


def open_new_positions(portfolio, signals, market_prices):
    """根据信号开新仓位 (v7: 严格入场条件 + 新仓位计算, v9: 闲置资金动态门槛)"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_positions = []

    existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
    current_count = len(portfolio["open_positions"])
    long_count = sum(1 for p in portfolio["open_positions"] if p.get("strategy") != "short_term")

    # v11: 长线持仓数硬限制
    if long_count >= MAX_LONG_POSITIONS:
        log(f"  [v11] 长线持仓已达上限 {MAX_LONG_POSITIONS}，跳过长线开仓")
        return new_positions

    # v12: 闲置资金动态门槛（不再降到1人）
    total_assets = portfolio["balance"] + sum(p["size"] for p in portfolio["open_positions"])
    cash_ratio = portfolio["balance"] / total_assets if total_assets > 0 else 0
    if cash_ratio > AGGRESSIVE_SCAN_THRESHOLD:
        dynamic_consensus = 2  # v12: 余额>60%也至少2人共识
        log(f"  [v12] 闲置资金{cash_ratio:.0%}>60%，共识门槛降至2人")
    elif cash_ratio > AGGRESSIVE_CASH_THRESHOLD:
        dynamic_consensus = 2  # v12: 余额>50%也至少2人共识
        log(f"  [v12] 闲置资金{cash_ratio:.0%}>50%，共识门槛降至2人")
    else:
        dynamic_consensus = MIN_TRADER_CONSENSUS

    for sig in signals:
        cid = sig["condition_id"]
        if cid in existing_cids:
            continue

        # v13: 主题去重
        sig_title = sig.get("title", "")
        if not _theme_allows_new(portfolio, sig_title):
            theme = _get_position_theme(sig_title)
            log(f"  [v13] 主题'{theme}'已达{MAX_POSITIONS_PER_THEME}笔上限，跳过: {sig_title[:40]}")
            continue

        # v7: 持仓数硬限制
        if current_count >= MAX_OPEN_POSITIONS:
            log(f"  达到最大持仓数 {MAX_OPEN_POSITIONS}，停止开仓")
            break

        # v7/v9: 最低共识人数要求（动态门槛）
        if sig["num_traders"] < dynamic_consensus:
            continue

        # v7: 必须通过事件过滤（排除体育/电竞/短线加密）
        market_type = sig.get("market_type", "NORMAL")
        if market_type in ("BLACKLIST", "SPORTS", "SPREAD", "SHORT_TERM", "CRYPTO_SHORT"):
            continue

        # v7: 排除短线盘标题
        title_lower = sig.get("title", "").lower()
        if "up or down" in title_lower or "o/u" in title_lower:
            continue
        import re
        if re.search(r'win on 2026-\d{2}-\d{2}', title_lower):
            continue

        if portfolio["balance"] < BASE_AMOUNT:
            break

        market = market_prices.get(cid)
        if market:
            entry_price = market["prices"].get(sig["outcome"], sig["avg_price"])
        else:
            entry_price = sig["avg_price"]

        if entry_price <= 0.05 or entry_price >= 0.95:
            continue

        # v7: 新仓位计算
        num_traders = sig["num_traders"]
        if num_traders >= STRONG_SIGNAL_TRADERS:
            # 强信号：25%资金
            size = portfolio["balance"] * STRONG_POSITION_PCT
        else:
            # 基础仓位：15%资金
            size = portfolio["balance"] * MAX_POSITION_PCT

        # v7: 事件过滤调整
        ef = sig.get("event_filter", {})
        ef_multiplier = ef.get("multiplier", 1.0)
        size *= ef_multiplier

        # v7: 仓位上下限 (v8: 使用长线参数)
        size = max(LONG_TERM_SIZE_MIN, min(size, LONG_TERM_SIZE_MAX))
        size = round(size, 2)

        if size > portfolio["balance"]:
            continue

        portfolio["balance"] -= size
        portfolio["balance"] = round(portfolio["balance"], 2)

        position = {
            "market": sig["title"],
            "outcome": sig["outcome"],
            "entry_price": round(entry_price, 4),
            "size": size,
            "entry_date": today,
            "condition_id": cid,
            "slug": sig["slug"],
            "signal_strength": sig["signal_strength"],
            "num_traders": sig["num_traders"],
            "trader_names": sig.get("trader_names", []),
            "market_type": market_type,
            "v5_event_filter": ef.get("action", "none"),
            "strategy": "long_term",
            "target_profit": TAKE_PROFIT_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
            "max_hold_until": None,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        portfolio["open_positions"].append(position)
        existing_cids.add(cid)
        current_count += 1
        new_positions.append(position)

    return new_positions


def migrate_positions_v8(portfolio):
    """v8: 给现有持仓添加短线/长线标记"""
    migrated = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for pos in portfolio["open_positions"]:
        if "strategy" not in pos:
            pos["strategy"] = "long_term"
            pos["target_profit"] = TAKE_PROFIT_PCT
            pos["stop_loss_pct"] = STOP_LOSS_PCT
            pos["max_hold_until"] = None
            pos["opened_at"] = pos.get("entry_date", now_iso) + "T00:00:00+00:00" if "T" not in pos.get("entry_date", "") else pos.get("entry_date", now_iso)
            migrated += 1
    if migrated:
        log(f"  [v8] 迁移 {migrated} 个持仓为 long_term")
    return migrated


def get_budget_allocation(portfolio):
    """v11: 计算短线/长线预算分配（短线优先70/20/10）"""
    total_assets = portfolio["balance"] + sum(p["size"] for p in portfolio["open_positions"])
    short_positions = [p for p in portfolio["open_positions"] if p.get("strategy") == "short_term"]
    long_positions = [p for p in portfolio["open_positions"] if p.get("strategy") != "short_term"]
    short_invested = sum(p["size"] for p in short_positions)
    long_invested = sum(p["size"] for p in long_positions)
    short_budget = max(0, total_assets * SHORT_TERM_BUDGET_PCT - short_invested)
    long_budget = max(0, total_assets * LONG_TERM_BUDGET_PCT - long_invested)
    # v11: 长线持仓数硬限制
    if len(long_positions) >= MAX_LONG_POSITIONS:
        long_budget = 0
    # v11: 短线持仓数硬限制
    if len(short_positions) >= MAX_SHORT_POSITIONS:
        short_budget = 0
    cash_ratio = portfolio["balance"] / total_assets if total_assets > 0 else 0
    return {
        "total_assets": round(total_assets, 2),
        "short_budget": round(min(short_budget, portfolio["balance"]), 2),
        "long_budget": round(min(long_budget, portfolio["balance"]), 2),
        "short_invested": round(short_invested, 2),
        "long_invested": round(long_invested, 2),
        "short_count": len(short_positions),
        "long_count": len(long_positions),
        "cash_ratio": round(cash_ratio, 4),
    }


def open_short_term_positions(portfolio, signals, market_prices):
    """v11: 根据短线信号开仓（短线优先模式）"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    new_positions = []

    existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
    current_count = len(portfolio["open_positions"])
    short_count = sum(1 for p in portfolio["open_positions"] if p.get("strategy") == "short_term")
    budget = get_budget_allocation(portfolio)

    # v11: 短线持仓数硬限制
    if short_count >= MAX_SHORT_POSITIONS:
        log(f"  [v11] 短线持仓已达上限 {MAX_SHORT_POSITIONS}，跳过短线开仓")
        return new_positions

    # v11: 动态仓位上限 - 更激进
    cash_ratio = budget.get("cash_ratio", 0)
    size_min = SHORT_TERM_SIZE_MIN
    size_max = AGGRESSIVE_SHORT_SIZE_MAX

    if budget["short_budget"] < size_min:
        log(f"  [v10] 短线预算不足: ${budget['short_budget']:.2f}")
        return new_positions

    remaining_budget = budget["short_budget"]

    for sig in signals:
        cid = sig.get("condition_id", "")
        if cid in existing_cids:
            continue

        # v13: 主题去重
        sig_title = sig.get("market", sig.get("title", ""))
        if not _theme_allows_new(portfolio, sig_title):
            theme = _get_position_theme(sig_title)
            log(f"  [v13] 主题'{theme}'已达{MAX_POSITIONS_PER_THEME}笔上限，跳过短线: {sig_title[:40]}")
            continue

        if current_count >= MAX_OPEN_POSITIONS:
            log(f"  达到最大持仓数 {MAX_OPEN_POSITIONS}，停止开仓")
            break
        if short_count >= MAX_SHORT_POSITIONS:
            log(f"  短线持仓达上限 {MAX_SHORT_POSITIONS}，停止开仓")
            break

        entry_price = sig.get("current_price", 0)
        market = market_prices.get(cid)
        if market:
            entry_price = market["prices"].get(sig["outcome"], entry_price)

        if entry_price <= 0.05 or entry_price >= 0.95:
            continue

        # v11: 短线仓位按confidence调整，动态上限
        confidence = sig.get("confidence", 0.5)
        size = size_min + (size_max - size_min) * confidence
        size = round(min(size, remaining_budget, portfolio["balance"]), 2)

        if size < size_min:
            break

        max_hold_hours = sig.get("max_hold_hours", 48)
        max_hold_until = (datetime.now(timezone.utc) + timedelta(hours=max_hold_hours)).isoformat()

        portfolio["balance"] -= size
        portfolio["balance"] = round(portfolio["balance"], 2)
        remaining_budget -= size

        position = {
            "market": sig["market"],
            "outcome": sig["outcome"],
            "entry_price": round(entry_price, 4),
            "size": size,
            "entry_date": today,
            "condition_id": cid,
            "slug": sig.get("slug", ""),
            "signal_strength": confidence,
            "num_traders": sig.get("num_traders", 0),
            "trader_names": sig.get("trader_names", []),
            "market_type": sig.get("strategy", "short_term"),
            "strategy": "short_term",
            "short_strategy": sig.get("strategy", ""),
            "target_profit": sig.get("target_profit", 0.15),
            "stop_loss_pct": sig.get("stop_loss", -0.10),
            "max_hold_until": max_hold_until,
            "max_hold_hours": max_hold_hours,
            "opened_at": now_iso,
        }
        portfolio["open_positions"].append(position)
        existing_cids.add(cid)
        current_count += 1
        short_count += 1
        new_positions.append(position)

    log(f"  [v11] 短线开仓: {len(new_positions)} 笔")
    return new_positions


def check_short_term_exits(portfolio, market_prices):
    """v8: 检查短线持仓的止盈/止损/超时"""
    now = datetime.now(timezone.utc)
    short_closed = []
    still_open = []

    for pos in portfolio["open_positions"]:
        if pos.get("strategy") != "short_term":
            still_open.append(pos)
            continue

        cid = pos["condition_id"]
        outcome = pos["outcome"]
        entry_price = pos["entry_price"]
        size = pos["size"]
        target_profit = pos.get("target_profit", 0.15)
        stop_loss_pct = pos.get("stop_loss_pct", -0.10)
        max_hold_until = pos.get("max_hold_until")

        market = market_prices.get(cid)
        if not market:
            # 无价格数据，保留
            still_open.append(pos)
            continue

        current_price = market["prices"].get(outcome, entry_price)
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        exit_reason = None

        # 止盈
        if pnl_pct >= target_profit:
            exit_reason = f"short_tp({pos.get('short_strategy', '')})"
        # 止损
        elif pnl_pct <= stop_loss_pct:
            exit_reason = f"short_sl({pos.get('short_strategy', '')})"
        # 超时
        elif max_hold_until:
            try:
                hold_deadline = datetime.fromisoformat(max_hold_until.replace("Z", "+00:00"))
                if now >= hold_deadline:
                    exit_reason = f"short_timeout({pos.get('short_strategy', '')})"
            except Exception:
                pass
        # 市场已关闭
        if not exit_reason and (market.get("closed") or market.get("resolved")):
            exit_reason = "short_settled"

        if exit_reason:
            pnl = size * pnl_pct
            pos["exit_price"] = current_price
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 2)
            pos["exit_date"] = now.strftime("%Y-%m-%d")
            pos["exit_reason"] = exit_reason
            portfolio["balance"] += size + pnl
            portfolio["balance"] = round(portfolio["balance"], 2)
            portfolio["closed_positions"].append(pos)
            short_closed.append(pos)
        else:
            pos["current_price"] = current_price
            pos["unrealized_pnl"] = round(size * pnl_pct, 2)
            still_open.append(pos)

    portfolio["open_positions"] = still_open
    return short_closed


# ── v12: 智能换仓执行 ──

def execute_position_swaps(portfolio, signals, market_prices):
    """
    v12: 评估现有持仓，发现弱仓时尝试换到更优机会。
    换仓 = 平掉弱仓 + 开新仓，记录换仓原因。
    返回: (swap_results, review_summary)
    """
    if not V12_SWAP:
        return [], {}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.strftime("%Y-%m-%d")

    # 1. 评估所有持仓
    weak_positions = []
    all_evaluations = []
    theme_counts = _count_theme_positions(portfolio)

    for pos in portfolio["open_positions"]:
        cid = pos.get("condition_id", "")
        outcome = pos.get("outcome", "")

        # 获取当前价格
        market = market_prices.get(cid)
        current_price = None
        market_data = None
        if market:
            current_price = market["prices"].get(outcome)
            market_data = market  # 可扩展 volume 等

        evaluation = evaluate_position(pos, current_price, market_data, theme_counts=theme_counts)
        all_evaluations.append((pos, evaluation))

        # 记录评估时间
        pos["last_review_at"] = now_iso

        if evaluation["hold_score"] < WEAK_SCORE_THRESHOLD:
            weak_positions.append((pos, evaluation))

    review_summary = {
        "total_reviewed": len(all_evaluations),
        "weak_count": len(weak_positions),
        "avg_score": round(sum(e["hold_score"] for _, e in all_evaluations) / max(len(all_evaluations), 1), 1),
        "evaluations": [(p.get("market", "")[:40], e["hold_score"], e["reason"]) for p, e in all_evaluations],
    }

    if not weak_positions:
        log(f"  [v12] 持仓评估完成: {len(all_evaluations)}笔，无弱仓（均>{WEAK_SCORE_THRESHOLD}分）")
        return [], review_summary

    log(f"  [v12] 发现 {len(weak_positions)} 个弱仓（<{WEAK_SCORE_THRESHOLD}分）:")
    for pos, ev in weak_positions:
        log(f"    • {pos['market'][:40]} | {ev['hold_score']}分 | {ev['reason']}")

    # 2. 给新机会打分
    existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
    scored_opportunities = []
    for sig in signals:
        sig_cid = sig.get("condition_id", "")
        if sig_cid in existing_cids:
            continue
        opp_score = score_opportunity(sig, market_prices)
        sig_with_score = dict(sig)
        sig_with_score["opportunity_score"] = opp_score
        scored_opportunities.append(sig_with_score)

    if not scored_opportunities:
        log(f"  [v12] 无可用新机会，跳过换仓")
        return [], review_summary

    log(f"  [v12] 评估了 {len(scored_opportunities)} 个新机会，最高分: {max(o['opportunity_score'] for o in scored_opportunities)}")

    # 3. 匹配换仓
    swap_candidates = find_swap_candidates(weak_positions, scored_opportunities)

    if not swap_candidates:
        log(f"  [v12] 无满足条件的换仓（需分差>{SWAP_SCORE_THRESHOLD}）")
        return [], review_summary

    # 4. 执行换仓
    swap_results = []
    for swap in swap_candidates:
        weak_pos = swap["weak_position"]
        new_opp = swap["new_opportunity"]

        # 平掉弱仓
        cid = weak_pos["condition_id"]
        outcome = weak_pos["outcome"]
        entry_price = weak_pos["entry_price"]
        size = weak_pos["size"]

        market = market_prices.get(cid)
        current_price = market["prices"].get(outcome, entry_price) if market else entry_price
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        pnl = size * pnl_pct

        weak_pos["exit_price"] = current_price
        weak_pos["pnl"] = round(pnl, 2)
        weak_pos["pnl_pct"] = round(pnl_pct * 100, 2)
        weak_pos["exit_date"] = today
        weak_pos["exit_reason"] = f"swap_out(score={swap['weak_score']})"
        portfolio["balance"] += size + pnl
        portfolio["balance"] = round(portfolio["balance"], 2)
        portfolio["closed_positions"].append(weak_pos)

        # 从 open_positions 移除
        portfolio["open_positions"] = [
            p for p in portfolio["open_positions"]
            if p.get("condition_id") != cid
        ]

        # 开新仓
        new_cid = new_opp.get("condition_id", "")
        new_outcome = new_opp.get("outcome", "")
        new_market = market_prices.get(new_cid)
        if new_market:
            new_entry_price = new_market["prices"].get(new_outcome, new_opp.get("avg_price", 0.5))
        else:
            new_entry_price = new_opp.get("avg_price", 0.5)

        new_size = min(size, portfolio["balance"])  # 用原仓位大小，不超过余额
        if new_size < 10:
            log(f"  [v12] 余额不足开新仓，跳过: {new_opp.get('title', '')[:40]}")
            continue

        portfolio["balance"] -= new_size
        portfolio["balance"] = round(portfolio["balance"], 2)

        new_position = {
            "market": new_opp.get("title", "") or new_opp.get("market", ""),
            "outcome": new_outcome,
            "entry_price": round(new_entry_price, 4),
            "size": round(new_size, 2),
            "entry_date": today,
            "condition_id": new_cid,
            "slug": new_opp.get("slug", ""),
            "signal_strength": new_opp.get("signal_strength", 0) or new_opp.get("confidence", 0),
            "num_traders": new_opp.get("num_traders", 0),
            "trader_names": new_opp.get("trader_names", []),
            "market_type": new_opp.get("market_type", "NORMAL"),
            "strategy": weak_pos.get("strategy", "long_term"),
            "target_profit": weak_pos.get("target_profit", TAKE_PROFIT_PCT),
            "stop_loss_pct": weak_pos.get("stop_loss_pct", STOP_LOSS_PCT),
            "max_hold_until": weak_pos.get("max_hold_until"),
            "opened_at": now_iso,
            "swap_from": weak_pos.get("market", "")[:50],
            "swap_reason": swap["reason"],
            "swap_cooldown_until": (now + timedelta(hours=SWAP_COOLDOWN_HOURS)).isoformat(),
            "last_review_at": now_iso,
        }
        portfolio["open_positions"].append(new_position)

        swap_result = {
            "closed": {
                "market": weak_pos.get("market", ""),
                "pnl": round(pnl, 2),
                "hold_score": swap["weak_score"],
                "reason": swap["weak_reason"],
            },
            "opened": {
                "market": new_position["market"],
                "entry_price": new_position["entry_price"],
                "size": new_position["size"],
                "opportunity_score": swap["new_score"],
            },
            "score_diff": swap["score_diff"],
        }
        swap_results.append(swap_result)

        log(f"  [v12] 换仓: {weak_pos['market'][:35]}({swap['weak_score']}分) → {new_position['market'][:35]}({swap['new_score']}分) 分差{swap['score_diff']}")

    log(f"  [v12] 完成换仓 {len(swap_results)} 笔")
    return swap_results, review_summary


# ── 报告生成 ──

def generate_report(portfolio, signals, new_positions, closed_today, stop_losses, take_profits, unrealized_pnl, v5_stats=None):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = []
    lines.append(f"📊 Polymarket 模拟盘日报 v5 - {today}")
    lines.append("=" * 50)

    # v5 新模块统计
    if v5_stats:
        rt = v5_stats.get("realtime", {})
        mo = v5_stats.get("momentum", {})
        ef = v5_stats.get("event_filter", {})
        lines.append(f"\n🆕 v5 模块:")
        lines.append(f"  🔄 跟单信号: {rt.get('strong_buy_signals', 0)}个强信号, {rt.get('exit_signals', 0)}个退出信号")
        lines.append(f"  📈 动量分析: {mo.get('momentum_exits', 0)}个趋势恶化, {mo.get('rapid_gains', 0)}个快速上涨")
        lines.append(f"  📰 事件过滤: {ef.get('skipped', 0)}个被过滤, {ef.get('boosted', 0)}个被加强")

    # 今日新信号
    lines.append(f"\n🔍 今日信号: {len(signals)} 个")
    for i, sig in enumerate(signals[:10]):
        direction = sig["outcome"]
        lines.append(
            f"  {i+1}. {sig['title'][:50]}"
            f"\n     方向: {direction} | 入场价: {sig['avg_price']:.2f}"
            f" | 强度: {sig['signal_strength']:.3f}"
            f" | 交易员: {sig['num_traders']}人"
        )
    if len(signals) > 10:
        lines.append(f"  ... 还有 {len(signals)-10} 个信号")

    # 持仓变动
    lines.append(f"\n📈 持仓变动:")
    lines.append(f"  新开仓: {len(new_positions)} 笔")
    for p in new_positions:
        ef_tag = f" [{p.get('v5_event_filter', '')}]" if p.get('v5_event_filter', 'none') != 'none' else ""
        lines.append(f"    • {p['market'][:45]} | {p['outcome']} @ {p['entry_price']:.2f} | ${p['size']}{ef_tag}")

    lines.append(f"  结算平仓: {len(closed_today)} 笔")
    for p in closed_today:
        pnl_str = f"+${p['pnl']:.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):.2f}"
        lines.append(f"    • {p['market'][:45]} | {pnl_str}")

    lines.append(f"  止损平仓: {len(stop_losses)} 笔")
    for p in stop_losses:
        lines.append(f"    • {p['market'][:45]} | -${abs(p['pnl']):.2f}")

    lines.append(f"  止盈平仓: {len(take_profits)} 笔")
    for p in take_profits:
        lines.append(f"    • {p['market'][:45]} | +${p['pnl']:.2f}")

    # v5 额外平仓
    if v5_stats:
        v5c = v5_stats.get("v5_closed", {})
        if v5c.get("momentum"):
            lines.append(f"  🔻 动量止损: {len(v5c['momentum'])} 笔")
            for p in v5c["momentum"]:
                lines.append(f"    • {p['market'][:45]} | {p.get('pnl', 0):+.2f}")
        if v5c.get("partial"):
            lines.append(f"  📉 部分止盈: {len(v5c['partial'])} 笔")
            for p in v5c["partial"]:
                lines.append(f"    • {p['market'][:45]} | +${p.get('pnl', 0):.2f} (50%)")
        if v5c.get("expiry"):
            lines.append(f"  ⏰ 到期止盈: {len(v5c['expiry'])} 笔")
            for p in v5c["expiry"]:
                lines.append(f"    • {p['market'][:45]} | +${p.get('pnl', 0):.2f}")
        if v5c.get("follow"):
            lines.append(f"  🔄 跟随退出: {len(v5c['follow'])} 笔")
            for p in v5c["follow"]:
                pnl_str = f"+${p['pnl']:.2f}" if p.get('pnl', 0) >= 0 else f"-${abs(p.get('pnl', 0)):.2f}"
                lines.append(f"    • {p['market'][:45]} | {pnl_str}")

        # v8: 短线统计
        v8s = v5_stats.get("v8_short", {})
        v8_closed = v5_stats.get("v8_short_closed", [])
        v8_new = v5_stats.get("v8_short_new", [])
        if v8s.get("catalyst") or v8s.get("reversion") or v8s.get("expiry") or v8_closed or v8_new:
            lines.append(f"\n⚡ v8 短线策略:")
            lines.append(f"  信号: 催化剂{v8s.get('catalyst', 0)} + 回归{v8s.get('reversion', 0)} + 到期{v8s.get('expiry', 0)} + 高量{v8s.get('high_volume', 0)}")
            if v8_new:
                lines.append(f"  短线开仓: {len(v8_new)} 笔")
                for p in v8_new:
                    lines.append(f"    • {p['market'][:40]} | {p['outcome']} @ {p['entry_price']:.2f} | ${p['size']} | {p.get('short_strategy', '')}")
            if v8_closed:
                lines.append(f"  短线平仓: {len(v8_closed)} 笔")
                for p in v8_closed:
                    pnl_str = f"+${p['pnl']:.2f}" if p.get('pnl', 0) >= 0 else f"-${abs(p.get('pnl', 0)):.2f}"
                    lines.append(f"    • {p['market'][:40]} | {pnl_str} | {p.get('exit_reason', '')}")

        # v10: 主动扫描统计
        v10_new = v5_stats.get("v10_proactive_new", [])
        if v10_new:
            elite_count = sum(1 for p in v10_new if 'elite' in p.get('short_strategy', ''))
            probe_count = sum(1 for p in v10_new if 'probe' in p.get('short_strategy', ''))
            lines.append(f"\n🔍 v10 主动扫描开仓: {len(v10_new)} 笔 (精英{elite_count} + 试探{probe_count})")
            for p in v10_new:
                lines.append(f"    • {p['market'][:40]} | {p['outcome']} @ {p['entry_price']:.2f} | ${p['size']} | {p.get('short_strategy', '')}")

        # v12: 换仓统计
        swap_results = v5_stats.get("v12_swaps", [])
        review_summary = v5_stats.get("v12_review", {})
        if swap_results:
            lines.append(f"\n🔄 v12 智能换仓: {len(swap_results)} 笔")
            for sw in swap_results:
                c = sw["closed"]
                o = sw["opened"]
                pnl_str = f"+${c['pnl']:.2f}" if c['pnl'] >= 0 else f"-${abs(c['pnl']):.2f}"
                lines.append(f"  平仓: {c['market'][:35]} | {c['hold_score']}分 | {pnl_str} | {c['reason']}")
                lines.append(f"  开仓: {o['market'][:35]} | {o['opportunity_score']}分 | @{o['entry_price']:.2f} | ${o['size']}")
        if review_summary and review_summary.get("total_reviewed", 0) > 0:
            lines.append(f"  📊 持仓评估: {review_summary['total_reviewed']}笔 均分{review_summary['avg_score']} 弱仓{review_summary['weak_count']}")

    # 当前持仓
    short_pos = [p for p in portfolio["open_positions"] if p.get("strategy") == "short_term"]
    long_pos = [p for p in portfolio["open_positions"] if p.get("strategy") != "short_term"]
    lines.append(f"\n💼 当前持仓: {len(portfolio['open_positions'])} 笔 (长线{len(long_pos)} + 短线{len(short_pos)})")
    for p in portfolio["open_positions"]:
        u_pnl = p.get("unrealized_pnl", 0)
        pnl_str = f"+${u_pnl:.2f}" if u_pnl >= 0 else f"-${abs(u_pnl):.2f}"
        cur_price = p.get("current_price", p["entry_price"])
        strat_tag = "⚡" if p.get("strategy") == "short_term" else "📊"
        lines.append(
            f"  {strat_tag} {p['market'][:40]} | {p['outcome']} @ {p['entry_price']:.2f}"
            f" → {cur_price:.2f} | ${p['size']} | {pnl_str}"
        )

    # 盈亏汇总
    realized_pnl = sum(p.get("pnl", 0) for p in portfolio["closed_positions"])
    total_assets = portfolio["balance"] + unrealized_pnl + sum(p["size"] for p in portfolio["open_positions"])

    lines.append(f"\n💰 资金状况:")
    lines.append(f"  可用余额: ${portfolio['balance']:.2f}")
    lines.append(f"  持仓成本: ${sum(p['size'] for p in portfolio['open_positions']):.2f}")
    lines.append(f"  未实现盈亏: ${unrealized_pnl:+.2f}")
    lines.append(f"  累计已实现盈亏: ${realized_pnl:+.2f}")
    lines.append(f"  总资产: ${total_assets:.2f}")
    lines.append(f"  收益率: {(total_assets - INITIAL_BALANCE) / INITIAL_BALANCE * 100:+.2f}%")

    # v9: 自动复盘分析
    lines.append(f"\n📊 策略复盘")
    today_gains = sum(p.get("pnl", 0) for p in (closed_today + take_profits) if p.get("pnl", 0) > 0)
    today_losses = sum(p.get("pnl", 0) for p in (closed_today + stop_losses) if p.get("pnl", 0) < 0)
    v5c_all = []
    if v5_stats:
        for v in v5_stats.get("v5_closed", {}).values():
            v5c_all.extend(v)
        v5c_all.extend(v5_stats.get("v8_short_closed", []))
    today_gains += sum(p.get("pnl", 0) for p in v5c_all if p.get("pnl", 0) > 0)
    today_losses += sum(p.get("pnl", 0) for p in v5c_all if p.get("pnl", 0) < 0)
    lines.append(f"• 今日盈亏: +${today_gains:.2f} / -${abs(today_losses):.2f}")
    short_pnl = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"] if p.get("strategy") == "short_term")
    long_pnl = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"] if p.get("strategy") != "short_term")
    lines.append(f"• 短线未实现: ${short_pnl:+.2f} | 长线未实现: ${long_pnl:+.2f}")
    issues = []
    if portfolio["balance"] / total_assets > SAFETY_CUSHION_PCT + 0.10:
        issues.append(f"闲置资金过高({portfolio['balance']/total_assets:.0%})")
    if len(stop_losses) > 2:
        issues.append(f"止损频繁({len(stop_losses)}笔)")
    mo_exits = len(v5_stats.get("v5_closed", {}).get("momentum", [])) if v5_stats else 0
    if mo_exits > 3:
        issues.append(f"动量退出多({mo_exits}笔)")
    lines.append(f"• 需要关注: {', '.join(issues) if issues else '无异常'}")
    suggestions = []
    if portfolio["balance"] / total_assets > AGGRESSIVE_CASH_THRESHOLD:
        suggestions.append("考虑更积极入场")
    if len(stop_losses) > len(take_profits):
        suggestions.append("检查入场信号质量")
    lines.append(f"• 建议: {', '.join(suggestions) if suggestions else '维持当前策略'}")

    # 记录日志
    portfolio["daily_log"].append({
        "date": today,
        "signals": len(signals),
        "new_positions": len(new_positions),
        "closed_settled": len(closed_today),
        "closed_stop_loss": len(stop_losses),
        "closed_take_profit": len(take_profits),
        "balance": round(portfolio["balance"], 2),
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": round(realized_pnl, 2),
        "total_assets": round(total_assets, 2),
        "open_count": len(portfolio["open_positions"]),
        "v5_stats": v5_stats,
    })

    return "\n".join(lines)


def generate_check_report(portfolio, new_positions, closed_today, stop_losses, take_profits, unrealized_pnl, v5_stats=None, priority_stats=None):
    """check 模式的简洁报告"""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    v5c = v5_stats.get("v5_closed", {}) if v5_stats else {}
    v5_changes = sum(len(v) for v in v5c.values()) if v5c else 0
    has_changes = closed_today or stop_losses or take_profits or new_positions or v5_changes > 0

    all_closed = closed_today + stop_losses + take_profits
    for v in v5c.values():
        all_closed.extend(v)
    realized_pnl_today = sum(p.get("pnl", 0) for p in all_closed)
    total_assets = portfolio["balance"] + unrealized_pnl + sum(p["size"] for p in portfolio["open_positions"])

    lines = []
    lines.append(f"📊 智能分级检查 v6 | {now_str}")

    # v6: 分级监控统计
    if priority_stats:
        total = priority_stats.get("high", 0) + priority_stats.get("medium", 0) + priority_stats.get("low", 0)
        lines.append(
            f"📋 本次检查 {priority_stats['checking']}/{total} 个持仓"
            f"（🔴高频{priority_stats['high']} 🟡中频{priority_stats['medium']} 🟢低频{priority_stats['low']} ⏭️跳过{priority_stats['skipped']}）"
        )

    if not has_changes:
        lines.append("✅ 无变动")
        lines.append(f"当前总资产: ${total_assets:,.2f}")
        return "\n".join(lines)

    lines.append(
        f"止损: {len(stop_losses)}笔 | 止盈: {len(take_profits)}笔"
        f" | 结算: {len(closed_today)}笔 | 新开: {len(new_positions)}笔"
    )

    # v5 额外信息
    if v5_stats:
        rt = v5_stats.get("realtime", {})
        mo = v5_stats.get("momentum", {})
        ef = v5_stats.get("event_filter", {})
        v5_lines = []
        if rt.get("strong_buy_signals", 0) or rt.get("exit_signals", 0):
            v5_lines.append(f"🔄 跟单: {rt.get('strong_buy_signals', 0)}强买/{rt.get('exit_signals', 0)}退出")
        if mo.get("momentum_exits", 0) or mo.get("rapid_gains", 0):
            v5_lines.append(f"📈 动量: {mo.get('momentum_exits', 0)}恶化/{mo.get('rapid_gains', 0)}上涨")
        if ef.get("skipped", 0) or ef.get("boosted", 0):
            v5_lines.append(f"📰 事件: {ef.get('skipped', 0)}过滤/{ef.get('boosted', 0)}加强")
        mc = v5c.get("momentum", [])
        pc = v5c.get("partial", [])
        ec = v5c.get("expiry", [])
        fc = v5c.get("follow", [])
        if mc or pc or ec or fc:
            v5_lines.append(f"v5平仓: 动量{len(mc)} 部分{len(pc)} 到期{len(ec)} 跟随{len(fc)}")
        if v5_lines:
            lines.extend(v5_lines)

    # v8: 短线统计
    if v5_stats:
        v8s = v5_stats.get("v8_short", {})
        v8_closed = v5_stats.get("v8_short_closed", [])
        v8_new = v5_stats.get("v8_short_new", [])
        if v8s.get("catalyst") or v8s.get("reversion") or v8s.get("expiry") or v8_closed or v8_new:
            lines.append(f"⚡ 短线: 信号{v8s.get('catalyst',0)+v8s.get('reversion',0)+v8s.get('expiry',0)} 开仓{len(v8_new)} 平仓{len(v8_closed)}")
            for p in v8_closed:
                pnl_str = f"+${p['pnl']:.2f}" if p.get('pnl', 0) >= 0 else f"-${abs(p.get('pnl', 0)):.2f}"
                lines.append(f"  • {p['market'][:35]} | {pnl_str} | {p.get('exit_reason', '')}")

    # v12: 换仓统计
    if v5_stats:
        swap_results = v5_stats.get("v12_swaps", [])
        review_summary = v5_stats.get("v12_review", {})
        if swap_results:
            has_changes = True  # 换仓也算变动
            lines.append(f"🔄 换仓: {len(swap_results)}笔")
            for sw in swap_results:
                c = sw["closed"]
                o = sw["opened"]
                pnl_str = f"+${c['pnl']:.2f}" if c['pnl'] >= 0 else f"-${abs(c['pnl']):.2f}"
                lines.append(f"  平仓: {c['market'][:30]} ({c['hold_score']}分) {pnl_str}")
                lines.append(f"  开仓: {o['market'][:30]} ({o['opportunity_score']}分) @{o['entry_price']:.2f} ${o['size']}")
        elif review_summary:
            avg = review_summary.get("avg_score", 0)
            weak = review_summary.get("weak_count", 0)
            total_r = review_summary.get("total_reviewed", 0)
            if total_r > 0:
                lines.append(f"🔍 持仓评估: {total_r}笔 均分{avg} 弱仓{weak}")

    lines.append(f"已实现盈亏: ${realized_pnl_today:+.2f}")
    lines.append(f"当前总资产: ${total_assets:,.2f}")

    # v9: check模式简短复盘
    if has_changes:
        cash_ratio = portfolio["balance"] / total_assets if total_assets > 0 else 0
        short_u = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"] if p.get("strategy") == "short_term")
        long_u = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"] if p.get("strategy") != "short_term")
        note_parts = [f"短线${short_u:+.1f}/长线${long_u:+.1f}"]
        if cash_ratio > 0.50:
            note_parts.append(f"闲置{cash_ratio:.0%}")
        if len(stop_losses) > 1:
            note_parts.append(f"止损多关注")
        lines.append(f"💡 {' | '.join(note_parts)}")

    return "\n".join(lines)


# ── 主流程 ──

def proactive_scan_markets(portfolio, elite_traders, market_prices):
    """v10: 主动扫描高交易量市场开仓（余额>50%总资产时触发）"""
    total_assets = portfolio["balance"] + sum(p["size"] for p in portfolio["open_positions"])
    cash_ratio = portfolio["balance"] / total_assets if total_assets > 0 else 0

    if cash_ratio < AGGRESSIVE_CASH_THRESHOLD:
        log(f"  [v10 proactive] 资金利用率OK({1-cash_ratio:.0%})，跳过主动扫描")
        return []

    log(f"  [v10 proactive] 闲置资金{cash_ratio:.0%}，启动主动扫描...")

    existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
    current_count = len(portfolio["open_positions"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    new_positions = []

    # 收集精英交易员持仓的 condition_ids
    elite_cids = set()
    BATCH = 5
    for batch_idx in range(0, min(len(elite_traders), 15), BATCH):
        batch = elite_traders[batch_idx:batch_idx + BATCH]
        for t in batch:
            wallet = t.get("wallet", "")
            if not wallet:
                continue
            activities = api_get(
                "https://data-api.polymarket.com/activity",
                {"user": wallet, "limit": 10},
                timeout=8,
            )
            time.sleep(DELAY)
            if activities:
                for a in activities:
                    if a.get("side") == "BUY":
                        cid = a.get("conditionId", "")
                        if cid:
                            elite_cids.add(cid)
                del activities
        del batch
        gc.collect()

    log(f"  [v10 proactive] 精英交易员持仓CID: {len(elite_cids)}个")

    # 获取活跃市场 top 50
    all_markets = []
    for offset in [0, 25]:
        data = api_get(
            "https://gamma-api.polymarket.com/markets",
            {"active": "true", "limit": 25, "offset": offset,
             "order": "volume24hr", "ascending": "false"},
            timeout=12,
        )
        time.sleep(DELAY)
        if data:
            all_markets.extend(data)

    for m in all_markets:
        if current_count >= MAX_OPEN_POSITIONS:
            break
        if portfolio["balance"] < PROACTIVE_PROBE_SIZE:
            break

        title = m.get("question", "") or m.get("title", "")
        cid = m.get("conditionId", "")
        if not cid or cid in existing_cids:
            continue

        # 过滤体育/电竞
        market_type = classify_market(title)
        if market_type in ("BLACKLIST", "SPORTS", "SPREAD", "SHORT_TERM", "CRYPTO_SHORT"):
            continue

        try:
            outcomes = m.get("outcomes", "[]")
            prices = m.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(prices, str):
                prices = json.loads(prices)
            price_map = {}
            for o, p in zip(outcomes, prices):
                try:
                    price_map[o] = float(p)
                except (ValueError, TypeError):
                    pass
        except Exception:
            continue

        yes_price = price_map.get("Yes", 0)
        if not (0.15 <= yes_price <= 0.85):
            continue

        slug = m.get("slug", "")
        has_elite = cid in elite_cids

        if has_elite:
            # 精英交易员持仓 → 正常仓位
            size = SHORT_TERM_SIZE_MIN
        else:
            # 无信号但高交易量 → 小仓位试探
            size = PROACTIVE_PROBE_SIZE

        size = min(size, portfolio["balance"])
        if size < PROACTIVE_PROBE_SIZE:
            break

        # 选方向：更接近0.5的
        no_price = price_map.get("No", 0)
        if abs(yes_price - 0.5) <= abs(no_price - 0.5):
            outcome = "Yes"
            entry_price = yes_price
        else:
            outcome = "No"
            entry_price = no_price

        portfolio["balance"] -= size
        portfolio["balance"] = round(portfolio["balance"], 2)

        position = {
            "market": title,
            "outcome": outcome,
            "entry_price": round(entry_price, 4),
            "size": size,
            "entry_date": today,
            "condition_id": cid,
            "slug": slug,
            "signal_strength": 0.5 if has_elite else 0.3,
            "num_traders": 1 if has_elite else 0,
            "trader_names": [],
            "market_type": "proactive_elite" if has_elite else "proactive_probe",
            "strategy": "short_term",
            "short_strategy": "proactive_elite" if has_elite else "proactive_probe",
            "target_profit": 0.12,
            "stop_loss_pct": -0.08,
            "max_hold_until": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
            "max_hold_hours": 72,
            "opened_at": now_iso,
        }
        portfolio["open_positions"].append(position)
        existing_cids.add(cid)
        current_count += 1
        new_positions.append(position)

    log(f"  [v10 proactive] 主动开仓: {len(new_positions)} 笔 (精英{sum(1 for p in new_positions if 'elite' in p.get('short_strategy',''))} + 试探{sum(1 for p in new_positions if 'probe' in p.get('short_strategy',''))})")
    del all_markets
    gc.collect()
    return new_positions


# ── 短线快速检查 ──

def run_short_check():
    """short_check 模式 v8：轻量级，只检查短线持仓止盈止损超时"""
    log("⚡ Polymarket 模拟盘 v8 - 短线快速检查 (short_check)")
    log(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log("")

    set_total_timeout(120)  # 短线检查2分钟超时

    # 1. 加载模拟盘
    log("[1/3] 加载模拟盘...")
    portfolio = load_portfolio()
    migrate_positions_v8(portfolio)

    short_positions = [p for p in portfolio["open_positions"] if p.get("strategy") == "short_term"]
    log(f"  总持仓: {len(portfolio['open_positions'])} 笔, 短线: {len(short_positions)} 笔")

    if not short_positions:
        log("  无短线持仓，退出")
        save_portfolio(portfolio)
        clear_timeout()
        report = f"⚡ 短线检查 | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n无短线持仓"
        log(report)
        return report

    try:
        # 2. 获取短线持仓价格
        log("\n[2/3] 获取短线持仓价格...")
        slug_to_cids = defaultdict(set)
        for p in short_positions:
            slug = p.get("slug", "")
            cid = p.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)

        market_prices = fetch_market_prices(slug_to_cids)

        # 3. 检查止盈/止损/超时
        log("\n[3/3] 检查短线持仓...")
        short_closed = check_short_term_exits(portfolio, market_prices)

        # 计算未实现盈亏
        short_remaining = [p for p in portfolio["open_positions"] if p.get("strategy") == "short_term"]
        unrealized = sum(p.get("unrealized_pnl", 0) for p in short_remaining)

        save_portfolio(portfolio)

        # 生成报告
        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [f"⚡ 短线检查 | {now_str}"]

        if short_closed:
            tp = [p for p in short_closed if "tp" in p.get("exit_reason", "")]
            sl = [p for p in short_closed if "sl" in p.get("exit_reason", "")]
            to = [p for p in short_closed if "timeout" in p.get("exit_reason", "")]
            st = [p for p in short_closed if "settled" in p.get("exit_reason", "")]
            realized = sum(p.get("pnl", 0) for p in short_closed)
            lines.append(f"平仓 {len(short_closed)} 笔: 止盈{len(tp)} 止损{len(sl)} 超时{len(to)} 结算{len(st)}")
            for p in short_closed:
                pnl_str = f"+${p['pnl']:.2f}" if p.get('pnl', 0) >= 0 else f"-${abs(p.get('pnl', 0)):.2f}"
                lines.append(f"  • {p['market'][:40]} | {pnl_str} | {p.get('exit_reason', '')}")
            lines.append(f"已实现: ${realized:+.2f}")
        else:
            lines.append("✅ 无触发")

        lines.append(f"短线持仓: {len(short_remaining)}笔 | 未实现: ${unrealized:+.2f}")
        lines.append(f"余额: ${portfolio['balance']:.2f}")

        report = "\n".join(lines)
        log("\n" + report)

        # v12: 有变动时直接发 Telegram
        if short_closed:
            notify_telegram(report)

        del market_prices
        gc.collect()
        clear_timeout()
        return report

    except TimeoutError as e:
        log(f"\n⚠️ {e}")
        save_portfolio(portfolio)
        clear_timeout()
        return f"⚠️ 短线检查超时，已保存状态"
    except Exception as e:
        log(f"\n❌ 短线检查异常: {e}")
        save_portfolio(portfolio)
        clear_timeout()
        raise


def run_full():
    """full 模式 v8：完整运行（分批处理 + 超时保护 + 短线策略）"""
    log("🚀 Polymarket 模拟盘 v8 - 完整运行 (full)")
    log(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"v5 模块: realtime={V5_REALTIME}, momentum={V5_MOMENTUM}, event_filter={V5_EVENT_FILTER}")
    log(f"v8 模块: short_term={V8_SHORT_TERM}")
    log(f"分批参数: 持仓{BATCH_POSITIONS}/批, slug{BATCH_SLUGS}/批, 交易员{BATCH_TRADERS}/批, 超时{TOTAL_TIMEOUT}s")
    log("")

    v5_stats = {"realtime": {}, "momentum": {}, "event_filter": {}, "v5_closed": {}}

    # 设置总超时保护
    set_total_timeout(TOTAL_TIMEOUT)

    # 1. 加载交易员
    log("[1/8] 加载交易员列表...")
    traders = load_traders()
    elite = select_elite_traders(traders)
    elite_wallets = {}
    for t in elite:
        wr = t.get("position_win_rate", 0.5)
        elite_wallets[t["wallet"]] = wr
    log(f"  总交易员: {len(traders)}, 精选: {len(elite)}")

    # 2. 加载模拟盘
    log("\n[2/8] 加载模拟盘...")
    portfolio = load_portfolio()
    migrate_positions_v8(portfolio)
    log(f"  余额: ${portfolio['balance']:.2f}, 持仓: {len(portfolio['open_positions'])} 笔")

    try:
        # 3. v5: 采集交易员最新交易（realtime_tracker）
        strong_buy_signals = []
        exit_signals = []
        if V5_REALTIME:
            log("\n[3/8] v5: 采集交易员实时交易...")
            try:
                strong_buys, exits, confidence, rt_stats = run_realtime_tracking(elite, portfolio["open_positions"])
                strong_buy_signals = strong_buys
                exit_signals = exits
                v5_stats["realtime"] = rt_stats
            except Exception as e:
                log(f"  [v5] realtime_tracker 失败: {e}")
        else:
            log("\n[3/8] v5: realtime_tracker 不可用，跳过")

        # 4. 获取最近24h活动 + 生成信号（分批采集）
        log("\n[4/8] 获取精选交易员最近24h交易活动...")
        activities = fetch_trader_activities(elite, hours=24)
        log("  生成跟单信号...")
        signals = generate_signals(activities, elite_wallets)
        log(f"  生成 {len(signals)} 个有效信号")

        # 释放交易员原始数据（保留elite给v8 catalyst）
        del traders
        gc.collect()

        # 合并强买入信号到 signals
        for sb in strong_buy_signals:
            sb_sig = {
                "condition_id": sb["condition_id"],
                "outcome": sb["outcome"],
                "title": sb["title"],
                "slug": sb["slug"],
                "market_type": "HIGH_VALUE",
                "num_traders": sb["num_traders"],
                "total_usdc": sb["total_usdc"],
                "avg_price": sb["avg_price"],
                "signal_strength": sb["num_traders"] / 5 * 2.0,
                "trader_wallets": [],
                "trader_names": sb.get("trader_names", []),
                "weight_multiplier": sb.get("weight_multiplier", 2.0),
            }
            existing_cids = {s["condition_id"] for s in signals}
            if sb_sig["condition_id"] not in existing_cids:
                signals.insert(0, sb_sig)

        # 5. v5: 事件过滤（分批）
        if V5_EVENT_FILTER and signals:
            log("\n[5/8] v5: 事件驱动过滤...")
            try:
                signals, ef_stats = batch_filter_signals(signals, max_filter=10)
                v5_stats["event_filter"] = ef_stats
            except Exception as e:
                log(f"  [v5] event_filter 失败: {e}")
        else:
            log("\n[5/8] v5: event_filter 不可用或无信号，跳过")

        # 收集所有需要查价的 slug
        slug_to_cids = defaultdict(set)
        for sig in signals:
            slug = sig.get("slug", "")
            cid = sig.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)
        for p in portfolio["open_positions"]:
            slug = p.get("slug", "")
            cid = p.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)

        # 6. 获取市场价格（分批查询）
        log("\n[6/8] 获取市场当前价格...")
        market_prices = fetch_market_prices(slug_to_cids)

        # 7. v5: 价格动量分析
        momentum_exits = []
        partial_takes = []
        expiry_takes = []
        if V5_MOMENTUM and portfolio["open_positions"]:
            log("\n[7/8] v5: 价格动量分析...")
            try:
                momentum_exits, partial_takes, expiry_takes, mo_stats = analyze_momentum(portfolio["open_positions"])
                v5_stats["momentum"] = mo_stats
            except Exception as e:
                log(f"  [v5] momentum 失败: {e}")
        else:
            log("\n[7/8] v5: momentum 不可用或无持仓，跳过")

        # 8. 分批更新持仓 & 开新仓
        log(f"\n[8/8] 分批更新持仓（{len(portfolio['open_positions'])}笔，每批{BATCH_POSITIONS}笔）...")
        closed_today, stop_losses, take_profits, unrealized_pnl = check_existing_positions(portfolio, market_prices)
        log(f"  结算: {len(closed_today)}, 止损: {len(stop_losses)}, 止盈: {len(take_profits)}, 未实现盈亏: ${unrealized_pnl:+.2f}")

        # v5: 执行动量退出
        v5_momentum_closed, v5_partial_closed, v5_expiry_closed = [], [], []
        if momentum_exits or partial_takes or expiry_takes:
            v5_momentum_closed, v5_partial_closed, v5_expiry_closed = execute_momentum_exits(
                portfolio, momentum_exits, partial_takes, expiry_takes, market_prices
            )
            log(f"  v5 动量: {len(v5_momentum_closed)}退出, {len(v5_partial_closed)}部分止盈, {len(v5_expiry_closed)}到期止盈")

        # v5: 执行跟随退出
        v5_follow_closed = []
        if exit_signals:
            v5_follow_closed = execute_follow_exits(portfolio, exit_signals, market_prices)
            log(f"  v5 跟随退出: {len(v5_follow_closed)}笔")

        v5_stats["v5_closed"] = {
            "momentum": v5_momentum_closed,
            "partial": v5_partial_closed,
            "expiry": v5_expiry_closed,
            "follow": v5_follow_closed,
        }

        # 重新计算未实现盈亏
        unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"])

        new_positions = open_new_positions(portfolio, signals, market_prices)
        log(f"  新开仓(长线): {len(new_positions)} 笔")

        # v8: 短线策略扫描 + 开仓
        v8_short_signals = []
        v8_short_new = []
        v8_short_closed = []
        v8_stats = {"catalyst": 0, "reversion": 0, "expiry": 0, "high_volume": 0, "arbitrage": 0, "errors": []}

        if V8_SHORT_TERM:
            log("\n[8b/8] v11: 短线策略全量扫描...")
            elite_list_backup = list(elite)  # v10: 备份给proactive scan
            try:
                existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
                v8_short_signals, v8_stats = scan_short_term_signals(
                    elite_traders=elite,
                    existing_cids=existing_cids,
                )
                # 释放elite
                del elite
                # 检查短线持仓止盈止损
                v8_short_closed = check_short_term_exits(portfolio, market_prices)
                if v8_short_closed:
                    log(f"  v8 短线平仓: {len(v8_short_closed)} 笔")
                # 开短线新仓
                if v8_short_signals:
                    # 补充短线信号的价格到 market_prices
                    extra_slugs = defaultdict(set)
                    for s in v8_short_signals:
                        slug = s.get("slug", "")
                        cid = s.get("condition_id", "")
                        if slug and cid and cid not in market_prices:
                            extra_slugs[slug].add(cid)
                    if extra_slugs:
                        extra_prices = fetch_market_prices(extra_slugs)
                        market_prices.update(extra_prices)
                    v8_short_new = open_short_term_positions(portfolio, v8_short_signals, market_prices)
            except Exception as e:
                log(f"  [v8] 短线策略失败: {e}")
                v8_stats["errors"].append(str(e))
        else:
            log("\n[8b/8] v8: short_term 不可用，跳过")
            elite_list_backup = list(elite)  # v10: 备份给proactive scan

        # 合并v8统计到v5_stats
        v5_stats["v8_short"] = v8_stats
        v5_stats["v8_short_closed"] = v8_short_closed
        v5_stats["v8_short_new"] = v8_short_new

        # v10: 主动扫描开仓（余额>50%时触发）
        v10_proactive_new = []
        try:
            # elite 可能在v8 block中被del，用elite_list备份
            v10_proactive_new = proactive_scan_markets(portfolio, elite_list_backup, market_prices)
            v5_stats["v10_proactive_new"] = v10_proactive_new
        except Exception as e:
            log(f"  [v10] 主动扫描失败: {e}")
            v5_stats["v10_proactive_new"] = []

        # v12: 智能换仓评估
        swap_results = []
        review_summary = {}
        if V12_SWAP and portfolio["open_positions"]:
            log("\n[v12] 智能换仓评估...")
            try:
                # 合并所有可用信号作为换仓候选
                all_swap_signals = list(signals) + list(v8_short_signals)
                swap_results, review_summary = execute_position_swaps(portfolio, all_swap_signals, market_prices)
                v5_stats["v12_swaps"] = swap_results
                v5_stats["v12_review"] = review_summary
            except Exception as e:
                log(f"  [v12] 换仓评估失败: {e}")
                v5_stats["v12_swaps"] = []
                v5_stats["v12_review"] = {}

        report = generate_report(portfolio, signals, new_positions, closed_today, stop_losses, take_profits, unrealized_pnl, v5_stats)
        save_portfolio(portfolio)
        log(f"  已保存到 {SIM_PORTFOLIO_PATH}")
        log("\n" + report)

        # 释放大对象
        del activities, signals, market_prices, elite_wallets
        gc.collect()

        clear_timeout()
        return report

    except TimeoutError as e:
        log(f"\n⚠️ {e}")
        save_portfolio(portfolio)
        log(f"  已保存当前状态到 {SIM_PORTFOLIO_PATH}")
        clear_timeout()
        return f"⚠️ 超时退出，已保存当前状态。持仓: {len(portfolio['open_positions'])} 笔"
    except Exception as e:
        log(f"\n❌ 运行异常: {e}")
        save_portfolio(portfolio)
        log(f"  已保存当前状态到 {SIM_PORTFOLIO_PATH}")
        clear_timeout()
        raise

def run_check():
    """check 模式 v6：分级智能检查持仓 + 扫描新信号开仓（分批处理 + 超时保护）"""
    log("🔍 Polymarket 模拟盘 v8 - 智能分级检查 (check)")
    log(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"v5 模块: realtime={V5_REALTIME}, momentum={V5_MOMENTUM}, event_filter={V5_EVENT_FILTER}")
    log(f"v6 模块: priority={V6_PRIORITY}")
    log(f"v8 模块: short_term={V8_SHORT_TERM}")
    log(f"分批参数: 持仓{BATCH_POSITIONS}/批, slug{BATCH_SLUGS}/批, 交易员{BATCH_TRADERS}/批, 超时{TOTAL_TIMEOUT}s")
    log("")

    v5_stats = {"realtime": {}, "momentum": {}, "event_filter": {}, "v5_closed": {}}
    priority_stats = None

    # 设置总超时保护
    set_total_timeout(TOTAL_TIMEOUT)

    # 1. 加载模拟盘
    log("[1/7] 加载模拟盘...")
    portfolio = load_portfolio()
    migrate_positions_v8(portfolio)
    total_positions = len(portfolio["open_positions"])
    log(f"  余额: ${portfolio['balance']:.2f}, 持仓: {total_positions} 笔")

    try:
        # 2. v6: 分级监控 - 决定哪些持仓需要检查
        positions_to_check = portfolio["open_positions"]
        skipped_positions = []
        classifications = {}

        if V6_PRIORITY and portfolio["open_positions"]:
            log("\n[2/7] v6: 分级监控分类...")
            try:
                classifications, positions_to_check, skipped_positions, priority_stats = classify_all_positions(
                    portfolio["open_positions"]
                )
                log(f"  🔴 高频(HIGH): {priority_stats['high']}个")
                log(f"  🟡 中频(MEDIUM): {priority_stats['medium']}个")
                log(f"  🟢 低频(LOW): {priority_stats['low']}个")
                log(f"  ✅ 本次检查: {priority_stats['checking']}个 | ⏭️ 跳过: {priority_stats['skipped']}个")
            except Exception as e:
                log(f"  [v6] priority_monitor 失败，回退全量检查: {e}")
                positions_to_check = portfolio["open_positions"]
                skipped_positions = []
        else:
            log("\n[2/7] v6: priority_monitor 不可用，检查全部持仓")

        # 3. v5: 采集交易员最新交易，识别退出信号
        log("\n[3/7] 加载交易员 & v5 实时跟踪...")
        traders = load_traders()
        elite = select_elite_traders(traders)
        elite_wallets = {}
        for t in elite:
            wr = t.get("position_win_rate", 0.5)
            elite_wallets[t["wallet"]] = wr

        strong_buy_signals = []
        exit_signals = []
        if V5_REALTIME:
            try:
                strong_buys, exits, confidence, rt_stats = run_realtime_tracking(elite, portfolio["open_positions"])
                strong_buy_signals = strong_buys
                exit_signals = exits
                v5_stats["realtime"] = rt_stats
            except Exception as e:
                log(f"  [v5] realtime_tracker 失败: {e}")

        # 获取最近活动（用于扫描新信号）- 分批采集
        activities = fetch_trader_activities(elite, hours=24)
        signals = generate_signals(activities, elite_wallets)
        log(f"  扫描到 {len(signals)} 个信号")

        # 释放交易员原始数据（保留elite给v8 catalyst）
        del traders
        gc.collect()

        # 收集需要查价的 slug（只查需要检查的持仓 + 信号）
        slug_to_cids = defaultdict(set)
        for sig in signals:
            slug = sig.get("slug", "")
            cid = sig.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)
        for p in positions_to_check:
            slug = p.get("slug", "")
            cid = p.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)
        for sb in strong_buy_signals:
            slug = sb.get("slug", "")
            cid = sb.get("condition_id", "")
            if slug and cid:
                slug_to_cids[slug].add(cid)

        # 4. 获取市场价格 - 分批查询
        log(f"\n[4/7] 获取市场当前价格（{len(slug_to_cids)}个市场）...")
        market_prices = fetch_market_prices(slug_to_cids)

        # 5. v5: 检查持仓价格动量（只对需要检查的持仓）
        momentum_exits = []
        partial_takes = []
        expiry_takes = []
        if V5_MOMENTUM and positions_to_check:
            log(f"\n[5/7] v5: 价格动量分析（{len(positions_to_check)}个持仓）...")
            try:
                momentum_exits, partial_takes, expiry_takes, mo_stats = analyze_momentum(positions_to_check)
                v5_stats["momentum"] = mo_stats
            except Exception as e:
                log(f"  [v5] momentum 失败: {e}")
        else:
            log("\n[5/7] v5: momentum 跳过")

        # 6. 分批检查持仓 + 执行退出
        log(f"\n[6/7] 分批检查持仓（{len(portfolio['open_positions'])}笔，每批{BATCH_POSITIONS}笔）...")
        closed_today, stop_losses, take_profits, unrealized_pnl = check_existing_positions(portfolio, market_prices)
        log(f"  结算: {len(closed_today)}, 止损: {len(stop_losses)}, 止盈: {len(take_profits)}")

        # v5: 执行动量退出
        v5_momentum_closed, v5_partial_closed, v5_expiry_closed = [], [], []
        if momentum_exits or partial_takes or expiry_takes:
            v5_momentum_closed, v5_partial_closed, v5_expiry_closed = execute_momentum_exits(
                portfolio, momentum_exits, partial_takes, expiry_takes, market_prices
            )
            log(f"  v5 动量: {len(v5_momentum_closed)}退出, {len(v5_partial_closed)}部分止盈, {len(v5_expiry_closed)}到期止盈")

        # v5: 执行跟随退出
        v5_follow_closed = []
        if exit_signals:
            v5_follow_closed = execute_follow_exits(portfolio, exit_signals, market_prices)
            log(f"  v5 跟随退出: {len(v5_follow_closed)}笔")

        v5_stats["v5_closed"] = {
            "momentum": v5_momentum_closed,
            "partial": v5_partial_closed,
            "expiry": v5_expiry_closed,
            "follow": v5_follow_closed,
        }

        # 7. 扫描开仓（v12: 先检查每日亏损熔断）
        log("\n[7/7] 扫描开仓...")
        new_positions = []

        # v12: 每日亏损熔断检查
        circuit_breaker_triggered = check_daily_loss_circuit_breaker(portfolio)
        if circuit_breaker_triggered:
            log("  [v12] 熔断已触发，跳过所有新开仓")
            save_portfolio(portfolio)
            # 仍然生成报告
            all_closed = closed_today + stop_losses + take_profits
            report = generate_check_report(portfolio, [], all_closed, stop_losses, take_profits, unrealized_pnl, v5_stats, priority_stats)
            log(report)
            return

        open_signals = []
        for sb in strong_buy_signals:
            sb_sig = {
                "condition_id": sb["condition_id"],
                "outcome": sb["outcome"],
                "title": sb["title"],
                "slug": sb["slug"],
                "market_type": "HIGH_VALUE",
                "num_traders": sb["num_traders"],
                "total_usdc": sb["total_usdc"],
                "avg_price": sb["avg_price"],
                "signal_strength": sb["num_traders"] / 5 * 2.0,
                "trader_wallets": [],
                "trader_names": sb.get("trader_names", []),
                "weight_multiplier": sb.get("weight_multiplier", 2.0),
            }
            open_signals.append(sb_sig)

        open_signals.extend(signals)

        if open_signals:
            new_positions = open_new_positions(portfolio, open_signals, market_prices)
        log(f"  新开仓(长线): {len(new_positions)} 笔")

        # v8: 短线策略
        v8_short_signals = []
        v8_short_new = []
        v8_short_closed = []
        v8_stats = {"catalyst": 0, "reversion": 0, "expiry": 0, "high_volume": 0, "arbitrage": 0, "errors": []}

        if V8_SHORT_TERM:
            log("  [v11] 短线策略全量扫描（check模式也扫描短线机会）...")
            elite_list_backup = list(elite)
            try:
                existing_cids = {p["condition_id"] for p in portfolio["open_positions"]}
                v8_short_signals, v8_stats = scan_short_term_signals(
                    elite_traders=elite,
                    existing_cids=existing_cids,
                )
                del elite
                v8_short_closed = check_short_term_exits(portfolio, market_prices)
                if v8_short_closed:
                    log(f"  v8 短线平仓: {len(v8_short_closed)} 笔")
                if v8_short_signals:
                    extra_slugs = defaultdict(set)
                    for s in v8_short_signals:
                        slug = s.get("slug", "")
                        cid = s.get("condition_id", "")
                        if slug and cid and cid not in market_prices:
                            extra_slugs[slug].add(cid)
                    if extra_slugs:
                        extra_prices = fetch_market_prices(extra_slugs)
                        market_prices.update(extra_prices)
                    v8_short_new = open_short_term_positions(portfolio, v8_short_signals, market_prices)
            except Exception as e:
                log(f"  [v8] 短线策略失败: {e}")
        else:
            elite_list_backup = list(elite)

        v5_stats["v8_short"] = v8_stats
        v5_stats["v8_short_closed"] = v8_short_closed
        v5_stats["v8_short_new"] = v8_short_new

        # v11: check模式也运行主动扫描
        v10_proactive_new = []
        try:
            el_backup = elite_list_backup if 'elite_list_backup' in dir() else []
            v10_proactive_new = proactive_scan_markets(portfolio, el_backup, market_prices)
            v5_stats["v10_proactive_new"] = v10_proactive_new
        except Exception as e:
            log(f"  [v11] 主动扫描失败: {e}")
            v5_stats["v10_proactive_new"] = []

        # v12: 智能换仓评估
        if V12_SWAP and portfolio["open_positions"]:
            log("  [v12] 智能换仓评估...")
            try:
                all_swap_signals = list(signals) + list(v8_short_signals)
                swap_results, review_summary = execute_position_swaps(portfolio, all_swap_signals, market_prices)
                v5_stats["v12_swaps"] = swap_results
                v5_stats["v12_review"] = review_summary
            except Exception as e:
                log(f"  [v12] 换仓评估失败: {e}")
                v5_stats["v12_swaps"] = []
                v5_stats["v12_review"] = {}

        # v6: 更新监控状态
        if V6_PRIORITY and classifications:
            checked_cids = [p.get("condition_id", "") for p in positions_to_check if p.get("condition_id")]
            update_monitor_state(checked_cids, classifications)
            log("  v6: 已更新 monitor_state.json")

        # 重新计算未实现盈亏
        unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in portfolio["open_positions"])

        report = generate_check_report(portfolio, new_positions, closed_today, stop_losses, take_profits, unrealized_pnl, v5_stats, priority_stats)
        save_portfolio(portfolio)
        log(f"  已保存到 {SIM_PORTFOLIO_PATH}")
        log("\n" + report)

        # v12: 有变动时直接发 Telegram
        has_changes = new_positions or closed_today or stop_losses or take_profits or v8_short_new or v8_short_closed
        if has_changes:
            notify_telegram(report)

        # 释放大对象
        del activities, signals, market_prices, elite_wallets
        gc.collect()

        clear_timeout()
        return report

    except TimeoutError as e:
        log(f"\n⚠️ {e}")
        save_portfolio(portfolio)
        log(f"  已保存当前状态到 {SIM_PORTFOLIO_PATH}")
        clear_timeout()
        return f"⚠️ 超时退出，已保存当前状态。持仓: {len(portfolio['open_positions'])} 笔"
    except Exception as e:
        log(f"\n❌ 运行异常: {e}")
        save_portfolio(portfolio)
        log(f"  已保存当前状态到 {SIM_PORTFOLIO_PATH}")
        clear_timeout()
        raise


# ── 向后兼容入口 ──

def run_daily_sim():
    """向后兼容：默认 full 模式"""
    return run_full()


def run_watch(interval_seconds=300, max_runtime=7200):
    """
    v12: 持续监控模式 — 每 interval_seconds 秒运行一次 short_check。
    max_runtime 秒后自动退出（默认2小时），配合 cron 每2小时重启。
    用法: python3 live_sim.py watch [--interval 300] [--runtime 7200]
    """
    log(f"👁️ Polymarket 持续监控模式 v12")
    log(f"  短线检查间隔: {interval_seconds}秒 ({interval_seconds//60}分钟)")
    log(f"  最大运行时间: {max_runtime}秒 ({max_runtime//3600}小时)")
    log(f"  Telegram 通知: {'已配置' if TELEGRAM_BOT_TOKEN else '未配置'}")
    log("")

    start_time = time.time()
    cycle = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_runtime:
            log(f"\n⏰ 已运行 {elapsed/3600:.1f} 小时，达到最大运行时间，退出")
            break

        cycle += 1
        log(f"\n{'='*40}")
        log(f"📍 第 {cycle} 轮 | 已运行 {elapsed/60:.0f} 分钟")

        try:
            run_short_check()
        except Exception as e:
            log(f"❌ short_check 异常: {e}")
            error_msg = f"⚠️ Polymarket 监控异常\n{e}"
            notify_telegram(error_msg)

        # 等待到下一轮
        remaining = max_runtime - (time.time() - start_time)
        if remaining <= 0:
            break
        wait = min(interval_seconds, remaining)
        log(f"  💤 等待 {wait:.0f} 秒...")
        time.sleep(wait)

    log("👋 监控结束")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    mode = mode.lower().strip()

    if mode == "check":
        run_check()
    elif mode == "full":
        run_full()
    elif mode == "short_check":
        run_short_check()
    elif mode == "watch":
        # 解析可选参数
        interval = 300  # 默认5分钟
        runtime = 7200  # 默认2小时
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--interval" and i + 1 < len(sys.argv):
                interval = int(sys.argv[i + 1])
            elif arg == "--runtime" and i + 1 < len(sys.argv):
                runtime = int(sys.argv[i + 1])
        run_watch(interval_seconds=interval, max_runtime=runtime)
    else:
        log(f"❌ 未知模式: {mode}")
        log("用法: python3 live_sim.py [full|check|short_check|watch]")
        log("  watch 选项: --interval 300 --runtime 7200")
        sys.exit(1)
