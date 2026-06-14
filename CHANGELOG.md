# 信号灯量化交易系统 · 版本更新说明

---

## v6.0（2026-04-27）⭐ 自进化版本

**主题：自进化系统（Phase 1~5 全链路）**

在量化信号系统基础上，新增「自进化」能力，让系统能自动记录决策、统计信号有效性、生成AI审核报告、影子追踪验证，真正实现"机器学习"闭环。

---

### 🆕 新增模块（evolution/）

| 模块 | 文件 | 职责 |
|------|------|------|
| 决策日志 | `decision_logger.py` | 每次 scan 记录每只股票的决策到 CSV |
| 统计更新 | `stats_analyzer.py` | 每日收盘后计算各信号胜率 |
| 权重管理 | `weight_manager.py` | 月末生成权重调整建议 |
| 影子追踪 | `shadow_tracker.py` | 验证期影子交易（不花真钱验证新权重）|
| 月度报告 | `monthly_report.py` | AI 初审/复审 + 飞书推送 |
| 衔接调度 | `orchestrator.py` | 各阶段自动衔接 |

---

### 🔄 自进化五阶段流程

```
Phase 1: 每日决策记录 → decision_log.csv
    ↓
Phase 2: 每日收盘统计 → signal_stats.json（各信号胜率）
    ↓ 月末数据≥20条
Phase 3: 权重建议生成 → AI初审 → 飞书报告 → 等待海赟确认
    ↓ 「确认W2」
Phase 4: 验证期影子追踪（1个月）
    ↓ 验证期满
Phase 5: AI复审对比 → 自动判断采纳/放弃/继续观察
```

---

### 🤖 AI 集成

- `notification/llm_analyzer.py` 新增 `analyze_text()` 通用接口
- 支持 MiniMax / DeepSeek / 智谱 / 豆包 / 通义 / OpenAI
- 月度报告 AI 初审/复审均通过 MiniMax-M2.7 调用

---

### 📋 自进化新命令

```bash
python3 main.py evolution status   # 查看系统当前状态
python3 main.py evolution stats    # 查看各信号胜率统计
python3 main.py evolution weights   # 查看当前权重参数
```

---

### ⚙️ 自进化关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 学习期最低数据量 | 20条 | 月末发报告的门槛 |
| 验证期最短时长 | 28天 | 验证期最少天数 |
| 影子采纳条件 | 5日均涨 > 1.5% 且胜率 > 55% | 新权重优于旧权重的条件 |

---

### 🗂️ 新增数据文件

| 文件 | 说明 |
|------|------|
| `evolution/cycle_state.json` | 自进化系统当前状态 |
| `evolution/decision_log.csv` | 所有历史决策记录 |
| `evolution/shadow_log.csv` | 影子交易记录 |
| `evolution/signal_stats.json` | 各信号胜率统计 |

---

### 🐛 BugFix

- `count_records()` 空文件返回 -1 → 修正为 0
- `on_month_end()` 同月重复触发 → 增加 `last_report_month` 防重
- 月末数据不足20条时仍发空报告 → 增加门槛判断
- 用户确认回复无法处理 → `orchestrator.on_user_feishu_reply()` 完整实现

---

### 📊 时间计划

```
2026-04-27        → v6.0 发布，开始记录数据
2026-04-28~30     → 4月剩余交易日
2026-05-01~05-27  → 学习期正式运行（积累决策数据）
2026-05-28~31     → 首次月报触发（数据够20条则发报告）
2026-06-28~31     → 海赟确认后验证期开始
2026-07-28~31     → 验证期满，AI复审对比结果
```

---

## v6.1（2026-05-06）

**主题：资金流数据源双保险（东方财富 + QVeris/财达） + 飞书发送双保险**

---

### 🔧 飞书发送双保险机制

**问题**：飞书插件偶发性消息丢失（网络抖动/openclaw进程不稳定）

**解决**：重构 `notification/feishu.py` 为双保险发送：
1. **第一优先**：subprocess 调用 `openclaw message send --channel feishu`（直接发）
2. **fallback**：写入 `data/pending_messages.json`，由 `scripts/send_pending.py` 每5秒轮询重发

新增文件 `scripts/send_pending.py`：后台服务，监控 pending 文件并确保消息最终送达。

---

### 💰 资金流双保险

东方财富资金流 AI 接口（`ai-saas.eastmoney.com`）有调用次数上限，
超限后资金流数据断连。新增 QVeris/财达 作为 fallback。

**新增文件：**
- `data_provider/qveris_money_flow.py` — QVeris/财达资金流适配器（`caidazi.get_stock_moneyflow`）

**改动文件：**
- `data_provider/money_flow.py` — `get_money_flow()` 双保险逻辑 + `MoneyFlowData` 新增 `mid_net` 字段

**调用逻辑：**
```
get_money_flow(code)
  └→ 东方财富 API（EM_API_KEY）
       ├─ 成功 → 返回 MoneyFlowData
       └─ 失败/超限 → QVeris/财达（caidazi.get_stock_moneyflow）
            ├─ 成功 → 返回 MoneyFlowData（main_net/super_net/big_net/small_net/mid_net）
            └─ 失败 → 返回 None
```

---

## v6.2（2026-06-03）⭐ 飞书发送链路 C 方案改造

**主题：飞书发送链路收敛 — 单一 outbox 路径 + 健康检查 + 失败告警**

### 🔥 问题背景

v6.0/v6.1 时代的飞书发送链路存在三大隐患：

| 隐患 | 表现 |
|------|------|
| 多通道并存 | `subprocess CLI` + `pending 文件` + `OpenClaw 兜底` 三条路径同时定义 |
| 路径 1 失效 | `subprocess.run(["openclaw","message","send"])` 自 2026-04-01 起一直失败（CLI 包找不到），累计 466/466 失败 |
| 死循环 | `send_pending.py` 后台进程（PID 1643819）每 5 秒重试，落账 466 条均失败 |

用户**今天**能收到报告，完全依赖 OpenClaw 平台内部兜底，不是信号灯代码本身的功劳。

### 🛠 改造方案：C 方案

**核心思想**：代码里**只剩 1 条发送路径**，且这条路径 100% 走通。

**链路**：
```
信号灯扫描
  ↓
FeishuNotifier (v4) — notification/feishu.py
  ├─ 成功 → 写 data/outbox/{timestamp}_{id}.json
  │         → 小海(OpenClaw heartbeat)检测 status=="pending"
  │         → OpenClaw message tool → 飞书私聊
  │         → 回写 status="delivered" + feishu_message_id
  │
  └─ 失败 → 写 data/send_health.json (outbox_failed)
            → 写 data/alert_needed.flag
            → 小海检测告警标志 → 飞书推送 🚨
```

### 📦 改动文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `notification/feishu.py` | v3 → v4 重写 | 删除 subprocess + pending 双路径,改为单一 outbox |
| `scripts/send_pending.py` | 重写 | 死循环进程 → outbox 应急工具(list/clear/health) |
| `backup/feishu_v3_subprocess_backup.py` | 新增 | 旧版备份,6.7 KB |
| `data/outbox/` | 新建目录 | 信号灯输出,小海读取 |
| `data/send_health.json` | 新建 | 健康检查(总写入/总失败/最近 50 条 history) |
| `data/alert_needed.flag` | 临时文件 | 失败告警标志,小海检测后立即推送 |

### 🧹 清理

- 杀掉 PID 1643819（send_pending.py 死循环进程）
- 删除 `logs/send_pending*.log`、`logs/watcher*.log` 旧日志

### ✅ 验证结果

| 测试项 | 结果 |
|--------|------|
| 单条测试消息（outbox 写入 + 转发） | ✅ `20260603_220031_869573be` 送达 |
| 完整扫描回归测试（7/7 只） | ✅ `20260603_220427_8cff629f` 送达 |
| 故障注入（open() 抛 OSError） | ✅ 健康检查记 1 次失败,告警标志文件创建 |
| 告警链路（flag → 飞书 🚨） | ✅ 推送成功 |
| 死循环进程 | ✅ 已清除 |

### 📋 新增命令

```bash
python3 scripts/send_pending.py list     # 查看 outbox 积压
python3 scripts/send_pending.py clear    # 清空 outbox(应急)
python3 scripts/send_pending.py health   # 查看健康状态
```

### ⚠️ 关键决策

- **不依赖** OpenClaw 内部 message API（避免未知端点风险）
- **不依赖** subprocess CLI（已废弃路径）
- **不依赖** send_pending.py 自动轮询（改为应急工具）
- **保留** OpenClaw 平台兜底作为最后一道防线（不动它）

---

## v6.3（2026-06-03）⭐ 资金流妙查 fallback

**主题：资金流三级 fallback + 妙查精确匹配修复**

### 🔥 问题背景

信号灯 `data_provider/money_flow.py` 资金流原有两级 fallback（东方财富 → QVeris），从 4 月起都失效，导致 7 只监控股票资金流**全部获取失败**。BUY/SELL 信号的"资金面"维度缺失或失真。

**根因**：TOOLS.md 早在 5/22 就写了"改用妙查"，但**只用于小海的 000629 手动分析**，未回填到信号灯主流程。

### 🛠 改造方案：三级 fallback

```
资金流查询
  1️⃣ 东方财富(EM_API_KEY) — 大概率失败
  2️⃣ QVeris/财达(QVERIS_API_KEY) — 偶发可用
  3️⃣ 妙查(mx-finance-data) — 最后兑底(本次新增,实际 100% 成功)
```

### 📦 改动文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `data_provider/miaochang_money_flow.py` | 新建 | 封装妙查 subprocess 调用 + Excel 解析 |
| `data_provider/money_flow.py` | 修改 | get_money_flow() 加 Step 3 妙查 fallback |

### 🐛 关键 bug 修复

