# -*- coding: utf-8 -*-
"""
LLM 大模型分析器
支持国内主流大模型：DeepSeek / 智谱 / 豆包 / 通义 / MiniMax / OpenAI兼容
AI增强报告模式：交易后异步调用，不影响交易执行
"""

import logging
from typing import Optional, Dict, Any
from config import get_config

logger = logging.getLogger(__name__)

# 大模型 Provider 映射
PROVIDER_CONFIG = {
    "deepseek": {
        "name": "DeepSeek",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "supports_vision": False,
    },
    "zhipu": {
        "name": "智谱AI",
        "default_model": "glm-4-flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "supports_vision": False,
    },
    "doubao": {
        "name": "豆包",
        "default_model": "doubao-pro",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "DOUBAO_API_KEY",
        "supports_vision": False,
    },
    "qwen": {
        "name": "通义千问",
        "default_model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "supports_vision": False,
    },
    "minimax": {
        "name": "MiniMax",
        "default_model": "MiniMax-Text-01",
        "base_url": "https://api.minimax.chat/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "supports_vision": False,
    },
    "openai": {
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "supports_vision": True,
    },
}


class LLMAnalyzer:
    """
    大模型分析器

    支持的 Provider:
    - deepseek: DeepSeek (deepseek-chat)
    - zhipu: 智谱AI (glm-4-flash)
    - doubao: 豆包 (doubao-pro)
    - qwen: 通义千问 (qwen-plus)
    - minimax: MiniMax (MiniMax-Text-01)
    - openai: OpenAI 兼容接口
    """

    def __init__(self, provider: str = "", model: str = "", api_key: str = "",
                 base_url: str = ""):
        """
        初始化分析器

        Args:
            provider: 模型提供商（deepseek/zhipu/doubao/qwen/minimax/openai）
            model: 具体模型名（可省略，使用provider默认）
            api_key: API Key（可省略，从配置读取）
            base_url: API地址（可省略，从配置读取）
        """
        config = get_config()

        self.provider = provider or config.llm_provider
        self.model = model or config.llm_model
        self.api_key = api_key or config.llm_api_key
        self.base_url = base_url or config.llm_base_url
        self.timeout = config.llm_timeout
        self.max_tokens = config.llm_max_tokens
        self.temperature = config.llm_temperature
        self.enabled = config.llm_enabled

        # 如果没有指定base_url，使用provider默认
        if not self.base_url and self.provider in PROVIDER_CONFIG:
            self.base_url = PROVIDER_CONFIG[self.provider]["base_url"]

        self._client = None

    @property
    def is_available(self) -> bool:
        """检查是否可用"""
        return self.enabled and bool(self.api_key) and bool(self.model)

    def _get_client(self):
        """获取 OpenAI 兼容客户端"""
        if not self.is_available:
            return None

        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
            except ImportError:
                logger.warning("openai SDK 未安装，尝试使用 httpx")
                self._client = None
        return self._client

    def analyze_stock(self, signal: dict, market: dict = None) -> Optional[str]:
        """
        对股票信号进行AI分析

        Args:
            signal: 信号字典（包含股票信息、信号数据）
            market: 大盘状态字典（可选）

        Returns:
            AI分析文本，失败返回None
        """
        if not self.is_available:
            logger.debug("LLM未启用或未配置，跳过AI分析")
            return None

        prompt = self._build_prompt(signal, market)
        return self._call_llm(prompt)

    def _build_prompt(self, signal: dict, market: dict = None) -> str:
        """构建分析Prompt"""
        code = signal.get("code", "")
        name = signal.get("name", code)
        price = signal.get("price", 0)
        change_pct = signal.get("change_pct", 0)
        ma5 = signal.get("ma5", 0)
        ma10 = signal.get("ma10", 0)
        ma20 = signal.get("ma20", 0)
        macd_dif = signal.get("macd_dif", 0)
        macd_dea = signal.get("macd_dea", 0)
        rsi_6 = signal.get("rsi_6", 0)
        atr = signal.get("atr", 0)
        atr_stop = signal.get("atr_stop_loss", 0)
        take_profit = signal.get("take_profit_price", 0)
        buy_count = signal.get("buy_count", 0)
        sell_count = signal.get("sell_count", 0)
        buy_signals = signal.get("buy_signals_detail", [])
        sell_signals = signal.get("sell_signals_detail", [])
        decision = signal.get("decision", "WATCH")
        trend = signal.get("trend_status", "未知")

        market_info = ""
        if market:
            m_status = market.get("status", "未知")
            m_change = market.get("change_pct", 0)
            market_info = f"\n大盘状态: {m_status}（涨跌{m_change:+.2f}%）"

        prompt = f"""你是A股技术分析助手，请对以下股票进行简短分析。

**股票**: {name}（{code}）
**当前价**: ¥{price:.2f}（{change_pct:+.2f}%）
**趋势**: {trend}
{market_info}

**技术指标**:
- MA5={ma5:.2f} | MA10={ma10:.2f} | MA20={ma20:.2f}
- MACD: DIF={macd_dif:.4f} DEA={macd_dea:.4f}
- RSI(6): {rsi_6:.1f}
- ATR: {atr:.4f}

**信号系统**:
- 买入信号 {buy_count}/10: {', '.join(buy_signals) if buy_signals else '无'}
- 卖出信号 {sell_count}/6: {', '.join(sell_signals) if sell_signals else '无'}
- 当前决策: {decision}

**风控价位**:
- 止损价: ¥{atr_stop:.2f}
- 止盈价: ¥{take_profit:.2f}

请用3-5句话给出简洁分析，包括：
1. 当前技术形态判断
2. 值得注意的风险点
3. 操作建议（如果有）

保持客观，不构成投资建议。"""

        return prompt

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用大模型"""
        client = self._get_client()
        if not client:
            return None

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个专业、客观的A股技术分析助手。你的分析应该简洁、有依据、不构成投资建议。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            result = response.choices[0].message.content.strip()
            logger.info(f"LLM分析成功: {result[:50]}...")
            return result

        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return None

    def analyze_trade(self, trade: dict) -> Optional[str]:
        """
        对已执行交易进行AI分析（交易后增强报告）

        Args:
            trade: 交易记录字典

        Returns:
            AI分析文本
        """
        if not self.is_available:
            return None

        action = trade.get("action", "")
        name = trade.get("name", "")
        code = trade.get("code", "")
        price = trade.get("price", 0)
        quantity = trade.get("quantity", 0)
        amount = trade.get("amount", 0)
        buy_signals = trade.get("buy_signals", 0)
        sell_signals = trade.get("sell_signals", 0)
        reason = trade.get("reason", "")

        action_desc = {
            "BUY": "买入",
            "SELL": "卖出",
            "STOP_LOSS": "止损",
            "TAKE_PROFIT": "止盈",
        }.get(action, action)

        prompt = f"""一笔{action_desc}已执行，请给出简要分析。

