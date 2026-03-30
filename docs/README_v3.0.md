# A股信号灯 v3.0 说明文档

> **版本**: v3.0
> **更新**: 2026-03-30
> **定位**: A股量化交易辅助工具（模拟交易/回测/预警）

---

## 一、框架概览

```
┌─────────────────────────────────────────────────────────┐
│                      main.py                              │
│                   （入口 / CLI）                          │
└─────────────────────┬───────────────────────────────────┘
                      │
          ┌───────────┼────────────┐
          ▼           ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  monitor  │ │ strategy  │ │ trading   │
    │  监控/预警 │ │ 市场过滤  │ │ 交易执行  │
    └─────┬────┘ └─────┬────┘ └─────┬────┘
          │             │             │
    ┌─────┴─────────────┴─────────────┴─────┐
    │           indicators 指标计算           │
    │  (signal_counter / ma / macd / rsi / atr) │
    └──────────────────┬──────────────────┘
                        │
    ┌───────────────────┼───────────────────┐
    ▼                   ▼                    ▼
┌─────────┐      ┌───────────┐      ┌──────────┐
│   Web   │      │ notification│      │  models  │
│ FastAPI  │      │ 飞书/企微/AI │      │ 持仓/交易│
│ app.py   │      │ 通知        │      │ 数据模型  │
└─────────┘      └───────────┘      └──────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ txstock  │ │ eastmoney │ │ selector  │
        │ 腾讯财经  │ │ 东方财富   │ │ 数据源切换 │
        └──────────┘ └──────────┘ └──────────┘
```

---

## 二、目录结构

```
astock-signal/
├── main.py                      # CLI 入口
├── config.py                    # 配置管理
├── requirements.txt            # Python 依赖
├── .env                        # 用户配置（API Key、Webhook等）
│
├── data/                       # 运行时数据
│   ├── watchlist.json          # 自选股列表
│   ├── positions.json           # 持仓记录
│   └── trades.db               # 交易历史（SQLite）
│
├── models/                     # 数据模型
│   ├── signal.py              # 信号/决策/市场状态 数据类
│   ├── position.py            # 持仓数据类 + 持久化
│   ├── trade.py               # 交易记录数据类
│   ├── account.py             # 账户模型（回测用）
│   └── watchlist.py           # 自选股管理
│
├── indicators/                 # 技术指标计算
│   ├── ma.py                  # 均线（MA5/10/20/60/120/250）
│   ├── macd.py               # MACD（DIF/DEA）
│   ├── rsi.py                # RSI（6/12/24日）
│   ├── atr.py                 # ATR 真实波幅
│   └── signal_counter.py      # ★ 核心：10+6 信号系统
│
├── data_provider/              # 数据源层（v3.0 新增）
│   ├── txstock.py             # 腾讯财经（主数据源）
│   ├── eastmoney.py           # 东方财富（备用数据源）
│   ├── data_selector.py       # 数据源自动切换
│   └── data_clean.py          # 数据清洗
│
├── strategy/                   # 策略层
│   └── market_filter.py        # 大盘状态判断（v3.0 多指数）
│
├── trading/                    # 交易执行层
│   ├── executor.py            # 模拟撮合（买入/卖出/止损）
│   ├── pre_check.py          # 交易前风控检查（v3.0 增强）
│   ├── enhanced_filters.py    # ★ v3.0 尾盘三层强化过滤
│   └── cost_calculator.py     # ★ v3.0 真实成本计算
│
├── backtest/                   # ★ v3.0 回测模块
│   ├── engine.py              # 回测引擎
│   └── __init__.py
│
├── monitor/                    # 监控预警层
│   ├── scanner.py             # 股票扫描器
│   ├── watcher.py             # 持仓盯盘
│   ├── alerter.py            # 基础预警
│   ├── ai_alerter.py         # ★ v3.0 AI增强预警
│   └── reporter.py            # ★ v3.0 交易复盘报告
│
├── notification/               # 通知层
│   ├── feishu.py             # 飞书 Webhook 推送
│   ├── wechat.py             # 企业微信 Webhook
│   └── llm_analyzer.py       # 大模型分析（DeepSeek等）
│
├── web/                       # Web 服务层
│   ├── app.py                # FastAPI 应用（含回测API）
│   └── templates/            # 前端模板
│
└── tests/                    # 单元测试
    ├── test_eastmoney.py
    ├── test_data_selector.py
    ├── test_data_clean.py
    ├── test_enhanced_filters.py
    ├── test_backtest_engine.py
    ├── test_ai_alerter.py
    └── test_reporter.py
```

