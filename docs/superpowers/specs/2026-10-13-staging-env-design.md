# 스테이징 환경 (EC2 한 대에 운영과 함께) — 설계

날짜: 2026-10-13
브랜치: `infra/feat-staging`
상태: 승인됨 (구현 계획 작성 대기)

## 왜 필요한가

운영 서버는 `main` 커밋만 배포한다(`infra/deploy.sh`). `main` 은 develop → main PR 에서 멘토 리뷰를
거친 뒤에만 바뀌므로, 내가 쓴 코드가 **배포 환경에서** 어떻게 도는지(실제 도메인·인증서·Caddy 규칙·
서버 사양) 머지 전에 볼 방법이 없다. 로컬 운영 compose(`DOMAIN=localhost`)로는 DNS·인증서·서버
자원 문제를 잡지 못한다.

서버는 운영진이 준 EC2 한 대(t3.medium, 4GiB, 50GB)뿐이고 새로 만들 수 없다. 그래서 같은 서버에
`develop` 을 올리는 스테이징 스택을 하나 더 띄운다.

## 핵심 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 스택 분리 | 운영 `docker-compose.prod.yml` 위에 **`docker-compose.staging.yml` 을 덮어쓴다** | 운영 파일 변경이 최소다. 컨테이너·이미지·볼륨 이름이 `glp1-staging` 으로 갈려 운영 DB 볼륨을 건드릴 길이 없다 |
| 80/443 | **운영 Caddy 하나**가 두 도메인을 받아 나눠 보낸다 | 포트는 한 컨테이너만 쓸 수 있다. 보안 그룹은 80·443 그대로, 인증서는 사이트별로 Caddy 가 받는다 |
| Caddy → 스테이징 api | 외부 네트워크 **`glp1-edge`** (운영 caddy 와 스테이징 api 만 붙음) | 스테이징 db·worker·ai 는 스테이징 내부 네트워크에만 있다 |
| upstream 이름 | 서비스 이름 `api` 가 아니라 **컨테이너 이름** (`glp1-prod-api`, `glp1-staging-api`) | 스테이징 api 가 edge 에 `api` 별칭으로 붙는다. 운영 caddy 가 `api` 를 찾으면 두 api 중 아무 쪽이나 걸릴 수 있다 |
| 접근 키 | 운영과 **같은 `DEMO_KEY`** | APK 하나로 `API_BASE_URL` 만 바꿔 양쪽을 본다. 운영 Caddy 가 두 사이트를 같은 규칙으로 검사한다 |
| `STAGING_DOMAIN` | **필수** (`${STAGING_DOMAIN:?}`) | 빈 주소의 사이트 블록은 Caddyfile 파싱 오류다. 선택으로 두려면 Caddyfile 을 분기해야 해서 복잡해진다 |
| 동시 배포 | 두 배포 스크립트가 **같은 `flock` 잠금**을 쓴다. 나중 것은 기다린다 | 빌드가 겹치면 4GiB 에서 스왑으로 밀려 운영이 느려진다. 수동 배포도 막아야 해서 CI concurrency 가 아니라 스크립트에서 막는다 |
| 메모리 | **스테이징에만** `mem_limit` + `oom_score_adj: 500` | `mem_limit` 은 스테이징이 쓸 양만 묶는다. 서버 전체가 모자랄 때 OOM killer 가 운영 Postgres 를 고르지 않게 `oom_score_adj` 로 스테이징을 먼저 죽게 한다 |
| 배포 방식 | 이번에는 **수동** (Session Manager 에서 `deploy-staging.sh`) | CI(`deploy-staging.yml`)는 운영 H2 워크플로를 만들 때 같이 붙인다 |

검토했지만 고르지 않은 것:

- 스테이징 전용 Caddy 를 다른 포트(8443 등)에 띄우기 — 보안 그룹을 더 열어야 하고, Let's Encrypt
  HTTP 검증은 80/443 만 쓰므로 DuckDNS DNS 검증 모듈을 넣은 Caddy 커스텀 빌드가 필요하다. 앱 URL 에 포트도 붙는다.
