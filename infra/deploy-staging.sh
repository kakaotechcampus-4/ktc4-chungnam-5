#!/usr/bin/env bash
# 스테이징 배포 스크립트. /opt/glp1-staging 에서 develop 커밋을 올린다. 운영은 deploy.sh.
#
# 사용: infra/deploy-staging.sh [커밋 SHA]   (생략하면 origin/develop)
#
# 운영 compose 위에 docker-compose.staging.yml 을 덮어써 이름·이미지·볼륨을 glp1-staging 으로 가른다.
# 80/443 은 운영 Caddy 가 맡고, glp1-edge 네트워크로 스테이징 api 에 닿는다.
# 흐름은 deploy.sh 와 같다 — 워커를 먼저 내리는 이유도 거기 적혀 있다.
set -euo pipefail
cd "$(dirname "$0")/.."
# deploy.sh 와 같은 잠금. 운영 배포가 돌고 있으면 끝날 때까지 기다린다.
exec 9>/tmp/glp1-deploy.lock
flock 9
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge >/dev/null
git fetch --quiet origin develop
git checkout --quiet --detach "${1:-origin/develop}"
cd infra
COMPOSE=(docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging)
"${COMPOSE[@]}" build
"${COMPOSE[@]}" stop worker
"${COMPOSE[@]}" up -d --wait
# dangling 이미지만 지우므로 운영 이미지(glp1-prod-*)는 남는다
docker image prune -f
docker builder prune -f --filter until=72h
