# DB 테이블 큐 — 설계

날짜: 2026-09-20
브랜치: `be/db-table-queue`
상태: 승인됨 (구현 계획 작성 대기)

## 왜 바꾸는가

지금 비동기 작업은 SQS(로컬은 ElasticMQ 컨테이너)를 거친다. 돌아가지만 값을 치른다.

- 로컬 개발자가 컨테이너를 하나 더 띄워야 한다. 안 띄우면 `test_queue_integration.py`
  가 통째로 skip 되고, 아무도 그걸 눈치채지 못한다.
- 작업 등록이 도메인 커밋과 따로 논다. `README` 가 "커밋이 먼저다"를 굵게 적어 둔 이유가
  이것이다 — 순서를 틀리면 Worker 가 DB 에 없는 행을 찾는다. 사람의 주의력에 맡긴 불변식이다.
- 실패한 작업이 어디에 왜 실패했는지 DB 에 남지 않는다. DLQ 를 boto3 로 들여다봐야 한다.

큐에 넣는 작업량이 초당 수십 건 규모가 아니고(사용자 행동 하나당 작업 하나),
이미 PostgreSQL 을 쓰고 있다. 테이블 하나로 위 셋을 전부 없앨 수 있다.

## 핵심 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 잠금 방식 | 처리하는 **동안 행 잠금을 유지**한다 | 실패·크래시 시 ROLLBACK 만으로 작업이 되돌아간다. lease 만료 시계를 따로 돌리지 않는다 |
| 실패 처리 | attempts 를 **별도 트랜잭션**에 기록 + backoff + 3회째 격리 | 롤백하면 attempts 도 같이 롤백된다. 별도로 남기지 않으면 poison task 가 워커를 무한 점유한다 |
| 작업 등록 | 도메인과 **같은 트랜잭션** | "커밋이 먼저다" 불변식 자체를 없앤다 |
| ElasticMQ | **전면 제거** | 두 구현을 남기면 재시도·격리 의미가 갈라지고 죽은 코드가 된다 |

## 1. 테이블 — `task_queue`

```
id           UUID        PK
type         TEXT        NOT NULL   -- 'meal.analyze' 등. dispatch 키
payload      JSONB       NOT NULL   -- 기존 메시지 본문에서 type 을 뺀 나머지
status       task_status NOT NULL   -- PENDING | DONE | FAILED
attempts     INT         NOT NULL DEFAULT 0
next_run_at  TIMESTAMPTZ NOT NULL DEFAULT now()
result       JSONB       NULL       -- 성공 시 핸들러 반환값
last_error   TEXT        NULL       -- 실패 사유. 민감정보를 넣지 않는다 (규칙 6)
created_at   TIMESTAMPTZ NOT NULL
updated_at   TIMESTAMPTZ NOT NULL
finished_at  TIMESTAMPTZ NULL
```

인덱스는 부분 인덱스 하나다.

```sql
CREATE INDEX ix_task_queue_pending ON task_queue (next_run_at, created_at)
  WHERE status = 'PENDING';
```

DONE 행이 쌓여도 집는 쿼리는 이 작은 인덱스만 훑는다. 보존 정책(오래된 DONE 정리)은
이번 범위가 아니다 — 필요해지면 그때 배치를 붙인다.

### `RUNNING` 상태를 두지 않는다

잠금을 유지하는 모델에서 `RUNNING` 을 써도 같은 트랜잭션 안이라 다른 세션에는 보이지
않는다. 커밋되는 시점에는 이미 `DONE` 이므로 아무도 관측할 수 없는 값이 된다.

"지금 처리 중"은 곧 **"PENDING 인데 행 잠금이 걸린 상태"**이고, 그건
`pg_locks` · `pg_stat_activity` 로 본다. `scripts/queue_status.py` 가 이걸 보여준다.

### `FAILED` 가 DLQ 자리다

`attempts` 가 `QUEUE_MAX_ATTEMPTS` 에 닿으면 `FAILED` 로 옮기고 더 집지 않는다.
되살리는 것은 사람이 한다 — `UPDATE task_queue SET status='PENDING', attempts=0 WHERE id=…`.
자동 복구를 넣지 않는 이유는, 3번 실패한 작업은 코드나 데이터가 잘못된 경우가 대부분이라
사람이 원인을 본 뒤에 다시 넣는 편이 맞기 때문이다.

### 모델 등록

`app/models/task.py` 에 `Task` 모델(테이블명 `task_queue`), `app/models/enums.py` 에
`TaskStatus`(`pg_enum` 사용), `app/models/__init__.py` 에 import 추가 — Alembic autogenerate
가 테이블을 발견하려면 필요하다.

모델 이름은 `Task` 다(`TaskQueue` 가 아니다). `TaskQueue` 는 `infra/queue.py` 의 Protocol
이름으로 남긴다 — 행 하나는 작업이고, 큐는 그 행들을 집어 주는 쪽이다.

