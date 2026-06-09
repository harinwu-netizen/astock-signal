# -*- coding: utf-8 -*-
"""
逆流选股法 — 量化扫描器

每日收盘后全市场扫描，找出符合"逆流"条件的股票进入观察池。

核心逻辑：
    近10日主力在大单级别持续净流出（派发）
    + 价格没有大跌（坚挺）
    + 底部逐步抬高
    = 主力在"出货换手"中悄悄吸筹 → 中期行情概率高

两层筛选：
    L1 免费指标：价格/振幅/MA20（用 baostock/efinance）
    L2 资金流：近10日累计大单净流出（调用东方财富 API，有限配额）

输出：候选股票列表（含评分），供 AI 分析器进一步确认
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from data_provider.data_selector import get_selector
from data_provider.data_clean import clean_kline_data
from stock_pool.money_flow_history import MoneyFlowHistory

logger = logging.getLogger(__name__)

# A股全市场股票代码（6位数字）
# 来自东方财富行业分类数据 + 指数成分股
# 实际使用时从 config 或外部注入
ALL_ASTOCK_CODES: List[str] = []


# ============================================================================
# 配置
# ============================================================================

@dataclass
class SelectorConfig:
    """扫描配置"""
    # L1 初筛：价格区间最大跌幅（%，>此值剔除）
    price_max_loss: float = -8.0

    # L1 初筛：底部抬高最小幅度（%）
    bottom_rise_min: float = 0.0

    # L1 初筛：近10日最大振幅上限（%）
    amplitude_max: float = 15.0

    # L1 初筛：MA20趋势要求（above/below/crossing）
    ma20_required: str = "above"

    # L1 初筛：日均成交额下限（万元）
    avg_amount_min: float = 3000

    # L2 精准筛：近N日累计大单净流出阈值（万元）
    # 正值=净流出越大（逆流条件越强）
    net_outflow_thresh: float = 3000.0
    net_outflow_window: int = 10

    # L2 精准筛：超大单+大单合计净流出阈值（万元）
    super_big_outflow_thresh: float = 5000.0

    # 候选股票上限（AI分析前截断）
    max_candidates: int = 20

    # 指数成分股扫描池（优先扫描这些，覆盖市场主要股票）
    # 默认：沪深300 + 中证500 + 科创50 ≈ 1000只
    use_index_pool: bool = True

    # API 调用间隔（秒），避免频率限制
    api_interval: float = 1.5


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class NixFlowCandidate:
    """逆流候选股"""
    code: str
    name: str
    scan_date: str

    # L1 指标
    close: float
    price_range_10d: float       # 近10日涨跌幅（%）
    bottom_rise_10d: float      # 近10日底部抬高幅度（%）
    amplitude_10d: float        # 近10日最大振幅（%）
    ma20_trend: str             # MA20趋势
    avg_amount_10d: float        # 日均成交额（万元）

    # L2 资金流
    net_outflow_10d: float      # 近10日累计大单净流出（万元）
    super_net_outflow_10d: float # 近10日累计超大+大单净流出（万元）
    main_net_outflow_10d: float  # 近10日累计主力净流出（万元）

    # 综合评分
    score: float = 0.0

    # 描述
    @property
    def signal_type(self) -> str:
        if self.net_outflow_10d <= 0:
            return "资金流入（不匹配）"
        if self.price_range_10d > 0:
            return "上涨逆流"
        return "下跌逆流"


# ============================================================================
# 逆流扫描器
# ============================================================================

class 逆流Selector:
    """
    逆流选股量化扫描器

    用法：
        cfg = SelectorConfig()
        selector = 逆流Selector(cfg)

        # 每日收盘后扫描
        candidates = selector.scan(scan_date="2026-04-30")

        for c in candidates:
            print(f"{c.code} {c.name}: 大单净流出={c.net_outflow_10d:.0f}万 评分={c.score:.1f}")
    """

    def __init__(self, config: SelectorConfig = None):
        self.cfg = config or SelectorConfig()
        self.data_selector = get_selector()
        self.mf_history = MoneyFlowHistory()

    # ------------------------------------------------------------------------
    # 主扫描入口
    # ------------------------------------------------------------------------

    def scan(self, scan_date: Optional[str] = None) -> List[NixFlowCandidate]:
        """
        执行全市场扫描，返回逆流候选股票列表

        Args:
            scan_date: 扫描日期 "YYYY-MM-DD"，默认今天

        Returns:
            List[NixFlowCandidate]，按 score 降序
        """
        scan_date = scan_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[逆流Selector] 开始扫描 {scan_date}")

        candidates = []
        stocks_to_scan = self._get_stock_pool()

        logger.info(f"[逆流Selector] 待扫描 {len(stocks_to_scan)} 只股票")

        for i, (code, name) in enumerate(stocks_to_scan):
            if i > 0 and i % 100 == 0:
                logger.info(f"[逆流Selector] 进度: {i}/{len(stocks_to_scan)}")

            c = self._scan_single(code, name, scan_date)
            if c is not None:
                candidates.append(c)

            # API 限速
            time.sleep(self.cfg.api_interval)

        # 按评分排序
        candidates.sort(key=lambda x: x.score, reverse=True)

        # 截断到上限
        result = candidates[: self.cfg.max_candidates]

        score_range = f"{candidates[-1].score:.1f} ~ {candidates[0].score:.1f}" if candidates else "N/A"
        logger.info(
            f"[逆流Selector] 扫描完成，候选 {len(candidates)} 只，"
            f"截取 TOP {len(result)}，评分范围: {score_range}"
        )
        return result

    def _get_stock_pool(self) -> List[Tuple[str, str]]:
        """
        获取待扫描股票池

        优先使用指数成分股（沪深300 + 中证500 + 科创50），
        覆盖全市场大部分市值，减少扫描量。
        若获取失败，用 fallback 小池子。
        """
        if self.cfg.use_index_pool:
            pool = self._get_index_pool()
            if pool:
                return pool
            # fallback
        return [
            ("000629", "攀钢钒钛"),
            ("600519", "贵州茅台"),
            ("300059", "东方财富"),
            ("000001", "平安银行"),
            ("000002", "万科A"),
            ("600036", "招商银行"),
            ("601318", "中国平安"),
            ("600276", "恒瑞医药"),
            ("000858", "五粮液"),
            ("002594", "比亚迪"),
        ]

    def _get_index_pool(self) -> List[Tuple[str, str]]:
        """
        获取沪深300、中证500、科创50成分股
        约 800 只，覆盖A股主要市值
        """
        # 指数代码
        indices = {
            "000300": "sh000300",  # 沪深300
            "000905": "sh000905",  # 中证500
            "000688": "sh000688",  # 科创50
        }

        all_stocks = {}  # code -> name

        try:
            from data_provider.eastmoney import EastMoney
            em = EastMoney()

            for idx_code, em_code in indices.items():
                try:
                    # 用 efinance 获取指数成分股
                    import efinance as ef
                    df = ef.stock.get_成分股(em_code)
                    if df is not None and len(df) > 0:
                        for _, row in df.iterrows():
                            code = str(row.get("代码", "")).zfill(6)
                            name = str(row.get("名称", code))
                            if code not in all_stocks:
                                all_stocks[code] = name
                        logger.info(f"[逆流Selector] 指数 {idx_code} 获取 {len(df)} 只成分股")
                except Exception as e:
                    logger.warning(f"[逆流Selector] 获取 {idx_code} 成分股失败: {e}")
                    continue
        except ImportError:
            logger.error("[逆流Selector] efinance 未安装，无法获取指数成分股")
        except Exception as e:
            logger.warning(f"[逆流Selector] 获取指数成分股异常: {e}")

        # fallback：如果 efinance 失败，用已知股票列表
        if not all_stocks:
            all_stocks = {
                "000629": "攀钢钒钛",
                "600519": "贵州茅台",
                "300059": "东方财富",
                "000001": "平安银行",
            }
            logger.warning(f"[逆流Selector] 使用 fallback 股票池，共 {len(all_stocks)} 只")

        return [(code, name) for code, name in all_stocks.items()]

    # ------------------------------------------------------------------------
    # 单只股票扫描
    # ------------------------------------------------------------------------

    def _scan_single(
        self,
        code: str,
        name: str,
        scan_date: str,
    ) -> Optional[NixFlowCandidate]:
        """
        对单只股票执行完整扫描
        """
        try:
            # 获取历史K线数据（至少40天：20天MA20 + 10天窗口 + 缓冲）
            raw = self.data_selector.get_history(code, days=300)
            if not raw:
                return None

            hist = clean_kline_data(raw)
            if not hist or len(hist) < 25:
                return None

            # 找到 scan_date 对应的日期索引
            date_to_idx = {d["date"]: i for i, d in enumerate(hist)}
            if scan_date not in date_to_idx:
                # 取最近的交易日
                scan_idx = len(hist) - 1
                scan_date_actual = hist[-1]["date"]
            else:
                scan_idx = date_to_idx[scan_date]
                scan_date_actual = scan_date

            # L1 指标计算
            l1 = self._calc_l1_metrics(hist, scan_idx)
            if l1 is None:
                return None

            # L1 初筛
            if not self._passes_l1_filter(l1):
                return None

            # L2 资金流精准筛选
            l2 = self._calc_l2_metrics(code, name, scan_date_actual)
            if l2 is None:
                return None

            # L2 精准筛
            if l2["net_outflow"] < self.cfg.net_outflow_thresh:
                return None

            # 综合评分
            score = self._calc_score(l1, l2)

            return NixFlowCandidate(
                code=code,
                name=name or self.data_selector.get_name(code),
                scan_date=scan_date_actual,
                close=l1["close"],
                price_range_10d=l1["price_range"],
                bottom_rise_10d=l1["bottom_rise"],
                amplitude_10d=l1["amplitude"],
                ma20_trend=l1["ma20_trend"],
                avg_amount_10d=l1["avg_amount"],
                net_outflow_10d=l2["net_outflow"],
                super_net_outflow_10d=l2["super_net_outflow"],
                main_net_outflow_10d=l2["main_net_outflow"],
                score=score,
            )

        except Exception as e:
            logger.debug(f"[逆流Selector] 扫描 {code} 失败: {e}")
            return None

    def _calc_l1_metrics(
        self,
        hist: List[dict],
        date_idx: int,
        window: int = 10,
    ) -> Optional[dict]:
        """
        计算 L1 免费指标

        使用 date_idx 当日及之前 N 日数据
        """
        if date_idx < window + 10:
            return None

        recent = hist[max(0, date_idx - window + 1):date_idx + 1]
        if len(recent) < window:
            return None

        older_start = max(0, date_idx - window * 2)
        older = hist[older_start:date_idx - window + 1] if older_start < date_idx - window else []

        close = recent[-1]["close"]
        if close == 0:
            return None

        # 近N日价格区间涨跌幅
        start_close = recent[0]["close"]
        price_range = (close - start_close) / start_close * 100

        # 近N日振幅（每日高-低/昨收 的最大值）
        prev_close_ref = hist[max(0, date_idx - window)]["close"]
        amplitudes = [(d["high"] - d["low"]) / prev_close_ref * 100 for d in recent]
        max_amplitude = max(amplitudes) if amplitudes else 0

        # 底部抬高
        recent_lows = [d["low"] for d in recent]
        older_lows = [d["low"] for d in older] if older else [prev_close_ref]
        recent_min = min(recent_lows)
        older_min = min(older_lows)
        bottom_rise = (recent_min - older_min) / older_min * 100 if older_min > 0 else 0

        # MA20趋势
        ma20_trend = self._calc_ma20_trend(hist, date_idx)

        # 日均成交额（万元）
        amounts = [d.get("volume", 0) * d["close"] * 100 / 10000 for d in recent]
        avg_amount = sum(amounts) / len(amounts) if amounts else 0

        return {
            "close": close,
            "price_range": price_range,
            "amplitude": max_amplitude,
            "bottom_rise": bottom_rise,
            "ma20_trend": ma20_trend,
            "avg_amount": avg_amount,
        }

    def _calc_ma20_trend(self, hist: List[dict], date_idx: int) -> str:
        """MA20趋势"""
        if date_idx < 20:
            return "below"
        window = hist[date_idx - 19:date_idx + 1]
        if len(window) < 20:
            return "below"
        ma20 = sum(d["close"] for d in window) / 20
        current = hist[date_idx]["close"]
        if current > ma20:
            return "above"
        elif current < ma20:
            return "below"
        return "crossing"

    def _passes_l1_filter(self, l1: dict) -> bool:
        """L1 初筛"""
        cfg = self.cfg

        # 跌幅限制
        if l1["price_range"] < cfg.price_max_loss:
            return False

        # 底部抬高
        if l1["bottom_rise"] < cfg.bottom_rise_min:
            return False

        # 振幅上限
        if l1["amplitude"] > cfg.amplitude_max:
            return False

        # MA20
        if cfg.ma20_required == "above" and l1["ma20_trend"] != "above":
            return False

        # 成交额下限
        if l1["avg_amount"] < cfg.avg_amount_min:
            return False

        return True

    def _calc_l2_metrics(
        self,
        code: str,
        name: str,
        end_date: str,
    ) -> Optional[dict]:
        """
        计算 L2 资金流指标

        调用 money_flow_history.get_history 获取真实数据
        """
        import pandas as pd
        window = self.cfg.net_outflow_window

        # 先尝试单日查询（可能有缓存）
        try:
            df = self.mf_history.get_history(code, end_date, end_date, name)
        except Exception:
            df = None

        # 如果数据不足（< window 天），从更长区间获取
        if df is None or df.empty or len(df) < window:
            start_dt = pd.to_datetime(end_date) - pd.Timedelta(days=window * 3)
            start_str = start_dt.strftime("%Y-%m-%d")
            try:
                df = self.mf_history.get_history(code, start_str, end_date, name)
            except Exception:
                return None

        if df is None or df.empty or "大单净流入" not in df.columns:
            return None

        window_df = df[df.index <= end_date].tail(window)
        if len(window_df) < window:
            return None

        net_outflow = -window_df["大单净流入"].sum()

        super_net = window_df["超大单净流入"].sum() if "超大单净流入" in window_df.columns else 0
        super_big = -(super_net + window_df["大单净流入"].sum())

        main_net = window_df["主力净流入"].sum() if "主力净流入" in window_df.columns else 0

        return {
            "net_outflow": net_outflow,
            "super_net_outflow": super_big,
            "main_net_outflow": main_net,
        }

    def _calc_score(self, l1: dict, l2: dict) -> float:
        """
        综合评分（0~100，越高越符合逆流）

        权重分配：
        - 资金流强度：50分（大单净流出越多越高）
        - 价格强势：30分（跌幅越小越高）
        - 底部抬高：20分
        """
        score = 0.0

        # 资金流（上限：10000万=50分）
        score += min(l2["net_outflow"] / 10000, 1.0) * 50

        # 价格强势（跌幅越小越高，上限5%不跌=30分）
        if l1["price_range"] >= 0:
            score += 30
        else:
            loss_ratio = min(abs(l1["price_range"]) / 5.0, 1.0)
            score += (1 - loss_ratio) * 30

        # 底部抬高（上限3%=20分）
        score += max(0, min(l1["bottom_rise"] / 3.0, 1.0)) * 20

        return score


# ============================================================================
# 工具函数
# ============================================================================

def print_candidates(candidates: List[NixFlowCandidate], title: str = "逆流候选"):
    """打印候选股票列表"""
    if not candidates:
        print(f"{title}：无")
        return

    print(f"\n{'='*80}")
    print(f"【{title}】共 {len(candidates)} 只")
    print(f"{'='*80}")
    print(f"{'#':<4} {'代码':<8} {'名称':<8} {'评分':>5} "
          f"{'大单净流出':>10} {'超博大单流出':>12} "
          f"{'10日涨跌':>8} {'底部抬高':>8} {'信号类型':<12}")
    print("-" * 80)

    for i, c in enumerate(candidates, 1):
        print(
            f"{i:<4} {c.code:<8} {c.name:<8} "
            f"{c.score:>5.1f} "
            f"{c.net_outflow_10d:>10.0f}万 "
            f"{c.super_net_outflow_10d:>12.0f}万 "
            f"{c.price_range_10d:>+8.2f}% "
            f"{c.bottom_rise_10d:>+8.2f}% "
            f"{c.signal_type:<12}"
        )

    print(f"{'='*80}")


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = SelectorConfig(use_index_pool=False, max_candidates=10)
    selector = 逆流Selector(cfg)

    print("扫描 000629 近10日数据...")
    c = selector._scan_single("000629", "攀钢钒钛", "2025-04-30")
    if c:
        print(f"代码: {c.code} {c.name}")
        print(f"评分: {c.score:.1f}")
        print(f"大单净流出: {c.net_outflow_10d:.0f}万元")
        print(f"超博大单流出: {c.super_net_outflow_10d:.0f}万元")
        print(f"10日涨跌: {c.price_range_10d:+.2f}%")
        print(f"底部抬高: {c.bottom_rise_10d:+.2f}%")
        print(f"信号类型: {c.signal_type}")
    else:
        print("无候选信号")