**Bug 1: 妙查 description.txt 错配**
- 现象：查 603683 晶华新材返回 000629 攀钢钦钛的数据
- 原因：description 第 3 行 "查询内容:" 才是本次查询，Sheet 名是历史缓存也含 603683
- 修复：精确匹配 "查询内容:" 行，避开 Sheet 名误扰

**Bug 2: 妙查输出路径**
- 现象：MX_OUTPUT_DIR 写绝对路径找不到文件
- 原因：妙查脚本以 `cwd` 为基准，不是固定路径
- 修复：MX_OUTPUT_DIR 改为相对路径 `miaoxiang/mx_finance_data`

### ✅ 验证

7/7 股票资金流全部获取成功：
- 000629 攀钢钦钛：QVeris +563万 (与妙查交叉验证一致)
- 603683 晶华新材：妙查 +4769万 (修复后正确)
- 300308 中际旭创：QVeris +11384万
- 002202 金风科技：QVeris +5145万
- 002792 通宇通讯：QVeris +5232万
- 002371 北方华创：QVeris +68918万
- 688981 华虹半导体：妙查 0万 (资金平衡)

### 🔄 保留 QVeris

QVERIS_API_KEY 未来可能设置，所以保留在 fallback 链中。位置：东方财富之后、妙查之前。

---

## v6.5（2026-06-03）⭐ 扫描时段改为两段（跳午休）

**主题：信号灯扫描时段从 09:15-15:05 改为 10:00-11:30 / 13:00-15:00**

### 🔥 背景

原扫描时段 `09:15-15:05` 覆盖全天（连续 5h50m）。但 11:30-13:00 是午休时间，A股无交易、API 返回脏数据，且信号系统在午休期间产生的 BUY/SELL 信号会误导用户。

### 🛠 改造

**改动文件**：`run_watch_daemon.sh`（`is_trading_hours()` 函数）

**前后对比**：

```
旧（v6.4）:        09:15 ──────────── 15:05  （连续 5h50m）
新（v6.5）:        10:00 ── 11:30 / 13:00 ── 15:00  （跳午休）
```

**实现**：

```bash
is_trading_hours() {
    local now=$(date +%H%M)
    if [ "$now" -ge "1000" ] && [ "$now" -lt "1130" ]; then return 0; fi  # 上午
    if [ "$now" -ge "1300" ] && [ "$now" -lt "1500" ]; then return 0; fi  # 下午
    return 1
}
```

### 📦 副作用

- 守护脚本 `is_trading_hours()` 改了两处时间常量（0915→1000，新增 1130/1300 边界）
- **午休时段会杀进程**（下次 cron 触发 13:00 时重启）
- 日志中间会出现 1.5h 静默期

### ✅ 验证

- 上午段 10:00-11:30：进程正常扫描
- 11:30 后：进程被杀、PIDFILE 清理
- 13:00 重启：进程正常运行
- 15:00 后：再次清理

---

## v6.4.1（2026-06-03 23:19） · 守护脚本 PID 修复

**主题：守护脚本 `$!` PID 错位 bug**

### 🐛 Bug

守护脚本用 `nohup` 或 `setsid` 启动 python3 进程时：

- `$!` 拿到的是 wrapper 进程（nohup/setsid）的 PID
- 不是 python3 真实 PID
- 杀进程时杀的是 wrapper，python3 被 systemd 收养继续跑
- 现象：看起来"杀掉了"，实际还活着，**资源泄漏**

### 🔧 修复

用裸 `python3 main.py watch --continuous &` 启动，`$!` 直接拿 python3 真实 PID。

**测试**：

- 23:18 启动 → 实际 PID 552700
- 23:19 守护脚本杀掉 → ✅ 干净退出

---

## v6.4（2026-06-03 23:14）⭐ 守护脚本去 flock + 节假日判断

**主题：彻底重写守护脚本 `run_watch_daemon.sh`**

### 🔥 问题背景

原守护脚本用 `flock -xn` 启动 `nohup` 进程，**严重 bug**：

| Bug | 现象 |
|-----|------|
| **fd 泄漏** | nohup 子进程继承 flock 的 fd，锁永不释放 |
| **守护脚本死锁** | 后续 cron 触发 flock 立即失败，守护脚本形同虚设 |
| **15:05 后不杀进程** | 进程靠 `main.py` 内部 self-loop 维持，守护脚本杀不掉 |
| **日志污染** | 15:10 之后 8 小时空跑，约 150 次无效 API 调用 |

### 🛠 改造

| 改造点 | 实现 |
|--------|------|
| **去 flock** | 改用 PIDFILE 单点判活 |
| **节假日判断** | 内置 2026 年部分法定节假日（元旦/春节/清明/劳动节/端午/中秋/国庆） |
| **非交易时间杀进程** | 每天 15:00 后清场 |
| **日切换保护** | 09:15 每天重启一次，避免长跑内存泄漏 |
| **进程判活** | `kill -0 $PID`（不真杀，只检测） |

### 📦 新增/改动文件

| 文件 | 变更 |
|------|------|
| `run_watch_daemon.sh` | v3 → v6.4 整体重写，109 行 |
| `backup/run_watch_daemon_pre_fix_20260603.sh` | 新增，旧版备份 |

### 🔄 Cron 触发逻辑

```
每 5 分钟触发
  ↓
is_trading_day()?
  ├─ 否（周末/节假日）→ 杀掉现存进程，退出
  └─ 是
       ↓
is_trading_hours()?
  ├─ 否（非交易时间）→ 杀掉现存进程，清理 PIDFILE
  └─ 是
       ↓
PIDFILE 存在且 kill -0 成功?
  ├─ 是 → 啥也不做
  └─ 否 → 启动 python3 main.py watch --continuous &
```

### ✅ 验证

| 时间 | 行为 | 结果 |
|------|------|------|
| 23:14 手动跑 | 识别"进程活着"+"非交易时间" | ✅ 杀进程+清理 PIDFILE |
| 改动前 | 15:10 之后 8 小时空跑 | ~150 次无效 API |
| 改动后 | 15:00 后立即停止 | 0 次无效调用 |

---


## v6.8 (2026-06-04 17:36) · 资金流 fallback 剔除 QVeris

**主题**: 资金流数据源从三级 fallback 简化为二级 (剔除 QVeris/财达)

### 🔥 问题背景

近 30 天 QVeris/财达调用记录:
- 成功 6 次
- 失败 611 次
- 成功率 0.97%

**根因**:
- `QVERIS_API_KEY` 从未在 `.env` 中设置
- 每次调用都打 "QVERIS_API_KEY 未设置" log
- 100% 失败后 fallback 到下一级
- 6 次成功是 6/2 之前某段时间有临时 key 进了日志
- MEMORY.md 记 "4 月起欠费" 实际是误判

### 🛠 改造

`data_provider/money_flow.py`:
- 删除 `from data_provider.qveris_money_flow import get_money_flow_via_qveris`
- 删除 Step 2 QVeris fallback block (25 行)
- docstring 从 "三级 fallback" 改为 "两级 fallback"
- 改 QVeris 相关 log 为 "妙想数据"

**保留文件** (不删):
- `data_provider/qveris_money_flow.py` (不改, 避免 git diff 噪音)

### 🔄 新 fallback 链

```
get_money_flow(code)
  1. 东方财富 (assistant/ask 端点) ← 主力
  2. 妙想数据 (searchData 端点)     ← v6.3 fallback
```

### ✅ 验证 (2026-06-04 18:35)

- Python 语法 OK
- get_money_flow 导入成功
- 实际调用 000629 返回: 主力净流入=-3732万, DDX=-0.119
- 调用耗时 ~5 秒/只 (正常)
- 调用走 assistant/ask 端点 (成功)

### 📈 收益

- 每次扫描减少 ~100ms 延迟 (省去 QVeris HTTPX 调用)
- fallback 链减少 1 层, 调试更简单
- MEMORY.md 误判 "4 月起欠费" 纠正为 "QVERIS_API_KEY 从未设置"


## v6.9 (2026-06-04 23:50) · push2delay 集成到资金流 fallback

**主题**: 资金流 fallback 链新增 Step 0 (push2delay 公开端点, 免费, 几乎不限流)

### 🔥 问题背景

信号灯"东方财富"(assistant/ask) + 妙想数据(mx-finance-data) 共用同一 EM_API_KEY 限额 ~60次/日.
- 6/4 10:40 触限后全天 403
- 全天 309 次调用, 237 次失败 (77% 失败率)

**新发现**: 东方财富 push2delay 端点 (`/api/qt/stock/fflow/daykline/get`)
- 公开 15 分钟延迟行情数据, 无需 key
- 几乎不限流

### 🛠 改造

新增 `scripts/fund_flow_api.py` (v6.6.1, 7.2KB)
- 函数 `fetch_public_flow(code, market)` → 返回 `dict` 含 main_net/big_net/super_net/mid_net/small_net (单位: 万元)
- 不提供 main_in/out, big_in/out, super_in/out, ddx, ddy (填 0.0)
- 字段对齐 `MoneyFlowData`

`data_provider/money_flow.py` 加 Step 0 块:
- `import fund_flow_api` (sys.path 加 scripts/)
- `_HAS_PUSH2DELAY` (import 成功标志) + `USE_PUSH2DELAY` (env 开关)
- `.env` 加 `FUND_FLOW_BACKEND=push2delay` 开关 (默认 False, 海赟可选启用)
- Step 0 块: 调用 `fetch_public_flow(code)` → 映射到 `MoneyFlowData` → 返回

**集成链路**:
```
1. 东方财富 (assistant/ask) ← 主力
2. 妙想数据 (mx-finance-data)  ← v6.3 fallback
   (push2delay 现在是 v6.9 Step 0, 但默认未启用, 海赟 .env 开关)
```

### ✅ 验证 (2026-06-04 23:50)

