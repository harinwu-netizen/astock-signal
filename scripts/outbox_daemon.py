#!/usr/bin/env python3
"""
outbox 守护进程 (v6.6 设计)

职责:
  - 持续轮询 outbox/ 目录，发现 status=pending 的文件
  - 调用飞书 IM API 发送
  - 写回 status=delivered + feishu_message_id + delivered_at
  - 写 send_health.json 健康检查
  - 失败时标记 status=failed，写 alert_needed.flag

设计原则:
  - 独立进程运行（不依赖 OpenClaw heartbeat）
  - PIDFILE + 启动时间戳，cron 5 分钟检查存活
  - 单文件原子写：先发飞书，成功后再回写 outbox 文件
  - 失败重试：最多 3 次（指数退避）
  - polling 间隔 3 秒（足够快，远小于信号灯 5 分钟扫描）

用法:
  python3 outbox_daemon.py start    # 后台启动
  python3 outbox_daemon.py stop     # 停止（SIGTERM）
  python3 outbox_daemon.py status   # 状态
  python3 outbox_daemon.py run      # 前台运行（调试用）
  python3 outbox_daemon.py once     # 只处理一轮
"""

import os
import sys
import json
import time
import signal
import logging
import requests
import fcntl
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler

# === 路径配置 ===
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTBOX_DIR = PROJECT_ROOT / "data" / "outbox"
HEALTH_FILE = PROJECT_ROOT / "data" / "send_health.json"
ALERT_FLAG = PROJECT_ROOT / "data" / "alert_needed.flag"
PIDFILE = Path("/tmp/astock-outbox-daemon.pid")
LOG_FILE = PROJECT_ROOT / "logs" / "outbox_daemon.log"

# === 飞书配置 (从 openclaw.json 读) ===
def _load_feishu_config():
    """从 ~/.openclaw/openclaw.json 读飞书凭据"""
    config_paths = [
        Path.home() / ".openclaw" / "openclaw.json",
        Path("/root/.openclaw/openclaw.json"),
    ]
    for p in config_paths:
        if p.exists():
            try:
                with p.open() as f:
                    cfg = json.load(f)
                feishu = cfg.get("channels", {}).get("feishu", {})
                return {
                    "app_id": feishu["appId"],
                    "app_secret": feishu["appSecret"],
                    "target": feishu.get("defaultTo", "ou_ee2947ff311d4978679c2a2d4433f62a"),
                }
            except Exception as e:
                print(f"读 {p} 失败: {e}", file=sys.stderr)
    raise RuntimeError("找不到飞书配置 (openclaw.json)")


FEISHU = _load_feishu_config()
TARGET_USER = FEISHU["target"]
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# === 日志 (v6.6.2 修复重复输出) ===
# 根因: 子进程 fork 后 os.dup2 把 stderr 重定向到日志文件, 与 logging.StreamHandler
# 默认绑 stderr 重复写, 导致每条日志写 2 次。修复: 只保留 RotatingFileHandler。
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=3)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log = logging.getLogger("outbox-daemon")
log.setLevel(logging.INFO)
log.addHandler(_file_handler)
log.propagate = False  # 不再冒泡到 root, 避免被任何 root handler 重复输出

# === 飞书 API ===
_token_cache = {"token": None, "expire_at": 0}


