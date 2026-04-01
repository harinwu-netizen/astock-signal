# -*- coding: utf-8 -*-
"""
交易执行器
模拟撮合引擎（实际交易需对接券商API）
"""

import logging
import uuid
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from config import get_config
from models.position import Position, PositionStore
from models.trade import TradeRecord, TradeStore
from models.signal import RealTimeSignal, Decision
from indicators.atr import calc_atr_stop_loss, calc_take_profit

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """交易结果"""
    success: bool
    action: str              # BUY / SELL / STOP_LOSS
    code: str
    name: str
    price: float
    quantity: int            # 手数
    amount: float            # 成交金额
    commission: float        # 手续费
    message: str
    trade_record: Optional[TradeRecord] = None


class TradeExecutor:
    """
    交易执行器

    注意：当前为模拟撮合，真实交易需要对接券商API（如Interactive Broker、东方财富等）
    """

    def __init__(self):
        self.config = get_config()
        self.position_store = PositionStore()
        self.trade_store = TradeStore()

        # 交易成本
        self.commission_rate = 0.00025    # 佣金万2.5
        self.min_commission = 5           # 最低佣金5元
        self.stamp_tax = 0.001           # 印花税千1（卖时）
        self.transfer_fee = 0.00002      # 过户费万0.2

    def execute_buy(
        self,
        signal: RealTimeSignal,
        quantity: int,
        atr: float = 0,
        market_regime: str = "CONSOLIDATE",
        atr_multiplier: float = 2.0,
        stop_loss_pct: float = 10.0,
        take_profit_pct: float = 15.0,
    ) -> TradeResult:
        """
        执行买入

        Args:
            signal: 信号对象
            quantity: 买入手数
            atr: ATR值
            market_regime: 市场状态（WEAK/CONSOLIDATE/STRONG）
            atr_multiplier: ATR止损倍数
            stop_loss_pct: 亏损止损线（%）
            take_profit_pct: 止盈线（%）

        Returns:
            TradeResult
        """
        code = signal.code
        name = signal.name
        price = signal.price
        amount = price * quantity * 100  # 每手100股

        # 手续费
        commission = self._calc_buy_commission(amount)

        # 成交金额（含手续费）
        total_cost = amount + commission

        # 检查资金
        positions = self.position_store.get_open_positions()
        locked = sum(p.cost for p in positions)
        available = self.config.total_capital - locked

        if total_cost > available:
            return TradeResult(
                success=False,
                action="BUY",
                code=code,
                name=name,
                price=price,
                quantity=quantity,
                amount=amount,
                commission=commission,
                message=f"资金不足：需{total_cost:.0f}元，可用{available:.0f}元"
            )

        # ATR止损价（含地板保护，根据市场状态）
        if atr > 0:
            floor_map = {"WEAK": 0.03, "STRONG": 0.15, "CONSOLIDATE": 0.10}
            floor_pct = floor_map.get(market_regime, 0.05)
            stop_loss = calc_atr_stop_loss(price, atr, atr_multiplier, floor_pct)
            take_profit = price * (1 + take_profit_pct / 100)
        else:
            stop_loss = price * (1 - stop_loss_pct / 100)
            take_profit = price * (1 + take_profit_pct / 100)

        # 创建持仓
        position = Position(
            id=str(uuid.uuid4()),
            code=code,
            name=name,
            buy_date=datetime.now().strftime("%Y-%m-%d"),
            buy_price=price,
            quantity=quantity,
            cost=amount,  # 不含手续费成本
            current_price=price,
            unrealized_pnl=0,
            pnl_pct=0,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=stop_loss,
            latest_buy_signals=signal.buy_count,
            latest_sell_signals=signal.sell_count,
            latest_rebound_signals=getattr(signal, 'rebound_count', 0),
            latest_trend_signals=getattr(signal, 'trend_count', 0),
            market_regime=market_regime,
            atr=atr,
            atr_multiplier=atr_multiplier,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            ma20_take_profit=getattr(signal, 'ma20', 0.0),
            status="open",
        )

        # 保存持仓
        all_positions = self.position_store.load()
        all_positions.append(position)
        self.position_store.save(all_positions)

        # 记录交易
        trade = TradeRecord(
            id=str(uuid.uuid4()),
            code=code,
            name=name,
            action="BUY",
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            stamp_tax=0,
            buy_signals=signal.buy_count,
            sell_signals=signal.sell_count,
            atr=atr,
            stop_loss=stop_loss,
            position_id=position.id,
            pre_check_passed=True,
            created_at=datetime.now().isoformat(),
            trade_date=datetime.now().strftime("%Y-%m-%d"),
        )
        self.trade_store.add(trade)

        logger.info(f"✅ 买入成交: {name} @{price:.2f} × {quantity}手 = ¥{amount:.0f}")

        # 触发AI增强分析（异步）
        self._trigger_llm_analysis(trade)

        return TradeResult(
            success=True,
            action="BUY",
            code=code,
            name=name,
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            message=f"买入成功",
            trade_record=trade
        )

    def execute_sell(
        self,
        position: Position,
        quantity: int,
        reason: str = "",
        signal: RealTimeSignal = None,
    ) -> TradeResult:
        """
        执行卖出

        Args:
            position: 持仓对象
            quantity: 卖出手数
            reason: 卖出原因
            signal: 当前信号（可选）
        """
        code = position.code
        name = position.name
        price = position.current_price  # 用当前市场价
        amount = price * quantity * 100

        # 手续费（含印花税）
        commission = self._calc_sell_commission(amount)
        total_proceeds = amount - commission

        # 更新持仓
        all_positions = self.position_store.load()
        for i, p in enumerate(all_positions):
            if p.id == position.id:
                if quantity >= p.quantity:
                    # 完全平仓
                    p.status = "closed"
                    p.closed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    p.closed_reason = reason
                else:
                    # 部分平仓
                    p.quantity -= quantity
                    p.cost = p.cost * (p.quantity / (p.quantity + quantity))
                break

        self.position_store.save(all_positions)

        # 记录交易
        trade = TradeRecord(
            id=str(uuid.uuid4()),
            code=code,
            name=name,
            action="SELL" if reason != "止损" else "STOP_LOSS",
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            stamp_tax=amount * self.stamp_tax,
            buy_signals=signal.buy_count if signal else 0,
            sell_signals=signal.sell_count if signal else 0,
            atr=position.atr,
            stop_loss=position.stop_loss,
            position_id=position.id,
            pre_check_passed=True,
            created_at=datetime.now().isoformat(),
            trade_date=datetime.now().strftime("%Y-%m-%d"),
        )
        self.trade_store.add(trade)

        # 计算盈亏
        pnl = total_proceeds - position.cost * (quantity / position.quantity)
        logger.info(f"{'🔴' if pnl < 0 else '🟢'} 卖出成交: {name} @{price:.2f} × {quantity}手，盈亏{pnl:+,.0f}元")

        # 触发AI增强分析（异步）
        self._trigger_llm_analysis(trade)

        return TradeResult(
            success=True,
            action=trade.action,
            code=code,
            name=name,
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            message=f"卖出成功，盈亏{pnl:+,.0f}元",
            trade_record=trade
        )

    def execute_stop_loss(self, position: Position) -> TradeResult:
        """执行止损"""
        return self.execute_sell(
            position=position,
            quantity=position.quantity,
            reason="止损",
        )

    def _trigger_llm_analysis(self, trade: TradeRecord):
        """
        触发AI增强分析（异步，不阻塞交易）

        交易执行后自动调用，向用户推送更详细的AI分析
        """
        try:
            from notification.llm_analyzer import get_llm_analyzer
            from notification.feishu import get_feishu_notifier
            import threading

            def _async_llm():
                llm = get_llm_analyzer()
                if not llm.is_available:
                    return

                trade_dict = trade.to_dict() if hasattr(trade, 'to_dict') else trade.__dict__
                analysis = llm.analyze_trade(trade_dict)

                if analysis:
                    notifier = get_feishu_notifier()
                    if notifier.enabled:
                        notifier.send(
                            f"🤖 **AI增强分析**\n\n"
                            f"**{trade.name} ({trade.action})**\n\n"
                            f"{analysis}\n\n"
                            f"_仅供参考，不构成投资建议_",
                            msg_type="markdown"
                        )

            # 异步执行，不阻塞交易
            thread = threading.Thread(target=_async_llm, daemon=True)
            thread.start()
            logger.debug(f"LLM分析线程已启动")

        except Exception as e:
            logger.error(f"LLM分析触发失败: {e}")

    def _calc_buy_commission(self, amount: float) -> float:
        """计算买入手续费"""
        commission = amount * self.commission_rate
        return max(commission, self.min_commission)

    def _calc_sell_commission(self, amount: float) -> float:
        """计算卖出手续费（含印花税+过户费）"""
        commission = amount * self.commission_rate
        commission = max(commission, self.min_commission)
        stamp_tax = amount * self.stamp_tax
        transfer_fee = amount * self.transfer_fee
        return commission + stamp_tax + transfer_fee


# 全局执行器
_executor: TradeExecutor = None


def get_executor() -> TradeExecutor:
    global _executor
    if _executor is None:
        _executor = TradeExecutor()
    return _executor
