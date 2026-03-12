#!/usr/bin/env python3
"""扩充交易员池 - 从 Polymarket API 发现新的活跃交易员"""
import json, os, time, sys, requests
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DELAY = 0.3

def log(msg):
    print(msg, flush=True)

def api_get(url, params, timeout=30):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  API error: {e}")
        return []

def main():
    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        existing = json.load(f)
    existing_wallets = {t["wallet"] for t in existing}
    log(f"Existing: {len(existing_wallets)} traders")

    wallet_info = {}
    all_trades = []

    # 1. Recent trades - 20 pages
    log("[1] Recent trades...")
    for offset in range(0, 2000, 100):
        trades = api_get("https://data-api.polymarket.com/trades", {"limit": 100, "offset": offset})
        if not trades:
            break
        all_trades.extend(trades)
        for t in trades:
            w = t.get("proxyWallet", "")
            if w and w not in wallet_info:
                wallet_info[w] = {"name": t.get("name", ""), "pseudonym": t.get("pseudonym", "")}
        log(f"  offset={offset}: {len(trades)} trades, wallets={len(wallet_info)}")
        time.sleep(DELAY)

    # 2. Hot market trades
    log("[2] Hot markets...")
    markets = api_get("https://gamma-api.polymarket.com/markets", 
                       {"limit": 30, "active": "true", "order": "volume", "ascending": "false", "closed": "false"})
    log(f"  Got {len(markets)} markets")
    
    skip = ["up or down", "up/down", "vs.", "vs ", "game 1", "game 2", "game 3", "game 4", "game 5"]
    for m in markets:
        q = m.get("question", "").lower()
        slug = m.get("slug", "")
        if any(kw in q for kw in skip) or not slug:
            continue
        trades = api_get("https://data-api.polymarket.com/trades", {"slug": slug, "limit": 200})
        all_trades.extend(trades)
        for t in trades:
            w = t.get("proxyWallet", "")
            if w and w not in wallet_info:
                wallet_info[w] = {"name": t.get("name", ""), "pseudonym": t.get("pseudonym", "")}
        log(f"  {slug[:45]:45s} | {len(trades):3d} trades | wallets={len(wallet_info)}")
        time.sleep(DELAY)

    # 3. Also get trades from resolved/popular political markets
    log("[3] Political/resolved markets...")
    political_slugs = []
    pmarkets = api_get("https://gamma-api.polymarket.com/markets",
                        {"limit": 30, "order": "volume", "ascending": "false", "closed": "true"})
    for m in pmarkets:
        slug = m.get("slug", "")
        if slug:
            political_slugs.append(slug)
    
    for slug in political_slugs[:15]:
        trades = api_get("https://data-api.polymarket.com/trades", {"slug": slug, "limit": 200})
        all_trades.extend(trades)
        for t in trades:
            w = t.get("proxyWallet", "")
            if w and w not in wallet_info:
                wallet_info[w] = {"name": t.get("name", ""), "pseudonym": t.get("pseudonym", "")}
        log(f"  {slug[:45]:45s} | {len(trades):3d} trades | wallets={len(wallet_info)}")
        time.sleep(DELAY)

    # 4. Analyze
    log(f"\n[4] Analyzing {len(wallet_info)} wallets from {len(all_trades)} trades...")
    ws = defaultdict(lambda: {"count": 0, "buys": 0, "usdc": 0, "markets": set()})
    for t in all_trades:
        w = t.get("proxyWallet", "")
        if not w: continue
        ws[w]["count"] += 1
        if t.get("side") == "BUY": ws[w]["buys"] += 1
        ws[w]["usdc"] += t.get("usdcSize", 0) or (t.get("size", 0) * t.get("price", 0))
        ws[w]["markets"].add(t.get("conditionId", "") or t.get("slug", ""))

    # Filter qualified
    qualified = {w: s for w, s in ws.items() if s["count"] >= 3 and len(s["markets"]) >= 2}
    new_wallets = {w: s for w, s in qualified.items() if w not in existing_wallets}
    log(f"  Qualified: {len(qualified)}, New: {len(new_wallets)}")

    # Sort by volume, take top 60
    sorted_new = sorted(new_wallets.items(), key=lambda x: x[1]["usdc"], reverse=True)[:60]

    # 5. Fetch activity for new traders
    log(f"\n[5] Fetching activity for {len(sorted_new)} new traders...")
    new_traders = []
    trader_activities = {}
    for i, (w, stats) in enumerate(sorted_new):
        info = wallet_info.get(w, {})
        activities = api_get("https://data-api.polymarket.com/activity", {"user": w, "limit": 100})
        trader_activities[w] = activities
        
        buys = [a for a in activities if a.get("side") == "BUY" and a.get("type") == "TRADE"]
        new_traders.append({
            "wallet": w,
            "name": info.get("name", w),
            "pseudonym": info.get("pseudonym", ""),
            "pnl": 0,
            "trade_count": stats["count"],
            "total_usdc": round(stats["usdc"], 2),
            "market_count": len(stats["markets"]),
            "activity_count": len(activities),
            "activity_buys": len(buys),
            "source": "discovered",
        })
        if (i + 1) % 10 == 0:
            log(f"  {i+1}/{len(sorted_new)} done")
        time.sleep(DELAY)

    # 6. Merge and save
    for t in existing:
        t["source"] = "original"
    all_traders = existing + new_traders
    log(f"\n[6] Total: {len(all_traders)} traders ({len(existing)} + {len(new_traders)})")

    with open(os.path.join(DATA_DIR, "expanded_traders.json"), "w") as f:
        json.dump(all_traders, f, indent=2)
    with open(os.path.join(DATA_DIR, "expanded_activities.json"), "w") as f:
        json.dump(trader_activities, f, indent=2)
    log("Saved!")

    log(f"\nTop 15 new traders:")
    for t in new_traders[:15]:
        log(f"  {t['name'][:30]:30s} | trades:{t['trade_count']:4d} | usdc:${t['total_usdc']:>10,.0f} | mkts:{t['market_count']}")

if __name__ == "__main__":
    main()
