"""
Step 2: Fetch market details - FAST version with more workers and less delay.
Saves progress incrementally.
"""
import json, os, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.parse import quote

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "deep_markets.json")

def api_get(url, retries=2, delay=0.05):
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            time.sleep(delay)
            return data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return None

def fetch_market(slug):
    url = f"https://gamma-api.polymarket.com/markets?slug={quote(slug)}"
    return slug, api_get(url)

def main():
    print("STEP 2: Fetching market details (fast mode)", flush=True)

    with open(os.path.join(DATA_DIR, "deep_trades.json")) as f:
        trades = json.load(f)

    slugs = list(set(t.get("slug", "") for t in trades if t.get("slug")))
    
    # Load existing progress if any
    all_markets = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH) as f:
                all_markets = json.load(f)
            print(f"Loaded {len(all_markets)} existing markets", flush=True)
        except:
            pass
    
    # Filter out already fetched
    remaining = [s for s in slugs if s not in all_markets]
    print(f"Total slugs: {len(slugs)}, remaining: {len(remaining)}", flush=True)

    fetched = 0
    failed = 0

    # Use 12 workers with minimal delay
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_market, s): s for s in remaining}
        for i, future in enumerate(as_completed(futures)):
            slug = futures[future]
            try:
                s, data = future.result()
                if data and len(data) > 0:
                    all_markets[s] = data
                    fetched += 1
                else:
                    failed += 1
            except:
                failed += 1

            # Save every 500
            if (i + 1) % 500 == 0:
                with open(OUT_PATH, "w") as f:
                    json.dump(all_markets, f)
                print(f"  Progress: {i+1}/{len(remaining)} (fetched: {fetched}, failed: {failed}) [saved]", flush=True)
            elif (i + 1) % 100 == 0:
                print(f"  Progress: {i+1}/{len(remaining)} (fetched: {fetched}, failed: {failed})", flush=True)

    # Final save
    with open(OUT_PATH, "w") as f:
        json.dump(all_markets, f)

    closed_count = sum(1 for slug, markets in all_markets.items() 
                       for m in markets if m.get("closed") or m.get("resolved"))

    print(f"\nStep 2 Results:", flush=True)
    print(f"  Total markets: {len(all_markets)}/{len(slugs)}", flush=True)
    print(f"  This run: fetched={fetched}, failed={failed}", flush=True)
    print(f"  Closed/resolved: {closed_count}", flush=True)

    # Sample closed market
    for slug, markets in all_markets.items():
        for m in markets:
            if m.get("closed"):
                print(f"\n  Sample closed market:", flush=True)
                print(f"    slug: {slug}", flush=True)
                print(f"    question: {m.get('question', '')[:60]}", flush=True)
                print(f"    outcomes: {m.get('outcomes', '')}", flush=True)
                print(f"    outcomePrices: {m.get('outcomePrices', '')}", flush=True)
                return

if __name__ == "__main__":
    main()
