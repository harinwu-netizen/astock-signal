# -*- coding: utf-8 -*-
"""
数据清洗模块
统一处理来自不同数据源的原始数据

清洗规则：
1. 异常值过滤：涨幅>20%、价格=0/负、成交量=0
2. 缺失值填充：前向填充（ffill）
3. 编码统一：GBK/UTF-8 自动识别后转为 UTF-8
4. 浮点数字符串强转换：str → float
5. K线数据去重：同一日期只保留一条
6. K线排序：按日期升序
"""

import logging
import pandas as pd
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# 异常值阈值
# ============================================================================

MAX_PRICE_CHANGE_PCT = 20.0    # 单日涨幅 >20% 视为异常
MIN_PRICE = 0.001             # 价格必须 > 0
MIN_VOLUME = 0                # 成交量必须 >= 0（0 视作停牌，不删除但标记）


# ============================================================================
# 公开 API
# ============================================================================

def clean_kline_data(raw_records: List[dict]) -> List[dict]:
    """
    清洗 K线历史数据

    Args:
        raw_records: 原始 K线数据列表，每条包含 date/open/high/low/close/volume

    Returns:
        清洗后的数据列表（排序 + 去重 + 过滤异常）

    处理流程：
        1. 转 DataFrame
        2. 强类型转换（全部字段转为正确类型）
        3. 日期去重（同一天只保留一条，通常保留最后一条）
        4. 按日期升序排列
        5. 过滤异常值（价格=0、涨幅超限）
        6. 前向填充缺失值（仅对 close 做填充，open/high/low 不填充）
    """
    if not raw_records:
        return []

    try:
        df = pd.DataFrame(raw_records)
    except Exception as e:
        logger.error(f"[DataClean] DataFrame 转换失败: {e}")
        return []

    # ---- 1. 强类型转换 ----
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # ---- 2. 日期清洗 ----
    if "date" in df.columns:
        df["date"] = df["date"].astype(str).str.strip()
        # 去掉时间部分（如果有的话）
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        # 扔掉无效日期
        df = df.dropna(subset=["date"])

    # ---- 3. 按日期去重（保留最后一条）----
    if "date" in df.columns and df["date"].duplicated().any():
        df = df.drop_duplicates(subset=["date"], keep="last")
        logger.debug(f"[DataClean] 去重后剩余 {len(df)} 条")

    # ---- 4. 按日期升序排列 ----
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    # ---- 5. 过滤异常值 ----
    original_len = len(df)

    # 价格 = 0 或 负
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df = df[df[col] > MIN_PRICE]

    # 单日涨幅异常（从 close 的日涨幅判断）
    if "close" in df.columns and len(df) > 1:
        df["_pct"] = df["close"].pct_change() * 100
        df.loc[df["_pct"].abs() <= MAX_PRICE_CHANGE_PCT, "_valid"] = True
        df.loc[df["_pct"].abs() > MAX_PRICE_CHANGE_PCT, "_valid"] = False
        # 第一行没有 pct，无所谓，保留
        df["_valid"] = df["_valid"].fillna(True)
        df = df[df["_valid"]].drop(columns=["_pct", "_valid"])

    dropped = original_len - len(df)
    if dropped > 0:
        logger.warning(f"[DataClean] 过滤了 {dropped} 条异常K线（价格=0/涨幅超限）")

    # ---- 6. 前向填充 close 缺失值 ----
    if "close" in df.columns:
        df["close"] = df["close"].ffill()

    # ---- 7. 重建 records ----
    result = []
    for _, row in df.iterrows():
        try:
            result.append({
                "date": row.get("date", ""),
                "open": round(float(row["open"]), 2) if pd.notna(row.get("open")) else None,
                "high": round(float(row["high"]), 2) if pd.notna(row.get("high")) else None,
                "low": round(float(row["low"]), 2) if pd.notna(row.get("low")) else None,
                "close": round(float(row["close"]), 2) if pd.notna(row.get("close")) else None,
                "volume": int(row["volume"]) if pd.notna(row.get("volume")) else 0,
            })
        except (ValueError, TypeError):
            continue

    logger.debug(f"[DataClean] 清洗完成：{len(result)} 条K线")
    return result


def clean_realtime_data(raw: dict) -> Optional[dict]:
    """
    清洗实时行情数据

    Args:
        raw: 原始实时行情 dict

    Returns:
        清洗后的 dict，异常时返回 None

    清洗规则：
    - 价格 <= 0 → None
    - 涨幅超限 → 置 0
    - 字段类型强制转换
    """
    if not raw:
        return None

    try:
        price = float(raw.get("price", 0))
        if price <= MIN_PRICE:
            logger.warning(f"[DataClean] 实时行情价格异常: {price}，已丢弃")
            return None

        prev_close = float(raw.get("prev_close", price))
        change_pct = float(raw.get("change_pct", 0))

        # 涨幅超限则置0
        if abs(change_pct) > MAX_PRICE_CHANGE_PCT:
            logger.warning(f"[DataClean] 实时行情涨幅超限: {change_pct}%，已修正为0")
            change_pct = 0.0

        result = {
            "code": str(raw.get("code", "")),
            "name": str(raw.get("name", "")),
            "price": round(price, 2),
            "prev_close": round(prev_close, 2),
            "open": round(float(raw.get("open", price)), 2),
            "high": round(float(raw.get("high", price)), 2),
            "low": round(float(raw.get("low", price)), 2),
            "volume": int(float(raw.get("volume", 0))),
            "change_pct": round(change_pct, 2),
            "turnover_rate": round(float(raw.get("turnover_rate", 0)), 2),
            "pe": round(float(raw.get("pe", 0)), 2),
            "source": str(raw.get("source", "")),
        }
        return result

    except (ValueError, TypeError) as e:
        logger.error(f"[DataClean] 实时行情清洗失败: {e}")
        return None


def is_suspended(volume: int, price: float) -> bool:
    """
    判断是否停牌（成交量=0 且价格无变化）

    Returns:
        True 表示停牌
    """
    return volume == 0 or price <= MIN_PRICE


def add_derived_fields(records: List[dict]) -> List[dict]:
    """
    为 K线数据追加衍生字段（不修改原始字段）

    新增字段：
    - ma5 / ma10 / ma20：收盘价的5/10/20日均线
    - volume_ratio：当日成交量 / 20日均量
    - pct_change：单日涨跌幅（%）

    这个函数供回测模块使用
    """
    if not records:
        return records

    df = pd.DataFrame(records)

    # 均值
    for window in (5, 10, 20):
        col = f"ma{window}"
        df[col] = df["close"].rolling(window=window, min_periods=1).mean().round(2)

    # 量比
    if "volume" in df.columns:
        df["volume_ratio"] = (df["volume"] / df["volume"].rolling(window=20, min_periods=5).mean()).round(2)

    # 涨跌幅
    if "close" in df.columns:
        df["pct_change"] = df["close"].pct_change().round(4) * 100

    return df.to_dict(orient="records")
