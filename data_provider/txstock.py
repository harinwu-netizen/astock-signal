# -*- coding: utf-8 -*-
"""
腾讯财经数据获取
A股/指数/基金 支持

API文档参考: https://gu.qq.com/
"""

import logging
import time
import random
from datetime import datetime
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)

# 腾讯财经 API
# 历史K线: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayqfq&param=sh000001,day,,,320,qfq
# 实时行情: https://qt.gtimg.cn/q=sz000629


def _get_url(mode: str, code: str, **kwargs) -> str:
    """
    构建腾讯财经API URL

    mode: history - 历史K线
          realtime - 实时行情
          minute - 分时数据
    """
    # 标准化代码前缀
    if code.startswith(("sh", "sz", "sh", "sz")):
        pass  # 已带前缀
    elif len(code) == 6 and code.startswith(("0", "3", "6")):
        # A股主板/创业板/科创板
        code = "sz" + code if code.startswith(("0", "3")) else "sh" + code
    elif len(code) == 8 and code.startswith(("hk", "HK")):
        pass  # 港股
    elif len(code) == 6 and code.isdigit():
        # 默认深市
        code = "sz" + code

    if mode == "history":
        days = kwargs.get("days", 60)
        return (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?_var=kline_dayqfq&param={code},day,,,{days},qfq"
        )
    elif mode == "realtime":
        return f"https://qt.gtimg.cn/q={code}"
    elif mode == "minute":
        return f"https://web.ifzq.gtimg.cn/appstock/app/n分时/query?_var=n_qfq_{code}&code={code}"
    return ""


def _parse_history_data(raw_text: str, code: str) -> Optional[Tuple[List, str]]:
    """
    解析腾讯财经历史K线数据
    返回: (DataFrame数据列表, 数据源名称)
    """
    try:
        # 腾讯返回格式: var kline_dayqfq={...}
        # 去掉变量声明
        json_str = raw_text.split("=", 1)[1] if "=" in raw_text else raw_text
        import json
        data = json.loads(json_str)

        # 提取K线数据
        # 格式: [日期, 开, 收, 高, 低,成交量(手), 成交额, 振幅, 涨跌幅, 涨跌额, 换手率]
        qfq_data = data.get("data", {}).get(code, {}).get("qfqday", [])
        if not qfq_data:
            # 尝试非复权数据
            day_data = data.get("data", {}).get(code, {}).get("day", [])
            qfq_data = day_data

        if not qfq_data:
            return None

        records = []
        for item in qfq_data:
            if len(item) >= 6:
                records.append({
                    "date": item[0],
                    "open": float(item[1]),
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "volume": int(float(item[5])),
                })

        return records, "腾讯财经"
    except Exception as e:
        logger.error(f"解析历史数据失败: {e}")
        return None


def _parse_realtime_data(raw_text: str, code: str) -> Optional[dict]:
    """
    解析腾讯财经实时行情
    返回字段: 名称, 当前价, 昨收, 今开, 成交量(手), 外盘, 内盘,
             最高, 最低, 涨跌, 涨跌幅, 换手率, 市盈率, 总市值, 流通市值等
    """
    try:
        # 格式: v_pv0="sz000629~name~当前价~昨收~今开~成交量~外盘~内盘~最高~最低~涨跌~涨跌幅~换手率~市盈率~总市值~流通市值..."
        # 去除 var p
        lines = raw_text.strip().split("\n")
        if not lines:
            return None

        parts = lines[0].split("~")
        if len(parts) < 40:
            return None

        current_price = float(parts[3]) if parts[3] else 0.0
        prev_close = float(parts[4]) if parts[4] else 0.0
        open_price = float(parts[5]) if parts[5] else 0.0
        volume = int(parts[6]) if parts[6] else 0  # 成交量（手）
        high = float(parts[33]) if parts[33] else 0.0
        low = float(parts[34]) if parts[34] else 0.0

        # 涨跌幅
        change_pct = 0.0
        if prev_close and prev_close > 0:
            change_pct = (current_price - prev_close) / prev_close * 100

        # 换手率
        turnover_rate = float(parts[37]) if len(parts) > 37 and parts[37] else 0.0

        # 量比 (腾讯没有直接提供，从外盘内盘比估算)
        volume_ratio = 0.0

        # 市盈率
        pe = float(parts[39]) if len(parts) > 39 and parts[39] else 0.0

        return {
            "code": code,
            "name": parts[1] if len(parts) > 1 else code,
            "price": current_price,
            "prev_close": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "change_pct": change_pct,
            "turnover_rate": turnover_rate,
            "pe": pe,
            "source": "腾讯财经",
        }
    except Exception as e:
        logger.error(f"解析实时行情失败: {e}")
        return None


