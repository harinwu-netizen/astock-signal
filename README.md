# A股信号灯 · 版本 6.0

> **信号灯 6.0 = 量化信号系统 + 自进化引擎**
>
> 在原有五版迭代的基础上，新增「自进化」能力——系统自动记录每笔决策、统计信号有效性、月度AI审核、影子追踪验证，真正实现"机器学习"的闭环。

---

## 目录

1. [系统概述](#1-系统概述)
2. [快速开始](#2-快速开始)
3. [核心概念](#3-核心概念)
4. [自进化系统（v6.0核心）](#4-自进化系统v60核心)
5. [命令参考](#5-命令参考)
6. [配置说明](#6-配置说明)
7. [架构一览](#7-架构一览)
8. [版本历史](#8-版本历史)

---

## 1. 系统概述

### 1.1 什么是信号灯

**信号灯**是一个A股量化交易辅助系统，通过技术指标（RSI、MACD、布林带、均线）自动判断个股状态，输出四宫格决策：

| 信号 | 含义 | 操作 |
|------|------|------|
| 🟢 **BUY** | 买入机会 | 可开仓 |
| 🟡 **HOLD** | 持仓观望 | 持有不动 |
| 🔴 **SELL** | 卖出信号 | 止盈/止损 |
| ⚪ **WATCH** | 观望等待 | 不操作 |

**信号灯不负责下单**，只产生决策信号并推送给用户，由用户最终确认是否执行（`auto_trade=true` 时可全自动执行）。

### 1.2 版本演进

| 版本 | 主题 |
|------|------|
| v4.x | 三市场状态自适应框架（弱市/强市/震荡市） |
| v5.0 | 架构重构，三个独立信号子系统 + 统一路由层 |
| v5.3~5.9 | 止盈止损优化、仓位分档、资金流集成、回测引擎 |
| **v6.0** | **新增自进化系统（Phase 1~5 全链路）** |

### 1.3 系统组件

```
astock-signal/
├── main.py                  # CLI 入口
├── config.py                # 全局配置
├── indicators/              # 信号计算核心
│   ├── signal_counter.py    # 旧版信号计数（兼容层）
│   ├── signal_unified.py    # 统一路由层（三市场分发）
│   ├── signal_weak.py       # 弱市反弹系统
│   ├── signal_strong.py     # 强市趋势系统
│   └── signal_consolidate.py # 震荡市波段系统
├── models/                  # 数据模型
│   ├── signal.py            # 信号枚举、决策模型
│   ├── position.py          # 持仓、组合模型
│   └── trade.py             # 交易记录模型
├── data_provider/           # 数据源
│   └── txstock.py           # 腾讯财经（主）
├── trading/                 # 交易执行
│   └── executor.py          # 下单执行器
├── monitor/                 # 监控推送
│   ├── watcher.py           # 实时监控
│   └── reporter.py          # 报告生成
├── notification/            # 通知模块
│   ├── feishu_sender.py     # 飞书推送
│   └── llm_analyzer.py      # AI 分析（MiniMax/DeepSeek 等）
├── backtest/                # 回测引擎
│   └── engine.py            # 回测核心
├── evolution/               # ⭐ 自进化系统（v6.0新增）
│   ├── decision_logger.py    # Phase 1: 决策日志
│   ├── stats_analyzer.py    # Phase 2: 统计更新
│   ├── weight_manager.py    # Phase 3: 权重管理
│   ├── shadow_tracker.py    # Phase 4: 影子追踪
│   ├── monthly_report.py    # Phase 5: 月度报告
│   └── orchestrator.py     # 衔接调度器
└── scripts/                 # 工具脚本
    ├── install_cron.sh      # Cron 安装
    └── evolution_monthly.py # 月末触发器
```

---

## 2. 快速开始

### 2.1 环境要求

- Python 3.10+
- Linux/macOS/Windows WSL
- 飞书机器人（用于接收通知）

### 2.2 安装依赖

```bash
cd astock-signal
pip install -r requirements.txt
```

### 2.3 基础配置

复制 `.env.example` 为 `.env`，填入必要配置：

```bash
cp .env.example .env
```

关键配置项：

```bash
# 交易模式
AUTO_TRADE=false          # true=全自动执行，false=仅推送信号
NOTIFY_ONLY=false         # true=只推送不交易

# 飞书通知
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_DEFAULT_TO=ou_ee2947ff311d4978679c2a2d4433f62a

# 模拟资金
TOTAL_CAPITAL=1000000

# LLM AI 增强（自进化系统需要）
LLM_PROVIDER=minimax
LLM_MODEL=MiniMax-M2.7
LLM_API_KEY=sk-cp-xxx
LLM_ENABLED=true
```

### 2.4 首次运行

```bash
# 查看帮助
python3 main.py --help

# 添加股票到监控池
python3 main.py pool add sz002997 瑞鹄模具
python3 main.py pool add sh600519 贵州茅台

# 分析单只股票
python3 main.py analyze sz002997

# 分析整个股票池
python3 main.py analyze --pool

# 启动实时监控（Ctrl+C 退出）
python3 main.py watch --continuous
```

### 2.5 定时任务安装

```bash
# 安装定时任务（自动设置 Cron）
bash scripts/install_cron.sh
```

---

## 3. 核心概念

### 3.1 三市场状态

系统根据大盘均线形态，自动判断当前市场状态：

| 状态 | 条件 | 策略 |
|------|------|------|
| 🟢 **强市** | MA5 > MA10 > MA20 | 趋势追踪，顺势而为 |
| 🟡 **震荡** | 均线纠缠，方向不明 | 波段高抛低吸 |
| 🔴 **弱市** | MA20 在最下方 | 抢反弹，快进快出 |

### 3.2 信号计数系统

三个市场各有专属指标，触发条件满足后计入对应信号：

**弱市反弹（3选2）**
| 信号 | 条件 |
|------|------|
| RSI超卖反弹 | RSI(6) < 25 且拐头向上 |
| 触及布林下轨 | 价格 ≤ 布林下轨 |
| 量能萎缩 | 量比 < 0.6 |

**强市趋势（3选2）**
| 信号 | 条件 |
|------|------|
| 均线多头排列 | MA5 > MA10 > MA20 |
| MACD扩散 | DIF 向远离 DEA 方向发展 |
| 量价齐升 | 放量上涨配合 |

**震荡市波段（3选2）**
| 信号 | 条件 |
|------|------|
| RSI低位回升 | RSI(6) 在 35~50 且拐头 |
| 回踩均线 | 价格 ≤ MA10 × 1.02 |
| 缩量整固 | 量比 < 0.7 |

**仓位分级：**
- 1个信号 → 试仓（10%）
- 2个信号 → 标准仓（20%）
- 3个信号 → 重仓（30%）

### 3.3 止损止盈机制

| 类型 | 弱市 | 强市 | 震荡市 |
|------|------|------|--------|
| 止损 | ATR × 1.5 或 -8% | MA20 跟踪止损 | 布林下轨 - 1×ATR |
| 止盈 | RSI > 72 | 收盘跌破 MA20 | RSI > 65 或触上轨 |

---

## 4. 自进化系统（v6.0核心）

### 4.1 设计目标

信号灯 v5.x 依赖人工回测来调整信号权重参数，存在两个根本问题：

1. **反馈周期长**：人工回测需要手动跑数据，等待数周才能验证一次
2. **参数固化**：一旦参数固定，无法自动适应市场风格变化

v6.0 新增**自进化引擎**，让系统能够：
- **自动记录**每一笔决策和结果
- **月度统计**各信号的实际胜率
- **AI 审核**权重调整建议的合理性
- **影子追踪**验证新权重是否真的更好
- **用户确认**后正式切换，全程透明可控

### 4.2 核心流程（Phase 1~5）

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: 决策日志                                          │
│  每日 scan/analyze → 每只股票决策写入 decision_log.csv       │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│  Phase 2: 统计更新                                          │
│  每日收盘后 → 从 decision_log 计算各信号胜率 → signal_stats.json │
└────────────────────────┬────────────────────────────────────┘
                         │ 月末数据量 ≥ 20 条
┌────────────────────────▼────────────────────────────────────┐
│  Phase 3: 权重建议                                          │
│  weight_manager 生成新权重建议 → AI初审 → 推送飞书报告       │
│  → 等待海赟确认                                             │
└────────────────────────┬────────────────────────────────────┘
                         │ 海赟回复「确认W2」
┌────────────────────────▼────────────────────────────────────┐
│  Phase 4: 验证期（影子追踪）                                 │
│  新权重（影子）追踪交易，与旧权重（实盘）同时运行             │
│  每日收盘后补充影子持仓收益数据                              │
└────────────────────────┬────────────────────────────────────┘
                         │ 验证期 1 个月后
┌────────────────────────▼────────────────────────────────────┐
│  Phase 5: 验证报告                                          │
│  AI复审对比实盘 vs 影子 → 自动判断是否采纳新权重            │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 各模块详解

#### Phase 1：决策日志（`decision_logger.py`）

每次 `analyze` 或 `watch` 扫描后，将每只股票的决策写入 `evolution/decision_log.csv`：

```csv
timestamp,scan_time,trade_date,code,name,price,change_pct,market_status,decision,
buy_count,sell_count,rebound_count,trend_count,consolidate_buy_count,
position_ratio,atr,rsi_6,macd_dif,macd_dea,macd_bar,ma5,ma10,ma20,
volume_ratio,buy_signals_detail,sell_signals_detail,decision_reason,
weight_system,shadow_price,shadow_result_5d,shadow_result_10d,result_filled
```

#### Phase 2：统计更新（`stats_analyzer.py`）

每日收盘后运行，从 `decision_log.csv` 读取数据，计算：

| 指标 | 说明 |
|------|------|
| `total_records` | 总决策记录数 |
| `signal_types.{name}.count` | 各信号出现次数 |
| `signal_types.{name}.win_rate_5d` | 各信号5日后盈利胜率 |
| `signal_types.{name}.effective` | 胜率 > 55% → 有效信号 |

结果保存至 `evolution/signal_stats.json`。

#### Phase 3：权重管理（`weight_manager.py`）

月末触发（每月28-31日）。条件：
- 数据量 ≥ 20 条
- 本月未发过报告

**判断规则：**
```
IF 信号胜率 > 55% AND 样本量 ≥ 5
   THEN 上调该信号权重
IF 信号胜率 < 40% AND 样本量 ≥ 5
   THEN 下调该信号权重
```

生成新权重（如 W2），AI 初审后推送飞书，等待海赟确认。

#### Phase 4：影子追踪（`shadow_tracker.py`）

进入验证期后，系统同时运行两套权重：

| | 旧权重（W1） | 新权重（W_new） |
|--|--|--|
| 实际交易 | ✅（实盘） | ❌（影子追踪） |
| 买入信号 | 正常记录 | 记录到 shadow_log |
| 卖出/止损 | 正常执行 | 记录影子结果 |

**影子结果回填：** 每日收盘后，补充影子持仓的5日/10日后价格，计算涨跌。

#### Phase 5：月度报告（`monthly_report.py`）

两种报告：

**学习期报告**（月末数据量够时）
- 各信号胜率统计
- 权重调整建议明细
- AI 初审意见
- 操作选项：确认 / 拒绝

**验证期报告**（验证期满1个月）
- 影子交易（新权重）vs 实盘（旧权重）对比
- 5日/10日平均涨跌
- AI 复审意见
- 自动判断：采纳 / 放弃 / 继续观察

### 4.4 关键判断规则

```
compare_verifying_vs_current() 判断逻辑：

IF 影子数据 < 5 条
   → 不采纳（数据不足）

IF 影子5日均涨 > 1.5% AND 胜率 > 55%
   → 采纳新权重

IF 影子5日均涨 < 0%
   → 放弃新权重

ELSE
   → 继续观察，下月再比
```

### 4.5 用户确认流程

海赟收到飞书报告后，回复：

| 回复 | 含义 |
|------|------|
| `确认W2` / `确认` / `采纳` | 确认建议，进入验证期 |
| `拒绝` / `放弃` | 拒绝建议，保持当前权重 |

确认后，系统状态切换：
```
learning_pending_confirm → verifying（影子追踪开始）
```

### 4.6 时间计划

```
2026-04-27        → v6.0 发布，系统开始记录数据
2026-04-28~30     → 4月剩余交易日，数据积累
2026-05-01~05-27  → 学习期正式运行
2026-05-28~31     → 首次月报触发（数据够20条则发报告）
2026-06-28~31     → 海赟确认后验证期报告
2026-07-28~31     → 验证期满，对比结论
```

> **4月份的数据也会纳入统计。** 无论学习期起点设在哪天，所有已积累的决策记录都会被使用，不会浪费。

### 4.7 查看系统状态

```bash
# 查看自进化系统当前状态
python3 main.py evolution status

# 查看各信号胜率统计
python3 main.py evolution stats

# 查看当前权重参数
python3 main.py evolution weights
```

示例输出：
```
📊 信号灯自进化系统
────────────────────────────────────────
  周期: Cycle 1 | 阶段: learning
  当前权重: W1
  学习开始: 2026-05-01
  验证开始: —
  待确认建议: 无
```

---

## 5. 命令参考

### 5.1 股票池管理

```bash
# 查看股票池
python3 main.py pool list

# 添加股票
python3 main.py pool add sz002997 瑞鹄模具

# 删除股票
python3 main.py pool remove sz002997

# 启用/禁用监控
python3 main.py pool enable sz002997
python3 main.py pool disable sz002997

# 查看配置
python3 main.py pool settings
```

### 5.2 分析与监控

```bash
# 分析单只股票
python3 main.py analyze sz002997

# 分析整个股票池
python3 main.py analyze --pool

# 实时监控（单次）
python3 main.py watch

# 实时监控（持续，Ctrl+C退出）
python3 main.py watch --continuous
```

### 5.3 持仓与报告

```bash
# 查看当前持仓
python3 main.py position

# 收盘复盘报告
python3 main.py report
```

### 5.4 自进化系统

```bash
# 查看系统状态
python3 main.py evolution status

# 查看信号胜率统计
python3 main.py evolution stats

# 查看当前权重
python3 main.py evolution weights
```

### 5.5 系统设置

```bash
# 查看所有配置
python3 main.py settings

# 修改配置
python3 main.py settings --auto_trade true
python3 main.py settings --notify_only false
```

---

## 6. 配置说明

### 6.1 交易参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `AUTO_TRADE` | `false` | 是否自动执行交易 |
| `NOTIFY_ONLY` | `false` | true=只推送不交易 |
| `MAX_POSITIONS` | `3` | 最大持仓股数 |
| `TOTAL_CAPITAL` | `1000000` | 初始资金（元） |
| `STOP_LOSS_PCT` | `8` | 止损线（%） |
| `TAKE_PROFIT_PCT` | `15` | 止盈线（%） |

### 6.2 LLM 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `minimax` | deepseek / zhipu / doubao / qwen / minimax / openai |
| `LLM_MODEL` | `MiniMax-M2.7` | 具体模型名 |
| `LLM_API_KEY` | — | API Key |
| `LLM_ENABLED` | `true` | 是否启用AI增强 |
| `LLM_TEMPERATURE` | `0.3` | 生成温度 |

### 6.3 仓位分档

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `POSITION_TIER1_PCT` | `10.0` | 试仓（1个信号）|
| `POSITION_TIER2_PCT` | `20.0` | 标准仓（2个信号）|
| `POSITION_TIER3_PCT` | `30.0` | 重仓（3个信号）|

### 6.4 自进化参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 学习期最低数据量 | 20条 | 月末发报告的门槛 |
| 验证期最短时长 | 28天 | 验证期最少天数 |
| 影子采纳条件 | 5日均涨 > 1.5% 且胜率 > 55% | 新权重优于旧权重的条件 |

---

## 7. 架构一览

### 7.1 数据流

```
Cron 触发（每日定时）
  │
  ▼
main.py watch / analyze
  │
  ▼
TxStock（腾讯财经数据）
  │
  ▼
SignalCounter（信号计数）
  │
  ▼
SignalUnified（统一路由）
  │              │
  ▼              ▼
弱市信号      强市信号
signal_weak  signal_strong
  │              │
  └──────┬───────┘
         ▼
  MarketStatus + Decision
         │
         ▼
  ┌─────┴─────┐
  │           │
  ▼           ▼
自进化系统   推送飞书
记录决策     通知用户
         │
         ▼
  AUTO_TRADE=true ?
  │         │
  ▼         ▼
  Executor  （跳过）
下单执行
```

### 7.2 自进化数据流

```
每日 scan 完成后
  │
  ▼
orchestrator.on_scan_completed()
  │
  ├─→ decision_logger.log_decision()    → decision_log.csv
  │
  └─→ shadow_tracker.track_shadow()     → shadow_log.csv（如在验证期）

每日收盘后（15:00后）
  │
  ▼
orchestrator.on_market_close()
  │
  ├─→ stats_analyzer.update_stats()     → signal_stats.json
  │
  └─→ shadow_tracker.fill_shadow_results() → 补充影子收益

每月28-31日 18:00
  │
  ▼
orchestrator.on_month_end()
  │
  ├─→ weight_manager.generate_weight_suggestion()
  │      │
  │      └─→ llm_analyzer.analyze_text()（AI初审）
  │
  └─→ monthly_report.build_learning_report()
         │
         └─→ 飞书推送报告给海赟
```

---

## 8. 版本历史

### v6.0（2026-04-27）⭐ 自进化版本

**主题：自进化系统（Phase 1~5 全链路）**

核心新增模块：

| 模块 | 文件 | 说明 |
|------|------|------|
| 决策日志 | `evolution/decision_logger.py` | 每次扫描自动记录每只股票决策 |
| 统计更新 | `evolution/stats_analyzer.py` | 每日收盘后计算各信号胜率 |
| 权重管理 | `evolution/weight_manager.py` | 月末生成权重调整建议 |
| 影子追踪 | `evolution/shadow_tracker.py` | 验证期影子交易追踪 |
| 月度报告 | `evolution/monthly_report.py` | AI审核 + 飞书报告推送 |
| 衔接调度 | `evolution/orchestrator.py` | 各阶段自动衔接 |

关键改进：
- 决策记录自动积累，替代人工回测
- AI 接入 MiniMax/DeepSeek 等大模型进行初审/复审
- 影子追踪机制，无需真金白银即可验证新权重
- 用户确认闭环，全程透明可控

---

### v5.9（2026-04-27）

止盈阈值优化（弱市 RSI 72止盈）、量能确认、强市 RSI 过滤

### v5.8（2026-04-27）

震荡市布林带宽过滤、弱市 RSI 阈值提至 <25

### v5.7（2026-04-26）

信号加权计分（P3），替代 3选2 简单计数

### v5.6（2026-04-26）

资金流获取失败默认否决、市场状态置信度降级

### v5.3（2026-04-10）

弱市止盈阈值优化（45→72），消除乱止盈

### v4.x

三市场状态自适应框架（弱市/强市/震荡市）

---

*信号灯 · 不预测，只应对 · 祝您投资顺利*
