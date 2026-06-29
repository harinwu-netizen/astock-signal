"""
资金流数据获取器 (v6.16, 2026-06-16 重写)

版本演进:
- v6.13 (2026-06-25) 三级 fallback:妙想 > screener > push2delay
- v6.16 (2026-06-16) 极简双源:妙想 > push2delay（删 screener/v6.11 防御/EM_API_KEY/.env 开关）

设计原则:
- 双源 fallback(妙想数据主源 + push2delay 兜底)
- 15 分钟缓存(每只股票独立)
- 删腾讯估算(21:35 海赟剔除,精度差)
- 删 v6.11 健康探测缓存(锁 4 小时的元凶)
- 删 30 分钟妙查退避(叠加失效的元凶)
- 删东方财富 EM_API_KEY(4 月起就废的死代码)
- 删 .env 开关(默认全链路启用)

字段说明:
- 妙想数据:字段全(main_net/in/out, big_net/in/out, super_net/in/out, ddx, ddy)
- mx-stocks-screener:5 个 net(main_net/big_net/super_net) + 完整行情
                   (无 main_in/out, ddx=0, ddy=0, mid_net/small_net=0)
- push2delay:5 个 net(main_net/big_net/super_net/small_net/mid_net)
            (无 main_in/out, ddx=0, ddy=0)

调用方:indicators/signal_unified.py

v6.13 (2026-06-25) 变更:
  - 新增 mx-stocks-screener 作为第二级 fallback(独立端点 selectSecurity)
  - screener 触发了风控(实测 ~22 次后失败,5+ 分钟不恢复),但仍作为备选
  - 优先级:妙想 > screener > push2delay
"""
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# 妙想数据 (主源,v6.3+)
from data_provider.miaochang_money_flow import get_money_flow_via_miaochang

# mx-stocks-screener (第二级 fallback, v6.13+)
try:
    from data_provider.screener_money_flow import get_money_flow_via_screener
except ImportError as e:
    logging.warning(f"[MoneyFlow] screener 模块导入失败: {e}")
    get_money_flow_via_screener = None

# push2delay 公开端点 (第三级 fallback,v6.9+)
_SCRIPTS_DIR = Path.home() / ".openclaw" / "workspace" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from fund_flow_api import fetch_public_flow
except ImportError as e:
    logging.warning(f"[MoneyFlow] push2delay 模块导入失败: {e}")
    fetch_public_flow = None

logger = logging.getLogger("MoneyFlow")

# 15 分钟缓存(避免同一股票 1 分钟内重复请求)
CACHE_TTL_SECONDS = 900
_cache: Dict[str, tuple] = {}  # code -> (timestamp, MoneyFlowData)


@dataclass
class MoneyFlowData:
    """资金流数据结构"""
    code: str           # 股票代码
    name: str           # 股票名称
    main_net: float     # 主力净流入（万元）
    main_in: float      # 主力流入（万元）
    main_out: float     # 主力流出（万元）
    big_net: float      # 大单净流入（万元）
    big_in: float       # 大单流入（万元）
    big_out: float      # 大单流出（万元）
    super_net: float    # 超大单净流入（万元）
    super_in: float     # 超大单流入（万元）
    super_out: float    # 超大单流出（万元）
    small_net: float    # 小单净流入（万元）
    mid_net: float      # 中单净流入（万元）
    ddx: float          # DDX 指标
    ddy: float          # DDY 指标
    date: str           # 数据日期
    source: str = ""    # 数据来源("miaochang"/"screener"/"push2delay")- v6.13 新增

    @property
    def is_main_net_inflow(self) -> bool:
        return self.main_net > 0

    @property
    def is_big_net_inflow(self) -> bool:
        return self.big_net > 0

    @property
    def is_safe(self) -> bool:
        return self.main_net > 0 or self.big_net > 0

    @property
    def signal(self) -> str:
        if self.main_net > 0 and self.big_net > 0:
            return "主力+大单双净流入"
        elif self.main_net > 0:
            return "主力净流入"
        elif self.big_net > 0:
            return "大单净流入"
        elif self.main_net < 0 and self.big_net < 0:
            return "主力+大单双净流出"
        elif self.main_net < 0:
            return "主力净流出"
        elif self.big_net < 0:
            return "大单净流出"
        return "资金平衡"

    def veto_reason(self) -> str:
        reasons = []
        if self.main_net < 0:
            reasons.append(f"主力净流出{self.main_net:.0f}万")
        if self.big_net < 0:
            reasons.append(f"大单净流出{self.big_net:.0f}万")
        if self.ddx < 0:
            reasons.append(f"DDX={self.ddx:.3f}空头")
        return " + ".join(reasons) if reasons else ""


