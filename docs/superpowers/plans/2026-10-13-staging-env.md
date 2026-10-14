# 스테이징 환경 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영 EC2 한 대에 `develop` 을 올리는 스테이징 스택(`glp1-staging`)을 운영과 함께 띄울 수 있게 한다.

**Architecture:** 운영 `docker-compose.prod.yml` 위에 `docker-compose.staging.yml` 을 덮어써 이름·이미지·볼륨을 가른다. 80/443 은 운영 Caddy 하나가 맡아 도메인으로 나누고, 외부 네트워크 `glp1-edge` 로 스테이징 api 에 닿는다. 두 배포 스크립트는 같은 `flock` 잠금으로 한 번에 하나씩 돈다.

**Tech Stack:** Docker Compose v2, Caddy 2 (`caddy:2-alpine`), bash, PostgreSQL 17

**Spec:** `docs/superpowers/specs/2026-10-13-staging-env-design.md`

## Global Constraints

- 브랜치: `infra/feat-staging` (develop 에서 분기). 커밋 메시지: `[INFRA] <type>: <한국어 설명>` + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- 도메인: 운영 `glp1-demo.duckdns.org`, 스테이징 `glp1-staging.duckdns.org`
- 데모 키: 스테이징도 운영과 같은 `DEMO_KEY`
- 이름: 프로젝트 `glp1-staging`, 컨테이너 `glp1-staging-<db|api|worker|ai|ai-stub>`, 이미지 `glp1-staging-<be|ai|ai-stub>`, 외부 네트워크 `glp1-edge`, 잠금 파일 `/tmp/glp1-deploy.lock`
- `mem_limit`(스테이징만): api 512m, worker 512m, ai 256m, ai-stub 128m, db 384m
- 운영 서비스 이름·볼륨 이름·컨테이너 이름은 바꾸지 않는다
- 비밀값은 커밋하지 않는다 (저장소 public). `infra/.env.prod`, `infra/.env.staging` 은 로컬에서만 만든다
- `.sh` 는 LF (`.gitattributes`), 실행 비트 `100755`
- Git Bash 에서 컨테이너 경로(`/etc/...`, `/mnt`)를 넘길 때는 `MSYS_NO_PATHCONV=1` 을 붙인다
- `contracts/Deployment.md` 는 `.git/info/exclude` 대상 — 수정만 하고 커밋하지 않는다

---

### Task 1: `contracts/Deployment.md` 에 스테이징 절 추가 (로컬 문서, 커밋 없음)

**Files:**
- Modify: `contracts/Deployment.md` (전체 순서 다음 줄 37 근처, D 의 cron 157-158, E 절 끝 = `## F.` 바로 위, G 절)

**Interfaces:**
- Produces: 서버 1회 작업 절차. 이후 Task 들이 만드는 파일 이름(`deploy-staging.sh`, `.env.staging`, `docker-compose.staging.yml`)을 그대로 쓴다

- [ ] **Step 1: 전체 순서 설명에 한 줄 추가**

`- H2는 E가 끝나서 서버에 저장소가 있어야 시작할 수 있음` 줄 바로 아래에 추가:

```markdown
- E2(스테이징)는 E가 끝난 뒤 선택. 스테이징 라우팅이 운영 Caddyfile(main)에 들어 있어서, 이 변경이 main에 머지되기 전에는 운영을 `deploy.sh origin/develop`으로 임시 우회해야 함
```

- [ ] **Step 2: D 의 cron 두 줄에서 `domains=glp1-demo` 를 `domains=glp1-demo,glp1-staging` 으로 바꾸고, 코드 블록 바로 아래에 한 줄 추가**

```markdown
- 스테이징 서브도메인(`glp1-staging`)도 같은 토큰으로 한 줄에서 함께 갱신함 (E2)
```

- [ ] **Step 3: `## F. 앱 빌드와 끝까지 실행` 바로 위(`---` 위)에 E2 절 추가**

````markdown
---

## E2. 스테이징 환경 (E가 끝난 뒤, 선택)

> 같은 EC2에 `develop`을 올리는 스택을 하나 더 띄움. 설계: `docs/superpowers/specs/2026-10-13-staging-env-design.md`
> 80/443은 운영 Caddy 하나가 맡고 도메인으로 나눔. 스테이징 api에는 외부 네트워크 `glp1-edge`로 닿음

