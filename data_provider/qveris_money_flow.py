"""
资金流数据获取器 — QVeris/财达 适配器

当东方财富 EM_API_KEY 超限或不可用时，
通过 QVeris 平台调用财达(caidazi)资金流 API 获取个股资金流向数据。

依赖：QVERIS_API_KEY 环境变量
工具：caidazi.get_stock_moneyflow.execute.v1.7a43f96e
"""
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger("QVerisMoneyFlow")

BASE_URL = "https://qveris.ai/api/v1"
TOOL_ID = "caidazi.get_stock_moneyflow.execute.v1.7a43f96e"
QVERIS_API_KEY = os.environ.get("QVERIS_API_KEY", "")


def _parse_caidazi_markdown(md_text: str, date: str = "") -> dict:
    """
    从财达返回的 markdown 文本中解析出各档资金流数值。

    财达返回格式示例：
    ## 000629.SZ (钒钛股份) - 资金流向数据
    ### 基础信息
    - 交易日期: 20260506
    - 最新价: 3.87
    - 涨跌幅: 6.32%
    ### 主力资金流向
    - **主力净流入额**: 9696.61 万元 (净流入)
    - **主力净流入占比**: 9.29%
    ### 分单净流入明细
    | 类型 | 净流入额(万元) | 占比(%) |
    | 超大单 | 9443.35 | 9.05 |
    | 大单 | 253.26 | 0.24 |
    | 中单 | -3410.68 | -3.27 |
    | 小单 | -6285.92 | -6.02 |
    """
    result = {"交易日期": date}

    # 解析表格: | 类型 | 净流入额(万元) | 占比(%) |
    #           | 超大单 | 9443.35 | 9.05 |
    table_pattern = re.compile(
        r'\|\s*([^|]+?)\s*\|\s*([\d.,-]+?)\s*\|',
        re.MULTILINE
    )
    for row_match in table_pattern.finditer(md_text):
        label = row_match.group(1).strip()
        value_str = row_match.group(2).strip().replace(",", "")

        # 映射标签到字段名
        field_map = {
            "超大单": "超大单净流入额",
            "大单": "大单净流入额",
            "中单": "中单净流入额",
            "小单": "小单净流入额",
        }
        if label in field_map:
            try:
                result[field_map[label]] = float(value_str)
            except ValueError:
                pass

    # 从列表项中提取主力净流入（表格可能没有，用列表项兜底）
    bullet_pattern = re.compile(r'\*\*主力净流入额\*\*[:：]?\s*([0-9.,-]+)\s*万元')
    for m in bullet_pattern.finditer(md_text):
        val_str = m.group(1).replace(",", "").strip()
        try:
            result.setdefault("主力净流入额", float(val_str))
        except ValueError:
            pass

    return result


def get_money_flow_via_qveris(code: str, name: str = "") -> Optional[dict]:
    """
    通过 QVeris/财达 获取个股资金流数据，返回原始字典。

    返回字典字段（与 MoneyFlowData 兼容）：
        code, name, main_net, main_in, main_out,
        big_net, big_in, big_out,
        super_net, super_in, super_out,
        small_net, mid_net, ddx, ddy, date

    失败返回 None。
    """
    if not QVERIS_API_KEY:
        logger.warning("QVERIS_API_KEY 未设置，无法通过 QVeris 获取资金流")
        return None

    # 标准化代码
    normalized = code
    for p in ("sh", "sz", "SH", "SZ"):
        if normalized.startswith(p):
            normalized = normalized[2:]

    # 财达接口接受 format: 000629.SZ 或 000629.SH
    qveris_symbol = f"{normalized}.SZ"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{BASE_URL}/tools/execute",
                params={"tool_id": TOOL_ID},
                headers={
                    "Authorization": f"Bearer {QVERIS_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "search_id": "",
                    "parameters": {"symbol": qveris_symbol},
                    "max_response_size": 4096,
                },
            )

        if resp.status_code != 200:
            logger.error(f"QVeris API HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        r = resp.json()

        # QVeris 响应结构:
        # {
        #   "success": true,
        #   "result": {
        #     "status_code": 200,
        #     "data": {
        #       "success": true,
        #       "result": "## markdown...",
        #       "error": null,
        #     }
        #   }
        # }
        caidazi_data = r.get("result", {}).get("data", {})
        if not caidazi_data.get("success"):
            logger.warning(f"QVeris 财达调用失败: {caidazi_data.get('error')}")
            return None

        md_text = caidazi_data.get("result", "")
        raw = _parse_caidazi_markdown(md_text, caidazi_data.get("交易日期", ""))
        if not raw or ("超大单净流入额" not in raw and "主力净流入额" not in raw):
            logger.warning(f"QVeris 财达数据解析失败: {md_text[:200]}")
            return None

        super_net = raw.get("超大单净流入额", 0.0)
        big_net   = raw.get("大单净流入额", 0.0)
        mid_net   = raw.get("中单净流入额", 0.0)
        small_net = raw.get("小单净流入额", 0.0)
        main_net  = raw.get("主力净流入额", super_net + big_net)

        return {
            "code": normalized,
            "name": name or normalized,
            "main_net": main_net,
            "main_in": 0.0,
            "main_out": 0.0,
            "big_net": big_net,
            "big_in": 0.0,
            "big_out": 0.0,
            "super_net": super_net,
            "super_in": 0.0,
            "super_out": 0.0,
            "small_net": small_net,
            "mid_net": mid_net,
            "ddx": 0.0,
            "ddy": 0.0,
            "date": raw.get("交易日期", ""),
        }

    except httpx.TimeoutException:
        logger.error("QVeris API 请求超时")
        return None
    except Exception as e:
        logger.error(f"QVeris 资金流获取失败: {e}")
        return None
