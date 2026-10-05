# DB 테이블 큐 lease 전환 — 설계

날짜: 2026-10-05
브랜치: `be/refactor-queue-lease`
상태: 승인됨 (구현 계획 작성 대기)
대체: `2026-09-20-db-table-queue-design.md` 의 "처리하는 동안 행 잠금을 유지한다" 결정

## 왜 바꾸는가

지금 워커는 `SELECT … FOR UPDATE SKIP LOCKED` 로 집은 행의 잠금과 트랜잭션을 **AI 응답을
기다리는 내내(최대 45초)** 쥐고 있다. 돌아가지만 값을 치른다.

- 작업 하나가 DB 커넥션 하나를 30~45초씩 점유한다. 워커를 N 개로 늘리면 커넥션 N 개가
  상시로 `idle in transaction` 이 된다. 처리량을 늘리는 일(다음 단계)이 커넥션 풀 크기에 묶인다.
- 롱 트랜잭션은 운영에서 장애 신호로 다루는 지표다. 정상 동작이 그 지표를 상시로 켜 둔다
  (멘토 리뷰: "롱 트랜잭션은 알림 조건 중 하나라 가능한 피하는 게 좋다").
- 행 잠금 유지 상태는 커밋 전이라 밖에서 보이지 않는다. "지금 처리 중" 을
  `pg_stat_activity` 로 근사해야 한다(`scripts/queue_status.py`).

## 핵심 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 잠금 방식 | **lease** — 집자마자 `PROCESSING` + 토큰 + 만료 시각을 커밋하고 커넥션을 반납한다 | AI 를 기다리는 동안 트랜잭션·커넥션이 0 개다 |
| 핸들러 계약 | `load` / `call_ai` / `apply` **3단계로 쪼갠다** | AI 호출 중 커넥션을 쥐는 실수를 규칙이 아니라 구조로 막는다. `call_ai` 는 `db` 를 아예 받지 않는다 |
| 완료 | 도메인 쓰기와 `DONE` 을 **한 트랜잭션**에, `lease_token` 이 내 것일 때만 | lease 를 잃은 워커의 도메인 쓰기가 반영되지 않는다 |
| attempts 의미 | "실패한 횟수" → **"집은 횟수"** (claim 때 증분) | 워커를 죽이는 작업(OOM·SIGKILL)은 실패 기록 기회가 없다. 집을 때 세야 이것도 상한에 걸린다 |
| 회수 | **claim 직전에** 만료된 `PROCESSING` 을 되돌린다 | 별도 스케줄러가 필요 없다. 기존 claim 쿼리·인덱스를 건드리지 않는다 |

검토했지만 고르지 않은 것:

- 시그니처 `run(db, task, ai)` 유지 + "AI 전에 `db.commit()`" 규칙 — 변경은 가장 작지만
  하나라도 빠뜨리면 다시 롱 트랜잭션이다. `dispatch.py` 의 "절대 커밋하지 마라" 와 같은 종류의 함정이 된다.
- 핸들러에 세션 팩토리를 넘겨 트랜잭션을 직접 관리 — `DONE` 과 도메인 쓰기를 묶는 책임까지
  핸들러로 넘어간다.

## 1. 테이블 — `task_queue`

추가:

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| `lease_token` | `uuid NULL` | claim 할 때마다 새로 발급. 완료·실패·반납이 "내 lease 인가" 를 이걸로 확인한다 |
| `lease_expires_at` | `timestamptz NULL` | 이 시각이 지나면 다른 워커가 회수한다 |

- `task_status` enum 에 `PROCESSING` 을 더한다. `PENDING` · `DONE` · `FAILED` 의 뜻은 그대로다.
- `PROCESSING` 이 아닌 행은 두 lease 컬럼이 NULL 이다.
- 부분 인덱스 `ix_task_queue_processing (lease_expires_at) WHERE status = 'PROCESSING'` — 회수 UPDATE 용.
  기존 `ix_task_queue_pending` 은 그대로다.

