#!/usr/bin/env python3
"""网格搜索调参脚本"""
import subprocess, sys, logging
logging.disable(logging.WARNING)

from backtest.multi_engine import MultiStockBacktestEngine

CODES = ['002997', '603596', '002801']

def patch_file(path, old, new):
    with open(path) as f:
        content = f.read()
    if old not in content:
        return False
    with open(path, 'w') as f:
        f.write(content.replace(old, new, 1))
    return True

def restore_files():
    subprocess.run(['git', 'checkout', 'HEAD', '--',
        'indicators/signal_consolidate.py',
        'indicators/signal_weak.py', 
        'indicators/signal_strong.py'],
        cwd='~/.openclaw/workspace/astock-signal', shell=True)

def run_backtest():
    engine = MultiStockBacktestEngine(CODES, initial_capital=1000000.0)
    return engine.run(days=90)

def main():
    base = '/root/.openclaw/workspace/astock-signal'
    results = []
    
    # ===== 震荡市 RSI止盈阈值 =====
    print('=== 震荡市 RSI止盈阈值 ===')
    for rsi_sell in [50, 55, 60]:
        subprocess.run(['git', 'checkout', 'HEAD', '--',
            'indicators/signal_consolidate.py', 'indicators/signal_weak.py', 
            'indicators/signal_strong.py'],
            cwd=base)
        
        patch_file(f'{base}/indicators/signal_consolidate.py',
            'if rsi > 55:',
            f'if rsi > {rsi_sell}:')
        
        r = run_backtest()
        print(f'RSI止盈={rsi_sell}: 收益={r["total_return"]:.2f}% 回撤={r["max_drawdown"]:.2f}% 交易={r["total_trades"]} 胜率={r["win_rate"]:.1f}%')
        results.append(('cons_rsi_sell', rsi_sell, r))
    
    # ===== 震荡市 RSI买入上限 =====
    print()
    print('=== 震荡市 RSI买入上限 ===')
    for rsi_upper in [55, 58, 60]:
        subprocess.run(['git', 'checkout', 'HEAD', '--',
            'indicators/signal_consolidate.py', 'indicators/signal_weak.py',
            'indicators/signal_strong.py'],
            cwd=base)
        
        patch_file(f'{base}/indicators/signal_consolidate.py',
            'if 30 <= rsi <= 60:',
            f'if 30 <= rsi <= {rsi_upper}:')
        
        r = run_backtest()
        print(f'RSI买入上限={rsi_upper}: 收益={r["total_return"]:.2f}% 回撤={r["max_drawdown"]:.2f}% 交易={r["total_trades"]} 胜率={r["win_rate"]:.1f}%')
        results.append(('cons_rsi_upper', rsi_upper, r))

    # ===== 弱市 RSI止盈阈值 =====
    print()
    print('=== 弱市 RSI止盈阈值 ===')
    for rsi_sell in [50, 55, 60]:
        subprocess.run(['git', 'checkout', 'HEAD', '--',
            'indicators/signal_consolidate.py', 'indicators/signal_weak.py',
            'indicators/signal_strong.py'],
            cwd=base)
        
        patch_file(f'{base}/indicators/signal_weak.py',
            'if rsi > 65:',
            f'if rsi > {rsi_sell}:')
        # Also change the second RSI check
        patch_file(f'{base}/indicators/signal_weak.py',
            'elif rsi > 50:',
            f'elif rsi > {rsi_sell - 10}:')
        
        r = run_backtest()
        print(f'弱市RSI止盈={rsi_sell}: 收益={r["total_return"]:.2f}% 回撤={r["max_drawdown"]:.2f}% 交易={r["total_trades"]} 胜率={r["win_rate"]:.1f}%')
        results.append(('weak_rsi_sell', rsi_sell, r))

    # 恢复文件
    subprocess.run(['git', 'checkout', 'HEAD', '--',
        'indicators/signal_consolidate.py', 'indicators/signal_weak.py',
        'indicators/signal_strong.py'],
        cwd=base)
    
    print()
    print('=== 最优参数 ===')
    results.sort(key=lambda x: x[2]['total_return'], reverse=True)
    for name, val, r in results[:5]:
        print(f'{name}={val}: 收益={r["total_return"]:.2f}%')

if __name__ == '__main__':
    main()
