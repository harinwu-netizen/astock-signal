# -*- coding: utf-8 -*-
"""
逆流选股法 — 股票池管理器

职责：
- 维护三层股票池：观察池 / 核心池 / 已出池
- 入池 / 出池 / 状态更新
- 与信号模块对接（提供候选列表）
- 飞书通知触发

数据存储：
- JSON 文件：stock_pool/data/observation_pool.json
                       stock_pool/data/core_pool.json
                       stock_pool/data/exit_pool.json
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# 数据结构
# ============================================================================

POOL_DATA_DIR = Path(__file__).parent / "data"


def _now():
    return datetime.now().strftime("%Y-%m-%d")


# ============================================================================
# 股票池管理器
# ============================================================================

class PoolManager:
    """
    股票池管理器

    用法：
        pm = PoolManager()

        # 添加到观察池
        pm.add_to_observation("000629", "逆流信号",
                              ai_analysis={"概率": "高", "摘要": "减持尾声，主力吸筹"})

        # 获取当前池
        obs = pm.get_observation_pool()
        print(f"观察池: {[s['code'] for s in obs]}")

        # 检查出池条件
        exits = pm.check_exit()
        for code, reason in exits:
            print(f"{code} 出池: {reason}")
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir) if data_dir else POOL_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._observation: List[dict] = []
        self._core: List[dict] = []
        self._exit: List[dict] = []

        self._load()

    # ------------------------------------------------------------------------
    # 文件 I/O
    # ------------------------------------------------------------------------

    def _pool_path(self, pool_type: str) -> Path:
        return self.data_dir / f"{pool_type}_pool.json"

    def _load(self):
        """从文件加载"""
        for pool_type in ("observation", "core", "exit"):
            path = self._pool_path(pool_type)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if pool_type == "observation":
                        self._observation = data
                    elif pool_type == "core":
                        self._core = data
                    else:
                        self._exit = data
                except Exception as e:
                    logger.warning(f"加载 {pool_type} 池失败: {e}")

    def _save(self, pool_type: str, data: List[dict]):
        """保存到文件"""
        path = self._pool_path(pool_type)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 {pool_type} 池失败: {e}")

    # ------------------------------------------------------------------------
    # 观察池
    # ------------------------------------------------------------------------

    def add_to_observation(
        self,
        code: str,
        signal_reason: str,
        ai_analysis: Optional[dict] = None,
        candidate_data: Optional[dict] = None,
    ) -> bool:
        """
        加入观察池

        Returns:
            True=新加入, False=已在池中
        """
        # 已在池中
        if self._find_in_pool(code, self._observation):
            logger.info(f"[PoolManager] {code} 已在观察池，跳过")
            return False

        # 超过上限
        max_observation = 10
        if len(self._observation) >= max_observation:
            logger.warning(f"[PoolManager] 观察池已达上限 {max_observation}，先出最旧的")
            self._remove_oldest("observation")

        entry = {
            "code": code,
            "name": candidate_data.get("name", code) if candidate_data else code,
            "入池日期": _now(),
            "入池原因": signal_reason,
            "ai_analysis": ai_analysis or {},
            "score": candidate_data.get("score", 0) if candidate_data else 0,
            "close": candidate_data.get("close", 0) if candidate_data else 0,
            "net_outflow": candidate_data.get("net_outflow_10d", 0) if candidate_data else 0,
            "最后更新": _now(),
            "状态": "观察中",
        }
        self._observation.append(entry)
        self._save("observation", self._observation)

        logger.info(
            f"[PoolManager] ✅ {code} 入观察池 | "
            f"原因: {signal_reason} | 评分: {entry['score']:.1f}"
        )
        return True

    def get_observation_pool(self) -> List[dict]:
        """获取观察池列表"""
        return list(self._observation)

    def promote_to_core(self, code: str) -> bool:
        """观察池 -> 核心池（升池）"""
        entry = self._find_and_remove(code, self._observation)
        if entry is None:
            logger.warning(f"[PoolManager] {code} 不在观察池，无法升池")
            return False

        entry["升池日期"] = _now()
        entry["状态"] = "核心"
        self._core.append(entry)
        self._save("core", self._core)
        self._save("observation", self._observation)

        logger.info(f"[PoolManager] ⬆️ {code} 升入核心池")
        return True

    # ------------------------------------------------------------------------
    # 核心池
    # ------------------------------------------------------------------------

    def get_core_pool(self) -> List[dict]:
        """获取核心池列表"""
        return list(self._core)

    def add_to_core(
        self,
        code: str,
        reason: str,
        ai_analysis: Optional[dict] = None,
    ) -> bool:
        """直接加入核心池"""
        if self._find_in_pool(code, self._core):
            return False

        entry = {
            "code": code,
            "入池日期": _now(),
            "入池原因": reason,
            "ai_analysis": ai_analysis or {},
            "最后更新": _now(),
            "状态": "核心",
        }
        self._core.append(entry)
        self._save("core", self._core)
        logger.info(f"[PoolManager] ⭐ {code} 直接入核心池 | {reason}")
        return True

    # ------------------------------------------------------------------------
    # 出池
    # ------------------------------------------------------------------------

    def remove(
        self,
        code: str,
        reason: str,
        pool_type: str = "auto",
    ) -> bool:
        """
        移出股票池

        Args:
            code: 股票代码
            reason: 出池原因
            pool_type: "observation" | "core" | "auto"
        """
        removed = False

        if pool_type in ("observation", "auto"):
            entry = self._find_and_remove(code, self._observation)
            if entry:
                self._record_exit(entry, reason)
                removed = True

        if pool_type in ("core", "auto") and not removed:
            entry = self._find_and_remove(code, self._core)
            if entry:
                self._record_exit(entry, reason)
                removed = True

        if removed:
            logger.info(f"[PoolManager] 🚪 {code} 出池 | 原因: {reason}")
        return removed

    def _record_exit(self, entry: dict, reason: str):
        """记录出池"""
        exit_entry = dict(entry)
        exit_entry["出池日期"] = _now()
        exit_entry["出池原因"] = reason

        # 计算池内收益
        if exit_entry.get("close", 0) > 0:
            # 需要实时价格来算，这里先记录入池价
            exit_entry["入池价格"] = exit_entry.get("close", 0)

        self._exit.append(exit_entry)
        self._save("exit", self._exit)

    def get_exit_pool(self) -> List[dict]:
        """获取已出池列表"""
        return list(self._exit)

    # ------------------------------------------------------------------------
    # 出池检查（每日调用）
    # ------------------------------------------------------------------------

    def check_exit(self) -> List[tuple]:
        """
        检查是否该出池，返回 [(code, reason), ...]

        出池条件：
        - 超时（入池 > 60 日无信号）
        - 趋势破坏（有效跌破 MA20 且 3 日内未收复）
        - 基本面恶化（业绩预亏等）
        """
        exits = []
        today = datetime.now()

        for entry in self._observation + self._core:
            code = entry["code"]
            entry_date = datetime.strptime(entry["入池日期"], "%Y-%m-%d")
            days_in_pool = (today - entry_date).days

            # P1: 超时
            if days_in_pool > 60:
                exits.append((code, f"超时出池（入池{days_in_pool}日）"))
                continue

            # P2: 趋势破坏（需要实时数据，这里仅做示例）
            # 实际实现时调用 data_selector.get_realtime(code) 检查 MA20
            # if self._is_broken_trend(code):
            #     exits.append((code, "趋势破坏出池"))

        # 执行出池
        for code, reason in exits:
            self.remove(code, reason, pool_type="auto")

        return exits

    # ------------------------------------------------------------------------
    # 观察池更新（每日收盘后调用）
    # ------------------------------------------------------------------------

    def update_observation_status(self, code: str, new_price: float, new_net_outflow: float):
        """更新观察池中股票的状态（每日收盘后调用）"""
        for entry in self._observation:
            if entry["code"] == code:
                entry["最后更新"] = _now()
                entry["现价"] = new_price
                entry["近10日净流出"] = new_net_outflow
                if new_price > 0 and entry.get("close", 0) > 0:
                    entry["浮亏"] = (new_price - entry["close"]) / entry["close"] * 100
                self._save("observation", self._observation)
                break

    def get_pool_codes(self) -> List[str]:
        """获取当前所有池的股票代码（去重）"""
        codes = set()
        for entry in self._observation + self._core:
            codes.add(entry["code"])
        return list(codes)

    # ------------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------------

    def _find_in_pool(self, code: str, pool: List[dict]) -> Optional[dict]:
        for entry in pool:
            if entry["code"] == code:
                return entry
        return None

    def _find_and_remove(self, code: str, pool: List[dict]) -> Optional[dict]:
        for i, entry in enumerate(pool):
            if entry["code"] == code:
                return pool.pop(i)
        return None

    def _remove_oldest(self, pool_type: str):
        """移除最旧的股票"""
        pool = self._observation if pool_type == "observation" else self._core
        if not pool:
            return
        # 找最早入池的
        oldest_idx = 0
        for i, entry in enumerate(pool):
            entry_date = datetime.strptime(entry["入池日期"], "%Y-%m-%d")
            oldest_date = datetime.strptime(pool[oldest_idx]["入池日期"], "%Y-%m-%d")
            if entry_date < oldest_date:
                oldest_idx = i
        entry = pool.pop(oldest_idx)
        self._record_exit(entry, "池满挤出")
        if pool_type == "observation":
            self._save("observation", self._observation)
        else:
            self._save("core", self._core)

    # ------------------------------------------------------------------------
    # 打印状态
    # ------------------------------------------------------------------------

    def print_status(self):
        """打印当前状态"""
        print(f"\n{'='*60}")
        print("【股票池状态】")
        print(f"{'='*60}")

        print(f"\n📊 观察池（{len(self._observation)} 只）")
        if self._observation:
            print(f"{'代码':<8} {'名称':<8} {'入池':<12} {'评分':>5} {'大单净流出':>10} {'浮亏':>8}")
            print("-" * 60)
            for e in self._observation:
                print(
                    f"{e['code']:<8} {e.get('name',''):<8} {e['入池日期']:<12} "
                    f"{e.get('score', 0):>5.1f} "
                    f"{e.get('net_outflow', 0):>10.0f}万 "
                    f"{e.get('浮亏', 0):>+7.2f}%"
                )
        else:
            print("  空")

        print(f"\n⭐ 核心池（{len(self._core)} 只）")
        if self._core:
            for e in self._core:
                print(f"  {e['code']} | 入池: {e['入池日期']} | {e.get('入池原因','')}")
        else:
            print("  空")

        print(f"\n🚪 已出池（{len(self._exit)} 只）")
        if self._exit:
            for e in self._exit[-5:]:  # 只显示最近5个
                print(
                    f"  {e['code']} | 出池: {e.get('出池日期','?')} | "
                    f"{e.get('出池原因','')}"
                )

        print(f"{'='*60}\n")


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pm = PoolManager()
    pm.print_status()

    # 测试添加
    pm.add_to_observation(
        "000629",
        "逆流信号测试",
        ai_analysis={"概率": "高", "摘要": "测试"},
        candidate_data={"name": "攀钢钒钛", "score": 72.5, "close": 3.90, "net_outflow_10d": 4279},
    )

    pm.print_status()

    # 测试出池检查
    exits = pm.check_exit()
    print(f"出池检查: {exits}")

