"""
模块3: 事件驱动过滤
- 开仓前搜索市场标题相关新闻
- 用 Google News RSS 获取新闻标题
- 简单关键词情绪分析
- 调整仓位大小或跳过
"""
import re
import time
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

DELAY = 0.3

# 正面关键词
POSITIVE_KEYWORDS = [
    "approve", "approved", "pass", "passed", "win", "wins", "won",
    "surge", "surges", "rally", "boost", "gain", "gains", "rise",
    "support", "supports", "agree", "agreement", "deal", "success",
    "confirm", "confirmed", "launch", "launches", "sign", "signed",
    "positive", "optimistic", "bullish", "progress", "advance",
    "victory", "breakthrough", "record", "high", "strong",
]

# 负面关键词
NEGATIVE_KEYWORDS = [
    "reject", "rejected", "fail", "fails", "failed", "lose", "lost",
    "crash", "crashes", "drop", "drops", "decline", "fall", "falls",
    "oppose", "opposes", "block", "blocked", "veto", "cancel",
    "negative", "pessimistic", "bearish", "risk", "threat",
    "scandal", "controversy", "crisis", "collapse", "defeat",
    "delay", "delayed", "suspend", "suspended", "ban", "banned",
    "withdraw", "withdrawn", "unlikely", "doubt", "concerns",
]


def log(msg):
    print(msg, flush=True)


def fetch_google_news_rss(query, max_results=10):
    """通过 Google News RSS 获取新闻标题"""
    try:
        encoded_q = quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en&gl=US&ceid=US:en"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)"
        }
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()

        root = ET.fromstring(r.content)
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text)
            if len(titles) >= max_results:
                break

        time.sleep(DELAY)
        return titles
    except Exception as e:
        log(f"  [event_filter] RSS fetch error for '{query[:30]}': {e}")
        return []


def extract_search_query(market_title):
    """从市场标题提取搜索关键词"""
    # 去掉常见的问句格式
    q = market_title
    for prefix in ["Will ", "will ", "Is ", "is ", "Does ", "does ",
                    "Can ", "can ", "Has ", "has ", "Are ", "are "]:
        if q.startswith(prefix):
            q = q[len(prefix):]
            break

    # 去掉尾部问号和常见后缀
    q = q.rstrip("?").strip()
    for suffix in [" by end of year", " this year", " in 2025", " in 2026",
                   " before ", " by ", " on or before"]:
        idx = q.lower().find(suffix)
        if idx > 10:
            q = q[:idx]

    # 限制长度
    words = q.split()
    if len(words) > 8:
        q = " ".join(words[:8])

    return q


def analyze_sentiment(titles, market_title):
    """简单关键词情绪分析"""
    if not titles:
        return "neutral", 0.0, []

    positive_count = 0
    negative_count = 0
    matched_titles = []

    market_lower = market_title.lower()

    for title in titles:
        title_lower = title.lower()
        pos_matches = sum(1 for kw in POSITIVE_KEYWORDS if kw in title_lower)
        neg_matches = sum(1 for kw in NEGATIVE_KEYWORDS if kw in title_lower)

        if pos_matches > 0 or neg_matches > 0:
            matched_titles.append({
                "title": title[:80],
                "positive": pos_matches,
                "negative": neg_matches,
            })

        positive_count += pos_matches
        negative_count += neg_matches

    total = positive_count + negative_count
    if total == 0:
        return "neutral", 0.0, matched_titles

    # 情绪分数: -1.0 (极负面) 到 +1.0 (极正面)
    score = (positive_count - negative_count) / total

    if score > 0.2:
        sentiment = "positive"
    elif score < -0.2:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    return sentiment, round(score, 3), matched_titles


def filter_signal(market_title, outcome="Yes"):
    """
    对单个市场信号做事件过滤
    返回: (action, multiplier, details)
    - action: "normal" | "boost" | "reduce" | "skip"
    - multiplier: 仓位倍数调整 (0.0 = 跳过, 0.5 = 减半, 1.0 = 正常, 1.5 = 加大)
    - details: 分析详情
    """
    query = extract_search_query(market_title)
    if not query or len(query) < 5:
        return "normal", 1.0, {"reason": "query_too_short"}

    titles = fetch_google_news_rss(query)
    if not titles:
        return "normal", 1.0, {"reason": "no_news_found"}

    sentiment, score, matched = analyze_sentiment(titles, market_title)

    details = {
        "query": query,
        "news_count": len(titles),
        "sentiment": sentiment,
        "score": score,
        "matched_count": len(matched),
        "sample_titles": [t["title"] for t in matched[:3]],
    }

    # 买Yes的情况
    if outcome.lower() == "yes":
        if sentiment == "negative" and score < -0.3:
            return "skip", 0.0, details
        elif sentiment == "negative":
            return "reduce", 0.5, details
        elif sentiment == "positive" and score > 0.3:
            return "boost", 1.5, details
        elif sentiment == "positive":
            return "normal", 1.2, details
    # 买No的情况
    elif outcome.lower() == "no":
        if sentiment == "positive" and score > 0.3:
            return "skip", 0.0, details
        elif sentiment == "positive":
            return "reduce", 0.5, details
        elif sentiment == "negative" and score < -0.3:
            return "boost", 1.5, details
        elif sentiment == "negative":
            return "normal", 1.2, details

    return "normal", 1.0, details


def batch_filter_signals(signals, max_filter=10):
    """
    批量过滤信号
    返回: (filtered_signals, filter_stats)
    - filtered_signals: 带 event_filter 信息的信号列表
    - filter_stats: 统计信息
    """
    log(f"  [event_filter] 开始事件过滤 ({min(len(signals), max_filter)}/{len(signals)} 个信号)...")

    filtered = []
    skipped = 0
    boosted = 0
    reduced = 0
    normal = 0

    for i, sig in enumerate(signals):
        if i >= max_filter:
            # 超过限制的信号直接通过
            sig["event_filter"] = {"action": "normal", "multiplier": 1.0}
            filtered.append(sig)
            normal += 1
            continue

        title = sig.get("title", "")
        outcome = sig.get("outcome", "Yes")

        try:
            action, multiplier, details = filter_signal(title, outcome)
        except Exception as e:
            log(f"    [event_filter] 过滤失败: {title[:30]}... - {e}")
            action, multiplier, details = "normal", 1.0, {"error": str(e)}

        sig["event_filter"] = {
            "action": action,
            "multiplier": multiplier,
            "details": details,
        }

        if action == "skip":
            skipped += 1
            log(f"    ❌ 跳过: {title[:40]}... (情绪: {details.get('sentiment', '?')})")
        elif action == "boost":
            boosted += 1
            filtered.append(sig)
        elif action == "reduce":
            reduced += 1
            filtered.append(sig)
        else:
            normal += 1
            filtered.append(sig)

    stats = {
        "total_checked": min(len(signals), max_filter),
        "skipped": skipped,
        "boosted": boosted,
        "reduced": reduced,
        "normal": normal + max(0, len(signals) - max_filter),
    }

    log(f"  [event_filter] 完成: {skipped}个跳过, {boosted}个加强, {reduced}个减弱")
    return filtered, stats
