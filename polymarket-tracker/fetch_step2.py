"""
Step 2: Fetch market details for all unique slugs from deep_trades.json
"""
import json, os, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def api_get(url, retries=2, delay=0.1):
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            time.sleep(delay)
            return data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 1.5)
            else:
                return None

def fetch_market(slug):
    url = f"https://gamma-api.polymarket.com/markets?slug={quote(slug)}"
    data = api_get(url)
    return slug, data

def main():
    print("=" * 60, flush=True)
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

            if (i + 1) % 200 == 0:
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
    print(f"  Total slugs fetched: {fetched}/{len(slugs)}", flush=True)
    print(f"  Failed: {failed}", flush=True)
    print(f"  Closed/resolved markets: {closed_count}", flush=True)
    print(f"  Saved to: {out_path}", flush=True)

    # Sample a closed market to understand structure
    for slug, markets in all_markets.items():
        for m in markets:
            if m.get("closed"):
                print(f"\n  Sample closed market:", flush=True)
                print(f"    slug: {slug}", flush=True)
                print(f"    question: {m.get('question', '')[:60]}", flush=True)
                print(f"    outcomes: {m.get('outcomes', '')}", flush=True)
                print(f"    outcomePrices: {m.get('outcomePrices', '')}", flush=True)
                print(f"    conditionId: {m.get('conditionId', '')[:30]}...", flush=True)
                return

if __name__ == "__main__":
    main()
