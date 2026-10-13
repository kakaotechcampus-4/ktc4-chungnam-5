#!/usr/bin/env bash
# 서버에서 실행하는 배포 스크립트. CI(ssm send-command)와 사람이 같은 스크립트를 쓴다.
# 배포 로직은 워크플로 YAML 이 아니라 여기에 둔다.
#
# 사용: infra/deploy.sh [커밋 SHA]   (생략하면 origin/main)
#
# .env.prod 는 gitignore 대상이라 git checkout 을 해도 지워지지 않는다.
# 저장소가 public 이라 git fetch 에 인증이 필요 없다.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git checkout --quiet --detach "${1:-origin/main}"
cd infra
COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.prod)
# 이미지를 먼저 만든다. 빌드 동안은 옛 컨테이너가 계속 일한다.
"${COMPOSE[@]}" build
# 마이그레이션 전에 옛 워커를 내린다. api 는 기동할 때 마이그레이션을 돌리고 worker 는 그 뒤에
# 교체되므로, 그대로 두면 옛 워커가 마이그레이션과 겹친다 — 행 잠금을 쥐고 있으면 ADD COLUMN 이
# 막히고, 새 enum 값을 옛 코드가 읽으면 터진다 (예: 9a1c4e7b2d3f). 처리 중인 작업은
# stop_grace_period(60초) 안에 끝내고 내려간다. 첫 배포처럼 워커가 없으면 아무것도 안 한다.
"${COMPOSE[@]}" stop worker
# --wait: 모든 서비스가 healthy 가 될 때까지 기다리고, 아니면 실패로 끝낸다 (CI 가 실패를 본다)
"${COMPOSE[@]}" up -d --wait
# 디스크는 50GB 고정이라 빌드할 때마다 쌓이는 이미지·캐시를 지운다
docker image prune -f
docker builder prune -f --filter until=72h
