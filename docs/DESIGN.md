# 信号灯 · 技术设计说明书

> 版本: v2.0
> 日期: 2026-03-27
> 简称: 信号灯

---

## 一、设计目标与原则

### 1.1 核心目标

为A股散户提供一套**规则化、自动化、可解释**的量化交易辅助工具。

### 1.2 设计原则

```
规则优先于AI
自动化优先于手动
可解释优先于黑盒
简单优先于复杂
```

### 1.3 不做什么

- 不做基本面分析（财报、估值）
- 不做高频交易
- 不做跨市场套利
- AI不参与交易决策

---

## 二、系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      用户交互层                           │
│  CLI (main.py)  │  Web (FastAPI)  │  定时任务 (cron)  │
└──────────────────┬──────────────────┬───────────────────┘
                   │                  │
┌──────────────────▼──────────────────▼───────────────────┐
│                    核心调度层 Pipeline                    │
│  股票池管理 → 数据获取 → 信号计算 → 风控检查 → 决策执行   │
└──────────────────┬──────────────────┬───────────────────┘
                   │                  │
    ┌──────────────▼──────┐    ┌─────▼─────────────────┐
    │    交易执行层        │    │    AI增强层（异步）     │
    │  pre_check.py       │    │  llm_analyzer.py      │
    │  executor.py        │    │  交易后触发，不阻塞    │
    │  risk_control.py   │    │                       │
    └────────────────────┘    └───────────────────────┘
```

### 2.2 模块依赖关系

```
config.py
  ├── models/watchlist.py      # 股票池模型
  ├── models/position.py      # 持仓模型
  ├── models/signal.py        # 信号模型
  ├── models/trade.py         # 交易记录模型
  │
  ├── data_provider/txstock.py
  │     └── 腾讯财经API（历史K线+实时行情）
  │
  ├── indicators/
  │     ├── ma.py             # 均线系统
  │     ├── macd.py           # MACD
  │     ├── rsi.py            # RSI
  │     ├── atr.py            # ATR
  │     ├── chip.py           # 筹码分布
  │     └── signal_counter.py # 10+6信号计数（核心）
  │
  ├── strategy/
  │     └── market_filter.py # 大盘状态判断
  │
  ├── trading/
  │     ├── pre_check.py      # 交易前风控检查
  │     ├── executor.py        # 模拟撮合执行
  │     └── risk_control.py  # 运行时风控
  │
  ├── monitor/
  │     ├── scanner.py       # 批量扫描器
  │     ├── watcher.py        # 监控器（定时）
  │     └── alerter.py        # 预警器
  │
  └── notification/
        ├── feishu.py         # 飞书推送
        ├── wechat.py         # 企业微信推送
        └── llm_analyzer.py   # 大模型分析（AI增强）
```

---

## 三、数据模型

### 3.1 信号模型（models/signal.py）

```python
@dataclass
class RealTimeSignal:
    code: str               # 股票代码
    name: str              # 股票名称
    price: float          # 当前价
    change_pct: float     # 涨跌幅%

    # 均线
    ma5: float; ma10: float; ma20: float; ma60: float
    bias_ma5: float       # 乖离率%

    # MACD
    macd_dif: float; macd_dea: float; macd_bar: float

    # RSI
    rsi_6: float

    # ATR
    atr: float

    # 成交量
    volume: int; volume_ma5: float; volume_ratio: float

    # 趋势
    trend_status: TrendStatus  # STRONG_BULL/BULL/WEAK_BULL/CONSOLIDATION/WEAK_BEAR/BEAR/STRONG_BEAR

    # 信号
    buy_signals: List[Signal]   # 10个买入信号
    sell_signals: List[Signal]  # 6个卖出信号
    buy_count: int; sell_count: int

    # 风控
    atr_stop_loss: float; take_profit_price: float

    # 决策
    decision: Decision  # BUY/HOLD/SELL/WATCH/STOP_LOSS/TAKE_PROFIT
    position_ratio: float  # 建议仓位 0.0-1.0

    # 大盘
    market_status: MarketStatus  # STRONG/WEAK/CONSOLIDATE
    market_change_pct: float