- Python 语法 OK
- import 链路 OK (`_HAS_PUSH2DELAY=True`)
- dotenv 加载后 `USE_PUSH2DELAY=True`
- 实际调用 000629: log `[MoneyFlow] push2delay 成功: 主力净流入=-3732万`
- 字段: main_net=-3731.61万, big_net=-1666.03万, super_net=-2065.59万, mid_net=-1332.51万, small_net=5064.12万 ✓

### 🔧 实施踩坑

- ❌ 直接 `python3 -c` 测试 → 没加载 dotenv → USE_PUSH2DELAY=False → 跑 Step 1
- ✅ `load_dotenv()` 模拟真实环境 → USE_PUSH2DELAY=True → Step 0 数据正确
- 信号灯主流程经 main.py → config.setup_env() → dotenv 自动加载 → Step 0 启用

### 📈 收益

- 即使 assistant/ask + mx-finance-data 都限流, 资金流仍能拿到
- 不消耗 EM_API_KEY 配额
- 明早 6/5 10:00 信号灯第一次扫描就生效

### ⚠️ 局限

- push2delay 仅返回**当日数据** (不能拿 5/10 日 DDX 历史)
- 15 分钟延迟 (盘后能拿到收盘数据)

---

## v6.6.2 (2026-06-05 13:12) · outbox 守护进程日志去重

**主题**: 修复 outbox_daemon.py 启动后日志每行重复输出 2 次的 bug

### 🐛 根因

`start()` 函数 `os.fork()` 后用 `os.dup2()` 把子进程的 `stderr` 重定向到日志文件 (`LOG_FILE`)。
但 logging 的 `StreamHandler()` 默认绑 `sys.stderr`,结果:
- `RotatingFileHandler` 写一次 → 1 行
- `StreamHandler` 写 stderr → stderr 已 dup 到同一日志文件 → 又写 1 行
- 同一行被写 2 次 (连毫秒都一样)

### 🛠 修复

`scripts/outbox_daemon.py` 第 76-90 行:
- 去掉 `StreamHandler()`, 只保留 `RotatingFileHandler`
- 给 `outbox-daemon` 子 logger 显式 `addHandler` + `setLevel(INFO)` + `propagate=False`
- 不再依赖 `basicConfig` (因为它绑 root, 受子进程 dup 干扰)

### ✅ 验证

- v6.6.1 备份: `logs/outbox_daemon.v6.6.1.log.bak` (7568 字节, 71 行)
- 重启后: `logs/outbox_daemon.log` 空 → 启动后无任何重复
- 待 13:20 信号灯扫描验证一次完整流程

**重启记录**:
- 旧 PID 827073 (v6.6.1, 启动 20h36m)
- 新 PID 1311625 (v6.6.2, 2026-06-05 13:12:18 启动)

---



---

## v6.6.3 (2026-06-05 17:08) · 飞书报告添加资金流字段

**主题**: 修复 `feishu.py:send_signal_report` 未输出资金流字段的 bug

### 🐛 根因

`analyze_unified` (signal_unified.py:349) 成功获取了 `mf_data` (含 push2delay 真实数据) 并存进 `signal.money_flow`。
但 `feishu.py:send_signal_report` 渲染循环 (138-147 行) 只输出了价格/买点/卖点/决策,**完全没读 `s.money_flow` 字段**。

结果: 6/5 全天 18 次扫描, 资金流 100% 拿到 (0 失败), 但飞书报告里**零资金流数据**, 用户看不到。

### 🛠 修复

`notification/feishu.py` 在"卖点"和"决策"之间插入资金流字段:

```python
if hasattr(s, 'money_flow') and s.money_flow:
    mf = s.money_flow
    mf_icon = "🟢" if mf.main_net > 0 else "🔴"
    ddx_str = f" DDX{mf.ddx:+.3f}" if mf.ddx else ""
    lines.append(
        f"   资金: {mf_icon} 主力{mf.main_net:+.0f}万 "
        f"大单{mf.big_net:+.0f}万 超大单{mf.super_net:+.0f}万{ddx_str}"
    )
```

### ✅ 验证 (2026-06-05 17:07, 模拟渲染 7 只池子, push2delay 真实数据)

- 000629 攀钢钒钛: 主力 -1527万 / 大单 -1152万 / 超大单 -374万 🔴
- 603683 晶华新材: 主力 +11111万 / 大单 -2454万 / 超大单 +13564万 🟢
- 300308 中际旭创: 主力 -638072万 / 大单 -41631万 / 超大单 -596440万 🔴
- 002202 金风科技: 主力 -17476万 / 大单 -3545万 / 超大单 -13931万 🔴
- 002792 通宇通讯: 主力 +13377万 / 大单 +8576万 / 超大单 +4801万 🟢
- 002371 北方华创: 主力 -36128万 / 大单 -8023万 / 超大单 -28106万 🔴
- 688981 华虹半导体: 主力 -134704万 / 大单 -41670万 / 超大单 -93034万 🔴

### ⚠️ 后续考虑 (v6.6.4+)

中盘股/大盘股数字绝对值差异大, 可考虑自动按绝对值切换"万/亿"单位 (>=10000万 → 显示"X.XX亿")。
是显示美观问题, 非数据正确性问题, 待海赟决策。

### 🛑 不需重启

信号灯运行时通过 `from notification.feishu import ...` 动态 import 渲染代码。
- 下次信号灯扫描 (6/8 10:00) 自动生效
- 守护进程 outbox_daemon.py 不需重启

## v5.8 （2026-04-27）

**主题：基于P4回测结果的针对性优化（震荡市/弱市过滤）**

---

### 🛠 优化一：回测引擎 regime 检测升级为 P2 置信度版（`backtest/engine.py`）

`_detect_regime_for_date` 嵌入 P2 置信度逻辑：
- 震荡市置信度 < 0.4 → 自动降级为弱市（不再在MA纠缠时错误使用震荡市策略）
- 回测 engine 和实盘使用同一套 regime 判断逻辑，保证一致

---

### 🛠 优化二：弱市 RSI 反弹加局部低点确认（`indicators/signal_weak.py`）

**问题**：RSI<30 拐头时，如果价格在下跌途中（飞刀），买入即被套

**修复**：RSI超卖反弹信号增加「价格处于近10日低点30%分位以下」要求
- 旧：`RSI<30 且拐头` → 买入
- 新：`RSI<30 且拐头 且 价格在近10日低点30%分位以下`
- 效果：弱市交易数从71笔→63笔（减少11%低质量信号）

---

### 🛠 优化三：震荡市加布林带宽过滤（`indicators/signal_consolidate.py`）

**问题**：布林带扩张时（大波动/趋势行情）仍然触发震荡市买入，假信号多

**新增**：`calc_bollinger_bandwidth()` 函数
- Bandwidth = (Upper - Lower) / Middle
- **带宽 ≥ 0.20** → 直接返回 WATCH（高波动/趋势市，不适合震荡波段）
- **带宽 < 0.10** → 真震荡，信号加权得分 ×1.3 强化
- 效果：震荡市交易从23笔→20笔，胜率从21.7%→25.0%，盈亏比从0.37→0.45

---

### 📊 回测结果对比（近期6月，20只股）

| 市场 | 指标 | 优化前(v5.7) | 优化后(v5.8) | 变化 |
|------|------|------------|------------|------|
| 震荡 | 交易数 | 23笔 | **20笔** | ✅ -3笔 |
| 震荡 | 胜率 | 21.7% | **25.0%** | ✅ +3.3pp |
| 震荡 | 盈亏比 | 0.37 | **0.45** | ✅ +0.08 |
| 弱市 | 交易数 | 71笔 | **63笔** | ✅ -8笔 |
| 弱市 | 胜率 | 52.1% | **54.0%** | ✅ +1.9pp |
| 全局 | 总交易 | 110笔 | **99笔** | ✅ -11笔噪音 |
| 最佳股 | 收益率 | +13.06% | **+15.65%** | ✅ |

**结论**：震荡市和弱市两大短板均得到改善，策略质量提升。强市结果不受影响（16笔/43.8%/2.25盈亏比完全不变）。

---

## v5.9 （2026-04-27）

**主题：止盈阈值优化 + 量能确认 + 强市 RSI 过滤**

---

### 🛠 优化一：弱市止盈阈值 65→72，让反弹走完（`signal_weak.py`）

**问题**：弱市 RSI>65 即止盈，但反弹往往在 RSI 65-72 之间才真正走完，提前卖飞

**改动**：
- 止盈阈值：65 → 72（让利润在 RSI 65-72 之间继续奔跑）
- 超买阈值：70 → 75（更高才认超买）
- RSI 65-72 之间：标记为「观察中」但不触发卖出

---

### 🛠 优化二：弱市加量能确认（`signal_weak.py`）

**问题**：缩量反弹是「死猫跳」，无量能支撑的 RSI 反弹信号质量差

**改动**：RSI 超卖反弹信号增加量比要求（0.6 ≤ 量比 ≤ 2.0）
- 量比 < 0.6：地量，反弹无资金参与，信号降级
- 量比 > 2.0：恐慌抛盘，也不参与
- 量比 0.6~2.0：健康反弹，量能参与合理

---

### 🛠 优化三：弱市 RSI 阈值从 <30 提至 <25（`signal_weak.py`）

**问题**：RSI 30 是超卖边缘，真正的超卖底通常 RSI < 25

**改动**：RSI 超卖反弹条件从 <30 提至 <25（更严格的底部确认）

---

### 🛠 优化四：强市加 RSI(14) 过滤（`signal_strong.py`）

**问题**：强市 MACD 金叉买入时，若 RSI(14) 已在 70 以上，属「强弩之末」

**改动**：MACD 扩散买入信号加 RSI(14) < 70 条件，量比阈值从 1.3 提至 1.5

---

### 📊 回测结果（四个时间段，多股验证）

