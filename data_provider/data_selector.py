# -*- coding: utf-8 -*-
"""
数据源自动选择器
负责：健康检查、主备切换、故障转移

支持模式：
- txstock  : 只用腾讯财经
- eastmoney: 只用东方财富
- auto     : 自动切换（默认）

主备逻辑：
- 连续失败 N 次 → 切换到备用
- 备用稳定 N 次 → 可选切回主源
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

logger = logging.getLogger(__name__)


# ============================================================================
# 数据源定义
# ============================================================================

class DataSource(Enum):
    TXSTOCK = "txstock"
    EASTMONEY = "eastmoney"

    def __str__(self):
        return self.value


@dataclass
class DataSourceConfig:
    """数据源配置"""
    provider: str = "auto"          # txstock | eastmoney | auto
    retry_count: int = 3            # 连续失败次数阈值，触发切换
    timeout: int = 5                # 请求超时（秒）
    recovery_pings: int = 3         # 恢复时连续成功次数，触发切回
    auto_recover: bool = True       # 是否自动切回主源


# ============================================================================
# 健康状态追踪
# ============================================================================

@dataclass
class HealthStatus:
    """单个数据源的健康状态"""
    source: DataSource
    failure_count: int = 0        # 连续失败次数
    success_count: int = 0        # 连续成功次数（用于恢复判定）
    is_alive: bool = True         # 当前是否可用
    last_error: str = ""          # 最近一次错误信息
    last_success_time: float = 0  # 最近成功时间戳

    def record_success(self):
        self.failure_count = 0
        self.success_count += 1
        self.is_alive = True
        self.last_success_time = time.time()

    def record_failure(self, error: str = ""):
        self.failure_count += 1
        self.success_count = 0
        self.last_error = error
        if self.failure_count >= 3:  # 连续失败3次判为不可用
            self.is_alive = False


# ============================================================================
# 主选择器
# ============================================================================

class DataSourceSelector:
    """
    数据源自动选择器

    用法（与 TxStock / EastMoney 接口完全一致）：
        from data_provider.data_selector import get_selector
        selector = get_selector()

        # 单只股票实时行情
        rt = selector.get_realtime("000629")

        # 单只股票历史K线
        hist = selector.get_history("000629", days=60)

        # 批量实时
        batch = selector.batch_get_realtime(["000629", "600519"])

        # 大盘指数
        idx = selector.get_index_realtime("sh000001")
    """

    # 用于健康检查的测试代码
    HEALTH_CHECK_CODES = ["000001", "000629"]  # 平安银行、钒钛股份

    def __init__(self, config: DataSourceConfig = None):
        from data_provider.txstock import TxStock
        from data_provider.eastmoney import EastMoney

        self.config = config or DataSourceConfig()
        self._lock = threading.Lock()

        # 初始化两个数据源
        self._tx = TxStock()
        self._em = EastMoney()

        # 健康状态
        self._health = {
            DataSource.TXSTOCK:    HealthStatus(source=DataSource.TXSTOCK),
            DataSource.EASTMONEY:  HealthStatus(source=DataSource.EASTMONEY),
        }

        # 当前活跃源
        self._active: DataSource = DataSource.TXSTOCK
        self._standby: DataSource = DataSource.EASTMONEY

        # 用户显式指定源时的覆盖
        self._forced_source: Optional[DataSource] = None

        # 上一次活跃源切换时间（避免频繁切换）
        self._last_switch_time: float = 0
        self._min_switch_interval: float = 30  # 至少间隔30秒才切换

        # 启动时做一次健康检查
        self._do_health_check()

    # ------------------------------------------------------------------------
    # 公开 API（与 TxStock/EastMoney 接口一致）
    # ------------------------------------------------------------------------

    def get_realtime(self, code: str) -> Optional[dict]:
        """获取实时行情（自动选择可用源）"""
        return self._call("get_realtime", code,
                          fallback_enabled=self.config.provider == "auto")

    def get_history(self, code: str, days: int = 60) -> Optional[List[dict]]:
        """获取历史K线（自动选择可用源）"""
        return self._call("get_history", code, fallback_enabled=self.config.provider == "auto",
                          _days=days)

    def batch_get_realtime(self, codes: List[str]) -> List[Optional[dict]]:
        """批量获取实时行情"""
        return self._call_batch("batch_get_realtime", codes,
                                fallback_enabled=self.config.provider == "auto")

    def get_index_realtime(self, index_code: str = "000001") -> Optional[dict]:
        """获取大盘指数实时行情"""
        # 指数暂时只用东方财富（腾讯的指数接口不如东方财富稳定）
        return self._call_index(index_code)

    def get_name(self, code: str) -> str:
        """获取股票名称（优先从缓存获取）"""
        # 先从当前活跃源拿
        provider = self._get_provider()
        name = provider.get_name(code)
        if name and name != code:
            return name
        # 备用源也试试
        if self.config.provider == "auto":
            backup = self._get_backup_provider()
            name2 = backup.get_name(code)
            return name2 if name2 != code else name
        return name

    # ------------------------------------------------------------------------
    # 强制切换（用于运维）
    # ------------------------------------------------------------------------

    def force_source(self, source: str):
        """
        强制使用指定数据源

        Args:
            source: "txstock" | "eastmoney" | "auto"
        """
        if source == "auto":
            self._forced_source = None
            self._do_health_check()
            logger.warning("[DataSelector] 切换为自动模式")
        elif source == "txstock":
            self._forced_source = DataSource.TXSTOCK
            logger.warning("[DataSelector] 强制使用腾讯财经")
        elif source == "eastmoney":
            self._forced_source = DataSource.EASTMONEY
            logger.warning("[DataSelector] 强制使用东方财富")
        else:
            logger.warning(f"[DataSelector] 未知数据源: {source}，切换为自动模式")
            self._forced_source = None

    def get_status(self) -> dict:
        """获取当前数据源状态（用于监控）"""
        return {
            "active": self._active.value,
            "standby": self._standby.value,
            "forced": self._forced_source.value if self._forced_source else "auto",
            "txstock_alive": self._health[DataSource.TXSTOCK].is_alive,
            "eastmoney_alive": self._health[DataSource.EASTMONEY].is_alive,
            "txstock_failures": self._health[DataSource.TXSTOCK].failure_count,
            "em_failures": self._health[DataSource.EASTMONEY].failure_count,
        }

    # ------------------------------------------------------------------------
    # 内部调度逻辑
    # ------------------------------------------------------------------------

    def _get_provider(self) -> object:
        """获取当前活跃的数据源 Provider"""
        source = self._forced_source or self._active
        return self._tx if source == DataSource.TXSTOCK else self._em

    def _get_backup_provider(self) -> object:
        """获取备用数据源 Provider"""
        source = (DataSource.EASTMONEY if self._active == DataSource.TXSTOCK
                  else DataSource.TXSTOCK)
        return self._tx if source == DataSource.TXSTOCK else self._em

    def _should_switch(self) -> bool:
        """判断是否应该切换到备用源"""
        if self.config.provider != "auto":
            return False
        if self._forced_source is not None:
            return False
        active_health = self._health[self._active]
        if active_health.failure_count >= self.config.retry_count:
            # 检查备用源是否活着
            standby_health = self._health[self._standby]
            if standby_health.is_alive:
                return True
        return False

    def _switch_if_needed(self):
        """必要时切换主备源"""
        if not self._should_switch():
            return

        # 防止频繁切换
        now = time.time()
        if now - self._last_switch_time < self._min_switch_interval:
            logger.debug("切换过于频繁，跳过")
            return

        old = self._active
        self._active, self._standby = self._standby, self._active
        self._last_switch_time = now

        logger.warning(
            f"[DataSelector] 数据源切换: {old.value} → {self._active.value} "
            f"({self._health[old].last_error})"
        )

    def _record_result(self, source: DataSource, success: bool, error: str = ""):
        """记录单次调用结果"""
        health = self._health[source]
        if success:
            health.record_success()
        else:
            health.record_failure(error)
            # 触发切换检查
            self._switch_if_needed()

    # ------------------------------------------------------------------------
    # 调用封装（带自动重试和故障转移）
    # ------------------------------------------------------------------------

    def _call(self, method: str, code: str, fallback_enabled: bool = True,
              _days: int = None, **kwargs):
        """
        调用数据源方法，失败时自动切换并重试

        Args:
            method: 方法名（get_realtime / get_history）
            code: 股票代码
            fallback_enabled: 是否启用备用源 fallback
        """
        # 处理 days 参数（get_history 特有）
        if _days is not None:
            kwargs["days"] = _days

        # 确定调用顺序
        if self._forced_source:
            order = [self._forced_source]
        elif self.config.provider == "auto":
            order = [self._active, self._standby] if fallback_enabled else [self._active]
        else:
            order = [DataSource(self.config.provider)]

        last_error = ""
        for src in order:
            provider = self._tx if src == DataSource.TXSTOCK else self._em
            try:
                result = getattr(provider, method)(code, **kwargs)
                self._record_result(src, success=True)
                return result
            except Exception as e:
                last_error = str(e)
                self._record_result(src, success=False, error=last_error)
                logger.debug(f"[DataSelector] {src.value}.{method}({code}) 失败: {e}")

        # 全部失败
        logger.error(f"[DataSelector] {method}({code}) 全部数据源失败: {last_error}")
        return None

    def _call_batch(self, method: str, codes: List[str], fallback_enabled: bool = True):
        """批量调用的封装"""
        if self._forced_source:
            order = [self._forced_source]
        elif self.config.provider == "auto":
            order = [self._active, self._standby] if fallback_enabled else [self._active]
        else:
            order = [DataSource(self.config.provider)]

        last_error = ""
        for src in order:
            provider = self._tx if src == DataSource.TXSTOCK else self._em
            try:
                result = getattr(provider, method)(codes)
                self._record_result(src, success=True)
                return result
            except Exception as e:
                last_error = str(e)
                self._record_result(src, success=False, error=last_error)

        logger.error(f"[DataSelector] {method} 全部失败: {last_error}")
        return [None] * len(codes)

    def _call_index(self, index_code: str):
        """获取指数行情（主要用东方财富）"""
        # 指数行情优先用东方财富（已经验证过稳定）
        try:
            result = self._em.get_index_realtime(index_code)
            self._record_result(DataSource.EASTMONEY, success=True)
            return result
        except Exception as e:
            self._record_result(DataSource.EASTMONEY, success=False, error=str(e))
            # fallback 到腾讯
            try:
                result = self._tx.get_index_realtime(index_code)
                self._record_result(DataSource.TXSTOCK, success=True)
                return result
            except Exception as e2:
                self._record_result(DataSource.TXSTOCK, success=False, error=str(e2))
                return None

    # ------------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------------

    def _do_health_check(self):
        """执行一次健康检查，初始化时调用"""
        logger.info("[DataSelector] 执行健康检查...")

        test_code = self.HEALTH_CHECK_CODES[0]  # 用 000001 平安银行测试

        for src, health in self._health.items():
            provider = self._tx if src == DataSource.TXSTOCK else self._em
            try:
                result = provider.get_realtime(test_code)
                if result and result.get("price", 0) > 0:
                    health.record_success()
                    logger.info(f"[DataSelector] {src.value}: ✅ 正常 (¥{result['price']})")
                else:
                    health.record_failure("健康检查返回空数据")
                    logger.warning(f"[DataSelector] {src.value}: ⚠️ 返回空数据")
            except Exception as e:
                health.record_failure(str(e))
                logger.warning(f"[DataSelector] {src.value}: ❌ 失败 ({e})")

        # 初始化主备关系：活的源优先
        tx_alive = self._health[DataSource.TXSTOCK].is_alive
        em_alive = self._health[DataSource.EASTMONEY].is_alive

        if tx_alive and not em_alive:
            self._active, self._standby = DataSource.TXSTOCK, DataSource.EASTMONEY
        elif em_alive and not tx_alive:
            self._active, self._standby = DataSource.EASTMONEY, DataSource.TXSTOCK
        elif tx_alive and em_alive:
            # 两者都活，腾讯优先
            self._active, self._standby = DataSource.TXSTOCK, DataSource.EASTMONEY
        else:
            # 都不活，用腾讯（至少有东西可以试）
            self._active, self._standby = DataSource.TXSTOCK, DataSource.EASTMONEY
            logger.error("[DataSelector] ⚠️ 两个数据源均不可用！")

        logger.info(f"[DataSelector] 主源: {self._active.value}, 备源: {self._standby.value}")


# ============================================================================
# 全局单例
# ============================================================================

_selector_instance: DataSourceSelector = None
_selector_lock = threading.Lock()


def get_selector(config: DataSourceConfig = None) -> DataSourceSelector:
    """
    获取数据源选择器全局单例

    Args:
        config: 数据源配置（仅首次生效）
    """
    global _selector_instance
    with _selector_lock:
        if _selector_instance is None:
            _selector_instance = DataSourceSelector(config)
        return _selector_instance


def reload_selector(config: DataSourceConfig = None):
    """重新创建选择器（配置变更时调用）"""
    global _selector_instance
    with _selector_lock:
        _selector_instance = DataSourceSelector(config)
    return _selector_instance