class TxStock:
    """腾讯财经数据获取器"""

    def __init__(self):
        self.name_cache = {}  # 代码→名称缓存

    def get_history(self, code: str, days: int = 60) -> Optional[List[dict]]:
        """
        获取历史K线数据

        Args:
            code: 股票代码，如 "000629"（自动加深交所前缀）
            days: 获取天数

        Returns:
            List[dict], 每条包含 date/open/high/low/close/volume
        """
        # 标准化代码
        if not code.startswith(("sh", "sz")):
            if len(code) == 6 and code.isdigit():
                code = "sz" + code if code.startswith(("0", "3")) else "sh" + code

        url = _get_url("history", code, days=days)

        try:
            import requests
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()

            # 修复编码问题
            resp.encoding = "gbk"
            raw = resp.text

            result = _parse_history_data(raw, code)
            if result:
                records, source = result
                logger.debug(f"获取 {code} 历史数据成功，共 {len(records)} 条 ({source})")
                return records
            return None
        except Exception as e:
            logger.error(f"获取 {code} 历史数据失败: {e}")
            return None

    def get_realtime(self, code: str) -> Optional[dict]:
        """
        获取实时行情

        Args:
            code: 股票代码，如 "000629"

        Returns:
            dict, 包含 price/name/change_pct/turnover_rate 等字段
        """
        # 标准化代码
        if not code.startswith(("sh", "sz", "hk")):
            if len(code) == 6 and code.isdigit():
                code = "sz" + code if code.startswith(("0", "3")) else "sh" + code

        url = _get_url("realtime", code)

        try:
            import requests
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            resp.encoding = "gbk"
            raw = resp.text

            result = _parse_realtime_data(raw, code)
            if result:
                # 缓存名称
                self.name_cache[code] = result["name"]
                logger.debug(f"获取 {code} 实时行情: {result['name']} ¥{result['price']}")
            return result
        except Exception as e:
            logger.error(f"获取 {code} 实时行情失败: {e}")
            return None

    def get_name(self, code: str) -> str:
        """
        获取股票名称（优先从缓存获取）
        """
        if code in self.name_cache:
            return self.name_cache[code]

        # 临时获取一次实时行情来获取名称
        realtime = self.get_realtime(code)
        if realtime:
            return realtime.get("name", code)
        return code

    def get_index_realtime(self, index_code: str = "sh000001") -> Optional[dict]:
        """
        获取大盘指数实时行情

        Args:
            index_code: 指数代码
                sh000001 - 上证指数
                sz399001 - 深证成指
                sz399006 - 创业板指
        """
        return self.get_realtime(index_code)

    def batch_get_realtime(self, codes: List[str]) -> List[Optional[dict]]:
        """
        批量获取实时行情（用逗号拼接URL减少请求）

        腾讯支持批量: https://qt.gtimg.cn/q=sz000629,sh000001,sh600519
        """
        if not codes:
            return []

        # 标准化代码
        normalized = []
        for code in codes:
            if not code.startswith(("sh", "sz", "hk")):
                if len(code) == 6 and code.isdigit():
                    code = "sz" + code if code.startswith(("0", "3")) else "sh" + code
            normalized.append(code)

        # 批量URL
        codes_str = ",".join(normalized)
        url = f"https://qt.gtimg.cn/q={codes_str}"

        try:
            import requests
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            resp.encoding = "gbk"
            raw = resp.text

            results = []
            for code in normalized:
                code_clean = code.replace("sz", "").replace("sh", "")
                # 用返回文本的位置来匹配
                lines = raw.split("\n")
                found = False
                for line in lines:
                    if code in line or code_clean in line:
                        result = _parse_realtime_data(line, code)
                        if result:
                            results.append(result)
                            found = True
                        break
                if not found:
                    results.append(None)

            return results
        except Exception as e:
            logger.error(f"批量获取实时行情失败: {e}")
            return [None] * len(codes)