| 서버 경로 | 브랜치 | 스크립트 | 비밀값 |
| --- | --- | --- | --- |
| `/opt/glp1` | `main` | `infra/deploy.sh` | `infra/.env.prod` |
| `/opt/glp1-staging` | `develop` | `infra/deploy-staging.sh` | `infra/.env.staging` |

1. DuckDNS에 `glp1-staging` 서브도메인 추가, cron의 `domains=`를 `glp1-demo,glp1-staging`으로 (D)
2. 운영 `.env.prod`에 한 줄 추가 후 운영 다시 배포 (운영 Caddy가 스테이징 사이트를 받음)

```bash
echo 'STAGING_DOMAIN=glp1-staging.duckdns.org' >> /opt/glp1/infra/.env.prod
/opt/glp1/infra/deploy.sh            # main 머지 전이면: /opt/glp1/infra/deploy.sh origin/develop
```

3. 스테이징 checkout과 비밀값

```bash
sudo mkdir -p /opt/glp1-staging && sudo chown ubuntu:ubuntu /opt/glp1-staging
git clone https://github.com/kakaotechcampus-4/ktc4-chungnam-5.git /opt/glp1-staging
cd /opt/glp1-staging/infra
cp /opt/glp1/infra/.env.prod .env.staging && chmod 600 .env.staging
# 운영과 달라야 하는 값만 새로 만든다 (DEMO_KEY·LLM 값·도메인은 그대로)
for k in DB_PASSWORD INTERNAL_SERVICE_TOKEN; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env.staging
done
./deploy-staging.sh
```

4. 스테이징 DB에 시드 (처음 한 번만)

```bash
docker exec -i glp1-staging-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner' \
  < seed/food_refs_20260828.dump
```

**검증**

- [ ]  `https://glp1-staging.duckdns.org/health` → 200, 유효한 인증서
- [ ]  헤더 없이 403 / 헤더 붙이면 200 / `/internal/*` 404 (스테이징 도메인)
- [ ]  운영 도메인도 그대로 200 (`/health`)
- [ ]  Session Manager 두 개로 `deploy.sh`와 `deploy-staging.sh`를 동시에 실행 → 나중 것이 앞의 것이 끝날 때까지 기다림
- [ ]  `free -h`, `docker stats --no-stream`으로 두 스택 메모리 확인

**규칙**

- 운영은 `main`, 스테이징은 `develop`만 올림. 다시 배포: `/opt/glp1-staging/infra/deploy-staging.sh` (생략하면 `origin/develop`)
- 스테이징도 같은 LLM 키로 실제 호출함. 아끼려면 `.env.staging`에서 ai-stub 두 줄을 켬
- 앱에서 스테이징을 보려면 `--dart-define=API_BASE_URL=https://glp1-staging.duckdns.org`로 빌드 (키는 같음)
````

- [ ] **Step 4: G 절 목록 끝(`- **이후:**` 줄 위)에 추가**

```markdown
- **스테이징:** 데모 당일에는 스테이징 배포 금지. 필요하면 `docker compose -p glp1-staging stop`으로 내림. 빌드가 운영 응답을 느리게 만들 수 있음. 종료할 때는 스테이징도 `docker compose -p glp1-staging down` (볼륨 prune 금지 규칙은 스테이징 볼륨에도 적용)
```

- [ ] **Step 5: 확인**

Run: `grep -n "E2\|glp1-staging" contracts/Deployment.md`
Expected: 전체 순서 줄, cron 두 줄, E2 절, G 절에서 모두 보임. `git status --short` 에 `contracts/` 가 나오지 않음 (exclude 대상)

---

### Task 2: compose — 스테이징 오버라이드와 운영 Caddy 의 공유 네트워크

**Files:**
- Create: `infra/docker-compose.staging.yml`
- Modify: `infra/docker-compose.prod.yml` (caddy 서비스 148-152 근처 environment, 맨 아래 `volumes:` 뒤)
- Modify: `infra/.env.prod.example` (Caddy 블록)
- Modify: `.gitignore:21-23`

**Interfaces:**
- Produces: 환경변수 `STAGING_DOMAIN` (caddy 에 전달, Task 3 의 Caddyfile 이 씀), 외부 네트워크 `glp1-edge` (Task 4 스크립트가 만듦), 컨테이너 이름 `glp1-prod-api`·`glp1-staging-api` (Task 3 upstream)

