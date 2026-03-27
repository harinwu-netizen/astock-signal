# -*- coding: utf-8 -*-
"""
FastAPI Web服务
提供持仓查看、信号仪表盘、设置管理界面
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# 导入项目模块
from config import get_config, reload_config
from models.watchlist import WatchlistStore
from models.position import PositionStore, Position
from models.signal import MarketStatus
from monitor.scanner import Scanner
from monitor.alerter import Alerter
from strategy.market_filter import get_market_filter

# 创建FastAPI应用
app = FastAPI(
    title="A股信号灯",
    description="A股量化交易信号系统",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 首页 ==========

@app.get("/", response_class=HTMLResponse)
async def index():
    """返回Web仪表盘"""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html><head><title>A股信号灯</title></head>
    <body>
        <h1>🏮 A股信号灯 v1.0</h1>
        <p>Web界面模板未找到，请检查 templates/index.html</p>
        <h2>API端点</h2>
        <ul>
            <li><a href="/api/portfolio">GET /api/portfolio</a> - 账户总览</li>
            <li><a href="/api/positions">GET /api/positions</a> - 持仓列表</li>
            <li><a href="/api/watchlist">GET /api/watchlist</a> - 股票池</li>
            <li><a href="/api/signals">GET /api/signals</a> - 信号扫描</li>
            <li><a href="/api/market">GET /api/market</a> - 大盘状态</li>
        </ul>
    </body></html>
    """


# ========== API: 账户总览 ==========

@app.get("/api/portfolio")
async def get_portfolio():
    """获取账户总览"""
    config = get_config()
    position_store = PositionStore()
    positions = position_store.get_open_positions()

    total_cost = sum(p.cost for p in positions)
    total_value = sum(p.cost + p.unrealized_pnl for p in positions)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "total_capital": config.total_capital,
        "available_cash": config.total_capital - total_cost,
        "total_value": total_value,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "position_count": len(positions),
        "max_positions": config.max_positions,
        "auto_trade": config.auto_trade,
        "notify_only": config.notify_only,
    }


# ========== API: 持仓列表 ==========

@app.get("/api/positions")
async def get_positions():
    """获取持仓列表"""
    position_store = PositionStore()
    positions = position_store.get_open_positions()
    return {
        "positions": [p.to_dict() for p in positions],
        "count": len(positions),
    }


# ========== API: 股票池 ==========

@app.get("/api/watchlist")
async def get_watchlist():
    """获取股票池"""
    store = WatchlistStore()
    watchlist = store.load()
    return {
        "stocks": [s.to_dict() for s in watchlist.stocks],
        "enabled_count": len(watchlist.get_enabled_stocks()),
        "settings": watchlist.settings.to_dict(),
    }


@app.post("/api/watchlist/add")
async def add_stock(code: str, name: Optional[str] = None):
    """添加股票到股票池"""
    from data_provider.txstock import TxStock

    tx = TxStock()
    if not name:
        name = tx.get_name(code)

    store = WatchlistStore()
    watchlist = store.load()

    if watchlist.find_by_code(code):
        raise HTTPException(status_code=400, detail=f"{code} 已在股票池中")

    watchlist.add_stock(code, name or code)
    store.save(watchlist)

    return {"success": True, "message": f"已添加 {name} ({code})"}


@app.post("/api/watchlist/remove")
async def remove_stock(code: str):
    """从股票池删除股票"""
    store = WatchlistStore()
    watchlist = store.load()

    if not watchlist.find_by_code(code):
        raise HTTPException(status_code=404, detail=f"{code} 不在股票池中")

    watchlist.remove_stock(code)
    store.save(watchlist)

    return {"success": True, "message": f"已删除 {code}"}


# ========== API: 信号扫描 ==========

@app.get("/api/signals")
async def get_signals():
    """获取信号扫描结果"""
    scanner = Scanner()
    try:
        signals = scanner.scan_watchlist()
        actions = scanner.get_actionable_signals(signals)

        return {
            "timestamp": datetime.now().isoformat(),
            "signals": [s.to_dict() for s in signals],
            "actions": {
                "buy": [s.code for s in actions["buy"]],
                "sell": [s.code for s in actions["sell"]],
                "hold": [s.code for s in actions["hold"]],
                "watch": [s.code for s in actions["watch"]],
            },
            "summary": {
                "total": len(signals),
                "buy_count": len(actions["buy"]),
                "sell_count": len(actions["sell"]),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== API: 大盘状态 ==========

@app.get("/api/market")
async def get_market():
    """获取大盘状态"""
    market_filter = get_market_filter()
    status, change = market_filter.get_market_status()

    return {
        "status": status.value,
        "change_pct": round(change, 2),
        "timestamp": datetime.now().isoformat(),
    }


# ========== API: 设置 ==========

@app.get("/api/settings")
async def get_settings():
    """获取当前设置"""
    config = get_config()
    return {
        "auto_trade": config.auto_trade,
        "notify_only": config.notify_only,
        "max_positions": config.max_positions,
        "total_capital": config.total_capital,
        "single_trade_limit": config.single_trade_limit,
        "stop_loss_pct": config.stop_loss_pct,
        "take_profit_pct": config.take_profit_pct,
        "open_window_start": config.open_window_start,
        "open_window_end": config.open_window_end,
        "market_crash_threshold": config.market_crash_threshold,
    }


@app.post("/api/settings")
async def update_settings(
    auto_trade: Optional[bool] = None,
    notify_only: Optional[bool] = None,
    max_positions: Optional[int] = None,
    total_capital: Optional[float] = None,
):
    """更新设置"""
    from config import _update_env_file

    updates = {}
    if auto_trade is not None:
        updates["AUTO_TRADE"] = str(auto_trade).lower()
    if notify_only is not None:
        updates["NOTIFY_ONLY"] = str(notify_only).lower()
    if max_positions is not None:
        updates["MAX_POSITIONS"] = str(max_positions)
    if total_capital is not None:
        updates["TOTAL_CAPITAL"] = str(total_capital)

    if updates:
        _update_env_file(updates)
        reload_config()

    return {"success": True, "updated": updates}


# ========== 健康检查 ==========

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ========== 启动服务 ==========

def run_server(host: str = "0.0.0.0", port: int = 8080):
    """启动Web服务"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
