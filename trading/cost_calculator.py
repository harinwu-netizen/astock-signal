# -*- coding: utf-8 -*-
"""
实盘成本计算器
佣金、印花税、滑点、成交量限制
"""

# ============================================================================
# 成本配置（可从 .env 覆盖）
# ============================================================================

COMMISSION_RATE = 0.00025    # 佣金：万2.5
MIN_COMMISSION = 5.0         # 最低佣金：5元
STAMP_TAX = 0.001           # 印花税：千1（仅卖出）
SLIPPAGE = 0.002            # 滑点：±0.2%（下单时价格偏移）
MAX_VOLUME_RATIO = 0.05     # 单次不超过当日成交量5%


def calculate_buy_cost(amount: float) -> dict:
    """
    计算买入成本

    Args:
        amount: 买入金额（price × quantity × 100）

    Returns:
        dict: {
            gross_amount,  # 原始金额
            commission,     # 佣金
            slippage_cost, # 滑点成本
            total_cost,    # 总成本（含佣金+滑点）
        }
    """
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    slippage_cost = amount * SLIPPAGE  # 买入时价格偏高
    total_cost = amount + commission + slippage_cost

    return {
        "gross_amount": round(amount, 2),
        "commission": round(commission, 2),
        "slippage_cost": round(slippage_cost, 2),
        "total_cost": round(total_cost, 2),
    }


def calculate_sell_cost(amount: float) -> dict:
    """
    计算卖出成本（含佣金+印花税+滑点）

    Args:
        amount: 卖出金额（price × quantity × 100）

    Returns:
        dict: {
            gross_amount,     # 原始金额
            commission,       # 佣金
            stamp_tax,       # 印花税
            slippage_cost,   # 滑点成本
            total_cost,      # 总成本（从本金中扣除）
            net_proceeds,    # 净得（到账金额）
        }
    """
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = amount * STAMP_TAX
    slippage_cost = amount * SLIPPAGE  # 卖出时价格偏低
    total_cost = commission + stamp_tax + slippage_cost
    net_proceeds = amount - total_cost

    return {
        "gross_amount": round(amount, 0),
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "slippage_cost": round(slippage_cost, 2),
        "total_cost": round(total_cost, 2),
        "net_proceeds": round(net_proceeds, 2),
    }


def check_volume_limit(quantity: int, daily_volume: int) -> tuple:
    """
    检查成交量限制

    Args:
        quantity: 拟交易数量（手）
        daily_volume: 当日成交量（手）

    Returns:
        (是否合规, 实际可交易数量, 原因)
    """
    max_allowed = int(daily_volume * MAX_VOLUME_RATIO)
    if daily_volume <= 0:
        return True, quantity, "停牌或成交量为0，跳过检查"

    if quantity <= max_allowed:
        return True, quantity, f"成交量限制通过（{quantity}手 ≤ {max_allowed}手上限）"
    else:
        return False, max_allowed, f"超过成交量限制（{quantity}手 > {max_allowed}手上限），调整为{max_allowed}手"


def calc_real_buy_price(price: float) -> float:
    """买入时实际成交价（考虑滑点）"""
    return round(price * (1 + SLIPPAGE), 2)


def calc_real_sell_price(price: float) -> float:
    """卖出时实际成交价（考虑滑点）"""
    return round(price * (1 - SLIPPAGE), 2)
