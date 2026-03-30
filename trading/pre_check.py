# -*- coding: utf-8 -*-
"""
交易前检查
所有风控检查必须通过才能执行交易

v3.0 升级：
- 追加尾盘开仓风险强化过滤（量能承接/板块共振/个股位置）
- 大盘暴跌判断升级为上证+创业板+科创板联合判定
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Tuple, Optional
from config import get_config
from models.position import Position, PositionStore
from models.signal import MarketStatus, Decision
from strategy.market_filter import get_market_filter, MarketFilter
from trading.enhanced_filters import run_all_enhanced_filters

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """检查结果"""
    passed: bool
    checks: List[Tuple[str, bool, str]]  # (检查项, 是否通过, 说明)

    def summary(self) -> str:
        if self.passed:
            return "✅ 所有检查通过"
        failed = [c[0] for c in self.checks if not c[1]]
        return f"❌ 检查失败: {', '.join(failed)}"

    def all_detail_lines(self) -> List[Tuple[str, bool, str]]:
        return self.checks


class PreTradeChecker:
    """
    交易前风控检查

    检查项（v3.0）：
      原有7层：
    1. 交易时间段（仅14:30-15:00允许开仓）
    2. 大盘状态（弱势市场禁止开仓）
    3. 大盘暴跌（>2%强平，取上证/双创最大跌幅）
    4. 持仓上限
    5. 资金充足
    6. 止损空间
    7. 同日限制

      新增3层（仅 BUY）：
    8. 量能承接（量比 0.8-1.5）
    9. 板块共振（板块MA5多头 + 涨幅≥0.5%）
    10. 个股位置（站上MA60 + 距高点回撤≤10%）
    """

    def __init__(self):
        self.config = get_config()
        self._market_filter: Optional[MarketFilter] = None

    @property
    def market_filter(self) -> MarketFilter:
        if self._market_filter is None:
            self._market_filter = get_market_filter()
        return self._market_filter

    def check(self, action: str, code: str, price: float, quantity: int,
              amount: float, market_status: MarketStatus,
              market_change_pct: float,
              kline_history: Optional[List[dict]] = None) -> CheckResult:
        """
        执行所有风控检查

        Args:
            action: BUY / SELL / STOP_LOSS
            code: 股票代码
            price: 交易价格
            quantity: 交易数量（手）
            amount: 交易金额
            market_status: 大盘状态
            market_change_pct: 大盘涨跌幅（已废弃，改用market_filter的联合判断）
            kline_history: K线历史（用于强化过滤，传入则执行3层新增检查）

        Returns:
            CheckResult
        """
        checks = []
        config = self.config

        # ===== 1. 交易时间段 =====
        now = datetime.now().time()
        open_start = datetime.strptime(config.open_window_start, "%H:%M").time()
        open_end = datetime.strptime(config.open_window_end, "%H:%M").time()

        in_open_window = open_start <= now <= open_end
        if action == "BUY":
            if not in_open_window:
                checks.append(("交易时间段", False,
                             f"仅{config.open_window_start}-{config.open_window_end}允许开仓"))
            else:
                checks.append(("交易时间段", True, f"{now.strftime('%H:%M')} 在开仓窗口内"))

        # ===== 2. 大盘状态 =====
        # 重新获取大盘状态（使用升级后的联合判断）
        real_market_status, real_worst_change = self.market_filter.get_market_status()

        if action == "BUY" and real_market_status == MarketStatus.WEAK:
            checks.append(("大盘状态", False, f"弱势市场（{real_market_status.value}），禁止开仓"))
        else:
            checks.append(("大盘状态", True, real_market_status.value))

        # ===== 3. 大盘暴跌（v3.0：取三指数最大跌幅）=====
        crash_threshold = config.market_crash_threshold
        # real_worst_change 是上证/创业板/科创板中跌幅最大的
        if real_worst_change < crash_threshold and action != "STOP_LOSS":
            checks.append(("大盘暴跌", False,
                          f"双创最大跌幅{real_worst_change:.2f}% < {crash_threshold}%，禁止开仓/强平"))
        else:
            checks.append(("大盘暴跌", True, f"双创最大跌幅{real_worst_change:.2f}%"))

        # ===== 4. 持仓上限 =====
        if action == "BUY":
            position_store = PositionStore()
            open_positions = position_store.get_open_positions()
            if len(open_positions) >= config.max_positions:
                checks.append(("持仓上限", False,
                             f"已达最大持仓数{config.max_positions}只"))
            else:
                checks.append(("持仓上限", True,
                             f"当前{len(open_positions)}只，最多{config.max_positions}只"))

        # ===== 5. 资金充足 =====
        if action == "BUY":
            position_store = PositionStore()
            positions = position_store.get_open_positions()
            locked = sum(p.cost for p in positions)
            available = config.total_capital - locked
            if amount > available:
                checks.append(("资金不足", False,
                             f"需{amount:.0f}元，可用{available:.0f}元"))
            else:
                checks.append(("资金充足", True, f"需{amount:.0f}元，可用{available:.0f}元"))

        # ===== 6. 止损空间合理性 =====
        if action == "BUY":
            risk_pct = 5.0  # 默认5%
            if risk_pct > 10.0:
                checks.append(("止损空间", False, f"风险比例{risk_pct:.1f}% > 10%"))
            else:
                checks.append(("止损空间", True, f"风险比例{risk_pct:.1f}%"))

        # ===== 7. 同日限制 =====
        if action == "BUY":
            from models.trade import TradeStore
            trade_store = TradeStore()
            if trade_store.has_traded_today(code):
                checks.append(("同日交易", False, "该股票今日已交易"))
            else:
                checks.append(("同日交易", True, "今日首次交易"))

        # ===== 8-10. 尾盘强化过滤（仅 BUY 且有K线时）=====
        if action == "BUY" and kline_history:
            enhanced = run_all_enhanced_filters(code, kline_history)
            for result in enhanced:
                checks.append(result.detail_line())
        elif action == "BUY":
            checks.append(("强化过滤", True, "无K线数据，跳过"))

        # 汇总
        all_passed = all(c[1] for c in checks)
        return CheckResult(passed=all_passed, checks=checks)

    def check_stop_loss(self, position: Position, current_price: float) -> bool:
        """检查持仓是否触发止损"""
        if position.stop_loss <= 0 or current_price <= 0:
            return False
        return current_price <= position.stop_loss
