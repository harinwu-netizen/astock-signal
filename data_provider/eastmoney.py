# -*- coding: utf-8 -*-
"""
东方财富数据获取
A股/指数 支持

接口与 txstock.py 完全一致，可直接互换：
- get_history()      → 历史K线（前复权）
- get_realtime()     → 实时行情
- get_name()         → 股票名称
- get_index_realtime() → 大盘指数
- batch_get_realtime()  → 批量实时

数据源：
- A股：efinance 库（已装）
- 指数：东方财富 direct API
"""

import logging
import requests
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)

# 东方财富指数 API
EM_INDEX_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EM_INDEX_FIELDS = "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170,f171"
EM_INDEX_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 指数代码映射（东方财富 secid 格式）
# secid = 市场号.代码  →  1=上交所, 0=深交所
INDEX_SECID_MAP = {
    "000001": "1.000001",   # 上证指数
    "399001": "0.399001",   # 深证成指
    "399006": "0.399006",   # 创业板指
    "000688": "1.000688",   # 科创50
    # 兼容带 sh/sz 前缀
    "sh000001": "1.000001",
    "sz399001": "0.399001",
    "sz399006": "0.399006",
    "sh000688": "1.000688",
}


# ============================================================================
# 内部工具函数
# ============================================================================

def _parse_em_index(code: str, data: dict) -> dict:
    """
    解析东方财富指数实时数据
    字段说明：https://push2.eastmoney.com/api/qt/stock/get
    """
    price = data.get("f43", 0) / 100      # 最新价（单位：元）
    prev_close = data.get("f60", 0) / 100  # 昨收
    open_price = data.get("f44", 0) / 100  # 开盘
    high = data.get("f45", 0) / 100      # 最高
    low = data.get("f46", 0) / 100       # 最低
    volume = int(data.get("f47", 0))      # 成交量（股）
    amount = data.get("f48", 0)           # 成交额（元）
    change_pct = data.get("f170", 0) / 100  # 涨跌幅（%）
    name = data.get("f58", code)

    return {
        "code": code,
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "change_pct": change_pct,
        "turnover_rate": 0.0,   # 指数无换手率概念
        "pe": 0.0,              # 指数无PE
        "source": "东方财富",
    }


def _ef_history_to_records(df) -> List[dict]:
    """
    将 efinance DataFrame 转为统一格式

    统一格式（与 txstock.py 一致）：
    [{"date": "2026-03-01", "open": 3.20, "high": 3.30,
      "low": 3.18, "close": 3.25, "volume": 123456}, ...]
    """
    import pandas as pd

    if df is None or df.empty:
        return []

    # 建立中英文列名映射
    col_map = {}
    for col in df.columns:
        cl = str(col).strip().lower()
        cn = str(col).strip()
        if "日期" in cn or "date" in cl:
            col_map["date"] = col
        elif "开盘" in cn or "open" in cl:
            col_map["open"] = col
        elif "最高" in cn or "high" in cl:
            col_map["high"] = col
        elif "最低" in cn or "low" in cl:
            col_map["low"] = col
        elif "收盘" in cn or "close" in cl:
            col_map["close"] = col
        elif ("成交量" in cn or "vol" in cl or "volume" in cl) and "成交额" not in cn:
            col_map["volume"] = col

    records = []
    for _, row in df.iterrows():
        try:
            rec = {
                "date": str(row[col_map["date"]]),
                "open": float(row[col_map["open"]]),
                "high": float(row[col_map["high"]]),
                "low": float(row[col_map["low"]]),
                "close": float(row[col_map["close"]]),
                "volume": int(float(row[col_map["volume"]])),
            }
            records.append(rec)
        except (KeyError, ValueError, TypeError):
            continue

    return records


def _ef_snapshot_to_realtime(code: str, snap) -> Optional[dict]:
    """
    将 efinance get_quote_snapshot 结果转为统一实时行情格式
    """
    import pandas as pd

    if snap is None or snap.empty:
        return None

    def get_val(key):
        val = snap.get(key)
        return val if pd.notna(val) else None

    price = get_val("最新价") or get_val("close")
    prev_close = get_val("昨收") or get_val("prev_close")
    open_price = get_val("今开") or get_val("open") or get_val("开盘")
    high = get_val("最高") or get_val("high")
    low = get_val("最低") or get_val("low")
    volume = get_val("成交量") or 0
    turnover = get_val("换手率") or 0
    name = get_val("名称") or code

    change_pct = 0.0
    if prev_close and prev_close > 0 and price:
        change_pct = (price - prev_close) / prev_close * 100

    return {
        "code": code,
        "name": str(name),
        "price": float(price) if price else 0.0,
        "prev_close": float(prev_close) if prev_close else float(price) if price else 0.0,
        "open": float(open_price) if open_price else float(price) if price else 0.0,
        "high": float(high) if high else float(price) if price else 0.0,
        "low": float(low) if low else float(price) if price else 0.0,
        "volume": int(float(volume)) if volume else 0,
        "change_pct": change_pct,
        "turnover_rate": float(turnover) if turnover else 0.0,
        "pe": 0.0,   # snapshot 无PE，用 get_quote_history 的PE
        "source": "东方财富",
    }


# ============================================================================
# 主类（接口与 TxStock 完全一致）
# ============================================================================

