#!/usr/bin/env bash
# Blue/Green 배포 (contracts/Deployment.md J3). api 를 두 벌 두고, 새 쪽을 띄워 확인한 뒤
# Caddy 가 받는 쪽을 바꾼다. Caddy 는 다시 만들지 않고 reload 만 한다.
#
# 사용: bg-deploy.sh prod|staging    (GIT_SHA · APP_VERSION 을 export 한 상태로)
#
# deploy.sh · deploy-staging.sh 가 잠금을 잡고 checkout 한 뒤 exec 로 부른다. 직접 부르지
# 않는다 — 잠금 없이 돌면 운영·스테이징 배포가 같은 Caddy 와 upstream 디렉터리를 동시에 건드린다.
#
# 전환 전 단계(빌드, 마이그레이션, 새 쪽 기동, Caddy 쪽 확인) 중 하나라도 실패하면 전환하지 않고
# 끝난다. 사용자는 계속 지금 받는 쪽을 쓴다.
set -euo pipefail
cd "$(dirname "$0")"
ENV=${1:?prod 또는 staging}
. ./bg-lib.sh

: "${GIT_SHA:?deploy.sh 가 export 한다}"
# 운영 릴리스는 버전 태그(v2026.10.18), 스테이징과 태그 없는 수동 배포는 SHA 앞 7자
TAG=${APP_VERSION:-${GIT_SHA:0:7}}

ensure_upstreams

if [ -f "$STATE" ]; then
  # shellcheck source=/dev/null
  . "$STATE"
  FIRST=
  NEXT=$(other "$ACTIVE")
  ACTIVE_TAG=$(tag_of "$ACTIVE")
  # 지금 받는 쪽과 같은 태그로 빌드하면 그 이미지를 덮어써 롤백할 이미지가 없어진다
  [ "$TAG" != "$ACTIVE_TAG" ] || { echo "이미 떠 있는 버전이다: $TAG" >&2; exit 1; }
else
  # 첫 전환 (J6) — 옛 단일 api(glp1-<env>-api)를 blue 로 바꾼다. 상태 파일이 없을 때만.
  FIRST=1
  ACTIVE='' ACTIVE_TAG='' BLUE_TAG='' GREEN_TAG=''
  NEXT=blue
  if [ "$ENV" = staging ] && ! caddy_is_bg; then
    # 스테이징 사이트는 운영 Caddy 가 받는다. 운영이 먼저 Blue/Green 으로 바뀌어야 한다.
    echo "운영 Caddy 가 아직 옛 구성이다 — 운영 첫 전환(deploy.sh)을 먼저 한다" >&2
    exit 1
  fi
fi
check_state

if [ "$NEXT" = blue ]; then BLUE_TAG=$TAG; else GREEN_TAG=$TAG; fi
BE_TAG=$TAG
export BLUE_TAG GREEN_TAG BE_TAG
echo "배포: $ENV ${ACTIVE:-(첫 전환)} → $NEXT ($TAG)"

# api 와 Caddy 를 뺀 나머지. worker · ai (+ COMPOSE_PROFILES=stub 이면 ai-stub)
mapfile -t OTHERS < <("${COMPOSE[@]}" config --services | grep -vxE 'db|caddy|api-blue|api-green|migrate')
BUILD=("api-$NEXT")
for s in "${OTHERS[@]}"; do [ "$s" = worker ] || BUILD+=("$s"); done

SWITCHED=
# 전환 전에 실패하면 새 쪽을 내리고(api 별칭으로 ai 의 역호출을 받지 않게) 옛 워커를 다시 켠다.
# 워커는 전환 전까지 정지만 하고 다시 만들지 않으므로 그대로 start 하면 옛 이미지다.
on_exit() {
  local rc=$?
  [ "$rc" -ne 0 ] && [ -z "$SWITCHED" ] || return 0
  echo "전환 전에 실패 — $NEXT 를 내리고 옛 워커를 다시 켠다. 사용자는 계속 ${ACTIVE:-옛 api} 를 쓴다" >&2
  "${COMPOSE[@]}" stop "api-$NEXT" || true
  docker start "glp1-$ENV-worker" >/dev/null 2>&1 || true
}
trap on_exit EXIT

