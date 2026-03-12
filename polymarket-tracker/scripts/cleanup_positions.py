#!/usr/bin/env python3
"""
持仓清理脚本：对102笔持仓评分，保留top 25，其余平仓。
"""
import json
import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SIM_PORTFOLIO_PATH = os.path.join(DATA_DIR, "sim_portfolio.json")
BACKUP_PATH = os.path.join(DATA_DIR, "sim_portfolio_backup_cleanup.json")

API_TIMEOUT = 10
BATCH_SIZE = 10
KEEP_COUNT = 25

# ── 分类关键词 ──
SPORTS_KEYWORDS = [
    "win on 2026-", "vs.", "vs ",
    "nba", "nfl", "nhl", "mlb", "ncaa",
    "lol:", "t20", "lec", "blast", "epl",
    "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "copa del", "ligue 1",
    "open:", "grand prix", "world cup",
    "six nations", "rugby",
    "jaguars vs", "tigers vs", "lajovic", "hanfmann",
    "southern jaguars", "texas southern",
    "rio open", "atp-", "boxing", "mma",
    "cricket", "tennis", "soccer", "football",
    "basketball", "esports",
    "ice hockey gold medal", "ice hockey",
    "biathlon", "winter olympics 2026:",
    "middlesbrough", "rio ave", "moreirense",
    "eurovision",
]

CRYPTO_SHORT_KEYWORDS = [
    "up or down", "o/u",
    "btc-updown", "eth-updown", "sol-updown",
    "bitcoin up or down", "ethereum up or down", "solana up or down",
]

POLITICAL_GEO_KEYWORDS = [
    "trump", "election", "president", "congress", "senate", "governor",
    "fed", "interest rate", "inflation", "recession",
    "war", "invasion", "nato", "sanctions", "strike", "iran",
    "ceasefire", "ukraine", "russia", "israel", "palestine",
    "shutdown", "tariff", "supreme court",
    "khamenei", "netanyahu", "starmer", "machado",
    "nominee", "primary", "democratic", "republican",
]

CRYPTO_TECH_KEYWORDS = [
    "etf", "approve", "ban", "crypto", "bitcoin", "ethereum",
    "ai model", "openai", "anthropic", "google", "grok",
    "apple", "nvidia", "alphabet", "waymo", "spacex",
]

def classify_for_scoring(title, slug):
    """分类用于评分"""
    t = title.lower()
    s = slug.lower() if slug else ""
    
    # 体育/电竞 → 直接清除
    for kw in SPORTS_KEYWORDS:
        if kw in t or kw in s:
            return "SPORTS"
    
    # 短线加密涨跌盘 → 直接清除
    for kw in CRYPTO_SHORT_KEYWORDS:
        if kw in t or kw in s:
            return "CRYPTO_SHORT"
    
    # 政治/地缘
    for kw in POLITICAL_GEO_KEYWORDS:
        if kw in t:
            return "POLITICAL"
    
    # 加密/科技
    for kw in CRYPTO_TECH_KEYWORDS:
        if kw in t:
            return "TECH"
    
    return "OTHER"