**交易信息**:
- 股票: {name}（{code}）
- 操作: {action_desc}
- 成交价: ¥{price:.2f}
- 数量: {quantity}手（{quantity*100}股）
- 金额: ¥{amount:,.0f}
- 触发信号: 买{buy_signals}/卖{sell_signals}
- 原因: {reason}

请分析：
1. 这笔交易的逻辑是否合理
2. 需要注意的风险点
3. 后市操作建议

保持客观，不构成投资建议。"""

        return self._call_llm(prompt)

    def get_provider_info(self) -> Dict[str, Any]:
        """获取当前Provider信息"""
        info = PROVIDER_CONFIG.get(self.provider, {})
        return {
            "provider": self.provider,
            "name": info.get("name", self.provider),
            "model": self.model,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "is_available": self.is_available,
        }

    @staticmethod
    def list_providers() -> Dict[str, str]:
        """列出所有支持的Provider"""
        return {k: v["name"] for k, v in PROVIDER_CONFIG.items()}

    def analyze_text(self, prompt: str, system_role: str = None) -> Optional[str]:
        """
        通用文本分析（供进化系统AI初审/复审使用）

        Args:
            prompt: 用户输入的提示
            system_role: 系统角色提示（可选）

        Returns:
            AI响应文本
        """
        if not self.is_available:
            logger.debug("LLM未启用，跳过文本分析")
            return None

        client = self._get_client()
        if not client:
            return None

        system_msg = system_role or "你是一个专业、客观的A股量化交易系统AI审核员。简洁专业，不构成投资建议。"

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[LLM] analyze_text失败: {e}")
            return None


# 全局实例
_llm_analyzer: LLMAnalyzer = None


def get_llm_analyzer() -> LLMAnalyzer:
    """获取全局LLM分析器实例"""
    global _llm_analyzer
    if _llm_analyzer is None:
        _llm_analyzer = LLMAnalyzer()
    return _llm_analyzer


def reload_llm():
    """重新初始化LLM（配置更新后调用）"""
    global _llm_analyzer
    _llm_analyzer = LLMAnalyzer()
    return _llm_analyzer
