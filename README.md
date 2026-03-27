# 🏮 A股信号灯

> A股自动化量化交易辅助工具 · 全自动交易 + AI增强报告

[![GitHub Stars](https://img.shields.io/github/stars/harinwu-netizen/astock-signal?style=flat)](https://github.com/harinwu-netizen/astock-signal)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

## 简介

信号灯是一款专为A股散户设计的自动化量化交易辅助工具，通过规则化的信号系统帮助用户克服追涨杀跌、频繁交易不止损等人性弱点。

**核心特点**：全自动交易 + AI增强报告，交易完全自动化，AI仅作事后参考，不参与决策。

## 核心功能

- 📊 **股票池监控** — 自选股批量实时监控
- 📈 **10+6信号系统** — 量化买点/卖点计数
- 🛡️ **ATR动态止损** — 自适应市场波动的止损
- 🌏 **大盘温度计** — 大盘不好不开仓
- 🤖 **自动交易** — 规则化执行，不等人
- 🤖 **AI增强报告** — 交易后异步调用，不阻塞交易
- 🔔 **飞书推送** — 信号/持仓/预警实时推送
- 📋 **Web仪表盘** — 持仓和信号一目了然

## 系统架构

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

## 快速开始

### 安装依赖

```bash
git clone https://github.com/harinwu-netizen/astock-signal.git
cd astock-signal
pip install --break-system-packages -r requirements.txt
cp .env.example .env
```

### 配置

编辑 `.env`，填入飞书 Webhook 地址：

```bash
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

### 使用

```bash
# 查看股票池
python main.py pool list

# 添加股票
python main.py pool add 000629
python main.py pool add 600519

# 分析股票
python main.py analyze --pool

# 启动监控（14:30-15:00）
python main.py watch

# 查看设置
python main.py settings
```

## 10+6信号系统

### 十大买入信号（满足≥5个触发买入）

| # | 信号 | 触发条件 |
|---|------|---------|
| 1 | MA多头排列 | MA5>MA10>MA20 |
| 2 | 价格回踩MA5 | 收盘价≤MA5×1.01 |
| 3 | 价格回踩MA10 | 收盘价≤MA10×1.01 |
| 4 | MACD金叉 | DIF上穿DEA |
| 5 | MACD零轴上方 | DIF>0 且 DEA>0 |
| 6 | RSI超卖反弹 | RSI(6)<30 且拐头向上 |
| 7 | 缩量回调 | 量比<0.7 |
| 8 | 乖离率修复 | bias_MA5在2-5%回调中 |
| 9 | 筹码集中 | 换手率<5% |
| 10 | 大盘配合 | 上证MA5>MA10 |

### 六大卖出信号（满足≥3个触发卖出）

| # | 信号 | 触发条件 |
|---|------|---------|
| 1 | MA空头排列 | MA5<MA10<MA20 |
| 2 | 乖离率过大 | bias_MA5>5% |
| 3 | MACD死叉 | DIF下穿DEA |
| 4 | RSI超买 | RSI(6)>75 |
| 5 | 放量破位 | 量比>1.8 且跌破MA10 |
| 6 | ATR止损 | 跌破买入价-2×ATR |

## 大模型配置（可选）

支持6个国内主流大模型：

```bash
# 配置 DeepSeek
python main.py settings --llm-enabled=true --llm-provider=deepseek --llm-api-key=sk-xxx

# 测试
python main.py llm test --code 000629
```

支持的Provider：deepseek / 智谱 / 豆包 / 通义千问 / MiniMax / OpenAI

## 文档

- 📖 [部署上线使用说明书](https://feishu.cn/docx/MDwRdrXAjo2CTgxkUs3cnpHnnZf)
- 🔧 [技术设计说明书](https://feishu.cn/docx/KSjJdSWO6oqkCvx4j09cRYdwnxb)

## 风险声明

⚠️ **重要**

1. 自动交易默认关闭，观察3天信号质量后再开启
2. 单笔限额2万，防止一次失误过大
3. 大盘暴跌>2%自动强平，不可绕过
4. 本工具仅供个人学习研究，股票投资有风险，决策权在用户

## Star History

[![Star History Chart](https://api.star-history.com/svg?repo=harinwu-netizen/astock-signal&type=Date)](https://star-history.com/#harinwu-netizen/astock-signal&type=Date)

---

**信号灯 · 让投资更理性**
