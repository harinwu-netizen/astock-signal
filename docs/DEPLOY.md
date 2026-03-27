# 📖 信号灯 · 部署上线使用说明书

> 版本: v2.0
> 日期: 2026-03-27
> 简称: 信号灯

---

## 一、项目概述

### 1.1 是什么

**信号灯**是一款专为A股散户设计的自动化量化交易辅助工具，通过规则化的信号系统帮助用户克服追涨杀跌、频繁交易不止损等人性弱点。

**核心特点**：全自动交易 + AI增强报告，交易完全自动化，AI仅作事后参考，不参与决策。

### 1.2 核心功能

```
📊 股票池监控     自选股批量实时监控
📈 10+6信号系统   量化买点/卖点计数
🛡️ ATR动态止损    自适应市场波动的止损
🌏 大盘温度计     大盘不好不开仓
🤖 自动交易      规则化执行，不等人
🤖 AI增强报告    交易后异步调用，不阻塞交易
🔔 飞书推送      信号/持仓/预警实时推送
📋 Web仪表盘     持仓和信号一目了然
```

### 1.3 系统架构

```
数据源(腾讯财经/东方财富)
         ↓
   技术指标计算(MA/MACD/RSI/ATR)
         ↓
   10+6信号系统 → 交易决策矩阵
         ↓
   风控检查(7项) → 执行/拒绝
         ↓
   交易执行  ──────────────────┐
         ↓                      ↓
   持仓管理              AI增强报告（异步）
         ↓                      ↓
   通知推送 ←───────────── LLM大模型分析
```

### 1.4 设计原则

- **全自动交易**：信号灯负责交易决策，AI不参与
- **AI仅作增强**：AI在交易后异步分析，仅供参考
- **规则优先**：所有操作基于明确规则，不依赖主观判断
- **零幻觉**：信号系统无AI的幻觉风险

---

## 二、快速部署

### 2.1 环境要求

```
Python 3.11+ (推荐 3.12)
Linux / macOS / Windows
网络访问（获取股票数据）
```

### 2.2 安装步骤

第一步：克隆项目

```bash
mkdir -p ~/astock-signal && cd ~/astock-signal
git clone <your-repo-url> .
```

第二步：安装依赖

```bash
pip install --break-system-packages -r requirements.txt
```

第三步：配置

```bash
cp .env.example .env
nano .env
```

第四步：验证安装

```bash
python main.py pool list
python main.py analyze 000629
```

---

## 三、配置说明

### 3.1 .env 完整配置

```bash
# ===== 账户设置 =====
TOTAL_CAPITAL=100000
MAX_POSITIONS=3
SINGLE_TRADE_LIMIT=20000

# ===== 交易规则 =====
BUY_SIGNAL_THRESHOLD=5
SELL_SIGNAL_THRESHOLD=3
STOP_LOSS_PCT=10.0
TAKE_PROFIT_PCT=15.0
ATR_STOP_MULTIPLIER=2.0

# ===== 开仓时间窗口 =====
OPEN_WINDOW_START=14:30
OPEN_WINDOW_END=15:00

# ===== 风控 =====
MARKET_CRASH_THRESHOLD=-2.0

# ===== 通知 ======
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

# ===== 交易开关 ======
NOTIFY_ONLY=true
AUTO_TRADE=false

# ===== 大模型配置（AI增强）=====
LLM_ENABLED=false
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=
LLM_BASE_URL=
LLM_TIMEOUT=30
LLM_MAX_TOKENS=1000
LLM_TEMPERATURE=0.3
```

### 3.2 飞书 Webhook 配置

1. 打开飞书群 → 设置 → 群机器人
2. 添加机器人 → 自定义机器人
3. 复制 Webhook 地址，填入 FEISHU_WEBHOOK_URL

### 3.3 大模型配置

支持6个国内主流大模型：