- [ ] **Step 1: 실패 확인 — 오버라이드가 아직 없다**

Run: `cd infra && docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.prod config --quiet`
Expected: FAIL (`docker-compose.staging.yml: no such file or directory` 류)

- [ ] **Step 2: `infra/docker-compose.staging.yml` 작성**

```yaml
# 스테이징(develop) 스택. 운영 compose 위에 덮어쓴다 — deploy-staging.sh 가 부른다.
#
#   docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging ...
#
# 같은 EC2 에 운영과 함께 뜬다. 겹치면 안 되는 것만 바꾼다:
#  - 프로젝트 이름 → 볼륨·기본 네트워크가 glp1-staging_* 로 갈린다. 운영 DB 볼륨을 건드릴 길이 없다
#  - 컨테이너 이름·이미지 태그 → 스테이징 빌드가 운영 이미지(glp1-prod-*)를 develop 코드로 덮어쓰지 않는다
#  - caddy → 띄우지 않는다. 80/443 은 운영 Caddy 하나가 맡고 glp1-edge 로 스테이징 api 에 닿는다
#  - mem_limit → 메모리가 모자라면 OOM killer 가 운영 대신 스테이징을 먼저 죽인다 (4GiB 서버)
#
# .env.staging 은 .env.prod 를 복사해 DB_PASSWORD · INTERNAL_SERVICE_TOKEN 만 새로 만든다.
# DOMAIN · STAGING_DOMAIN 은 여기서 쓰이지 않지만 운영 파일의 ${...:?} 검사를 통과하려면 채워 둔다.
name: glp1-staging

services:
  db:
    container_name: glp1-staging-db
    mem_limit: 384m

  api:
    image: glp1-staging-be
    container_name: glp1-staging-api
    # 운영 Caddy 가 컨테이너 이름(glp1-staging-api)으로 찾아온다
    networks: [default, edge]
    mem_limit: 512m

  worker:
    image: glp1-staging-be
    container_name: glp1-staging-worker
    mem_limit: 512m

  ai:
    image: glp1-staging-ai
    container_name: glp1-staging-ai
    mem_limit: 256m

  ai-stub:
    image: glp1-staging-ai-stub
    container_name: glp1-staging-ai-stub
    mem_limit: 128m

  caddy:
    profiles: [disabled]

networks:
  edge:
    external: true
    name: glp1-edge
```

- [ ] **Step 3: `infra/docker-compose.prod.yml` 의 caddy 수정**

`ALLOWED_IPS: ${ALLOWED_IPS:-127.0.0.1/32}` 줄 바로 아래에 추가:

```yaml
      # 스테이징 사이트 주소(Caddyfile 의 두 번째 사이트). 비면 Caddyfile 파싱이 실패하므로 필수다.
      STAGING_DOMAIN: ${STAGING_DOMAIN:?}
```

caddy 의 `volumes:` 블록 바로 위에 추가:

```yaml
    networks:
      - default
      # 스테이징 api(glp1-staging-api)에 닿는 통로. 운영 api 는 여기 붙지 않는다.
      - edge
```

파일 맨 끝(`caddy_config:` 다음)에 추가:

```yaml

networks:
  # 운영 caddy 와 스테이징 api 만 붙는 외부 네트워크. compose 가 만들지 않으므로
  # deploy.sh · deploy-staging.sh 가 먼저 만든다 (docker network create glp1-edge).
  edge:
    external: true
    name: glp1-edge
```

- [ ] **Step 4: `infra/.env.prod.example` 수정**

`ALLOWED_IPS=` 줄 바로 아래에 추가:

```bash
# 스테이징(develop) 도메인. 운영 Caddy 가 이 주소로 온 요청을 스테이징 api 로 보낸다. 필수.
# 예: glp1-staging.duckdns.org (로컬 검증은 staging.localhost)
STAGING_DOMAIN=
```

- [ ] **Step 5: `.gitignore` 수정**

`!.env.prod.example` 줄 바로 아래에 추가:

```gitignore
# 스테이징 서버 비밀값(infra/.env.staging). 위 .env.* 에도 걸리지만 명시해 둔다
.env.staging
```

- [ ] **Step 6: 로컬 비밀값 파일 준비 (커밋 안 됨)**

