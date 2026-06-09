# 逆流选股模块 — 设计说明书

> 版本：v1.0 | 日期：2026-05-12 | 状态：已上线
> 所属项目：astock-signal | 文件位置：`stock_pool/mx_nixflow_selector.py`

---

## 一、核心设计思想

### 1.1 什么是「逆流选股」

**逆流**（Counter-Flow）是一种基于主力行为识别的选股策略。

> 市场普跌时，大资金悄然流入；市场普涨时，主力反而派发。
> 识别这种「与大多数资金反向」的行为，在主力吸筹阶段买入，等待中期拉升。

核心逻辑：

```
近10日主力大单净流出（主力在卖）
+ 价格没有大跌（有人在接）
+ 底部逐步抬高（有人在悄悄吸）
= 主力在"拉高卖、砸下去接"的循环吸筹
→ 中期行情概率高
```

### 1.2 为什么会有效

- **大股东减持**需要通过券商大宗或做市派发，造成大单净流出假象
- 市场恐慌时散户抛售，主力低位吸筹
- 主力完成吸筹后，需要拉升出货，因此中期有行情
- 逆流策略在「主力派发但价格不跌」中识别这一信号

---

## 二、整体框架

```
┌─────────────────────────────────────────────────────────┐
│                    逆流选股系统                          │
│                                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │   Stage 1    │ → │   Stage 2    │ → │  Stage 3   │ │
│  │  妙想MCP查询  │   │  L1量化筛选   │   │ AI深度评估 │ │
│  │  (全市场~2200 │   │ (本地计算     │   │ (MiniMax   │ │
│  │   只粗筛)     │   │  20只候选)    │   │  LLM分析)  │ │
│  └──────┬───────┘   └──────┬───────┘   └──────┬────┘ │
│         │                  │                  │       │
│  ┌──────▼──────────────────▼──────────────────▼────┐ │
│  │              候选股票池 (NixFlowPoolManager)     │ │
│  │  · 入池 / 出池 / 持仓标记                        │ │
│  │  · 每日盘后检查（池内淘汰）                       │ │
│  │  · 每周五全面维护（淘汰+补充）                    │ │
│  └──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 模块依赖关系

| 模块 | 文件 | 职责 |
|---|---|---|
| `MxNixflowSelector` | `mx_nixflow_selector.py` | Stage 1+2：妙想粗筛 + L1量化筛选 |
| `AIAnalyzer` | `ai_analyzer.py` | Stage 3：AI深度分析 |
| `NixFlowPoolManager` | `pool_manager.py` | 股票池动态管理 + 定时维护 |
| `NixFlowPoolRecord` | `pool_manager.py` | 股票池记录数据结构 |
| `data_selector` | `data_provider/data_selector.py` | K线数据获取（efinance） |

---

## 三、两阶段筛选机制

### Stage 1：妙想 MCP 全市场粗筛

调用东方财富妙想 MCP 接口，一次查询返回全市场股票：

```
条件：近10个交易日主力资金累计净流出 ≥ 3000万元
      A股，剔除ST和退市股
      按主力净流出金额从大到小排序
返回：约 1500~2200 只股票（每只含代码/名称/净流出金额等）
耗时：约 5~8 秒
```

### Stage 2：L1 本地量化筛选

对 Stage 1 返回的每只股票，批量获取K线数据，计算技术指标：

| 指标 | 计算方式 | 筛选阈值 |
|---|---|---|
| **价格区间** | 近10日最高/最低 | -3% ~ +5% |
| **底部抬高** | 最低价相对位置 | ≥ 0%（允许平底） |
| **振幅** | (最高-最低)/最低 | ≤ 20% |
| **MA20趋势** | 价格 > MA20 且 MA20向上 | 必须 above |
| **成交额** | 日均成交额 | ≥ 1000万元 |

L1 筛选后的股票按评分排序，输出 TOP 20。

### 评分模型

```
满分 100 分，山谷曲线设计（价格 0~3% 区间得分最高）：

score = 基础分（70~115分）
      + 价格偏离惩罚（涨幅>3% 或跌幅过大扣分）
      + 底部抬高加成（底部越高加分）
      + 振幅惩罚（振幅过大扣分）
