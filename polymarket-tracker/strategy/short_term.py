"""
短线高频交易策略模块
策略1: 事件催化剂交易 - 跟随精英交易员共识
策略2: 价格回归交易 - 过度波动后回归
策略3: 到期前收割 - 高确定性到期收益
"""
import gc
import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta

DELAY = 0.3
API_TIMEOUT = 10

# 体育/电竞排除关键词（小写）
EXCLUDED_KEYWORDS = [
    "tennis", "championships", "dubai", "atp", "wta", "grand slam",
    "wimbledon", "roland garros", "australian open", "us open",
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "champions league", "europa league", "world cup", "olympics", "medal",
    "rugby", "cricket", "f1", "formula", "nascar", "golf", "pga", "tour",
    "boxing", "ufc", "mma", "wwe", "nhl", "mlb", "nfl", "nba",
    "super bowl", "stanley cup", "world series", "march madness",
    "dota", "csgo", "valorant", "league of legends", "overwatch",
    "sports", "esports", "football", "soccer", "basketball",
    "lec", "blast", "epl", "six nations", "ice hockey",
    "biathlon", "eurovision", "win on 2026-", "win on 2025-",
    "vs.", "vs ", "o/u", "up or down", "spread:",
    "ncaa", "copa del", "grand prix", "t20",
]


def log(msg):
    print(msg, flush=True)


def api_get(url, params, timeout=API_TIMEOUT):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  [short_term] API error: {url} - {e}")
        return None


def is_excluded(title):
    """检查市场是否应被排除"""
    t = title.lower()
    return any(kw in t for kw in EXCLUDED_KEYWORDS)


def parse_market_prices(market_data):
    """解析市场数据中的价格"""
    try:
        outcomes = market_data.get("outcomes", "[]")
        prices = market_data.get("outcomePrices", "[]")
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
        return price_map
    except Exception:
        return {}


# ═══════════════════════════════════════
# 策略1: 事件催化剂交易
# ═══════════════════════════════════════

