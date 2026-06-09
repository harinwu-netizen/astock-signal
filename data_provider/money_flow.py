"""
资金流数据获取器
使用东方财富 EM_API_KEY 获取主力资金流数据
东方财富超限后自动切换 妙想数据(mx-finance-data) 作为 fallback
v6.8 (2026-06-04): 剔除 QVeris/财达 (QVERIS_API_KEY 从未设置, 100% 失败)
v6.9 (2026-06-04): push2delay 公开端点作为 Step 0 (免费, 几乎不限流)
v6.10 (2026-06-09): 修复 setup_env() 未在模块加载时调用导致 USE_PUSH2DELAY 始终为 False
"""
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# v6.10: 必须在 import 阶段加载 .env, 否则模块级 os.environ.get() 拿不到值
# 原因: money_flow.py 会在 import 时执行 USE_PUSH2DELAY 赋值,
# 此时 Config.load() 还没被调用, .env 也未加载
from config import setup_env
setup_env()

logger = logging.getLogger("MoneyFlow")

API_URL = "https://ai-saas.eastmoney.com/proxy/app-robo-advisor-api/assistant/ask"
API_KEY = os.environ.get("EM_API_KEY", "")

# 妙查 fallback (v6.3,2026-06-03) - v6.8 简化掉 QVeris
from data_provider.miaochang_money_flow import get_money_flow_via_miaochang

# v6.9: push2delay 公开端点 (免费, 几乎不限流)
# 脚本位置: ~/.openclaw/workspace/scripts/fund_flow_api.py
_SCRIPTS_DIR = Path.home() / ".openclaw" / "workspace" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from fund_flow_api import fetch_public_flow
    _HAS_PUSH2DELAY = True
except ImportError as e:
    logger.warning(f"[MoneyFlow] push2delay 模块导入失败: {e}")
    _HAS_PUSH2DELAY = False

# .env 开关: FUND_FLOW_BACKEND=push2delay 启用 Step 0
USE_PUSH2DELAY = os.environ.get("FUND_FLOW_BACKEND", "").lower() in ("push2delay", "public", "1", "true", "yes")


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
    mid_net: float     # 中单净流入（万元，QVeris财达提供）
    ddx: float         # DDX 指标
    ddy: float          # DDY 指标
    date: str           # 数据日期

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


def _parse_table_value(table_md: str, key: str) -> Optional[float]:
    lines = table_md.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('| ---') or '数据如下' in stripped:
            continue
        if key not in stripped or '|' not in stripped:
            continue
        parts = [p.strip() for p in stripped.split('|')]
        for i, part in enumerate(parts):
            if key in part:
                if i + 1 < len(parts):
                    val_str = parts[i + 1].strip()
                    val = _convert_unit(val_str)
                    if val is not None:
                        return val
    return None


def _convert_unit(val_str: str) -> Optional[float]:
    if not val_str:
        return None
    val_str = val_str.replace(",", "")
    is_yi = "亿" in val_str
    val_str = val_str.replace("亿", "").replace("万元", "").replace("万", "").strip()
    try:
        val = float(val_str)
        return val * 10000 if is_yi else val
    except ValueError:
        return None


def _parse_markdown_tables(refs: List[dict]) -> Dict[str, float]:
    result = {}
    field_map = {
        "主力净流入": ["主力净流入", "主力净流入资金", "主力净额"],
        "主力流入": ["主力流入", "主力流入资金"],
        "主力流出": ["主力流出", "主力流出资金"],
        "大单净流入": ["大单净流入", "大单净额", "大单净流入资金"],
        "大单流入": ["大单流入"],
        "大单流出": ["大单流出"],
        "超大单净流入": ["超大单净额", "超大单净流入"],
        "超大单流入": ["超大单流入"],
        "超大单流出": ["超大单流出"],
        "小单净流入": ["小单净额", "小单净流入"],
        "DDX": ["DDX"],
        "DDY": ["DDY"],
    }
    for ref in refs:
        if ref.get("type") != "查数":
            continue
        markdown = ref.get("markdown", "")
        if not markdown:
            continue
        for field_name, keywords in field_map.items():
            if field_name in result:
                continue
            for kw in keywords:
                val = _parse_table_value(markdown, kw)
                if val is not None:
                    result[field_name] = val
                    break
    return result