def get_feishu_token():
    """拿 tenant_access_token，自动缓存"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 60:
        return _token_cache["token"]

    r = requests.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU["app_id"], "app_secret": FEISHU["app_secret"]},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"拿 token 失败: {d}")
    _token_cache["token"] = d["tenant_access_token"]
    _token_cache["expire_at"] = now + d.get("expire", 7200)
    return _token_cache["token"]


def send_to_feishu(content: str, max_retries: int = 3) -> dict:
    """调飞书 IM API 发私聊消息，带重试"""
    last_err = None
    for attempt in range(max_retries):
        try:
            token = get_feishu_token()
            url = f"{FEISHU_API_BASE}/im/v1/messages"
            params = {"receive_id_type": "open_id"}
            body = {
                "receive_id": TARGET_USER,
                "msg_type": "text",
                "content": json.dumps({"text": content}, ensure_ascii=False),
            }
            r = requests.post(
                url, params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body, timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") == 0:
                msg_id = d.get("data", {}).get("message_id", "")
                return {"ok": True, "message_id": msg_id}
            last_err = d.get("msg", str(d))
            log.warning(f"飞书 API 拒绝 (尝试 {attempt+1}/{max_retries}): {last_err}")
        except Exception as e:
            last_err = str(e)
            log.warning(f"发送异常 (尝试 {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # 指数退避
    return {"ok": False, "error": last_err}


# === 健康检查 ===
def record_health(status: str, msg_file: str = "", content_len: int = 0, error: str = ""):
    """追加 send_health.json history（保留最近 50 条）"""
    try:
        health = {"history": []}
        if HEALTH_FILE.exists():
            try:
                with HEALTH_FILE.open() as f:
                    health = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # 文件损坏时重新开始
        health.setdefault("history", []).append({
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "msg_file": msg_file,
            "content_len": content_len,
            "error": error,
        })
        health["history"] = health["history"][-50:]
        health["total_outbox_delivered"] = sum(1 for h in health["history"] if h["status"] == "outbox_delivered")
        health["total_outbox_failed"] = sum(1 for h in health["history"] if h["status"] == "outbox_failed")
        health["last_status"] = status
        health["last_update"] = datetime.now().isoformat()
        # 原子写
        tmp = HEALTH_FILE.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
        tmp.replace(HEALTH_FILE)
    except Exception as e:
        log.error(f"写健康文件失败: {e}")


def write_alert_flag(error_msg: str):
    """写告警标志文件，小海检测后会推送告警"""
    try:
        with ALERT_FLAG.open("w") as f:
            json.dump({
                "type": "feishu_send_failed",
                "error": error_msg,
                "created_at": datetime.now().isoformat(),
            }, f, ensure_ascii=False)
    except Exception as e:
        log.error(f"写告警标志失败: {e}")


# === 核心逻辑：处理一个 outbox 文件 ===
def process_one_file(filepath: Path) -> bool:
    """处理一个 outbox 文件，成功返回 True"""
    try:
        with filepath.open() as f:
            msg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"读 {filepath.name} 失败: {e}")
        return False

    # 已处理过则跳过
    if msg.get("status") == "delivered":
        return True

    content = msg.get("content", "")
    target = msg.get("target", TARGET_USER)
    if not content:
        log.warning(f"{filepath.name} 内容为空，跳过")
        return False

    log.info(f"处理 {filepath.name} ({len(content)} chars, target={target[:20]}...)")
    result = send_to_feishu(content)

    if result["ok"]:
        # 成功：原子更新 status
        msg["status"] = "delivered"
        msg["feishu_message_id"] = result["message_id"]
        msg["delivered_at"] = datetime.now().isoformat()
        tmp = filepath.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(msg, f, ensure_ascii=False, indent=2)
        tmp.replace(filepath)
        log.info(f"✅ {filepath.name} delivered mid={result['message_id'][:30]}")
        record_health("outbox_delivered", filepath.name, len(content), "")
        return True
    else:
        # 失败：标记 failed
        msg["status"] = "failed"
        msg["last_error"] = result["error"]
        msg["failed_at"] = datetime.now().isoformat()
        tmp = filepath.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(msg, f, ensure_ascii=False, indent=2)
        tmp.replace(filepath)
        log.error(f"❌ {filepath.name} failed: {result['error']}")
        record_health("outbox_failed", filepath.name, len(content), result["error"])
        write_alert_flag(result["error"])
        return False


def scan_once() -> dict:
    """扫描 outbox 一次，返回统计"""
    stats = {"scanned": 0, "delivered": 0, "failed": 0, "skipped": 0}
    if not OUTBOX_DIR.exists():
        return stats
    files = sorted(OUTBOX_DIR.glob("*.json"))
    for fp in files:
        # 跳过正在写的 .tmp 文件
        if fp.suffix == ".tmp":
            continue
        try:
            with fp.open() as f:
                msg = json.load(f)
            status = msg.get("status", "pending")
            if status == "delivered":
                stats["skipped"] += 1
                continue
            stats["scanned"] += 1
            if process_one_file(fp):
                stats["delivered"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            log.error(f"处理 {fp.name} 异常: {e}")
            stats["failed"] += 1
    return stats


# === 守护进程管理 ===
_already_running = False


def is_running() -> bool:
    """检查是否已有进程在跑"""
    if not PIDFILE.exists():
        return False
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)  # 不真发信号，只检查
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def write_pidfile():
    PIDFILE.write_text(str(os.getpid()))


def remove_pidfile():
    try:
        PIDFILE.unlink()
    except FileNotFoundError:
        pass


def run_foreground():
    """前台运行（调试用）"""
    if is_running():
        existing = PIDFILE.read_text().strip()
        print(f"❌ 已有进程在跑 (PID={existing})")
        sys.exit(1)
    write_pidfile()
    log.info(f"🚀 outbox 守护进程启动 PID={os.getpid()}")
    record_health("daemon_started", "", 0, "")

    stop_flag = {"stop": False}

    def handle_signal(sig, frame):
        log.info(f"收到信号 {sig}，准备退出...")
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        while not stop_flag["stop"]:
            stats = scan_once()
            if stats["scanned"] > 0:
                log.info(f"本轮: {stats}")
            time.sleep(3)
    finally:
        remove_pidfile()
        log.info("👋 守护进程退出")
        record_health("daemon_stopped", "", 0, "")


def start():
    """后台启动"""
    if is_running():
        existing = PIDFILE.read_text().strip()
        print(f"❌ 已在运行 (PID={existing})")
        sys.exit(1)

    # fork
    pid = os.fork()
    if pid > 0:
        # 父进程
        time.sleep(1)
        if is_running():
            print(f"✅ 已启动 PID={pid}")
        else:
            print(f"❌ 启动失败，请查日志 {LOG_FILE}")
            sys.exit(1)
        return

    # 子进程
    os.setsid()
    # 重定向 stdio
    with open("/dev/null", "rb") as f:
        os.dup2(f.fileno(), 0)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "ab") as f:
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
    run_foreground()


def stop():
    """停止"""
    if not is_running():
        print("未在运行")
        return
    pid = int(PIDFILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        log.info(f"已发送 SIGTERM 给 PID={pid}")
        for _ in range(10):
            time.sleep(0.5)
            if not is_running():
                print(f"✅ 已停止 PID={pid}")
                return
        print(f"⚠️ PID={pid} 还在跑，强杀中...")
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"PID={pid} 不存在")
        remove_pidfile()


def status():
    """状态"""
    if not is_running():
        print("🔴 未运行")
        return
    pid = int(PIDFILE.read_text().strip())
    # 进程运行时间
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read().split()
        # /proc/[pid]/stat 格式: ... start_time(字段22) 单位是 jiffies
        start_time_jiffies = int(stat[21])
        clock_tick = os.sysconf("SC_CLK_TCK")  # 通常 100
        # btime 是系统启动时间
        btime = 0
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime"):
                    btime = int(line.split()[1])
                    break
        # 进程启动时间 (秒)
        start_time = btime + start_time_jiffies / clock_tick
        age = time.time() - start_time
        age_str = f"{int(age//3600)}h{int((age%3600)//60)}m{int(age%60)}s"
    except Exception as e:
        age_str = f"未知 ({e})"
    print(f"🟢 运行中 PID={pid}, 启动时长 {age_str}")

    # 统计 outbox
    if OUTBOX_DIR.exists():
        files = list(OUTBOX_DIR.glob("*.json"))
        pending = sum(1 for fp in files if json.load(fp.open()).get("status") != "delivered")
        print(f"   outbox 总文件: {len(files)}, pending: {pending}")

    # 健康状态
    if HEALTH_FILE.exists():
        try:
            h = json.load(HEALTH_FILE.open())
            print(f"   send_health: last_status={h.get('last_status')}, delivered={h.get('total_outbox_delivered')}, failed={h.get('total_outbox_failed')}")
        except: pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "run":
        run_foreground()
    elif cmd == "once":
        stats = scan_once()
        print(f"扫描结果: {stats}")
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