@dataclass
class Signal:
    name: str        # 信号名称
    triggered: bool   # 是否触发
    reason: str = "" # 触发原因
```

### 3.2 持仓模型（models/position.py）

```python
@dataclass
class Position:
    id: str
    code: str; name: str

    # 建仓
    buy_date: str; buy_price: float
    quantity: int    # 手数
    cost: float      # 成本（不含手续费）

    # 当前
    current_price: float; unrealized_pnl: float; pnl_pct: float

    # 风控
    stop_loss: float; take_profit: float; trailing_stop: float

    # 信号追踪
    latest_buy_signals: int; latest_sell_signals: int

    # 状态
    status: str  # open/closed/stopped
    closed_at: str; closed_reason: str
```

### 3.3 交易记录模型（models/trade.py）

```python
@dataclass
class TradeRecord:
    id: str; code: str; name: str
    action: str          # BUY/SELL/STOP_LOSS/TAKE_PROFIT
    price: float; quantity: int; amount: float
    commission: float; stamp_tax: float
    buy_signals: int; sell_signals: int
    atr: float; stop_loss: float
    position_id: str
    pre_check_passed: bool
    created_at: str; trade_date: str
```

### 3.4 股票池模型（models/watchlist.py）

```python
@dataclass
class StockEntry:
    code: str; name: str
    added_at: str
    enabled: bool  # 是否启用监控

@dataclass
class WatchlistSettings:
    auto_trade: bool = False
    notify_only: bool = True
    max_positions: int = 3
    total_capital: float = 100000.0
```

---

## 四、10+6信号系统

### 4.1 十大买入信号（indicators/signal_counter.py）

| # | 信号名称 | 触发条件 | 代码位置 |
|---|---------|---------|---------|
| 1 | MA多头排列 | MA5>MA10>MA20 | `ma.py:check_ma_alignment()` |
| 2 | 价格回踩MA5 | 收盘价≤MA5×1.01 且≥MA5×0.98 | `ma.py:check_price_ma_support()` |
| 3 | 价格回踩MA10 | 收盘价≤MA10×1.01 且≥MA10×0.97 | `ma.py:check_price_ma_support()` |
| 4 | MACD金叉 | DIF上穿DEA | `macd.py:check_macd_signals()` |
| 5 | MACD零轴上方 | DIF>0 且 DEA>0 | `macd.py:check_macd_signals()` |
| 6 | RSI超卖反弹 | RSI(6)<30 且拐头向上 | `rsi.py:check_rsi_signals()` |
| 7 | 缩量回调 | 量比<0.7 且 bias_ma5>0 | `signal_counter.py` |
| 8 | 乖离率修复 | bias_MA5在2-5%区间回调中 | `ma.py:calc_bias()` |
| 9 | 筹码集中 | 换手率<5% | `signal_counter.py` |
| 10 | 大盘配合 | 上证MA5>MA10 多头 | `market_filter.py` |

### 4.2 六大卖出信号

| # | 信号名称 | 触发条件 |
|---|---------|---------|
| 1 | MA空头排列 | MA5<MA10<MA20 |
| 2 | 乖离率过大 | bias_MA5>5%（追高预警）|
| 3 | MACD死叉 | DIF下穿DEA |
| 4 | RSI超买 | RSI(6)>75 |
| 5 | 放量破位 | 量比>1.8 且跌破MA10 |
| 6 | ATR止损触发 | 当前价<买入价-2×ATR |

### 4.3 信号计数流程

```
history + realtime
       ↓
计算MA/MACD/RSI/ATR/成交量
       ↓
逐一检查10个买入条件 → buy_count
       ↓
逐一检查6个卖出条件 → sell_count
       ↓
apply_decision_matrix()
  BUY≥5 → BUY
  SELL≥3 → SELL
  否则 → WATCH
```

---

## 五、技术指标详解

### 5.1 MA均线系统（indicators/ma.py）

```python
def calc_all_ma(closes: List[float]) -> dict:
    """计算MA5/10/20/60"""
    ma5 = pd.Series(closes).rolling(5).mean().iloc[-1]
    ma10 = pd.Series(closes).rolling(10).mean().iloc[-1]
    ma20 = pd.Series(closes).rolling(20).mean().iloc[-1]
    ma60 = pd.Series(closes).rolling(60).mean().iloc[-1]

