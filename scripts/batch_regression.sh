#!/usr/bin/env bash
# 全量回归批次:16 新视频(4类目) + 4 基线视频(首个基线已冒烟)
set -u
PY=.venv/Scripts/python.exe
BVS=(
  # 基线(可行性实验样本)
  BV1NxgH6wEsW BV13F9mYoEhN BV1GtswzgEgP BV1L9WPedEwf
  # 水电
  BV1mQtB6FEuG BV1aaY96dEKU BV1kr4y1i7mD BV1RK4y1g7xy
  # 美缝
  BV1FH4y1w7W4 BV17N41157dA BV1kbtJ6AE7u BV1PMGzzeEhm
  # 乳胶漆
  BV15s4y167CW BV1vx4y1D7eQ BV1Tv4y1m7R3 BV1oT4y1k7sc
  # 卫生间地漏/回填
  BV1urgzeyEL4 BV1t2dSB8EDy BV1J8WDefEzH BV1eTs4enEG4
)
for bv in "${BVS[@]}"; do
  echo "== import $bv"
  $PY -m reno import "https://www.bilibili.com/video/${bv}/" 2>&1 | tail -1
done
echo "== process all"
$PY -m reno run
echo "== judge"
$PY -m reno judge
echo "== report"
$PY -m reno report
echo "== BATCH DONE"
