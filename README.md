# A股信号灯 · v6.20

> **A股量化交易辅助系统** · 技术面信号 + 资金流 + 三市场自适应 + 自进化引擎

**信号灯** 不负责下单，只产生决策信号并推送飞书，由用户最终确认执行（`auto_trade=true` 时可全自动）。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **三市场自适应** | 弱市/震荡/强市自动识别，参数（止损线、仓位、止盈）按市场状态切换 |
| **技术指标** | RSI / 量比 / MACD / 布林带 / 均线 多维信号打分（10 分制）|
| **资金流维度** | 主力净流入 / 大单 / 超大单 / DDX / DDY，多源 fallback |
| **风险控制** | ATR 动态止损 + 弱市 RSI 反弹卖出 + 亏损超限 + 持仓超期 |
| **自进化** | 决策日志 → 统计胜率 → 月度AI审核 → 影子追踪验证 |
| **飞书推送** | 实时信号 + 交易通知 + AI 增强分析 |
| **outbox 守护** | 独立进程保障送达链路，3 秒轮询，进程崩溃自愈 |

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env: TOTAL_CAPITAL / FEISHU_WEBHOOK_URL / LLM_API_KEY

# 3. 一次性扫描
python main.py scan

# 4. 实时监控（交易时段：10:00-11:30 / 13:00-15:00）
python main.py watch

# 5. 安装守护进程（Linux cron）
crontab -e
# 添加：
# */5 * * * * /path/to/astock-signal/run_watch_daemon.sh
# */5 * * * * /path/to/astock-signal/run_outbox_daemon.sh
```

详见 [`docs/QUICKSTART.md`](docs/QUICKSTART.md)（如未提供则看 `main.py --help`）

---

## 📊 信号输出示例

```
📊 A股信号灯 · 信号扫描报告 · 2026-06-18 10:00

⚪ 攀钢钒钛 (sz000629)        价格: ¥3.25 (+1.56%) | 信号: 2/10
   买点: ✅RSI=39.8低位整固    ✅量比=0.28<0.7
   资金: 🟢 主力+478万  DDX+0.016
   决策: ⚪ 观望

🟢 金风科技 (sz002202)        价格: ¥20.99 (-2.05%) | 信号: 1/10
   买点: ✅RSI=18.9超卖拐头+局部低点+量能确认
   决策: 🟢 买入
```

四宫格决策：

| 信号 | 含义 |
|------|------|
| 🟢 BUY | 买入机会 |
| 🟡 HOLD | 持仓观望 |
| 🔴 SELL | 卖出信号 |
| ⚪ WATCH | 观望等待 |

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────┐
│             main.py (CLI / 入口)                │
└──────┬────────────────────────────┬─────────────┘
       │                            │
       ▼                            ▼
┌──────────────┐            ┌──────────────────┐
│  Scanner     │            │   Watcher        │
│  扫描信号    │            │   实时监控       │
└──────┬───────┘            └──────┬───────────┘
       │                           │
       ▼                           ▼
┌──────────────────────────────────────────┐
│  indicators/ (RSI/MACD/布林/资金流/三市场) │
└──────┬───────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  trading/ (executor + 风控 + 持仓管理)    │
└──────┬───────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  notification/ (飞书 + LLM 分析)          │
└──────┬───────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  outbox/ (持久化消息队列)                 │
│       ↑                                   │
│  outbox_daemon.py (3s轮询，飞书送达)      │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  evolution/ (Phase 1-5 自进化)            │
│  decision_log → signal_stats → 月度AI审核  │
└──────────────────────────────────────────┘
```

---

## 📁 目录结构

```
astock-signal/
├── main.py                  # CLI 入口（scan/watch/report/backtest）
├── config.py                # 全局配置（三市场参数/资金流开关/节假日）
├── .env                     # 环境变量（持仓资金/飞书key/LLM）
│
├── indicators/              # 信号计算
│   ├── signal_unified.py    # 统一路由层
│   ├── signal_weak.py       # 弱市反弹系统
│   ├── signal_strong.py     # 强市趋势系统
│   └── signal_consolidate.py # 震荡市波段系统
│
├── models/                  # 数据模型
│   ├── signal.py            # 信号枚举、决策
│   ├── position.py          # 持仓、组合
│   └── trade.py             # 交易记录（v6.20 quantity_lots）
│
├── data_provider/           # 数据源
│   ├── txstock.py           # 腾讯财经（主）
│   ├── money_flow.py        # 资金流（push2delay 兜底）
│   └── market_regime.py     # 市场状态识别
│
├── trading/                 # 交易执行
│   ├── executor.py          # 下单执行器
│   ├── pre_check.py         # 交易前检查
│   ├── risk_control.py      # 风控（止损/止盈/仓位）
│   └── cost_calculator.py   # 手续费计算
│
├── monitor/                 # 监控
│   ├── watcher.py           # 实时监控循环（v6.15 止损统一）
│   └── reporter.py          # 报告生成
│
├── notification/            # 通知
│   ├── feishu.py            # 飞书推送（v6.2 outbox 中转）
│   └── llm_analyzer.py      # AI 增强分析
│
├── evolution/               # 自进化（v6.0+）
│   ├── decision_logger.py   # Phase 1: 决策日志
│   ├── stats_analyzer.py    # Phase 2: 统计胜率
│   ├── weight_manager.py    # Phase 3: 权重管理
│   ├── shadow_tracker.py    # Phase 4: 影子追踪
│   ├── monthly_report.py    # Phase 5: 月度AI报告
│   └── orchestrator.py      # 衔接调度
│
├── backtest/                # 回测引擎
│   ├── engine.py            # 单标的回测
│   └── multi_engine.py      # 多标的回测
│
├── scripts/                 # 工具脚本
│   ├── outbox_daemon.py     # outbox 守护进程（v6.6）
│   ├── migrate_v620_rename_quantity.py # v6.20 DB 迁移
│   └── test_v615_sell_reason.py # 止损决策测试
│
├── run_watch_daemon.sh      # 信号灯守护（v6.4）
├── run_outbox_daemon.sh     # outbox 守护（v6.6）
│
├── data/                    # 运行时数据
│   ├── trades.db            # SQLite 交易记录
│   ├── positions.json       # 当前持仓
│   ├── outbox/              # 待发送消息队列
│   └── watchlist.json       # 监控股票池
│
├── logs/                    # 运行日志
├── evolution/               # 自进化数据
├── CHANGELOG.md             # 版本历史（v6.0 ~ v6.20）
└── README.md                # 本文件
```

