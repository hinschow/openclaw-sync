"""
v14: 新闻驱动风控模块 - News-Driven Risk Control
扫描持仓相关的实时新闻，发现重大事件时主动触发调仓/平仓。

核心逻辑：
1. 从持仓标题提取关键词
2. 用 web_fetch 搜索相关新闻
3. 判断新闻对持仓的影响方向（利好/利空）
4. 生成风控建议（平仓/加仓/观望）
"""
import re
from datetime import datetime, timezone

# 持仓关键词 → 搜索词映射
MARKET_SEARCH_TERMS = {
    "iran": ["US strikes Iran", "Iran nuclear", "Iran attack"],
    "khamenei": ["Khamenei", "Iran supreme leader"],
    "fed": ["Federal Reserve interest rate", "Fed rate decision"],
    "bitcoin": ["Bitcoin price", "BTC crash", "BTC rally"],
    "trump": ["Trump policy", "Trump executive order"],
    "russia_ukraine": ["Russia Ukraine ceasefire", "Ukraine war"],
    "netanyahu": ["Netanyahu Israel", "Israel government"],
    "starmer": ["Starmer UK", "UK prime minister"],
    "claude": ["Anthropic Claude", "Claude AI release"],
    "warsh": ["Kevin Warsh Fed", "Fed chair nomination"],
    "oscar": ["Oscar awards", "Academy Awards winners"],
    "musk": ["Elon Musk tweets", "Musk X posts"],
    "venezuela": ["Venezuela Machado", "Venezuela politics"],
}

def extract_search_terms(market_title):
    """从市场标题提取搜索关键词"""
    title_lower = market_title.lower()
    terms = []
    for key, searches in MARKET_SEARCH_TERMS.items():
        if key in title_lower:
            terms.extend(searches)
    # 如果没匹配到预设，提取标题核心词
    if not terms:
        # 去掉常见词，取前3个有意义的词
        stop_words = {'will', 'the', 'be', 'by', 'in', 'of', 'a', 'to', 'or', 'and', 'is', 'on', 'at', 'for', 'from'}
        words = [w for w in re.findall(r'[a-zA-Z]+', market_title) if w.lower() not in stop_words and len(w) > 2]
        if words:
            terms.append(' '.join(words[:4]))
    return terms

def build_news_check_prompt(positions):
    """生成新闻检查的 prompt，供 agent 执行"""
    if not positions:
        return None
    
    lines = ["检查以下持仓相关的最新新闻动态：\n"]
    seen_terms = set()
    
    for pos in positions:
        title = pos.get("market", "")
        terms = extract_search_terms(title)
        outcome = pos.get("outcome", "")
        size = pos.get("size", 0)
        
        for term in terms:
            if term not in seen_terms:
                seen_terms.add(term)
                lines.append(f"- 搜索: \"{term}\" (持仓: {outcome} ${size} on '{title[:50]}')")
    
    if len(seen_terms) == 0:
        return None
        
    lines.append("\n对每条相关新闻，判断：")
    lines.append("1. 是否对持仓方向有重大影响（利好/利空/中性）")
    lines.append("2. 如果利空且影响重大，建议立即平仓")
    lines.append("3. 如果利好且确定性高，可以考虑加仓")
    lines.append("\n输出格式：")
    lines.append("🔴 紧急平仓: [市场名] - [原因]")
    lines.append("🟡 关注: [市场名] - [原因]")
    lines.append("🟢 利好: [市场名] - [原因]")
    
    return '\n'.join(lines)

def get_position_news_keywords(positions):
    """返回所有持仓的去重搜索词列表"""
    all_terms = []
    seen = set()
    for pos in positions:
        terms = extract_search_terms(pos.get("market", ""))
        for t in terms:
            if t not in seen:
                seen.add(t)
                all_terms.append(t)
    return all_terms