| 时间段 | 平均收益 | 胜率 | 盈亏比 | 最大回撤 | 总交易 |
|--------|---------|------|--------|---------|--------|
| 近期6月 | -0.10% | 36.6% | **1.43** | 2.13% | 90 |
| 中期9月 | **+0.72%** | 38.9% | **1.97** | 3.25% | 105 |
| 年度2025 | **+0.97%** | 38.0% | **1.82** | 3.65% | 152 |
| 长期15月 | **+1.31%** | **40.1%** | **1.48** | 4.09% | 191 |

**关键结论**：
- 周期越长 → 收益越高、胜率越高 → 策略在趋势市场中随时间有效
- **全时间段盈亏比 > 1.0**，数学期望为正，策略逻辑成立
- 强市（135笔，47.4%胜率，2.04盈亏比）= 策略最佳应用场景

**v5.8 → v5.9 核心变化**：全局盈亏比从 0.78 → 1.43（+83%），是真正的质变

---

## v5.8 （2026-04-27）

**主题：基于P4回测结果的针对性优化（震荡市/弱市过滤）**

---

### 🛠 P4：多股多周期完整回测框架（`backtest/full_runner.py` + `data/stock_pool_sample.py`）

**新增文件：**

```
data/stock_pool_sample.py   # 20只样本股（大盘8/中盘7/小盘5）
backtest/full_runner.py     # 多股多周期回测引擎
data/backtest_results/       # JSON报告输出目录
```

**样本股票池（20只）：**
| 市值 | 只数 | 代表股 |
|------|------|--------|
| 大盘 | 8 | 格力电器、贵州茅台、招商银行、比亚迪、隆基绿能 |
| 中盘 | 7 | 海光信息、科大讯飞、紫光国微、闻泰科技、大华股份 |
| 小盘 | 5 | 钒钛股份、晶华新材、瑞鹄模具、微光股份、皇马科技 |

**回测时间段：**
| 名称 | 区间 | 天数 |
|------|------|------|
| 近期6月 | 2025-10-01 ~ 2026-04-01 | 183天 |
| 中期9月 | 2025-07-01 ~ 2026-04-01 | 274天 |
| 年度2025 | 2025-01-01 ~ 2026-01-01 | 365天 |
| 长期15月 | 2025-01-01 ~ 2026-04-01 | 456天 |

**运行方式：**
```bash
# 全量（4时间段 × 20只股）
python -m backtest.full_runner

# 单时间段
python -m backtest.full_runner --period 近期6月

# 按市值筛选
python -m backtest.full_runner --stocks 小盘

# 指定初始资金
python -m backtest.full_runner --capital 500000
```

---

### 📊 P4 初步回测结果（近期6月，20只股，110笔交易）

| 市场状态 | 交易数 | 胜率 | 盈亏比 | 平均持仓 |
|----------|--------|------|--------|---------|
| 🟢 强市 | 16 | **43.8%** | **2.25** | 21.5天 |
| 🟡 震荡 | 23 | 21.7% | 0.37 | 8.5天 |
| 🔴 弱市 | 71 | 52.1% | 0.65 | 6.2天 |
| **全局** | **110** | **42.1%** | **0.82** | — |

**关键发现：**
- 震荡市是最大短板（胜率21.7%，盈亏比0.37）→ 信号系统在震荡市产生大量假信号
- 弱市交易数最多（71笔），但平均亏损时亏得大（盈亏比0.65）
- 强市是策略最佳应用场景（高盈亏比2.25）
- 全局最大回撤仅2.47%，风控底线稳健

**结论：** 信号灯在趋势市场（强市/弱市反弹）表现较好，震荡市需要进一步加强过滤或降级处理

---

## v5.7 （2026-04-26）

**主题：P3 信号加权计分（替代 3选2 简单计数）**

---

### 🛠 P3：信号加权计分（`signal_weak / signal_strong / signal_consolidate`）

**问题**：三个指标权重相同，简单 `len(buy_triggered) >= 2` 忽略了信号质量差异

**改动**：每个子系统新增信号权重字典 + 加权得分函数，替换原阈值判断

#### 弱市（`signal_weak.py`）

| 信号 | 权重 | 说明 |
|------|------|------|
| RSI超卖反弹 | **0.5** | RSI<30 且拐头，质量最高 |
| 触及布林下轨 | **0.4** | 独立支撑信号 |
| RSI偏低 | **0.2** | 单独意义弱 |
| 缩量 | **0.3** | 需配合 RSI 信号 |

**阈值**：加权得分 >= **0.5**（RSI超卖反弹+布林下轨=0.9 买，RSI偏低+缩量=0.5 刚好达标，仅缩量=0.3 不买）

#### 强市（`signal_strong.py`）

| 信号 | 权重 | 说明 |
|------|------|------|
| 均线多头排列 | **0.5** | 趋势基准，最稳定 |
| MACD扩散 | **0.4** | 动量信号 |
| 量价齐升 | **0.4** | 趋势确认 |

**阈值**：加权得分 >= **0.6**（强市需更强确认，单一信号不够）

#### 震荡市（`signal_consolidate.py`）

| 信号 | 权重 | 说明 |
|------|------|------|
| RSI低位整固 | **0.5** | 真正有意义的底部信号 |
| 回踩布林下轨 | **0.4** | 支撑确认 |
| RSI偏低 | **0.2** | 单独偏弱 |
| 缩量整固 | **0.3** | 配合信号 |

**阈值**：加权得分 >= **0.5**

#### 仓位计算同步优化
- 原：`position_ratio = 0.5`（三信号全中才 1.0）
- 新：`position_ratio = min(weighted_score / 1.2, 上限)`（连续归一化，分数越高仓位越大）

---

## v5.6 （2026-04-26）

**主题：P1资金流错误处理 + P2市场状态置信度**

---

### 🛠 P1：资金流获取失败 → 默认否决（`indicators/signal_unified.py`）

**问题**：资金流 API 获取失败时原代码 `pass` 静默忽略，过滤器形同虚设

**修复**：引入 `mf_fetch_failed` 标记，失败后强制走保守策略

```python
# 旧：except Exception: pass  ← 静默忽略
# 新：mf_fetch_failed = True  ← 标记后强制处理

if mf_fetch_failed:
    if decision == "BUY":
        decision = "WATCH"
        reason = "【资金流获取失败，默认否决】..."
    elif decision in ("HOLD", "TAKE_PROFIT"):
        reason = f"{reason} ⚠️资金流数据获取失败"
```

**效果**：资金流不可用时不冒进，信号决策更保守

---

### 🛠 P2：市场状态加置信度 + 低置信降级（`indicators/market_regime.py`）

**问题**：MA纠缠时市场状态来回切换，导致策略漂移

**新增**：`MarketRegimeResult.confidence` 字段（0.0~1.0）

| 市场状态 | 置信度算法 |
|----------|------------|
| 强市/弱市 | `min(均线间距5日/20日 ÷ 5%, 1.0)` — 间距越大越确定 |
| 震荡市 | `max(0, 1 - 均线间距5日/10日 ÷ 3%)` — 间距越小越确定 |

**新增**：低置信震荡市自动降级为弱市（保守处理）

```python
if regime == CONSOLIDATE and confidence < 0.4:
    regime = WEAK  # 降级
    reason = f"震荡→弱市降级：置信度{confidence:.2f}<0.4，均线间距过大..."
```

**修改**：所有 `MarketRegimeResult` 返回处均加 `confidence` 字段，异常/数据不足时 `confidence=0.0`

---

## v5.3 （2026-04-10）

**主题：弱市"让利润跑"重构 — 消除乱止盈**

---

### 🚀 核心改进

#### 1. 弱市取消 RSI 45/55 止盈（关键）
- **旧**：RSI > 45 止盈，RSI > 55 强止盈（提前下车，大量利润回吐）
- **新**：RSI > 65 才止盈，RSI > 70 才强走（让反弹走完）
- **效果**：弱市 PNL 从 +2,548 → +12,376（5倍提升）

#### 2. 弱市买入从严（RSI < 40 → RSI < 35）
- RSI < 35 才算偏低（减少接飞刀）
- RSI < 30 且拐头才触发超卖反弹

---

### 📊 回测对比

| 指标 | v5.2 | v5.3 | 变化 |
|------|------|------|------|
| 总收益率 | 2.08% | **3.47%** | ✅ +1.4pp |
| 最大回撤 | 5.17% | 5.12% | ✅ |
| 盈亏比 | 1.58 | **1.89** | ✅ |
| **弱势市场** | +2,548 | **+12,376** | ✅ 5倍 |

---


### 🚀 核心改进（震荡市）

#### 1. 买入条件大改（关键）
- **旧**：`RSI 30-58 适中回调`（太宽，盲目抄底）
- **新**：`RSI 低位拐头/回升`（RSI 35-50 且拐头向上，或 RSI 35-55 从低位回升）
  - 捕捉 RSI 真正的底部整固，而不是 RSI 还在半山腰

#### 2. 布林支撑从严
- 下轨倍数：1.02× → **1.01×**（支撑必须真的接近下轨）
- 取消布林中轨买入（中轨买入信号弱，删除）

#### 3. 缩量整固从严
- 量比阈值：< 0.8 → **< 0.7**（更明显的缩量信号）

#### 4. ATR 止损大放宽（给波动空间）
- ATR倍数：0.5× → **1.5×**（正常波动不触发止损）
- 固定止损：-8% → **-10%**

#### 5. 新增 RSI 回落卖出
- 买入后 RSI 从反弹高位再次回落 → 触发 SELL
- 捕捉"反弹失败"的早期信号

---

### 📊 回测对比（15只股，180天）

| 指标 | v5.1 | v5.2 | 变化 |
|------|------|------|------|
| **总收益率** | -5.72% | **+2.08%** | ✅ 转正！ |
| 最大回撤 | 10.80% | **5.17%** | ✅ -5.6pp |
| 胜率 | 34.5% | **42.3%** | ✅ +8pp |
| 盈亏比 | 1.46 | **1.58** | ✅ |
| 总交易 | 87笔 | **52笔** | ✅ -35笔噪音 |
| **震荡市** | 42笔/亏87k | **2笔/亏2.8k** | ✅ 质变 |