---

## 三、各文件详细说明

### 3.1 入口与配置

#### `main.py`
CLI 入口，支持以下命令：
```bash
python main.py scan          # 扫描自选股信号
python main.py watch          # 启动盯盘模式
python main.py backtest 000629 --days 60  # 回测
python main.py report        # 发送复盘报告
python main.py test-alert    # 测试预警
```

#### `config.py`
全局配置管理，读取 `.env` 文件。**所有配置项必须有默认值**，不在 `.env` 中时使用默认值。

#### `.env`
用户私有配置（**不在 Git 中**），需要手动创建或追加：
```bash
# ===== 数据源（v3.0新增）=====
DATA_PROVIDER=auto                    # auto / txstock / eastmoney
DATA_RETRY_COUNT=3                    # 连续失败次数，触发切换
DATA_TIMEOUT=5                         # 请求超时（秒）

# ===== AI预警（v3.0新增）=====
AI_LOSS_THRESHOLD=8                   # 亏损预警阈值（%）
AI_ALERT_ENABLED=true                 # AI预警开关

# ===== 飞书 ======
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# ===== 大模型（AI分析用）=====
LLM_PROVIDER=deepseek                 # deepseek / zhipu / doubao / minimax
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

---

### 3.2 数据层

#### `data_provider/txstock.py`（主）
腾讯财经数据源，提供：
- `get_realtime(code)` → 实时行情
- `get_history(code, days)` → 历史K线（前复权日K）
- `get_name(code)` → 股票名称
- `batch_get_realtime(codes)` → 批量实时

返回格式：
```python
{
    "code": "sz000629", "name": "钒钛股份",
    "price": 3.43, "prev_close": 3.36,
    "open": 3.40, "high": 3.46, "low": 3.37,
    "volume": 1030075, "change_pct": 2.08,
    "turnover_rate": 1.24, "pe": 42.5,
    "source": "腾讯财经"
}
```

#### `data_provider/eastmoney.py`（备）⭐ v3.0新增
东方财富数据源，**接口完全兼容 txstock.py**，可直接互换：
- A股：`efinance` 库
- 指数（上证/深证/创业板/科创50）：东方财富 direct API
- 支持板块指数（如 BK0479 钢铁）K线

#### `data_provider/data_selector.py` ⭐ v3.0新增
数据源自动选择器（单例）：
```python
selector = get_selector()
rt = selector.get_realtime("000629")  # 自动选可用源

