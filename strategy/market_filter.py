# -*- coding: utf-8 -*-
"""
市场过滤器
判断大盘状态（强势/震荡/弱势）

v3.0 升级：
- 使用 data_selector 获取指数数据（支持自动切换）
- 大盘暴跌判断：综合上证/创业板/科创板，取最大跌幅
- 弱势市场：上证+双创任一走弱即判定弱势
"""

import logging
import time
import pandas as pd
from typing import Tuple, Optional
from models.signal import MarketStatus
from data_provider.data_selector import get_selector

logger = logging.getLogger(__name__)


# 指数配置
INDEX_CONFIG = {
    "sh000001": {"name": "上证指数", "weight": 1.0},
    "sz399006": {"name": "创业板指", "weight": 1.0},
    "sh000688": {"name": "科创50",  "weight": 0.8},
}


class MarketFilter:
    """
    市场过滤器

    大盘状态判断规则（v3.0 综合多指数）：
    - 强势: 上证 MA5>MA10>MA20，且任一指数跌幅不大
    - 弱势: 上证跌破MA10，或双创之一跌破MA10，或综合跌幅>2%
    - 震荡: 其他情况

    大盘暴跌判断（v3.0）：
    - 取上证/创业板/科创板三指数中跌幅最大值
    - 最大跌幅超过阈值（默认-2%）→ 触发强平
    """

    def __init__(self):
        self._selector = get_selector()
        self._cache: Optional[dict] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 300  # 5分钟缓存

    def get_market_status(self, force_refresh: bool = False) -> Tuple[MarketStatus, float]:
        """
        获取大盘状态（带缓存，5分钟内不重复请求）

        Returns:
            (market_status, worst_change_pct)
            - worst_change_pct: 三指数中跌幅最大的那个
        """
        now = time.time()
        if not force_refresh and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache["status"], self._cache["worst_change_pct"]

        try:
            result = self._evaluate_market()
            self._cache = result
            self._cache_time = now
            return result["status"], result["worst_change_pct"]
        except Exception as e:
            logger.error(f"[MarketFilter] 获取大盘状态失败: {e}")
            return MarketStatus.CONSOLIDATE, 0.0

    def get_multi_index_status(self, force_refresh: bool = False) -> dict:
        """
        获取多指数详细状态（用于监控面板）

        Returns:
            dict: {指数代码: {name, price, change_pct, ma5, ma10, ma20, status}}
        """
        now = time.time()
        if not force_refresh and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache.get("indices", {})

        try:
            result = self._evaluate_market()
            self._cache = result
            self._cache_time = now
            return result.get("indices", {})
        except Exception as e:
            logger.error(f"[MarketFilter] 获取多指数状态失败: {e}")
            return {}

    def _evaluate_market(self) -> dict:
        """执行市场评估（获取所有指数并判断）"""
        indices_data = {}
        all_changes = []
        all_ma_ok = []

        for code, cfg in INDEX_CONFIG.items():
            data = self._get_single_index(code)
            if data:
                indices_data[code] = data
                all_changes.append(data["change_pct"])
                all_ma_ok.append(data.get("ma_ok", False))

        if not indices_data:
            return {
                "status": MarketStatus.CONSOLIDATE,
                "worst_change_pct": 0.0,
                "indices": {},
            }

        # 取跌幅最大
        worst_change_pct = min(all_changes) if all_changes else 0.0

        # 三指数MA状态
        sz_ma_ok = indices_data.get("sh000001", {}).get("ma_ok", False)
        cy_ma_ok = indices_data.get("sz399006", {}).get("ma_ok", False)
        kc_ma_ok = indices_data.get("sh000688", {}).get("ma_ok", False)

        # 综合MA多头：上证MA5>MA10>MA20
        comprehensive_ma = sz_ma_ok

        # 任一双创走弱
        dual_weak = (not cy_ma_ok) or (not kc_ma_ok)

        # 状态判定
        if worst_change_pct < -2.0:
            status = MarketStatus.WEAK
            label = "🔴 弱势（暴跌）"
        elif comprehensive_ma and not dual_weak and worst_change_pct >= -1.0:
            status = MarketStatus.STRONG
            label = "🟢 强势"
        elif worst_change_pct < -1.5:
            status = MarketStatus.WEAK
            label = "🔴 弱势（走弱）"
        elif not comprehensive_ma or dual_weak:
            status = MarketStatus.WEAK
            label = "🔴 弱势"
        else:
            status = MarketStatus.CONSOLIDATE
            label = "🟡 震荡"

        # 记录
        for code, data in indices_data.items():
            cfg = INDEX_CONFIG[code]
            logger.info(
                f"[MarketFilter] {cfg['name']}({code}): "
                f"{data['price']:.2f}({data['change_pct']:+.2f}%) "
                f"MA5={data.get('ma5', 0):.2f} MA10={data.get('ma10', 0):.2f} "
                f"MA20={data.get('ma20', 0):.2f} → {data.get('ma_ok', False) and '多头' or '空头'}"
            )

        logger.info(f"[MarketFilter] 综合判断: {label}（最大跌幅={worst_change_pct:+.2f}%）")

        return {
            "status": status,
            "worst_change_pct": worst_change_pct,
            "indices": indices_data,
        }

    def _get_single_index(self, code: str) -> Optional[dict]:
        """获取单个指数的状态数据"""
        try:
            rt = self._selector.get_index_realtime(code)
            if not rt or rt.get("price", 0) <= 0:
                return None

            hist = self._selector.get_history(code, days=30)
            if not hist or len(hist) < 20:
                return None

            closes = [h["close"] for h in hist]
            closes_series = pd.Series(closes)

            ma5 = float(closes_series.rolling(5).mean().iloc[-1])
            ma10 = float(closes_series.rolling(10).mean().iloc[-1])
            ma20 = float(closes_series.rolling(20).mean().iloc[-1])

            current_price = rt["price"]
            ma_ok = current_price > ma5 > ma10 > ma20

            return {
                "name": rt["name"],
                "price": current_price,
                "change_pct": rt["change_pct"],
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "ma_ok": ma_ok,
            }
        except Exception as e:
            logger.warning(f"[MarketFilter] 获取 {code} 数据失败: {e}")
            return None


# ============================================================================
# 全局单例
# ============================================================================

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
    status, worst_change_pct = get_market_filter().get_market_status()
    if status == MarketStatus.WEAK:
        return f"大盘弱势（最大跌幅{worst_change_pct:+.2f}%），建议谨慎"
    elif worst_change_pct < -1.5:
        return f"大盘下跌{worst_change_pct:.2f}%，注意风险"
    return "大盘正常"