```

### 信号类型分类

| 信号类型 | 条件 | 推荐 |
|---|---|---|
| ✅ 经典逆流 | 净流出 + 涨幅 0~3% | 是（AI评估） |
| ✅ 稳定逆流 | 净流出 + 涨幅 -3%~0% | 是（AI评估） |
| ⚠️ 强派发 | 净流出 + 涨幅 > 3% | 需AI判断 |
| ⚠️ 弱势逆流 | 净流出 + 跌幅 > 3% | 不推荐 |

---

## 四、AI 评估机制

### 分析维度

AI（MiniMax M2.7）从以下7个维度分析单只股票：

1. **减持进度** — 是否有大股东减持？是否接近尾声？
2. **业绩与基本面** — 业绩趋势如何？是否有超预期可能？
3. **行业政策** — 公司所在行业是否受益于近期政策？
4. **资金用途** — 若有减持，资金用途是什么？
5. **高管动态** — 近期高管是否有增持动作？
6. **限售解禁** — 近期是否有大额解禁？
7. **技术面** — 当前技术形态是否支持拉升？

### 输出格式

```json
{
  "中期行情概率": "高/中/低",
  "入池建议": "建议入池/不建议入池",
  "核心逻辑": "一句话描述",
  "分析摘要": "200字以内",
  "风险提示": "主要风险",
  "关键观察点": ["观察点1", "观察点2", "观察点3"]
}
```

### 入池决策

| AI 概率 | 入池建议 | 结果 |
|---|---|---|
| 高 | 建议入池 | ✅ 入池 |
| 中 | 建议入池 | ✅ 入池 |
| 低 | 不建议入池 | ❌ 不入池 |
| 高/中 | 不建议入池 | ❌ 不入池 |

---

## 五、股票池动态管理

### NixFlowPoolManager 职责

- 维护逆流股票池（上限10只）
- 持仓标记：已持仓股票不受淘汰规则影响
- 每日盘后检查（池内淘汰，不补充）
- 每周五全面维护（淘汰 + 市场重扫 + 入池补充）

### 数据结构 NixFlowPoolRecord

| 字段 | 说明 |
|---|---|
| `code` | 股票代码 |
| `name` | 股票名称 |
| `score` | 逆流评分 |
| `ai_prob` | AI评估概率（高/中/低） |
| `is_held` | 是否已持仓（持仓的不被淘汰） |
| `is_traded` | 是否已触发过交易信号 |
| `status` | 观察中 / 已持仓 / 已出池 |
| `exit_reason` | 出池原因 |
| `last_ai_date` | 上次AI评估日期 |
| `last_l1_date` | 上次L1检查日期 |

### 持仓标记接口

```python
from stock_pool.pool_manager import NixFlowPoolManager

pm = NixFlowPoolManager()
pm.mark_held("000629", held=True)   # 标记已持仓
pm.mark_held("000629", held=False)  # 取消持仓标记
pm.is_held("000629")               # 查询是否持仓
```

---

## 六、定时维护任务

### 每日盘后检查（周一~五 18:00）

```
1. 遍历池内所有股票（持仓除外）
2. 重新获取K线，验证L1条件是否仍然满足
   - 价格区间是否仍符合 -3%~+5%
   - 振幅是否仍 ≤ 20%
   - MA20趋势是否保持
   - 成交额是否仍 ≥ 1000万
3. AI概率="低"的直接淘汰
4. 不满足L1条件的淘汰
5. 不补充新股票
```

### 每周全面维护（周五 18:00）

```
1. 执行每日检查的所有淘汰逻辑
2. 调用 MxNixflowSelector.scan() 进行全市场扫描
3. 调用 AIAnalyzer.batch_analyze() 对候选股进行AI评估
4. 按评分排序，将 AI推荐 且 不在池中 的股票入池
5. 飞书推送每周维护报告
```

---

## 七、与信号灯系统对接

### 架构关系

```
信号灯系统（signal灯）
  ├── run_watch.py    — 每日盘中监控，信号触发交易
  ├── run_report.py   — 收盘报告
  └── ...
  │
  └── 逆流选股池（NixFlowPoolManager）
        ├── 每周五18点补充候选股票
        └── 观察池 → 可作为信号灯的候选池输入