# 强制指定
selector.force_source("eastmoney")    # 强制东方财富
selector.force_source("auto")           # 切回自动
status = selector.get_status()         # 查看当前状态
```
**主备逻辑**：腾讯为主，东方财富为备。连续3次失败自动切换。

#### `data_provider/data_clean.py` ⭐ v3.0新增
数据清洗工具：
- `clean_kline_data(records)`：过滤异常值（价格=0、涨幅>20%）、去重、排序、前向填充
- `clean_realtime_data(raw)`：price=0丢弃、超限涨幅修正为0
- `add_derived_fields(records)`：追加MA5/10/20、量比、涨跌幅

---

### 3.3 指标与信号系统

#### `indicators/signal_counter.py` ★ 核心
10+6 信号计数器，是整个系统的决策核心。

**10个买入信号**（需≥5个才考虑买入）：
| # | 信号名 | 触发条件 |
|---|--------|---------|
| 1 | MA多头排列 | MA5 > MA10 > MA20 |
| 2 | 价格回踩MA5 | 现价 ≤ MA5×1.01 |
| 3 | 价格回踩MA10 | 现价 ≤ MA10×1.01 |
| 4 | MACD金叉 | DIF上穿DEA |
| 5 | MACD零轴上方 | DIF>0 且 DEA>0 |
| 6 | RSI超卖反弹 | RSI(6)<30 且拐头向上 |
| 7 | 缩量回调 | 量比 < 0.7 |
| 8 | 乖离率修复 | bias_MA5 在 2~5% 回调中 |
| 9 | 筹码集中 | 换手率 < 5% |
| 10 | 大盘配合 | 上证处于强势 |

**6个卖出信号**（需≥3个就考虑卖出）：
| # | 信号名 | 触发条件 |
|---|--------|---------|
| 1 | MA空头排列 | MA5 < MA10 < MA20 |
| 2 | 乖离率过大 | bias_MA5 > 5% |
| 3 | MACD死叉 | DIF下穿DEA |
| 4 | RSI超买 | RSI(6) > 75 |
| 5 | 放量破位 | 量比>1.8 且 跌破MA10 |
| 6 | ATR止损触发 | 现价 < 买入价 - 2×ATR |

**决策规则**：
- 止损优先（触及ATR止损价）
- 止盈（持有≥5天且盈利≥15%）
- 卖出信号 ≥3 → SELL
- 买入信号 ≥5 → BUY
- 弱势市场须买入信号 ≥7 才考虑

---

### 3.4 策略层

#### `strategy/market_filter.py` ⭐ v3.0升级
大盘状态判断，**v3.0升级为上证+创业板+科创50三指数联合判断**：

| 方法 | 说明 |
|------|------|
| `get_market_status()` | 返回 (MarketStatus, 最大跌幅) |
| `get_multi_index_status()` | 返回三指数详细数据（含MA状态） |

**状态判断**：
- **强势**：上证MA多头 且 双创不弱 且 最大跌幅 ≥-1%
- **弱势**：上证跌破MA10，或 双创之一跌破MA10，或 最大跌幅 >-2%
- **震荡**：其他情况

**暴跌阈值**：取三指数中跌幅最大者，超过 `-2%` 触发强平警告。

---

### 3.5 交易执行层

#### `trading/executor.py`
模拟撮合引擎（非真实下单）：
```python
executor = get_executor()
result = executor.execute_buy(signal=signal, quantity=10, atr=0.05)
```

#### `trading/pre_check.py` ⭐ v3.0增强
交易前**10层风控检查**（新增3层）：

| 层 | 检查项 | 失败后果 |
|----|--------|---------|
| 1 | 交易时间段（14:30-15:00） | 拒绝开仓 |
| 2 | 大盘状态（弱势市场） | 拒绝开仓 |
| 3 | 大盘暴跌（三指数最大跌幅<-2%） | 禁止开仓/强平 |
| 4 | 持仓上限 | 拒绝超过3只 |
| 5 | 资金充足 | 拒绝资金不足 |
| 6 | 止损空间合理性 | 风险>10%警告 |
| 7 | 同日限制 | 今日已交易则拒绝 |
| **8** | **量能承接**（量比0.8-1.5） | **拒绝** ⭐新增 |
| **9** | **板块共振**（MA5多头+涨幅≥0.5%） | **拒绝** ⭐新增 |
| **10** | **个股位置**（站上MA60+回撤≤10%） | **拒绝** ⭐新增 |

#### `trading/enhanced_filters.py` ⭐ v3.0新增
三层尾盘强化过滤，实现细节：
```python
# 量能承接
vol_ratio = 当日成交量 / 20日均量
要求：0.8 ≤ vol_ratio ≤ 1.5

# 板块共振
板块MA5多头排列 AND 板块涨幅 ≥ 0.5%
板块K线直接走东方财富（腾讯不支持BK板块码）

# 个股位置
现价 > MA60 AND (近60日高点 - 现价) / 近60日高点 ≤ 10%
```

#### `trading/cost_calculator.py` ⭐ v3.0新增
真实成本计算：
```python
# 买入成本
佣金 = max(金额 × 0.00025, 5元)      # 万2.5，最低5元
滑点 = 金额 × 0.002                    # ±0.2%

# 卖出成本
佣金 = max(金额 × 0.00025, 5元)
印花税 = 金额 × 0.001                  # 千1（仅卖出）
滑点 = 金额 × 0.002
```

---

### 3.6 回测模块

#### `backtest/engine.py` ⭐ v3.0新增
```python
from backtest.engine import BacktestEngine

engine = BacktestEngine(
    initial_capital=100000,    # 初始资金
    buy_threshold=5,           # 买入信号阈值
    sell_threshold=3,          # 卖出信号阈值
    atr_multiplier=2.0,        # ATR止损倍数
)