def strategy_catalyst(elite_traders, existing_cids=None):
    """
    从精英交易员最近6小时的交易中找共识买入。
    ≥2个精英交易员买入同一市场 → 跟买信号。
    """
    log("  [catalyst] 扫描精英交易员共识...")
    if existing_cids is None:
        existing_cids = set()

    signals = []
    cutoff = int(time.time()) - 6 * 3600  # 6小时内
    market_buyers = {}  # cid -> {outcome, traders, title, slug, prices}

    BATCH = 5
    total = len(elite_traders)
    fetched = 0

    for batch_idx in range(0, total, BATCH):
        batch = elite_traders[batch_idx:batch_idx + BATCH]
        for t in batch:
            wallet = t.get("wallet", "")
            if not wallet:
                continue
            activities = api_get(
                "https://data-api.polymarket.com/activity",
                {"user": wallet, "limit": 20},
                timeout=8,
            )
            time.sleep(DELAY)
            if not activities:
                continue
            fetched += 1
            for a in activities:
                ts = a.get("timestamp", 0)
                if ts < cutoff:
                    continue
                if a.get("side") != "BUY" or a.get("type") != "TRADE":
                    continue
                title = a.get("title", "")
                if is_excluded(title):
                    continue
                cid = a.get("conditionId", "")
                if not cid or cid in existing_cids:
                    continue
                outcome = a.get("outcome", "")
                price = a.get("price", 0) or 0
                if price <= 0.05 or price >= 0.95:
                    continue
                key = (cid, outcome)
                if key not in market_buyers:
                    market_buyers[key] = {
                        "traders": set(),
                        "title": title,
                        "slug": a.get("slug", "") or a.get("eventSlug", ""),
                        "prices": [],
                        "outcome": outcome,
                        "cid": cid,
                        "trader_names": [],
                    }
                trader_name = t.get("name", "") or t.get("pseudonym", "") or wallet[:10]
                market_buyers[key]["traders"].add(wallet)
                market_buyers[key]["prices"].append(price)
                market_buyers[key]["trader_names"].append(trader_name)
            del activities
        del batch
        gc.collect()
        if batch_idx + BATCH < total:
            time.sleep(0.5)

    # v10: 1 人精英大额买入就跟（之前2人）
    for key, data in market_buyers.items():
        num_traders = len(data["traders"])
        if num_traders >= 1:
            avg_price = sum(data["prices"]) / len(data["prices"])
            confidence = min(num_traders / 5.0, 1.0)
            signals.append({
                "market": data["title"],
                "slug": data["slug"],
                "condition_id": data["cid"],
                "outcome": data["outcome"],
                "current_price": round(avg_price, 4),
                "strategy": "catalyst",
                "target_profit": 0.15,
                "stop_loss": -0.10,
                "max_hold_hours": 48,
                "confidence": round(confidence, 2),
                "num_traders": num_traders,
                "trader_names": list(set(data["trader_names"]))[:5],
            })

    signals.sort(key=lambda x: x["confidence"], reverse=True)
    log(f"  [catalyst] 找到 {len(signals)} 个催化剂信号（{fetched}个交易员有数据）")
    del market_buyers
    gc.collect()

    # v11: 24h交易量突然放大>200%的市场也算催化剂信号
    log("  [catalyst] 扫描交易量暴增市场...")
    try:
        vol_markets = []
        for offset in [0, 25]:
            data = api_get(
                "https://gamma-api.polymarket.com/markets",
                {"active": "true", "limit": 25, "offset": offset,
                 "order": "volume24hr", "ascending": "false"},
                timeout=12,
            )
            time.sleep(DELAY)
            if data:
                vol_markets.extend(data)

        vol_spike_count = 0
        for m in vol_markets:
            title = m.get("question", "") or m.get("title", "")
            if is_excluded(title):
                continue
            cid = m.get("conditionId", "")
            if not cid or cid in existing_cids:
                continue
            # Check if already in signals
            if any(s["condition_id"] == cid for s in signals):
                continue

            vol24 = 0
            vol_total = 0
            for field in ["volume24hr", "volume_24h"]:
                try:
                    vol24 = float(m.get(field, 0) or 0)
                    if vol24 > 0:
                        break
                except (ValueError, TypeError):
                    pass
            try:
                vol_total = float(m.get("volume", 0) or 0)
            except (ValueError, TypeError):
                vol_total = 0

            # Estimate daily average: total / max(days_active, 7)
            if vol_total > 0 and vol24 > 0:
                created = m.get("createdAt", "") or m.get("startDate", "")
                days_active = 30  # default
                if created:
                    try:
                        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
                            try:
                                created_dt = datetime.strptime(created, fmt).replace(tzinfo=timezone.utc)
                                days_active = max((datetime.now(timezone.utc) - created_dt).days, 1)
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass
                avg_daily = vol_total / max(days_active, 7)
                if avg_daily > 0 and vol24 / avg_daily > 2.0:
                    # Volume spike detected!
                    price_map = parse_market_prices(m)
                    if not price_map:
                        continue
                    yes_price = price_map.get("Yes", 0)
                    no_price = price_map.get("No", 0)
                    if not (0.10 < yes_price < 0.90):
                        continue
                    slug = m.get("slug", "")
                    spike_ratio = vol24 / avg_daily
                    confidence = min(spike_ratio / 5.0, 0.9)
                    if abs(yes_price - 0.5) <= abs(no_price - 0.5):
                        outcome = "Yes"
                        price = yes_price
                    else:
                        outcome = "No"
                        price = no_price
                    signals.append({
                        "market": title,
                        "slug": slug,
                        "condition_id": cid,
                        "outcome": outcome,
                        "current_price": round(price, 4),
                        "strategy": "catalyst_volume_spike",
                        "target_profit": 0.12,
                        "stop_loss": -0.08,
                        "max_hold_hours": 48,
                        "confidence": round(confidence, 2),
                        "num_traders": 0,
                        "trader_names": [],
                        "volume_spike": round(spike_ratio, 1),
                    })
                    existing_cids.add(cid)
                    vol_spike_count += 1

        log(f"  [catalyst] 交易量暴增信号: {vol_spike_count} 个")
        del vol_markets
    except Exception as e:
        log(f"  [catalyst] 交易量暴增扫描失败: {e}")

    gc.collect()
    return signals


# ═══════════════════════════════════════
# 策略2: 价格回归交易
# ═══════════════════════════════════════