### 상태 전이

```
PENDING ──claim──▶ PROCESSING ──complete──▶ DONE
   ▲                   │
   ├── fail(재시도) ────┤
   ├── lease 만료 회수 ──┤
   ├── release(종료) ───┤
   │                   └── fail(격리)/회수(상한 도달) ──▶ FAILED
```

### `attempts`

- claim 이 `attempts = attempts + 1` 을 같은 UPDATE 에서 한다. SKIP LOCKED 로 잠근 행 위의 증분이라
  다른 워커와 경합하지 않는다 — 지금 `_record_failure` 의 "DB 안에서 증분해야 실패 횟수를 잃지 않는다"
  는 처리가 필요 없어진다.
- 핸들러에 넘기는 `ClaimedTask.attempts` 는 **이전까지의 시도 횟수**(증분 전 값)다. job 들의
  `_is_last_attempt` (`task.attempts + 1 >= QUEUE_MAX_ATTEMPTS`) 는 바꾸지 않는다.
- 격리 판정은 `attempts >= QUEUE_MAX_ATTEMPTS` (증분 후 값) 이다.

## 2. 큐 — `app/infra/queue.py`

컨텍스트 매니저 하나(`claim()`)였던 것을 동작별로 나눈다.

```python
@dataclass(frozen=True)
class Lease:
    task: ClaimedTask
    token: uuid.UUID

class TaskQueue(Protocol):
    def claim(self) -> Lease | None: ...
    def read(self, lease: Lease) -> AbstractContextManager[Session]: ...
    def complete(self, lease: Lease) -> AbstractContextManager[Completion]: ...
    def fail(self, lease: Lease, exc: BaseException) -> None: ...
    def release(self, lease: Lease) -> None: ...
```

| 메서드 | 트랜잭션 | 하는 일 |
|---|---|---|
| `claim` | 짧은 Tx, **커밋** | ① 만료 lease 회수 ② `SKIP LOCKED` 로 PENDING 한 건 ③ `PROCESSING` · 새 토큰 · `lease_expires_at = now() + QUEUE_LEASE_SEC` · `attempts + 1` |
| `read` | 짧은 Tx, **항상 롤백** | `load` 가 읽기만 하도록. 실수로 쓴 것도 반영되지 않는다 |
| `complete` | 짧은 Tx, **커밋** | 블록 안의 도메인 쓰기 + `DONE`·`result`·`finished_at`, lease 컬럼 NULL. 아래 가드가 0행이면 `LeaseLostError` 로 전부 롤백 |
| `fail` | 별도 Tx | 아래 SQL. 가드가 0행이면 조용히 넘어간다 |
| `release` | 별도 Tx | `PENDING` · `attempts - 1` · lease NULL. 종료 신호로 놓는 작업이 시도 횟수를 태우지 않게 |

`Completion` 은 `db: Session` 과 `result: dict | None` 을 가진다 — 지금 `Claim.result` 와 같은 역할.

모든 가드는 `WHERE id = :id AND status = 'PROCESSING' AND lease_token = :mine` 이다.

`complete` 는 지금 `claim()` 의 성공 경로와 같은 이유로 DONE UPDATE 와 commit 을 같은 try 안에 둔다 —
`autoflush=False` 라 도메인 객체의 flush(FK·UNIQUE 위반, JSON 직렬화 실패)가 commit 시점에 터진다.

### `fail`

```sql
UPDATE task_queue SET
  status           = CASE WHEN attempts >= :max OR :non_retryable THEN 'FAILED' ELSE 'PENDING' END,
  next_run_at      = now() + make_interval(secs => :backoff_base * 2 ^ (attempts - 1)),  -- 30s → 60s
  last_error       = :reason,
  lease_token      = NULL,
  lease_expires_at = NULL,
  updated_at       = now()
WHERE id = :id AND status = 'PROCESSING' AND lease_token = :mine
```