result = engine.run("000629", days=60)
```

**输出指标**：
- 总收益率、年化收益率
- 胜率、盈亏比
- 夏普比率（简化版）
- 最大回撤
- 交易明细、每日净值曲线

**核心逻辑**：
1. 跳过前20天（用于指标预热）
2. 按日迭代，每个交易日计算信号
3. ATR追踪止损：止损位只上移不下移
4. 止盈：持有≥5天且盈利≥15%
5. 尾盘14:30后信号触发才交易

#### `models/account.py` ⭐ v3.0新增
回测账户模型，跟踪：
- 总资产 / 可用资金 / 冻结资金 / 市值
- 收益率、最大回撤
- 盈亏统计（总交易/盈利/亏损次数）

---

### 3.7 监控预警层

#### `monitor/ai_alerter.py` ⭐ v3.0新增
AI增强预警，触发条件：

| 触发条件 | 预警等级 | 说明 |
|---------|---------|------|
| 持仓亏损 > 8% | 🚨 DANGER | 紧急止损预警 + AI分析 |
| 持仓亏损 > 5% | ⚠️ WARNING | 亏损关注 |
| 买入信号减少 ≥ 2 | 📉 WARNING | 信号转弱 |
| 卖出信号增加 ≥ 2 | 🔴 WARNING | 出现卖出信号 |
| 大盘最大跌幅 < -2% | 🚨 DANGER | 大盘暴跌预警 |
| 大盘涨幅 > 1.5% | 📈 INFO | 大盘大涨 |

**去重机制**：同类预警5分钟内不重复推送。

#### `monitor/reporter.py` ⭐ v3.0新增
交易复盘报告：
```python
reporter = get_reporter()
reporter.send_daily_report()    # 每日收盘后
reporter.send_weekly_report()  # 每周
```
报告内容：
- 大盘状态（多指数）
- 今日交易明细
- 当前持仓状态（含ATR止损位）
- 近5日信号有效性统计（各信号胜率）
- AI综合建议（异步）

#### `monitor/alerter.py`
基础预警（已有），包含：
- 止损预警（亏损>8%且距止损<2%）
- 止盈预警（达到目标价80%）
- 信号转弱预警

---

### 3.8 通知层

#### `notification/feishu.py`
飞书 Webhook 推送，支持：
- `send(content, msg_type="text/markdown")`
- `send_signal_report()` 信号扫描报告
- `send_position_report()` 持仓日报
- `send_trade_notification()` 交易通知

#### `notification/llm_analyzer.py`
大模型分析，支持 Provider：
| Provider | 模型 | API Key 环境变量 |
|---------|------|-----------------|
| deepseek | deepseek-chat | DEEPSEEK_API_KEY |
| zhipu | glm-4-flash | ZHIPU_API_KEY |
| doubao | doubao-pro | DOUBAO_API_KEY |
| qwen | qwen-plus | QWEN_API_KEY |
| minimax | MiniMax-Text-01 | MINIMAX_API_KEY |
| openai | gpt-4o-mini | OPENAI_API_KEY |

---

### 3.9 Web 服务

#### `web/app.py`
FastAPI 应用，端口默认 8080：
```bash
cd astock-signal && python -m web.app
# 或 uvicorn web.app:app --host 0.0.0.0 --port 8080
```

**API 路由**：
| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 仪表盘 |
| `/api/portfolio` | GET | 账户总览 |
| `/api/positions` | GET | 当前持仓 |
| `/api/watchlist` | GET | 自选股列表 |
| `/api/watchlist/add` | POST | 添加自选股 |
| `/api/watchlist/remove` | POST | 删除自选股 |
| `/api/signals` | GET | 扫描信号 |
| `/api/market` | GET | 大盘状态 |
| `/api/settings` | GET/POST | 设置管理 |
| **`/api/backtest`** | **GET** | **★回测API** |
| `/health` | GET | 健康检查 |

**回测API**：
```
GET /api/backtest?code=000629&days=60&buy_threshold=5&sell_threshold=3

Response:
{
  "success": true,
  "result": {
    "code": "000629",
    "total_return": 0.0,
    "annual_return": 0.0,
    "win_rate": 0.0,
    "sharpe_ratio": 0.0,
    "max_drawdown": 0.0,
    "total_trades": 0,
    "trades": [...],
    "equity_curve": [{"date": "2026-01-01", "capital": 100000}, ...]
  }
}
```

---

## 四、运行机制

### 4.1 正常盯盘流程

```
1. main.py scan
      ↓
2. Scanner 扫描自选股
      ↓
3. 对每只股票：
      ├─ get_realtime()      ← data_selector 自动选源
      ├─ get_history(days=60) ← 同上
      └─ SignalCounter.count_signals()  ← 计算10+6信号
      ↓