| Provider | 模型 | 默认模型名 | API地址 |
|----------|------|-----------|---------|
| deepseek | DeepSeek | deepseek-chat | api.deepseek.com |
| zhipu | 智谱AI | glm-4-flash | open.bigmodel.cn |
| doubao | 豆包 | doubao-pro | ark.cn-beijing.volces.com |
| qwen | 通义千问 | qwen-plus | dashscope.aliyuncs.com |
| minimax | MiniMax | MiniMax-Text-01 | api.minimax.chat |
| openai | OpenAI兼容 | gpt-4o-mini | api.openai.com |

配置示例：

```bash
# 启用LLM
python main.py settings --llm-enabled=true

# 配置DeepSeek
python main.py settings --llm-provider=deepseek --llm-api-key=sk-xxx

# 查看状态
python main.py llm status

# 测试分析
python main.py llm test --code 000629
```

---

## 四、使用指南

### 4.1 命令行命令

股票池管理

```bash
python main.py pool list              # 查看股票池
python main.py pool add 000629        # 添加股票
python main.py pool remove 000629     # 删除股票
python main.py pool disable 000629    # 禁用监控
python main.py pool enable 000629     # 重新启用
```

分析

```bash
python main.py analyze 000629          # 分析单只股票
python main.py analyze --pool          # 分析股票池所有股票
```

监控（重点！）

```bash
python main.py watch                  # 启动实时监控（14:30-15:00）
python main.py watch --continuous     # 持续监控（测试用）
```

持仓与报告

```bash
python main.py position               # 查看当前持仓
python main.py report                # 收盘复盘报告
```

大模型

```bash
python main.py llm status             # 查看LLM状态
python main.py llm test --code 000629  # 测试AI分析
```

设置

```bash
python main.py settings               # 查看所有设置
python main.py settings --auto-trade=false
python main.py settings --llm-enabled=true --llm-provider=deepseek --llm-api-key=sk-xxx
```

### 4.2 交易时间段

- 09:15-09:25 — 开盘竞价（信号灯不交易）
- 09:30-11:30 — 上午交易（信号灯不交易）
- 13:00-14:30 — 下午时段（信号灯不交易）
- 14:30-15:00 — ⭐ 开仓窗口（信号灯主要交易时段）

---

## 五、大模型（AI增强）

### 5.1 设计理念

**AI不参与交易决策，仅作事后增强报告。**

```
交易执行 → 推送交易通知 → 异步调用AI → 推送AI分析
```

- 全自动交易：信号灯规则触发，即时执行
- AI分析：交易后异步进行，不影响交易速度
- 你收到：交易执行通知 + AI详细分析报告

### 5.2 AI分析内容

交易完成后，AI会自动分析：

- 当前技术形态判断
- 值得注意的风险点
- 后市操作建议

### 5.3 最佳实践

```
第一步: NOTIFY_ONLY=true, AUTO_TRADE=false, LLM_ENABLED=false
→ 只看信号，不交易，观察3-5天

第二步: NOTIFY_ONLY=false, AUTO_TRADE=true, LLM_ENABLED=true
→ 开启自动交易 + AI增强报告

第三步: 发现问题随时关闭
python main.py settings --auto-trade=false
```

---

## 六、自动交易配置

### 6.1 风控规则（P0-P3）

P0 强制止损（不可绕过）
- 大盘单日跌幅 > 2% → 全仓强平
- 单股持仓亏损 > 10% → 强制止损

P1 禁止开仓
- 弱势市场（大盘MA5 < MA10）
- 非开仓时间段（14:30前）
- 同一股票同日已交易

P2 仓位限制
- 单只仓位 ≤ 总资金30%
- 最大持仓数 ≤ 3只
- 总仓位 ≤ 80%

P3 信号要求
- 买入：信号 ≥ 5
- 卖出：信号 ≥ 3

### 6.2 交易决策矩阵

无持仓时：
- BUY ≥ 5 → 买入30%
- BUY ≥ 6 → 买入50%
- BUY ≥ 7 → 买入80%

已有持仓时：
- SELL ≥ 3 → 卖出50%
- SELL ≥ 4 → 卖出全
- SELL ≥ 5 → 全卖

