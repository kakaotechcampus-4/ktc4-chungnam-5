#!/usr/bin/env bash
# 스테이징 배포 스크립트. /opt/glp1-staging 에서 develop 커밋을 올린다. 운영은 deploy.sh.
#
# 사용: infra/deploy-staging.sh [커밋 SHA]   (생략하면 origin/develop)
#
# 운영 compose 위에 docker-compose.staging.yml 을 덮어써 이름·이미지·볼륨을 glp1-staging 으로 가른다.
# 80/443 은 운영 Caddy 가 맡고, glp1-edge 네트워크로 스테이징 api 에 닿는다.
# 흐름은 deploy.sh 와 같다 — 잠금 · checkout 뒤에 bg-deploy.sh 가 Blue/Green 으로 올린다.
set -euo pipefail
cd "$(dirname "$0")/.."
# deploy.sh 와 같은 잠금. 운영 배포가 돌고 있으면 끝날 때까지 기다린다.
# ubuntu 사용자로 실행한다 (CI 도 runuser -u ubuntu). root 가 잠금 파일을 먼저 만들면 ubuntu 가 열지 못한다.
exec 9>/tmp/glp1-deploy.lock
flock 9
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge >/dev/null
git fetch --quiet origin develop
git checkout --quiet --detach "${1:-origin/develop}"
# 스테이징은 수시로 배포하므로 버전 없이 SHA 만 싣는다 (/health 의 version 은 null).
# APP_VERSION 을 비워 export 하는 건 셸에 남은 값이 이미지에 섞이지 않게 하려는 것이다.
GIT_SHA=$(git rev-parse HEAD)
APP_VERSION=
export GIT_SHA APP_VERSION
# 나머지는 운영과 같은 Blue/Green 로직이다. 이미지 정리는 glp1-staging-be 만 건드린다.
exec infra/bg-deploy.sh staging
