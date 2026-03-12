"""
Step 1 & 2: Fetch deep positions and market data for backtest repair.
- Step 1: Fetch all positions for each trader wallet (paginated)
- Step 2: Fetch market details for all unique slugs from deep_trades.json
"""

import json
import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def api_get(url, retries=3, delay=0.15):
    """Simple GET with retries."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            time.sleep(delay)
            return data
        except (HTTPError, URLError, Exception) as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 2
                print(f"  Retry {attempt+1} for {url[:80]}... ({e})", flush=True)
                time.sleep(wait)
            else:
                print(f"  FAILED: {url[:80]}... ({e})", flush=True)
                return None


def fetch_all_positions(wallet):
    """Fetch all positions for a wallet, paginated."""
    all_positions = []
    offset = 0
    limit = 100
    while True:
        url = f"https://data-api.polymarket.com/positions?user={wallet}&limit={limit}&offset={offset}"
        data = api_get(url)
        if data is None or len(data) == 0:
            break
        all_positions.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return wallet, all_positions


def fetch_market(slug):
    """Fetch market details for a slug."""
    url = f"https://gamma-api.polymarket.com/markets?slug={quote(slug)}"
    data = api_get(url)
    return slug, data


def step1_fetch_positions():
    """Step 1: Fetch all positions for all traders."""
    print("=" * 60, flush=True)
    print("STEP 1: Fetching deep positions for all traders", flush=True)
    print("=" * 60, flush=True)

    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)

    wallets = [t["wallet"] for t in traders]
    print(f"Traders to fetch: {len(wallets)}", flush=True)

    all_positions = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_all_positions, w): w for w in wallets}
        for i, future in enumerate(as_completed(futures)):
            wallet = futures[future]
            try:
                w, positions = future.result()
                all_positions.extend(positions)
                trader_name = next((t["name"] for t in traders if t["wallet"] == w), w[:10])
                print(f"  [{i+1}/{len(wallets)}] {trader_name}: {len(positions)} positions", flush=True)
            except Exception as e:
                print(f"  [{i+1}/{len(wallets)}] ERROR {wallet[:10]}: {e}", flush=True)

    # Save
    out_path = os.path.join(DATA_DIR, "deep_positions.json")
    with open(out_path, "w") as f:
        json.dump(all_positions, f)

    # Stats
    condition_ids = set(p.get("conditionId", "") for p in all_positions if p.get("conditionId"))
    print(f"\nStep 1 Results:", flush=True)
    print(f"  Total positions: {len(all_positions)}", flush=True)
    print(f"  Unique conditionIds: {len(condition_ids)}", flush=True)
    print(f"  Saved to: {out_path}", flush=True)
    return all_positions


def step2_fetch_markets():
    """Step 2: Fetch market details for all unique slugs."""
    print("\n" + "=" * 60, flush=True)
    print("STEP 2: Fetching market details for all slugs", flush=True)
    print("=" * 60, flush=True)

    with open(os.path.join(DATA_DIR, "deep_trades.json")) as f:
        trades = json.load(f)

    slugs = list(set(t.get("slug", "") for t in trades if t.get("slug")))
    print(f"Unique slugs to fetch: {len(slugs)}", flush=True)

    all_markets = {}
    fetched = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_market, s): s for s in slugs}
        for i, future in enumerate(as_completed(futures)):
            slug = futures[future]
            try:
                s, data = future.result()
                if data and len(data) > 0:
                    all_markets[s] = data
                    fetched += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1

            if (i + 1) % 100 == 0:
                print(f"  Progress: {i+1}/{len(slugs)} (fetched: {fetched}, failed: {failed})", flush=True)

    # Save
    out_path = os.path.join(DATA_DIR, "deep_markets.json")
    with open(out_path, "w") as f:
        json.dump(all_markets, f)

    # Stats
    closed_count = 0
    for slug, markets in all_markets.items():
        for m in markets:
            if m.get("closed") or m.get("resolved"):
                closed_count += 1

    print(f"\nStep 2 Results:", flush=True)
    print(f"  Total slugs fetched: {fetched}", flush=True)
    print(f"  Failed: {failed}", flush=True)
    print(f"  Closed/resolved markets: {closed_count}", flush=True)
    print(f"  Saved to: {out_path}", flush=True)
    return all_markets


if __name__ == "__main__":
    step1_fetch_positions()
    step2_fetch_markets()
    print("\n✅ Data collection complete!", flush=True)
