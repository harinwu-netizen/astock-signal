"""
资金流数据获取器
使用东方财富 EM_API_KEY 获取主力资金流数据
东方财富超限后自动切换 QVeris/财达 作为 fallback
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger("MoneyFlow")

API_URL = "https://ai-saas.eastmoney.com/proxy/app-robo-advisor-api/assistant/ask"
API_KEY = os.environ.get("EM_API_KEY", "")

# QVeris fallback
from data_provider.qveris_money_flow import get_money_flow_via_qveris
# 妙查 fallback (v6.3,2026-06-03)
from data_provider.miaochang_money_flow import get_money_flow_via_miaochang


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
    获取单只股票资金流数据（三级 fallback）
    1. 东方财富(EM_API_KEY) — 优先
    2. QVeris/财达 — 备选
    3. 妙查(mx-finance-data) — 最后备选(2026-06-03 新增)

    Args:
        code: 股票代码，如 "000629"
        name: 股票名称，如 "钒钛股份"

    Returns:
        MoneyFlowData 或 None（失败时）
    """
    # ===== Step 1: 尝试东方财富 =====
    if API_KEY:
        result = _get_money_flow_eastmoney(code, name)
        if result is not None:
            return result

    # ===== Step 2: Fallback — QVeris/财达 =====
    logger.info("[MoneyFlow] 东方财富不可用，尝试 QVeris/财达...")
    qveris_data = get_money_flow_via_qveris(code, name)
    if qveris_data:
        logger.info(f"[MoneyFlow] QVeris/财达获取成功: 主力净流入={qveris_data['main_net']:.0f}万")
        return MoneyFlowData(
            code=qveris_data["code"],
            name=qveris_data["name"],
            main_net=qveris_data["main_net"],
            main_in=qveris_data["main_in"],
            main_out=qveris_data["main_out"],
            big_net=qveris_data["big_net"],
            big_in=qveris_data["big_in"],
            big_out=qveris_data["big_out"],
            super_net=qveris_data["super_net"],
            super_in=qveris_data["super_in"],
            super_out=qveris_data["super_out"],
            small_net=qveris_data["small_net"],
            mid_net=qveris_data.get("mid_net", 0.0),
            ddx=qveris_data["ddx"],
            ddy=qveris_data["ddy"],
            date=qveris_data["date"],
        )

    # ===== Step 3: Fallback — 妙查 (v6.3,2026-06-03) =====
    logger.info("[MoneyFlow] QVeris/财达不可用，尝试妙查...")
    mc_data = get_money_flow_via_miaochang(code, name)
    if mc_data:
        logger.info(f"[MoneyFlow] 妙查获取成功: 主力净流入={mc_data['main_net']:.0f}万")
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
