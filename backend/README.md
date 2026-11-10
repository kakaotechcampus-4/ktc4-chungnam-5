# backend — Backend

GLP-1 포즈 단계별 식사 코치의 **백엔드**. 공개 API·내부 API·비동기 워커·도메인 로직을 담당한다.

- 스택: **Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL** (D2)
- 컨테이너 2개로 뜬다: `api`, `worker` (D4)

---

## 이 파트가 지켜야 하는 절대 규칙

1. **의료 판단 금지.** 포즈 단계는 분류(`PRE_DOSE`/`INITIAL`/`TITRATION`/`MAINTENANCE`)까지만.
   용량 증량·감량·단약·처방 판단은 하지 않는다. 감지되면 차단 + `medical_handoff_logs` 기록.
2. **Rule Engine(Q/Q/S 채점기)은 순수 함수.** DB·네트워크 의존 0.
   `(식사 성분, 개인 baseline, 단계 프로파일) → (Q, Q, S)` 만 계산한다. 이 프로젝트의 차별점이 여기 있다.
3. **`total_score` · `*_weight` 컬럼을 만들지 않는다.** 단계별 차이는 곱셈 가중치가 아니라
   **단계별 기준선(목표 범위)의 엄격함**으로 구현한다 (D9).
4. **Quantity는 절대 섭취량·BMR 목표 kcal 대비가 아니다.** 개인 baseline(평소 한 끼) 대비 **감소폭** (D7).
5. **`services/` 하위 모듈은 서로 직접 참조하지 않는다.** 조합은 `api/` · `internal/` · `worker/` 레이어에서만.
   DB 접근은 `crud/`를 통해서만 한다. **트랜잭션 경계(`commit`/`rollback`)는 `services/`가 정한다 —
   그 외의 세션 조작(쿼리·직접 add 등)은 하지 않는다.** `crud/`는 `add`/`flush`까지만 하고 커밋하지 않는다
   (예: `services/user.py`의 `create_profile`이 "유저 생성 + 첫 체중 기록"을 한 트랜잭션으로 묶으려고
   `db.commit()`을 부른다).
6. **포즈 정보는 민감 건강정보.** 로그에 약제·용량·이미지 키를 남기지 않는다.
   모든 공개 API는 본인 데이터만 조회.

> 🚨 **현재 공개 API 는 규칙 6을 지키지 않는다.** `POST /users/profile` 을 뺀 모든
> 공개 엔드포인트(`users` · `meals` · `meal_items` · `medications` · `user_states` ·
> `evaluations` · `feedbacks` · `home` · `dashboard` · `insights` · `nutrition` · `chat`)가
> `get_current_user_id` 로 `X-User-Id` 헤더의 UUID 를 그대로 사용자 식별자로 신뢰한다 —
> **이건 인증이 아니라 인증 이음새(seam)다.** 헤더에 아무 UUID나 넣으면 그 사용자의
> 식사·체중·투약 기록(민감 건강정보)을 읽고 쓸 수 있다. 실제 인증이 붙기 전까지
> **공개 API 를 어떤 공개 환경에도 배포하지 않는다.**
> (`APP_ENV=production` 이면 `get_current_user_id` 가 요청을 막는다 — `app/core/config.py` ·
> `app/core/deps.py` 참고.) FE 는 그동안 모든 호출에 이 `X-User-Id` 헤더를 붙여야 한다.

---

## 디렉터리 구조

```
backend/app/
├── main.py                엔트리포인트
├── worker_main.py         Worker 엔트리포인트
├── core/                  설정 · 사용자 식별 · 응답 래퍼 · 에러 코드
├── db/                    base · session
├── models/                SQLAlchemy 모델
├── schemas/               Pydantic 스키마
├── crud/                  DB 접근만
├── services/          ★  비즈니스 로직 (단계 판정 · 상태 머신 · 평가 · 가드레일)
├── api/v1/endpoints/      공개 REST
├── internal/v1/       ★  AI Service 전용 REST — 서비스 토큰 (아직 엔드포인트 없음)
├── worker/            ★  큐 소비 + 스케줄 배치
├── infra/             ★  S3 · 큐 · FCM · AI HTTP
└── tests/
```

- `services/` 안에서 **상태(baseline 저장·갱신)와 계산(채점기)을 분리**한다.
  상태는 `crud/`를 거쳐 DB가 들고, 채점기는 현재 baseline을 인자로 받는 순수 함수다.
