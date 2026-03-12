#!/usr/bin/env python3
"""
增强版新闻采集脚本 - Enhanced News Collector
整合多个技能获取：
  1. 加密货币实时行情（crypto-market-data skill）
  2. 黄金白银贵金属价格（crypto-gold-monitor / CoinGecko）
  3. 财联社快讯（CLS API）
  4. AI 领域新闻（web 搜索 + 抓取）

用法:
  python3 enhanced_news.py           # 完整采集
  python3 enhanced_news.py crypto    # 仅加密货币
  python3 enhanced_news.py metals    # 仅贵金属
  python3 enhanced_news.py cls       # 仅财联社
  python3 enhanced_news.py ai        # 仅 AI 新闻
"""
import json
import subprocess
import sys
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills")
CRYPTO_SKILL = os.path.join(SKILL_DIR, "crypto-market-data", "scripts")
GOLD_SKILL = os.path.join(SKILL_DIR, "crypto-gold-monitor", "crypto-monitor.sh")

# ── 工具函数 ──

def run_node(script, args=None, timeout=30):
    """运行 Node.js 脚本并返回 JSON"""
    cmd = ["node", script] + (args or [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception as e:
        print(f"  [WARN] node {os.path.basename(script)} failed: {e}", file=sys.stderr)
    return None


def http_get_json(url, timeout=15):
    """简单 HTTP GET 返回 JSON"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [WARN] HTTP GET {url[:60]}... failed: {e}", file=sys.stderr)
        return None


def fmt_num(n, decimals=2):
    """格式化数字，大数用 B/M/K"""
    if n is None:
        return "N/A"
    if abs(n) >= 1e12:
        return f"${n/1e12:.2f}T"
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.2f}M"
    if abs(n) >= 1e4:
        return f"${n/1e3:.1f}K"
    return f"${n:,.{decimals}f}"


def fmt_pct(n):
    """格式化百分比"""
    if n is None:
        return "--"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


# ── 1. 加密货币行情 ──

def fetch_crypto_prices():
    """使用 crypto-market-data skill 获取主流币行情"""
    print("📡 获取加密货币行情...", file=sys.stderr)

    # 使用 get_top_coins 获取丰富数据
    script = os.path.join(CRYPTO_SKILL, "get_top_coins.js")
    data = run_node(script, ["--per_page=15", "--currency=usd"], timeout=30)

    if not data or not isinstance(data, list):
        # fallback: 直接用 CoinGecko
        print("  [WARN] get_top_coins failed, trying direct API...", file=sys.stderr)
        data = http_get_json(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc&per_page=15&page=1"
            "&sparkline=false&price_change_percentage=24h"
        )

    if not data:
        return "⚠️ 加密货币数据获取失败\n"

    # 补充小币种（不在 top 15 中但需要跟踪的）
    # RATS: 使用 Binance 合约 1000RATSUSDT 价格（更准确）
    existing_ids = {c.get("id") for c in data}
    if "rats" not in existing_ids:
        rats_data = http_get_json("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=1000RATSUSDT")
        if rats_data and "lastPrice" in rats_data:
            rats_price = float(rats_data["lastPrice"]) / 1000  # 1000RATS -> 1 RATS
            rats_high = float(rats_data.get("highPrice", 0)) / 1000
            rats_low = float(rats_data.get("lowPrice", 0)) / 1000
            rats_change = float(rats_data.get("priceChangePercent", 0))
            rats_vol = float(rats_data.get("quoteVolume", 0))
            data.append({
                "id": "rats", "name": "RATS", "symbol": "rats",
                "current_price": rats_price,
                "price_change_percentage_24h": rats_change,
                "market_cap": 0, "total_volume": rats_vol,
                "high_24h": rats_high, "low_24h": rats_low,
                "market_cap_rank": None,
                "_source": "binance_futures"
            })

    # 获取汇率
    rate_data = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")
    cny_rate = rate_data["rates"].get("CNY", 7.25) if rate_data else 7.25

    lines = []
    lines.append("₿ 加密货币实时行情")
    lines.append(f"  汇率: 1 USD = ¥{cny_rate:.2f}")
    lines.append("")

    # 重点币种
    highlight_ids = {"bitcoin", "ethereum", "solana", "dogecoin", "ripple",
                     "cardano", "avalanche-2", "chainlink", "polkadot", "tron",
                     "rats"}

    for coin in data:
        cid = coin.get("id", "")
        name = coin.get("name", "")
        symbol = coin.get("symbol", "").upper()
        price = coin.get("current_price") or 0
        change_24h = coin.get("price_change_percentage_24h")
        mcap = coin.get("market_cap") or 0
        vol = coin.get("total_volume") or 0
        high = coin.get("high_24h")
        low = coin.get("low_24h")
        rank = coin.get("market_cap_rank") or 999

        arrow = "🟢" if (change_24h or 0) >= 0 else "🔴"
        cny_price = (price or 0) * cny_rate

        if cid in highlight_ids or (isinstance(rank, int) and rank <= 10):
            lines.append(f"  {arrow} #{rank} {name} ({symbol})")
            # 智能价格格式化：极小价格用科学计数或更多小数位
            if price < 0.001:
                price_str = f"${price:.8f}"
                cny_str = f"¥{cny_price:.6f}"
            elif price < 1:
                price_str = f"${price:.4f}"
                cny_str = f"¥{cny_price:.2f}"
            else:
                price_str = f"${price:,.2f}"
                cny_str = f"¥{cny_price:,.0f}"
            lines.append(f"     价格: {price_str} / {cny_str}")
            if high and high < 0.001:
                high_str = f"${high:.8f}"
            elif high and high < 1:
                high_str = f"${high:.4f}"
            else:
                high_str = f"${high:,.2f}" if high else "N/A"
            if low and low < 0.001:
                low_str = f"${low:.8f}"
            elif low and low < 1:
                low_str = f"${low:.4f}"
            else:
                low_str = f"${low:,.2f}" if low else "N/A"
            lines.append(f"     24h: {fmt_pct(change_24h)} | 高: {high_str} 低: {low_str}")
            lines.append(f"     市值: {fmt_num(mcap)} | 24h量: {fmt_num(vol)}")
            lines.append("")

    # 全局市场数据
    global_script = os.path.join(CRYPTO_SKILL, "get_global_market_data.js")
    gdata = run_node(global_script, timeout=20)
    if gdata and "data" in gdata:
        g = gdata["data"]
        total_mcap = g.get("total_market_cap", {}).get("usd", 0)
        total_vol = g.get("total_volume", {}).get("usd", 0)
        btc_dom = g.get("market_cap_percentage", {}).get("btc", 0)
        lines.append(f"  📊 全球加密市场")
        lines.append(f"     总市值: {fmt_num(total_mcap)} | 24h总量: {fmt_num(total_vol)}")
        lines.append(f"     BTC 占比: {btc_dom:.1f}%")
        lines.append("")

    return "\n".join(lines)


# ── 2. 黄金白银贵金属 ──

def fetch_metals_prices():
    """获取黄金白银价格"""
    print("📡 获取贵金属行情...", file=sys.stderr)

    gold_price = None
    silver_price = None
    gold_change = None
    silver_change = None

    # 方法1: 使用 crypto-gold-monitor 的 CoinGecko API 直接获取
    cg_data = http_get_json(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=xau,xag"
    )
    if cg_data and "bitcoin" in cg_data:
        # BTC 价格已知，反推金银价格
        btc_xau = cg_data["bitcoin"].get("xau")  # 1 BTC = ? oz gold
        btc_xag = cg_data["bitcoin"].get("xag")  # 1 BTC = ? oz silver
        btc_usd_data = http_get_json(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin&vs_currencies=usd"
        )
        if btc_usd_data and btc_xau and btc_xag:
            btc_usd = btc_usd_data["bitcoin"]["usd"]
            gold_price = round(btc_usd / btc_xau, 2)
            silver_price = round(btc_usd / btc_xag, 2)

    # 方法2: 备用 - 使用 metals.dev 免费 API
    if gold_price is None:
        metals_data = http_get_json("https://api.metals.dev/v1/latest?api_key=demo&currency=USD&unit=toz")
        if metals_data and "metals" in metals_data:
            gold_price = metals_data["metals"].get("gold")
            silver_price = metals_data["metals"].get("silver")

    if gold_price is None:
        gold_price = 2900
        gold_change = None
    if silver_price is None:
        silver_price = round(gold_price / 90, 2)
        silver_change = None

    rate_data = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")
    cny_rate = rate_data["rates"].get("CNY", 7.25) if rate_data else 7.25

    lines = []
    lines.append("🥇 贵金属行情")
    lines.append("")
    lines.append(f"  🥇 黄金 (XAU/USD)")
    lines.append(f"     价格: ${gold_price:,.2f}/oz / ¥{gold_price*cny_rate:,.0f}/oz")
    if gold_change is not None:
        arrow = "🟢" if gold_change >= 0 else "🔴"
        lines.append(f"     24h: {arrow} {fmt_pct(gold_change)}")
    lines.append("")
    lines.append(f"  🥈 白银 (XAG/USD)")
    lines.append(f"     价格: ${silver_price:,.2f}/oz / ¥{silver_price*cny_rate:,.0f}/oz")
    if silver_change is not None:
        arrow = "🟢" if silver_change >= 0 else "🔴"
        lines.append(f"     24h: {arrow} {fmt_pct(silver_change)}")
    lines.append("")
    lines.append(f"  金银比: {gold_price/silver_price:.1f}")
    lines.append("")

    return "\n".join(lines)


# ── 3. 财联社快讯 ──

def fetch_cls_news():
    """获取财联社快讯"""
    print("📡 获取财联社快讯...", file=sys.stderr)

    lines = []
    lines.append("📰 财联社快讯")
    lines.append("")

    # CLS 公开 API - telegraphList 端点
    url = "https://www.cls.cn/nodeapi/telegraphList?app=CailianpressWeb&os=web&sv=8.4.6&rn=20"
    data = http_get_json(url)

    if not data:
        # 备用: v1 roll list
        url2 = "https://www.cls.cn/v1/roll/get_roll_list?app=CailianpressWeb&os=web&sv=8.4.6&category=&rn=20"
        data = http_get_json(url2)

    news_items = []
    if data:
        # 尝试解析不同格式
        items = []
        if isinstance(data, dict):
            items = data.get("data", {}).get("roll_data", [])
            if not items:
                items = data.get("data", {}).get("telegraphs", [])
            if not items:
                items = data.get("data", [])

        for item in items[:15]:
            title = item.get("title", "") or item.get("brief", "") or ""
            content = item.get("content", "") or item.get("descSummary", "") or ""
            ts = item.get("ctime", 0) or item.get("modified_time", 0)

            # 清理 HTML
            import re
            content = re.sub(r'<[^>]+>', '', content)
            title = re.sub(r'<[^>]+>', '', title)

            text = title if title else content[:100]
            if text:
                if ts:
                    try:
                        t = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
                        time_str = t.strftime("%H:%M")
                    except Exception:
                        time_str = ""
                else:
                    time_str = ""

                prefix = f"[{time_str}] " if time_str else ""
                news_items.append(f"  • {prefix}{text[:120]}")

    if news_items:
        lines.extend(news_items)
    else:
        lines.append("  ⚠️ 财联社数据暂时无法获取，请稍后重试")

    lines.append("")
    return "\n".join(lines)


# ── 4. AI 新闻（轻量版，用于早晚报） ──

def fetch_ai_news_lite():
    """轻量 AI 新闻采集 - 抓取几个关键源"""
    print("📡 获取 AI 新闻...", file=sys.stderr)

    lines = []
    lines.append("🤖 AI 领域动态")
    lines.append("")

    news = []

    # 1. TechCrunch AI
    try:
        tc_url = "https://techcrunch.com/category/artificial-intelligence/feed/"
        req = urllib.request.Request(tc_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        import re
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', content)
        if not titles:
            titles = re.findall(r'<title>(.*?)</title>', content)
        for t in titles[1:6]:  # skip feed title
            t = t.strip()
            if t and "TechCrunch" not in t:
                news.append(f"  • [TC] {t}")
    except Exception as e:
        print(f"  [WARN] TechCrunch fetch failed: {e}", file=sys.stderr)

    # 2. The Verge AI
    try:
        verge_url = "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
        req = urllib.request.Request(verge_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        import re
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', content)
        if not titles:
            titles = re.findall(r'<title>(.*?)</title>', content)
        for t in titles[1:6]:
            t = t.strip()
            if t and "Verge" not in t:
                news.append(f"  • [Verge] {t}")
    except Exception as e:
        print(f"  [WARN] Verge fetch failed: {e}", file=sys.stderr)

    # 3. Hacker News AI (search)
    try:
        hn_url = "https://hn.algolia.com/api/v1/search?query=AI+LLM&tags=story&hitsPerPage=5"
        hn_data = http_get_json(hn_url)
        if hn_data and "hits" in hn_data:
            for hit in hn_data["hits"][:5]:
                title = hit.get("title", "")
                points = hit.get("points", 0)
                if title and points > 20:
                    news.append(f"  • [HN {points}⬆] {title}")
    except Exception as e:
        print(f"  [WARN] HN fetch failed: {e}", file=sys.stderr)

    if news:
        lines.extend(news[:12])
    else:
        lines.append("  ⚠️ AI 新闻暂时无法获取")

    lines.append("")
    return "\n".join(lines)


# ── 主函数 ──

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    mode = mode.lower().strip()

    now = datetime.now(timezone(timedelta(hours=8)))
    header = f"📋 超级大脑增强版新闻采集\n⏰ {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n{'='*45}\n"

    sections = []

    if mode in ("all", "crypto"):
        sections.append(fetch_crypto_prices())

    if mode in ("all", "metals"):
        sections.append(fetch_metals_prices())

    if mode in ("all", "cls"):
        sections.append(fetch_cls_news())

    if mode in ("all", "ai"):
        sections.append(fetch_ai_news_lite())

    output = header + "\n".join(sections)
    print(output)
    return output


if __name__ == "__main__":
    main()