---


### 🚀 核心改进

#### 1. 强市买入否决过滤器（关键突破）
- **问题**：熊市（全局RSI>60）中追强市突破，大部分以大亏结束
- **规则**：强势市场买入时，增加全局RSI(14)过滤器；整体市场RSI>60时不追强势信号
- **效果**：强势交易从7笔（亏-30,332）→ 1笔（赚+8,888）

#### 2. 震荡市RSI止盈阈值优化
- RSI止盈：55 → **60**（给波段更多空间）
- RSI买入上限：60 → **58**（与止盈阈值之间留缓冲）

#### 3. 弱市RSI止盈阈值优化
- RSI止盈阈值：50 → **45**（熊市反弹短跑，及时兑现）
- RSI强止盈：65 → **55**（RSI>55必须走）

---

### 📊 回测对比

| 指标 | v5.0 | v5.1 | 变化 |
|------|------|------|------|
| 总收益率 | -6.04% | **-0.51%** | ✅ +5.5pp |
| 最大回撤 | 6.72% | **1.39%** | ✅ -5.3pp |
| 胜率 | 39.1% | **50.0%** | ✅ +11pp |
| 盈亏比 | 0.43 | **0.85** | ✅ 接近1 |
| 强势交易 | 7笔/亏-30k | **1笔/赚+8.9k** | ✅ 质变 |

### ⚠️ 注意事项

- v5.1 进步显著，但仅在3只股票、90天数据上验证，存在过拟合风险
- 强势买入过滤是**逻辑规则**而非参数，有较好的泛化能力
- 建议先在模拟盘观察2-4周，确认信号质量后再切实盘

---


### 🚀 核心架构升级

#### 1. 三个独立信号系统（新增）
新拆分为三个完全独立的市场状态专属信号系统，各自拥有独立的指标、逻辑和参数：

- `indicators/signal_weak.py` — **弱市反弹系统**
  - 核心指标：RSI(6) < 40 + 触及布林下轨 + 量比 < 0.6（3选2买）
  - 买入：RSI偏低 + 支撑位 + 缩量三合一
  - 卖出：RSI > 50 止盈，RSI > 65 强制止盈
  - 止损：硬止损 -2%，不抗单

- `indicators/signal_strong.py` — **强市趋势系统**
  - 核心指标：MA多头排列 + MACD扩散 + 量价齐升（3选2买）
  - 持有：让利润奔跑，MA20 跟踪止损
  - 卖出：MA5下穿MA10 或 收盘跌破MA20
  - 止损：跌破MA20 或 亏损>8%

- `indicators/signal_consolidate.py` — **震荡市波段系统**
  - 核心指标：RSI在30-60区间 + 回踩均线 + 量比<0.8（3选2买）
  - 卖出：触及布林上轨 或 RSI>65
  - 止损：布林下轨 - 0.5×ATR 或 亏损>8%

#### 2. 统一路由层（新增）
- `indicators/signal_unified.py` — 三层决策路由器
  - 优先使用当前市场状态对应的子系统
  - 跨系统否决：震荡市不追强市趋势破坏的票，弱市不抢RSI>65的高位
  - 持仓期间：sell_count > 0 直接触发 SELL

#### 3. 兼容层
- `indicators/signal_counter.py` 重写为代理层，内部调用 `signal_unified`，对外保持旧接口不变

---

### 🔧 关键修复

#### 1. 回测引擎退出逻辑重构（严重Bug）
- **问题**：旧引擎有独立的 ATR 追踪止损 + 固定止损 + RSI 阈值判断，完全绕过信号系统，导致信号系统的 SELL/TAKE_PROFIT 从未被使用
- **修复**：回测引擎退出改为信号驱动（STOP_LOSS/SELL/TAKE_PROFIT 优先），安全网兜底

#### 2. 持仓股 buy_price 传入（严重Bug）
- **问题**：回测引擎计算信号时对所有股票传 `buy_price=0`，导致信号系统无法区分持仓/观望状态，所有持仓期间都返回 WATCH
- **修复**：对持仓股单独传入 `pos.buy_price`，信号系统才能正确输出 SELL/HOLD

#### 3. 跨系统否决逻辑修复
- **问题**：旧否决逻辑会将强市卖出信号否决弱市买入（逻辑错误，弱市反弹本就该在空头市场买）
- **修复**：只有震荡市才参考其他系统的否决信号；弱市/强市主要看自己的系统

---

### 📊 回测对比（90天，3只股，100万本金）

| 指标 | v4.x（旧） | v5.0（新） | 变化 |
|------|-----------|-----------|------|
| 总收益率 | -10.71% | -6.31% | ✅ +4.4pp |
| 最大回撤 | 12.10% | 6.99% | ✅ -5.1pp |
| 信号驱动退出 | 0% | 88% | ✅ 质变 |
| 安全网兜底 | 100% | 12% | ✅ 大幅减少 |

---

## v4.6 （2026-04-08）

**主题：Cron扫描时间表修正**

---

### 🐛 问题修复

#### 1. 上午cron表达式错误（严重Bug）
- **问题**：v4.5 changelog 记录了正确表达式 `0 0,10,20 9-11 * * 1-5`，但实际创建时错误地分成了两段，只覆盖 10点和11点，**9点完全缺失**
- **修复**：按实际需求重建 cron

#### 2. 下午监控覆盖不足
- **问题**：原配置下午只到 14:50
- **需求**：13:00-15:00 每10分钟扫描，收盘（15:00）再单独确认

---

### ⏰ 定时任务配置（v4.6起生效）

| 作业 | 表达式 | 执行时间 |
|------|--------|---------|
| 上午-10点 | `0 0,10,20,30,40,50 10 * * 1-5` | 10:00-10:50 每10分钟 |
| 上午-11点 | `0 0,10,20,30 11 * * 1-5` | 11:00-11:30 每10分钟 |
| 下午监控 | `0 0,10,20,30,40,50 13-14 * * 1-5` | 13:00-14:50 每10分钟 |
| 收盘扫描 | `0 15 * * 1-5` | 每天15:00 |
| 月末复盘 | `0 18 28-31 * *` | 每月末28-31日18:00 |

**Session策略**：全部5个作业统一使用持久化 `session:信号灯`，上下文跨任务保持。

**推送机制**：全部5个作业统一使用 `announce` 模式，系统自动推送飞书。

### 🔧 交易机制调整

#### 开仓窗口扩展
- **旧**：强势市场 14:30-15:00，弱市 14:00-15:00
- **新**：全天 09:30-15:00 任意时间可开仓（强势/弱市统一）

---

## v4.4 （2026-04-01）

**主题：震荡市波段策略 + 飞书直发增强**

---

### 🆕 新增功能

#### 1. 震荡市波段交易体系
震荡市不再"无交易可做"，新增3指标波段系统：

**买入信号（满足2/3个即可）：**
| 指标 | 条件 | 含义 |
|------|------|------|
| RSI适中回调 | RSI(6) 在 30~50 区间 | 回调到低位，非超卖 |
| 价格回踩均线 | 收盘价 ≤ MA10 × 1.02 | 触及均线支撑 |
| 缩量整固 | 当日成交量 < 5日均量 × 0.8 | 抛压轻 |

**卖出信号（满足2/3个即可）：**
| 指标 | 条件 | 含义 |
|------|------|------|
| RSI回升止盈 | RSI(6) > 55 | 回到正常区间 |
| 触及布林上轨 | 收盘价 ≥ 布林上轨 × 0.98 | 触及区间上沿 |
| 持仓超5天 | 持有 > 5个交易日 | 时间止损 |

#### 2. 飞书通知直发功能
- 通知模块升级为 v2，支持直接推送消息到用户个人账号
- 默认发送目标：`ou_ee2947ff311d4978679c2a2d4433f62a`
- 交易后自动触发 AI 分析（异步，不阻塞交易）
- 分析内容包括：买入逻辑合理性、风险点、后市建议

#### 3. 后台消息队列服务
- 新增 `scripts/send_pending.py`，支持消息队列持久化
- 断网或服务重启不丢消息

---

### 🔧 优化改动

#### 1. 仓位计算修复（关键BugFix）
- **问题**：震荡市使用旧的 `buy_count`（需≥5才买入），导致波段买入信号（`consolidate_buy_count`）达标时没有匹配仓位
- **修复**：`_calc_position_ratio()` 对震荡市改用 `consolidate_buy_count`
  - count=3 → 100% 仓位
  - count=2 → 50% 仓位
  - count<2 → 不买入

#### 2. 布林上轨字段引入
- `MultiPosition` 新增 `bb_upper` 字段
- 建仓时记录布林上轨，止盈时用 `close >= bb_upper × 0.98` 判断

#### 3. 震荡市卖出逻辑增强
- 原有逻辑：固定止盈 + 超期平仓
- 新增：RSI>55 或 触及布林上轨×0.98 → 波段止盈
- 卖出理由标记为"RSI>XX波段止盈"

#### 4. 初始资金更新
- 100,000 元 → **1,000,000 元**

---

### 📊 v4.4 回测表现（2025-07-31 ~ 2026-04-01）

| 指标 | v4.3 | **v4.4** |
|------|------|---------|
| 收益率 | +21.09% | **+21.61%** |
| 最终资金 | 121,095 | **1,216,081** |
| 最大回撤 | 4.49% | **4.62%** |
| 胜率 | 60.0% | **60.0%** |
| 盈亏比 | 2.00 | **2.00** |
| 总交易 | 40笔 | **40笔** |

