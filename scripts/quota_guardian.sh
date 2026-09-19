#!/usr/bin/env bash
# 配额恢复看护:每 4 分钟探测一次 API,恢复后自动续跑批量 -> judge -> report
PY=.venv/Scripts/python.exe
while true; do
  R=$($PY - <<'EOF'
import sys, json, urllib.request
sys.path.insert(0, '.')
from reno import config
req = urllib.request.Request("https://open.bigmodel.cn/api/paas/v4/chat/completions",
    data=json.dumps({"model":"glm-4.6","messages":[{"role":"user","content":"ok"}],
        "max_tokens":4,"thinking":{"type":"disabled"}}).encode(),
    headers={"Authorization":"Bearer "+config.get("zhipu_api_key"),"Content-Type":"application/json"})
try:
    urllib.request.urlopen(req, timeout=45)
    print("UP")
except Exception as e:
    print("DOWN")
EOF
)
  echo "$(date +%H:%M:%S) api=$R"
  if [ "$R" = "UP" ]; then
    echo "quota recovered -> resuming batch"
    $PY -m reno run
    $PY -m reno judge
    $PY -m reno report
    echo "GUARDIAN_BATCH_DONE"
    break
  fi
  sleep 240
done