def get_money_flow(code: str, name: str = "") -> Optional[MoneyFlowData]:
    """
    获取单只股票资金流数据（两级 fallback，v6.8 简化）
    1. 东方财富(EM_API_KEY) — 优先
    2. 妙想数据(mx-finance-data) — 备选 (v6.3 新增)

    v6.8: 剔除 QVeris/财达 (100% 失败, QVERIS_API_KEY 从未配置)

    Args:
        code: 股票代码，如 "000629"
        name: 股票名称，如 "钒钛股份"

    Returns:
        MoneyFlowData 或 None（失败时）
    """
    # ===== Step 0: push2delay 公开端点 (v6.9,2026-06-04) =====
    # 免费, 几乎不限流, 作为最高优先级 fallback
    # 启用条件: .env 设 FUND_FLOW_BACKEND=push2delay (默认未启用)
    if USE_PUSH2DELAY and _HAS_PUSH2DELAY:
        try:
            pub_data = fetch_public_flow(code, name)
            if pub_data:
                logger.info(f"[MoneyFlow] push2delay 成功: 主力净流入={pub_data['main_net']:+.0f}万")
                return MoneyFlowData(
                    code=pub_data["code"],
                    name=pub_data["name"],
                    main_net=pub_data["main_net"],
                    main_in=0.0,        # push2delay 不提供此字段
                    main_out=0.0,
                    big_net=pub_data["big_net"],
                    big_in=0.0,
                    big_out=0.0,
                    super_net=pub_data["super_net"],
                    super_in=0.0,
                    super_out=0.0,
                    small_net=pub_data["small_net"],
                    mid_net=pub_data["mid_net"],
                    ddx=0.0,             # push2delay 不提供此字段
                    ddy=0.0,
                    date=pub_data["date"],
                )
        except Exception as e:
            logger.warning(f"[MoneyFlow] push2delay 失败: {e}")

    # ===== Step 1: 尝试东方财富 =====
    if API_KEY:
        result = _get_money_flow_eastmoney(code, name)
        if result is not None:
            return result

    # ===== Step 2: Fallback — 妙想数据 (v6.3,2026-06-03) =====
    logger.info("[MoneyFlow] 东方财富不可用，尝试妙想数据(mx-finance-data)...")
    mc_data = get_money_flow_via_miaochang(code, name)
    if mc_data:
        logger.info(f"[MoneyFlow] 妙想数据获取成功: 主力净流入={mc_data['main_net']:.0f}万")
        return MoneyFlowData(
            code=mc_data["code"],
            name=mc_data["name"],
            main_net=mc_data["main_net"],
            main_in=mc_data["main_in"],
            main_out=mc_data["main_out"],
            big_net=mc_data["big_net"],
            big_in=mc_data["big_in"],
            big_out=mc_data["big_out"],
            super_net=mc_data["super_net"],
            super_in=mc_data["super_in"],
            super_out=mc_data["super_out"],
            small_net=mc_data["small_net"],
            mid_net=mc_data.get("mid_net", 0.0),
            ddx=mc_data["ddx"],
            ddy=mc_data["ddy"],
            date=mc_data.get("date", ""),
        )

    logger.warning(f"[MoneyFlow] 全部资金流获取方式均失败: code={code}")
    return None


def _get_money_flow_eastmoney(code: str, name: str = "") -> Optional[MoneyFlowData]:
    """东方财富资金流获取（内部函数）"""
    normalized = code
    for p in ("sh", "sz", "SH", "SZ"):
        if normalized.startswith(p):
            normalized = normalized[2:]

    question = (
        f"获取{normalized}（{name}）今日资金流向，"
        "包括主力净流入、大单净流入、超大单净流入、DDX、DDY等指标的具体数值（万元）"
    )

    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    API_URL,
                    json={"question": question},
                    headers={
                        "Content-Type": "application/json",
                        "em_api_key": API_KEY,
                        "Accept": "application/json",
                        "User-Agent": "mx-financial-assistant/1.0",
                    },
                )
                return resp.json()

        result = asyncio.run(_fetch())

        if result.get("code") != 200:
            logger.error(f"[EM] 资金流 API 返回错误: {result}")
            return None

        ref_list = result.get("data", {}).get("refIndexList", [])
        vals = _parse_markdown_tables(ref_list)

        if not vals:
            logger.warning(f"[EM] 未解析到资金流数据: code={code}")
            return None

        return MoneyFlowData(
            code=normalized,
            name=name or normalized,
            main_net=vals.get("主力净流入", 0.0),
            main_in=vals.get("主力流入", 0.0),
            main_out=vals.get("主力流出", 0.0),
            big_net=vals.get("大单净流入", 0.0),
            big_in=vals.get("大单流入", 0.0),
            big_out=vals.get("大单流出", 0.0),
            super_net=vals.get("超大单净流入", 0.0),
            super_in=vals.get("超大单流入", 0.0),
            super_out=vals.get("超大单流出", 0.0),
            small_net=vals.get("小单净流入", 0.0),
            mid_net=0.0,
            ddx=vals.get("DDX", 0.0),
            ddy=vals.get("DDY", 0.0),
            date="",
        )

    except Exception as e:
        logger.error(f"[EM] 获取资金流数据异常: {e}")
        return None