def calc_bias(price: float, ma: float) -> float:
    """乖离率 = (当前价 - 均线) / 均线 × 100%"""
    return (price - ma) / ma * 100
```

### 5.2 MACD（indicators/macd.py）

```python
def calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD = 12日EMA - 26日EMA，信号线=9日EMA"""
    dif = EMA(fast) - EMA(slow)
    dea = EMA(dif, 9)
    macd_bar = (dif - dea) * 2
```

### 5.3 RSI（indicators/rsi.py）

```python
def calc_rsi(closes, period=6):
    """RSI = 100 - 100/(1+RS)，RS=平均涨幅/平均跌幅"""
    deltas = pd.Series(closes).diff()
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)
    avg_gain = gains.rolling(6).mean()
    avg_loss = losses.rolling(6).mean()
    rs = avg_gain / avg_loss
    return 100 - 100/(1+rs)
```

### 5.4 ATR（indicators/atr.py）

```python
def calc_atr(highs, lows, closes, period=14):
    """TR = max(高点-低点, abs(高点-昨日收), abs(低点-昨日收))
       ATR = Wilder平滑TR"""
    trs = calc_tr(highs, lows, closes)
    atr = sum(trs[-period:]) / period
    for tr in trs[period:]:
        atr = (atr * (period-1) + tr) / period
    return atr

def calc_atr_stop_loss(buy_price, atr, multiplier=2.0):
    """止损价 = 买入价 - multiplier × ATR"""
    return buy_price - atr * multiplier
```

---

## 六、风控体系

### 6.1 交易前检查（trading/pre_check.py）

```python
class PreTradeChecker:
    def check(self, action, code, price, quantity,
              amount, market_status, market_change_pct) -> CheckResult:
        checks = []
        checks.append(self._check_time_window())        # 1. 时间窗口
        checks.append(self._check_market_status())      # 2. 大盘状态
        checks.append(self._check_market_crash())       # 3. 大盘暴跌
        checks.append(self._check_position_limit())       # 4. 持仓上限
        checks.append(self._check_fund_available())      # 5. 资金充足
        checks.append(self._check_stop_loss_space())     # 6. 止损空间
        checks.append(self._check_same_day_trade())     # 7. 同日限制
        return CheckResult(passed=all(c[1] for c in checks), checks=checks)
```

### 6.2 运行时风控（trading/risk_control.py）

```python
class RiskController:
    def check_risk_level(self) -> str:
        """返回 SAFE/CAUTION/WARNING/DANGER"""

    def should_force_close_all(self, market_change: float) -> bool:
        """大盘暴跌>2% → 全仓强平"""

    def should_force_stop(self, position: Position, current_price: float) -> bool:
        """单股亏损>10% → 强制止损"""

    def can_open_position(self, code: str) -> (bool, str):
        """检查是否可以开仓"""
```

### 6.3 动态止损（trading/executor.py）

```python
def _trigger_llm_analysis(self, trade: TradeRecord):
    """交易后异步触发AI分析，不阻塞交易"""
    thread = threading.Thread(target=_async_llm, daemon=True)
    thread.start()
```

---

## 七、大模型模块（notification/llm_analyzer.py）

### 7.1 支持的Provider

```python
PROVIDER_CONFIG = {
    "deepseek":  {"name": "DeepSeek", "default_model": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1"},
    "zhipu":     {"name": "智谱AI", "default_model": "glm-4-flash",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    "doubao":    {"name": "豆包", "default_model": "doubao-pro",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
    "qwen":      {"name": "通义千问", "default_model": "qwen-plus",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "minimax":   {"name": "MiniMax", "default_model": "MiniMax-Text-01",
                    "base_url": "https://api.minimax.chat/v1"},
    "openai":    {"name": "OpenAI", "default_model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1"},
}
```

### 7.2 调用流程

```
交易执行完成
     ↓
_triggers_llm_analysis(trade)
     ↓
threading.Thread(target=_async_llm)  ← 后台线程，daemon=True
     ↓
async_llm():
    llm.analyze_trade(trade_dict)
     ↓
notifier.send(AI分析报告)  ← 飞书推送给你
```

### 7.3 Prompt设计

```python
def _build_prompt(self, signal: dict, market: dict = None) -> str:
    """构建股票分析Prompt"""
    # 包含：股票信息、技术指标、信号数据、大盘状态
    # 要求：3-5句话，给出形态判断+风险点+操作建议

def analyze_trade(self, trade: dict) -> str:
    """构建交易分析Prompt"""
    # 包含：交易信息、触发信号、原因
    # 要求：分析合理性+风险点+后市建议
```

---

## 八、数据源（data_provider/txstock.py）

### 8.1 数据获取

```python
class TxStock:
    def get_history(code, days=60) -> List[dict]:
        """获取日K线数据
        URL: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
        返回: [{date, open, high, low, close, volume}, ...]
        编码: GBK
        """

    def get_realtime(code) -> dict:
        """获取实时行情
        URL: https://qt.gtimg.cn/q=sz000629
        返回: {name, price, change_pct, turnover_rate, volume, ...}
        编码: GBK
        """
```

### 8.2 数据解析

```python
# 实时行情解析（关键字段）
parts = line.split("~")
name = parts[1]
price = float(parts[3])
prev_close = float(parts[4])
change_pct = (price - prev_close) / prev_close * 100

# K线解析（成交量可能是浮点数）
volume = int(float(item[5]))  # "781267.000" → 781267
```

---

## 九、监控引擎（monitor/）

### 9.1 Scanner（monitor/scanner.py）

```python
class Scanner:
    def scan_watchlist(watchlist) -> List[RealTimeSignal]:
        """批量扫描股票池"""
        for entry in enabled_stocks:
            signal = self._scan_single(code, name, market_status, ...)
            signals.append(signal)

    def get_actionable_signals(signals) -> dict:
        """按决策分类信号"""
        return {"buy": [...], "sell": [...], "hold": [...], "watch": [...]}
```

### 9.2 Watcher（monitor/watcher.py）

```python
class Watcher:
    def start(continuous=False):
        """启动监控"""
        if continuous:
            self._run_continuous()  # 持续扫描
        else:
            self._run_watch_mode()  # 等到14:30-15:00窗口

    def _run_watch_cycle():
        """一次扫描周期"""
        signals = scanner.scan_watchlist()
        actions = scanner.get_actionable_signals(signals)
        _handle_stop_loss(actions["stop_loss"])
        _handle_sell(actions["sell"])
        _handle_buy(actions["buy"])
        _update_positions(signals)
        notifier.send_signal_report(signals)
```

### 9.3 Alerter（monitor/alerter.py）

```python
class Alerter:
    def check_positions(signals) -> List[Alert]:
        """检查持仓异常"""
        # 止损预警：亏损>8%且距止损<2%
        # 止盈预警：达到目标价80%+
        # 信号转弱：买点减少>2或卖点增加

    def check_market(status, change) -> Optional[Alert]:
        """大盘异动预警：涨跌幅>1.5%"""
```

---

## 十、Web API（web/app.py）

### 10.1 FastAPI端点

```
GET  /                      仪表盘HTML
GET  /api/portfolio        账户总览
GET  /api/positions        持仓列表
GET  /api/watchlist        股票池
GET  /api/signals          信号扫描结果
GET  /api/market           大盘状态
GET  /api/settings          当前设置
POST /api/watchlist/add    添加股票
POST /api/watchlist/remove 删除股票
GET  /health               健康检查
```

### 10.2 数据流

```
浏览器请求
    ↓
FastAPI路由
    ↓
调用对应模块（scanner/position_store/...）
    ↓
返回JSON响应
    ↓
前端更新页面
```

---

## 十一、配置体系（config.py）

### 11.1 配置加载

```python
class Config:
    # dataclass字段定义默认值
    llm_provider: str = "deepseek"
    llm_api_key: str = ""

    @classmethod
    def load(cls) -> "Config":
        # 从os.getenv加载，支持.env文件
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "deepseek"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            ...
        )
```

### 11.2 环境变量覆盖优先级

```
代码默认值 < .env文件 < 系统环境变量 < 运行时参数
```

---

## 十二、文件结构

```
astock-signal/
├── main.py                    # CLI入口（745行）
├── config.py                  # 配置管理（153行）
├── DESIGN.md                   # 本文档
├── DEPLOY.md                   # 部署指南
├── requirements.txt
├── Dockerfile
├── .env.example
│
├── models/                     # 数据模型
│   ├── watchlist.py           # 股票池
│   ├── position.py            # 持仓
│   ├── signal.py              # 信号
│   └── trade.py               # 交易记录（SQLite）
│
├── data_provider/
│   └── txstock.py             # 腾讯财经数据源
│
├── indicators/                # 技术指标
│   ├── ma.py                  # 均线
│   ├── macd.py               # MACD
│   ├── rsi.py                 # RSI
│   ├── atr.py                 # ATR
│   ├── chip.py                # 筹码
│   └── signal_counter.py       # 信号计数（核心，385行）
│
├── strategy/
│   └── market_filter.py      # 大盘过滤器
│
├── trading/                   # 交易引擎
│   ├── pre_check.py           # 交易前检查
│   ├── executor.py             # 模拟撮合
│   └── risk_control.py         # 运行时风控
│
├── monitor/                   # 监控引擎
│   ├── scanner.py             # 批量扫描
│   ├── watcher.py             # 定时监控
│   └── alerter.py            # 预警
│
├── notification/               # 通知
│   ├── feishu.py             # 飞书
│   ├── wechat.py             # 企业微信
│   └── llm_analyzer.py        # 大模型分析
│
├── web/
│   ├── app.py                 # FastAPI服务
│   └── templates/index.html   # 仪表盘
│
├── scripts/
│   └── install_cron.sh        # 定时任务安装
│
└── data/                      # 数据目录
    ├── watchlist.json
    ├── positions.json
    └── trades.db
```

---

## 十三、关键算法

### 13.1 ATR动态止损

```python
# 建仓时设置初始止损
stop_loss = buy_price - 2 * atr

# 持仓期间只上移，不下移
new_stop = current_price - 2 * atr
stop_loss = max(new_stop, stop_loss)  # 只上移

# 触发检查
if current_price <= stop_loss:
    execute_stop_loss()
```

### 13.2 仓位计算

```python
def calc_position_ratio(buy_count: int) -> float:
    if buy_count >= 8: return 1.0   # 满仓
    elif buy_count == 7: return 0.8
    elif buy_count == 6: return 0.5
    elif buy_count == 5: return 0.3
    else: return 0.0
```

### 13.3 大盘状态判断

```python
def get_market_status(index_code="sh000001"):
    history = txstock.get_history(index_code, days=25)
    closes = [h["close"] for h in history]
    ma5 = mean(closes[-5:])
    ma10 = mean(closes[-10:])
    ma20 = mean(closes[-20:])
    current = closes[-1]
    change_pct = (current - prev_close) / prev_close * 100

    if current > ma5 and current > ma10 and current > ma20 and change_pct > -1:
        return STRONG
    elif current < ma5 and current < ma10:
        return WEAK
    else:
        return CONSOLIDATE
```

---

## 十四、测试清单

```bash
# 语法检查
python3 -c "import ast; [ast.parse(open(f).read()) for f in glob('**/*.py')]"

# 导入检查
python3 -c "import sys; [__import__(m) for m in modules]"

# 功能测试
python main.py pool list
python main.py pool add 000629
python main.py analyze 000629
python main.py analyze --pool
python main.py position
python main.py settings
python main.py llm status

# Web测试
curl http://localhost:8080/health
curl http://localhost:8080/api/signals
```

---

## 十五、版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v1.0 | 2026-03-27 | 初始版本：10+6信号系统、基本交易框架 |
| v2.0 | 2026-03-27 | 新增LLM大模型支持、AI增强报告、6大Provider |

---

*技术设计说明书 v2.0 | 2026-03-27*