- `infra/`는 `Protocol`(또는 ABC) 뒤에 구현을 숨긴다 (D13):
  - `FileStorage` → `S3Storage`(prod) / `LocalDiskStorage`(로컬 개발)
  - `TaskQueue` → `DbTaskQueue` (PostgreSQL `task_queue` 테이블. 로컬·프로덕션 같은 구현)
  - 도메인은 어느 구현이 붙는지 모른다.

---

## meals 상태 머신

```
ANALYZING → REVIEW_REQUIRED → EVALUATED   (+ feedbackStatus 별도)
                    ↓
                 FAILED
```

이 4개가 전부다. `RECOGNIZED` · `USER_CONFIRMED` 같은 중간 상태를 추가하지 않는다.

**AI가 죽어도 Q/Q/S는 남는다.** 점수(Rule Engine)와 피드백 문장(AI)은 분리돼 있어,
피드백 생성 실패는 부분 실패로 처리한다.

---

## 실행

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# python3.12 -m venv .venv && source .venv/bin/activate   # macOS · Linux

pip install -r requirements.txt
cp .env.example .env

# PostgreSQL — 호스트 5433 에 뜬다 (5432 는 로컬 설치형 PG 가 쓰는 경우가 많다)
cd ../infra && docker compose up -d && cd ../backend

# 스키마 적용
alembic upgrade head

# 공공 영양DB 시드 복원 (336,351건)
docker exec -i glp1-db pg_restore -U glp1 -d glp1_dev --data-only --no-owner < ../infra/seed/food_refs_20260828.dump

# AI 스텁 — Worker 가 쓴다. 큐는 위 PostgreSQL 의 task_queue 테이블이라 따로 안 띄운다.
# 자세한 건 ../infra/README.md
cd ../infra && docker compose -f docker-compose.ai-stub.yml up -d && cd ../backend

# API — http://127.0.0.1:8000/docs
uvicorn app.main:app --reload

# Worker — 별도 프로세스다. 창을 하나 더 연다
python -m app.worker_main

pytest
```

### 세팅 확인

위 절차가 제대로 끝났는지 확인한다. 셋 다 기대값과 같아야 한다.

```bash
# 1. 테이블 수 — 23 이 나와야 한다
docker exec glp1-db psql -U glp1 -d glp1_dev -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"

# 2. 시드 건수 — GENERAL 14991 / PROCESSED 261101
docker exec glp1-db psql -U glp1 -d glp1_dev -c "SELECT category, count(*) FROM food_refs GROUP BY category;"

