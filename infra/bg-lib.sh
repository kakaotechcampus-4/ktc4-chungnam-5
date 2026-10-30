# bg-deploy.sh · bg-switch.sh 가 함께 쓰는 Blue/Green 도우미. 직접 실행하지 않고 source 한다.
# source 하기 전에 ENV(prod | staging)를 정하고, infra/ 에서 부른다.
#
# 배포 상태는 git 밖 /opt/glp1-bg 에 둔다 — 운영(/opt/glp1)·스테이징(/opt/glp1-staging)
# checkout 이 따로 있고, git checkout 이 상태를 건드리면 안 된다.
#   <env>.env               ACTIVE=blue|green, BLUE_TAG, GREEN_TAG
#   upstreams/<env>.caddy   지금 받는 쪽 한 줄. 운영 Caddy 가 /etc/caddy-upstreams 로 읽는다

BG_DIR=${GLP1_BG_DIR:-/opt/glp1-bg}
# compose 의 caddy 마운트도 같은 경로를 보게 한다 (로컬 검증에서 경로를 바꿀 때)
export GLP1_BG_DIR=$BG_DIR
# 운영 Caddy 하나가 운영·스테이징 두 사이트를 맡는다
CADDY=glp1-prod-caddy

case "$ENV" in
  prod) COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.prod) ;;
  staging) COMPOSE=(docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging) ;;
  *) echo "환경은 prod 또는 staging 이다: $ENV" >&2; exit 1 ;;
esac
STATE=$BG_DIR/$ENV.env
UPSTREAM=$BG_DIR/upstreams/$ENV.caddy

other() { [ "$1" = blue ] && echo green || echo blue; }

tag_of() { [ "$1" = blue ] && echo "$BLUE_TAG" || echo "$GREEN_TAG"; }

# 임시 파일에 쓰고 mv 로 바꾼다 — 중간에 끊겨도 반쯤 쓴 파일이 남지 않는다.
write_state() { # ACTIVE
  printf 'ACTIVE=%s\nBLUE_TAG=%s\nGREEN_TAG=%s\n' "$1" "$BLUE_TAG" "$GREEN_TAG" >"$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# {args[0]} 은 Caddyfile 사이트 블록이 넘긴 데모 키다. 키는 Caddy 가 풀므로 파일에는 글자 그대로 둔다.
write_upstream() { # 파일 컨테이너
  printf 'import gate %s:8000 {args[0]}\n' "$2" >"$1.tmp"
  mv "$1.tmp" "$1"
}

# Caddyfile 이 두 파일을 다 import 한다. 하나라도 없으면 Caddy 설정 전체가 파싱에 실패한다.
# 아직 첫 전환을 하지 않은 환경은 옛 단일 api(glp1-<env>-api)를 가리킨다 — 운영 첫 전환으로
# 새 Caddy 가 떠도 스테이징은 첫 전환 전까지 옛 api 로 계속 받는다.
ensure_upstreams() {
  mkdir -p "$BG_DIR/upstreams"
  local e
  for e in prod staging; do
    [ -f "$BG_DIR/upstreams/$e.caddy" ] || write_upstream "$BG_DIR/upstreams/$e.caddy" "glp1-$e-api"
  done
}

# 상태 파일의 ACTIVE 와 Caddy 가 실제로 받는 쪽(upstream 파일)이 같은지. 전환(reload)과 상태 기록
# 사이에 끊기면 둘이 어긋나고, 그대로 진행하면 지금 받는 쪽 컨테이너를 다시 만들거나 내린다.
# 첫 전환(ACTIVE 가 빈 값)이면 upstream 은 옛 단일 api 여야 한다 — 다만 지난 첫 전환이 Caddy 를 띄운 뒤
# 확인에서 실패했으면 upstream 은 blue 인데 blue 는 내려가 있다. 받는 사용자가 없으므로 그대로 진행한다.
check_state() {
  local serving
  serving=$(sed -n 's/^import gate glp1-[a-z]*-api-\(blue\|green\):8000 .*/\1/p' "$UPSTREAM")
  [ "$serving" = "$ACTIVE" ] && return 0
  if [ -z "$ACTIVE" ] && [ "$(docker container inspect -f '{{.State.Running}}' "glp1-$ENV-api-$serving" 2>/dev/null)" != true ]; then
    return 0
  fi
  echo "상태 파일은 ACTIVE=${ACTIVE:-(없음)} 인데 Caddy 는 ${serving:-옛 단일 api} 로 받는다 — 지난 배포·롤백이 전환 직후 끊겼다." >&2
  echo "$UPSTREAM 과 docker ps 를 보고 $STATE 를 맞춘 뒤 다시 실행한다" >&2
  return 1
}

caddy_reload() { docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile; }

# Caddy 안에서 그쪽 api 에 직접 닿고 DB 까지 붙는지. 컨테이너 healthcheck 는 자기 안에서만 본다.
caddy_can_reach() { # color
  docker exec "$CADDY" wget -qO /dev/null "http://glp1-$ENV-api-$1:8000/health/ready"
}

# 운영 Caddy 컨테이너가 있는지 (정지 상태 포함). 없으면 새 서버다 — 받던 쪽도 없다.
caddy_exists() { docker container inspect "$CADDY" >/dev/null 2>&1; }

# 운영 Caddy 가 upstream 파일을 읽는 새 구성으로 떠 있는지 (첫 전환 전의 옛 Caddy 는 아니다)
caddy_is_bg() { docker exec "$CADDY" test -d /etc/caddy-upstreams 2>/dev/null; }

# upstream 을 바꾸고 reload. 진행 중인 요청은 옛 설정으로 끝까지 처리된다.
# reload 가 실패하면 Caddy 는 옛 설정으로 계속 돈다. 파일도 되돌린다 — 깨진 파일을 남기면
# 다른 환경의 다음 reload 까지 실패한다.
switch_to() { # color
  cp "$UPSTREAM" "$UPSTREAM.bak"
  write_upstream "$UPSTREAM" "glp1-$ENV-api-$1"
  if ! caddy_reload; then
    mv "$UPSTREAM.bak" "$UPSTREAM"
    echo "Caddy reload 실패 — upstream 을 되돌렸다" >&2
    return 1
  fi
  rm -f "$UPSTREAM.bak"
}

# glp1-<env>-be 중 blue · green 이 쓰는 태그만 남긴다 (롤백할 이미지 하나 + 지금 이미지)
prune_be_images() {
  local tag
  docker images "glp1-$ENV-be" --format '{{.Tag}}' | while read -r tag; do
    [ "$tag" = "$BLUE_TAG" ] || [ "$tag" = "$GREEN_TAG" ] \
      || docker rmi "glp1-$ENV-be:$tag" >/dev/null || true
  done
}
