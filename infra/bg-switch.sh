#!/usr/bin/env bash
# Blue/Green 롤백 (contracts/Deployment.md J5). 정지해 둔 옛 쪽을 다시 띄우고 Caddy 를 그쪽으로
# 돌린다. 다시 빌드하지 않으므로 몇 초 안에 끝난다. 다시 부르면 그 반대로 돌아간다.
#
# 사용: infra/bg-switch.sh prod|staging
#
# DB 는 되돌리지 않는다. J4(Expand / Contract)를 지킨 마이그레이션이면 옛 코드가 새 스키마에서도
# 돈다. 지키지 않았으면 이 방법이 안 된다 — Deployment.md G절(downgrade 후 이전 버전 배포)을 따른다.
set -euo pipefail
cd "$(dirname "$0")"
ENV=${1:?prod 또는 staging}
# deploy.sh 와 같은 잠금. 배포 중이면 끝날 때까지 기다린다.
exec 9>/tmp/glp1-deploy.lock
flock 9
. ./bg-lib.sh

[ -f "$STATE" ] || { echo "상태 파일이 없다: $STATE" >&2; exit 1; }
# shellcheck source=/dev/null
. "$STATE"
TARGET=$(other "$ACTIVE")
TARGET_TAG=$(tag_of "$TARGET")
[ -n "$TARGET_TAG" ] || { echo "되돌릴 쪽이 없다 (첫 전환 직후)" >&2; exit 1; }
docker image inspect "glp1-$ENV-be:$TARGET_TAG" >/dev/null 2>&1 || {
  echo "이미지가 없다: glp1-$ENV-be:$TARGET_TAG — 지워졌으면 deploy.sh <이전 버전> 으로 다시 배포한다" >&2
  exit 1
}
export BLUE_TAG GREEN_TAG BE_TAG=$TARGET_TAG
echo "롤백: $ENV $ACTIVE → $TARGET ($TARGET_TAG)"

"${COMPOSE[@]}" up -d --wait --no-deps "api-$TARGET"
caddy_can_reach "$TARGET"
switch_to "$TARGET"
write_state "$TARGET"

# 워커도 같은 버전으로 맞춘다
"${COMPOSE[@]}" stop worker
"${COMPOSE[@]}" up -d --wait --no-deps worker
sleep 15
"${COMPOSE[@]}" stop -t 30 "api-$ACTIVE"
echo "완료: $ENV → $TARGET ($TARGET_TAG)"
