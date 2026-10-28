# infra — 로컬 개발 환경 · 운영 배포

> 운영(EC2) 배포는 [아래 "운영 배포"](#운영-배포) 절. 위쪽은 전부 로컬 개발 얘기다.

로컬 개발 컨테이너는 전부 같은 compose 프로젝트(`infra`)에 속한다. 그래서 한 네트워크(`infra_default`)에
붙고 **서비스 이름으로 서로를 부를 수 있다.**

| 파일 | 서비스 | 컨테이너 | 호스트 포트 |
|---|---|---|---|
| `docker-compose.yml` | `db` | `glp1-db` | 5433 |
| `docker-compose.ai-stub.yml` | `ai-stub` | `glp1-ai-stub` | 8001 |
| `docker-compose.be.yml` | `api` · `worker` | `glp1-api` · `glp1-worker` | 8000 (api) |

---

## 전부 띄우기

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.ai-stub.yml `
  -f docker-compose.be.yml `
  up -d
```

파일을 하나씩 띄워도 동작한다(같은 프로젝트라 네트워크를 공유한다). 다만 그때는
compose 가 나머지 컨테이너를 모르는 것으로 보고 **`Found orphan containers` 경고**를 낸다.
`-f` 를 모아 주면 사라진다. `--remove-orphans` 는 **쓰지 말 것.** `-f` 에 빠진 컨테이너를
지워 버린다.

내리기:

```powershell
docker compose -f docker-compose.yml -f docker-compose.ai-stub.yml -f docker-compose.be.yml down
```

---

## 주소가 두 벌인 이유

**컨테이너 안에서 `localhost` 는 자기 자신이다.** 그래서 같은 서비스를 부르는 주소가
어디서 부르느냐에 따라 다르다.

| 대상 | 호스트(venv 실행)에서 | 컨테이너 안에서 |
|---|---|---|
| ai-stub | `http://localhost:8001` | `http://ai-stub:8000` |
| db | `localhost:5433` | `db:5432` |

호스트 쪽 값은 `backend/.env` 에, 컨테이너 쪽 값은 `docker-compose.be.yml` 의
`environment` 에 있다. 환경변수가 `.env` 보다 우선하므로 컨테이너에서는 compose 값이 이긴다.

포트도 두 벌이다. `ai-stub` 은 컨테이너 안에서 8000 을 듣고 호스트 8001 로 매핑된다(`"8001:8000"`).
BE API 가 호스트 8000 을 쓰기 때문에 겹치지 않게 옮긴 것이다.

---

## 왕복 확인

전제는 ai-stub 뿐이다(`docker compose -f docker-compose.ai-stub.yml up -d` 또는 로컬
uvicorn). 큐는 PostgreSQL 의 `task_queue` 테이블이라 따로 띄울 컨테이너가 없다.

```powershell
cd ..\backend
.\.venv\Scripts\Activate.ps1
python -m scripts.smoke_queue_ai
```

정상 경로 1회와 실패 경로(`ERROR_500` → 3회 재시도 → `FAILED` 격리)를 돌린다.
이 스크립트는 호스트에서 도므로 `backend\.env` 의 `localhost` 주소를 쓴다.

컨테이너 워커가 처리하는 것을 보려면 작업만 넣고 로그를 본다:

```powershell
docker logs -f glp1-worker
```

---

## 큐 상태 보기

**"처리 중" 이라는 상태 컬럼은 없다.** 워커는 행 잠금을 쥐고 있을 뿐이고, 그건 커밋
전이라 다른 세션에 보이지 않는다. 대신 `task_queue` 를 만지며 트랜잭션을 연 채인
커넥션 수로 근사한다 — 워커가 아닌 세션도 잡힐 수 있는 근사치다.

```powershell
cd ../backend
.\.venv\Scripts\Activate.ps1
python -m scripts.queue_status
```

```
상태         건수  가장 오래된 것
------------------------------------------------
DONE            5  2026-09-20 10:12:03+00:00
PENDING          1  2026-09-20 10:15:40+00:00

처리 중(작업 트랜잭션을 연 커넥션): 1
  (근사치 — task_queue 를 만지며 트랜잭션을 연 커넥션을 센다. 워커가 아닌 세션도 잡힐 수 있다)
  pid=1234 경과=0:00:03.128
```

`FAILED` 가 있으면 목록과 함께 다시 넣는 SQL 을 같이 출력해 준다.

---

## 운영 배포

팀 EC2 1대에 컨테이너 5개를 띄운다. 외부에 열린 포트는 Caddy 의 80·443 뿐이다.
서버 접속은 SSH 가 아니라 **SSM Session Manager** 로 한다.

| 파일 | 역할 |
|---|---|
| `docker-compose.prod.yml` | `db` · `api-blue` · `api-green` · `worker` · `ai` · `caddy` (+ 일회성 `migrate`, 데모용 `ai-stub`). compose 프로젝트 이름은 `glp1-prod` |
| `caddy/Caddyfile` | HTTPS(Let's Encrypt) + 데모 키 검사. 디렉터리째 마운트한다 |
| `.env.prod.example` | 서버의 `.env.prod` 에 채울 키 목록. 비밀값은 **서버의 `.env.prod` 에만** 둔다 |
| `deploy.sh` | 배포 스크립트. 사람과 CI 가 같은 스크립트를 쓴다. 잠금 · checkout 뒤 `bg-deploy.sh` 를 부른다 |
| `bg-deploy.sh` · `bg-lib.sh` | Blue/Green 무중단 배포 로직 (운영 · 스테이징 공통) |
| `bg-switch.sh` | 롤백. 정지해 둔 옛 쪽으로 되돌린다 |
| `docker-compose.staging.yml` | 스테이징(develop) 스택. 운영 파일 위에 덮어써 이름·이미지·볼륨을 `glp1-staging` 으로 가른다 |
| `deploy-staging.sh` | 스테이징 배포 스크립트. `deploy.sh` 와 같은 잠금을 써서 둘이 동시에 돌지 않는다 |

```
                         ┌─▶ api-blue  ─┐
인터넷 ──80/443──▶ caddy ┤  (한쪽만 받음) ├──▶ db:5432
                         └─▶ api-green ─┘
                         api(별칭) ▲   │
                       /internal  │   ▼ (큐: task_queue 테이블)
                                  ai ◀── worker
```

### 접근 규칙 (Caddyfile, 위에서부터)

1. `/internal/*` → 404. AI 가 BE 를 다시 부르는 경로라 compose 내부 네트워크로만 닿는다
2. `/health` → 통과
3. `X-Demo-Key` 헤더가 `DEMO_KEY` 와 같거나, 접속 IP 가 `ALLOWED_IPS` 안이면 → api.
   팀원 IP 는 경로 제한 없이 **전체 API** 를 키 없이 통과한다 — Swagger 의 "Try it out" 요청에는
   키 헤더가 붙지 않아 `/docs` 만 열면 쓸 수 없다. 그래서 공용망 IP 는 넣지 않는다
4. 나머지 → 403

업로드 제한은 12MB 로 BE(10MB)보다 약간 크다. BE 가 내는 422 `VALIDATION_ERROR`(크기 안내 메시지)가
앱까지 가게 하려는 것이다. 12MB 를 넘으면 Caddy 가 본문 없는 413 으로 끊는다.

### 첫 배포 (Session Manager 에서)

```bash
sudo mkdir -p /opt/glp1 && sudo chown ubuntu:ubuntu /opt/glp1
git clone https://github.com/kakaotechcampus-4/ktc4-chungnam-5.git /opt/glp1 && cd /opt/glp1/infra
cp .env.prod.example .env.prod && vi .env.prod   # 비밀값은 openssl rand -hex 32 로 만든다
chmod 600 .env.prod
./deploy.sh
docker compose -f docker-compose.prod.yml logs -f caddy   # 인증서 발급 로그 확인
```

스키마는 배포마다 `migrate`(일회성 `alembic upgrade head`)가 새 api 를 띄우기 전에 맞춘다. 공공 영양DB 시드는
**처음 한 번만** 넣는다 (336,351건):

```bash
docker exec -i glp1-prod-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner' \
  < seed/food_refs_20260828.dump
```

### 다시 배포 (Blue/Green)

버전 태그(`v2026.10.23`)를 push 하면 GitHub Actions 가 `ssm send-command` 로 `deploy.sh` 를 부른다.
급할 때는 Session Manager 에서 직접 실행한다.

```bash
/opt/glp1/infra/deploy.sh                # origin/main
/opt/glp1/infra/deploy.sh v2026.10.23    # 버전 태그 또는 main 의 특정 커밋 SHA
```

`main` 에 있는 커밋만 배포할 수 있다. 리뷰를 거친 코드만 서버에 올리려는 규칙이다 — 핫픽스도
`main` 에 머지한 뒤 배포한다.

api 는 `api-blue` · `api-green` 두 벌이고, 한쪽만 요청을 받는다. `bg-deploy.sh` 의 흐름:

1. 받지 않는 쪽 이미지를 빌드한다 (`glp1-prod-be:<버전>`, 태그 없는 배포는 SHA 앞 7자). 그동안 지금 쪽이 계속 받는다
2. 워커를 내리고 `migrate` 로 마이그레이션 — **옛 api 가 계속 받는 중이라 옛 코드와 호환되는 마이그레이션만** 넣는다 (Expand / Contract)
3. 새 쪽을 띄우고 `/health/ready`(DB 까지 확인)로 healthy 를 기다린 뒤, Caddy 안에서도 닿는지 본다
4. upstream 파일을 새 쪽으로 바꾸고 `caddy reload` — Caddy 는 다시 만들지 않으므로 끊기지 않는다
5. 옛 요청이 끝나길 15초 기다린 뒤 옛 쪽 api 를 **정지만** 해 둔다 (롤백용)
6. worker · ai 를 새 이미지로 올린다. 옛 쪽을 정지한 뒤라 내부 별칭 `api`(사진 주소, ai 의 `/internal` 역호출)가
   새 쪽 하나만 가리킨다

1~3 중 하나라도 실패하면 전환하지 않고 끝난다. 사용자는 계속 옛 쪽을 쓰고, 워커도 옛 이미지로 다시 켜진다.
지금 받는 쪽과 같은 버전을 다시 배포하면 빌드 전에 막는다 — 같은 태그로 빌드하면 롤백할 이미지가 덮어써진다.

배포 상태는 git 밖 `/opt/glp1-bg` 에 있다:

```bash
cat /opt/glp1-bg/prod.env               # ACTIVE=green, BLUE_TAG=v2026.10.22, GREEN_TAG=v2026.10.23
cat /opt/glp1-bg/upstreams/prod.caddy   # import gate glp1-prod-api-green:8000 {args[0]}
```

**롤백 = `bg-switch.sh`.** 정지해 둔 옛 쪽을 다시 켜고 Caddy 를 그쪽으로 돌린다. 다시 빌드하지 않는다.
트래픽 전환은 20초 안팎, 워커 교체까지 1분쯤 걸린다. 한 번 더 부르면 다시 새 쪽으로 간다.

```bash
/opt/glp1/infra/bg-switch.sh prod
curl -s https://glp1-demo.duckdns.org/health    # version 이 이전 버전이면 완료
```

ai 도 되돌리지 않는다. 롤백 뒤에는 **새 ai 가 옛 api 의 내부 API(`/internal/v1`)를 부른다.** 그래서 내부 API 도
마이그레이션과 같은 규칙(Expand / Contract)을 따른다 — 엔드포인트·필드는 추가만 하고, 삭제나 의미 변경은
ai 가 더 이상 쓰지 않게 된 다음 배포에서 한다.

DB 는 되돌리지 않는다. 옛 코드와 호환되지 않는 마이그레이션이 들어갔다면 `bg-switch.sh` 로는 안 된다.
그때는 새 이미지가 떠 있는 동안 먼저 DB 를 내린 뒤 이전 버전으로 다시 배포한다
(이전 버전 이미지는 이미 지워졌을 수 있어 다시 빌드한다).

```bash
docker exec glp1-prod-api-<지금 쪽> alembic downgrade <이전 버전의 head 리비전>
/opt/glp1/infra/deploy.sh <이전 버전 태그>
```

손으로 배포할 때도 `up` 을 직접 부르지 말고 `deploy.sh` 를 쓴다. compose 를 직접 부르면 이미지 태그가
비어(`:none`) api · worker 이미지를 찾지 못한다.

### 운영 중 자주 쓰는 것

```bash
cd /opt/glp1/infra
docker compose -f docker-compose.prod.yml ps
docker logs -f glp1-prod-worker

# .env.prod 를 고친 뒤에는 restart 가 아니라 up -d 로 컨테이너를 다시 만든다 (환경변수는 생성 시점에 읽힌다)
# caddy 를 다시 만드는 동안 80/443 이 잠깐 끊긴다. Caddyfile 만 바꿨으면 reload 로 충분하다 (끊기지 않음)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --no-deps caddy
docker exec glp1-prod-caddy caddy reload --config /etc/caddy/Caddyfile
```

- 디스크는 50GB 고정이다. 차면 `docker system df` 로 보고 정리한다. `docker system prune -af` 는
  롤백용으로 정지해 둔 api 컨테이너와 이미지까지 지운다 — 쓴 뒤에는 다음 배포 전까지 `bg-switch.sh` 가 안 된다.
  **`docker volume prune` 은 쓰지 않는다** — `db-data` · `media` · `caddy_data` 가 지워진다.
- 서버는 **중지하지 말고 재부팅만** 한다. 중지 → 시작하면 공인 IP 가 바뀐다 (DuckDNS cron 이 따라가지만 몇 분 끊긴다).

### AI 스텁으로 데모하기

실제 AI 의 엔드포인트가 아직 501 인 동안에는 식사 분석이 전부 FAILED 로 끝난다.
그 기간에는 고정 더미 응답을 주는 `ai-stub` 을 붙인다. `.env.prod` 에 두 줄을 넣고 다시 배포한다.

```bash
COMPOSE_PROFILES=stub
AI_SERVICE_BASE_URL=http://ai-stub:8000
```

`ai` 컨테이너는 계속 뜨므로 LLM 값은 그대로 채워 둔다. 끌 때는 두 줄을 지우고 다시 배포한 뒤
남은 스텁을 내린다 — profile 을 끄는 것만으로는 이미 떠 있는 컨테이너가 내려가지 않는다.

```bash
docker rm -f glp1-prod-ai-stub
```

### 스테이징

같은 EC2 에 `develop` 을 올리는 스택을 하나 더 띄운다. 머지 전에 배포 환경(도메인·인증서·Caddy·서버 자원)에서
돌려 보려는 것이다. 설계: `docs/superpowers/specs/2026-10-13-staging-env-design.md`

```
glp1-demo.duckdns.org    ┐                     ┌─▶ glp1-prod-api-{blue,green}     (main,    /opt/glp1)
                         ├─▶ glp1-prod-caddy ──┤
glp1-staging.duckdns.org ┘   (80/443 하나)     └─▶ glp1-staging-api-{blue,green}  (develop, /opt/glp1-staging)
                                                    glp1-edge 네트워크
```

- 스테이징도 같은 Blue/Green 으로 올라간다. 롤백은 `/opt/glp1-staging/infra/bg-switch.sh staging`

- 80/443 은 운영 Caddy 하나가 맡고 도메인으로 나눈다. 스테이징 api 에는 외부 네트워크 `glp1-edge` 로 닿는다
- DB·사진·큐는 볼륨이 따로라(`glp1-staging_*`) 운영 데이터와 섞이지 않는다. 데모 키는 운영과 같다
- 스테이징 컨테이너에는 `mem_limit`(쓸 수 있는 양, 합 약 1.8GiB)과 `oom_score_adj: 500`(서버 메모리가 모자랄 때 OOM killer 가 운영 대신 스테이징을 먼저 고름)이 있다
- `deploy.sh` 와 `deploy-staging.sh` 는 같은 잠금(`/tmp/glp1-deploy.lock`)을 써서 한 번에 하나씩 돈다
- 운영 `.env.prod` 에 `STAGING_DOMAIN` 이 **반드시** 있어야 한다 (비면 운영 배포가 기동 전에 멈춘다)
- 이 변경 뒤 **첫** `deploy.sh` 전에 서버에서 네트워크를 한 번 만든다. 첫 배포는 서버에 있던 옛 `deploy.sh` 가 돌 수 있어(네트워크를 만들지 않는다) 먼저 손으로 만든다. 실패하면 한 번 더 실행한다 (워커가 내려가 있을 수 있다)

```bash
docker network inspect glp1-edge >/dev/null 2>&1 || docker network create glp1-edge
```

```bash
/opt/glp1-staging/infra/deploy-staging.sh            # origin/develop
/opt/glp1-staging/infra/deploy-staging.sh <커밋 SHA>
docker compose -p glp1-staging stop                  # 데모 당일 등 잠시 내릴 때
```

처음 띄우는 절차(서브도메인·`.env.staging`·시드)는 `contracts/Deployment.md` E2 절을 따른다.

로컬 측정(유휴 상태, Docker Desktop):

| 컨테이너 | 메모리 (사용 / 한도) |
|---|---|
| glp1-prod-caddy | 12.9MiB / 제한 없음 |
| glp1-prod-api | 67.6MiB / 제한 없음 |
| glp1-prod-worker | 59.7MiB / 제한 없음 |
| glp1-prod-ai | 35.6MiB / 제한 없음 |
| glp1-prod-db | 27.4MiB / 제한 없음 |
| glp1-staging-api | 67.5MiB / 512MiB |
| glp1-staging-worker | 59.5MiB / 512MiB |
| glp1-staging-ai | 35.6MiB / 256MiB |
| glp1-staging-db | 44.5MiB / 384MiB |

### 로컬에서 운영 구성 확인

`DOMAIN=localhost` 로 띄우면 Caddy 가 내부 인증서를 쓴다. 개발 환경과 함께 떠 있어도 된다 —
컨테이너 이름·이미지 태그(`glp1-prod-*`), 볼륨·네트워크(프로젝트 `glp1-prod`), 포트(80·443)가
개발용과 겹치지 않는다.

배포 스크립트(`bg-deploy.sh`)를 그대로 쓴다. 상태 디렉터리만 `GLP1_BG_DIR` 로 바꾸고,
`BG_SKIP_PRUNE=1` 로 끝의 `docker image prune` · `builder prune` 을 건너뛴다 — 서버 디스크 정리용이라
이 PC 의 다른 프로젝트 이미지·빌드 캐시까지 지운다. 이 프로젝트의 옛 `glp1-<env>-be` 태그 정리는 그대로 돈다.
Git Bash 에서는 `MSYS_NO_PATHCONV=1` 이 필요하다 — 없으면 `docker exec` 에 넘기는 `/etc/...` 경로를
Windows 경로로 바꿔 버린다. `bg-switch.sh` 는 `flock` 이 있어야 해서 Git Bash 에서는 돌지 않는다.

```bash
cd infra
cp .env.prod.example .env.prod      # DOMAIN=localhost, STAGING_DOMAIN=staging.localhost, 나머지는 아무 시험 값
docker network create glp1-edge     # 운영 compose 가 요구하는 외부 네트워크 (deploy.sh 는 알아서 만든다)
export MSYS_NO_PATHCONV=1 GLP1_BG_DIR="$HOME/.glp1-bg-local" BG_SKIP_PRUNE=1   # 상태는 저장소 밖
GIT_SHA=$(git rev-parse HEAD) APP_VERSION= ./bg-deploy.sh prod

curl -k https://localhost/health                                    # 200
curl -k https://localhost/docs                                      # 403
curl -k -H "X-Demo-Key: <값>" https://localhost/docs                # 200
curl -k -H "X-Demo-Key: <값>" https://localhost/internal/v1/foods   # 404
docker compose -p glp1-prod ps                                      # 모두 healthy

# 스테이징까지 함께: .env.prod 를 복사해 DB_PASSWORD·INTERNAL_SERVICE_TOKEN 만 바꾼다
cp .env.prod .env.staging
sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=staging-test-pw|; s|^INTERNAL_SERVICE_TOKEN=.*|INTERNAL_SERVICE_TOKEN=staging-test-token|" .env.staging
GIT_SHA=$(git rev-parse HEAD) APP_VERSION= ./bg-deploy.sh staging
curl -k https://staging.localhost/health                            # 200

docker compose -p glp1-staging down
docker compose -p glp1-prod down    # -v 를 붙이면 DB·사진·인증서 볼륨까지 지운다
rm -rf "$GLP1_BG_DIR"
```

같은 커밋으로 두 번 배포하면 "이미 떠 있는 버전" 으로 막힌다. 다시 시험할 때는 `GIT_SHA` 를 바꾼다.