- `status` CASE 는 지금처럼 native ENUM 으로 cast 한다.
- `last_error` 는 지금처럼 SQL 덤프(`\n[SQL:` 뒤)를 잘라낸 뒤 500자로 자른다(규칙 6).
- `fail` 자체가 실패해도(DB 순단) 작업은 `PROCESSING` 으로 남았다가 **lease 만료로 회수된다**.
  조용히 사라지지 않는다.

### 회수

claim 이 매번 먼저 실행한다.

```sql
UPDATE task_queue SET
  status = CASE WHEN attempts >= :max THEN 'FAILED' ELSE 'PENDING' END,
  next_run_at = now() + backoff(attempts),
  last_error = 'LeaseExpired',
  lease_token = NULL, lease_expires_at = NULL, updated_at = now()
WHERE status = 'PROCESSING' AND lease_expires_at < now()
```

- attempts 는 claim 때 이미 셌으므로 올리지 않는다.
- 여러 워커가 동시에 돌려도 행 잠금이 직렬화하고, READ COMMITTED 의 재평가로 두 번째는 0행이다.

### 설정

| 이름 | 값 | 비고 |
|---|---|---|
| `QUEUE_LEASE_SEC` | 120 | 새로 추가. `AI_TIMEOUT_SEC`(45) 보다 커야 한다 — 워커 기동 시 확인하고 아니면 기동을 거부한다 |
| `QUEUE_IDLE_TX_TIMEOUT_SEC` | — | **삭제.** 작업 트랜잭션이 짧아져 안전망 자체가 의미가 없다 |
| `QUEUE_POLL_INTERVAL_SEC` · `QUEUE_MAX_ATTEMPTS` · `QUEUE_BACKOFF_BASE_SEC` | 그대로 | |

`AI_TIMEOUT_SEC` 는 httpx 의 단계별 타임아웃이지 총 시간 상한이 아니다. 응답이 조금씩 흘러들면
45초를 넘을 수 있어 lease 를 넉넉히 120초로 둔다.

## 3. 핸들러 계약 — `app/worker/jobs/*`

작업 하나당 파일 하나는 그대로다. 각 파일이 세 함수와 `JOB` 을 둔다.

```python
@dataclass(frozen=True)
class Skip:
    result: dict[str, Any] | None

@dataclass(frozen=True)
class Job(Generic[Ctx, Resp]):
    load: Callable[[Session, ClaimedTask], Ctx | Skip]
    call_ai: Callable[[Ctx, AiClient], Resp | None]
    apply: Callable[[Session, ClaimedTask, Ctx, Resp | None], dict[str, Any] | None]
    on_ai_error: Callable[[Session, ClaimedTask, Ctx, Exception], dict[str, Any] | None] | None = None
```

| 단계 | 세션 | 규칙 |
|---|---|---|
| `load` | `queue.read` (롤백) | 읽기만. 반환값은 **ORM 객체가 아닌 순수 데이터** — 세션이 닫히면 ORM 객체는 못 쓴다. AI 가 필요 없으면 `Skip(result)` |
| `call_ai` | **없음** | AI 를 부르고 응답을 검증한다. 부를 필요가 없으면 `None` |
| `apply` | `queue.complete` | 다시 잠그고 재확인한 뒤 쓴다. 지금 job 들의 "AI 를 기다리는 사이 바뀌었으면 쓰지 않는다" 로직이 여기 온다 |
| `on_ai_error` (선택) | `queue.complete` | `call_ai` 가 실패했을 때 도메인에 실패를 남겨야 하는 job 만. 정의하지 않으면 예외가 그대로 올라가 큐가 재시도한다 |

job 별로 자르는 자리:

