"""
资金流数据获取器
使用东方财富 EM_API_KEY 获取主力资金流数据
"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("MoneyFlow")

API_URL = "https://ai-saas.eastmoney.com/proxy/app-robo-advisor-api/assistant/ask"
API_KEY = os.environ.get("EM_API_KEY", "")


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
    ddx: float         # DDX 指标
    ddy: float          # DDY 指标
    date: str           # 数据日期

    @property
    def is_main_net_inflow(self) -> bool:
        """主力是否净流入"""
        return self.main_net > 0

    @property
    def is_big_net_inflow(self) -> bool:
        """大单是否净流入"""
        return self.big_net > 0

    @property
    def is_safe(self) -> bool:
        """资金面是否安全（主力净流入 或 大单净流入）"""
        return self.main_net > 0 or self.big_net > 0

    @property
    def signal(self) -> str:
        """资金流信号描述"""
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
        """返回否决原因（资金流出时）"""
        reasons = []
        if self.main_net < 0:
            reasons.append(f"主力净流出{self.main_net:.0f}万")
        if self.big_net < 0:
            reasons.append(f"大单净流出{self.big_net:.0f}万")
        if self.ddx < 0:
            reasons.append(f"DDX={self.ddx:.3f}空头")
        return " + ".join(reasons) if reasons else ""


def _parse_table_value(table_md: str, key: str) -> Optional[float]:
    """从 markdown 表格中提取指定 key 的数值（第二列）"""
    lines = table_md.strip().split("\n")
    for line in lines:
        # 跳过表头行和分隔符行
        stripped = line.strip()
        if not stripped or stripped.startswith('| ---') or '数据如下' in stripped:
            continue
        if key not in stripped or '|' not in stripped:
            continue
        
        parts = [p.strip() for p in stripped.split('|')]
        # parts = ['', 'key', 'value', ...] 或 ['', 'key', 'value']
        # 我们要的是 key 之后的第一列数值
        for i, part in enumerate(parts):
            if key in part:
                # 找下一列
                if i + 1 < len(parts):
                    val_str = parts[i + 1].strip()
                    val = _convert_unit(val_str)
                    if val is not None:
                        return val
    return None


def _convert_unit(val_str: str) -> Optional[float]:
    """将带单位的字符串转为万元数值"""
    if not val_str:
        return None
    # 去掉逗号
    val_str = val_str.replace(",", "")
    is_yi = "亿" in val_str
    val_str = val_str.replace("亿", "").replace("万元", "").replace("万", "").strip()
    try:
        val = float(val_str)
        return val * 10000 if is_yi else val
    except ValueError:
        return None


def _parse_markdown_tables(refs: List[dict]) -> Dict[str, float]:
    """解析所有参考数据的 markdown 表格，提取资金流数值"""
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
                continue  # 已提取过，跳过
            for kw in keywords:
                val = _parse_table_value(markdown, kw)
                if val is not None:
                    result[field_name] = val
                    break

    return result


def get_money_flow(code: str, name: str = "") -> Optional[MoneyFlowData]:
    """
    获取单只股票资金流数据
    
    Args:
        code: 股票代码，如 "000629"
        name: 股票名称，如 "钒钛股份"
    
    Returns:
        MoneyFlowData 或 None（失败时）
    """
    if not API_KEY:
        logger.warning("EM_API_KEY 未设置，无法获取资金流数据")
        return None

    # 标准化代码
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
            logger.error(f"资金流 API 返回错误: {result}")
            return None

        ref_list = result.get("data", {}).get("refIndexList", [])
        vals = _parse_markdown_tables(ref_list)

        if not vals:
            logger.warning(f"未解析到资金流数据，code={code}")
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
            ddx=vals.get("DDX", 0.0),
            ddy=vals.get("DDY", 0.0),
            date="",
        )

    except Exception as e:
        logger.error(f"获取资金流数据失败: {e}")
        return None
