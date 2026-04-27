# -*- coding: utf-8 -*-
"""
市场状态判断（强市/震荡/弱市）
基于上证指数近5日数据自动判断
"""

import sys
import logging
from typing import Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# 尝试导入txstock（兼容两种导入路径）
try:
    sys.path.insert(0, '/root/.openclaw/workspace/skills/txstock/scripts')
    from txstock import TxStock
    _HAS_TXSTOCK = True
except ImportError:
    _HAS_TXSTOCK = False
    TxStock = None


class MarketRegime(Enum):
    """市场状态枚举"""
    STRONG = "强市"      # 趋势向上
    CONSOLIDATE = "震荡"  # 方向不明
    WEAK = "弱市"        # 趋势向下


@dataclass
class MarketRegimeResult:
    """市场状态判断结果"""
    regime: MarketRegime
    confidence: float = 0.0            # P2新增：置信度 0.0~1.0，越高越确定
    index_code: str = ""
    index_price: float = 0.0
    index_change_pct_5d: float = 0.0  # 5日累计涨跌幅
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    reason: str = ""


def detect_market_regime(
    index_code: str = "sh000001",
    days: int = 25,
    txstock_instance=None,
) -> MarketRegimeResult:
    """
    检测当前市场状态（强市/震荡/弱市）

    判断逻辑：
      - 强市：5日累计涨幅 > 0% 且 MA5 > MA10 > MA20
      - 弱市：5日累计跌幅 > 1.5% 或 MA5 < MA10 < MA20
      - 震荡：其余情况

    Args:
        index_code: 上证指数代码（默认 sh000001）
        days: 拉取历史K线天数
        txstock_instance: TxStock实例（可选，传入则复用）

    Returns:
        MarketRegimeResult 对象
    """
    try:
        if txstock_instance is not None:
            tx = txstock_instance
        elif _HAS_TXSTOCK and TxStock is not None:
            tx = TxStock()
        else:
            logger.warning("无法导入txstock，市场状态判断失败")
            return MarketRegimeResult(
                regime=MarketRegime.CONSOLIDATE,
                confidence=0.0,
                index_code=index_code,
                index_price=0,
                index_change_pct_5d=0,
                ma5=0.0, ma10=0.0, ma20=0.0,
                reason="数据源不可用，默认震荡"
            )

        # 获取近期K线
        hist = tx.get_history(index_code, days=days)
        if hist is None or len(hist) < 25:
            logger.warning(f"K线数据不足（{hist.shape[0] if hist is not None else 0}条），默认震荡")
            return MarketRegimeResult(
                regime=MarketRegime.CONSOLIDATE,
                confidence=0.0,
                index_code=index_code,
                index_price=0,
                index_change_pct_5d=0,
                ma5=0.0, ma10=0.0, ma20=0.0,
                reason=f"K线不足{days}条，默认震荡"
            )

        closes = hist["close"].values
        latest_close = closes[-1]

        # 计算均线
        ma5 = closes[-5:].mean()
        ma10 = closes[-10:].mean() if len(closes) >= 10 else ma5
        ma20 = closes[-20:].mean() if len(closes) >= 20 else ma10

        # 5日累计涨跌幅（相对5天前收盘价）
        close_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]
        change_pct_5d = ((latest_close - close_5d_ago) / close_5d_ago * 100) if close_5d_ago != 0 else 0

        # 均线排列判断
        ma_bull = ma5 > ma10 > ma20
        ma_bear = ma5 < ma10 < ma20

        # 判断市场状态
        if change_pct_5d > 0 and ma_bull:
            regime = MarketRegime.STRONG
            reason = (
                f"强市：5日累计涨幅{change_pct_5d:+.2f}%，"
                f"均线多头(MA5={ma5:.2f}>MA10={ma10:.2f}>MA20={ma20:.2f})"
            )
        elif change_pct_5d < -1.5 or ma_bear:
            regime = MarketRegime.WEAK
            reason = (
                f"弱市：5日累计跌幅{change_pct_5d:+.2f}%，"
                f"均线{'空头排列' if ma_bear else '下跌趋势'}(MA5={ma5:.2f},MA10={ma10:.2f},MA20={ma20:.2f})"
            )
        else:
            regime = MarketRegime.CONSOLIDATE
            reason = (
                f"震荡：5日累计{change_pct_5d:+.2f}%，"
                f"均线{'多头' if ma_bull else '空头' if ma_bear else '纠缠'}，方向不明"
            )

        # ===== P2新增：置信度计算 =====
        ma_spread = abs(ma5 - ma10) / ma10 * 100 if ma10 > 0 else 0  # 均线间距百分比
        trend_strength = abs(ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0  # 趋势强度

        if regime == MarketRegime.STRONG:
            confidence = min(trend_strength / 5.0, 1.0)  # 间距5%以上=高置信
        elif regime == MarketRegime.WEAK:
            confidence = min(trend_strength / 5.0, 1.0)
        else:
            # 震荡市：均线间距越小越确定（纠缠=真的震荡）
            # 间距<1%=高置信，>3%=置信度低，可能要变盘
            confidence = max(0.0, 1.0 - (ma_spread / 3.0))

        # ===== P2新增：低置信震荡市降级为弱市（保守）=====
        original_regime = regime
        if regime == MarketRegime.CONSOLIDATE and confidence < 0.4:
            regime = MarketRegime.WEAK
            reason = (
                f"震荡→弱市降级：置信度{confidence:.2f}<0.4，"
                f"均线间距过大({ma_spread:.2f}%)，趋势不明，保守处理。"
                f"原始判断：{reason}"
            )
            confidence = 0.4  # 不降低到0，维持基础置信

        logger.info(f"市场状态判断 [{index_code}]: {reason} (置信度={confidence:.2f})")

        return MarketRegimeResult(
            regime=regime,
            confidence=confidence,
            index_code=index_code,
            index_price=latest_close,
            index_change_pct_5d=change_pct_5d,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            reason=reason,
        )

    except Exception as e:
        logger.error(f"市场状态判断异常: {e}")
        return MarketRegimeResult(
            regime=MarketRegime.CONSOLIDATE,
            confidence=0.0,
            index_code=index_code,
            index_price=0,
            index_change_pct_5d=0,
            ma5=0.0, ma10=0.0, ma20=0.0,
            reason=f"判断异常: {e}，默认震荡"
        )


def get_market_regime_str(regime: MarketRegime) -> str:
    """返回市场状态的中文描述"""
    mapping = {
        MarketRegime.STRONG: "🟢 强市",
        MarketRegime.CONSOLIDATE: "🟡 震荡",
        MarketRegime.WEAK: "🔴 弱市",
    }
    return mapping.get(regime, "⚪ 未知")


if __name__ == "__main__":
    # 简单测试
    result = detect_market_regime()
    print(f"市场状态: {get_market_regime_str(result.regime)}")
    print(f"判断理由: {result.reason}")