# 3. 모델과 DB 가 어긋나지 않았는지
alembic check
```

| 확인 | 기대값 |
| --- | --- |
| 테이블 수 | **23** — 도메인 테이블 21개 + 작업 큐 `task_queue` 1개 + Alembic 이 쓰는 `alembic_version` 1개 |
| `food_refs` | GENERAL 14,991 · PROCESSED 261,101 (합 276,092) |
| `alembic check` | `No new upgrade operations detected.` |

> 테이블 목록을 직접 보려면 `docker exec -it glp1-db psql -U glp1 -d glp1_dev -c "\dt"`.
> 한글이 깨지면 PowerShell 에서 `chcp 65001` 을 한 번 실행한다.

> **`alembic check` 가 `Target database is not up to date` 로 실패하면** DB 가 최신
> 마이그레이션까지 올라와 있지 않다는 뜻이다. `alembic upgrade head` 를 먼저 돌린다.
> 검사는 항상 스키마 적용 **뒤에** 한다.

### 공공 영양DB 적재

`food_refs` 는 식약처 식품영양성분DB 복제본이다. 원본 336,351건 중 **이름이 같은 음식은
`(category, 이름)` 마다 한 행으로 합치고 성분은 100g 당 평균으로 둔다**(276,092건). 일반음식과
가공식품은 이름이 같아도 따로 남는다. 엑셀 원본은 204MB 라 레포에 넣지 않고, **압축
덤프(7.0MB)를 `infra/seed/` 에 커밋해 공유한다.**

> 병합 전 덤프를 이미 복원해 둔 DB 는 `alembic upgrade head` 만 돌리면 된다 —
> 마이그레이션 `8d3f6a1c5e27` 이 같은 규칙으로 합치고, 지워지는 행을 가리키던
> `meal_items` · 채팅 카드 · 끼니 피드백 제안의 id 도 대표 행으로 옮긴다.

#### 팀원 — 덤프 복원 (권장)

스키마를 올린 뒤 데이터만 복원한다. 엑셀 다운로드도 적재도 필요 없다.

```powershell
docker exec -i glp1-db pg_restore -U glp1 -d glp1_dev --data-only --no-owner < ..\infra\seed\food_refs_20260828.dump
```

```bash
# macOS · Linux
docker exec -i glp1-db pg_restore -U glp1 -d glp1_dev --data-only --no-owner < ../infra/seed/food_refs_20260828.dump
```

확인:

```bash
docker exec glp1-db psql -U glp1 -d glp1_dev -c "SELECT category, count(*) FROM food_refs GROUP BY category;"
# PROCESSED 261101 / GENERAL 14991
```

#### 엑셀 원본에서 재적재 (데이터셋이 갱신됐을 때만)

```bash
python -m scripts.load_food_refs GENERAL   "<경로>/20260828_음식DB_19617건.xlsx"
python -m scripts.load_food_refs PROCESSED "<경로>/20260828_가공식품DB_316734건.xlsx"
```

적재하면서 같은 이름을 평균으로 합친다(위 규칙과 동일). `ON CONFLICT DO UPDATE` 라 여러 번 돌려도 안전하고, 트랜잭션 하나로 처리해 중간에
실패하면 아무것도 들어가지 않는다. 가공식품DB 적재에 몇 분 걸린다.

새 덤프를 만들어 공유하려면:

```bash
docker exec glp1-db pg_dump -U glp1 -d glp1_dev -t food_refs --data-only -Fc -Z9 > ../infra/seed/food_refs_<날짜>.dump
```

> **원본 데이터에 결측이 많다.** `외식(프랜차이즈 등 업체 제공 영양정보)` 15,225건은
> 지방 87% · 탄수화물 84% 가 비어 있다. 업체가 열량·단백질·나트륨까지만 제출하기 때문이다.
> 가공식품DB 316,734건은 대부분 채워져 있다. **Rule Engine 의 Quality 채점은 영양소가
> 없는 경우를 처리해야 한다** — `qqs_evaluations.quality_score` 를 NULL 허용으로 둔 게 이 때문이다.

### 스키마 변경

테이블 정의의 진실은 `app/models/` 하나다. DDL 을 psql 에 직접 치지 않는다.

```bash
# 모델을 고친 뒤
alembic revision --autogenerate -m "설명"   # 마이그레이션 생성 → 반드시 눈으로 검토
alembic upgrade head                        # 적용
alembic check                               # 모델과 DB 가 어긋났는지 확인
```

> ENUM 을 새로 추가하는 마이그레이션은 생성된 파일을 손봐야 한다. Alembic 은 같은
> ENUM 을 여러 테이블이 쓰면 `CREATE TYPE` 을 중복 실행하고, downgrade 에서는 타입을
> 지우지 않아 재적용이 깨진다. 초기 마이그레이션의 `ENUM_TYPES` 처리 방식을 따른다.

## 큐 사용법

비동기 작업은 PostgreSQL 의 `task_queue` 테이블을 거친다. 큐 컨테이너는 없다.

### 넣기 — 도메인과 같은 트랜잭션

```python
from app.infra.queue import enqueue

def create_meal(db: Session, ...) -> MealResponse:
    meal = crud.meal.create(db, ...)
    enqueue(db, "meal.analyze", {
        "mealId": str(meal.id),
        "mealType": "LUNCH",
        "eatenAt": "2026-08-21T12:40:00+09:00",
        "stage": "MAINTENANCE",
        "rawText": "김밥 한 줄",
    })
    db.commit()     # 식사와 작업이 한 번에 들어간다
    return ...
```

`enqueue` 는 **커밋하지 않는다.** 쓰던 세션에 INSERT 만 하므로 도메인 변경과 작업
등록이 원자적이다 — 커밋이 실패하면 둘 다 없고, 성공하면 둘 다 있다. SQS 를 쓸 때
지켜야 했던 "커밋이 먼저다" 규칙은 이제 없다.

### 꺼내기 — lease 로 빌린다

```python
lease = queue.claim()          # PROCESSING · 토큰 · 만료 시각을 쓰고 곧바로 커밋
with queue.read() as db:       # load: 읽기만. 끝나면 롤백
    ...
...                            # call_ai: 세션 없음 — AI 를 기다리는 동안 커넥션 0
with queue.complete(lease) as done:
    done.db                    # apply: 도메인 쓰기
    done.result = {...}        # DONE 과 함께 result 컬럼에 들어간다