| job | `load` | `call_ai` | `apply` | `on_ai_error` |
|---|---|---|---|---|
| `meal.analyze` | 식사 읽기, `_should_skip` 이면 `Skip(None)`, 요청 본문 구성 | `ai.analyze_meal` + `_parse_item` 검증 | 잠금·재확인, 항목 교체(SAVEPOINT), `REVIEW_REQUIRED`/`FAILED` | `_fail_or_raise` — 마지막 시도면 식사 `FAILED` |
| `feedback.meal` | 식사·평가 읽기, `_skip_reason` 이면 `Skip`, `scored_as` 와 요청 본문 | `ai.short_feedback` | 잠금·재확인(평가 변경 포함), upsert | — |
| `feedback.daily` | 근거·단계 읽기, 요청 본문 | 근거가 없으면 `None`, 아니면 `ai.short_feedback` | `None` 이면 그날 행 삭제, 아니면 upsert + 근거 재생성 | — |
| `feedback.long` | 점수·근거·단계 읽기, 요청 본문 | 점수 있는 날이 모자라면 `None`, 아니면 `ai.long_feedback` | `None` 이면 기간 행 삭제, 아니면 upsert + 근거 재생성 | — |

로직은 바꾸지 않고 자르기만 한다. 로그·`result` 에 민감정보를 남기지 않는 규칙(규칙 6)도 그대로다.

`meal.analyze` 의 `_request` 는 presigned URL 을 발급하므로 `load` 가 아니라 `call_ai` 직전에 만든다 —
지금처럼 "호출 직전에 새로 발급" 이 유지돼야 한다.

### `dispatch.py`

`type → JOB` 매핑만 남긴다. "이 `db` 로 커밋하는 service 를 부르지 마라" 경고는
"쓰기는 `apply` · `on_ai_error` 에서만. 거기서도 커밋은 큐가 한다" 로 바꾼다.

## 4. 워커 루프 — `app/worker/loop.py`

```
lease = queue.claim()                         # 없으면 쉰다(커넥션 없이)
try:
    with queue.read(lease) as db:      ctx = job.load(db, task)
    if Skip:                           with queue.complete(lease) as c: c.result = ctx.result
    else:
        try:    resp = job.call_ai(ctx, ai)           # 커넥션 0개
        except Exception as exc:
            if job.on_ai_error is None: raise
            with queue.complete(lease) as c: c.result = job.on_ai_error(c.db, task, ctx, exc)
        else:
            with queue.complete(lease) as c: c.result = job.apply(c.db, task, ctx, resp)
except LeaseLostError:                       # 다른 워커가 가져갔다. 기록하지 않는다
    log warning
except (KeyboardInterrupt, SystemExit):
    queue.release(lease); raise
except Exception as exc:
    queue.fail(lease, exc); log
```

- 파일 맨 위의 "커밋 시점이 이 파일의 전부다" 는 그대로 유효하다 — 이제 그 시점이 `complete` 다.
- `claim()` 자체가 터지면(DB 다운 등) 지금처럼 `QUEUE_POLL_INTERVAL_SEC` 만큼 쉬고 다시 돈다.

## 5. 실패·중복·종료

- **두 번 실행될 수 있는 유일한 경우**: A 가 lease 를 쥔 채 `QUEUE_LEASE_SEC` 넘게 멈추고 B 가 회수해 실행.
  AI 는 두 번 불릴 수 있다(비용 2배). 도메인 쓰기는 한 번만 반영된다 — A 의 `complete` 가 토큰 불일치로
  0행이 되어 `LeaseLostError` 로 롤백한다.
- **더블 탭 등 같은 작업의 중복 등록**은 지금처럼 job 의 도메인 재확인이 막는다. 바꾸지 않는다.
- **정상 종료**: SIGTERM 을 받으면 처리 중인 작업을 끝까지 간다(최대 ~45초). `docker stop` 기본 유예는
  10초라 그 뒤 SIGKILL → lease 만료 회수로 시도 1회를 태운다. `infra/docker-compose.be.yml` 의 worker 에
  `stop_grace_period: 60s` 를 둔다.
