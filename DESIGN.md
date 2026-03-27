# A股信号灯 v2.0 — 设计方案（完整版）

> 项目代号: `astock-signal`
> 版本: v2.0
> 日期: 2026-03-27

---

## 一、核心定位

**定位**: A股自动化量化交易助手

> 不是高频交易，不是套利工具，是帮助散户**克服人性弱点**（追涨杀跌、频繁交易、不止损）的**规则化交易系统**。

---

## 二、系统架构（完整版）

```
┌─────────────────────────────────────────────────────────┐
│                      用户交互层                           │
│  CLI  │  Web界面  │  飞书机器人  │  定时任务              │
└──────────┬─────────┬───────────┬────────────────────────┘
           │         │           │
┌──────────▼─────────▼───────────▼────────────────────────┐
│                    核心调度层 Pipeline                    │
│  股票池管理 → 数据获取 → 信号计算 → 风控检查 → 决策生成  │
│                                    ↓                      │
│                              [自动交易]（可选）            │
└──────────┬─────────┬───────────┬────────────────────────┘
           │         │           │
┌──────────▼──┐ ┌───▼────┐ ┌────▼─────────────────────┐
│  监控引擎    │ │ 交易引擎│ │  通知引擎                │
│  ·实时数据   │ │ ·买入   │ │  ·信号推送               │
│  ·条件扫描   │ │ ·卖出   │ │  ·持仓日报               │
│  ·预警触发   │ │ ·止损   │ │  ·预警提醒               │
└─────────────┘ └────────┘ └────────────────────────────┘
```

---

## 三、股票池管理

### 3.1 数据结构

```python
# data/watchlist.json
{
  "stocks": [
    {"code": "000629", "name": "钒钛股份", "added_at": "2026-03-25", "enabled": true},
    {"code": "600519", "name": "贵州茅台", "added_at": "2026-03-20", "enabled": true},
    {"code": "300750", "name": "宁德时代", "added_at": "2026-03-18", "enabled": false}
  ],
  "settings": {
    "auto_trade": true,
    "notify_only": false,       # true=只推送不交易
    "max_positions": 3,
    "total_capital": 100000
  }
}
```

### 3.2 CLI 操作

```bash
# 查看股票池
python main.py pool list

# 添加股票
python main.py pool add 000629
python main.py pool add 600519 --name 贵州茅台

# 删除股票
python main.py pool remove 000629

# 启用/禁用
python main.py pool disable 300750   # 暂时观察
python main.py pool enable 300750    # 重新启用

# 查看设置
python main.py pool settings

# 修改设置
python main.py pool settings --auto-trade true --notify-only false --max-positions 3
```

### 3.3 交易时间段

```python
TRADING_RULES = {
    "open_auction":   {"start": "09:15", "end": "09:25"},
    "morning":        {"start": "09:30", "end": "11:30"},
    "afternoon":      {"start": "13:00", "end": "15:00"},
    "close_auction": {"start": "14:57", "end": "15:00"},
    # 开仓窗口（尾盘确定性更高）
    "open_window": {"start": "14:30", "end": "15:00"},
}
```

---

## 四、监控引擎

### 4.1 监控模式

| 模式 | 触发条件 | 说明 |
|------|---------|------|
| **watch** | 14:30-15:00 每5分钟扫描 | 主要交易窗口 |
| **alert** | 24小时监控，条件触发 | 止损/止盈预警 |
| **daily** | 每天15:05 | 收盘复盘 |
| **morning** | 09:25前 | 开盘前报 |

### 4.2 watch 模式流程

```
14:30:00  →  扫描股票池所有股票
              ↓
         计算信号（10个买入 + 6个卖出）
              ↓
         检查风控条件
              ↓
    ┌─────── 是否持仓？────────┐
    │ YES                      │ NO
    │ 检查卖出信号              │ 检查买入信号
    │ 信号≥3 → 准备卖出        │ 信号≥5 → 准备买入
    │ 信号<3 → 继续持有        │ 信号<5 → 观望
    │ 止损检查 → 触发即卖       │ 仓位检查 → 有仓位才买
    └────────────────────────┘
              ↓
         生成操作指令
              ↓
    ┌────────────────────────┐
    │  notify_only = false ?   │
    │  auto_trade = true ?     │
    │  → 执行交易              │
    │  → 推送通知              │
    └────────────────────────┘
```