## 2. `app/infra/queue.py` 재작성

`receive` 와 `delete` 를 분리할 수 없다. 트랜잭션이 처리 전체를 감싸야 하기 때문이다.
그래서 인터페이스가 컨텍스트 매니저로 바뀐다.

```python
@dataclass(frozen=True)
class ClaimedTask:
    id: UUID
    type: str
    payload: dict[str, Any]
    attempts: int          # 지금까지 실패한 횟수. 첫 시도는 0


def enqueue(db: Session, task_type: str, payload: dict[str, Any]) -> None:
    """호출부의 세션에 INSERT 만 한다. 커밋은 service 가 한 번에 한다."""


class TaskQueue(Protocol):
    def claim(self) -> AbstractContextManager[tuple[Session, ClaimedTask] | None]: ...
```

`claim()` 이 책임지는 전부:

| 시점 | 동작 |
|---|---|
| 진입 | 세션을 열고 `SELECT … WHERE status='PENDING' AND next_run_at <= now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1` |
| 빈 큐 | `None` 을 yield 하고 세션을 정리한다 |
| 블록 정상 종료 | `status='DONE'`, `result=<핸들러 반환값>`, `finished_at=now()` → **COMMIT** |
| 블록에서 예외 | **ROLLBACK** → 별도 세션으로 실패를 기록 → 예외를 재전파 |

실패 기록(별도 세션, 짧은 트랜잭션):

```sql
UPDATE task_queue
   SET attempts = attempts + 1,
       last_error = :error,
       next_run_at = now() + :backoff,
       status = CASE WHEN attempts + 1 >= :max_attempts THEN 'FAILED' ELSE 'PENDING' END,
       updated_at = now()
 WHERE id = :id;
```

`backoff = QUEUE_BACKOFF_BASE_SEC * 2 ** attempts` (30s → 60s). `FAILED` 로 갈 때도
`next_run_at` 을 갱신하지만 의미는 없다 — 조건절이 `status='PENDING'` 을 먼저 본다.

**주의:** 실패 기록은 실패 경로에서 도는 코드다. 여기서 또 예외가 나면(DB 연결이 끊긴
경우 등) 원래 예외를 덮어써서는 안 된다. 기록 실패는 로그만 남기고 원래 예외를 올린다.
기록이 안 되면 attempts 가 안 오를 뿐, 작업은 PENDING 으로 남아 다음에 다시 집힌다.

### 핸들러에 큐 세션을 넘긴다

`run(db, task, ai) -> dict | None` 으로 바꾸고 `jobs/*` 의 `with SessionLocal() as db:`
를 없앤다. 도메인 쓰기와 작업 완료 기록이 한 트랜잭션이 된다.

이게 두 번째 큰 이득이다. SQS 때는 "AI 호출은 성공했는데 작업 삭제 직전에 죽으면 재배달"
때문에 재배달 대비 코드가 필요했다 — `analyze_meal` 의 "기존 모델 생성 항목을 지우고
새로 넣는다", `feedback_daily` 의 "근거를 지우고 다시 만든다" 가 그것이다. 이제 실패하면
이전 시도의 도메인 변경 자체가 롤백되므로 흔적이 없다.

멱등성 검사가 전부 불필요해지는 것은 아니다. `analyze_meal` 의 "status 가 ANALYZING 이
아니면 return" 은 남긴다 — 같은 작업이 실수로 두 번 enqueue 되는 경우(사용자 더블 탭 등)는
여전히 가능하고, 그건 큐 구현과 무관하다.

`jobs/*` 4개는 현재 전부 `NotImplementedError` 스텁이다. 시그니처와 TODO 주석만 고치면
된다 — 지금이 바꿀 타이밍이다.

## 3. 워커 루프

```python
while _running:
    with queue.claim() as claimed:
        if claimed is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue
        db, task = claimed
        handle(db, task, ai)
```

`loop.py` 의 주제가 "삭제 시점"에서 "커밋 시점"으로 바뀔 뿐 구조는 같다. 예외는
`claim()` 이 실패 기록까지 하고 재전파하므로, 루프는 잡아서 로그만 남기고 다음으로 간다.

롱폴링 대신 1초 sleep 이다. LISTEN/NOTIFY 는 넣지 않는다 — 10~30초 걸리는 AI 작업 앞에서
1초 지연은 의미가 없고, 알림 유실 시의 폴백 폴링을 어차피 둬야 해서 코드만 는다.

`request_stop()` 은 그대로 둔다. sleep 중에 SIGTERM 을 받으면 최대 1초 늦게 멈춘다.

## 4. 설정과 운영 리스크