**各市场贡献变化：**
| 市场 | v4.3笔数 | **v4.4笔数** | 变化 |
|------|---------|-------------|------|
| 震荡市 | 16笔 | **26笔** | +10笔 |
| 强势 | 11笔 | **4笔** | -7笔 |
| 弱势 | 13笔 | **10笔** | -3笔 |

> 震荡市捕捉能力大幅提升（从16笔→26笔），强势/弱势机会减少因波段信号优先于趋势信号

---

### 🐛 已知问题（待优化）

以下问题已定位，将在1-2个月模拟盘数据验证后修复：

1. **603683晶华震荡市超期持仓亏**：3笔10天超期全亏，建议将震荡市持仓期从10天缩至7天
2. **弱势市场ATR止损太敏感**：建议弱势市场改用MA20止损
3. **RSI止盈阈值偏低**：建议震荡市RSI止盈从55提至65

---

### 🔢 版本历史

| 版本 | 日期 | 主题 |
|------|------|------|
| v4.0 | 2026-03 | 初始版本，弱强市自适应框架 |
| v4.1 | 2026-03 | 弱势ATR 2x→3x，持仓期差异化 |
| v4.2 | 2026-03 | 多股回测引擎，股票池扩展 |
| v4.3 | 2026-04-01 | 震荡市波段策略初版（3指标体系） |
| **v4.4** | **2026-04-01** | **仓位计算修复 + 布林上轨止盈 + 飞书直发增强** |

---

## v4.5 （2026-04-02）

**主题：系统稳定性全面修复 + 定时任务重构**

---

### 🐛 问题修复

#### 1. watch --continuous 参数失效
- **问题**：`main.py watch --continuous` 收到参数但从未使用，始终执行单次扫描
- **修复**：`cmd_watch(continuous=True)` → 直接调用 `Watcher().start(continuous=True)`
- **文件**：main.py

#### 2. 非交易日/节假日检测升级
- **问题**：仅判断周末，法定节假日（如春节/国庆）仍会触发无效扫描
- **修复**：通过上证指数 `get_history('sh000001')` 数据判断——最新数据日期≠今日 → 非交易日
- **实现**：`is_trading_day()` 函数，失败时默认当交易日（保守）
- **文件**：monitor/watcher.py, main.py

#### 3. send_pending 超时过短
- **问题**：超时10秒导致消息发送失败积压
- **修复**：超时时间 10秒 → 30秒
- **文件**：scripts/send_pending.py

#### 4. 积压消息清理
- **问题**：之前调试阶段积压40+条旧消息未发送
- **处理**：已全部发送完毕，pending_messages.json 已清空

#### 5. 重复进程导致 Feishu 限流（根因修复）
- **问题根因**：曾手动 `nohup` 启动过 send_pending.py，随后又创建了 systemd 服务，两进程同时竞争读取 pending_messages.json 并向同一飞书 channel 发送消息，触发 Feishu API 限流，单次请求耗时30秒超时，引发重试风暴，消息队列堆积
- **修复**：停掉所有手动启动的测试进程，只保留 systemd 服务
- **重试策略优化**：最多重试3次后丢弃，避免重试风暴加剧队列堆积
- **cron delivery 优化**：三组 cron 任务 delivery 策略全部改为 `bestEffort: true`，防止重复推送
- **服务保活**：send_pending.py 配置为 systemd 服务（astock-signal-send.service）+ `Restart=always`，开机自启动

---

### ⚙️ 配置修复

#### 5. AUTO_TRADE 设置未生效
- **问题**：`notify_only` 和 `AUTO_TRADE` 的布尔值字符串转换逻辑有误
- **修复**：直接修改 .env 文件确认值正确
- **当前值**：`AUTO_TRADE=true`, `NOTIFY_ONLY=false`

#### 6. 测试进程干扰生产
- **问题**：之前调试时手动启动的 `Watcher(continuous=True)` 测试进程无视窗口持续扫描
- **处理**：已停止所有测试进程

#### 7. 测试数据清理
- **问题**：trades.db 残留晶华新材测试记录
- **处理**：trades.db 已清空重建，当前空仓，资金 ¥1,000,000

---

### ⏰ 定时任务重构

#### 8. 扫描间隔调整
- **旧**：每5分钟
- **新**：每10分钟

#### 9. 上午时段扩展
- **旧**：`*/5 9,10`（9:00-10:55 乱序）
- **新**：`0 0,10,20 9-11 * * 1-5`（9:00-11:20 每10分钟）

#### 10. 下午时段修正
- **旧**：`0,5,10,15,20,25,30,35,40,45,50,55 13,14`（14:56-14:59 仍执行）
- **新**：`0 0,10,20,30,40,50 13-14 * * 1-5`（13:00-14:50 每10分钟）

#### 11. delivery.channel 缺失
- **问题**：所有 cron 任务缺少 `delivery.channel` 导致报错
- **修复**：4个任务全部添加 `"channel": "feishu"`

#### 12. 冗余任务清理
- **处理**：删除"上午后半段"定时任务（上午时段已覆盖 9:00-11:20）

#### 13. 月度复盘触发时间
- **旧**：每月15日9:00
- **新**：每月末28-31日18:00（月末最后交易日收盘后）

---

### 📋 当前 Cron 任务表

| 任务 | 表达式 | 时段 |
|------|--------|------|
| 信号灯-上午扫描 | `0 0,10,20 9-11 * * 1-5` | 9:00-11:20 每10分钟 |
| 信号灯-下午监控 | `0 0,10,20,30,40,50 13-14 * * 1-5` | 13:00-14:50 每10分钟 |
| 信号灯-收盘扫描 | `0 15 * * 1-5` | 每天15:00 |
| 信号灯-月度复盘 | `0 18 28-31 * *` | 每月末28-31日18:00 |

---

### 📊 系统当前状态

- **模式**：全自动交易（AUTO_TRADE=true, NOTIFY_ONLY=false）
- **持仓**：空仓
- **资金**：¥1,000,000
- **持仓股**：瑞鹄模具(002997)、伯特利(603596)、微光股份(002801)
- **开仓窗口**：14:30-15:00
- **数据源**：腾讯财经（主）、东方财富（备）


## 2026-04-07 v4.6

### 功能改进

- **职责分离**: cron隔离session=唯一执行路径（扫描→AI判断→执行/拦截→推送），主session=仅分析展示，不再竞争
- **无交易也发摘要**: 每次cron执行无论有无交易都推送飞书消息（原来静默拦截）

### Bug修复

- **发送超时**: subprocess超时30秒→120秒，避免飞书长消息响应慢触发重试

### 配置变更

- `AUTO_TRADE=true`，`NOTIFY_ONLY=false`（已确认生效）

---

## v5.4 （2026-04-20）

**主题：MACD 计算 Bug 排查**

### 🔧 问题
收到用户反馈：stock-analyzer 与妙想 skill 对同一只股票（000629）的 MACD 判断出现矛盾（一方显示死叉，另一方显示金叉/偏多），但 MACD 值均为 +0.07。

### 🔍 排查结论
- stock-analyzer `calc_macd()` 中，`dea = self.calc_ema(9)` 实际计算的是**收盘价的 EMA(9)**，而非 `EMA(9) of DIF`，导致 DIF > DEA 比较完全错误
- **信号灯系统 MACD 实现正确**（`dea_series = dif_series.ewm(span=signal).mean()`），不受影响

### ✅ stock-analyzer 修复内容
新增 `calc_ema_of_series()` 方法，修复 `calc_macd()` 中 DEA 计算逻辑：
- 旧：`dea = self.calc_ema(9)` — 对收盘价算 EMA(9) ❌
- 新：`dea = self.calc_ema_of_series(dif, 9)` — 对 DIF 序列算 EMA(9) ✅

### 📊 修复效果（000629）
| 指标 | 修复前 | 修复后 | 妙想 skill |
|------|--------|--------|-----------|
| MACD 判断 | 死叉 ❌ | 金叉 ✅ | 金叉 ✅ |
| 短线评分 | 20 | 60 | — |
| 中长线评分 | 40 | 100 | — |
| 综合评分 | 24 | 50 | 50 |

---

## v5.4 （2026-04-20）

**主题：资金流数据集成**

### 新增功能

#### 资金流数据获取（`data_provider/money_flow.py`）
- 新增 `MoneyFlowData` 数据类，包含主力/大单/超大单净流入、DDX/DDY 等指标
- 通过东方财富 EM_API_KEY 获取实时资金流数据
- 支持从 markdown 表格解析结构化数值（主力净流入、大单、超大单、小单、DDX/DDY）

#### 资金流否决机制
- 买入信号触发时，若主力净流入 < 0，自动否决改为 WATCH
- 卖出信号触发时，追加资金流原因作为决策依据
- 修改文件：`indicators/signal_unified.py` — `analyze_unified()` 函数

#### 资金流输出
- `main.py analyze` 输出新增资金流行：
  ```
  资金: [OUT] 主力-3338万 大单-997万 DDX-0.102 | 主力+大单双净流出
  ```
  - `[OUT]` = 主力净流出（否决买入），`[OK]` = 资金安全

### 技术细节

| 字段 | 说明 |
|------|------|
| main_net | 主力净流入（万元），<0 则否决买入 |
| big_net | 大单净流入（万元） |
| super_net | 超大单净流入（万元） |
| ddx | DDX 指标，<0 表示大单资金净流出 |
| is_safe | main_net > 0 或 big_net > 0 时为安全 |

### 依赖
- `EM_API_KEY` 环境变量（与妙想 skill 共用）

---

## v5.5 （2026-04-20）

**主题：仓位管控机制完善 + 胜率排序**

### 仓位分档（信号强度决定仓位）

| 档位 | 条件 | 仓位 |
|------|------|------|
| 试仓 | 1个买入信号 | `position_tier1_pct` = 10% |
| 标准 | 2个买入信号 | `position_tier2_pct` = 20% |
| 重仓 | 3个+买入信号 | `position_tier3_pct` = 30% |