弱势市场：禁止开仓，已有持仓减仓/全卖
大盘跌 > 2%：禁止开仓，已有持仓强卖

---

## 七、Web 界面

### 7.1 启动 Web 服务

```bash
cd ~/astock-signal
python web/app.py
# 访问 http://localhost:8080
```

### 7.2 API 端点

- GET / — Web仪表盘
- GET /api/portfolio — 账户总览
- GET /api/positions — 持仓列表
- GET /api/watchlist — 股票池
- GET /api/signals — 信号扫描
- GET /api/market — 大盘状态
- GET /api/settings — 当前设置
- GET /health — 健康检查

---

## 八、定时任务

### 8.1 Crontab 配置

```bash
# 安装定时任务
bash scripts/install_cron.sh

# 手动编辑
crontab -e

# 添加任务：
25 14 * * 1-5 cd ~/astock-signal && python main.py watch >> logs/watch.log 2>&1
10 15 * * 1-5 cd ~/astock-signal && python main.py report >> logs/report.log 2>&1
20 9 * * 1-5 cd ~/astock-signal && python main.py analyze --pool >> logs/morning.log 2>&1
```

### 8.2 定时任务说明

- 09:20 — 开盘前分析
- 14:25 — 启动监控
- 15:10 — 收盘报告

---

## 九、Docker 部署

### 9.1 构建镜像

```bash
cd ~/astock-signal
docker build -t astock-signal .
```

### 9.2 运行容器

```bash
mkdir -p ~/astock-signal-data
docker run -d \
  --name astock-signal \
  -v ~/astock-signal-data:/app/data \
  -p 8080:8080 \
  -e FEISHU_WEBHOOK_URL="https://..." \
  -e LLM_ENABLED=true \
  -e LLM_PROVIDER=deepseek \
  -e LLM_API_KEY="sk-xxx" \
  astock-signal
```

### 9.3 常用命令

```bash
docker stop astock-signal
docker start astock-signal
docker logs -f astock-signal
docker restart astock-signal
```

---

## 十、故障排查

### 常见问题

Q: 飞书收不到推送？
1. 检查 FEISHU_WEBHOOK_URL 是否正确
2. 检查网络能否访问 open.feishu.cn
3. 确认机器人没有被移除

Q: 自动交易没有执行？
1. 确认 NOTIFY_ONLY=false, AUTO_TRADE=true
2. 确认当前在14:30-15:00之间
3. 检查是否有持仓（已持仓则不会再买）

Q: LLM分析失败？
1. 确认 LLM_ENABLED=true
2. 确认 LLM_API_KEY 正确
3. 检查 API是否欠费/额度用完
4. 查看 python main.py llm status

### 日志位置

- logs/watch.log — watch模式运行日志
- data/trades.db — 交易记录数据库
- data/positions.json — 持仓记录

---

## 十一、快速开始 checklist

☐ 1. 安装依赖
pip install --break-system-packages -r requirements.txt

☐ 2. 配置 .env
cp .env.example .env
填入 FEISHU_WEBHOOK_URL

☐ 3. 添加股票
python main.py pool add 000629
python main.py pool add 600519

☐ 4. 验证运行
python main.py analyze --pool
确认有信号输出

☐ 5. 配置LLM（可选）
python main.py settings --llm-enabled=true --llm-provider=deepseek --llm-api-key=sk-xxx
python main.py llm test --code 000629

☐ 6. 开启自动交易（可选）
观察3天后确认信号可靠
python main.py settings --notify-only=false --auto-trade=true

---

## 十二、风险声明

⚠️ 重要

1. 自动交易默认关闭，观察3天信号质量后再开启
2. 单笔限额2万，防止一次失误过大
3. 大盘暴跌>2%自动强平，不可绕过
4. 本工具仅供个人学习研究，股票投资有风险，决策权在用户，承担一切后果

---

信号灯 · 让投资更理性
文档版本: v2.0 | 2026-03-27