# -*- coding: utf-8 -*-
"""
企业微信通知模块
"""

import logging
import requests
from config import get_config

logger = logging.getLogger(__name__)


class WechatNotifier:
    """企业微信 Webhook 推送"""

    def __init__(self, webhook_url: str = ""):
        config = get_config()
        self.webhook_url = webhook_url or config.wechat_webhook_url
        self.enabled = bool(self.webhook_url)

    def send(self, content: str, msg_type: str = "text") -> bool:
        """
        发送企业微信消息

        Args:
            content: 消息内容
            msg_type: text 或 markdown
        """
        if not self.enabled:
            logger.debug("企业微信通知未配置，跳过")
            return False

        try:
            headers = {"Content-Type": "application/json"}

            if msg_type == "markdown":
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": content}
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {"content": content}
                }

            resp = requests.post(self.webhook_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()

            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("企业微信消息发送成功")
                return True
            else:
                logger.error(f"企业微信消息发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"企业微信通知发送异常: {e}")
            return False


# 全局通知器
_wechat_notifier: WechatNotifier = None


def get_wechat_notifier() -> WechatNotifier:
    global _wechat_notifier
    if _wechat_notifier is None:
        _wechat_notifier = WechatNotifier()
    return _wechat_notifier