`QueueSettings` 에서 SQS·AWS 항목이 전부 빠지고 셋만 남는다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `QUEUE_POLL_INTERVAL_SEC` | `1.0` | 빈 큐일 때 쉬는 시간 |
| `QUEUE_MAX_ATTEMPTS` | `3` | 이 횟수만큼 실패하면 FAILED 로 격리 (기존 maxReceiveCount 와 같은 값) |
| `QUEUE_BACKOFF_BASE_SEC` | `30` | 재시도 지연의 기준. 30s → 60s |

`QueueSettings` 를 `core.Settings` 로 합치지 않고 `infra/queue.py` 안에 남기는 것은
기존 패턴(`AiSettings` 도 같은 모양)과 맞추기 위해서다.

리스크 두 가지를 설계에 박아 둔다.

- **idle in transaction** — AI 호출 45초 동안 커넥션이 트랜잭션을 연 채 있다. 워커가
  여는 세션에 `SET idle_in_transaction_session_timeout = '120s'` 를 걸어, 멈춘 워커가
  행을 영원히 붙잡지 않게 한다. AI 타임아웃(45초)보다 넉넉히 위여야 정상 작업을 죽이지
  않는다. 관리형 DB 가 이 값을 더 낮게 강제하는 경우가 있으니 배포 시 확인한다.
- **커넥션 풀** — 워커는 단일 스레드에 배치 1이라 동시에 1~2개(작업용 + 실패 기록용)면
  충분하다. `DB_POOL_SIZE=5` 를 그대로 둔다.

## 5. 테스트

`test_queue_integration.py`(ElasticMQ 의존, 컨테이너가 없으면 통째로 skip)를
`test_task_queue.py` 로 교체한다. conftest 의 Postgres testcontainer 를 그대로 쓰므로
**조건부로 건너뛰는 테스트가 없어진다.**

검증 항목:

1. 도메인 트랜잭션이 롤백되면 작업도 같이 사라진다 (enqueue 원자성)
2. 커넥션 둘이 동시에 claim 하면 서로 다른 행을 집는다 — SKIP LOCKED 의 핵심
3. 실패하면 PENDING 으로 남고 attempts·last_error·next_run_at 이 기록된다
4. attempts 가 `QUEUE_MAX_ATTEMPTS` 에 닿으면 FAILED 로 격리되고 더 집히지 않는다
5. `next_run_at` 이 미래면 집히지 않는다
6. 성공하면 DONE + result 가 남는다

**2번은 conftest 의 `db` 픽스처로 못 쓴다.** 그 픽스처는 savepoint 방식의 단일 커넥션이라
두 워커를 흉내 낼 수 없다. `test_engine` 에서 커넥션을 직접 둘 연다.

`test_worker_loop.py` 의 `FakeQueue` 를 새 인터페이스로 갈아끼운다. 검증 대상이
"실패한 작업을 지우지 않는가"에서 **"실패한 작업을 DONE 으로 커밋하지 않는가"**로 바뀐다.

## 6. 걷어내는 것

| 대상 | 처리 |
|---|---|
| `infra/docker-compose.queue.yml`, `infra/elasticmq.conf` | 삭제 |
| `backend/requirements.txt` 의 `boto3` | 삭제 |
| `infra/docker-compose.be.yml` | `SQS_*`·`AWS_*` 환경변수 제거, worker 의 db 의존 명시 |
| `backend/.env.example`, `backend/.env` | 큐 절을 `QUEUE_*` 셋으로 교체 |
| `scripts/queue_status.py` | 상태별 집계 + 잠긴 행(`pg_locks`) 조회로 재작성 |
| `scripts/smoke_queue_ai.py` | enqueue 후 워커가 집어가는지, 실패 시 attempts 가 오르는지 확인하도록 재작성 |
| `backend/README.md` 큐 절 (201~306행 부근) | DB 큐 기준으로 다시 씀 |
| `infra/README.md` | 컨테이너 표에서 sqs 행 제거, 주소 표·왕복 확인 절 갱신 |

## 7. 마이그레이션

Alembic revision 1개. `down_revision = 'fb9324353300'`(현재 head).

- `task_status` ENUM 생성
- `task_queue` 테이블 생성
- 부분 인덱스 `ix_task_queue_pending` 생성 (autogenerate 가 부분 인덱스를 놓치므로 손으로 확인)
- downgrade 는 역순. ENUM 은 `DROP TYPE` 까지 명시한다 — 테이블만 지우면 타입이 남는다

## 범위 밖

- DONE 행 보존/정리 배치
- 워커 다중화(프로세스를 늘리면 SKIP LOCKED 로 그냥 동작한다. 늘릴 일이 생기면 그때)
- 우선순위 컬럼, 지연 실행 API (`next_run_at` 이 이미 있지만 enqueue 로는 노출하지 않는다)
- LISTEN/NOTIFY
