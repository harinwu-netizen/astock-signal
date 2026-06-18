#!/usr/bin/env python3
"""
v6.20 迁移脚本: trades 表 quantity → quantity_lots 列重命名

⚠️ 重要:
  - 执行前会自动备份到 data/archive/trades_pre_v620_<timestamp>.db
  --check-only: 仅检查 schema,不做任何修改
  --force:     跳过确认直接执行

用法:
  python3 scripts/migrate_v620_rename_quantity.py --check-only  # 干跑
  python3 scripts/migrate_v620_rename_quantity.py              # 实际迁移
"""
import sqlite3
import sys
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "trades.db"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"


def check_schema(conn) -> dict:
    """检查 trades 表当前 schema,返回 {列名: 列定义}"""
    c = conn.cursor()
    c.execute("PRAGMA table_info(trades)")
    cols = {row[1]: row for row in c.fetchall()}
    return cols


def backup_db() -> Path:
    """备份 DB 到 archive 目录"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = ARCHIVE_DIR / f"trades_pre_v620_{ts}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def main():
    check_only = "--check-only" in sys.argv
    force = "--force" in sys.argv

    if not DB_PATH.exists():
        print(f"❌ DB 不存在: {DB_PATH}")
        sys.exit(1)

    print(f"📦 目标 DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cols = check_schema(conn)

    print(f"\n当前 trades 表共 {len(cols)} 列:")
    for name, row in cols.items():
        print(f"  {name:20s} {row[2]}")

    has_quantity = "quantity" in cols
    has_quantity_lots = "quantity_lots" in cols

    print(f"\n诊断:")
    print(f"  - 'quantity' 列存在: {has_quantity}")
    print(f"  - 'quantity_lots' 列存在: {has_quantity_lots}")

    if not has_quantity and not has_quantity_lots:
        print("\n✅ 两列都不存在,无需迁移")
        conn.close()
        return 0

    if has_quantity_lots and not has_quantity:
        print("\n✅ 已经是 v6.20 schema,无需迁移")
        conn.close()
        return 0

    if has_quantity and has_quantity_lots:
        print("\n⚠️ 两列同时存在(异常状态),请人工处理:")
        print("  1) 验证 quantity_lots 数据正确")
        print("  2) 手动 ALTER TABLE DROP COLUMN quantity")
        conn.close()
        return 1

    # 需要迁移: has_quantity=True, has_quantity_lots=False
    print(f"\n📋 计划迁移:")
    print(f"  ALTER TABLE trades RENAME COLUMN quantity TO quantity_lots;")
    print(f"  (SQLite 3.25+ 支持 RENAME COLUMN)")

    # 检查 SQLite 版本
    sqlite_version = sqlite3.sqlite_version
    print(f"  当前 SQLite 版本: {sqlite_version}")
    major, minor = map(int, sqlite_version.split(".")[:2])
    if (major, minor) < (3, 25):
        print(f"\n❌ SQLite 版本 < 3.25,不支持 RENAME COLUMN")
        print(f"   请升级 SQLite 或用 'ALTER TABLE trades ADD COLUMN quantity_lots;' + 数据迁移")
        conn.close()
        return 1

    if check_only:
        print("\n[--check-only] 仅检查,不执行迁移")
        conn.close()
        return 0

    # 实际迁移前确认
    if not force:
        print(f"\n⚠️  即将执行迁移,会自动备份到 {ARCHIVE_DIR}/trades_pre_v620_<ts>.db")
        resp = input("确认执行? (yes/no): ")
        if resp.lower() != "yes":
            print("已取消")
            conn.close()
            return 0

    # 备份
    backup = backup_db()
    print(f"\n✅ 已备份到: {backup}")

    # 执行 RENAME
    try:
        c = conn.cursor()
        c.execute("ALTER TABLE trades RENAME COLUMN quantity TO quantity_lots")
        conn.commit()
        print("✅ 列重命名成功")
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        print(f"   备份在 {backup},可手动恢复")
        conn.close()
        return 1

    # 验证
    cols_after = check_schema(conn)
    print(f"\n迁移后 schema:")
    for name, row in cols_after.items():
        print(f"  {name:20s} {row[2]}")

    if "quantity_lots" in cols_after and "quantity" not in cols_after:
        print(f"\n🎉 迁移完成!")
        print(f"   备份文件: {backup}")
        print(f"   可以用 'ls {ARCHIVE_DIR}' 查看备份")
    else:
        print(f"\n⚠️ 迁移后 schema 不符合预期,请检查")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())