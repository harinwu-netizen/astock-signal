#!/bin/bash
# 核查妙查日上限 + push2delay 兜底表现
# 用法: bash scripts/check_miaochang_quota.sh
# 海赟要求:下午 15:00 收盘后跑

set -e
cd /root/.openclaw/workspace/astock-signal

LOG="logs/watch_cron.log"
TODAY=$(date '+%Y-%m-%d')

echo "============================================================"
echo "  妙查日上限核查报告  $TODAY"
echo "============================================================"
echo ""

echo "## 1. 全天总调用次数"
echo ""
echo "妙查总调用: $(grep "$TODAY.*\[MC\] 调用妙查" $LOG | wc -l) 次"
echo "成功(妙想数据获取成功): $(grep "$TODAY.*\[MoneyFlow\] 妙想数据获取成功" $LOG | wc -l) 次"
echo "失败: $(grep "$TODAY.*\[MC\].*失败\|$TODAY.*妙想数据.*失败\|$TODAY.*miaochang.*error" $LOG | wc -l) 次"
echo ""

echo "## 2. 首次失败时间"
echo ""
FIRST_FAIL=$(grep -n "$TODAY.*\[MC\].*失败\|$TODAY.*妙想数据.*失败\|$TODAY.*miaochang.*error" $LOG | head -1)
if [ -n "$FIRST_FAIL" ]; then
  echo "$FIRST_FAIL"
  echo ""
  echo "首次失败上下文(前 3 行后 5 行):"
  echo "$FIRST_FAIL" | cut -d: -f1 | xargs -I {} sed -n '{}p' $LOG
else
  echo "(全天无失败记录)"
fi
echo ""

echo "## 3. 各时段调用分布"
echo ""
for hour in 09 10 11 12 13 14 15; do
  cnt=$(grep "$TODAY $hour:" $LOG 2>/dev/null | grep -c "调用妙查" || true)
  fail=$(grep "$TODAY $hour:" $LOG 2>/dev/null | grep -cE "MC.*失败|miaochang.*error" || true)
  if [ "$cnt" -gt 0 ] || [ "$fail" -gt 0 ]; then
    echo "  $hour:00 时段: 调用 $cnt 次, 失败 $fail 次"
  fi
done
echo ""

echo "## 4. push2delay 兜底表现"
echo ""
echo "push2delay 跳过(端点不健康): $(grep "$TODAY.*push2delay 端点不健康" $LOG | wc -l) 次"
echo "push2delay 命中(供数据): $(grep "$TODAY.*push2delay 兜底成功" $LOG | wc -l) 次"
echo "push2delay API 返回 data=None: $(grep "$TODAY.*API 错误.*data.*None" $LOG | wc -l) 次"
echo ""

echo "## 5. 失败后 fallback 行为"
echo ""
# 找出妙查失败后,push2delay 是否接上
grep -A 5 "$TODAY.*\[MC\].*失败" $LOG 2>/dev/null | head -20
echo ""

echo "## 6. 各股票调用次数"
echo ""
for code in 000629 603683 300308 002202 002792 002371 688981; do
  cnt=$(grep "$TODAY.*$code" $LOG | grep -c "调用妙查")
  echo "  $code: $cnt 次"
done
echo ""

echo "## 7. 决策建议"
echo ""
TOTAL=$(grep "$TODAY.*\[MC\] 调用妙查" $LOG | wc -l)
echo "全天总调用: $TOTAL 次"
echo "如果上限 60 次,触限时间应该是:扫描 #$((60 / 7 + 1)) (约上午 + 下午某轮)"
echo "如果上限 50 次,触限时间应该是:扫描 #$((50 / 7 + 1)) (约上午末 + 下午初)"
echo ""

echo "============================================================"
echo "  核查完毕"
echo "============================================================"