queue.fail(lease, exc)         # 실패: PENDING(재시도) 또는 FAILED(격리)
queue.release(lease)           # 종료 신호: 시도를 세지 않고 PENDING 으로
```

`SELECT … FOR UPDATE SKIP LOCKED` 로 한 행을 집되 **집는 순간 커밋한다.** AI 를 기다리는
동안 트랜잭션도 행 잠금도 커넥션도 쥐지 않는다. 완료·실패·반납은 `lease_token` 이 자기
것일 때만 행을 바꾼다. 워커가 죽어 lease(`QUEUE_LEASE_SEC`, 120초)가 지나면 다음 `claim`
이 회수한다.

**배포 순서**: 워커를 내린다 → 마이그레이션 → API 교체 → 새 워커를 올린다. 옛 API 는
`PROCESSING` 을 몰라(네이티브 enum) 그 행을 읽으면 `LookupError` 가 나므로 새 워커보다
먼저 바꾼다. 옛 워커가 행 잠금을 쥔 채면 `ADD COLUMN` 이 막히므로 먼저 내린다.

**동시 처리**: 워커 프로세스 하나가 폴링 루프를 스레드 `QUEUE_WORKER_THREADS`(기본 4)개로
돌린다. 작업 시간이 거의 AI 응답 대기라 스레드로 충분하고, 같은 작업을 두 스레드가 집지
않는 건 SKIP LOCKED + lease 가 보장한다. 스레드 하나는 한순간에 커넥션을 하나씩만 쓰므로
`DB_POOL_SIZE + DB_MAX_OVERFLOW` 를 넘길 수 없다(기동 시 확인). 값은 LLM API 의 동시
처리량을 재 보고 맞춘다. 스레드 하나가 죽으면 나머지도 멈추고 프로세스가 0 이 아닌 코드로
끝난다 — compose 가 다시 띄운다.

### 커밋 시점 — 여기가 전부다

| 단계 | 큐가 하는 일 |
|---|---|
| `claim` | `PROCESSING`, `attempts+1`, 토큰, `lease_expires_at` 커밋 |
| `complete` 정상 종료 | 도메인 쓰기 + `DONE` · `result` · `finished_at` 을 한 트랜잭션으로 커밋. 토큰이 안 맞으면 `LeaseLostError` 로 전부 롤백 |
| `fail` | `last_error` · `next_run_at`(30s→60s), 3번째로 집힌 시도면 `FAILED` |
| lease 만료 | 다음 `claim` 이 `PENDING`(backoff) 또는 `FAILED` 로 회수 |

`attempts` 는 **집힌 횟수**다. 워커를 죽이는 작업(OOM·SIGKILL)도 상한에 걸리게 하려고
집을 때 센다. `app/worker/loop.py` 의 `process_one` · `run` 이 이 구조이고,
`app/tests/test_worker_loop.py` 가 "AI 호출 중 커넥션 0" 과 실패 경로를 검증한다.

`FAILED` 는 DLQ 자리다. 자동으로 되살리지 않는다 — 3번 실패한 작업은 대개 코드나
데이터가 잘못된 것이라 사람이 원인을 보고 다시 넣는다.

```sql
UPDATE task_queue SET status='PENDING', attempts=0, next_run_at=now() WHERE id='…';
```

상태는 `python -m scripts.queue_status` 로 본다. 처리 중은 `PROCESSING` 으로 바로 보이고,
lease 가 지난 것도 따로 센다.

### 작업을 하나 붙이려면

1. `app/worker/jobs/` 에 파일을 만들고 `load` · `call_ai` · `apply`(필요하면
   `on_ai_error`)와 `JOB = Job(...)` 을 둔다. 계약은 `app/worker/job.py` 에 있다
2. `app/worker/dispatch.py` 의 `_JOBS` 에 한 줄 더한다
3. `_NOT_IMPLEMENTED` 에서 그 타입을 지운다

`load` 가 넘기는 값은 ORM 객체가 아닌 순수 데이터여야 한다 — `load` 의 세션은 곧 닫힌다.
`call_ai` 는 세션을 받지 않는다. 쓰기는 `apply` · `on_ai_error` 에서만 한다.

**`apply` 에서 부르는 `services/`·`crud/` 함수는 커밋하면 안 된다.** 커밋은 큐가 DONE 과
함께 한다 — 도중에 커밋하는 service 를 부르면 lease 를 잃었을 때 그 변경을 되돌릴 수 없다.
커밋하는 service 가 있으면 커밋 없는 버전으로 쪼개서 쓴다.

## 환경변수

`.env.example` 참고. `core/`의 Pydantic `BaseSettings`로 로드해, 없거나 형식이 틀리면
**부팅 시점에 실패**하도록 검증한다 (런타임에 이상하게 죽는 것보다 기동 시 명확히 죽는 게 낫다).

| 키                                        | 설명                                                                   |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `DB_URL` · `DB_USER` · `DB_PASSWORD`      | PostgreSQL 접속. `DB_URL` 은 자격증명을 뺀 `host:port/dbname` 형태     |
| `DB_ECHO`                                 | SQL 로그 출력. 로컬 디버깅할 때만 `true`. 기본 false                   |
| `DB_POOL_SIZE` · `DB_MAX_OVERFLOW`        | 커넥션 풀. 기본 5 · 10. 합이 `QUEUE_WORKER_THREADS` 이상이어야 한다    |
| `APP_ENV`                                 | `local` \| `production`. production 이면 `X-User-Id` 이음새가 막힌다   |
| `AI_SERVICE_BASE_URL`                     | AI 컨테이너 주소. 로컬은 `http://localhost:8001`                       |
| `AI_TIMEOUT_SEC`                          | 기본 45. 분석이 10~30초 걸린다                                         |
| `AI_STUB_SCENARIO`                        | ai-stub 의 실패 경로를 부르는 손잡이. **로컬 전용, 평소엔 비움**       |
| `STORAGE_TYPE`                            | `s3` \| `local`. 기본 `local`                                          |
| `LOCAL_STORAGE_DIR`                       | `local` 일 때 사진 저장 경로. 기본 `var/media`                         |
| `LOCAL_STORAGE_BASE_URL`                  | 워커가 AI 에 넘기는 `imageUrl` 의 앞부분. **AI 가 접근할 수 있는 주소** |
| `S3_BUCKET`                               | `s3` 일 때 필수. **`glp1-team-*` 패턴이어야 함.** 인스턴스 역할 권한이 이 패턴으로 한정 |
| `QUEUE_POLL_INTERVAL_SEC`                 | 빈 큐일 때 쉬는 시간. 기본 1.0                                         |
| `QUEUE_MAX_ATTEMPTS`                      | 이 횟수만큼 실패하면 FAILED 로 격리. 기본 3                            |
| `QUEUE_BACKOFF_BASE_SEC`                  | 재시도 지연 기준. 기본 30 (30s → 60s)                                  |
| `QUEUE_LEASE_SEC`                         | 집은 작업의 lease 길이. 기본 120                                       |
| `QUEUE_WORKER_THREADS`                    | 워커 프로세스 하나의 폴링 스레드 수. 기본 4                            |

