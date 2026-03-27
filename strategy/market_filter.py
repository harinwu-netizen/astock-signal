# -*- coding: utf-8 -*-
"""
市场过滤器
判断大盘状态（强势/震荡/弱势）
"""

import logging
from typing import Tuple
from models.signal import MarketStatus
from data_provider.txstock import TxStock

logger = logging.getLogger(__name__)


class MarketFilter:
    """
    市场过滤器

    大盘状态判断规则:
    - 强势: 上证指数 MA5>MA10>MA20，且跌幅 > -1%
    - 弱势: 上证指数跌破MA10，或跌幅 > -2%
    - 震荡: 其他情况
    """

    def __init__(self, index_code: str = "sh000001"):
        self.index_code = index_code
        self.txstock = TxStock()
        self._cache_status: MarketStatus = MarketStatus.CONSOLIDATE
        self._cache_change_pct: float = 0.0
        self._cache_time: float = 0

    def get_market_status(self, force_refresh: bool = False) -> Tuple[MarketStatus, float]:
        """
        获取大盘状态（带缓存，5分钟内不重复请求）

        Returns:
            (market_status, change_pct)
        """
        import time
        now = time.time()

        # 5分钟缓存
        if not force_refresh and (now - self._cache_time) < 300:
            return self._cache_status, self._cache_change_pct

        try:
            history = self.txstock.get_history(self.index_code, days=25)
            if not history or len(history) < 20:
                logger.warning(f"大盘历史数据不足，无法判断市场状态")
                self._cache_status = MarketStatus.CONSOLIDATE
                self._cache_change_pct = 0.0
                return self._cache_status, self._cache_change_pct

            closes = [h["close"] for h in history]
            current_price = closes[-1]
            prev_close = history[-1].get("prev_close", closes[-2] if len(closes) > 1 else current_price)

            # 计算均线
            import pandas as pd
            closes_series = pd.Series(closes)
            ma5 = float(closes_series.rolling(5).mean().iloc[-1])
            ma10 = float(closes_series.rolling(10).mean().iloc[-1])
            ma20 = float(closes_series.rolling(20).mean().iloc[-1])

            change_pct = (current_price - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

            logger.info(f"大盘状态: 上证={current_price:.2f}({change_pct:+.2f}%), "
                       f"MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}")

            # 判断状态
            if current_price > ma5 and current_price > ma10 and current_price > ma20 and change_pct > -1:
                self._cache_status = MarketStatus.STRONG
                logger.info("大盘状态: 🟢 强势")
            elif current_price < ma5 and current_price < ma10:
                self._cache_status = MarketStatus.WEAK
                logger.info("大盘状态: 🔴 弱势")
            elif change_pct < -2.0:
                self._cache_status = MarketStatus.WEAK
                logger.info("大盘状态: 🔴 弱势（暴跌）")
            else:
                self._cache_status = MarketStatus.CONSOLIDATE
                logger.info("大盘状态: 🟡 震荡")

            self._cache_change_pct = change_pct
            self._cache_time = now

        except Exception as e:
            logger.error(f"获取大盘状态失败: {e}")
            self._cache_status = MarketStatus.CONSOLIDATE
            self._cache_change_pct = 0.0

        return self._cache_status, self._cache_change_pct


# 全局大盘过滤器
_market_filter: MarketFilter = None


def get_market_filter() -> MarketFilter:
    global _market_filter
    if _market_filter is None:
        _market_filter = MarketFilter()
    return _market_filter


def is_market_support_buy() -> bool:
    """大盘是否支持买入"""
    status, _ = get_market_filter().get_market_status()
    return status != MarketStatus.WEAK


def get_market_warning() -> str:
    """获取大盘预警信息"""
    status, change_pct = get_market_filter().get_market_status()
    if status == MarketStatus.WEAK:
        return f"大盘弱势（{change_pct:+.2f}%），建议谨慎"
    elif change_pct < -1.5:
        return f"大盘下跌{change_pct:.2f}%，注意风险"
    return "大盘正常"