---

## 五、自动交易引擎

### 5.1 交易决策矩阵

```
                    持仓状态
          ┌─────────┬─────────┬─────────┐
          │  无持仓  │  轻仓   │  半仓+  │
    ──────┼─────────┼─────────┼─────────┤
    BUY≥5 │ 买入30% │ 可加仓  │  持有   │
    BUY≥6 │ 买入50% │ 加仓50% │  持有   │
    BUY≥7 │ 买入80% │ 全仓   │  持有   │
    BUY≥8 │ 全仓   │ 持有   │  持有   │
    ──────┼─────────┼─────────┼─────────┤
    SELL≥3│  观望   │ 卖出50% │ 卖出30% │
    SELL≥4│  观望   │ 卖出全  │ 卖出50% │
    SELL≥5│  观望   │ 卖出全 │  全卖   │
    ──────┼─────────┼─────────┼─────────┤
    止损信号│    -    │  强卖  │  强卖   │
    止盈信号│    -    │ 可止盈 │  分批止 │
    弱势市场│  禁止   │  减仓  │  全卖   │
    大跌>2%│  禁止   │  强卖  │  强卖   │
    └─────────┴─────────┴─────────┘
```

### 5.2 交易前检查（必须全部通过）

```python
def pre_trade_check(instruction) -> CheckResult:
    """
    交易前风控检查，必须全部通过才能执行
    """
    checks = []
    
    # 1. 交易时间段检查
    if not is_in_open_window() and not instruction.action == "STOP_LOSS":
        checks.append(("交易时间段", False, "仅14:30-15:00开仓"))
    
    # 2. 大盘状态检查
    market = get_market_status()
    if market.status == "弱势" and instruction.action == "BUY":
        checks.append(("大盘状态", False, "弱势市场禁止开仓"))
    
    # 3. 大盘暴跌检查（>2%强平）
    if market.change_pct < -2.0 and instruction.action != "STOP_LOSS":
        checks.append(("大盘暴跌", False, "大盘跌幅>2%，禁止开仓/强平"))
    
    # 4. 持仓上限检查
    if instruction.action == "BUY":
        if portfolio.position_count >= portfolio.max_positions:
            checks.append(("持仓上限", False, f"已达最大持仓数{portfolio.max_positions}"))
    
    # 5. 资金检查
    if instruction.action == "BUY":
        available = portfolio.total_capital - portfolio.locked_capital
        if instruction.amount > available:
            checks.append(("资金不足", False, f"需{instruction.amount}元，可用{available}元"))
    
    # 6. 止损价合理性
    if instruction.action == "BUY":
        risk_pct = (instruction.price - instruction.stop_loss) / instruction.price * 100
        if risk_pct > 10:
            checks.append(("止损过大", False, f"风险{risk_pct:.1f}%>10%"))
    
    # 7. 同日交易限制
    if portfolio.has_traded_today(instruction.code):
        checks.append(("同日交易", False, "同一股票同日已交易"))
    
    return CheckResult(passed=all(c[1] for c in checks), checks=checks)
```

### 5.3 动态止损（ATR）

```python
def update_stop_loss(position, current_price, atr):
    """
    ATR动态止损
    
    买入后：止损 = 买入价 - 2×ATR
    持仓中：止损只上移，不下移
    """
    if position.status != "open" or position.stop_loss == 0:
        return current_price - 2 * atr
    
    new_stop = current_price - 2 * atr
    # 只上移，不下移
    return max(new_stop, position.stop_loss)
```

---

## 六、风控体系（核心！）

### 6.1 规则优先级

```
P0 强制止损（不可绕过）
├── 大盘单日跌幅>2% → 全仓强平
├── 单股持仓亏损>10% → 强制止损
└── 流动性枯竭 → 强制止损

P1 禁止开仓
├── 弱势市场（大盘MA5<MA10）
├── 非开仓时间段（14:30前）
└── 同一股票同日已交易

P2 仓位限制
├── 单只仓位≤总资金30%
├── 最大持仓数≤3只
└── 总仓位≤80%

P3 信号要求
├── 买入：信号≥5
├── 卖出：信号≥3
└── 低信号时只卖不买
```

