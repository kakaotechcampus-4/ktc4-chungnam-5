# infra — 로컬 개발 환경

컨테이너는 전부 같은 compose 프로젝트(`infra`)에 속한다. 그래서 한 네트워크(`infra_default`)에
붙고 **서비스 이름으로 서로를 부를 수 있다.**

| 파일 | 서비스 | 컨테이너 | 호스트 포트 |
|---|---|---|---|
| `docker-compose.yml` | `db` | `glp1-db` | — |
| `docker-compose.ai-stub.yml` | `ai-stub` | `glp1-ai-stub` | 8001 |
| `docker-compose.be.yml` | `api` · `worker` | `glp1-api` · `glp1-worker` | 8000 (api) |

> ⚠️ `docker-compose.yml`(DB)은 아직 커밋돼 있지 않다. `glp1-db` 컨테이너는 돌고 있지만
> 파일이 저장소에 없어서 새로 클론한 사람은 DB 를 띄울 수 없다. 담당 티켓에서 추가할 것.

---

## 전부 띄우기

```powershell
docker compose `
  -f docker-compose.ai-stub.yml `
  -f docker-compose.be.yml `
  up -d
```

파일을 하나씩 띄워도 동작한다(같은 프로젝트라 네트워크를 공유한다). 다만 그때는
compose 가 나머지 컨테이너를 모르는 것으로 보고 **`Found orphan containers` 경고**를 낸다.
`-f` 를 모아 주면 대부분 사라진다.

`glp1-db` 에 대한 경고는 위 명령으로도 남는다 — `docker-compose.yml` 이 저장소에 없어서
`-f` 목록에 넣을 수가 없기 때문이다. 그 파일이 커밋되면 목록에 추가하고 경고도 없어진다.
`--remove-orphans` 는 **쓰지 말 것.** 돌고 있는 `glp1-db` 를 지워 버린다.

내리기:

```powershell
docker compose -f docker-compose.ai-stub.yml -f docker-compose.be.yml down
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