# ============================================================================
# 逆流股票池管理器 — 动态维护
# ============================================================================

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import asyncio


@dataclass
class NixFlowPoolRecord:
    """
    逆流股票池记录（含持仓标记）

    v2.0 新增字段：
    - ma_convergence, box_width, box_touches, divergence_ratio
    - ai_四维评分（催化剂/空间/持续性/筹码）
    - ai_确定性等级, ai_综合得分, ai_类型
    """
    code: str
    name: str
    scan_date: str

    # 量化评分
    score: float = 0.0
    close: float = 0.0
    net_outflow_10d: float = 0.0
    price_range_10d: float = 0.0
    bottom_rise_10d: float = 0.0
    amplitude_10d: float = 0.0
    ma20_trend: str = ""
    avg_amount_10d: float = 0.0

    # v2.0 新版指标
    ma_convergence: float = 0.0
    box_width: float = 0.0
    box_touches: int = 0
    divergence_ratio: float = 0.0

    # AI 归类结果（第二步）
    ai_类型: str = ""
    ai_logic: str = ""
    ai_summary: str = ""

    # AI 四维评分（第三步）
    ai_确定性等级: str = "低"
    ai_综合得分: float = 0.0
    ai_催化剂得分: float = 0.0
    ai_空间得分: float = 0.0
    ai_持续性得分: float = 0.0
    ai_筹码得分: float = 0.0

    # 兼容旧字段
    ai_prob: str = ""          # = ai_确定性等级

    # 持仓状态
    signal_type: str = ""
    is_held: bool = False
    is_traded: bool = False
    in_pool_date: str = ""
    last_ai_date: str = ""
    last_l1_date: str = ""
    status: str = "观察中"
    exit_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_candidate(c, ai_result=None) -> "NixFlowPoolRecord":
        # v2.0 兼容 NixFlowClassResult（旧AIResult字段自动兼容）
        ai_prob = ""
        ai_logic = ""
        ai_summary = ""
        ai_类型 = ""
        ai_确定性等级 = "低"
        ai_综合得分 = 0.0
        ai_催化剂得分 = 0.0
        ai_空间得分 = 0.0
        ai_持续性得分 = 0.0
        ai_筹码得分 = 0.0

        if ai_result:
            # 兼容 NixFlowClassResult（新）
            if hasattr(ai_result, "确定性等级"):
                ai_确定性等级 = ai_result.确定性等级
                ai_prob = ai_result.确定性等级
                ai_logic = ai_result.核心结论  # 核心结论作为逻辑摘要
                ai_summary = ai_result.核心结论
                ai_类型 = ai_result.类型
                ai_综合得分 = ai_result.综合得分
                ai_催化剂得分 = ai_result.催化剂得分
                ai_空间得分 = ai_result.空间得分
                ai_持续性得分 = ai_result.持续性得分
                ai_筹码得分 = ai_result.筹码得分
            # 兼容旧 AIAnalysisResult
            elif hasattr(ai_result, "概率"):
                ai_prob = ai_result.概率
                ai_logic = ai_result.核心逻辑
                ai_summary = ai_result.分析摘要

        # 新版指标（candidate 可能有这些字段）
        ma_convergence = getattr(c, "ma_convergence", 0.0)
        box_width = getattr(c, "box_width", 0.0)
        box_touches = getattr(c, "box_touches", 0)
        divergence_ratio = getattr(c, "divergence_ratio", 0.0)

        return NixFlowPoolRecord(
            code=c.code,
            name=c.name,
            scan_date=c.scan_date,
            score=c.score,
            close=c.close,
            net_outflow_10d=c.net_outflow_10d,
            price_range_10d=c.price_range_10d,
            bottom_rise_10d=c.bottom_rise_10d,
            amplitude_10d=getattr(c, "amplitude_10d", 0.0),
            ma20_trend=getattr(c, "ma20_trend", ""),
            avg_amount_10d=getattr(c, "avg_amount_10d", 0.0),
            # v2.0 新指标
            ma_convergence=ma_convergence,
            box_width=box_width,
            box_touches=box_touches,
            divergence_ratio=divergence_ratio,
            # AI 结果
            ai_prob=ai_prob,
            ai_logic=ai_logic,
            ai_summary=ai_summary,
            ai_类型=ai_类型,
            ai_确定性等级=ai_确定性等级,
            ai_综合得分=ai_综合得分,
            ai_催化剂得分=ai_催化剂得分,
            ai_空间得分=ai_空间得分,
            ai_持续性得分=ai_持续性得分,
            ai_筹码得分=ai_筹码得分,
            signal_type=getattr(c, "signal_type", ""),
            in_pool_date=c.scan_date,
            last_ai_date=c.scan_date,
            last_l1_date=c.scan_date,
            status="观察中",
        )