---

## 七、命令体系

```bash
# ========== 股票池管理 ==========
pool list              # 查看股票池
pool add <code>        # 添加股票
pool remove <code>     # 删除股票
pool enable <code>     # 启用监控
pool disable <code>    # 禁用监控

# ========== 监控与交易 ==========
watch                  # 启动实时监控（14:30-15:00）
watch --continuous     # 持续监控
analyze <code>         # 分析单只股票
analyze --pool         # 分析股票池所有股票

# ========== 持仓管理 ==========
position              # 查看当前持仓
position <code>       # 查看单只持仓详情
close <code>          # 平仓（需确认）
close <code> --force   # 强制平仓

# ========== 报告 ==========
report                # 收盘复盘报告
report --daily        # 每日持仓报告
report --weekly       # 周报

# ========== 设置 ==========
settings              # 查看所有设置
settings --auto-trade=false   # 关闭自动交易
settings --notify-only=true   # 切换为只推送不交易
```

---

## 八、文件结构

```
astock-signal/
├── main.py                    # CLI入口 + 调度
├── config.py                  # 配置管理
├── models/
│   ├── watchlist.py           # 股票池
│   ├── position.py            # 持仓
│   ├── signal.py              # 信号
│   ├── trade.py               # 交易记录
│   └── portfolio.py           # 账户组合
├── data_provider/
│   ├── txstock.py            # 腾讯财经（主）
│   ├── eastmoney.py          # 东方财富（备）
│   └── fetcher.py            # 统一调度
├── indicators/
│   ├── ma.py                 # 均线
│   ├── macd.py               # MACD
│   ├── rsi.py                # RSI
│   ├── atr.py                 # ATR
│   └── signal_counter.py     # 信号计数
├── strategy/
│   ├── signal_decision.py    # 信号→决策
│   ├── position_sizer.py     # 仓位计算
│   └── market_filter.py      # 市场过滤
├── trading/
│   ├── executor.py           # 交易执行
│   ├── risk_control.py       # 风控
│   ├── pre_check.py          # 交易前检查
│   └── order.py              # 订单
├── monitor/
│   ├── scanner.py            # 扫描器
│   ├── watcher.py            # 监控器
│   └── alerter.py            # 预警
├── notification/
│   ├── feishu.py             # 飞书
│   ├── wechat.py             # 企业微信
│   └── templates/            # 模板
├── storage/
│   ├── db.py                 # SQLite
│   └── json_store.py         # JSON
├── web/
│   ├── app.py               # FastAPI
│   └── templates/            # HTML
├── scripts/
│   ├── daily_report.sh
│   └── install_cron.sh
├── tests/
├── data/
│   ├── watchlist.json
│   ├── positions.json
│   ├── trades.db
│   └── logs/
├── .env.example
├── requirements.txt
└── Dockerfile
```

---

## 九、实现计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| Phase 1 | 核心框架（项目结构/数据源/信号系统/股票池CRUD） | 1周 |
| Phase 2 | 自动交易（交易指令/风控检查/模拟撮合/ATR止损） | 1周 |
| Phase 3 | 监控引擎（watch模式/定时调度/持仓日报/预警） | 1周 |
| Phase 4 | Web界面（仪表盘/管理界面/历史查看/设置面板） | 1周 |

---

## 十、风险声明

> ⚠️ **重要**
> 1. 自动交易默认**关闭**，需要手动开启
> 2. 首次使用建议 `--notify-only` 模式，观察3天再开启自动交易
> 3. 单笔交易限额默认2万，防止一次失误过大
> 4. 大盘暴跌>2%自动触发强平，不可绕过
> 5. 本工具仅供个人学习研究，决策权在用户，承担一切后果

---

确认设计方案后可以从以下模块开始写代码：
1. **股票池管理** — 最简单，先热身
2. **信号系统** — 核心，先打好基础
3. **监控引擎** — 核心功能的重中之重

你想先从哪个开始？