def get_money_flow(code: str, name: str = "") -> Optional[MoneyFlowData]:
    """
    获取单只股票资金流数据 (v6.13 三级 fallback)

    流程:
      1. 15 分钟缓存(每只股票独立)
      2. 妙想数据(主源, 实时, 字段全,~47次/日)
      3. mx-stocks-screener(第二级, 独立端点, 字段略少)
      4. push2delay(第三级, 15分钟延迟, 字段不全,仅收盘后有效)

    Args:
        code: 股票代码,如 "000629"
        name: 股票名称,如 "钒钛股份"

    Returns:
        MoneyFlowData 或 None(全部失败时)
    """
    now = time.time()

    # 1️⃣ 15 分钟缓存
    cached = _cache.get(code)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        logger.debug(f"[MoneyFlow] 缓存命中: {code} (age={int(now-cached[0])}s)")
        return cached[1]

    # 2️⃣ 主源:妙想数据 (实时, 字段全)
    mc_data = get_money_flow_via_miaochang(code, name)
    if mc_data:
        logger.info(f"[MoneyFlow] 妙想数据获取成功: 主力净流入={mc_data['main_net']:.0f}万")
        result = MoneyFlowData(
            code=mc_data["code"],
            name=mc_data["name"],
            main_net=mc_data["main_net"],
            main_in=mc_data["main_in"],
            main_out=mc_data["main_out"],
            big_net=mc_data["big_net"],
            big_in=mc_data["big_in"],
            big_out=mc_data["big_out"],
            super_net=mc_data["super_net"],
            super_in=mc_data.get("super_in", 0.0),
            super_out=mc_data.get("super_out", 0.0),
            small_net=mc_data["small_net"],
            mid_net=mc_data.get("mid_net", 0.0),
            ddx=mc_data["ddx"],
            ddy=mc_data["ddy"],
            date=mc_data.get("date", ""),
            source="miaochang",
        )
        _cache[code] = (now, result)
        return result

    # 3️⃣ 第二级 fallback:mx-stocks-screener (独立端点, 字段略少)
    if get_money_flow_via_screener:
        try:
            scr_data = get_money_flow_via_screener(code, name)
            if scr_data:
                logger.info(
                    f"[MoneyFlow] screener 兜底成功: "
                    f"主力={scr_data['main_net']:+.0f}万"
                )
                result = MoneyFlowData(
                    code=scr_data["code"],
                    name=scr_data["name"],
                    main_net=scr_data["main_net"],
                    main_in=scr_data.get("main_in", 0.0),
                    main_out=scr_data.get("main_out", 0.0),
                    big_net=scr_data["big_net"],
                    big_in=scr_data.get("big_in", 0.0),
                    big_out=scr_data.get("big_out", 0.0),
                    super_net=scr_data["super_net"],
                    super_in=scr_data.get("super_in", 0.0),
                    super_out=scr_data.get("super_out", 0.0),
                    small_net=scr_data.get("small_net", 0.0),
                    mid_net=scr_data.get("mid_net", 0.0),
                    ddx=scr_data.get("ddx", 0.0),  # screener 不提供
                    ddy=scr_data.get("ddy", 0.0),  # screener 不提供
                    date=scr_data.get("date", ""),
                    source="screener",
                )
                _cache[code] = (now, result)
                return result
        except Exception as e:
            logger.warning(f"[MoneyFlow] screener 兜底失败: {e}")

    # 4️⃣ 第三级 fallback:push2delay (15 分钟延迟, 字段不全)
    if fetch_public_flow:
        try:
            pub_data = fetch_public_flow(code, name)
            if pub_data:
                logger.info(f"[MoneyFlow] push2delay 兜底成功: 主力净流入={pub_data['main_net']:+.0f}万")
                result = MoneyFlowData(
                    code=pub_data["code"],
                    name=pub_data["name"],
                    main_net=pub_data["main_net"],
                    main_in=0.0,        # push2delay 不提供
                    main_out=0.0,
                    big_net=pub_data["big_net"],
                    big_in=0.0,
                    big_out=0.0,
                    super_net=pub_data["super_net"],
                    super_in=0.0,
                    super_out=0.0,
                    small_net=pub_data["small_net"],
                    mid_net=pub_data["mid_net"],
                    ddx=0.0,            # push2delay 不提供
                    ddy=0.0,
                    date=pub_data["date"],
                    source="push2delay",
                )
                _cache[code] = (now, result)
                return result
        except Exception as e:
            logger.warning(f"[MoneyFlow] push2delay 兜底失败: {e}")

    logger.warning(f"[MoneyFlow] 全部资金流获取方式均失败: code={code}")
    return None
