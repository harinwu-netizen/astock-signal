#!/usr/bin/env python3
"""
飞书消息发送器 - 后台服务
每5秒检查pending_messages.json，有新消息则通过OpenClaw发送
"""
import sys
import os
import json
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

PENDING_FILE = "data/pending_messages.json"
STATE_FILE = "data/pending_state.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_sent": "", "count": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_via_openclaw(message: str, target: str, msg_type: str = "text") -> bool:
    """通过OpenClaw CLI发送消息"""
    try:
        import subprocess
        result = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "feishu",
             "--target", target,
             "--message", message],
            capture_output=True,
            text=True,
            timeout=120,  # 飞书长消息需要更长时间
        )
        if result.returncode == 0:
            logger.info(f"✅ 消息已发送: {message[:50]}...")
            return True
        else:
            logger.error(f"❌ 发送失败: {result.stderr}")
            return False
    except FileNotFoundError:
        logger.error("openclaw命令不存在，尝试其他方式")
        return False
    except Exception as e:
        logger.error(f"发送异常: {e}")
        return False


def send_via_http(message: str, target: str) -> bool:
    """通过OpenClaw HTTP API发送"""
    try:
        import requests
        resp = requests.post(
            "http://localhost:18789/api/message/send",
            json={"channel": "feishu", "target": target, "message": message},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    state = load_state()
    logger.info("🚀 飞书消息发送器启动")

    while True:
        try:
            if not os.path.exists(PENDING_FILE):
                time.sleep(5)
                continue

            with open(PENDING_FILE) as f:
                pending = json.load(f)

            if not pending:
                time.sleep(5)
                continue

            # 发送第一条（最多重试3次，3次失败则丢弃）
            msg = pending[0]
            retry_count = msg.get("__retry", 0)
            sent = False

            # 方式1: openclaw CLI
            if not sent:
                sent = send_via_openclaw(msg["content"], msg.get("target", "ou_ee2947ff311d4978679c2a2d4433f62a"))

            # 方式2: HTTP API
            if not sent:
                sent = send_via_http(msg["content"], msg.get("target", "ou_ee2947ff311d4978679c2a2d4433f62a"))

            if sent:
                pending.pop(0)
                with open(PENDING_FILE, "w") as f:
                    json.dump(pending, f, ensure_ascii=False, indent=2)
                state["count"] += 1
                save_state(state)
                logger.info(f"📤 已发送 {state['count']} 条消息")
            else:
                # 重试超过3次则丢弃，避免无限重试
                if retry_count >= 3:
                    pending.pop(0)
                    with open(PENDING_FILE, "w") as f:
                        json.dump(pending, f, ensure_ascii=False, indent=2)
                    logger.warning(f"⚠️ 消息发送失败已丢弃（重试{retry_count}次）: {msg.get('content','')[:50]}...")
                else:
                    # 标记重试次数并写回
                    msg["__retry"] = retry_count + 1
                    pending[0] = msg
                    with open(PENDING_FILE, "w") as f:
                        json.dump(pending, f, ensure_ascii=False, indent=2)
                    logger.info(f"⏳ 发送失败，{5*(retry_count+1)}秒后重试（第{retry_count+1}次）")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"处理异常: {e}")

        time.sleep(5)


if __name__ == "__main__":
    main()
