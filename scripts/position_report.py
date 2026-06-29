#!/usr/bin/env python3
"""
持仓情况报告 · 信号灯模拟账户
============================
按真实券商 App 截图风格生成持仓报告（参考海赟 2026-06-22 截图）

数据源：
- trades.db        历史交易记录
- positions.json   当前持仓（如果有）
- watchlist.json   监控池
- 实时行情         妙查 / push2delay / 腾讯

使用：
  python3 scripts/position_report.py           # 默认报告当前持仓
  python3 scripts/position_report.py --live    # 拉最新实时行情重算浮盈
  python3 scripts/position_report.py --send    # 推送飞书
"""

import sqlite3
import json
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# ANSI 颜色 — 仅在 TTY 终端启用
USE_COLOR = sys.stdout.isatty() and os.environ.get('TERM', '') != 'dumb'
def red(s):
    return f'\033[1;31m{s}\033[0m' if USE_COLOR else s
def green(s):
    return f'\033[1;32m{s}\033[0m' if USE_COLOR else s
def bold(s):
    return f'\033[1m{s}\033[0m' if USE_COLOR else s

# 路径
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
DB_PATH = ROOT_DIR / 'data' / 'trades.db'
POSITIONS_PATH = ROOT_DIR / 'data' / 'positions.json'
WATCHLIST_PATH = ROOT_DIR / 'data' / 'watchlist.json'
REAL_PORTFOLIO_PATH = ROOT_DIR / 'data' / 'real_portfolio.json'
INITIAL_CAPITAL = 1_000_000


def load_positions():
    """加载当前持仓（positions.json + trades.db 推算）"""
    if POSITIONS_PATH.exists():
        with open(POSITIONS_PATH) as f:
            pos = json.load(f)
        if pos:
            return pos

    # 如果 positions.json 为空，从 trades.db 推算
    # 简单逻辑：最后一条交易是 BUY 且没有对应的 SELL/STOP_LOSS
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT action, code, name, price, quantity_lots, amount FROM trades ORDER BY created_at DESC LIMIT 1')
    row = c.fetchone()
    conn.close()

    if row and row[0] == 'BUY':
        action, code, name, price, qty, amount = row
        return [{
            'code': code,
            'name': name,
            'quantity': qty * 100,  # 手 → 股
            'available': qty * 100,
            'cost_price': price,
            'amount': amount,
        }]
    return []