def strategy_reversion(existing_cids=None):
    """
    扫描活跃市场，找24h价格变动>5%的过度波动市场。
    v11: 波动门槛 8%→5%，扫描范围 top 25→top 50。
    """
    log("  [reversion] 扫描过度波动市场...")
    if existing_cids is None:
        existing_cids = set()

    signals = []

    # v11: 扫描 top 50 活跃市场
    all_markets = []
    for offset in [0, 25, 50]:
        data = api_get(
            "https://gamma-api.polymarket.com/markets",
            {"active": "true", "limit": 25, "offset": offset,
             "order": "volume24hr", "ascending": "false"},
            timeout=12,
        )
        time.sleep(DELAY)
        if data:
            all_markets.extend(data)

    log(f"  [reversion] 获取到 {len(all_markets)} 个活跃市场")

    for m in all_markets:
        title = m.get("question", "") or m.get("title", "")
        if is_excluded(title):
            continue
        cid = m.get("conditionId", "")
        if not cid or cid in existing_cids:
            continue

        price_map = parse_market_prices(m)
        if not price_map:
            continue

        slug = m.get("slug", "")

        # 尝试多种方式获取24h价格变动
        price_change = None
        for field in ["oneDayPriceChange", "one_day_price_change", "priceDelta24h"]:
            val = m.get(field)
            if val is not None:
                try:
                    price_change = float(val)
                    break
                except (ValueError, TypeError):
                    pass

        if price_change is None:
            yes_price = price_map.get("Yes", 0)
            spread = m.get("spread", 0)
            try:
                spread = float(spread) if spread else 0
            except (ValueError, TypeError):
                spread = 0
            if spread > 0.10 and 0.10 < yes_price < 0.85:
                price_change = -spread
            else:
                continue

        # v11: 波动门槛 8%→5%
        if price_change < -0.05:
            yes_price = price_map.get("Yes", 0)
            if 0.10 < yes_price < 0.85:
                confidence = min(abs(price_change) / 0.25, 1.0)
                signals.append({
                    "market": title,
                    "slug": slug,
                    "condition_id": cid,
                    "outcome": "Yes",
                    "current_price": round(yes_price, 4),
                    "strategy": "reversion",
                    "target_profit": 0.15,
                    "stop_loss": -0.10,
                    "max_hold_hours": 48,
                    "confidence": round(confidence, 2),
                    "price_change_24h": round(price_change, 4),
                })
        elif price_change > 0.05:
            no_price = price_map.get("No", 0)
            if 0.10 < no_price < 0.85:
                confidence = min(abs(price_change) / 0.25, 1.0)
                signals.append({
                    "market": title,
                    "slug": slug,
                    "condition_id": cid,
                    "outcome": "No",
                    "current_price": round(no_price, 4),
                    "strategy": "reversion",
                    "target_profit": 0.15,
                    "stop_loss": -0.10,
                    "max_hold_hours": 48,
                    "confidence": round(confidence, 2),
                    "price_change_24h": round(price_change, 4),
                })

    signals.sort(key=lambda x: x["confidence"], reverse=True)
    log(f"  [reversion] 找到 {len(signals)} 个回归信号")
    del all_markets
    gc.collect()
    return signals


# ═══════════════════════════════════════
# 策略3: 到期前收割
# ═══════════════════════════════════════

