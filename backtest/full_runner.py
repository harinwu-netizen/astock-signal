# -*- coding: utf-8 -*-
"""
P4 多股多周期完整回测引擎

功能：
  - 基于 v5.7 信号系统（加权计分 + P1资金流 + P2置信度）
  - 在 sample_pool（20只，覆盖大盘/中盘/小盘、制造/科技/消费/周期）上回测
  - 多时间段：近期6月 / 中期9月 / 年度2025 / 长期15月
  - 按市场状态（强市/震荡/弱市）分组统计胜率、P/L比、交易数
  - 输出完整 JSON 报告 + 终端汇总表

用法：
  python -m backtest.full_runner
  python -m backtest.full_runner --period 近期6月 --stocks 小盘
"""

import sys
import json
import logging
import argparse
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Optional

# 确保项目根目录在 path
sys.path.insert(0, '/root/.openclaw/workspace/astock-signal')

from data.stock_pool_sample import (
    STOCK_POOL, STOCK_CODES, SampleStock,
    BACKTEST_PERIODS, get_pool_by_market_cap, get_pool_by_sector,
)
from backtest.engine import BacktestEngine
from config import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%m-%d %H:%M"
)
logger = logging.getLogger(__name__)


# ============================================================================
# 汇总指标结构
# ============================================================================

class RegimeStats:
    """单市场状态的统计数据"""
    def __init__(self, name: str):
        self.name = name
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.total_wins = 0.0
        self.total_losses = 0.0
        self._hold_days: List[float] = []

    def add_trade(self, pnl: float, hold_days: int, regime: str):
        self.total_trades += 1
        self.total_pnl += pnl
        self._hold_days.append(hold_days)
        if pnl > 0:
            self.winning_trades += 1
            self.total_wins += pnl
        elif pnl < 0:
            self.losing_trades += 1
            self.total_losses += abs(pnl)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def avg_win(self) -> float:
        if self.winning_trades == 0:
            return 0.0
        return self.total_wins / self.winning_trades

    @property
    def avg_loss(self) -> float:
        if self.losing_trades == 0:
            return 0.0
        return self.total_losses / self.losing_trades

    @property
    def profit_loss_ratio(self) -> float:
        if self.avg_loss == 0:
            return float("inf") if self.avg_win > 0 else 0.0
        return self.avg_win / self.avg_loss

    @property
    def avg_hold_days(self) -> float:
        if not self._hold_days:
            return 0.0
        return sum(self._hold_days) / len(self._hold_days)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 1),
            "total_pnl": round(self.total_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_loss_ratio": round(self.profit_loss_ratio, 2) if self.profit_loss_ratio != float("inf") else "inf",
            "avg_hold_days": round(self.avg_hold_days, 1),
        }


