#!/bin/bash
# vringon-cost 백엔드. jhkim 전용 포트 18452, 다른 서비스와 무관하게 동작한다.
# cd 는 루프 안에서 한다 — 배포로 app/ 을 갈아끼워도 새 경로를 잡는다.
while true; do
  cd /data/jhkim/vringon-cost-svc/app || { sleep 5; continue; }
  set -a; source ../env; set +a
  ../venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 18452 --workers 1 \
    >> ../server.log 2>&1
  echo "$(date) 재시작" >> ../server.log
  sleep 5
done