- 운영 compose 하나를 `${STACK}` 변수로 이름만 바꿔 쓰기 — 운영 파일 전체가 변수에 기대게 된다.
  값이 하나 틀리면 운영 볼륨 이름이 바뀌어 빈 DB 가 새로 생기는 사고가 난다.

## 1. 구성

```
앱 ──https──▶ glp1-demo.duckdns.org    ┐
앱 ──https──▶ glp1-staging.duckdns.org ┤  (DNS 는 둘 다 같은 EC2 공인 IP)
                                        ▼
                     glp1-prod-caddy  (네트워크: glp1-prod_default + glp1-edge)
                       │ 도메인으로 나눔, 규칙은 (gate) snippet 하나
                       ├─▶ glp1-prod-api:8000     (glp1-prod_default)
                       └─▶ glp1-staging-api:8000  (glp1-edge)
                                │
              glp1-staging_default 안에서만: db · worker · ai · ai-stub
```

| 서버 경로 | 브랜치 | 스크립트 | 비밀값 |
|---|---|---|---|
| `/opt/glp1` | `main` | `infra/deploy.sh` | `infra/.env.prod` |
| `/opt/glp1-staging` | `develop` | `infra/deploy-staging.sh` | `infra/.env.staging` |

## 2. 파일 변경

### `infra/docker-compose.staging.yml` (새 파일)

운영 파일 위에 덮어쓴다: `docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml`.

- `name: glp1-staging` — 볼륨·기본 네트워크가 `glp1-staging_*` 로 갈린다
- 서비스마다 `container_name: glp1-staging-<서비스>`, 빌드하는 서비스는 `image: glp1-staging-<be|ai|ai-stub>`.
  이미지 이름을 바꾸지 않으면 스테이징 빌드가 운영 이미지(`glp1-prod-be`)를 develop 코드로 덮어쓴다
- `api` 만 `networks: [default, edge]`
- `caddy: { profiles: [disabled] }` — 뜨지 않는다
- `mem_limit`: api 512m, worker 512m, ai 256m, ai-stub 128m, db 384m (상한 합 약 1.8GiB)
- `oom_score_adj: 500` (db·api·worker·ai·ai-stub): 서버 전체 메모리가 모자랄 때 OOM killer 가 운영 대신 스테이징을 먼저 고른다
- `networks: { edge: { external: true, name: glp1-edge } }`

### `infra/docker-compose.prod.yml` (수정)

- caddy: `networks: [default, edge]`, `environment` 에 `STAGING_DOMAIN: ${STAGING_DOMAIN:?}`
- 맨 아래 `networks: { edge: { external: true, name: glp1-edge } }`
- 서비스 이름·볼륨·컨테이너 이름은 그대로 (운영 데이터 무변경)

### `infra/Caddyfile` (수정)

- 지금의 사이트 본문(gzip, 12MB, internal 404, health, 데모 키, 팀 IP, 403)을 snippet `(gate)` 로 옮기고
  upstream 을 `{args[0]}` 로 받는다
- `{$DOMAIN} { import gate glp1-prod-api:8000 }`
- `{$STAGING_DOMAIN} { import gate glp1-staging-api:8000 }`
- 스테이징 스택이 꺼져 있으면 스테이징 도메인만 502. Caddy 는 요청 때 이름을 찾으므로 기동은 실패하지 않는다

### `infra/deploy.sh` (수정)

- 맨 앞에 `flock` 으로 `/tmp/glp1-deploy.lock` 을 잡는다 (다른 배포가 끝날 때까지 기다림)
- `docker network create glp1-edge` (이미 있으면 넘어감) — 운영 compose 가 외부 네트워크를 요구한다

### `infra/deploy-staging.sh` (새 파일)

`deploy.sh` 와 같은 흐름. 다른 점만:

- `git fetch origin develop`, 기본 대상 `origin/develop`
- `COMPOSE=(docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml --env-file .env.staging)`
- 같은 잠금 파일, 같은 네트워크 생성, 같은 prune (dangling 만 지우므로 다른 스택 이미지는 안전)

### 그 밖

- `infra/.env.prod.example`: `STAGING_DOMAIN=` 과 설명 추가. `.env.staging` 도 이 파일을 복사해 쓴다
  (스테이징에서 `DOMAIN`·`STAGING_DOMAIN` 은 compose 검사만 통과하면 되고 쓰이지 않는다)
- `.gitignore`: `.env.staging` 추가
- `infra/README.md`: "스테이징" 절 추가
- `contracts/Deployment.md` (로컬 전용): 스테이징 절 — 서버 1회 작업, 데모 당일 규칙

## 3. 서버 1회 작업

1. DuckDNS 에 `glp1-staging` 추가. cron 의 `domains=` 를 `glp1-demo,glp1-staging` 으로
2. 운영 `.env.prod` 에 `STAGING_DOMAIN=glp1-staging.duckdns.org` 추가. 첫 `deploy.sh` 전에 네트워크를 손으로 만든다:
   `docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge`
   → `/opt/glp1/infra/deploy.sh` (이 변경이 `main` 에 머지되기 전이면 `deploy.sh origin/develop` 으로 임시 우회)
   첫 배포는 서버에 있던 옛 `deploy.sh` 가 돌 수 있어(네트워크를 만들지 않는다) 먼저 만든다. 실패하면 한 번 더 실행한다 (워커가 내려가 있을 수 있다).
3. `/opt/glp1-staging` 에 clone, `.env.staging` 작성 — `DB_PASSWORD`·`INTERNAL_SERVICE_TOKEN` 은 운영과
   다른 값으로 새로 생성, `DEMO_KEY`·LLM 값은 운영과 같게 → `./deploy-staging.sh`
4. 스테이징 DB(`glp1-staging-db`)에 영양DB 시드 투입

## 4. 운영 규칙

- 운영은 `main`, 스테이징은 `develop` 만 올린다
- 데모 당일에는 스테이징 배포 금지. 필요하면 `docker compose -p glp1-staging stop` 으로 내린다
- 스테이징도 같은 LLM 키로 실제 호출한다. 아끼려면 `.env.staging` 에서 ai-stub 을 켠다
- `docker volume prune` 금지 규칙은 스테이징 볼륨에도 적용

## 5. 검증 (로컬, 이번 작업의 완료 기준)

`DOMAIN=localhost`, `STAGING_DOMAIN=staging.localhost` 로 두 스택을 함께 띄운다
(로컬 개발 Caddy 가 80/443 을 쓰고 있으면 잠시 멈춘다).

- [ ] 양쪽 `/health` → 200
- [ ] 양쪽 모두 키 없이 403 / 키 붙이면 200 / `/internal/*` 404
- [ ] 데이터 분리 — 운영에서 만든 사용자가 스테이징에서 조회되지 않음
- [ ] 스테이징 스택을 내리면 스테이징만 502, 운영은 200 유지
- [ ] 운영 스택만 다시 올려도 스테이징이 계속 응답
- [ ] `docker stats` 로 두 스택 메모리 측정, 결과를 README 에 적음
- [ ] 두 배포 스크립트는 shellcheck 통과

서버에서만 확인하는 것 (`flock` 이 Windows 에 없고, 스크립트가 `git checkout` 을 하므로 로컬에서 돌리지 않는다):

- [ ] 두 배포 스크립트를 동시에 실행하면 하나가 끝난 뒤 다른 하나가 시작
- [ ] 실제 도메인 두 개 모두 유효한 인증서로 `/health` 200

## 범위 밖

- 스테이징 CI 자동 배포 (`deploy-staging.yml`) — 운영 H2 와 함께
- 스테이징 전용 데모 키, PR 별 미리보기 환경