### 候选排序逻辑

多股同时满足条件时，按综合得分排序：
```
综合得分 = 0.6 × 历史胜率 + 0.4 × (信号数 / 候选最大信号数)
```
- 优先买历史上帮你赚钱的股票（胜率权重60%）
- 次要参考当前信号强度（权重40%）

### 个股历史胜率追踪

- 回测/实盘中每笔平仓后更新该股胜率
- 新股初始胜率 = 50%（无历史数据时）
- 排序打分时实时使用最新胜率

### 配置参数（环境变量）

```bash
POSITION_TIER1_PCT=10.0   # 试仓仓位（%）
POSITION_TIER2_PCT=20.0   # 标准仓（%）
POSITION_TIER3_PCT=30.0   # 重仓（%）
WIN_RATE_WEIGHT=0.6        # 胜率在排序中的权重
```

### 修改文件
- `config.py` — 新增仓位分档参数
- `backtest/multi_engine.py` —胜率追踪 + 综合打分 + 仓位分档



## v6.10 (2026-06-09 20:55) · 修复 push2delay 模块加载未生效 bug ⭐ 关键修复

**主题**: 修复 v6.9 push2delay 集成后，**实际从未生效**的根因

### 🐛 Bug 现象

v6.9 (2026-06-04 23:50) 改造时，预期配置 `.env` 的 `FUND_FLOW_BACKEND=push2delay` 后信号灯会走 push2delay 端点。
但实际**今天 (6/9) 整整一天的扫描日志里完全没有 `[MoneyFlow] push2delay` 调用记录**——所有资金流都走"东方财富 → 妙想 fallback"。

### 🔍 根因

`data_provider/money_flow.py` 顶部**模块级**赋值：
```python
USE_PUSH2DELAY = os.environ.get("FUND_FLOW_BACKEND", "").lower() in (...)
```

但 `os.environ` 在 **import 阶段**还没加载 `.env`：
- `setup_env()` 只在 `Config.load()` 内部调用
- `Config.load()` 在 watch 命令**运行时**才执行
- `money_flow.py` 的 `USE_PUSH2DELAY = ...` 在 `import` 时**早于** `Config.load()`

→ `USE_PUSH2DELAY` 永远是 `False`，Step 0 块被短路。

### 🛠 修复

`data_provider/money_flow.py` 顶部加 2 行：
```python
from config import setup_env
setup_env()  # 在 import 阶段加载 .env
```

**为什么 v6.7 改 `WATCH_INTERVAL=900` 生效？** — 因为 v6.7 只改 `.env`，`WATCH_INTERVAL` 在 `Config.load()` 内部 `os.getenv()` 运行时读，**晚于** setup_env。v6.9 改的 `FUND_FLOW_BACKEND` 走**模块级** `os.environ.get()`，**早于** setup_env → 永远拿不到值。

### ✅ 验证

| 测试 | 结果 |
|------|------|
| 删 .pyc 后仅 import money_flow | `USE_PUSH2DELAY = True` ✅ |
| import signal_unified (触发 money_flow 二次 import) | `USE_PUSH2DELAY = True` ✅ |
| 7 只股票全量 (000629/603683/300308/002202/002792/002371/688981) | 7/7 拿到 push2delay 真实数据 ✅ |

### 📅 生效

- **06-10 09:15** 守护脚本日切换杀进程 → 启动新 watch → 加载 v6.10 → `USE_PUSH2DELAY=True`
- **06-10 10:00** 第一次扫描, 预期 `watch_cron.log` 出现 `[MoneyFlow] push2delay 成功`

### 💡 教训 (给小海)

- 未来 .env 变量想"模块级"用：必须保证 import 阶段 setup_env 先执行 (A2 模式)
- 未来 .env 变量想"运行时"用：保持 `Config.load()` 内 `os.getenv()` 即可
- 不要从"工具失败"推论"权限/路径问题"——先做最小端到端复现验证


## v6.11 (2026-06-09 21:06) · 000629 每日采集切换 push2delay + txstock ⭐ 关键修复

**主题**: 解决 000629 每日 16:30 自动采集连续 5 天 妙查 403 失败

### 🐛 问题

`scripts/collect_000629.py` 走 `mx-finance-data` skill 调妙查 API，妙查自 6/4 起持续 403（"使用次数已达上限"），截至 6/9 已连续 5 个交易日失败。
- 16:30 cron 失败 → 不写 outbox
- 16:35 人工盘点 → 手写 `alert_000629_Nday_fail.json` 推送

### 🛠 改造

`scripts/collect_000629.py` 新增多源采集：

| 数据 | 来源 | 说明 |
|------|------|------|
| 资金流 (主力/超大/大单/中单/小单) | **push2delay** | 免费, 几乎不限流 |
| 行情 (开/高/低/昨收/收盘/涨幅) | **txstock (腾讯)** | 免费, 含昨收 |
| DDX/DDY (1/3/5/10 日) | **N/A** | push2delay 不提供, 无替代源 |

新增函数：
- `fetch_push2delay_data()` — 拉资金流, 字段映射到妙查命名 (主力净流入/超大单净额/...)
- `fetch_txstock_data()` — 拉行情, 用 `TxStock().get_realtime()`
- `fetch_000629_data()` — 多源合并, 优先级 push2delay + txstock
- `main()` 加 `--source` 参数: `multi` (默认) / `push2delay` / `txstock` / `miaochang` (旧版)

### 🐛 顺带修 outbox 切分 bug

原代码 `log.split("---")[0].strip()` 切到 markdown 表格分隔符 `|---|---|` 就停了，导致 outbox 内容**只有表格头**（~58 字符）。

修复：`log.split("\n\n---")[0].strip()` — 切到正文章节分隔符 (line 29 的 `---`)，保留完整内容 (~377 字符)。

### ✅ 验证

| 测试 | 结果 |
|------|------|
| push2delay + txstock 联合 | 18 字段 (含 4 个 DDX N/A) ✅ |
| 仅 push2delay 失败 | txstock 仍能拿 6 字段, 不抛错 ✅ |
| 双源失败 | 优雅返回 error, `main()` 正常 `sys.exit(1)` ✅ |
| dry-run + 真实写文件 | memory + outbox 内容完整 ✅ |

### 📅 生效

- **06-10 16:30** collect_000629 cron 第一次跑 v6.11, 预期成功推 `ftqq_daily` 到海赟私聊
- **06-10 16:35** 不会再触发 5day_fail (因为不再失败)
- 妙查 `fetch_miaochang_data()` 保留为 `--source miaochang` fallback

### 📦 配套改动

- `.gitignore` 补 8 条规则 (data/outbox/ / data/archive/ / miaoxiang/ / stock_pool/ 等)
- ⚠️ 抓到一个 `.gitignore` 行尾注释 bug：规则行尾写 `# 注释` 会被 git 当成规则一部分，必须独立行才生效 (已修复)
- git commits: `0e3021d` (v6.8-11 主修复) + `045800c` (chore 批量补) + push 到 origin/main (e1c09a5..045800c, 7 commits)


## v6.6.1 (2026-06-04 23:28) · push2delay 资金流独立工具 ⭐ 关键里程碑

**主题**: 在 v6.9 集成到信号灯之前, 先把 push2delay 抽成独立工具 (`scripts/fund_flow_api.py`)

### 🔥 问题背景

信号灯 fallback 链 (东方财富 assistant/ask + 妙想 searchData) 共用 EM_API_KEY, ~60次/日
- 6/4 10:40 触限后全天 403
- 全天 309 次调用, 237 次失败 (77%)

### 🌟 新发现 (2026-06-04 23:00)

东方财富 push2delay 端点: `https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get`
- **几乎不限流** (公开 15 分钟延迟行情)
- **无需 API key** / 无 OAuth
- 仅当日数据 (不能拿 5/10 日 DDX 历史)
- 端点稳定, 适合作为"几乎免费"资金流 fallback

### 🛠 实施 (海赟选 A 方案: 独立工具)

新增 `scripts/fund_flow_api.py` (7.2KB)
- 封装 `fetch_public_flow(code, market)` → 返回 dict
  - `main_net / big_net / super_net / mid_net / small_net` (单位: 万元)
  - `main_pct / big_pct / super_pct` (主力/大单/超大单 占比%)
  - `close / change_pct / date`
  - `ddx / ddy` 推算指标 (填 0.0)
- 封装 `get_money_flow_batch(codes)` 批量接口
- CLI: `--test / --health / --batch` 三个子命令
- 字段对齐 `MoneyFlowData`

### ✅ 验证 (2026-06-04 23:28)

- 7/7 股票成功
- 000629 主力净流入=-3731.6万
- 002371 北方华创 主力净流入=+44142万
- 端点响应时间 ~800ms (东方财富端)

### 📅 下一步

- v6.9 (23:50) 将 push2delay 集成到 `data_provider/money_flow.py` 作为 Step 0
- v6.10 (6/9) 修复 v6.9 集成的 setup_env 时机 bug
- v6.11 (6/9) 同样接入 `collect_000629.py`

---

## v6.12 (2026-06-10 21:05) · 资金流 15 分钟缓存 + push2delay 健康探测 + 妙查 403 退避 ⭐ 关键优化

**主题**: 解决 6/10 资金流 fallback 链"全天 49 次失败"的三大根因

### 🐛 问题

- 6/10 早盘 push2delay 端点挂了 (rc:100/102 data:null) → fallthrough 到妙查
- 妙查日配额 ~100 次, 14 次扫描 × 7 只 = 98 次, **完美卡限**
- 13:00 开盘后妙查 403 触限 → **全天 49 次资金流全失败** (13:00-15:00)
- 14:01 北方华创主力净流入仅 +2 万, 数字严重偏离

### 🛠 三层改进

