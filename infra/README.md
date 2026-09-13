# infra — 로컬 개발 환경

컨테이너는 전부 같은 compose 프로젝트(`infra`)에 속한다. 그래서 한 네트워크(`infra_default`)에
붙고 **서비스 이름으로 서로를 부를 수 있다.**

| 파일 | 서비스 | 컨테이너 | 호스트 포트 |
|---|---|---|---|
| `docker-compose.yml` | `db` | `glp1-db` | — |
| `docker-compose.queue.yml` | `sqs` | `glp1-sqs` | 9324 |
| `docker-compose.ai-stub.yml` | `ai-stub` | `glp1-ai-stub` | 8001 |
| `docker-compose.be.yml` | `api` · `worker` | `glp1-api` · `glp1-worker` | 8000 (api) |

> ⚠️ `docker-compose.yml`(DB)은 아직 커밋돼 있지 않다. `glp1-db` 컨테이너는 돌고 있지만
> 파일이 저장소에 없어서 새로 클론한 사람은 DB 를 띄울 수 없다. 담당 티켓에서 추가할 것.

---

## 전부 띄우기

```powershell
docker compose `
  -f docker-compose.queue.yml `
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
docker compose -f docker-compose.queue.yml -f docker-compose.ai-stub.yml -f docker-compose.be.yml down
```

---

## 주소가 두 벌인 이유

**컨테이너 안에서 `localhost` 는 자기 자신이다.** 그래서 같은 서비스를 부르는 주소가
어디서 부르느냐에 따라 다르다.

| 대상 | 호스트(venv 실행)에서 | 컨테이너 안에서 |
|---|---|---|
| ai-stub | `http://localhost:8001` | `http://ai-stub:8000` |
| sqs | `http://localhost:9324` | `http://sqs:9324` |
| db | `localhost:5433` | `db:5432` |

호스트 쪽 값은 `backend/.env` 에, 컨테이너 쪽 값은 `docker-compose.be.yml` 의
`environment` 에 있다. 환경변수가 `.env` 보다 우선하므로 컨테이너에서는 compose 값이 이긴다.

포트도 두 벌이다. `ai-stub` 은 컨테이너 안에서 8000 을 듣고 호스트 8001 로 매핑된다(`"8001:8000"`).
BE API 가 호스트 8000 을 쓰기 때문에 겹치지 않게 옮긴 것이다.

### node-address.host 는 건드리지 않아도 된다

`elasticmq.conf` 의 `node-address.host` 는 `localhost` 로 두었다. ElasticMQ 가 돌려주는
큐 URL 에 이 값이 박히지만, 우리 코드는 큐 URL 을 설정에서 직접 만들어 넘기고
ElasticMQ 는 **경로(`/queue/<이름>`)로 라우팅**하므로 호스트 부분이 달라도 동작한다.
컨테이너에서 `http://sqs:9324/queue/glp1-tasks` 로 붙는 것을 확인했다.

---

## 왕복 확인

```powershell
cd ..\backend
.\.venv\Scripts\Activate.ps1
python -m scripts.smoke_queue_ai
```

정상 경로 1회와 실패 경로(`ERROR_500` → 3회 재배달 → DLQ)를 돌린다.
이 스크립트는 호스트에서 도므로 `backend\.env` 의 `localhost` 주소를 쓴다.

컨테이너 워커가 처리하는 것을 보려면 작업만 넣고 로그를 본다:

```powershell
docker logs -f glp1-worker
```

---

## 큐 상태 보기

**웹 UI 는 없다.** `elasticmq-native` 이미지에는 `rest-stats` 서버가 들어 있지 않아,
설정에 넣어도 조용히 무시되고 9325 에는 아무것도 뜨지 않는다.

대신 `GetQueueAttributes` 로 읽는다:

```powershell
cd ../backend
.\.venv\Scripts\Activate.ps1
python -m scripts.queue_status
```

```
큐                          대기     처리중     지연
---------------------------------------------
glp1-tasks                  2       1      0
glp1-tasks-dlq              0       0      0
```

`처리중` 은 누군가 꺼내갔고 아직 `delete` 하지 않은 것이다 —
visibility timeout(60초) 안에 처리되지 않으면 `대기` 로 돌아온다.