class EastMoney:
    """东方财富数据获取器"""

    def __init__(self):
        self.name_cache = {}  # code → 名称

    # ------------------------------------------------------------------------
    # 历史K线（前复权日K）
    # ------------------------------------------------------------------------

    def get_history(self, code: str, days: int = 60) -> Optional[List[dict]]:
        """
        获取历史K线数据（前复权日K）

        Args:
            code: 股票代码，如 "000629"（sh/sz 前缀均可，会自动处理）
            days: 获取天数，默认60

        Returns:
            List[dict], 每条: {date, open, high, low, close, volume}
            None 表示失败
        """
        # 标准化：去掉 sh/sz 前缀
        normalized = code
        for p in ("sh", "sz", "SH", "SZ"):
            if normalized.startswith(p):
                normalized = normalized[2:]
                break

        # 标准化校验：支持A股(6位数字)和板块指数(BK开头6位)
        is_stock = len(normalized) == 6 and normalized.isdigit()
        is_block = len(normalized) == 6 and normalized.startswith("BK") and normalized[2:].isdigit()
        if not (is_stock or is_block):
            logger.warning(f"[EastMoney] 无效代码: {code}")
            return None

        try:
            import efinance as ef
            df = ef.stock.get_quote_history(normalized, klt=101)  # 101=日K前复权
            if df is None or df.empty:
                logger.warning(f"[EastMoney] {code} 历史数据为空")
                return None

            # 取最近 days 条
            if len(df) > days:
                df = df.tail(days)

            records = _ef_history_to_records(df)
            if records:
                logger.debug(f"[EastMoney] {code} 历史数据 {len(records)} 条")
            return records or None

        except ImportError:
            logger.error("[EastMoney] efinance 未安装: pip install efinance>=0.5.5")
            return None
        except Exception as e:
            logger.error(f"[EastMoney] {code} 历史数据失败: {e}")
            return None

    # ------------------------------------------------------------------------
    # 实时行情
    # ------------------------------------------------------------------------

    def get_realtime(self, code: str) -> Optional[dict]:
        """
        获取实时行情

        Args:
            code: 股票代码，如 "000629"

        Returns:
            dict: {code, name, price, prev_close, open, high, low,
                   volume, change_pct, turnover_rate, pe, source}
            None 表示失败
        """
        # 标准化
        normalized = code
        for p in ("sh", "sz", "SH", "SZ"):
            if normalized.startswith(p):
                normalized = normalized[2:]
                break

        # 标准化校验：支持A股(6位数字)和板块指数(BK开头6位)
        is_stock = len(normalized) == 6 and normalized.isdigit()
        is_block = len(normalized) == 6 and normalized.startswith("BK") and normalized[2:].isdigit()
        if not (is_stock or is_block):
            logger.warning(f"[EastMoney] 无效代码: {code}")
            return None

        try:
            import efinance as ef
            snap = ef.stock.get_quote_snapshot(normalized)
            result = _ef_snapshot_to_realtime(normalized, snap)
            if result:
                self.name_cache[code] = result["name"]
                logger.debug(
                    f"[EastMoney] {code} 实时: {result['name']} ¥{result['price']} "
                    f"({result['change_pct']:+.2f}%)"
                )
            return result

        except ImportError:
            logger.error("[EastMoney] efinance 未安装")
            return None
        except Exception as e:
            logger.error(f"[EastMoney] {code} 实时行情失败: {e}")
            return None

    # ------------------------------------------------------------------------
    # 批量实时行情
    # ------------------------------------------------------------------------

    def batch_get_realtime(self, codes: List[str]) -> List[Optional[dict]]:
        """
        批量获取实时行情

        Args:
            codes: 股票代码列表

        Returns:
            List[dict], 与输入顺序对应
        """
        results = []
        for code in codes:
            results.append(self.get_realtime(code))
        return results

    # ------------------------------------------------------------------------
    # 股票名称
    # ------------------------------------------------------------------------

    def get_name(self, code: str) -> str:
        """获取股票名称（优先缓存）"""
        if code in self.name_cache:
            return self.name_cache[code]
        rt = self.get_realtime(code)
        return rt.get("name", code) if rt else code

    # ------------------------------------------------------------------------
    # 大盘指数
    # ------------------------------------------------------------------------

    def get_index_realtime(self, index_code: str = "000001") -> Optional[dict]:
        """
        获取大盘指数实时行情

        Args:
            index_code: 指数代码（支持多种格式）
                000001 / sh000001   → 上证指数
                399001 / sz399001   → 深证成指
                399006 / sz399006   → 创业板指
                000688 / sh000688   → 科创50

        Returns:
            dict: {code, name, price, prev_close, open, high, low,
                   volume, change_pct, turnover_rate, pe, source}
        """
        # 标准化：提取数字部分
        clean = index_code
        for p in ("sh", "sz", "SH", "SZ"):
            if clean.startswith(p):
                clean = clean[2:]
                break

        # 查 secid
        secid = INDEX_SECID_MAP.get(clean) or INDEX_SECID_MAP.get(index_code)
        if not secid:
            logger.warning(f"[EastMoney] 未知指数代码: {index_code}")
            return None

        try:
            params = {
                "secid": secid,
                "fields": EM_INDEX_FIELDS,
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            }
            resp = requests.get(
                EM_INDEX_URL, params=params,
                headers=EM_INDEX_HEADERS, timeout=10
            )
            resp.raise_for_status()
            json_data = resp.json()
            data = json_data.get("data", {})

            if not data:
                logger.warning(f"[EastMoney] 指数 {index_code} 无数据: {json_data}")
                return None

            result = _parse_em_index(clean, data)
            logger.debug(
                f"[EastMoney] 指数 {result['name']} ¥{result['price']} "
                f"({result['change_pct']:+.2f}%)"
            )
            return result

        except Exception as e:
            logger.error(f"[EastMoney] 指数 {index_code} 失败: {e}")
            return None