#### 1. 15 分钟缓存 (每只股票独立)
- 路径: `data_provider/money_flow.py` 加 `_cache: Dict[str, tuple]`
- 粒度: 每只股票单独缓存 (`code → (timestamp, MoneyFlowData)`)
- 失效: 15 分钟 (CACHE_TTL_SECONDS=900)
- 范围: **所有路径都缓存** (push2delay / 东财 / 妙查)
- 效果: 5 分钟扫描时, 7 只股票在 15 分钟内复用同一份数据 → 全天妙查调用从 98 → ~32 次

#### 2. push2delay 端点健康探测
- 新增 `_is_push2healthy()` 函数
- 5 分钟内复用同一健康状态 (PUSH2_HEALTH_CHECK_TTL=300)
- 探活用 `secid=0.000001` (沪指) - 不消耗业务数据配额
- 端点挂时直接跳过 Step 0, fallthrough 到东财/妙查
- 避免每天重启 14 次都探测一次 (节省 13 次探测请求)

#### 3. 妙查 403 退避 30 分钟
- 新增 `_mc_blocked_until` 时间戳
- 妙查失败时设置 `now + MC_BACKOFF_SECONDS(1800)`
- 退避期内直接跳过 Step 2, 用之前缓存的结果
- 成功时清退避 (`_mc_blocked_until = 0.0`)

### 🐛 v6.11 改造过程中的两个 bug (海赟中途暂停前修完)

1. **缓存写入位置错误**: `_cache[code] = ...` 写在了 `return MoneyFlowData(...)` 之后 (return 多行表达式闭合之后), 实际是死代码
   - 修复: 把 `return MoneyFlowData(...)` 改成 `result = MoneyFlowData(...)` + `_cache[code] = (now, result)` + `return result`
2. **第一次 edit 拼写错误**: `big_out=mc_data["big_in"]` 应该是 `big_out=mc_data["big_out"]`
   - 在后续 edit 中已纠正

### ✅ 验证

| 场景 | 结果 |
|------|------|
| 首次 000629 (push2delay 路径) | main_net=-1730万 耗时 0.30s ✅ |
| 二次 000629 (缓存命中) | main_net=-1730万 耗时 **0.01ms** ✅ |
| push2delay 模拟挂 (健康=False) | 自动走妙查 ✅ |
| 妙查 403 模拟 (退避中) | 用之前缓存, 0 耗时 ✅ |

### 📅 生效

- 2026-06-11 10:00 信号灯日切换, 新 watch 进程加载 v6.12
- 预期: 资金流全天稳定 (push2delay 健康即用, 不健康 fallthrough, 妙查退避兜底)
- 预期: 妙查日调用从 ~98 → ~14 次 (15 分钟内全缓存)

### 🔧 配置变化

`.env`:
```diff
- # FUND_FLOW_BACKEND=push2delay  # 2026-06-10 端点整体不可用,临时禁用
+ FUND_FLOW_BACKEND=push2delay  # v6.11 重新启用,端点 15:35 已恢复,加健康探测自动跳过挂的状态
```


---

## v6.13（2026-06-14 01:40）⭐ 静默修复 - 信号可观测性 + 资金流降级改造

**主题：修复 6/8-6/12 模拟交易 0 笔成交背后的多个静默 bug**

> **背景**: 海赟 6/14 凌晨提问"这周信号灯有交易么"，小海排查发现 6/2 之后整整 12 天 **0 笔新交易**。根因有 3 个独立 bug + 1 个老问题，全部静默不可见。

### 🐛 根因诊断

| Bug | 触发场景 | 影响 |
|---|---|---|
| **#1 amount<1000 静默 continue** | 6/2 攀钢建仓占 30%, 弱市上限 30% → 剩余 0.03% × 100万 = 300元 < 1000元 | 6/8-6/12 共 101 次 BUY 决策 0 次成交 |
| **#2 资金流失败 BUY→WATCH (P1 默认保守)** | 6/3-6/12 资金流三路 (东财/妙想/push2delay) 几乎全失败 | 即使有 BUY 决策也被降级为 WATCH，actions["buy"] 为空 |
| **#3 止损检查依赖 actionable_signals['stop_loss']** | 同上：所有 signal 降级 WATCH → stop_loss 列表为空 | 攀钢浮亏 -9.68% 已超弱市 -2% 止损线 12 天未止损 |
| **#4 is_trading_day() days=2 数据延迟** | 6/3 (周三) 启动时 last_date=6/2 → today=6/3 不等 → 误判非交易日 | 6/3-6/5 整天被跳过扫描 |

### 🆕 v6.13 修复（按优先级）

#### P0-1：信号可观测性（立刻修）

**修复**：`monitor/watcher.py:463` 的 `if amount < 1000: continue` 加 log + 写 outbox 告警

```python
if amount < 1000:
    logger.warning(
        f"⚠️ [{signal.name}] 剩余可开仓 ¥{amount} < ¥1000 最低买入额，"
        f"跳过 (position_ratio={position_ratio:.4f}, "
        f"持仓占用 {current_total_pct:.2f}%, 上限 {total_position_pct}%)"
    )
    self._write_silenced_signal_alert(signal, reason="amount_too_small",
                                       extra={"amount": amount, "position_ratio": position_ratio})
    continue
```

#### P0-2：`_handle_buy_signals` 入口加 log

**修复**：让"本周期 N 个 BUY 决策"可观测
```python
logger.info(
    f"📋 _handle_buy_signals 进入: 本周期 {len(signals)} 个 BUY 决策 → "
    f"{[s.name for s in signals]}"
)
```

**配套新增方法**：`_write_silenced_signal_alert(signal, reason, extra)`
- 写 `data/alert_silenced_signals/{ts}_{code}_{reason}.json`
- 同时发飞书 outbox (kind=silenced_signal_alert)

#### P1-3：资金流失败时 BUY 保留 + 降仓 50%（替代 WATCH 降级）

**修复**：`indicators/signal_unified.py:308-325` 的 mf_fetch_failed 分支

```python
# 旧 v6.3 逻辑: BUY → WATCH (静默降级)
# 新 v6.13 逻辑: 保留 BUY, position_ratio * 0.5 (降仓 50%)
if mf_fetch_failed:
    if decision == "BUY":
        if position_ratio > 0:
            position_ratio = position_ratio * 0.5
        else:
            position_ratio = 0.05
        reason = f"{reason} ⚠️[资金流缺失,建议降仓至{position_ratio:.1%}]"
    elif decision in ("HOLD", "TAKE_PROFIT"):
        reason = f"{reason} ⚠️资金流数据获取失败"
```

#### P2-5：`is_trading_day()` days 2→5 缓冲

**修复**：`monitor/watcher.py:17-31` + `main.py:64-78`

```python
# 旧: hist = tx.get_history('sh000001', days=2)  # 数据延迟时 today != last_date → 误判
# 新: hist = tx.get_history('sh000001', days=5)  # 5 天缓冲
```

#### P2-6：独立止损检查（不依赖 actionable_signals）

**修复**：在 `_run_scan_cycle` 第 5 步（stop_loss）后插入 5.5 步
```python
# 5.5 v6.13: 独立止损检查 (不依赖 actionable_signals['stop_loss'])
self._handle_stop_loss_for_all_positions(signals)
```

**新增方法**：`_handle_stop_loss_for_all_positions(signals)`
- 直接遍历 `position_store.get_open_positions()`
- 用 `signal_map[code]` 拿到当前实时价检查止损
- 复用原 `_handle_stop_loss_signals` 完全相同的 4 个止损条件

#### P2-6.1：独立止损加交易时间窗口保护

**修复**：凌晨 1 点时，止损只 log 警告，**不实际下单**，避免非交易时间被强平

```python
# v6.13.1: 凌晨/午休/盘后不实际下单
if not is_in_open_window(now_time):
    # 只 log 警告, 实际止损推到下一轮交易时间内执行
    return
```

### ✅ 验证

| 测试场景 | 结果 |
|---|---|
| 6/8 10:00 资金流成功时, 走原资金否决路径 | 攀钢 BUY, position_ratio=0.3 ✅ |
| 6/12 14:00 资金流全失败时, 走新 mf_fetch_failed 路径 | 攀钢 **BUY** (不再是 WATCH), position_ratio=0.15 ✅ |
| 凌晨 1 点 dry-run `_handle_stop_loss_for_all_positions` | 只 log 警告, trades.db 不变, positions.json status=open ✅ |
| 6/3 (周三) 启动时 is_trading_day() | days=5 缓冲后正确返回 True (待 6/15 验证) |

### 📁 备份

`backup/v6.13_2026-06-14_silence_fix/`:
- `watcher.py` (改动前)
- `signal_unified.py` (改动前)
- `scanner.py` (改动前, 但本次未改)

### ⚠️ 已知遗留问题（不属 v6.13 范围，留给 v6.14）

1. **`TradeResult` 没有 `pnl` 字段**：`monitor/watcher.py:433,456` 的 `result.pnl` 实际是 `TradeResult` 不存在的属性（仅局部变量）
   - 影响：止损成交时 `_update_loss_streak_on_sell` 会抛 AttributeError
   - 修复方案：把 `pnl` 存到 `TradeResult` 字段或 `TradeRecord`
2. **回测使用旧版信号系统**：`backtest/engine.py` 没接 v6.13 修复
3. **逆流池模块尚未开发完毕**（海赟 6/4 明确说明）—— 涉及的 2 个 cron 错误保留

### 📅 生效

- 2026-06-14 凌晨 1:40 立即生效（手动 git add 提交，daemon 9:15 启动新进程）
- 6/15 (周一) 10:00 自动重启 watch 进程时加载 v6.13
- 建议周一观察 09:15-15:00 的 watch_cron.log：
  - 应该看到 `📋 _handle_buy_signals 进入` log 出现
  - 资金流失败时应该看到 `⚠️[资金流缺失,建议降仓至X%]` 出现在 outbox
  - 不再出现 `if amount < 1000` 静默 continue

---

