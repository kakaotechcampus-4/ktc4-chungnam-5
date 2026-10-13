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
| `docker-compose.prod.yml` | `db` · `api` · `worker` · `ai` · `caddy` (+ 데모용 `ai-stub`). compose 프로젝트 이름은 `glp1-prod` |
| `Caddyfile` | HTTPS(Let's Encrypt) + 데모 키 검사 |
| `.env.prod.example` | 서버의 `.env.prod` 에 채울 키 목록. 비밀값은 **서버의 `.env.prod` 에만** 둔다 |
| `deploy.sh` | 배포 스크립트. 사람과 CI 가 같은 스크립트를 쓴다 |

```
인터넷 ──80/443──▶ caddy ──▶ api:8000 ──▶ db:5432
                              ▲   │
                    /internal │   ▼ (큐: task_queue 테이블)
                              ai ◀── worker
```

### 접근 규칙 (Caddyfile, 위에서부터)

1. `/internal/*` → 404. AI 가 BE 를 다시 부르는 경로라 compose 내부 네트워크로만 닿는다
2. `/health` → 통과
3. `X-Demo-Key` 헤더가 `DEMO_KEY` 와 같거나, 접속 IP 가 `ALLOWED_IPS` 안이면 → api.
   팀원 IP 는 경로 제한 없이 **전체 API** 를 키 없이 통과한다 — Swagger 의 "Try it out" 요청에는
   키 헤더가 붙지 않아 `/docs` 만 열면 쓸 수 없다. 그래서 공용망 IP 는 넣지 않는다
4. 나머지 → 403

업로드 제한은 12MB 로 BE(10MB)보다 약간 크다. BE 가 내는 413 메시지가 앱까지 가게 하려는 것이다.

### 첫 배포 (Session Manager 에서)

```bash
sudo mkdir -p /opt/glp1 && sudo chown ubuntu:ubuntu /opt/glp1
git clone https://github.com/kakaotechcampus-4/ktc4-chungnam-5.git /opt/glp1 && cd /opt/glp1/infra
cp .env.prod.example .env.prod && vi .env.prod   # 비밀값은 openssl rand -hex 32 로 만든다
chmod 600 .env.prod
./deploy.sh
docker compose -f docker-compose.prod.yml logs -f caddy   # 인증서 발급 로그 확인
```

스키마는 `api` 가 기동할 때마다 `alembic upgrade head` 로 맞춘다. 공공 영양DB 시드는
**처음 한 번만** 넣는다 (336,351건):

```bash
docker exec -i glp1-prod-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner' \
  < seed/food_refs_20260828.dump
```

### 다시 배포

`main` 에 병합되면 GitHub Actions 가 `ssm send-command` 로 `deploy.sh` 를 부른다.
급할 때는 Session Manager 에서 직접 실행한다.

```bash
/opt/glp1/infra/deploy.sh            # origin/main
/opt/glp1/infra/deploy.sh <커밋 SHA>  # 특정 커밋
```

`deploy.sh` 는 이미지를 먼저 만들고, **워커를 내린 뒤** 나머지를 올린다. 마이그레이션은 `api` 가
기동할 때 도는데, 옛 워커가 그와 겹치면 잠금·enum 문제로 마이그레이션이나 워커가 터질 수 있어서다
(예: `9a1c4e7b2d3f`). 그래서 배포 중 워커는 마이그레이션 · `api` 기동 동안 잠시 멈춘다.
작업은 큐(`task_queue`)에 남아 있다가 새 워커가 처리한다. 손으로 배포할 때도 `up` 을 직접
부르지 말고 `deploy.sh` 를 쓴다.

### 운영 중 자주 쓰는 것

```bash
cd /opt/glp1/infra
docker compose -f docker-compose.prod.yml ps
docker logs -f glp1-prod-worker

# .env.prod 를 고친 뒤에는 restart 가 아니라 up -d 로 컨테이너를 다시 만든다 (환경변수는 생성 시점에 읽힌다)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d caddy
```

- 디스크는 50GB 고정이다. 차면 `docker system df` 로 보고 `docker system prune -af` 로 정리한다.
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

### 로컬에서 운영 구성 확인

`DOMAIN=localhost` 로 띄우면 Caddy 가 내부 인증서를 쓴다. 개발 환경과 함께 떠 있어도 된다 —
컨테이너 이름·이미지 태그(`glp1-prod-*`), 볼륨·네트워크(프로젝트 `glp1-prod`), 포트(80·443)가
개발용과 겹치지 않는다.

```bash
cd infra
cp .env.prod.example .env.prod      # DOMAIN=localhost, 나머지는 아무 시험 값
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build --wait

curl -k https://localhost/health                                    # 200
curl -k https://localhost/docs                                      # 403
curl -k -H "X-Demo-Key: <값>" https://localhost/docs                # 200
curl -k -H "X-Demo-Key: <값>" https://localhost/internal/v1/foods   # 404
docker compose -f docker-compose.prod.yml ps                        # 5개 모두 healthy

docker compose -f docker-compose.prod.yml down    # -v 를 붙이면 DB·사진·인증서 볼륨까지 지운다
```