# 빌드하는 동안 지금 받는 쪽이 계속 요청을 받는다. 다른 쪽(api-$ACTIVE)은 빌드하지 않는다 —
# 같은 태그로 지금 코드를 덮어쓰게 된다.
"${COMPOSE[@]}" build "${BUILD[@]}"
# 마이그레이션 전에 옛 워커를 내린다 — 행 잠금을 쥐고 있으면 ADD COLUMN 이 막히고, 새 enum 값을
# 옛 코드가 읽으면 터진다 (예: 9a1c4e7b2d3f). 처리 중인 작업은 stop_grace_period(60초) 안에 끝낸다.
"${COMPOSE[@]}" stop worker
# 옛 api 가 계속 요청을 받는 중이다. 옛 코드와 호환되는 마이그레이션만 넣는다 (J4).
"${COMPOSE[@]}" run --rm migrate
# healthy(/health/ready)가 아니면 여기서 실패로 끝난다
"${COMPOSE[@]}" up -d --wait --no-deps "api-$NEXT"
if [ -n "$FIRST" ] && [ "$ENV" = prod ] && ! caddy_exists; then
  # 새 서버(로컬 확인 · 장애 복구) — 받던 쪽이 없으므로 끊길 것도 없다. Caddy 가 있어야
  # 아래에서 Caddy 쪽 확인을 할 수 있으니 먼저 띄운다. 아래 첫 전환 블록은 같은 일을 다시 해도 바뀌는 게 없다.
  write_upstream "$UPSTREAM" "glp1-prod-api-blue"
  "${COMPOSE[@]}" up -d --wait --no-deps caddy
fi
caddy_can_reach "$NEXT"

if [ -n "$FIRST" ] && [ "$ENV" = prod ]; then
  # 옛 Caddy 는 Caddyfile 파일 하나만 마운트하고 upstream 디렉터리가 없다. 한 번만 다시 만든다 —
  # 이 동안 80/443 이 잠깐 끊긴다. 옛 api 는 그때까지 요청을 받는다.
  write_upstream "$UPSTREAM" "glp1-prod-api-blue"
  "${COMPOSE[@]}" up -d --wait --no-deps caddy
else
  switch_to "$NEXT"
fi
SWITCHED=1
write_state "$NEXT"

# 옛 쪽으로 들어간 요청이 끝날 시간
sleep 15
# rm 하지 않는다. 롤백(bg-switch.sh)용으로 남긴다
[ -n "$FIRST" ] || "${COMPOSE[@]}" stop -t 30 "api-$ACTIVE"
# 첫 전환 전의 옛 단일 api. compose 파일에서 빠진 서비스라 compose 로는 내릴 수 없다.
# 첫 전환이 이 앞에서 실패했으면 다음 배포가 지운다. 없으면 아무것도 안 한다.
docker rm -f "glp1-$ENV-api" >/dev/null 2>&1 || true

# 워커 · ai 는 옛 api 를 내린 뒤에 띄운다. 그래야 api 별칭(사진 주소, ai 의 /internal 역호출)이
# 새 쪽 하나만 가리킨다. 워커가 그만큼 더 쉬지만 큐가 DB 라 일감은 기다렸다 처리된다.
# 여기서 실패해도 이미 새 쪽이 받고 있다. 이미지 정리까지 마치고 마지막에 실패로 끝낸다.
OTHERS_RC=0
"${COMPOSE[@]}" up -d --wait --no-deps "${OTHERS[@]}" || OTHERS_RC=$?

# 디스크는 50GB 고정이다. 롤백용 태그 하나와 지금 태그만 남긴다.
prune_be_images
# 아래 둘은 이 프로젝트 것만이 아니라 그 PC 전체를 지운다. 로컬 확인에서는 BG_SKIP_PRUNE=1 로 건너뛴다.
if [ -z "${BG_SKIP_PRUNE:-}" ]; then
  docker image prune -f
  docker builder prune -f --filter until=72h
fi
if [ "$OTHERS_RC" -ne 0 ]; then
  echo "api 는 $NEXT 로 전환했지만 ${OTHERS[*]} 가 healthy 가 아니다 — docker ps 로 확인한다" >&2
  exit "$OTHERS_RC"
fi
echo "완료: $ENV → $NEXT ($TAG)"