def load_cash_and_history():
    """从 trades.db 推算当前现金 + 历史已清仓交易"""
    if not DB_PATH.exists():
        return INITIAL_CAPITAL, 0.0, [], 0.0

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT action, price, quantity_lots, amount, commission, stamp_tax,
                        code, name, trade_date, created_at
                 FROM trades ORDER BY created_at''')
    rows = c.fetchall()
    conn.close()

    cash = INITIAL_CAPITAL
    total_fee = 0
    position = None
    closed_trades = []  # [{name, code, buy_price, sell_price, qty, pnl, pnl_pct, buy_date, sell_date, fee}]
    realized_pnl = 0.0

    for action, price, qty, amount, commission, stamp_tax, code, name, date, ts in rows:
        fee = commission + stamp_tax
        total_fee += fee
        if action == 'BUY':
            cash -= (amount + commission)
            position = {
                'code': code, 'name': name, 'price': price, 'qty': qty,
                'amount': amount, 'date': date, 'fee': commission,
            }
        elif action in ('SELL', 'STOP_LOSS') and position:
            cash += (amount - commission - stamp_tax)
            pnl = (price - position['price']) * position['qty'] * 100 - (fee + position['fee'])
            pnl_pct = (price - position['price']) / position['price'] * 100
            closed_trades.append({
                'name': position['name'],
                'code': position['code'],
                'buy_price': position['price'],
                'sell_price': price,
                'qty': position['qty'],
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'buy_date': position['date'],
                'sell_date': date,
                'fee': fee + position['fee'],
            })
            realized_pnl += pnl
            position = None

    return cash, realized_pnl, closed_trades, total_fee


def fetch_live_price(code, name=''):
    """拉取实时行情（用 push2delay 或妙查）"""
    try:
        import requests
        # 简化：用 push2delay 公开端点
        if code.startswith('sh') or code.startswith('sz'):
            market = code[:2]
            pure_code = code[2:]
        else:
            market = 'sz' if code.startswith('0') or code.startswith('3') else 'sh'
            pure_code = code

        url = f'https://push2delay.eastmoney.com/api/qt/stock/get'
        params = {
            'secid': f'{market}.{pure_code}',
            'fields': 'f43,f44,f45,f46,f60,f169,f170',  # 现价、今开、最高、最低、昨收、涨跌额、涨跌幅
            'invt': 2,
        }
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json().get('data', {})
        if data:
            return {
                'price': data.get('f43', 0) / 100,
                'open': data.get('f44', 0) / 100,
                'high': data.get('f45', 0) / 100,
                'low': data.get('f46', 0) / 100,
                'prev_close': data.get('f60', 0) / 100,
                'change': data.get('f169', 0) / 100,
                'change_pct': data.get('f170', 0) / 100,
            }
    except Exception as e:
        pass
    return None


def load_real_portfolio():
    """加载实盘账户数据"""
    if not REAL_PORTFOLIO_PATH.exists():
        return None
    with open(REAL_PORTFOLIO_PATH) as f:
        return json.load(f)


def render_one_account(account_name, cash, positions, closed_trades, total_fee, realized_pnl, live=False, today_pnl_realized=0):
    """渲染单个账户的报告（信号灯模拟 / 实盘 共用）

    字段定义（海赟 2026-06-22 明确）：
    - 浮动盈亏：建仓以来累计盈亏（已实现 + 未实现）
    - 当日：今日产生的盈亏
    """
    # 计算持仓市值
    total_market_value = 0
    enriched_positions = []

    for pos in positions:
        code = pos.get('code', '')
        name = pos.get('name', '')
        qty = pos.get('quantity', pos.get('quantity_lots', 0) * 100)
        cost = pos.get('cost_price', pos.get('price', 0))
        market = pos.get('market', 'sz' if code.startswith('0') or code.startswith('3') else 'sh')
        if not market and 'code' in pos:
            market = 'sz' if pos['code'].startswith(('0', '3')) else 'sh'

        if live:
            quote = fetch_live_price(code, name)
            if quote:
                current_price = quote['price']
                today_change = quote['change']
            else:
                current_price = pos.get('last_price', cost)
                today_change = pos.get('today_change', 0)
        else:
            current_price = pos.get('last_price', cost)
            today_change = pos.get('today_change', 0)

        market_value = current_price * qty
        float_pnl = (current_price - cost) * qty
        float_pnl_pct = (current_price - cost) / cost * 100 if cost else 0
        total_market_value += market_value

        enriched_positions.append({
            'code': code,
            'name': name,
            'market': market,
            'quantity': qty,
            'available': pos.get('available', qty),
            'cost': cost,
            'current': current_price,
            'market_value': market_value,
            'float_pnl': float_pnl,
            'float_pnl_pct': float_pnl_pct,
            'today_pnl': today_change * qty,
        })

    total_assets = cash + total_market_value
    position_ratio = total_market_value / total_assets * 100 if total_assets else 0
    available = cash

    # 浮动盈亏 = 建仓以来累计（已实现 + 未实现）
    # 当日 = 今日产生的盈亏
    cumulative_pnl = realized_pnl  # 已是累计
    today_pnl_total = today_pnl_realized  # 今日产生的

    now = datetime.now()
    float_pnl_str = f'{abs(cumulative_pnl):,.2f}'
    today_pnl_str = f'{abs(today_pnl_total):,.2f}'
    float_pnl_sign = '+' if cumulative_pnl >= 0 else '−'
    today_pnl_sign = '+' if today_pnl_total >= 0 else '−'
    today_time = now.strftime('%H:%M:%S')

    # 顶部装饰条
    print()
    print('┌' + '─' * 60 + '┐')
    print('│' + ' ' * 60 + '│')

    # 浮动盈亏标题行 + 更新时间（始终显示）
    title_line = f'│  浮动盈亏(元)  👁'
    title_line = title_line.ljust(28)
    title_line += f'更新时间 {today_time}'
    title_line = title_line.ljust(60)
    print(title_line + '│')
    print('│' + ' ' * 60 + '│')
    # 数值行（盈利红色 / 亏损绿色，A股习惯）
    color_fn = red if cumulative_pnl >= 0 else green
    color_fn_today = red if today_pnl_total >= 0 else green
    value_line = f'│   {color_fn(float_pnl_sign + float_pnl_str)}    当日 {color_fn_today(today_pnl_sign + today_pnl_str)}'
    value_line = value_line.ljust(60)
    print(value_line + '│')

    print('│' + ' ' * 60 + '│')
    print('└' + '─' * 60 + '┘')
    print()

    # 三栏（账户资产 / 总市值 / 仓位）— 无框线
    if enriched_positions:
        ta_str = f'{total_assets:,.2f}'
        mv_str = f'{total_market_value:,.2f}'
        pr_str = f'{position_ratio:.2f}%'
    else:
        ta_str = f'{total_assets:,.2f}'
        mv_str = '0.00'
        pr_str = '0.00%'

    print(f'  账户资产 >                总市值 >                仓位 >')
    print(f'  {ta_str:>10}              {mv_str:>10}            {pr_str:>8}')
    print()
    print(f'  可用  {available:>12,.2f}    ⬇            可取  {available:>12,.2f}    ⬆')
    print()

    # 持仓列表（保留表头 + 分隔线，空仓时显示一行"无持仓"说明）
    print('─' * 60)
    print(f'{"名称/市值":<14} {"持仓/可用":<13} {"现价/成本":<13} {"浮动盈亏":<12}')
    print('─' * 60)
    if enriched_positions:
        for p in enriched_positions:
            market_tag = '深' if p['market'] == 'sz' else '沪'
            code_short = p['code'].replace('sz', '').replace('sh', '')
            print(f'【{p["name"]}】 [{market_tag}]')
            print(f'市值 {p["market_value"]:>12,.2f}')
            print(f'  {p["quantity"]:,} / {p["available"]:,}     {p["current"]:>6.3f}    {p["float_pnl"]:>+10.2f}')
            print(f'  {"":14} {p["quantity"]:,}     {p["cost"]:>6.3f}    {p["float_pnl_pct"]:>+5.2f}%')
            print()
    else:
        # 空仓：保留表头，表中加一行"无持仓"提示
        print('  （当前空仓，无持仓）')
    print('─' * 60)

    return {
        'cash': cash, 'total_assets': total_assets, 'total_market_value': total_market_value,
        'position_ratio': position_ratio, 'cumulative_pnl': cumulative_pnl,
        'today_pnl': today_pnl_total, 'realized_pnl': realized_pnl, 'total_fee': total_fee,
        'positions': enriched_positions, 'closed_trades': closed_trades,
    }


def render_report(live=False, show_history=True, account='sim'):
    """渲染报告

    account: 'sim'  信号灯模拟账户
             'real' 实盘账户
             'both' 双账户对比
    """
    if account == 'real':
        # 渲染实盘账户
        real = load_real_portfolio()
        if not real:
            print('❌ 实盘数据未配置: data/real_portfolio.json')
            return None
        # 转换实盘数据为统一格式
        positions = real.get('positions', [])
        # 现金 = 快照现金
        snapshot = real.get('history', [{}])[-1]
        cash = snapshot.get('snapshot_cash', 0)
        # 实盘没有"已实现盈亏"概念，用初始资金 - 当前总资产 估算
        initial = real.get('initial_capital', cash)
        current_assets = snapshot.get('snapshot_total_assets', cash)
        realized_pnl = current_assets - initial  # 粗略估算：含持仓浮盈
        closed_trades = []  # 实盘无清仓记录
        total_fee = 0  # 不知道
        return render_one_account(
            account_name=real.get('account_name', '实盘账户'),
            cash=cash,
            positions=positions,
            closed_trades=closed_trades,
            total_fee=total_fee,
            realized_pnl=realized_pnl,
            live=live,
        )

    elif account == 'both':
        print('\n' + '█' * 60)
        print('█' + ' ' * 18 + '信 号 灯 双 账 户 对 比' + ' ' * 19 + '█')
        print('█' * 60 + '\n')

        # 1. 信号灯模拟
        cash, realized_pnl, closed_trades, total_fee = load_cash_and_history()
        sim_positions = load_positions()
        sim = render_one_account(
            '信号灯模拟账户',
            cash, sim_positions, closed_trades, total_fee, realized_pnl,
            live=live,
        )

        # 2. 实盘
        print('\n' + '─' * 60 + '\n')
        real = render_report(live=live, show_history=show_history, account='real')

        # 3. 合计
        if sim and real:
            print('\n' + '═' * 60)
            print(f'  {"双 账 户 合 计":^50}')
            print('═' * 60)
            combined_assets = sim['total_assets'] + real['total_assets']
            combined_market = sim['total_market_value'] + real['total_market_value']
            combined_cash = sim['cash'] + real['cash']
            combined_float = sim['cumulative_pnl'] + real['cumulative_pnl']
            combined_today = sim['today_pnl'] + real['today_pnl']
            print(f'  总资产合计:   {combined_assets:>14,.2f} 元')
            print(f'  持仓市值合计: {combined_market:>14,.2f} 元')
            print(f'  现金合计:     {combined_cash:>14,.2f} 元')
            print(f'  浮动盈亏合计: {combined_float:>+14,.2f} 元')
            print(f'  当日盈亏合计: {combined_today:>+14,.2f} 元')
            print('═' * 60)
        return {'sim': sim, 'real': real}

    else:  # 'sim'
        cash, realized_pnl, closed_trades, total_fee = load_cash_and_history()
        # 计算今日盈亏
        from datetime import date
        today_str = date.today().isoformat()
        today_pnl_realized = sum(
            t['pnl'] for t in closed_trades
            if t.get('sell_date', '').startswith(today_str)
        )
        positions = load_positions()
        result = render_one_account(
            '信号灯模拟账户', cash, positions, closed_trades, total_fee, realized_pnl,
            live=live, today_pnl_realized=today_pnl_realized,
        )

        if show_history and closed_trades:
            print()
            print('  📊 历史已清仓记录:')
            print()
            print(f'  {"股票":<13} {"买价":>7} {"卖价":>7} {"手数":>5} {"盈亏":>14} {"收益率":>8}')
            print('  ' + '─' * 62)
            for t in closed_trades:
                code_short = t['code'].replace('sz', '').replace('sh', '')
                name_str = f'{t["name"]}({code_short})'
                print(f'  {name_str:<13} {t["buy_price"]:>7.3f} {t["sell_price"]:>7.3f} {t["qty"]:>5} {t["pnl"]:>+14,.2f} {t["pnl_pct"]:>+7.2f}%')

        print()
        print('─' * 60)
        print(f'  浮动盈亏（建仓以来累计）:  {result["cumulative_pnl"]:>+14,.2f} 元')
        print(f'  其中已实现:                {realized_pnl:>+14,.2f} 元')
        print(f'  其中未实现（持仓浮盈）:    {result["cumulative_pnl"] - realized_pnl:>+14,.2f} 元')
        print(f'  当日盈亏:                  {result["today_pnl"]:>+14,.2f} 元')
        print(f'  手续费累计:      {total_fee:>14,.2f} 元')
        print(f'  当前总资产:      {result["total_assets"]:>14,.2f} 元')
        print(f'  初始资金:        {INITIAL_CAPITAL:>14,.2f} 元')
        print(f'  累计收益率:      {(result["total_assets"]-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:>+13.2f}%')
        print('=' * 60)
        return result

    # 计算持仓市值和浮动盈亏
    total_market_value = 0
    total_float_pnl = 0
    today_pnl = 0
    enriched_positions = []

    for pos in positions:
        code = pos.get('code', '')
        name = pos.get('name', '')
        qty = pos.get('quantity', 0)
        cost = pos.get('cost_price', 0)
        market = pos.get('market', 'sz' if code.startswith('0') or code.startswith('3') else 'sh')

        if live:
            quote = fetch_live_price(code, name)
            if quote:
                current_price = quote['price']
                today_change = quote['change']
            else:
                current_price = cost
                today_change = 0
        else:
            # 用 positions.json 里的 last_price（如果有）
            current_price = pos.get('last_price', cost)
            today_change = pos.get('today_change', 0)

        market_value = current_price * qty
        float_pnl = (current_price - cost) * qty
        float_pnl_pct = (current_price - cost) / cost * 100 if cost else 0
        total_market_value += market_value

        enriched_positions.append({
            'code': code,
            'name': name,
            'market': market,
            'quantity': qty,
            'available': pos.get('available', qty),
            'cost': cost,
            'current': current_price,
            'market_value': market_value,
            'float_pnl': float_pnl,
            'float_pnl_pct': float_pnl_pct,
            'today_pnl': today_change * qty,
        })

    total_assets = cash + total_market_value
    position_ratio = total_market_value / total_assets * 100 if total_assets else 0
    available = cash  # 简化：现金 = 可用 = 可取（无融资融券时）

    now = datetime.now()

    # ========== 渲染 ==========
    print('=' * 60)
    print(f'{"持仓情况 · 信号灯模拟账户":^50}')
    print(f'{now.strftime("%Y-%m-%d %H:%M:%S"):^60}')
    print('=' * 60)
    print()
    print('┌' + '─' * 56 + '┐')
    print(f'│  浮动盈亏(元)   👁              理财持仓 →       {now.strftime("%H:%M:%S"):>13} │')
    print('│' + ' ' * 56 + '│')
    if enriched_positions:
        print(f'│  {total_float_pnl:>+12.2f}          当日 {today_pnl:>+10.2f}                │')
    else:
        print(f'│      —              当日    —                       │')
    print('└' + '─' * 56 + '┘')
    print()
    print('┌' + '─' * 17 + '┬' + '─' * 17 + '┬' + '─' * 17 + '┐')
    print(f'│  账户资产      │  总市值        │  仓位           │')
    print(f'│  {total_assets:>12.2f}  │  {total_market_value:>12.2f}  │  {position_ratio:>10.2f}%   │')
    print('└' + '─' * 17 + '┴' + '─' * 17 + '┴' + '─' * 17 + '┘')
    print()
    print(f'   可用  {available:>12.2f}              可取  {available:>12.2f}')
    print()

    # 持仓列表（保留表头 + 分隔线，空仓时显示一行"无持仓"说明）
    print('─' * 60)
    print(f'{"名称/市值":<14} {"持仓/可用":<13} {"现价/成本":<13} {"浮动盈亏":<12}')
    print('─' * 60)
    if enriched_positions:
        for p in enriched_positions:
            market_tag = '深' if p['market'] == 'sz' else '沪'
            code_short = p['code'].replace('sz', '').replace('sh', '')
            print(f'【{p["name"]}】 [{market_tag}]')
            print(f'市值 {p["market_value"]:>12,.2f}')
            print(f'  {p["quantity"]:,} / {p["available"]:,}     {p["current"]:>6.3f}    {p["float_pnl"]:>+10.2f}')
            print(f'  {"":14} {p["quantity"]:,}     {p["cost"]:>6.3f}    {p["float_pnl_pct"]:>+5.2f}%')
            print()
    else:
        # 空仓：保留表头，表中加一行"无持仓"提示
        print('  （当前空仓，无持仓）')
    print('─' * 60)

    # 历史已清仓记录
    if show_history and closed_trades:
        print()
        print('  📊 历史已清仓记录:')
        print()
        print(f'  {"股票":<13} {"买价":>7} {"卖价":>7} {"手数":>5} {"盈亏":>14} {"收益率":>8}')
        print('  ' + '─' * 62)
        for t in closed_trades:
            code_short = t['code'].replace('sz', '').replace('sh', '')
            name_str = f'{t["name"]}({code_short})'
            print(f'  {name_str:<13} {t["buy_price"]:>7.3f} {t["sell_price"]:>7.3f} {t["qty"]:>5} {t["pnl"]:>+14,.2f} {t["pnl_pct"]:>+7.2f}%')

    print()
    print('─' * 60)
    print(f'  累计已实现盈亏:  {realized_pnl:>+14,.2f} 元')
    print(f'  浮动盈亏:        {total_float_pnl:>+14,.2f} 元')
    print(f'  当日盈亏:        {today_pnl:>+14,.2f} 元')
    print(f'  手续费累计:      {total_fee:>14,.2f} 元')
    print(f'  当前总资产:      {total_assets:>14,.2f} 元')
    print(f'  初始资金:        {INITIAL_CAPITAL:>14,.2f} 元')
    print(f'  累计收益率:      {(total_assets-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:>+13.2f}%')
    print('=' * 60)

    return {
        'cash': cash,
        'total_assets': total_assets,
        'total_market_value': total_market_value,
        'position_ratio': position_ratio,
        'total_float_pnl': total_float_pnl,
        'today_pnl': today_pnl,
        'realized_pnl': realized_pnl,
        'total_fee': total_fee,
        'positions': enriched_positions,
        'closed_trades': closed_trades,
    }


def push_to_feishu(report):
    """推送到飞书（用 message 工具）"""
    print('\n📨 推送飞书请用 message 工具调用')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='信号灯持仓报告（券商 App 风格）')
    parser.add_argument('--live', action='store_true', help='拉取实时行情')
    parser.add_argument('--no-history', action='store_true', help='不显示历史清仓记录')
    parser.add_argument('--send', action='store_true', help='推送到飞书')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式')
    parser.add_argument('--account', choices=['sim', 'real', 'both'], default='sim',
                        help='账户类型: sim=模拟(默认), real=实盘, both=双账户对比')
    args = parser.parse_args()

    if args.json:
        # 输出 JSON
        report = render_report(live=args.live, show_history=False, account=args.account)
        if report:
            print('\n--- JSON ---')
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        report = render_report(live=args.live, show_history=not args.no_history, account=args.account)
        if args.send:
            push_to_feishu(report)
