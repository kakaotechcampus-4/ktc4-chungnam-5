#!/usr/bin/env bash
# 서버에서 실행하는 배포 스크립트. CI(ssm send-command)와 사람이 같은 스크립트를 쓴다.
# 배포 로직은 워크플로 YAML 이 아니라 여기에 둔다.
#
# 사용: infra/deploy.sh [커밋 SHA | 버전 태그]   (생략하면 origin/main)
#   infra/deploy.sh v2026.10.19     릴리스 태그로 배포 (H2 워크플로는 태그가 가리키는 SHA 를 넘긴다)
# main 에 있는 커밋만 배포한다 — 리뷰를 거친 코드만 서버에 올리려는 것이다. 핫픽스도 main 에 머지한 뒤 배포한다.
# 태그는 아무 커밋에나 붙일 수 있으므로 아래에서 실제로 검사한다.
#
# 버전과 커밋은 git 에서 읽어 이미지에 넣는다 (GIT_SHA · APP_VERSION → Dockerfile ARG → /health).
# SHA 는 자르지 않는다 — 배포 워크플로가 /health 의 sha 를 GITHUB_SHA 전체와 비교한다.
# 버전 태그는 한 번 붙이면 지우거나 옮기지 않는다. 옮긴 태그는 git fetch --tags 가 덮어쓰지 않아
# 여기서 실패로 끝난다 — 고칠 것이 있으면 다음 번호(v2026.10.19.1)로 새로 붙인다.
#
# .env.prod 는 gitignore 대상이라 git checkout 을 해도 지워지지 않는다.
# 저장소가 public 이라 git fetch 에 인증이 필요 없다.
set -euo pipefail
cd "$(dirname "$0")/.."
# 운영·스테이징 배포가 겹치지 않게 한 번에 하나씩 돈다 (deploy-staging.sh 와 같은 잠금).
# 빌드가 겹치면 4GiB 서버가 스왑으로 밀려 운영 응답이 느려진다. 앞 배포가 끝날 때까지 기다린다.
# ubuntu 사용자로 실행한다 (CI 도 runuser -u ubuntu). root 가 잠금 파일을 먼저 만들면 ubuntu 가 열지 못한다.
exec 9>/tmp/glp1-deploy.lock
flock 9
# 운영 Caddy 가 스테이징 api 에 닿는 외부 네트워크. compose 가 만들지 않으므로 먼저 있어야 한다.
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge >/dev/null
git fetch --quiet --tags origin main
# checkout 전에 대상을 정하고 검사한다 — 검사에 걸려도 서버의 작업 트리는 움직이지 않는다.
TARGET=$(git rev-parse --verify --quiet "${1:-origin/main}^{commit}") \
  || { echo "배포 대상을 찾을 수 없다: ${1:-origin/main}" >&2; exit 1; }
git merge-base --is-ancestor "$TARGET" origin/main \
  || { echo "main 에 없는 커밋이라 배포하지 않는다: $TARGET" >&2; exit 1; }
git checkout --quiet --detach "$TARGET"
GIT_SHA=$TARGET
# 이 커밋에 붙은 릴리스 태그. 태그 없이 SHA 로 배포하면 빈 값이다 (/health 의 version 이 null).
APP_VERSION=$(git describe --tags --exact-match --match 'v*' "$TARGET" 2>/dev/null || true)
export GIT_SHA APP_VERSION
echo "배포: ${APP_VERSION:-(버전 없음)} $GIT_SHA"
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