4. PreTradeChecker.check(BUY, kline_history)
      ├─ 大盘状态检查          ← market_filter（多指数）
      ├─ 三层强化过滤          ← enhanced_filters
      │    ├─ 量能承接
      │    ├─ 板块共振
      │    └─ 个股位置
      └─ 原有7层风控
      ↓
5a. 若全部通过 → 执行买入 → 更新持仓 → 记录交易
5b. 若有持仓 → 检查止损/止盈/卖出信号 → 执行卖出
      ↓
6. Alerter 检查持仓
      ├─ 亏损预警
      ├─ 信号转弱预警
      └─ 大盘异动预警
      ↓
7. 发送飞书通知（可选）
      ↓
8. 收盘后 Reporter.send_daily_report()
```

### 4.2 数据源自动切换机制

```
正常情况：腾讯财经（主）
    ↓
连续失败3次 → 自动切换东方财富（备）
    ↓
备用连续成功3次 → 可选切回主源
    ↓
用户可强制指定：selector.force_source("txstock/eastmoney/auto")
```

### 4.3 回测流程

```
engine.run("000629", days=60)
      ↓
获取K线数据（data_selector）
      ↓
清洗数据（data_clean）
      ↓
跳过前20天（指标预热）
      ↓
按日迭代：
  ├─ 计算当日信号
  ├─ 检查卖出（止损/止盈/卖出信号）
  ├─ 更新ATR追踪止损
  └─ 检查买入（信号≥5 且 三层强化过滤）
      ↓
记录每日净值
      ↓
最后一天平仓所有持仓
      ↓
计算绩效指标（胜率/夏普/最大回撤）
      ↓
返回 BacktestResult（to_dict 可序列化）
```

---

## 五、参数设置

### 5.1 交易参数（`.env`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TOTAL_CAPITAL` | 100000 | 初始本金（元） |
| `MAX_POSITIONS` | 3 | 最大持仓数 |
| `SINGLE_TRADE_LIMIT` | 20000 | 单笔交易限额（元） |
| `BUY_SIGNAL_THRESHOLD` | 5 | 买入信号阈值 |
| `SELL_SIGNAL_THRESHOLD` | 3 | 卖出信号阈值 |
| `STOP_LOSS_PCT` | 10.0 | 止损线（%） |
| `TAKE_PROFIT_PCT` | 15.0 | 止盈线（%） |
| `ATR_STOP_MULTIPLIER` | 2.0 | ATR止损倍数 |
| `OPEN_WINDOW_START` | 14:30 | 开仓窗口起始 |
| `OPEN_WINDOW_END` | 15:00 | 开仓窗口结束 |
| `MARKET_CRASH_THRESHOLD` | -2.0 | 大盘暴跌阈值（%） |

### 5.2 数据源参数（v3.0新增）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DATA_PROVIDER` | auto | auto / txstock / eastmoney |
| `DATA_RETRY_COUNT` | 3 | 失败次数触发切换 |
| `DATA_TIMEOUT` | 5 | 请求超时（秒） |

### 5.3 尾盘强化过滤阈值（代码内）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `VOLUME_RATIO_MIN/MAX` | 0.8/1.5 | 量比范围 |
| `BLOCK_CHANGE_MIN` | 0.5 | 板块最小涨幅（%） |
| `DRAWDOWN_MAX` | 10.0 | 距高点最大回撤（%） |
| `MA_PERIOD` | 60 | MA周期 |

### 5.4 成本参数（v3.0新增）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `COMMISSION_RATE` | 0.00025 | 佣金（万2.5） |
| `MIN_COMMISSION` | 5.0 | 最低佣金（元） |
| `STAMP_TAX` | 0.001 | 印花税（千1） |
| `SLIPPAGE` | 0.002 | 滑点（±0.2%） |
| `MAX_VOLUME_RATIO` | 0.05 | 单次最大成交量（当日5%） |

### 5.5 AI预警参数（v3.0新增）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `AI_LOSS_THRESHOLD` | 8.0 | 亏损预警阈值（%） |
| `AI_ALERT_ENABLED` | true | AI预警总开关 |

---

## 六、操作说明

### 6.1 首次启动

```bash
# 1. 安装依赖
cd astock-signal
pip install -r requirements.txt

# 2. 配置 .env（从模板复制）
cp .env.example .env
# 手动编辑 .env，填写：
#   - FEISHU_WEBHOOK_URL
#   - LLM_API_KEY（如需要AI功能）
#   - TOTAL_CAPITAL

# 3. 配置自选股
# 编辑 data/watchlist.json
[
    {"code": "000629", "name": "钒钛股份"},
    {"code": "600519", "name": "贵州茅台"}
]

# 4. 运行扫描
python main.py scan

# 5. 启动 Web 服务（另开终端）
python -m web.app
```