class NixFlowPoolManager:
    """
    逆流股票池动态管理器

    职责：
    - 持仓标记：持仓股票（is_held=True）不被淘汰
    - 每周维护：周五18:00盘后，池内淘汰 → 市场重扫 → 入池补充
    - 每日检查：每日18:00盘后，仅池内淘汰（不补充）
    - 与模拟交易对接：持仓标记由外部触发

    数据存储：stock_pool/data/nixflow_pool.json
    """

    POOL_FILE = POOL_DATA_DIR / "nixflow_pool.json"
    MAX_POOL_SIZE = 10          # 观察池上限

    def __init__(self):
        self._pool: List[NixFlowPoolRecord] = []
        self._exit_records: List[NixFlowPoolRecord] = []
        self._load()

    # ------------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------------

    def _load(self):
        path = self.POOL_FILE
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._pool = [NixFlowPoolRecord(**r) for r in raw.get("pool", [])]
                self._exit_records = [NixFlowPoolRecord(**r) for r in raw.get("exit_records", [])]
                logger.info(f"[NixFlowPoolManager] 加载池: {len(self._pool)} 只")
            except Exception as e:
                logger.warning(f"[NixFlowPoolManager] 加载失败: {e}")
                self._pool = []
                self._exit_records = []

    def _save(self):
        self.POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.POOL_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "pool": [r.to_dict() for r in self._pool],
                    "exit_records": [r.to_dict() for r in self._exit_records[-50:]],  # 只保留最近50条
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[NixFlowPoolManager] 保存失败: {e}")

    # ------------------------------------------------------------------------
    # 持仓标记（由外部/模拟交易调用）
    # ------------------------------------------------------------------------

    def mark_held(self, code: str, held: bool = True) -> bool:
        """标记股票是否已持仓（持仓的不被淘汰）"""
        for r in self._pool:
            if r.code == code:
                r.is_held = held
                r.status = "已持仓" if held else "观察中"
                self._save()
                logger.info(f"[NixFlowPoolManager] {code} 持仓标记: {held}")
                return True
        return False

    def is_held(self, code: str) -> bool:
        for r in self._pool:
            if r.code == code:
                return r.is_held
        return False

    def get_pool(self) -> List[NixFlowPoolRecord]:
        return list(self._pool)

    def get_pool_codes(self) -> List[str]:
        return [r.code for r in self._pool]

    # ------------------------------------------------------------------------
    # 手动入池/出池（main_cli 调用）
    # ------------------------------------------------------------------------

    def add_to_pool(self, candidate, ai_result=None, send_notification: bool = True) -> bool:
        """手动添加候选股票到池中（main_cli用）"""
        code = candidate.code
        if code in self.get_pool_codes():
            return False

        record = NixFlowPoolRecord.from_candidate(candidate, ai_result)
        self._pool.append(record)
        self._save()
        logger.info(f"[NixFlowPoolManager] ✅ 入池: {code} {candidate.name}")
        return True

    def remove_from_pool(self, code: str, reason: str = "手动移出"):
        """手动移出股票（main_cli用）"""
        for i, r in enumerate(self._pool):
            if r.code == code:
                r.status = "已出池"
                r.exit_reason = reason
                self._exit_records.append(r)
                self._pool.pop(i)
                self._save()
                logger.info(f"[NixFlowPoolManager] 🚪 出池: {code} | {reason}")
                return

    # ------------------------------------------------------------------------
    # L1 检查（对池内单只股票）
    # ------------------------------------------------------------------------

    def _check_l1_for_record(self, record: "NixFlowPoolRecord") -> bool:
        """检查池内记录是否仍然满足L1条件（新版）"""
        cfg = MxSelectorConfig()
        # 新版指标
        if record.ma_convergence > 0 and record.ma_convergence >= cfg.ma_convergence_thresh:
            return False
        if record.box_width > 0 and record.box_width >= cfg.box_width_max:
            return False
        # 背离度低于阈值
        if record.divergence_ratio < cfg.divergence_thresh:
            return False
        # 基础条件
        if record.price_range_10d < cfg.price_max_loss:
            return False
        if record.price_range_10d > cfg.price_max_gain:
            return False
        return True

    # ------------------------------------------------------------------------
    # 池内淘汰（不补充）
    # ------------------------------------------------------------------------

    def daily_pool_check(self) -> dict:
        """
        每日盘后池内检查：淘汰已不满足L1或AI判定的股票（持仓除外）

        Returns:
            dict with keys: eliminated (list), kept (int)
        """
        from stock_pool.mx_nixflow_selector import MxNixflowSelector, MxSelectorConfig
        from stock_pool.ai_analyzer import AIAnalyzer

        today = datetime.now().strftime("%Y-%m-%d")
        cfg = MxSelectorConfig(fetch_workers=10)
        scanner = MxNixflowSelector(cfg)
        ai = AIAnalyzer()

        eliminated = []
        kept = 0

        logger.info(f"[NixFlowPoolManager] 每日池检查: 共 {len(self._pool)} 只")

        for record in list(self._pool):
            # 持仓股票不受影响
            if record.is_held:
                logger.info(f"  {record.code} {record.name}: 已持仓，跳过检查")
                kept += 1
                continue

            # 重新获取K线，验证L1条件
            try:
                from data_provider.data_clean import clean_kline_data
                raw = scanner.data_sel.get_history(record.code, days=35)
                if not raw:
                    reason = "K线获取失败"
                    self._eliminate(record, reason)
                    eliminated.append(record.code)
                    continue
                hist = clean_kline_data(raw)
                if not hist or len(hist) < 32:
                    reason = "K线不足"
                    self._eliminate(record, reason)
                    eliminated.append(record.code)
                    continue

                l1 = scanner._calc_l1_from_hist(hist, len(hist) - 1, cfg.net_outflow_window)
                # passes_l1_filter 新版需要 net_outflow/market_cap/close，这里直接用 record 字段判断
                passes_l1 = True
                if l1 is not None:
                    # 用 record 中已存储的指标做快速判断
                    if record.ma_convergence > 0 and record.ma_convergence >= cfg.ma_convergence_thresh:
                        passes_l1 = False
                    if record.box_width > 0 and record.box_width >= cfg.box_width_max:
                        passes_l1 = False
                    if record.divergence_ratio > 0 and record.divergence_ratio < cfg.divergence_thresh:
                        passes_l1 = False
                    if record.price_range_10d < cfg.price_max_loss or record.price_range_10d > cfg.price_max_gain:
                        passes_l1 = False
                    reason = "L1条件不再满足"
                    self._eliminate(record, reason)
                    eliminated.append(record.code)
                    continue

                kept += 1

            except Exception as e:
                logger.warning(f"  {record.code} L1复查异常: {e}")
                kept += 1  # 异常时保守保留

        self._save()
        logger.info(f"[NixFlowPoolManager] 每日检查完成: 淘汰{len(eliminated)}只，保留{kept}只")
        return {"eliminated": eliminated, "kept": kept, "date": today}

    # ------------------------------------------------------------------------
    # 每周维护（周五 18:00）
    # ------------------------------------------------------------------------

    def weekly_maintenance(self, send_notification: bool = True) -> dict:
        """
        每周股票池全面维护（周五盘后调用）

        流程：
        1. 池内淘汰：不满足L1 或 AI不推荐（持仓除外）
        2. 市场重扫：妙想筛选 → L1计算 → AI评估
        3. 补充入池：按评分排序，补充至上限10只

        Returns:
            dict with: eliminated, added, final_pool
        """
        from stock_pool.mx_nixflow_selector import MxNixflowSelector, MxSelectorConfig, print_candidates
        from stock_pool.ai_analyzer import AIAnalyzer, format_analysis_for_feishu

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[NixFlowPoolManager] ========== 每周维护 {today} ==========")

        # Step 1: 池内淘汰
        logger.info("[NixFlowPoolManager] Step 1: 池内淘汰检查...")
        eliminated = []
        cfg = MxSelectorConfig(fetch_workers=10)
        scanner = MxNixflowSelector(cfg)
        ai = AIAnalyzer()

        for record in list(self._pool):
            if record.is_held:
                logger.info(f"  {record.code} 已持仓，跳过淘汰")
                continue

            # L1复查
            try:
                from data_provider.data_clean import clean_kline_data
                raw = scanner.data_sel.get_history(record.code, days=35)
                if not raw:
                    self._eliminate(record, "K线获取失败")
                    eliminated.append(record.code)
                    continue
                hist = clean_kline_data(raw)
                l1 = scanner._calc_l1_from_hist(hist, len(hist) - 1, cfg.net_outflow_window) if hist else None
                passes_l1 = l1 is not None and scanner._passes_l1_filter(l1)
            except Exception as e:
                logger.warning(f"  {record.code} L1复查异常: {e}")
                passes_l1 = True  # 异常时保守

            if not passes_l1:
                self._eliminate(record, "L1条件不再满足")
                eliminated.append(record.code)
                continue

            # AI复查（v2.0：确定性等级低则出池）
            if record.ai_确定性等级 == "低":
                self._eliminate(record, f"AI确定性等级低({record.ai_确定性等级})")
                eliminated.append(record.code)
                continue

            logger.info(f"  {record.code} {record.name}: 保留(L1={'通过' if passes_l1 else '失败'}, AI={record.ai_prob})")

        logger.info(f"[NixFlowPoolManager] Step 1 完成: 淘汰 {len(eliminated)} 只")

        # Step 2: 市场扫描（Stage 1）
        logger.info("[NixFlowPoolManager] Step 2: 市场扫描...")
        candidates = scanner.scan()
        logger.info(f"[NixFlowPoolManager] Stage 1 量化筛选: {len(candidates)} 只候选")

        # Step 3: AI评估（Stage 2）
        logger.info("[NixFlowPoolManager] Step 3: AI评估...")
        ai_results = ai.batch_analyze(
            [scanner._candidate_to_dict(c) for c in candidates],
            scan_date=today,
            delay=1.5,
        )

        # Step 4: 入池（按四维综合得分排序，取TOP）
        slots = self.MAX_POOL_SIZE - len(self._pool)
        added = []
        if slots > 0:
            # 按综合得分降序
            ranked = sorted(zip(candidates, ai_results), key=lambda x: x[1].综合得分, reverse=True)
            for c, r in ranked:
                # v2.0: 排除类型不入池，确定性等级低不入池
                if r.is_excluded:
                    continue
                if r.确定性等级 == "低":
                    continue
                if c.code in self.get_pool_codes():
                    continue  # 已在池中

                record = NixFlowPoolRecord.from_candidate(c, r)
                record.last_ai_date = today
                self._pool.append(record)
                added.append(c.code)
                logger.info(f"[NixFlowPoolManager] ✅ 入池: {c.code} {c.name} (量化评分:{c.score:.0f}, 四维综合:{r.综合得分:.0f}, 确定性:{r.确定性等级})")
                if len(added) >= slots:
                    break
        else:
            logger.info("[NixFlowPoolManager] 池已满，跳过补充")

        self._save()

        # 汇总
        result = {
            "date": today,
            "eliminated": eliminated,
            "added": added,
            "final_pool": [
                (r.code, r.name, r.score, r.ai_确定性等级, r.ai_综合得分, r.is_held, r.status)
                for r in self._pool
            ],
            "pool_size": len(self._pool),
        }

        logger.info(
            f"[NixFlowPoolManager] ========== 每周维护完成 ==========\n"
            f"  淘汰: {len(eliminated)} 只 {eliminated}\n"
            f"  新增: {len(added)} 只 {added}\n"
            f"  池内: {len(self._pool)} 只"
        )

        # 飞书通知
        if send_notification:
            self._notify_weekly(result)

        return result

    # ------------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------------

    def _notify_weekly(self, result: dict):
        """发送每周维护报告（v2.0新版四维评分）"""
        try:
            from notification.feishu import get_feishu_notifier
            notifier = get_feishu_notifier()

            eliminated = result["eliminated"]
            added = result["added"]
            pool = result["final_pool"]

            lines = [
                f"📊 **逆流股票池每周维护报告 v2.0**",
                f"📅 {result['date']}（周五盘后）",
                "",
                f"🚪 **出池** {len(eliminated)} 只",
            ]
            for code in eliminated:
                lines.append(f"  - {code}")
            if not eliminated:
                lines.append("  （无）")

            lines.extend(["", f"✅ **入池** {len(added)} 只"])
            for code in added:
                lines.append(f"  - {code}")
            if not added:
                lines.append("  （无）")

            lines.extend(["", f"📋 **当前观察池** {result['pool_size']} 只"])
            # pool: (code, name, score, ai_确定性等级, ai_综合得分, is_held, status)
            for code, name, score, certainty, four_d_score, is_held, status in pool:
                held_tag = "【持仓】" if is_held else ""
                emoji = {"高": "🟢", "中": "🟡", "低": "🔴"}.get(certainty, "⚪")
                lines.append(
                    f"  {emoji}{code} {name} {held_tag} "
                    f"量化{score:.0f} | 四维{four_d_score:.0f} | {certainty}确定性"
                )

            notifier.send("\n".join(lines))
            logger.info("[NixFlowPoolManager] 飞书通知已发送")
        except Exception as e:
            logger.warning(f"[NixFlowPoolManager] 飞书通知失败: {e}")

    # ------------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------------

    def _eliminate(self, record: NixFlowPoolRecord, reason: str):
        """淘汰股票"""
        record.status = "已出池"
        record.exit_reason = reason
        self._exit_records.append(record)
        for i, r in enumerate(self._pool):
            if r.code == record.code:
                self._pool.pop(i)
                break
        logger.info(f"[NixFlowPoolManager] 🚪 {record.code} {record.name} 出池: {reason}")

    def print_status(self):
        """打印当前池状态（v2.0新版）"""
        print(f"\n{'='*90}")
        print(f"【逆流股票池 v2.0】共 {len(self._pool)} 只  （上限 {self.MAX_POOL_SIZE}只）")
        print(f"{'='*90}")
        header = (
            f"{'代码':<8} {'名称':<8} {'量化分':>6} {'确定性':>5} {'四维总分':>7} "
            f"{'催化':>5} {'空间':>5} {'持续':>5} {'筹码':>5} {'持仓':>4} {'状态'}"
        )
        print(header)
        print("-" * 90)
        for r in self._pool:
            held = "✅" if r.is_held else "  "
            certainty = r.ai_确定性等级 or "—"
            total_score = r.ai_综合得分
            print(
                f"{r.code:<8} {r.name:<8} {r.score:>5.0f}   "
                f"{certainty:>4}  {total_score:>6.0f}   "
                f"{r.ai_催化剂得分:>4.0f} {r.ai_空间得分:>5.0f} {r.ai_持续性得分:>5.0f} {r.ai_筹码得分:>5.0f}  "
                f"{held}   {r.status}"
            )
        print(f"{'='*90}")
        if self._exit_records:
            print(f"\n最近出池 ({len(self._exit_records)} 只):")
            for r in self._exit_records[-5:]:
                print(f"  {r.code} {r.name} | 出池: {r.exit_reason} | 原确定性: {r.ai_确定性等级}")
        print()