---

## ⚙️ 配置说明

核心配置在 `.env` 和 `config.py`。**`.env` 优先级更高**（环境变量覆盖）。

### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `TOTAL_CAPITAL` | 1,000,000 | 总资金（元）|
| `MAX_POSITIONS` | 3 | 同时持仓数上限 |
| `WATCH_INTERVAL` | 900 | 扫描间隔（秒，v6.7 从 300 改 900）|
| `WEAK_RSI_SELL_THRESHOLD` | 75.0 | 弱市 RSI 反弹卖出阈值（v6.19 65→75）|
| `WEAK_STOP_LOSS_PCT` | 2.0 | 弱市亏损止损线 |
| `STRONG_STOP_LOSS_PCT` | 10.0 | 强市止损线 |
| `CONSOLIDATE_STOP_LOSS_PCT` | 6.0 | 震荡市止损线 |

### 交易时段（v6.5）

- **上午**：10:00 ~ 11:30
- **下午**：13:00 ~ 15:00
- **午休 / 收盘后**：自动停跑
- **节假日**：内置清单（元旦/春节/清明/劳动/端午/中秋/国庆）

---

## 🛡️ 风控规则

| 规则 | 触发条件 | 操作 |
|------|---------|------|
| **ATR 追踪止损** | 价格 ≤ 持仓止损价 | 强制止损 |
| **弱市 RSI 反弹** | RSI_6 > 75（v6.19）| 卖出锁利 |
| **亏损超限** | pnl_pct ≤ -stop_loss_pct | 强制止损 |
| **持仓超期** | 弱市禁用；强市 30 天 / 震荡 10 天 | 卖出 |
| **连续止损锁仓** | 连续 N 笔止损（v4.2）| 暂停开仓 N 天 |

---

## 📈 版本演进（最近 10 个版本）

| 版本 | 日期 | 主题 |
|------|------|------|
| **v6.22** | 2026-06-29 | 风控加固+数据完整性+可观测性全面修复（7 个 P0/P1/P2 问题） |
| **v6.21** | 2026-06-29 | 版本号统一（文档注释同步） |
| **v6.20** | 2026-06-18 | `quantity` → `quantity_lots` 重命名 + DB 迁移 |
| **v6.19** | 2026-06-18 | 弱市 RSI 卖出阈值 65→75（避免 6/18 金风误触） |
| **v6.18** | 2026-06-16 | evolution stats 每日自动刷新 |
| **v6.16** | 2026-06-16 | 资金流全面简化 + 守护脚本尾盘边界修复 |
| **v6.15** | 2026-06-15 | 止损逻辑统一 + max_hold_days 崩溃修复 |
| **v6.14** | 2026-06-14 | TradeResult.pnl 字段修复（静默bug） |
| **v6.13** | 2026-06-14 | 静默修复 - 信号可观测性 + 资金流降级改造 |
| **v6.12** | 2026-06-10 | 资金流 15min 缓存 + push2delay 健康探测 |
| **v6.11** | 2026-06-09 | 000629 采集切换 push2delay + txstock |

完整历史：[`CHANGELOG.md`](CHANGELOG.md)（v6.0 ~ v6.22，共 25 个版本）

---

## 🧪 测试

```bash
# 止损决策单测（12 个 case）
python3 scripts/test_v615_sell_reason.py

# DB 迁移脚本（dry-run 检查）
python3 scripts/migrate_v620_rename_quantity.py --check-only

# 实际迁移
python3 scripts/migrate_v620_rename_quantity.py --force
```

---

## ⚠️ 风险声明

本项目**仅供学习与研究**，不构成任何投资建议。量化交易存在重大风险：

- 历史回测不代表未来表现
- 模拟交易与实盘有显著差异（流动性/滑点/手续费）
- 自动交易可能导致重大亏损
- 使用前请充分测试，理解每个规则背后的逻辑

---

## 📜 License

MIT（详见 LICENSE 文件）

---

**维护者**：Harin Wu · 最后更新：2026-06-18