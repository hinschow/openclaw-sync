"""
Step 1: Fetch deep positions for all traders with safety limits.
"""
import json, os, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def api_get(url, retries=3, delay=0.15):
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            time.sleep(delay)
            return data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 2)
            else:
                print(f"  FAILED: {url[:80]}... ({e})", flush=True)
                return None

def fetch_all_positions(wallet, max_pages=50):
    """Fetch all positions for a wallet, paginated, with max_pages safety."""
    all_positions = []
    offset = 0
    limit = 100
    page = 0
    while page < max_pages:
        url = f"https://data-api.polymarket.com/positions?user={wallet}&limit={limit}&offset={offset}"
        data = api_get(url)
        if data is None or len(data) == 0:
            break
        all_positions.extend(data)
        if len(data) < limit:
            break
        offset += limit
        page += 1
    return wallet, all_positions

def main():
    print("=" * 60, flush=True)
    print("STEP 1: Fetching deep positions for all traders", flush=True)
    print("=" * 60, flush=True)

    with open(os.path.join(DATA_DIR, "real_traders.json")) as f:
        traders = json.load(f)

    wallets = [t["wallet"] for t in traders]
    wallet_names = {t["wallet"]: t["name"] for t in traders}
    print(f"Traders to fetch: {len(wallets)}", flush=True)

    all_positions = []
    # Process sequentially to avoid overwhelming the API and to handle large wallets
    # But use threads for batches of 6
    batch_size = 6
    for batch_start in range(0, len(wallets), batch_size):
        batch = wallets[batch_start:batch_start + batch_size]
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fetch_all_positions, w): w for w in batch}
            for future in as_completed(futures):
                wallet = futures[future]
                try:
                    w, positions = future.result()
                    all_positions.extend(positions)
                    name = wallet_names.get(w, w[:10])
                    idx = wallets.index(w) + 1
                    print(f"  [{idx}/{len(wallets)}] {name}: {len(positions)} positions", flush=True)
                except Exception as e:
                    print(f"  ERROR {wallet[:10]}: {e}", flush=True)

    # Save
    out_path = os.path.join(DATA_DIR, "deep_positions.json")
    with open(out_path, "w") as f:
        json.dump(all_positions, f)

    condition_ids = set(p.get("conditionId", "") for p in all_positions if p.get("conditionId"))
    print(f"\nStep 1 Results:", flush=True)
    print(f"  Total positions: {len(all_positions)}", flush=True)
    print(f"  Unique conditionIds: {len(condition_ids)}", flush=True)
    print(f"  Saved to: {out_path}", flush=True)

if __name__ == "__main__":
    main()