```bash
cd infra
grep -q '^STAGING_DOMAIN=' .env.prod || echo 'STAGING_DOMAIN=staging.localhost' >> .env.prod
cp .env.prod .env.staging
sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=staging-test-pw|; s|^INTERNAL_SERVICE_TOKEN=.*|INTERNAL_SERVICE_TOKEN=staging-test-token|" .env.staging
git status --short   # .env.prod · .env.staging 이 나오면 안 됨
```

- [ ] **Step 7: 통과 확인 — 병합 결과 검사**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet && echo PROD_OK
docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging config > /tmp/staging.yml && echo STAGING_OK
grep -E '^name:|container_name:|image: glp1|mem_limit' /tmp/staging.yml
docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging config --services
```

Expected: `PROD_OK`, `STAGING_OK`. `name: glp1-staging`, 컨테이너·이미지 이름이 모두 `glp1-staging-*`(`glp1-prod-` 가 하나도 없음), `mem_limit` 5개(바이트로 표시됨). `--services` 출력에 `caddy`·`ai-stub` 이 없음 (profile)

추가 확인: `docker compose -f docker-compose.prod.yml --env-file .env.prod config | grep -n 'glp1-edge'` → caddy 네트워크와 맨 아래 선언에서 보임

- [ ] **Step 8: Commit**

```bash
git add infra/docker-compose.staging.yml infra/docker-compose.prod.yml infra/.env.prod.example .gitignore
git commit -F - <<'EOF'
[INFRA] feat: 같은 EC2 에 스테이징 스택을 띄우는 compose 오버라이드를 추가한다

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: Caddyfile — 두 사이트가 같은 규칙(snippet)을 쓰게

**Files:**
- Modify: `infra/Caddyfile` (전체 사이트 블록 10-41)

**Interfaces:**
- Consumes: `STAGING_DOMAIN` 환경변수, 컨테이너 이름 `glp1-prod-api`·`glp1-staging-api` (Task 2)

- [ ] **Step 1: 실패 확인 — 지금 Caddyfile 에는 스테이징 사이트가 없다**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD/infra/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -e DOMAIN=localhost -e STAGING_DOMAIN=staging.localhost -e DEMO_KEY=x -e ALLOWED_IPS=127.0.0.1/32 \
  caddy:2-alpine caddy adapt --config /etc/caddy/Caddyfile | grep -c staging.localhost