- `NonRetryableError`(AI 4xx) 는 지금처럼 곧바로 격리한다.

## 6. 같이 바꾸는 곳

| 파일 | 변경 |
|---|---|
| `app/models/enums.py` | `TaskStatus.PROCESSING`. docstring 의 "RUNNING 이 없다" 를 lease 설명으로. `is_in_flight` (PENDING 또는 PROCESSING) |
| `app/services/daily_feedback.py:67,135` · `app/services/insight.py:65,207` | `== PENDING` → `is_in_flight`. 처리 중을 "생성 중" 으로 보여 주고, 중복 등록 방지도 처리 중을 포함한다 |
| `scripts/queue_status.py` | 처리 중을 `pg_stat_activity` 근사 대신 `status = PROCESSING` 으로 센다. 만료 지난 lease 수도 보인다 |
| `scripts/smoke_queue_ai.py` | 새 큐 API 로 |
| `infra/docker-compose.be.yml` | worker `stop_grace_period: 60s` |
| `docs/superpowers/specs/2026-09-20-db-table-queue-design.md` | 맨 위에 "잠금 방식은 이 문서로 대체됨" 한 줄 |

## 7. 마이그레이션

head `05ef77bf2a6a` 다음.

- upgrade: `ALTER TYPE task_status ADD VALUE 'PROCESSING'` 을 `op.get_context().autocommit_block()` 안에서
  (추가한 트랜잭션 안에서는 새 값을 쓸 수 없다) → 두 컬럼 → 부분 인덱스.
- 기존 데이터 이전은 없다. 지금 구조에서 `PROCESSING` 행은 존재할 수 없다.
- downgrade: `PROCESSING` 행을 `PENDING` 으로 돌리고 인덱스·컬럼을 지운다. enum 값은 Postgres 가 지울 수
  없어 남긴다.
- 배포 순서: 마이그레이션 → 워커 교체. 옛 워커와 새 워커가 동시에 돌면 안 된다(옛 워커는 lease 를 모른다) —
  워커를 내린 뒤 올린다.

## 8. 테스트

- `test_task_queue.py` (다시 쓴다)
  - claim 이 커밋된다 — 다른 세션에서 `PROCESSING` · 토큰 · 만료 시각이 보인다
  - 두 세션이 같은 행을 집지 않는다
  - 만료 lease 회수 → `PENDING` + backoff, 상한이면 `FAILED`
  - `complete` 토큰 불일치 → `LeaseLostError`, 도메인 쓰기까지 롤백
  - `fail` 토큰 불일치 → 아무것도 바꾸지 않는다. `NonRetryableError` → 바로 `FAILED`
  - `release` → `PENDING`, attempts 원복
  - `read` 안에서 쓴 것이 반영되지 않는다
  - 실패 메시지 SQL 덤프 제거·길이 제한 (기존 테스트 유지)
- `test_worker_loop.py`
  - **`call_ai` 가 도는 동안 커넥션 풀 checked-out 이 0** — 이 변경의 목적을 고정한다
  - Skip · `None` 응답 · `on_ai_error` · `LeaseLostError` · 종료 신호 경로
- job 테스트 4개: `run(db, task, ai)` 호출부를 3단계를 실행하는 테스트 헬퍼로 바꾼다. 시나리오 단언은
  그대로 둔다 — 동작이 바뀌지 않았다는 증거다.
- 서비스 테스트: `PROCESSING` 일 때 "생성 중" 응답, 중복 등록 방지.

## 범위 밖

- 한 프로세스에서 스레드 N 개로 워커 늘리기 — 다음 작업. 이 변경으로 커넥션 제약이 없어진다.
- AI 호출 전체의 총 시간 상한(deadline).
- lease 연장(heartbeat) — AI 타임아웃이 lease 보다 충분히 짧아 지금은 필요 없다.
- 작업 종류별 워커 풀 분리 — 성능 측정 결과를 보고 정한다.