class AggregateStats:
    """聚合统计数据"""
    def __init__(self):
        self.stocks: List[Dict] = []
        self.regimes: Dict[str, RegimeStats] = {
            "强市": RegimeStats("强市"),
            "震荡": RegimeStats("震荡"),
            "弱市": RegimeStats("弱市"),
        }

    def add_stock_result(self, result) -> bool:
        """
        将单只股票回测结果加入聚合
        返回 True if 该股票有交易
        """
        if result.total_trades == 0:
            return False

        stock_dict = {
            "code": result.code,
            "name": result.name,
            "total_return": round(result.total_return, 2),
            "annual_return": round(result.annual_return, 2),
            "win_rate": round(result.win_rate, 1),
            "profit_loss_ratio": round(result.profit_loss_ratio, 2),
            "max_drawdown": round(result.max_drawdown, 2),
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "avg_hold_days": round(result.avg_hold_days, 1),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
        }
        self.stocks.append(stock_dict)

        # 按 regime 归类交易
        for rt in result.round_trips:
            regime = rt.get("market_regime", "震荡")
            pnl = rt.get("pnl", 0.0)
            hold_days = rt.get("hold_days", 0)
            # 标准化 regime 名称（"强势"→"强市" 等）
            regime_map = {"强势": "强市", "弱势": "弱市", "震荡": "震荡"}
            regime_key = regime_map.get(regime, regime)
            if regime_key in self.regimes:
                self.regimes[regime_key].add_trade(pnl, hold_days, regime_key)

        return True

    def summary(self) -> dict:
        """汇总所有股票的全局指标"""
        if not self.stocks:
            return {}

        total_ret = sum(s["total_return"] for s in self.stocks) / len(self.stocks)
        total_ann_ret = sum(s["annual_return"] for s in self.stocks) / len(self.stocks)
        total_dd = sum(s["max_drawdown"] for s in self.stocks) / len(self.stocks)
        total_wr = sum(s["win_rate"] for s in self.stocks) / len(self.stocks)
        total_pl = sum(s["profit_loss_ratio"] for s in self.stocks) / len(self.stocks)
        total_trades = sum(s["total_trades"] for s in self.stocks)
        total_wins = sum(s["winning_trades"] for s in self.stocks)
        total_sharpe = sum(s["sharpe_ratio"] for s in self.stocks) / len(self.stocks)

        return {
            "avg_return": round(total_ret, 2),
            "avg_annual_return": round(total_ann_ret, 2),
            "avg_max_drawdown": round(total_dd, 2),
            "avg_win_rate": round(total_wr, 1),
            "avg_profit_loss_ratio": round(total_pl, 2),
            "total_trades": total_trades,
            "total_wins": total_wins,
            "total_loses": total_trades - total_wins,
            "avg_sharpe": round(total_sharpe, 2),
            "num_stocks": len(self.stocks),
        }

    def regime_summary(self) -> Dict[str, dict]:
        return {k: v.to_dict() for k, v in self.regimes.items()}


# ============================================================================
# 全量回测运行器
# ============================================================================