```

Expected: `0`

- [ ] **Step 2: Caddyfile 의 `{$DOMAIN} { ... }` 블록 전체(10행부터 끝까지)를 아래로 교체** (머리 주석 1-8행은 그대로)

```caddy
# 운영(main)·스테이징(develop) 두 사이트가 같은 규칙을 쓴다. upstream 만 다르다 ({args[0]}).
# upstream 은 서비스 이름(api)이 아니라 컨테이너 이름이다 — 스테이징 api 도 glp1-edge 에 `api`
# 별칭으로 붙어서, 서비스 이름으로 찾으면 두 api 중 아무 쪽이나 걸릴 수 있다.
(gate) {
	encode gzip

	# BE 제한(10MB, meals.py 의 _MAX_IMAGE_BYTES)보다 약간 크게 둔다. 같거나 작으면
	# Caddy 가 먼저 끊어서(본문 없는 413) BE 가 보내는 422 VALIDATION_ERROR(크기 안내 메시지)가
	# 앱까지 가지 않는다.
	request_body {
		max_size 12MB
	}

	# route 안에서는 적힌 순서대로 적용된다. 먼저 응답한 줄에서 끝난다.
	route {
		# 1. AI → BE tool 역호출 경로. compose 내부 네트워크(api:8000)로만 닿는다.
		@internal path /internal /internal/*
		respond @internal 404

		# 2. 상태 확인은 키 없이 통과
		reverse_proxy /health {args[0]}

		# 3. 데모 키가 맞거나 팀원 IP 이면 통과
		@demo header X-Demo-Key {$DEMO_KEY}
		reverse_proxy @demo {args[0]}

		# 팀원 IP — 경로 제한 없이 전체 통과 (위 머리 주석 참고)
		@team remote_ip {$ALLOWED_IPS}
		reverse_proxy @team {args[0]}

		# 4. 나머지
		respond 403
	}
}

{$DOMAIN} {
	import gate glp1-prod-api:8000
}

# 스테이징 — 스택이 꺼져 있으면 이 도메인만 502 이고 운영에는 영향이 없다.
# Caddy 는 요청 때 이름을 찾으므로 스테이징이 없어도 기동은 실패하지 않는다.
{$STAGING_DOMAIN} {
	import gate glp1-staging-api:8000
}
```

- [ ] **Step 3: 통과 확인 — 파싱과 두 upstream**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD/infra/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -e DOMAIN=localhost -e STAGING_DOMAIN=staging.localhost -e DEMO_KEY=x -e ALLOWED_IPS=127.0.0.1/32 \
  caddy:2-alpine sh -c 'caddy validate --config /etc/caddy/Caddyfile && caddy adapt --config /etc/caddy/Caddyfile' \
  | grep -oE '"(dial|host)":[^]}]*' | sort | uniq -c
```

Expected: `Valid configuration` 출력. `dial` 에 `glp1-prod-api:8000` 과 `glp1-staging-api:8000` 이 각각 3번(health·demo·team), `host` 에 `localhost` 와 `staging.localhost`. `{args[0]}` 가 문자 그대로 남아 있으면 FAIL

- [ ] **Step 4: Commit**

```bash
git add infra/Caddyfile
git commit -F - <<'EOF'
[INFRA] feat: 운영 Caddy 가 스테이징 도메인을 스테이징 api 로 보낸다

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: 배포 스크립트 — 잠금·네트워크와 `deploy-staging.sh`

**Files:**
- Modify: `infra/deploy.sh` (`cd "$(dirname "$0")/.."` 줄 바로 아래)
- Create: `infra/deploy-staging.sh` (실행 비트)

**Interfaces:**
- Consumes: `docker-compose.staging.yml`, 네트워크 이름 `glp1-edge` (Task 2)
- Produces: `infra/deploy-staging.sh [SHA]` (기본 `origin/develop`), 잠금 `/tmp/glp1-deploy.lock`

- [ ] **Step 1: `infra/deploy.sh` 에 잠금·네트워크 추가** — `cd "$(dirname "$0")/.."` 바로 아래, `git fetch` 위에:

```bash
# 운영·스테이징 배포가 겹치지 않게 한 번에 하나씩 돈다 (deploy-staging.sh 와 같은 잠금).
# 빌드가 겹치면 4GiB 서버가 스왑으로 밀려 운영 응답이 느려진다. 앞 배포가 끝날 때까지 기다린다.
exec 9>/tmp/glp1-deploy.lock
flock 9
# 운영 Caddy 가 스테이징 api 에 닿는 외부 네트워크. compose 가 만들지 않으므로 먼저 있어야 한다.
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge >/dev/null
```

- [ ] **Step 2: `infra/deploy-staging.sh` 작성**

```bash
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
```

- [ ] **Step 3: 실행 비트와 줄바꿈**

```bash
git add infra/deploy-staging.sh
git update-index --chmod=+x infra/deploy-staging.sh
git ls-files -s infra/deploy.sh infra/deploy-staging.sh   # 둘 다 100755
file infra/deploy-staging.sh infra/deploy.sh               # CRLF 가 없어야 함
```

- [ ] **Step 4: 정적 검사**

```bash
bash -n infra/deploy.sh && bash -n infra/deploy-staging.sh && echo SYNTAX_OK
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD/infra:/mnt:ro" koalaman/shellcheck:stable /mnt/deploy.sh /mnt/deploy-staging.sh && echo SHELLCHECK_OK
```

Expected: `SYNTAX_OK`, `SHELLCHECK_OK` (경고 없음)

- [ ] **Step 5: 두 스크립트의 compose 이후 부분이 COMPOSE 줄만 다른지 확인**

Run: `diff <(sed -n '/^cd infra/,$p' infra/deploy.sh) <(sed -n '/^cd infra/,$p' infra/deploy-staging.sh)`
Expected: `COMPOSE=` 줄과 주석 줄 차이만 나옴 (명령 줄은 같음)

- [ ] **Step 6: Commit**

```bash
git add infra/deploy.sh infra/deploy-staging.sh
git commit -F - <<'EOF'
[INFRA] feat: 스테이징 배포 스크립트를 추가하고 운영 배포와 겹치지 않게 잠근다

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: 로컬 통합 검증 + `infra/README.md`

**Files:**
- Modify: `infra/README.md` ("운영 배포" 표 112-117, "로컬에서 운영 구성 확인" 절 216-, 그 앞에 "스테이징" 절 추가)

**Interfaces:**
- Consumes: Task 2-4 전부, 로컬 `infra/.env.prod`(`STAGING_DOMAIN=staging.localhost`)·`infra/.env.staging` (Task 2 Step 6)

- [ ] **Step 1: 두 스택 띄우기** (로컬에 떠 있는 옛 이름 `glp1-caddy` 등은 같은 프로젝트 `glp1-prod` 라 새 이름으로 다시 만들어진다)

```bash
cd infra
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge
P=(docker compose -f docker-compose.prod.yml --env-file .env.prod)
S=(docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging)
"${P[@]}" up -d --build --wait
"${S[@]}" up -d --build --wait
docker ps --format '{{.Names}}\t{{.Status}}' | grep glp1
```

Expected: `glp1-prod-*` 5개, `glp1-staging-*` 4개(db·api·worker·ai) 모두 healthy. `glp1-staging-caddy` 없음

- [ ] **Step 2: 접근 규칙 (양쪽)** — `KEY` 는 `.env.prod` 의 `DEMO_KEY`

```bash
KEY=$(grep '^DEMO_KEY=' .env.prod | cut -d= -f2-)
for h in localhost staging.localhost; do
  echo "== $h"
  curl -sk -o /dev/null -w 'health %{http_code}\n' https://$h/health
  curl -sk -o /dev/null -w 'nokey %{http_code}\n'  https://$h/docs
  curl -sk -o /dev/null -w 'key %{http_code}\n'    -H "X-Demo-Key: $KEY" https://$h/docs
  curl -sk -o /dev/null -w 'internal %{http_code}\n' -H "X-Demo-Key: $KEY" https://$h/internal/v1/foods
done
```

Expected: 양쪽 모두 `health 200`, `nokey 403`, `key 200`, `internal 404`

- [ ] **Step 3: 라우팅이 맞는 api 로 가는지**

```bash
curl -sk -o /dev/null -H "X-Demo-Key: $KEY" "https://staging.localhost/docs?route=staging-probe"
curl -sk -o /dev/null -H "X-Demo-Key: $KEY" "https://localhost/docs?route=prod-probe"
docker logs glp1-staging-api 2>&1 | grep -c staging-probe   # 1 이상
docker logs glp1-staging-api 2>&1 | grep -c prod-probe      # 0
docker logs glp1-prod-api 2>&1 | grep -c prod-probe         # 1 이상
docker logs glp1-prod-api 2>&1 | grep -c staging-probe      # 0
```

- [ ] **Step 4: DB 분리**

```bash
docker exec glp1-prod-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "create table _sep_probe(x int)"'
docker exec glp1-staging-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select to_regclass('"'"'_sep_probe'"'"')"'   # 빈 줄
docker exec glp1-prod-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "drop table _sep_probe"'
docker exec glp1-staging-api python -c "import socket; print(socket.gethostbyname('db'))"
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' glp1-staging-db   # 위 IP 와 같음
docker volume ls --format '{{.Name}}' | grep -E 'glp1-(prod|staging)_db-data'                     # 두 개
```

- [ ] **Step 5: 장애 격리**

```bash
"${S[@]}" stop api
curl -sk -o /dev/null -w 'staging %{http_code}\n' https://staging.localhost/health   # 502
curl -sk -o /dev/null -w 'prod %{http_code}\n'    https://localhost/health           # 200
"${S[@]}" up -d --wait
"${P[@]}" up -d --force-recreate --wait caddy api
curl -sk -o /dev/null -w 'staging %{http_code}\n' https://staging.localhost/health   # 200
```

- [ ] **Step 6: 메모리 측정**

Run: `docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | grep glp1`
Expected: 결과를 기록해 Step 7 README 에 적는다. 스테이징 각 컨테이너가 `mem_limit` 값(예: `/ 512MiB`)을 분모로 보여야 함

- [ ] **Step 7: `infra/README.md` 수정**

(a) "운영 배포" 파일 표의 `deploy.sh` 행 아래에 두 행 추가:

```markdown
| `docker-compose.staging.yml` | 스테이징(develop) 스택. 운영 파일 위에 덮어써 이름·이미지·볼륨을 `glp1-staging` 으로 가른다 |
| `deploy-staging.sh` | 스테이징 배포 스크립트. `deploy.sh` 와 같은 잠금을 써서 둘이 동시에 돌지 않는다 |
```

(b) "### 로컬에서 운영 구성 확인" 바로 위에 절 추가 (`<측정값>` 은 Step 6 결과로 채운다 — 컨테이너별 값, 한 줄씩):

````markdown
### 스테이징

같은 EC2 에 `develop` 을 올리는 스택을 하나 더 띄운다. 머지 전에 배포 환경(도메인·인증서·Caddy·서버 자원)에서
돌려 보려는 것이다. 설계: `docs/superpowers/specs/2026-10-13-staging-env-design.md`

```
glp1-demo.duckdns.org    ┐                     ┌─▶ glp1-prod-api     (main,    /opt/glp1)
                         ├─▶ glp1-prod-caddy ──┤
glp1-staging.duckdns.org ┘   (80/443 하나)     └─▶ glp1-staging-api  (develop, /opt/glp1-staging)
                                                    glp1-edge 네트워크
```

- 80/443 은 운영 Caddy 하나가 맡고 도메인으로 나눈다. 스테이징 api 에는 외부 네트워크 `glp1-edge` 로 닿는다
- DB·사진·큐는 볼륨이 따로라(`glp1-staging_*`) 운영 데이터와 섞이지 않는다. 데모 키는 운영과 같다
- 스테이징 컨테이너에는 `mem_limit` 이 있다. 메모리가 모자라면 운영 대신 스테이징이 먼저 죽는다
- `deploy.sh` 와 `deploy-staging.sh` 는 같은 잠금(`/tmp/glp1-deploy.lock`)을 써서 한 번에 하나씩 돈다
- 운영 `.env.prod` 에 `STAGING_DOMAIN` 이 **반드시** 있어야 한다 (비면 운영 배포가 기동 전에 멈춘다)

```bash
/opt/glp1-staging/infra/deploy-staging.sh            # origin/develop
/opt/glp1-staging/infra/deploy-staging.sh <커밋 SHA>
docker compose -p glp1-staging stop                  # 데모 당일 등 잠시 내릴 때
```

처음 띄우는 절차(서브도메인·`.env.staging`·시드)는 `contracts/Deployment.md` E2 절을 따른다.

로컬 측정(유휴 상태, Docker Desktop):

<측정값>
````

(c) "### 로컬에서 운영 구성 확인" 절의 코드 블록을 아래로 교체:

````markdown
```bash
cd infra
cp .env.prod.example .env.prod      # DOMAIN=localhost, STAGING_DOMAIN=staging.localhost, 나머지는 아무 시험 값
docker network create glp1-edge     # 운영 compose 가 요구하는 외부 네트워크 (배포 스크립트는 알아서 만든다)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build --wait

curl -k https://localhost/health                                    # 200
curl -k https://localhost/docs                                      # 403
curl -k -H "X-Demo-Key: <값>" https://localhost/docs                # 200
curl -k -H "X-Demo-Key: <값>" https://localhost/internal/v1/foods   # 404
docker compose -f docker-compose.prod.yml ps                        # 5개 모두 healthy

# 스테이징까지 함께: .env.prod 를 복사해 DB_PASSWORD·INTERNAL_SERVICE_TOKEN 만 바꾼다
cp .env.prod .env.staging
docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging up -d --build --wait
curl -k https://staging.localhost/health                            # 200

docker compose -p glp1-staging down
docker compose -f docker-compose.prod.yml down    # -v 를 붙이면 DB·사진·인증서 볼륨까지 지운다
```
````

- [ ] **Step 8: 로컬 스택 정리** (볼륨은 남긴다)

```bash
docker compose -p glp1-staging down
"${P[@]}" down
```

- [ ] **Step 9: Commit**

```bash
git add infra/README.md
git commit -F - <<'EOF'
[INFRA] docs: 스테이징 운용 방법과 로컬 측정 메모리를 README 에 적는다

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: push 와 PR (사용자 확인 후)

- [ ] **Step 1:** 사용자에게 push·PR(→ `develop`) 여부를 묻는다. 승인 시:

```bash
git push -u origin infra/feat-staging
```

PR 본문: 요약(스펙 링크), 변경 파일, 로컬 검증 결과(Task 5 출력), 서버에서 할 일(E2), 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. `gh` 가 없으므로 PR 은 GitHub 웹에서 만든다 (compare 링크 제공)