def score_position(pos):
    """对持仓评分"""
    score = 0
    reasons = []
    title = pos.get("market", "")
    slug = pos.get("slug", "")
    category = classify_for_scoring(title, slug)
    
    # ── 扣分项（直接清除）──
    if category == "SPORTS":
        score -= 10
        reasons.append("体育/电竞(-10)")
        return score, reasons, category
    
    if category == "CRYPTO_SHORT":
        score -= 10
        reasons.append("短线加密涨跌(-10)")
        return score, reasons, category
    
    # 标题含 "win on 2026-" 的短线盘
    t = title.lower()
    if re.search(r'win on 2026-\d{2}-\d{2}', t):
        score -= 10
        reasons.append("短线日期盘(-10)")
        return score, reasons, category
    
    # ── 加分项 ──
    
    # 1. 多个交易员共识 (+3/人)
    num_traders = pos.get("num_traders", 1)
    if num_traders >= 2:
        bonus = num_traders * 3
        score += bonus
        reasons.append(f"{num_traders}人共识(+{bonus})")
    
    # 2. 政治/地缘/加密/科技 (+5)
    if category in ("POLITICAL", "TECH"):
        score += 5
        reasons.append(f"{category}类(+5)")
    
    # 3. 入场价格优势
    entry_price = pos.get("entry_price", 0.5)
    outcome = pos.get("outcome", "")
    if outcome.lower() == "yes" and entry_price < 0.3:
        score += 3
        reasons.append(f"低价Yes@{entry_price}(+3)")
    elif outcome.lower() == "no" and entry_price > 0.7:
        score += 3
        reasons.append(f"高价No@{entry_price}(+3)")
    
    # 4. 到期时间合理（1周-3个月）→ 通过slug/title推断
    # 简单判断：如果有明确的短期日期（< 24h），扣分
    entry_date = pos.get("entry_date", "")
    now = datetime.now(timezone.utc)
    
    # 检查是否有明确的到期日期在标题中
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', slug) or re.search(r'(february|march|april|may|june) (\d{1,2})', t)
    if date_match:
        # 尝试解析slug中的日期
        slug_date_match = re.search(r'(\d{4}-\d{2}-\d{2})', slug)
        if slug_date_match:
            try:
                expiry = datetime.strptime(slug_date_match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_to_expiry = (expiry - now).days
                if days_to_expiry < 1:
                    score -= 5
                    reasons.append(f"到期<24h(-5)")
                elif 7 <= days_to_expiry <= 90:
                    score += 2
                    reasons.append(f"到期{days_to_expiry}天(+2)")
            except:
                pass
    else:
        # 没有明确日期，可能是长期盘，给+2
        if "2026" in t or "2027" in t:
            score += 2
            reasons.append("长期盘(+2)")
    
    # 5. 当前盈利中
    upnl = pos.get("unrealized_pnl", 0)
    size = pos.get("size", 10)
    if upnl > 0:
        score += 2
        reasons.append(f"盈利${upnl:.2f}(+2)")
    
    # ── 扣分项 ──
    # 当前亏损 > 15%
    if size > 0 and upnl < 0:
        loss_pct = abs(upnl) / size
        if loss_pct > 0.15:
            score -= 3
            reasons.append(f"亏损{loss_pct*100:.0f}%(-3)")
    
    return score, reasons, category


def fetch_current_prices_batch(positions):
    """分批查询当前价格，每批10笔"""
    prices = {}
    slugs_seen = set()
    slug_list = []
    
    for pos in positions:
        slug = pos.get("slug", "")
        if slug and slug not in slugs_seen:
            slugs_seen.add(slug)
            slug_list.append(slug)
    
    total = len(slug_list)
    print(f"  需要查询 {total} 个市场价格，分 {(total + BATCH_SIZE - 1) // BATCH_SIZE} 批...")
    
    for batch_idx in range(0, total, BATCH_SIZE):
        batch = slug_list[batch_idx:batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        found = 0
        
        for slug in batch:
            try:
                r = requests.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": slug, "limit": 10},
                    timeout=API_TIMEOUT
                )
                r.raise_for_status()
                data = r.json()
                time.sleep(0.3)
                
                for m in data:
                    cid = m.get("conditionId", "")
                    outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
                    price_strs = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices", [])
                    
                    price_map = {}
                    for outcome, price in zip(outcomes, price_strs):
                        try:
                            price_map[outcome] = float(price)
                        except:
                            pass
                    
                    if cid and price_map:
                        prices[cid] = price_map
                        found += 1
            except Exception as e:
                print(f"    查询 {slug[:30]}... 失败: {e}")
        
        print(f"    [批次 {batch_num}] 查询 {len(batch)} 个slug，获取 {found} 个价格")
        time.sleep(0.5)
    
    print(f"  共获取 {len(prices)} 个市场价格")
    return prices


def main():
    print("=" * 60)
    print("🧹 持仓清理脚本")
    print(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    
    # 加载
    with open(SIM_PORTFOLIO_PATH) as f:
        portfolio = json.load(f)
    
    # 备份
    with open(BACKUP_PATH, "w") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已备份到 {BACKUP_PATH}")
    
    open_positions = portfolio["open_positions"]
    balance_before = portfolio["balance"]
    count_before = len(open_positions)
    
    print(f"\n📊 清理前状态:")
    print(f"  持仓数: {count_before}")
    print(f"  可用余额: ${balance_before:.2f}")
    print(f"  持仓总成本: ${sum(p['size'] for p in open_positions):.2f}")
    
    # ── 评分 ──
    print(f"\n📝 对 {count_before} 笔持仓评分...")
    scored = []
    for pos in open_positions:
        score, reasons, category = score_position(pos)
        scored.append({
            "pos": pos,
            "score": score,
            "reasons": reasons,
            "category": category,
        })
    
    # 排序
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    # 打印所有评分
    print(f"\n{'排名':>4} | {'分数':>4} | {'类别':>12} | {'市场':50} | 原因")
    print("-" * 120)
    for i, s in enumerate(scored):
        market = s["pos"]["market"][:50]
        reasons_str = ", ".join(s["reasons"]) if s["reasons"] else "-"
        print(f"{i+1:>4} | {s['score']:>4} | {s['category']:>12} | {market:50} | {reasons_str}")
    
    # 分割：保留 top KEEP_COUNT，其余平仓
    keep = scored[:KEEP_COUNT]
    close = scored[KEEP_COUNT:]
    
    print(f"\n✂️ 保留 {len(keep)} 笔，平仓 {len(close)} 笔")
    
    # ── 查询当前价格（用于平仓计算）──
    print(f"\n💰 查询当前价格...")
    close_positions = [s["pos"] for s in close]
    current_prices = fetch_current_prices_batch(close_positions)
    
    # ── 执行平仓 ──
    print(f"\n🔄 执行模拟平仓...")
    total_released = 0
    total_pnl = 0
    closed_details = []
    
    for s in close:
        pos = s["pos"]
        cid = pos["condition_id"]
        outcome = pos["outcome"]
        entry_price = pos["entry_price"]
        size = pos["size"]
        
        # 获取当前价格
        price_map = current_prices.get(cid, {})
        current_price = price_map.get(outcome, entry_price)  # 查不到按入场价
        
        # 计算盈亏
        if entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = 0
        pnl = size * pnl_pct
        
        # 平仓记录
        pos["exit_price"] = round(current_price, 4)
        pos["pnl"] = round(pnl, 2)
        pos["pnl_pct"] = round(pnl_pct * 100, 2)
        pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos["exit_reason"] = f"cleanup_score_{s['score']}"
        
        released = size + pnl
        total_released += released
        total_pnl += pnl
        
        closed_details.append({
            "market": pos["market"][:50],
            "score": s["score"],
            "category": s["category"],
            "size": size,
            "pnl": round(pnl, 2),
            "reasons": s["reasons"],
        })
    
    # ── 更新 portfolio ──
    new_open = [s["pos"] for s in keep]
    new_closed = portfolio.get("closed_positions", []) + [s["pos"] for s in close]
    new_balance = portfolio["balance"] + total_released
    
    portfolio["open_positions"] = new_open
    portfolio["closed_positions"] = new_closed
    portfolio["balance"] = round(new_balance, 2)
    
    # 保存
    with open(SIM_PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, ensure_ascii=False, separators=(",", ":"))
    
    # ── 报告 ──
    print(f"\n{'=' * 60}")
    print(f"📊 清理完成报告")
    print(f"{'=' * 60}")
    print(f"\n清理前: {count_before} 笔持仓, 余额 ${balance_before:.2f}")
    print(f"清理后: {len(new_open)} 笔持仓, 余额 ${new_balance:.2f}")
    print(f"平仓: {len(close)} 笔")
    print(f"释放资金: ${total_released:.2f}")
    print(f"平仓盈亏: ${total_pnl:+.2f}")
    
    print(f"\n🏆 保留的 Top {len(keep)} 笔持仓:")
    for i, s in enumerate(keep):
        p = s["pos"]
        reasons_str = ", ".join(s["reasons"]) if s["reasons"] else "-"
        print(f"  {i+1:>2}. [{s['score']:>3}分] {p['market'][:50]} | {p['outcome']} @ {p['entry_price']} | ${p['size']} | {reasons_str}")
    
    print(f"\n❌ 平仓的 {len(close)} 笔:")
    for d in closed_details:
        pnl_str = f"+${d['pnl']:.2f}" if d["pnl"] >= 0 else f"-${abs(d['pnl']):.2f}"
        print(f"  [{d['score']:>3}分] {d['market'][:45]} | ${d['size']} | {pnl_str} | {d['category']}")
    
    # 输出JSON摘要供后续使用
    summary = {
        "before": {"count": count_before, "balance": balance_before},
        "after": {"count": len(new_open), "balance": round(new_balance, 2)},
        "closed_count": len(close),
        "released_funds": round(total_released, 2),
        "cleanup_pnl": round(total_pnl, 2),
        "kept_top10": [
            {"rank": i+1, "score": s["score"], "market": s["pos"]["market"][:60], 
             "outcome": s["pos"]["outcome"], "size": s["pos"]["size"]}
            for i, s in enumerate(keep[:10])
        ],
    }
    
    summary_path = os.path.join(DATA_DIR, "cleanup_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n📄 摘要已保存到 {summary_path}")


if __name__ == "__main__":
    main()
