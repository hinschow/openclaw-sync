# 配置文件

# API 端点
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon

# 交易参数
TOP_TRADERS_COUNT = 50
MIN_TRADE_AMOUNT = 1.0      # 最小交易金额 (USDC)
MAX_TRADE_AMOUNT = 10.0     # 最大交易金额 (USDC)
FOLLOW_THRESHOLD = 0.6      # 跟单信号阈值（60%以上头部交易员同方向才跟）
MIN_LIQUIDITY = 5000        # 最低流动性要求

# 持仓管理参数 (v12: 减少持仓数，集中仓位)
MAX_POSITIONS = 15           # v12: 总最大持仓数 40→15（集中精力）
MAX_LONG_POSITIONS = 5       # v12: 长线最多5笔
MAX_SHORT_POSITIONS = 10     # v12: 短线最多10笔
MIN_POSITION_SIZE = 30       # 最小单笔仓位 $30
MAX_POSITION_SIZE = 50       # 最大单笔仓位 $50
MIN_TRADER_CONSENSUS = 3     # 默认最少精英交易员共识人数
POSITION_SIZE_PCT = 0.15     # 基础仓位 = balance * 15%
STRONG_SIGNAL_PCT = 0.20     # 强信号仓位 20% (≥5人共识)
STRONG_SIGNAL_THRESHOLD = 5  # 强信号共识人数阈值

# 事件过滤 - 排除类型
EXCLUDED_CATEGORIES = [
    "sports", "esports", "football", "soccer", "basketball",
    "tennis", "cricket", "boxing", "mma",
    "nfl", "nba", "mlb", "nhl", "lec", "blast", "epl",
]

# 排除短线盘关键词
EXCLUDED_TITLE_PATTERNS = [
    "Up or Down", "O/U", "win on 2026-",
]

# v8: 扩展体育/电竞排除关键词
EXCLUDED_KEYWORDS_V8 = [
    "Tennis", "Championships", "Dubai", "ATP", "WTA", "Grand Slam",
    "Wimbledon", "Roland Garros", "Australian Open", "US Open",
    "Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
    "Champions League", "Europa League", "World Cup", "Olympics", "medal",
    "Rugby", "Cricket", "F1", "Formula", "NASCAR", "Golf", "PGA", "Tour",
    "boxing", "UFC", "MMA", "WWE", "NHL", "MLB", "NFL", "NBA",
    "Super Bowl", "Stanley Cup", "World Series", "March Madness",
    "Dota", "CSGO", "Valorant", "League of Legends", "Overwatch",
]

# 短线交易参数 (v11: 短线优先)
SHORT_TERM_POSITION_SIZE_MIN = 30   # v12: 短线最小$30（降低风险敞口）
SHORT_TERM_POSITION_SIZE_MAX = 40   # v12: 短线最大$40
LONG_TERM_POSITION_SIZE_MIN = 30    # 保持
LONG_TERM_POSITION_SIZE_MAX = 50    # 保持
SHORT_TERM_BUDGET_PCT = 0.70        # v11: 短线 70%
LONG_TERM_BUDGET_PCT = 0.20         # v11: 长线 20%
SAFETY_BUFFER_PCT = 0.10            # v11: 安全垫 10%
TARGET_UTILIZATION = 0.90           # v11: 目标资金利用率 90%

# v12: 保守模式（不再激进加仓）
AGGRESSIVE_CASH_THRESHOLD = 0.50    # v12: 余额>50%才考虑加仓
AGGRESSIVE_SCAN_THRESHOLD = 0.60    # v12: 余额>60%才主动扫描
AGGRESSIVE_SHORT_SIZE_MAX = 40      # v12: 短线单笔上限$40
CATALYST_MIN_CONSENSUS = 2          # v12: 催化剂至少2人共识
REVERSION_MIN_CHANGE = 0.10         # v12: 回归波动门槛 5%→10%
EXPIRY_MIN_PRICE = 0.80             # v12: 到期收割门槛 0.70→0.80
EXPIRY_WINDOW_DAYS = 7              # v12: 到期窗口 14天→7天
PROACTIVE_SCAN_LIMIT = 30           # v12: 主动扫描市场数 50→30
PROACTIVE_PROBE_SIZE = 20           # v12: 试探仓位 $30→$20（降低风险）
VOLUME_SPIKE_THRESHOLD = 3.0        # v12: 24h交易量放大>300%才算催化剂

# v12: 每日亏损熔断
DAILY_LOSS_LIMIT = 50.0             # 单日最大亏损 $50
DAILY_LOSS_LIMIT_PCT = 0.05         # 或余额的 5%
MAX_DRAWDOWN_PCT = 0.15             # 最大回撤 15%（从峰值算）

# 内存优化参数
MAX_TRACKED_TRADERS = 30     # 最多追踪30个交易员（按PnL排序取前30）
TRADES_PER_TRADER = 20       # 每个交易员最近20条交易
TRADER_BATCH_SIZE = 5        # 交易员采集每批5个
TRADER_API_TIMEOUT = 8       # 单个交易员请求超时8秒

# 模拟模式
SIMULATION_MODE = True

# 账户（后续填入）
PRIVATE_KEY = ""
FUNDER_ADDRESS = ""
