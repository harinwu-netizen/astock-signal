# -*- coding: utf-8 -*-
"""
信号计数器 v5.0

本模块已重构为三层决策路由架构：
  - signal_weak.py     弱市反弹系统（RSI+布林+量能）
  - signal_strong.py   强市趋势系统（MA+MACD+量价）
  - signal_consolidate.py  震荡波段系统（布林+RSI+量比）
  - signal_unified.py  统一路由层

旧接口保持兼容，内部代理到 signal_unified.SignalCounter
"""

# 代理到新版统一分析器（保持向后兼容）
from indicators.signal_unified import SignalCounter, analyze_unified, UnifiedSignal

# 保留旧版关键导出（兼容直接引用旧模块的地方）
from indicators.signal_unified import (
    SignalCounter as SignalCounter,
    analyze_unified as count_signals,
    UnifiedSignal as RealTimeSignal,
)

# 旧模型的 Decision 枚举（路由层直接用字符串，这里保留兼容）
from models.signal import Decision, MarketStatus, TrendStatus, Signal

__all__ = [
    "SignalCounter",
    "analyze_unified",
    "UnifiedSignal",
    "RealTimeSignal",
    "Decision",
    "MarketStatus",
    "TrendStatus",
    "Signal",
]