### 6.2 日常使用

```bash
# 扫描信号
python main.py scan

# 启动盯盘（持续监控）
python main.py watch

# 发送复盘报告
python main.py report

# 回测
python main.py backtest 000629 --days 60

# 测试预警
python main.py test-alert

# 启动 Web 服务
cd web && python app.py
# 访问 http://localhost:8080
```

### 6.3 Web 端使用

访问 `http://localhost:8080`：
- 仪表盘：持仓、信号一览
- 回测：输入股票代码和天数，点击回测，查看绩效图表

---

## 七、故障排除

### 7.1 数据源问题

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| `get_realtime` 返回 None | 腾讯财经 API 故障 | 等待自动切换到东方财富，或手动 `force_source("eastmoney")` |
| K线数据不足 | 股票停牌过久 | 正常现象，数据清洗时会跳过停牌段 |
| 板块共振跳过 | 板块指数 BK 码不支持 | 已修复，数据清洗改走东方财富 |

### 7.2 信号问题

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| 买入信号永远不足5个 | 市场处于弱势 | 正常，弱势市场需≥7个信号才考虑买入 |
| ATR 显示为 0 | 数据不足14天 | 等待数据积累，或换交易活跃股票 |
| 回测交易为0 | 信号触发条件太严格 | 降低 `buy_threshold=4` 测试 |

### 7.3 Web 服务问题

| 症状 | 解决方案 |
|------|---------|
| 端口被占用 | `python -m web.app --port 8081` 换端口 |
| 导入模块报错 | `pip install -r requirements.txt` 重装依赖 |
| 回测报 500 | 查看终端错误日志，可能是数据源超时 |

### 7.4 通知问题

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| 飞书收不到消息 | Webhook URL 过期或错误 | 重新从飞书群机器人设置中复制 |
| LLM 不返回分析 | API Key 错误或额度用完 | 检查 `.env` 中 `LLM_API_KEY` |
| 预警重复推送 | 5分钟去重窗口内重复触发 | 正常现象，去重机制保护避免轰炸 |

### 7.5 回测问题

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| 收益率永远0 | K线数据全部异常被清洗 | 检查 `data_clean.py` 日志，过滤了哪些数据 |
| 夏普比率为0 | 交易次数为0 | 正常，降低 `buy_threshold=4` 再测 |
| 最终资金不等于净值曲线最后一条 | 最后一条净值在平仓前记录 | 以 `final_capital` 为准 |

---

## 八、v3.0 升级亮点

| 模块 | v2.0 | v3.0 |
|------|------|------|
| 数据源 | 仅腾讯财经 | 腾讯+东方财富自动切换 |
| 数据清洗 | 无 | 异常值过滤/去重/前向填充 |
| 大盘判断 | 仅上证 | 上证+创业板+科创50联合 |
| 尾盘过滤 | 7层 | 10层（+3层强化过滤） |
| 成本计算 | 简单估算 | 佣金+印花税+滑点+成交量限制 |
| 回测 | 无 | 单股60日回测+绩效指标 |
| Web API | 无 | `/api/backtest` 回测接口 |
| AI预警 | 基础 | 亏损/信号/大盘+AI分析 |
| 复盘报告 | 无 | 每日+每周+AI建议 |

---

## 九、Git 提交记录

```
a96ec12 feat: 交易复盘报告 reporter.py
ab88a3c feat: AI预警系统 v3.0
af07346 feat: 回测 Web API 接入 app.py
5297ff4 feat: 回测引擎 + 账户模型 + 成本计算器
f3cda9e feat: 尾盘开仓风险强化过滤 v3.0
f64236b feat: 数据清洗模块
dbfffec feat: 数据源自动选择器
9ad0a52 feat: 东方财富数据源
5d74e55 feat: 初始提交 v2.0
```

---

## 十、联系与支持

- **项目位置**: `~/.openclaw/workspace/astock-signal/`
- **配置文件**: `.env`（需手动创建，不在 Git 中）
- **运行日志**: `logs/` 目录
- **数据文件**: `data/` 目录

> ⚠️ **免责声明**：本工具仅供辅助参考，不构成投资建议。模拟交易不代表真实交易结果，实盘操作风险自负。
