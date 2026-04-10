# -*- coding: utf-8 -*-
"""
astock-signal 配置管理
所有配置从 .env 文件加载
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def setup_env():
    """初始化环境变量"""
    env_file = os.getenv("ENV_FILE", str(Path(__file__).parent / ".env"))
    load_dotenv(env_file, override=False)


@dataclass
class Config:
    """全局配置"""

    # ===== 股票池设置 =====
    watchlist_path: str = "data/watchlist.json"
    positions_path: str = "data/positions.json"

    # ===== 交易账户设置 =====
    total_capital: float = 100000.0   # 总本金
    max_positions: int = 3             # 最大持仓数
    single_trade_limit: float = 20000.0  # 单笔交易限额

    # ===== 交易规则（震荡市默认）=====
    buy_signal_threshold: int = 5      # 买入信号阈值（震荡市）
    sell_signal_threshold: int = 3     # 卖出信号阈值（震荡市）
    stop_loss_pct: float = 10.0        # 止损线（%）（震荡市）
    take_profit_pct: float = 15.0       # 止盈线（%）（震荡市）
    atr_stop_multiplier: float = 2.0    # ATR止损倍数（震荡市）

    # 开仓时间窗口
    open_window_start: str = "09:30"   # 开仓窗口开始（全天可交易）
    open_window_end: str = "15:00"      # 开仓窗口结束

    # ===== 弱强市策略参数（v5.0新架构）=====
    # 弱市反弹策略
    weak_rebound_rsi_max: float = 40.0      # RSI(6)偏低阈值（放宽到40，对应约25%的交易日）
    weak_rebound_price_near_low: float = 1.05  # 价格接近布林下轨/前低倍数
    weak_rebound_volume_ratio_max: float = 0.6   # 缩量上限（<均量×0.6）
    weak_rebound_min_signals: int = 2          # 弱市反弹：3指标中至少满足2个
    weak_stop_loss_pct: float = 2.0            # 弱市亏损止损线（%）：硬止损-2%，不抗单
    weak_take_profit_pct: float = 8.0          # 弱市止盈线（%）：RSI>50即走
    weak_atr_multiplier: float = 3.0           # 弱市ATR止损倍数（v4.2: 2.0→3.0，减少被扫）

    # ===== v4.2 新增参数 =====
    # 强势RSI退出（持有期间RSI>80才主动止盈）
    strong_rsi_exit_threshold: float = 80.0       # 强势持仓RSI>80止盈

    # 连续止损锁仓机制
    consecutive_stop_loss_lock: int = 2           # 连续亏损N笔后锁仓
    consecutive_stop_loss_lock_days: int = 5       # 锁仓天数

    # 弱势连续亏损后提高买点阈值
    weak_consecutive_loss_extra_signals: int = 1   # 弱市连续亏损后额外需要的信号数
    weak_consecutive_loss_count: int = 2           # 连续亏损达到N笔时触发
    weak_max_hold_days: int = 3                # 弱市最大持仓天数
    weak_rsi_sell_threshold: float = 65.0       # 弱市RSI反弹到位阈值
    weak_single_position_pct: float = 10.0      # 弱市单只仓位上限（%）
    weak_total_position_pct: float = 30.0      # 弱市总仓位上限（%）
    weak_open_window_start: str = "09:30"      # 弱市开仓时间窗口开始（须等走势确认）

    # 强市趋势策略
    strong_trend_min_signals: int = 2          # 强市趋势：3指标中至少满足2个
    strong_stop_loss_pct: float = 8.0          # 强市亏损止损线（%）：收紧到8%，配合MA20跟踪止损
    strong_take_profit_pct: float = 25.0         # 强市止盈线（%）：让利润奔跑
    strong_atr_multiplier: float = 2.0          # 强市ATR止损倍数（给波动空间，MA20为主要防线）
    strong_max_hold_days: int = 30              # 强市最大持仓天数
    strong_single_position_pct: float = 30.0    # 强市单只仓位上限（%）
    strong_total_position_pct: float = 80.0     # 强市总仓位上限（%）

    # 震荡市策略
    consolidate_min_signals: int = 2             # 震荡市买入信号阈值：3指标满足2个
    consolidate_sell_signals: int = 1            # 震荡市卖出信号阈值：1个即考虑卖
    consolidate_stop_loss_pct: float = 6.0     # 震荡市止损线（%）：收紧到6%
    consolidate_take_profit_pct: float = 15.0   # 震荡市止盈线（%）：布林上轨或RSI>65
    consolidate_atr_multiplier: float = 1.5     # 震荡市ATR止损倍数：收紧
    consolidate_max_hold_days: int = 10         # 震荡市最大持仓天数
    consolidate_single_position_pct: float = 20.0  # 震荡市单只仓位上限（%）
    consolidate_total_position_pct: float = 60.0  # 震荡市总仓位上限（%）

    # ===== 风控规则 =====
    market_crash_threshold: float = -2.0  # 大盘暴跌强平阈值（%）
    max_single_position_pct: float = 30.0  # 单只仓位上限（%）
    max_total_position_pct: float = 80.0   # 总仓位上限（%）
    max_trades_per_day: int = 1             # 每日最大交易次数

    # ===== 监控设置 =====
    watch_interval: int = 300            # watch扫描间隔（秒）
    watch_enabled: bool = True           # 是否启用监控

    # ===== 数据源设置（v3.0）=====
    data_provider: str = "auto"          # txstock | eastmoney | auto
    data_retry_count: int = 3           # 连续失败次数，触发切换
    data_timeout: int = 5               # 请求超时（秒）

    # ===== 通知设置 =====
    feishu_webhook_url: str = ""
    wechat_webhook_url: str = ""
    notify_enabled: bool = True
    notify_only: bool = True             # true=只推送不交易
    auto_trade: bool = False             # 自动交易开关（默认关闭！）

    # ===== 交易时间段 =====
    trading_morning_start: str = "09:30"
    trading_morning_end: str = "11:30"
    trading_afternoon_start: str = "13:00"
    trading_afternoon_end: str = "15:00"
    trading_open_auction_start: str = "09:15"
    trading_open_auction_end: str = "09:25"
    trading_close_auction_start: str = "14:57"
    trading_close_auction_end: str = "15:00"

    # ===== 系统设置 =====
    log_level: str = "INFO"
    log_dir: str = "logs"
    database_path: str = "data/trades.db"

    # ===== AI预警配置 =====
    ai_loss_threshold: float = 8.0      # 亏损超此值触发预警（%）
    ai_alert_enabled: bool = True       # AI预警总开关

    # ===== 大模型配置（AI增强报告）=====
    llm_provider: str = "deepseek"      # deepseek / zhipu / doubao / qwen / minimax / openai
    llm_model: str = "deepseek-chat"    # 具体模型名
    llm_api_key: str = ""              # API Key
    llm_base_url: str = ""             # API地址
    llm_api_version: str = ""          # API版本（仅部分平台需要）
    llm_timeout: int = 30              # 超时秒数
    llm_enabled: bool = False           # 是否启用AI增强（默认关闭）
    llm_max_tokens: int = 1000         # 最大输出token
    llm_temperature: float = 0.3        # 温度参数

    @classmethod
    def load(cls) -> "Config":
        """从环境变量加载配置"""
        setup_env()
        return cls(
            # 股票池
            watchlist_path=os.getenv("WATCHLIST_PATH", "data/watchlist.json"),
            positions_path=os.getenv("POSITIONS_PATH", "data/positions.json"),
            # 账户
            total_capital=float(os.getenv("TOTAL_CAPITAL", "100000")),
            max_positions=int(os.getenv("MAX_POSITIONS", "3")),
            single_trade_limit=float(os.getenv("SINGLE_TRADE_LIMIT", "20000")),
            # 交易规则（震荡市默认）
            buy_signal_threshold=int(os.getenv("BUY_SIGNAL_THRESHOLD", "5")),
            sell_signal_threshold=int(os.getenv("SELL_SIGNAL_THRESHOLD", "3")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "10.0")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "15.0")),
            atr_stop_multiplier=float(os.getenv("ATR_STOP_MULTIPLIER", "2.0")),
            open_window_start=os.getenv("OPEN_WINDOW_START", "09:30"),
            open_window_end=os.getenv("OPEN_WINDOW_END", "15:00"),

            # ===== 弱强市策略参数（v4.0）=====
            # 弱市反弹
            weak_rebound_rsi_max=float(os.getenv("WEAK_REBOUND_RSI_MAX", "40.0")),
            weak_rebound_price_near_low=float(os.getenv("WEAK_REBOUND_PRICE_NEAR_LOW", "1.05")),
            weak_rebound_volume_ratio_max=float(os.getenv("WEAK_REBOUND_VOLUME_RATIO_MAX", "0.6")),
            weak_rebound_min_signals=int(os.getenv("WEAK_REBOUND_MIN_SIGNALS", "2")),
            weak_stop_loss_pct=float(os.getenv("WEAK_STOP_LOSS_PCT", "2.0")),
            weak_take_profit_pct=float(os.getenv("WEAK_TAKE_PROFIT_PCT", "8.0")),
            weak_atr_multiplier=float(os.getenv("WEAK_ATR_MULTIPLIER", "2.0")),
            strong_rsi_exit_threshold=float(os.getenv("STRONG_RSI_EXIT_THRESHOLD", "80.0")),
            consecutive_stop_loss_lock=int(os.getenv("CONSECUTIVE_STOP_LOSS_LOCK", "2")),
            consecutive_stop_loss_lock_days=int(os.getenv("CONSECUTIVE_STOP_LOSS_LOCK_DAYS", "5")),
            weak_consecutive_loss_extra_signals=int(os.getenv("WEAK_CONSECUTIVE_LOSS_EXTRA_SIGNALS", "1")),
            weak_consecutive_loss_count=int(os.getenv("WEAK_CONSECUTIVE_LOSS_COUNT", "2")),
            weak_max_hold_days=int(os.getenv("WEAK_MAX_HOLD_DAYS", "3")),
            weak_rsi_sell_threshold=float(os.getenv("WEAK_RSI_SELL_THRESHOLD", "65.0")),
            weak_single_position_pct=float(os.getenv("WEAK_SINGLE_POSITION_PCT", "10.0")),
            weak_total_position_pct=float(os.getenv("WEAK_TOTAL_POSITION_PCT", "30.0")),
            weak_open_window_start=os.getenv("WEAK_OPEN_WINDOW_START", "09:30"),
            # 强市趋势
            strong_trend_min_signals=int(os.getenv("STRONG_TREND_MIN_SIGNALS", "2")),
            strong_stop_loss_pct=float(os.getenv("STRONG_STOP_LOSS_PCT", "8.0")),
            strong_take_profit_pct=float(os.getenv("STRONG_TAKE_PROFIT_PCT", "25.0")),
            strong_atr_multiplier=float(os.getenv("STRONG_ATR_MULTIPLIER", "2.0")),
            strong_max_hold_days=int(os.getenv("STRONG_MAX_HOLD_DAYS", "30")),
            strong_single_position_pct=float(os.getenv("STRONG_SINGLE_POSITION_PCT", "30.0")),
            strong_total_position_pct=float(os.getenv("STRONG_TOTAL_POSITION_PCT", "80.0")),
            # 震荡市
            consolidate_min_signals=int(os.getenv("CONSENSUS_MIN_SIGNALS", "2")),
            consolidate_sell_signals=int(os.getenv("CONSENSUS_SELL_SIGNALS", "1")),
            consolidate_stop_loss_pct=float(os.getenv("CONSENSUS_STOP_LOSS_PCT", "6.0")),
            consolidate_take_profit_pct=float(os.getenv("CONSENSUS_TAKE_PROFIT_PCT", "15.0")),
            consolidate_atr_multiplier=float(os.getenv("CONSENSUS_ATR_MULTIPLIER", "1.5")),
            consolidate_max_hold_days=int(os.getenv("CONSENSUS_MAX_HOLD_DAYS", "10")),
            consolidate_single_position_pct=float(os.getenv("CONSENSUS_SINGLE_POSITION_PCT", "20.0")),
            consolidate_total_position_pct=float(os.getenv("CONSENSUS_TOTAL_POSITION_PCT", "60.0")),
            # 风控
            market_crash_threshold=float(os.getenv("MARKET_CRASH_THRESHOLD", "-2.0")),
            max_single_position_pct=float(os.getenv("MAX_SINGLE_POSITION_PCT", "30.0")),
            max_total_position_pct=float(os.getenv("MAX_TOTAL_POSITION_PCT", "80.0")),
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "1")),
            # 监控
            watch_interval=int(os.getenv("WATCH_INTERVAL", "300")),
            watch_enabled=os.getenv("WATCH_ENABLED", "true").lower() == "true",
            # 数据源（v3.0）
            data_provider=os.getenv("DATA_PROVIDER", "auto"),
            data_retry_count=int(os.getenv("DATA_RETRY_COUNT", "3")),
            data_timeout=int(os.getenv("DATA_TIMEOUT", "5")),
            # 通知
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
            wechat_webhook_url=os.getenv("WECHAT_WEBHOOK_URL", ""),
            notify_enabled=os.getenv("NOTIFY_ENABLED", "true").lower() == "true",
            notify_only=os.getenv("NOTIFY_ONLY", "true").lower() == "true",
            auto_trade=os.getenv("AUTO_TRADE", "false").lower() == "true",
            # 交易时间
            trading_morning_start=os.getenv("TRADING_MORNING_START", "09:30"),
            trading_morning_end=os.getenv("TRADING_MORNING_END", "11:30"),
            trading_afternoon_start=os.getenv("TRADING_AFTERNOON_START", "13:00"),
            trading_afternoon_end=os.getenv("TRADING_AFTERNOON_END", "15:00"),
            trading_open_auction_start=os.getenv("TRADING_OPEN_AUCTION_START", "09:15"),
            trading_open_auction_end=os.getenv("TRADING_OPEN_AUCTION_END", "09:25"),
            trading_close_auction_start=os.getenv("TRADING_CLOSE_AUCTION_START", "14:57"),
            trading_close_auction_end=os.getenv("TRADING_CLOSE_AUCTION_END", "15:00"),
            # 系统
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_dir=os.getenv("LOG_DIR", "logs"),
            database_path=os.getenv("DATABASE_PATH", "data/trades.db"),
            # 大模型
            # AI预警
            ai_loss_threshold=float(os.getenv("AI_LOSS_THRESHOLD", "8.0")),
            ai_alert_enabled=os.getenv("AI_ALERT_ENABLED", "true").lower() == "true",
            # 大模型
            llm_provider=os.getenv("LLM_PROVIDER", "deepseek"),
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_version=os.getenv("LLM_API_VERSION", ""),
            llm_timeout=int(os.getenv("LLM_TIMEOUT", "30")),
            llm_enabled=os.getenv("LLM_ENABLED", "false").lower() == "true",
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1000")),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        )


# 全局配置实例
_config: Config = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config():
    """重新加载配置"""
    global _config
    _config = Config.load()