```

### 对接方式

逆流池为信号灯系统提供**候选股票观察名单**：

1. **观察池股票**：作为信号灯的潜在买入候选
2. **持仓标记**：当信号灯买入某只股票后，调用 `mark_held()` 标记
3. **每日淘汰**：不符合条件的股票自动移出池，信号灯不再关注

信号灯在选股时，可以优先从逆流观察池中选取。

---

## 八、参数配置

### MxSelectorConfig（扫描配置）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `net_outflow_window` | 10 | 近N日资金流窗口 |
| `net_outflow_thresh` | 3000.0万 | 主力净流出阈值（万元） |
| `price_max_loss` | -3.0% | 价格最大跌幅限制 |
| `price_max_gain` | 5.0% | 价格最大涨幅限制 |
| `bottom_rise_min` | 0.0% | 底部抬高最小幅度 |
| `amplitude_max` | 20.0% | 振幅上限 |
| `ma20_required` | "above" | MA20趋势要求 |
| `avg_amount_min` | 1000.0万 | 日均成交额下限 |
| `max_candidates` | 20 | 输出TOP N候选供AI评估 |
| `fetch_workers` | 20 | K线批量获取并发数 |

### NixFlowPoolManager（池管理配置）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `MAX_POOL_SIZE` | 10 | 观察池上限 |
| `POOL_FILE` | `data/nixflow_pool.json` | 池数据文件路径 |

---

## 九、调用说明

### 快速扫描（纯量化，不含AI）

```python
import sys
sys.path.insert(0, '/root/.openclaw/workspace/astock-signal')

from stock_pool.mx_nixflow_selector import MxNixflowSelector, MxSelectorConfig, print_candidates

cfg = MxSelectorConfig(fetch_workers=20)
scanner = MxNixflowSelector(cfg)
candidates = scanner.scan()  # Stage 1: ~60秒

print_candidates(candidates)  # 打印结果
```

### 两阶段扫描（量化 + AI + 入池）

```python
import sys
sys.path.insert(0, '/root/.openclaw/workspace/astock-signal')

from stock_pool.mx_nixflow_selector import MxNixflowSelector, MxSelectorConfig
from stock_pool.ai_analyzer import AIAnalyzer
from stock_pool.pool_manager import NixFlowPoolManager

# Stage 1: 量化筛选
scanner = MxNixflowSelector(MxSelectorConfig(fetch_workers=20))
candidates = scanner.scan()  # ~75秒

# Stage 2: AI评估
ai = AIAnalyzer()
today = '2026-05-12'
results = ai.batch_analyze(
    [scanner._candidate_to_dict(c) for c in candidates],
    scan_date=today,
    delay=1.5,
)  # ~5分钟（9只）

# 入池
pm = NixFlowPoolManager()
for i, c in enumerate(candidates):
    r = results[i] if i < len(results) else None
    if r and r.is_recommended:
        from stock_pool.pool_manager import NixFlowPoolRecord
        record = NixFlowPoolRecord.from_candidate(c, r)
        pm._pool.append(record)
pm._save()
```

### 每周维护（自动执行，无需调用）

定时任务已配置：
- **每日盘后检查**：周一~五 18:00（cron: `0 18 * * 1-5`）
- **每周全面维护**：周五 18:00（cron: `0 18 * * 5`）

---

## 十、关键文件索引

| 文件 | 说明 |
|---|---|
| `stock_pool/mx_nixflow_selector.py` | 核心扫描器（含MxNixflowSelector / MxSelectorConfig / NixFlowCandidate） |
| `stock_pool/ai_analyzer.py` | AI分析器（含AIAnalyzer / AIAnalysisResult / PROMPT_TEMPLATE） |
| `stock_pool/pool_manager.py` | 股票池管理器（含NixFlowPoolManager / NixFlowPoolRecord） |
| `stock_pool/逆流_selector.py` | 原版逐只API方案（保留作为fallback） |
| `data_provider/data_selector.py` | K线数据源（efinance封装） |
| `notification/feishu.py` | 飞书通知（FeishuNotifier） |

---

## 附录：数据流详解

### 妙想 MCP 数据字段映射

```
妙想返回字段 → NixFlowCandidate 字段
─────────────────────────────────────
SECURITY_CODE           → code
SECURITY_SHORT_NAME     → name
MARKET_SHORT_NAME       → market
NEWEST_PRICE            → close
CHG                     → change_pct（涨跌幅%）
主力资金净流向（近10日）  → net_outflow_10d（万元，负值=净流出）
...
```

### L1 计算输入来源

- K线数据：`data_selector.get_history(code, days=35)` — 返回35日K线
- 计算周期：取最近 `net_outflow_window`(=10) 个交易日
- 并行获取：20线程，约 60 秒完成 ~2200 只

---

*本文档由 AI 生成，最后更新：2026-05-12*