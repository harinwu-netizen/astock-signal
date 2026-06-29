"""
资金流数据获取 - mx-stocks-screener (v6.13 新增)
通过 subprocess 调用 mx-stocks-screener skill,解析 CSV 提取关键字段

设计背景 (2026-06-25):
  - mx-finance-data 走 searchData 端点,与 mx-financial-assistant 共用 EM_API_KEY, ~47次/日
  - 13:00 触限后全天 403
  - push2delay 是日 K 端点,13:00-15:00 拿不到盘中数据
  - 妙想 skill 工具包内的 mx-stocks-screener 走 **独立端点 selectSecurity**,独立配额,全天可用
  - 字段:主力净额/大单净额/超大单净额 + 完整行情
  - 缺失:DDX/DDY、main_in/out、mid_net/small_net

调用示例:
  from data_provider.screener_money_flow import get_money_flow_via_screener
  r = get_money_flow_via_screener("000629", "钒钛股份")
  # 返回: {code, name, main_net, big_net, super_net, ...} (单位:万元)
"""

import csv
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("MoneyFlow.Screener")

# mx-stocks-screener 脚本路径
SCREENER_SCRIPT = "/root/.openclaw/workspace/skills/mx-stocks-screener/scripts/get_data.py"
# screener 输出目录(以 cwd 为基准,所以从 astock-signal/ 跑会到 astock-signal/miaoxiang/...)
SCREENER_OUTPUT_DIR = "miaoxiang/mx_stocks_screener"


def _parse_value_with_unit(s: str) -> Optional[float]:
    """
    解析 screener CSV 里的带单位数字,如:
      "4019.16万" → 4019.16 (万元)
      "1.35亿"   → 13500.0 (万元)
      "1.35亿股" → 13500.0 (万股)
      "3.18"     → 3.18
      "-2.75"    → -2.75
    """
    if not s or s == '-' or s == 'N/A':
        return None
    s = s.strip().replace(',', '').replace(' ', '')

    # 匹配数字+可选单位
    m = re.match(r'^(-?\d+\.?\d*)([万亿%元]?)(股?)$', s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None

    num = float(m.group(1))
    unit = m.group(2)
    if unit == '万':
        return num  # 已经是万元
    elif unit == '亿':
        return num * 10000  # 亿 → 万
    elif unit == '%':
        return num  # 百分比
    else:
        return num  # 元 / 纯数字 / 维持原值


def _find_latest_csv_for_code(code: str) -> Optional[Path]:
    """
    从 screener 输出目录找包含本股票代码的最新 CSV 文件。
    """
    output_dir = Path(SCREENER_OUTPUT_DIR)
    if not output_dir.is_dir():
        return None

    candidates = []
    for csv_file in output_dir.glob("*.csv"):
        # 读 description.txt 看是否查询了这只股票
        desc_file = csv_file.with_name(csv_file.stem + "_description.txt")
        if not desc_file.exists():
            continue
        try:
            content = desc_file.read_text(encoding="utf-8")
            # 提取"查询内容"行
            m = re.search(r'查询内容:\s*([^\n]+)', content)
            if m and code in m.group(1):
                candidates.append((csv_file.stat().st_mtime, csv_file))
        except Exception:
            continue

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _parse_screener_csv(csv_path: Path, code: str) -> Optional[Dict[str, Any]]:
    """
    解析 screener 输出的 CSV,提取资金流相关字段。
    字段名格式: "主力净额(元) 2026.06.25" → 提取 "主力净额(元)" 作为 key
    """
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("代码") == code:
                    return row
    except Exception as e:
        logger.warning(f"[Screener] CSV 解析失败: {e}")
        return None
    return None


def _extract_money_flow_from_row(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    从 screener 行的所有字段里,提取日期+资金流字段。
    """
    result = {"code": "", "name": "", "date": ""}

    # 基本信息
    result["code"] = row.get("代码", "").strip()
    result["name"] = row.get("名称", "").strip()

    # 日期(从字段名后缀提取,如 "主力净额(元) 2026.06.25")
    date_pattern = re.compile(r'\d{4}\.\d{2}\.\d{2}')
    for key in row.keys():
        m = date_pattern.search(key)
        if m:
            result["date"] = m.group(0)
            break

    # 资金流字段
    field_map = {
        "main_net": r"主力净额",
        "big_net": r"大单净额",
        "super_net": r"超大单净额",
        # 以下字段 screener 不提供,标记为 0
        "main_in": None,
        "main_out": None,
        "big_in": None,
        "big_out": None,
        "super_in": None,
        "super_out": None,
        "small_net": None,
        "mid_net": None,
        "ddx": None,
        "ddy": None,
    }

    for target_key, pattern in field_map.items():
        if pattern is None:
            # screener 不提供,填 0
            result[target_key] = 0.0
            continue

        for key, val in row.items():
            if re.search(pattern, key):
                parsed = _parse_value_with_unit(val)
                if parsed is not None:
                    result[target_key] = parsed
                break
        else:
            result[target_key] = 0.0

    return result


def get_money_flow_via_screener(code: str, name: str = "") -> Optional[Dict[str, Any]]:
    """
    通过 mx-stocks-screener 获取单只股票资金流数据(独立配额,全天可用)

    Args:
        code: 股票代码,如 "000629"
        name: 股票名称,如 "钒钛股份"

    Returns:
        dict 含 main_net/big_net/super_net (单位:万元)
              ddx/ddy/main_in/out 等 screener 不提供,填 0
        None 表示失败
    """
    code = str(code).strip()
    # 去掉 sh/sz 前缀
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[2:]
            break

    # 构造查询
    if name:
        query = f"{code} {name} 今日资金流向 主力 大单 超大单"
    else:
        query = f"{code} 今日资金流向 主力 大单 超大单"

    # 调用 screener
    try:
        result = subprocess.run(
            ["python3", SCREENER_SCRIPT, "--query", query, "--select-type", "A股"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"[Screener] subprocess 失败: {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"[Screener] 调用超时: {code}")
        return None
    except Exception as e:
        logger.warning(f"[Screener] 调用异常: {e}")
        return None

    # 找最新 CSV
    csv_path = _find_latest_csv_for_code(code)
    if not csv_path:
        logger.warning(f"[Screener] 未找到 {code} 的 CSV 输出")
        return None

    # 解析 CSV
    row = _parse_screener_csv(csv_path, code)
    if not row:
        logger.warning(f"[Screener] CSV 中无 {code}")
        return None

    # 提取资金流字段
    data = _extract_money_flow_from_row(row)
    if not data:
        return None

    logger.info(
        f"[Screener] 资金流获取成功: {code} {data['name']} "
        f"主力={data['main_net']:+.0f}万 大单={data['big_net']:+.0f}万 "
        f"超大单={data['super_net']:+.0f}万"
    )
    return data


# 自检
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "000629"
    name = sys.argv[2] if len(sys.argv) > 2 else "钒钛股份"
    r = get_money_flow_via_screener(code, name)
    if r:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("❌ 获取失败")