class FullRunner:
    """
    多股多周期全量回测
    使用 v5.7 信号系统（加权计分）
    """

    def __init__(self, capital: float = 1000000.0):
        self.capital = capital
        self.cfg = get_config()

    def run_single(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> Optional:
        """运行单只股票回测"""
        engine = BacktestEngine(
            initial_capital=self.capital,
            max_positions=3,
        )
        try:
            result = engine.run(
                code,
                days=999,  # 足够大，由 start_date/end_date 过滤
                start_date=start_date,
                end_date=end_date,
            )
            return result
        except Exception as e:
            logger.warning(f"[FullRunner] {code} 回测异常: {e}")
            return None

    def run_pool(
        self,
        stocks: List[SampleStock],
        start_date: str,
        end_date: str,
        period_name: str,
    ) -> tuple:
        """
        对股票池运行回测，返回 (aggregate, results_dict)
        """
        agg = AggregateStats()
        results = {}
        ok_count = 0

        logger.info(f"[FullRunner] 开始回测 {period_name}: {start_date} → {end_date}")
        logger.info(f"[FullRunner] 股票池: {len(stocks)} 只")

        for i, stock in enumerate(stocks):
            result = self.run_single(stock.code, start_date, end_date)
            if result is None:
                results[stock.code] = None
                continue

            results[stock.code] = result
            added = agg.add_stock_result(result)
            status = "✓" if added else "·"
            logger.info(
                f"  [{status}] [{i+1:02d}/{len(stocks)}] {stock.code} {stock.name} "
                f"收益={result.total_return:+.2f}% 胜率={result.win_rate:.0f}% "
                f"交易={result.total_trades}次 回撤={result.max_drawdown:.2f}%"
            )
            if added:
                ok_count += 1

        logger.info(
            f"[FullRunner] {period_name} 完成: "
            f"{ok_count}/{len(stocks)} 只股有交易，共 {sum(r.total_trades for r in results.values() if r)} 笔"
        )
        return agg, results

    def run_all_periods(self, stocks: List[SampleStock]) -> Dict[str, dict]:
        """运行所有时间段，生成完整报告"""
        all_results = {}

        header = (
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  🐙 信号灯 v5.7 全量回测报告  (P4)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        print(header)

        pool_desc = (
            f"股票池: {len(stocks)} 只 "
            f"(大盘{sum(1 for s in stocks if s.market_cap=='大盘')} "
            f"/ 中盘{sum(1 for s in stocks if s.market_cap=='中盘')} "
            f"/ 小盘{sum(1 for s in stocks if s.market_cap=='小盘')})"
        )
        print(f"  {pool_desc}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        for period_name, (start, end, days, desc) in BACKTEST_PERIODS.items():
            agg, _ = self.run_pool(stocks, start, end, period_name)
            summary = agg.summary()
            regime_stats = agg.regime_summary()

            all_results[period_name] = {
                "period": period_name,
                "start": start,
                "end": end,
                "days": days,
                "desc": desc,
                "summary": summary,
                "regimes": regime_stats,
                "stocks": agg.stocks,
            }

            # 打印该时间段汇总表
            self._print_period_summary(period_name, desc, summary, regime_stats, agg.stocks)

        # 全局汇总（所有时间段合并）
        self._print_global_summary(all_results)

        return all_results

    def _print_period_summary(
        self,
        period_name: str,
        desc: str,
        summary: dict,
        regime_stats: dict,
        stocks: list,
    ):
        """打印单时间段汇总"""
        print(f"\n{'─' * 60}")
        print(f"  📊 {period_name}（{desc}）")
        print(f"{'─' * 60}")

        if not summary:
            print("  ⚠️  该时段无有效回测数据")
            return

        print(
            f"  股票数   {summary['num_stocks']:>3} 只    "
            f"总交易   {summary['total_trades']:>3} 笔    "
            f"夏普比率 {summary['avg_sharpe']:>+5.2f}"
        )
        print(
            f"  平均收益 {summary['avg_return']:>+7.2f}%   "
            f"年化收益 {summary['avg_annual_return']:>+7.2f}%   "
            f"胜率     {summary['avg_win_rate']:>5.1f}%"
        )
        print(
            f"  最大回撤 {summary['avg_max_drawdown']:>7.2f}%   "
            f"盈亏比   {summary['avg_profit_loss_ratio']:>6.2f}    "
            f"盈利/亏损 {summary['total_wins']:>3} / {summary['total_loses']:>3}"
        )

        # Regime 分组
        print(f"\n  {'─' * 52}")
        print(f"  {'市场状态':^8} {'交易数':^8} {'胜率':^8} {'盈亏比':^8} {'平均持仓':^10}")
        print(f"  {'─' * 52}")
        for name, stats in regime_stats.items():
            if stats["total_trades"] > 0:
                pl_str = str(stats["profit_loss_ratio"]) if stats["profit_loss_ratio"] != "inf" else "∞"
                print(
                    f"  {name:^8} {stats['total_trades']:^8} "
                    f"{stats['win_rate']:^7.1f}% {pl_str:^8} "
                    f"{stats['avg_hold_days']:^9.1f}天"
                )
        print(f"  {'─' * 52}")

        # 最佳/最差股票
        if stocks:
            sorted_stocks = sorted(stocks, key=lambda x: x["total_return"], reverse=True)
            best = sorted_stocks[0]
            worst = sorted_stocks[-1]
            print(
                f"  🏆 最佳: {best['name']}({best['code']}) "
                f"{best['total_return']:+.2f}% "
                f"胜率{best['win_rate']:.0f}% "
                f"交易{best['total_trades']}次"
            )
            print(
                f"  📉 最差: {worst['name']}({worst['code']}) "
                f"{worst['total_return']:+.2f}% "
                f"胜率{worst['win_rate']:.0f}% "
                f"交易{worst['total_trades']}次"
            )

    def _print_global_summary(self, all_results: dict):
        """打印跨时段全局汇总"""
        print(f"\n{'━' * 60}")
        print(f"  🌏 全局汇总（跨所有时间段）")
        print(f"{'━' * 60}")

        # 按时间段排列
        print(
            f"\n  {'时间段':^10} {'平均收益':^10} {'胜率':^8} {'盈亏比':^8} "
            f"{'最大回撤':^10} {'总交易':^8} {'夏普':^8}"
        )
        print(f"  {'─' * 60}")

        for period_name, data in all_results.items():
            s = data["summary"]
            if not s:
                continue
            pl = s["avg_profit_loss_ratio"]
            pl_str = f"{pl:.2f}" if pl < 100 else "∞"
            print(
                f"  {period_name:^10} {s['avg_return']:^+9.2f}% "
                f"{s['avg_win_rate']:^7.1f}% {pl_str:^8} "
                f"{s['avg_max_drawdown']:^9.2f}% {s['total_trades']:^8} "
                f"{s['avg_sharpe']:^7.2f}"
            )

        # Regime 全局统计
        global_regime: Dict[str, RegimeStats] = {
            "强市": RegimeStats("强市"),
            "震荡": RegimeStats("震荡"),
            "弱市": RegimeStats("弱市"),
        }

        total_all_trades = 0
        for period_name, data in all_results.items():
            for regime_name, rs in data["regimes"].items():
                if rs["total_trades"] > 0 and regime_name in global_regime:
                    gr = global_regime[regime_name]
                    gr.total_trades += rs["total_trades"]
                    gr.winning_trades += rs["winning_trades"]
                    gr.losing_trades += rs["losing_trades"]
                    gr.total_pnl += rs["total_pnl"]
                    total_all_trades += rs["total_trades"]

        print(f"\n  {'市场状态分布（所有时间段合计）':^30}")
        print(f"  {'─' * 52}")
        print(
            f"  {'市场状态':^8} {'交易数':^8} {'胜率':^8} {'盈亏比':^8} "
            f"{'平均持仓':^10}"
        )
        print(f"  {'─' * 52}")
        for name, gr in global_regime.items():
            if gr.total_trades > 0:
                pl = gr.profit_loss_ratio
                pl_str = f"{pl:.2f}" if pl != float("inf") else "∞"
                print(
                    f"  {name:^8} {gr.total_trades:^8} "
                    f"{gr.win_rate:^7.1f}% {pl_str:^8} "
                    f"{gr.avg_hold_days:^9.1f}天"
                )
        print(f"  {'─' * 52}")
        print(f"  共计 {total_all_trades} 笔交易")

        print(f"\n{'━' * 60}\n")


# ============================================================================
# 保存报告
# ============================================================================

def save_report(all_results: dict, stocks: List[SampleStock], output_dir: str):
    """将完整报告保存为 JSON"""
    import os
    os.makedirs(output_dir, exist_ok=True)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "v5.7",
        "pool": [
            {"code": s.code, "name": s.name, "market_cap": s.market_cap, "sector": s.sector}
            for s in stocks
        ],
        "periods": all_results,
    }

    fname = os.path.join(output_dir, f"backtest_full_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[FullRunner] 报告已保存: {fname}")
    print(f"\n📄 完整 JSON 报告: {fname}")


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="信号灯 v5.7 全量回测")
    parser.add_argument(
        "--period",
        choices=list(BACKTEST_PERIODS.keys()),
        default=None,
        help="指定回测时间段（默认运行所有）"
    )
    parser.add_argument(
        "--stocks",
        choices=["大盘", "中盘", "小盘", "全部"],
        default="全部",
        help="股票池范围（默认全部）"
    )
    parser.add_argument(
        "--save",
        default="data/backtest_results",
        help="JSON 报告保存目录"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000000.0,
        help="初始资金（默认 100万）"
    )
    args = parser.parse_args()

    # 筛选股票池
    if args.stocks == "全部":
        pool = STOCK_POOL
    else:
        pool = get_pool_by_market_cap(args.stocks)

    logger.info(f"股票池筛选: {args.stocks} → {len(pool)} 只")

    runner = FullRunner(capital=args.capital)

    if args.period:
        # 单时段
        start, end, days, desc = BACKTEST_PERIODS[args.period]
        agg, results = runner.run_pool(pool, start, end, args.period)
        summary = agg.summary()
        runner._print_period_summary(args.period, desc, summary, agg.regime_summary(), agg.stocks)

        single_result = {
            args.period: {
                "period": args.period,
                "start": start,
                "end": end,
                "days": days,
                "desc": desc,
                "summary": summary,
                "regimes": agg.regime_summary(),
                "stocks": agg.stocks,
            }
        }
        save_report(single_result, pool, args.save)
    else:
        # 全量
        all_results = runner.run_all_periods(pool)
        save_report(all_results, pool, args.save)


if __name__ == "__main__":
    main()
