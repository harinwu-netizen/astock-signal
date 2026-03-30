# -*- coding: utf-8 -*-
"""
尾盘开仓风险强化过滤
追加在原有7层风控之上的3层额外检查

1. 量能承接：量比 0.8-1.5（当日量/20日均量），尾盘5分钟量逐步放大
2. 板块共振：主要板块 MA5 多头 + 板块涨幅 ≥0.5%
3. 个股位置：站在 MA60 上方，距阶段高点回撤 ≤10%
"""

import logging
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from data_provider.data_selector import get_selector

logger = logging.getLogger(__name__)

# ============================================================================
# 阈值配置（可随 .env 覆盖）
# ============================================================================

VOLUME_RATIO_MIN = 0.8    # 量比下限
VOLUME_RATIO_MAX = 1.5    # 量比上限
BLOCK_CHANGE_MIN = 0.5   # 板块最小涨幅（%）
DRAWDOWN_MAX = 10.0      # 距高点最大回撤（%）
MA_PERIOD = 60           # MA60 周期
VOLUME_MA_PERIOD = 20    # 成交量均线周期


# ============================================================================
# 检查结果
# ============================================================================

@dataclass
class FilterResult:
    """强化过滤结果"""
    passed: bool
    filter_name: str
    detail: str = ""

    def detail_line(self) -> Tuple[str, bool, str]:
        return (self.filter_name, self.passed, self.detail)


# ============================================================================
# 1. 量能承接检查
# ============================================================================

def check_volume承接(code: str, hist: list) -> FilterResult:
    """
    检查量能是否合理承接

    规则：
    - 量比 = 当日成交量 / 20日均量，需在 0.8-1.5 之间
    - 尾盘量逐步放大（可选，作为辅助判断）

    Args:
        code: 股票代码
        hist: 清洗后的K线历史（近60日），最后一条为今日

    Returns:
        FilterResult
    """
    if not hist or len(hist) < VOLUME_MA_PERIOD:
        return FilterResult(True, "量能承接", "数据不足，跳过检查")

    try:
        df = pd.DataFrame(hist[-VOLUME_MA_PERIOD:])

        # 20日均量
        vol_ma = df["volume"].mean()
        today_vol = hist[-1]["volume"]

        # 量比
        vol_ratio = today_vol / vol_ma if vol_ma > 0 else 0

        in_range = VOLUME_RATIO_MIN <= vol_ratio <= VOLUME_RATIO_MAX
        detail = f"量比={vol_ratio:.2f}（20日均量={vol_ma:.0f}手，今日={today_vol}手）"

        if in_range:
            logger.debug(f"[Filter] {code} 量能承接✅: {detail}")
            return FilterResult(True, "量能承接", detail)
        else:
            reason = f"量比{vol_ratio:.2f}超出{VOLUME_RATIO_MIN}-{VOLUME_RATIO_MAX}范围"
            logger.warning(f"[Filter] {code} 量能承接❌: {reason}")
            return FilterResult(False, "量能承接", f"{detail}，{reason}")

    except Exception as e:
        logger.error(f"[Filter] {code} 量能承接检查失败: {e}")
        return FilterResult(True, "量能承接", f"检查异常: {e}，跳过")


# ============================================================================
# 2. 板块共振检查
# ============================================================================

def check板块共振(code: str, hist: list) -> FilterResult:
    """
    检查板块是否与大盘共振

    规则：
    - 取股票所属的主要板块（第一个板块，通常是行业板块）
    - 板块 MA5 多头排列（收盘 > MA5）
    - 板块今日涨幅 ≥ BLOCK_CHANGE_MIN（0.5%）

    Args:
        code: 股票代码
        hist: 清洗后的K线历史（近60日）

    Returns:
        FilterResult
    """
    try:
        # 获取股票所属板块
        board = _get_main_board(code)
        if not board:
            return FilterResult(True, "板块共振", "未找到板块信息，跳过")

        block_code = board["code"]
        block_name = board["name"]
        block_change = board["change_pct"]

        # 获取板块指数历史K线（东方财富直接走，不经过selector避免腾讯不支持BK码）
        block_hist_raw = _get_board_history(block_code, days=35)
        if not block_hist_raw or len(block_hist_raw) < 25:
            return FilterResult(True, "板块共振", f"板块'{block_name}'历史数据不足，跳过")

        # 计算板块 MA5
        closes = [h["close"] for h in block_hist_raw[-25:]]
        ma5 = sum(closes[-5:]) / 5
        current_close = closes[-1]

        ma5_ok = current_close > ma5
        change_ok = block_change >= BLOCK_CHANGE_MIN

        detail = (
            f"板块='{block_name}'({block_change:+.2f}%)，"
            f"MA5={ma5:.2f}，现价={current_close:.2f}，"
            f"MA5多头={'✅' if ma5_ok else '❌'}，涨幅≥0.5%={'✅' if change_ok else '❌'}"
        )

        if ma5_ok and change_ok:
            logger.debug(f"[Filter] {code} 板块共振✅: {detail}")
            return FilterResult(True, "板块共振", detail)
        else:
            reasons = []
            if not ma5_ok:
                reasons.append("MA5空头")
            if not change_ok:
                reasons.append(f"涨幅{block_change:.2f}%<{BLOCK_CHANGE_MIN}%")
            detail += f"，失败原因: {'，'.join(reasons)}"
            logger.warning(f"[Filter] {code} 板块共振❌: {detail}")
            return FilterResult(False, "板块共振", detail)

    except Exception as e:
        logger.error(f"[Filter] {code} 板块共振检查失败: {e}")
        return FilterResult(True, "板块共振", f"检查异常: {e}，跳过")


