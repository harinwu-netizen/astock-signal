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

    # ===== 交易规则 =====
    buy_signal_threshold: int = 5      # 买入信号阈值
    sell_signal_threshold: int = 3     # 卖出信号阈值
    stop_loss_pct: float = 10.0        # 止损线（%）
    take_profit_pct: float = 15.0       # 止盈线（%）
    atr_stop_multiplier: float = 2.0    # ATR止损倍数

    # 开仓时间窗口
    open_window_start: str = "14:30"   # 开仓窗口开始
    open_window_end: str = "15:00"      # 开仓窗口结束

    # ===== 风控规则 =====
    market_crash_threshold: float = -2.0  # 大盘暴跌强平阈值（%）
    max_single_position_pct: float = 30.0  # 单只仓位上限（%）
    max_total_position_pct: float = 80.0   # 总仓位上限（%）
    max_trades_per_day: int = 1             # 每日最大交易次数

    # ===== 监控设置 =====
    watch_interval: int = 300            # watch扫描间隔（秒）
    watch_enabled: bool = True           # 是否启用监控

    # ===== 数据源设置 =====
    data_source_priority: str = "txstock,eastmoney"  # 数据源优先级

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
            # 交易规则
            buy_signal_threshold=int(os.getenv("BUY_SIGNAL_THRESHOLD", "5")),
            sell_signal_threshold=int(os.getenv("SELL_SIGNAL_THRESHOLD", "3")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "10.0")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "15.0")),
            atr_stop_multiplier=float(os.getenv("ATR_STOP_MULTIPLIER", "2.0")),
            open_window_start=os.getenv("OPEN_WINDOW_START", "14:30"),
            open_window_end=os.getenv("OPEN_WINDOW_END", "15:00"),
            # 风控
            market_crash_threshold=float(os.getenv("MARKET_CRASH_THRESHOLD", "-2.0")),
            max_single_position_pct=float(os.getenv("MAX_SINGLE_POSITION_PCT", "30.0")),
            max_total_position_pct=float(os.getenv("MAX_TOTAL_POSITION_PCT", "80.0")),
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "1")),
            # 监控
            watch_interval=int(os.getenv("WATCH_INTERVAL", "300")),
            watch_enabled=os.getenv("WATCH_ENABLED", "true").lower() == "true",
            # 数据源
            data_source_priority=os.getenv("DATA_SOURCE_PRIORITY", "txstock,eastmoney"),
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