def strategy_expiry(existing_cids=None):
    """
    v11: 扫描14天内到期的市场，找高确定性（Yes>0.70或No>0.70）的。
    到期窗口 7天→14天，入场门槛 0.75→0.70。
    """
    log("  [expiry] 扫描即将到期的高确定性市场...")
    if existing_cids is None:
        existing_cids = set()

    signals = []
    now = datetime.now(timezone.utc)
    expiry_deadline = now + timedelta(days=14)  # v11: 7天→14天

    # v11: 获取活跃市场 top 75
    all_markets = []
    for offset in [0, 25, 50]:
        data = api_get(
            "https://gamma-api.polymarket.com/markets",
            {"active": "true", "limit": 25, "offset": offset,
             "order": "volume24hr", "ascending": "false"},
            timeout=12,
        )
        time.sleep(DELAY)
        if data:
            all_markets.extend(data)

    if not all_markets:
        log("  [expiry] 无法获取市场数据")
        return signals

    log(f"  [expiry] 获取到 {len(all_markets)} 个活跃市场，筛选14天内到期...")

    for m in all_markets:
        title = m.get("question", "") or m.get("title", "")
        if is_excluded(title):
            continue
        cid = m.get("conditionId", "")
        if not cid or cid in existing_cids:
            continue

        # 检查到期时间
        end_date_str = m.get("endDate", "")
        if not end_date_str:
            continue
        try:
            for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
                try:
                    end_date = datetime.strptime(end_date_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                continue
            # v11: 必须在未来且14天内到期
            if end_date <= now or end_date > expiry_deadline:
                continue
        except Exception:
            continue

        price_map = parse_market_prices(m)
        if not price_map:
            continue

        slug = m.get("slug", "")
        yes_price = price_map.get("Yes", 0)
        no_price = price_map.get("No", 0)

        # v11: 高确定性门槛 0.75→0.70
        if yes_price > 0.70:
            potential = 1.0 - yes_price  # 潜在收益
            if potential < 0.02:
                continue  # 太接近1了，收益太低
            confidence = min(yes_price, 0.99)
            signals.append({
                "market": title,
                "slug": slug,
                "condition_id": cid,
                "outcome": "Yes",
                "current_price": round(yes_price, 4),
                "strategy": "expiry",
                "target_profit": 0.10,
                "stop_loss": -0.05,
                "max_hold_hours": 168,
                "confidence": round(confidence, 2),
                "potential_gain": round(potential, 4),
            })
        # v11: 高确定性: No > 0.70 → 买No
        elif no_price > 0.70:
            potential = 1.0 - no_price
            if potential < 0.02:
                continue
            confidence = min(no_price, 0.99)
            signals.append({
                "market": title,
                "slug": slug,
                "condition_id": cid,
                "outcome": "No",
                "current_price": round(no_price, 4),
                "strategy": "expiry",
                "target_profit": 0.10,
                "stop_loss": -0.05,
                "max_hold_hours": 168,
                "confidence": round(confidence, 2),
                "potential_gain": round(potential, 4),
            })

    signals.sort(key=lambda x: x["confidence"], reverse=True)
    log(f"  [expiry] 找到 {len(signals)} 个到期收割信号")
    del data
    gc.collect()
    return signals


# ═══════════════════════════════════════
# 策略4: 高交易量策略 (v10 新增)
# ═══════════════════════════════════════

def strategy_high_volume(existing_cids=None):
    """
    v11: 扫描24h交易量top50的活跃市场，
    找到价格在0.15-0.85区间的（有波动空间），自动开仓。
    不限数量，每次check都扫描。小仓位$30。
    """
    log("  [high_volume] 扫描高交易量活跃市场...")
    if existing_cids is None:
        existing_cids = set()

    signals = []

    # v11: 获取 top 50 活跃市场
    all_markets = []
    for offset in [0, 25, 50]:
        data = api_get(
            "https://gamma-api.polymarket.com/markets",
            {"active": "true", "limit": 25, "offset": offset,
             "order": "volume24hr", "ascending": "false"},
            timeout=12,
        )
        time.sleep(DELAY)
        if data:
            all_markets.extend(data)

    log(f"  [high_volume] 获取到 {len(all_markets)} 个活跃市场")

    for m in all_markets:
        title = m.get("question", "") or m.get("title", "")
        if is_excluded(title):
            continue
        cid = m.get("conditionId", "")
        if not cid or cid in existing_cids:
            continue

        price_map = parse_market_prices(m)
        if not price_map:
            continue

        yes_price = price_map.get("Yes", 0)
        no_price = price_map.get("No", 0)
        slug = m.get("slug", "")

        # v11: 价格在 0.15-0.85 区间（放宽）
        if 0.15 <= yes_price <= 0.85:
            vol24 = 0
            for field in ["volume24hr", "volume_24h"]:
                try:
                    vol24 = float(m.get(field, 0) or 0)
                    if vol24 > 0:
                        break
                except (ValueError, TypeError):
                    pass

            confidence = min(vol24 / 100000, 0.8) if vol24 > 0 else 0.4
            # 选择更接近0.5的方向（波动空间大）
            if abs(yes_price - 0.5) <= abs(no_price - 0.5):
                outcome = "Yes"
                price = yes_price
            else:
                outcome = "No"
                price = no_price

            signals.append({
                "market": title,
                "slug": slug,
                "condition_id": cid,
                "outcome": outcome,
                "current_price": round(price, 4),
                "strategy": "high_volume",
                "target_profit": 0.12,
                "stop_loss": -0.08,
                "max_hold_hours": 72,
                "confidence": round(confidence, 2),
                "volume_24h": vol24,
            })

    signals.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
    log(f"  [high_volume] 找到 {len(signals)} 个高交易量信号")
    del all_markets
    gc.collect()
    return signals


# ═══════════════════════════════════════
# 策略5: 价差套利 (v11 新增)
# ═══════════════════════════════════════

def strategy_arbitrage(existing_cids=None):
    """
    v11: 扫描同一事件的多个相关市场，找价格不一致。
    例如 "X by March" Yes=0.30 但 "X by June" Yes=0.25 → June应>=March，买June。
    """
    log("  [arbitrage] 扫描价差套利机会...")
    if existing_cids is None:
        existing_cids = set()

    signals = []

    # 获取活跃事件（按交易量排序）
    events = api_get(
        "https://gamma-api.polymarket.com/events",
        {"active": "true", "limit": 30, "order": "volume24hr", "ascending": "false"},
        timeout=12,
    )
    time.sleep(DELAY)

    if not events:
        log("  [arbitrage] 无法获取事件数据")
        return signals

    log(f"  [arbitrage] 获取到 {len(events)} 个活跃事件")

    for event in events:
        event_title = event.get("title", "") or event.get("name", "")
        markets = event.get("markets", [])
        if not markets or len(markets) < 2:
            continue

        # 过滤体育/电竞
        if is_excluded(event_title):
            continue

        # 收集同一事件下的市场价格
        market_info = []
        for m in markets:
            title = m.get("question", "") or m.get("groupItemTitle", "") or ""
            if is_excluded(title):
                continue
            cid = m.get("conditionId", "")
            if not cid:
                continue
            price_map = parse_market_prices(m)
            if not price_map:
                continue
            yes_price = price_map.get("Yes", 0)
            no_price = price_map.get("No", 0)
            slug = m.get("slug", "") or event.get("slug", "")
            active = m.get("active", True)
            closed = m.get("closed", False)
            if not active or closed:
                continue
            market_info.append({
                "title": title,
                "cid": cid,
                "slug": slug,
                "yes_price": yes_price,
                "no_price": no_price,
            })

        if len(market_info) < 2:
            continue

        # 寻找时间序列不一致：较晚到期的应该 >= 较早到期的
        # 通过标题中的日期关键词排序
        import re
        date_pattern = re.compile(r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s*\d{0,2},?\s*\d{0,4}|\d{4}-\d{2}-\d{2}|\b(q[1-4])\b', re.IGNORECASE)
        month_order = {
            'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
            'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
            'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
            'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
            'q1': 3, 'q2': 6, 'q3': 9, 'q4': 12,
        }

        def extract_date_order(title):
            matches = date_pattern.findall(title.lower())
            if matches:
                for match_tuple in matches:
                    for match in match_tuple:
                        if not match:
                            continue
                        # Try YYYY-MM-DD
                        if '-' in match and len(match) == 10:
                            try:
                                return int(match.replace('-', ''))
                            except:
                                pass
                        # Try month name
                        parts = match.strip().split()
                        if parts:
                            month_key = parts[0].lower().rstrip(',')
                            if month_key in month_order:
                                return month_order[month_key]
            return 0

        for m in market_info:
            m["date_order"] = extract_date_order(m["title"])

        # Compare pairs: if later deadline has lower Yes price, it's mispriced
        dated_markets = [m for m in market_info if m["date_order"] > 0]
        dated_markets.sort(key=lambda x: x["date_order"])

        for i in range(len(dated_markets)):
            for j in range(i + 1, len(dated_markets)):
                earlier = dated_markets[i]
                later = dated_markets[j]
                # Later deadline should have >= Yes price than earlier
                if later["yes_price"] < earlier["yes_price"] - 0.05:
                    # Later is underpriced
                    cid = later["cid"]
                    if cid in existing_cids:
                        continue
                    mispricing = earlier["yes_price"] - later["yes_price"]
                    confidence = min(mispricing / 0.20, 0.9)
                    signals.append({
                        "market": later["title"],
                        "slug": later["slug"],
                        "condition_id": cid,
                        "outcome": "Yes",
                        "current_price": round(later["yes_price"], 4),
                        "strategy": "arbitrage",
                        "target_profit": round(mispricing * 0.6, 4),
                        "stop_loss": -0.08,
                        "max_hold_hours": 72,
                        "confidence": round(confidence, 2),
                        "mispricing": round(mispricing, 4),
                        "reference_market": earlier["title"][:40],
                        "num_traders": 0,
                        "trader_names": [],
                    })
                    existing_cids.add(cid)
                # Earlier deadline should have <= Yes price than later
                elif earlier["yes_price"] > later["yes_price"] + 0.05:
                    # Earlier might be overpriced → buy No on earlier
                    cid = earlier["cid"]
                    if cid in existing_cids:
                        continue
                    if earlier["no_price"] < 0.10 or earlier["no_price"] > 0.90:
                        continue
                    mispricing = earlier["yes_price"] - later["yes_price"]
                    confidence = min(mispricing / 0.20, 0.9)
                    signals.append({
                        "market": earlier["title"],
                        "slug": earlier["slug"],
                        "condition_id": cid,
                        "outcome": "No",
                        "current_price": round(earlier["no_price"], 4),
                        "strategy": "arbitrage",
                        "target_profit": round(mispricing * 0.6, 4),
                        "stop_loss": -0.08,
                        "max_hold_hours": 72,
                        "confidence": round(confidence, 2),
                        "mispricing": round(mispricing, 4),
                        "reference_market": later["title"][:40],
                        "num_traders": 0,
                        "trader_names": [],
                    })
                    existing_cids.add(cid)

    signals.sort(key=lambda x: x["confidence"], reverse=True)
    log(f"  [arbitrage] 找到 {len(signals)} 个套利信号")
    gc.collect()
    return signals


# ═══════════════════════════════════════
# 主入口
# ═══════════════════════════════════════

def scan_short_term_signals(elite_traders=None, existing_cids=None):
    """
    运行所有短线策略，返回合并的信号列表。
    每个策略独立运行，某个失败不影响其他。
    """
    log("[短线策略] 开始扫描...")
    if existing_cids is None:
        existing_cids = set()

    all_signals = []
    stats = {"catalyst": 0, "reversion": 0, "expiry": 0, "high_volume": 0, "arbitrage": 0, "errors": []}

    # 策略1: 催化剂
    if elite_traders:
        try:
            catalyst_signals = strategy_catalyst(elite_traders, existing_cids)
            all_signals.extend(catalyst_signals)
            stats["catalyst"] = len(catalyst_signals)
            for s in catalyst_signals:
                existing_cids.add(s["condition_id"])
        except Exception as e:
            log(f"  [catalyst] 策略失败: {e}")
            stats["errors"].append(f"catalyst: {e}")
    else:
        log("  [catalyst] 无精英交易员数据，跳过")

    gc.collect()

    # 策略2: 回归
    try:
        reversion_signals = strategy_reversion(existing_cids)
        all_signals.extend(reversion_signals)
        stats["reversion"] = len(reversion_signals)
        for s in reversion_signals:
            existing_cids.add(s["condition_id"])
    except Exception as e:
        log(f"  [reversion] 策略失败: {e}")
        stats["errors"].append(f"reversion: {e}")

    gc.collect()

    # 策略3: 到期收割
    try:
        expiry_signals = strategy_expiry(existing_cids)
        all_signals.extend(expiry_signals)
        stats["expiry"] = len(expiry_signals)
        for s in expiry_signals:
            existing_cids.add(s["condition_id"])
    except Exception as e:
        log(f"  [expiry] 策略失败: {e}")
        stats["errors"].append(f"expiry: {e}")

    gc.collect()

    # 策略4: 高交易量 (v10 新增)
    try:
        hv_signals = strategy_high_volume(existing_cids)
        all_signals.extend(hv_signals)
        stats["high_volume"] = len(hv_signals)
    except Exception as e:
        log(f"  [high_volume] 策略失败: {e}")
        stats["errors"].append(f"high_volume: {e}")

    gc.collect()

    # 策略5: 价差套利 (v11 新增)
    try:
        arb_signals = strategy_arbitrage(existing_cids)
        all_signals.extend(arb_signals)
        stats["arbitrage"] = len(arb_signals)
    except Exception as e:
        log(f"  [arbitrage] 策略失败: {e}")
        stats["errors"].append(f"arbitrage: {e}")

    gc.collect()

    log(f"[短线策略] 完成: 催化剂{stats['catalyst']} + 回归{stats['reversion']} + 到期{stats['expiry']} + 高量{stats['high_volume']} + 套利{stats['arbitrage']} = {len(all_signals)}个信号")
    return all_signals, stats