def _get_main_board(code: str) -> Optional[dict]:
    """
    获取股票所属行业板块（第一个板块）

    Returns:
        dict: {code, name, change_pct} 或 None
    """
    try:
        import efinance as ef
        boards = ef.stock.get_belong_board(code)
        if boards is None or boards.empty:
            return None

        # 取第一个板块（通常是行业主板块）
        row = boards.iloc[0]
        return {
            "code": str(row.get("板块代码", "")),
            "name": str(row.get("板块名称", "")),
            "change_pct": float(row.get("板块涨幅", 0)),
        }
    except Exception as e:
        logger.warning(f"[Filter] 获取 {code} 板块信息失败: {e}")
        return None


def _get_board_history(block_code: str, days: int = 35) -> Optional[list]:
    """
    获取板块指数K线（直接用 EastMoney，不走 selector 避免 txstock 不支持 BK 码）
    """
    from data_provider.eastmoney import EastMoney
    em = EastMoney()
    try:
        hist = em.get_history(block_code, days=days)
        if hist and len(hist) >= 25:
            logger.debug(f"[Filter] 板块 {block_code} K线获取成功: {len(hist)} 条")
            return hist
        return None
    except Exception as e:
        logger.warning(f"[Filter] 板块 {block_code} K线获取失败: {e}")
        return None


# ============================================================================
# 3. 个股位置检查
# ============================================================================

def check个股位置(code: str, hist: list) -> FilterResult:
    """
    检查个股当前位置是否适合买入

    规则：
    - 站在 MA60 上方（现价 > MA60）
    - 距近60日阶段高点回撤 ≤10%

    Args:
        code: 股票代码
        hist: 清洗后的K线历史（近60日）

    Returns:
        FilterResult
    """
    if not hist or len(hist) < MA_PERIOD:
        return FilterResult(True, "个股位置", "数据不足，跳过检查")

    try:
        df = pd.DataFrame(hist[-MA_PERIOD:])

        # MA60
        ma60 = df["close"].rolling(window=MA_PERIOD, min_periods=MA_PERIOD).mean().iloc[-1]
        current_price = hist[-1]["close"]

        # 近60日最高点
        phase_high = df["high"].max()

        # 位置判断
        above_ma60 = current_price > ma60
        drawdown = (phase_high - current_price) / phase_high * 100 if phase_high > 0 else 0
        drawdown_ok = drawdown <= DRAWDOWN_MAX

        detail = (
            f"现价={current_price:.2f}，MA60={ma60:.2f}，"
            f"60日高点={phase_high:.2f}，回撤={drawdown:.1f}%，"
            f"站上MA60={'✅' if above_ma60 else '❌'}，"
            f"回撤≤10%={'✅' if drawdown_ok else '❌'}"
        )

        if above_ma60 and drawdown_ok:
            logger.debug(f"[Filter] {code} 个股位置✅: {detail}")
            return FilterResult(True, "个股位置", detail)
        else:
            reasons = []
            if not above_ma60:
                reasons.append(f"现价{current_price:.2f}<MA60={ma60:.2f}")
            if not drawdown_ok:
                reasons.append(f"回撤{drawdown:.1f}%>{DRAWDOWN_MAX}%")
            detail += f"，失败原因: {'，'.join(reasons)}"
            logger.warning(f"[Filter] {code} 个股位置❌: {detail}")
            return FilterResult(False, "个股位置", detail)

    except Exception as e:
        logger.error(f"[Filter] {code} 个股位置检查失败: {e}")
        return FilterResult(True, "个股位置", f"检查异常: {e}，跳过")


# ============================================================================
# 批量执行
# ============================================================================

def run_all_enhanced_filters(code: str, hist: list) -> list:
    """
    运行全部3个强化过滤器

    Args:
        code: 股票代码
        hist: K线历史

    Returns:
        List[FilterResult]
    """
    results = [
        check_volume承接(code, hist),
        check板块共振(code, hist),
        check个股位置(code, hist),
    ]
    return results


def all_passed(code: str, hist: list) -> Tuple[bool, str]:
    """
    快捷方法：判断是否全部通过强化过滤

    Returns:
        (是否全部通过, 失败原因汇总)
    """
    results = run_all_enhanced_filters(code, hist)
    failed = [r for r in results if not r.passed]
    if not failed:
        return True, ""
    reasons = "; ".join([r.detail for r in failed])
    return False, reasons