아직 Settings 에 없는 키 — 담당 티켓에서 추가한다: `KAKAO_CLIENT_ID` ·
`KAKAO_CLIENT_SECRET`(D3) · `INTERNAL_SERVICE_TOKEN`(`/internal/v1` 호출용, AI Service 와 공유) · `FCM_*`(푸시 발송).

> 🚨 AWS 액세스 키는 두지 않는다. 서버는 **인스턴스 역할**로 S3 에 접근한다.

---

## 테스트 전략

| 대상         | 방식                                                                                                                            |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| Rule Engine  | **순수 함수 단위 테스트.** 입력→기대 점수 표로 고정(`pytest.mark.parametrize`). 단계를 바꾸면 점수가 실제로 달라지는지 검증(R1) |
| 레이어 경계  | `services/*` 상호 참조 금지 · `services` → `crud` 단방향. **아직 도구로 강제하지 않는다**(import-linter 미도입) — 리뷰에서 지킨다 |
| API          | `httpx.ASGITransport` + Testcontainers(Postgres)                                                                                |
| Worker 루프  | 가짜 큐·가짜 AI 로 **커밋 시점**만 검증 — 실패한 작업을 DONE 으로 커밋하지 않는지(`test_worker_loop.py`). 외부 의존 0 |
| 작업 큐      | 실제 Postgres 로 lease·SKIP LOCKED·재시도·격리·회수 검증(`test_task_queue.py`). AI 호출 중 커넥션 0 을 `test_worker_loop.py` 가 확인한다 |
| infra 추상화 | 로컬 구현으로 테스트, 외부 의존 0                                                                                               |
