# be — Backend

GLP-1 포즈 단계별 식사 코치의 **백엔드**. 공개 API·내부 API·비동기 워커·도메인 로직을 담당한다.

- 스택: **Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL** (D2)
- 컨테이너 2개로 뜬다: `api`, `worker` (D4)
- 상세 설계: [`../docs/architecture.md`](../docs/architecture.md) · 결정 근거: [`../docs/decisions.md`](../docs/decisions.md)

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
   DB 접근은 `crud/`를 통해서만 한다 — `services/`가 세션을 직접 다루지 않는다.
6. **포즈 정보는 민감 건강정보.** 로그에 약제·용량·이미지 키를 남기지 않는다.
   모든 공개 API는 JWT + 본인 데이터만 조회.

---

## 디렉터리 구조

```
be/app/
├── main.py                엔트리포인트
├── worker_main.py         Worker 엔트리포인트
├── core/                  설정 · JWT · 응답 래퍼 · 에러 코드
├── db/                    base · session
├── models/                SQLAlchemy 모델
├── schemas/               Pydantic 스키마
├── crud/                  DB 접근만
├── services/          ★  비즈니스 로직 (단계 판정 · 상태 머신 · 평가 · 가드레일)
├── api/v1/endpoints/      공개 REST — JWT
├── internal/v1/       ★  AI Service 전용 REST — 서비스 토큰
├── worker/            ★  큐 소비 + 스케줄 배치
├── infra/             ★  S3 · SQS · FCM · AI HTTP
└── tests/
```

- `services/` 안에서 **상태(baseline 저장·갱신)와 계산(채점기)을 분리**한다.
  상태는 `crud/`를 거쳐 DB가 들고, 채점기는 현재 baseline을 인자로 받는 순수 함수다.
- `infra/`는 `Protocol`(또는 ABC) 뒤에 구현을 숨긴다 (D13):
  - `FileStorage` → `S3Storage`(prod) / `LocalDiskStorage`(로컬 개발)
  - `TaskQueue` → `SqsQueue`(prod) / `LocalQueue`(로컬 개발)
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

# API — http://127.0.0.1:8000/docs
uvicorn app.main:app --reload

# ── 아래는 아직 없다 (구현되면 주석을 푼다) ──────────────────
# python -m app.worker_main        # worker_main.py 미작성
# pytest                           # pytest 미설치 · 테스트 미작성
```

### 공공 영양DB 적재

`food_refs` 는 식약처 식품영양성분DB 복제본이다(336,351건). 엑셀 원본은 204MB 라 레포에
넣지 않고, **압축 덤프(7.9MB)를 `infra/seed/` 에 커밋해 공유한다.**

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
# PROCESSED 316734 / GENERAL 19617
```

#### 엑셀 원본에서 재적재 (데이터셋이 갱신됐을 때만)

```bash
python -m scripts.load_food_refs GENERAL   "<경로>/20260828_음식DB_19617건.xlsx"
python -m scripts.load_food_refs PROCESSED "<경로>/20260828_가공식품DB_316734건.xlsx"
```

`ON CONFLICT DO UPDATE` 라 여러 번 돌려도 안전하고, 트랜잭션 하나로 처리해 중간에
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

## 환경변수

`.env.example` 참고. `core/`의 Pydantic `BaseSettings`로 로드해, 없거나 형식이 틀리면
**부팅 시점에 실패**하도록 검증한다 (런타임에 이상하게 죽는 것보다 기동 시 명확히 죽는 게 낫다).

| 키                                        | 설명                                                                   |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `DB_URL` · `DB_USER` · `DB_PASSWORD`      | PostgreSQL 접속. `DB_URL` 은 자격증명을 뺀 `host:port/dbname` 형태     |
| `JWT_SECRET`                              | 액세스 토큰 서명 키                                                    |
| `KAKAO_CLIENT_ID` · `KAKAO_CLIENT_SECRET` | 카카오 OAuth (D3)                                                      |
| `INTERNAL_SERVICE_TOKEN`                  | `/internal/v1` 호출용. AI Service와 공유                               |
| `AI_SERVICE_BASE_URL`                     | AI 컨테이너 주소                                                       |
| `STORAGE_TYPE`                            | `s3` \| `local`                                                        |
| `S3_BUCKET`                               | **`glp1-team-*` 패턴이어야 함.** 인스턴스 역할 권한이 이 패턴으로 한정 |
| `QUEUE_TYPE`                              | `sqs` \| `local`                                                       |
| `SQS_QUEUE_URL` · `SQS_DLQ_URL`           | 비동기 파이프라인                                                      |
| `FCM_*`                                   | 푸시 발송                                                              |

> 🚨 AWS 액세스 키는 두지 않는다. 서버는 **인스턴스 역할**로 S3·SQS에 접근한다.

---

## 테스트 전략

| 대상         | 방식                                                                                                                            |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| Rule Engine  | **순수 함수 단위 테스트.** 입력→기대 점수 표로 고정(`pytest.mark.parametrize`). 단계를 바꾸면 점수가 실제로 달라지는지 검증(R1) |
| 레이어 경계  | import-linter — `services/*` 상호 참조 금지 · `services` → `crud` 단방향을 CI에서 강제                                          |
| API          | `httpx.ASGITransport` + Testcontainers(Postgres)                                                                                |
| infra 추상화 | 로컬 구현으로 테스트, 외부 의존 0                                                                                               |

---

## 참고

- API 명세 · 스키마 · 단계 프로파일은 [`../contracts/`](../contracts/)가 **코드보다 먼저**다.
- 개발 규칙: [`../CLAUDE.md`](../CLAUDE.md)

## 테이블 명세

> 아래는 읽기용 요약이다. **실제 정의는 `app/models/` 가 진실의 출처**이고, 스키마는
> `alembic/versions/` 의 마이그레이션으로만 바뀐다. 모든 `TIMESTAMP` 는 `TIMESTAMPTZ` 다.

## `users`

로그인한 사용자를 식별하고 닉네임 등 기본 정보를 제공한다. 다른 대부분의 데이터가 `user_id`를 통해 이 사용자와 연결된다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 사용자 ID |
| auth_provider | VARCHAR | 로그인 방식 |
| provider_user_id | VARCHAR | 공급자쪽 사용자 식별자. 카카오는 이메일이 선택 동의라 이 값으로 계정을 찾는다 |
| email | VARCHAR, NULL | 로그인 계정. 동의하지 않으면 없다 |
| nickname | VARCHAR | 표시 이름 |
| restrictions | JSONB, DEFAULT `[]` | 알레르기·못 먹는 음식 |
| created_at | TIMESTAMP | 가입일 |
| updated_at | TIMESTAMP | 수정일 |
| fcm_token | VARCHAR, NULL | FCM 푸시 토큰. 미등록·만료 시 NULL |
| **fcm_token_updated_at** | TIMESTAMP, NULL | 토큰 갱신 시각 |
| notification_enabled | BOOLEAN, DEFAULT true | 알림 수신 동의 (기기가 아닌 사용자 설정) |
| baseline_meal_kcal | DECIMAL, NOT NULL | **평소 한 끼 열량 (kcal).** Quantity 감소폭의 분모 |

### 제약·인덱스

| 대상 | 종류 | 내용 | 이유 |
| --- | --- | --- | --- |
| fcm_token | **부분 UNIQUE** | `UNIQUE (fcm_token) WHERE fcm_token IS NOT NULL` | 같은 기기에 두 계정이 붙으면 **남의 식사 알림이 뜬다.** |
| auth_provider + provider_user_id | UNIQUE | `UNIQUE (auth_provider, provider_user_id)` | 같은 소셜 계정으로 두 번 가입되는 걸 막는다 |

---

## `user_goals`

목표 체중을 표시하고 현재 체중과 비교해 목표 진행 상황을 보여주는 데 사용한다.(추후 사용될 수 있는 테이블)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 목표 ID |
| user_id | UUID FK | 사용자 ID |
| target_weight_kg | DECIMAL | 목표 체중 |
| status | ENUM, DEFAULT ACTIVE | ACTIVE / COMPLETED / CANCELLED |
| created_at | TIMESTAMP | 생성 시각 |
| updated_at | TIMESTAMP | 수정 시각 |

> 진행 중인 목표는 사용자당 하나다 — `UNIQUE (user_id) WHERE status = 'ACTIVE'`.

## `user_states`

사용자가 입력한 현재 체중, 식욕, GI 증상을 저장한다. 최신 상태 표시 및 기간별 체중·식욕 변화 분석에 활용한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 상태 기록 ID |
| user_id | UUID FK | 사용자 ID |
| appetite_level | INT | 현재 식욕 정도 |
| weight_kg | DECIMAL | 입력 시점 체중 |
| gi_symptoms | JSONB, DEFAULT `[]` | 위장관 증상 목록 및 정도 |
| note | TEXT | 사용자 GI 증상 추가 설명 |
| recorded_at | TIMESTAMP | 상태 기록 시각 |
| created_at | TIMESTAMP | 생성 시각 |

> 조회 인덱스: `(user_id, recorded_at DESC)` — "최신 상태"와 기간 조회가 같은 인덱스를 탄다.

## `medical_handoff_logs`

GI 증상이나 피드백 생성 과정에서 의료 판단이 필요한 상황이 감지되면 기록한다.(추후 사용될 수 있는 테이블)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 핸드오프 ID |
| user_id | UUID FK | 사용자 ID |
| meal_id | UUID FK, NULL | 특정 식사와 관련된 경우 |
| user_state_id | UUID FK, NULL | 증상 기록에서 발생한 경우 |
| trigger_type | ENUM | DOSAGE_QUESTION / DISCONTINUATION_QUESTION / PRESCRIPTION_QUESTION / SEVERE_GI_SYMPTOM / OTHER |
| trigger_reason | TEXT | 감지된 이유 |
| original_input | TEXT | 사용자의 원본 질문/입력 |
| status | ENUM, DEFAULT PENDING | PENDING / REVIEWED / RESOLVED |
| reviewer_note | TEXT | 검수 결과 |
| detected_at | TIMESTAMP | 감지 시각 |
| reviewed_at | TIMESTAMP, NULL | 검수 시각 |

## `medication_records`

약물명, 용량, 투약 회차, 현재 단계를 저장한다. 식사 평가 시 "현재 사용자가 어느 투약 단계인가"를 결정하는 핵심 Context로 사용한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 투약 기록 ID |
| user_id | UUID FK | 사용자 ID |
| drug_name | VARCHAR | 약물명 |
| dose_mg | DECIMAL | 해당 시점 투약 용량 |
| injection_count | INT | 투약 회차 |
| stage | ENUM | INITIAL / TITRATION / MAINTENANCE/PRE_DOSE |
| effective_from | DATE | 해당 투약 상태 적용 시작일 |
| effective_to | DATE, NULL | 종료일, 현재 상태면 NULL |
| created_at | TIMESTAMP | 기록 생성 시각 |
| updated_at | TIMESTAMP | 수정 시각 |

> `UNIQUE (user_id) WHERE effective_to IS NULL` — "현재 단계"인 행이 둘이면 단계 판정이 모호해진다.

---

---

## `medication_snapshots`

한 끼 식사에 매칭될 투약정보 스냅샷. **생성 후 변경하지 않는다** — 그래서 `updated_at` 이 없다.
사용자가 나중에 과거 투약 기록을 고쳐도 이미 평가된 식사의 단계 맥락은 변하지 않는다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 스냅샷 ID |
| user_id | UUID FK | 사용자 ID |
| source_record_id | UUID FK, NULL | 어느 `medication_records` 에서 떠왔는지. 원본이 지워져도 스냅샷은 남는다 |
| drug_name | VARCHAR, NULL | 약물명. **PRE_DOSE 는 투약 기록이 없어 NULL** |
| dose_mg | DECIMAL, NULL | 해당 시점 투약 용량 |
| injection_count | INT, NULL | 투약 회차 |
| stage | ENUM | PRE_DOSE / INITIAL / TITRATION / MAINTENANCE |
| created_at | TIMESTAMP | 기록 생성 시각 |

---

## `meals`

한 끼 자체를 나타내는 중심 테이블이다. 사진/텍스트 입력, 식사 시간, 식사 종류, 분석 진행 상태 등을 저장한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 식사 ID |
| user_id | UUID FK | 사용자 ID |
| medication_snapshot_id | UUID FK | 식사 당시 투약 단계. PRE_DOSE 사용자도 빈 스냅샷이 하나 붙는다 |
| meal_type | ENUM | BREAKFAST / LUNCH / DINNER / SNACK |
| image_key | TEXT, NULL | S3 오브젝트 **키**. presigned URL 은 런타임에 만든다|
| raw_text | TEXT, NULL | 사용자 입력 원문 |
| eaten_at | TIMESTAMP | 실제 식사 시각 |
| status | ENUM, DEFAULT ANALYZING | 식사 처리 상태 |
| created_at | TIMESTAMP | 등록 시각 |

### Status

```
ANALYZING
REVIEW_REQUIRED
EVALUATED
FAILED
```

> `CHECK (image_key IS NOT NULL OR raw_text IS NOT NULL)` — 사진도 텍스트도 없는 식사는 분석할 게 없다.
> 조회 인덱스: `(user_id, eaten_at DESC)`.

---

## `meal_items`

사진에서 인식된 각각의 음식과 양을 저장한다. 예를 들어 한 끼 사진에서 밥, 고기, 계란이 인식되면 3개의 `meal_item`이 생성된다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 음식 ID |
| meal_id | UUID FK | 식사 ID |
| food_ref_id | VARCHAR FK, NULL | 공공 DB 음식 ID. AI 가 매칭하지 못하면 NULL |
| original_food_name | VARCHAR | 최초 AI 추정 음식명 |
| display_name | VARCHAR | 최종 확정 음식명 |
| estimated_amount_g | DECIMAL | AI 추정 섭취량 |
| confirmed_amount_g | DECIMAL, NULL | 사용자 확인·수정 섭취량. 확인 전에는 NULL |
| confidence | DECIMAL | AI 분석 신뢰도. `CHECK (0 ~ 1)` |
| source | ENUM, DEFAULT MODEL | MODEL / USER |
| raw_ai_result | JSONB | AI 원본 결과 |

---

## `user_corrections`

AI가 인식한 음식명이나 양을 사용자가 수정했을 때 변경 전/후 값을 기록한다. 이후 AI 분석 성능 평가에 활용될 수 있다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 수정 ID |
| meal_item_id | UUID FK | 대상 음식 |
| original_value | JSONB | AI 최초 추정값 |
| corrected_value | JSONB | 사용자 수정값 |
| corrected_at | TIMESTAMP | 수정 시각 |

---

## `food_refs`

공공 영양 DB 복제본

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | VARCHAR PK | 공공 DB 음식 ID |
| name | VARCHAR | 표준 음식명 |
| category | ENUM, NULL | PROCESSED(가공식품) / GENERAL(일반음식). 공공 DB 적재 시 미분류면 NULL |
| origin_type | VARCHAR | 식품기원명 |
| serving_size | DECIMAL | 영양성분함량기준량 |
| calories | DECIMAL | 열량 |
| carbohydrate_g | DECIMAL | 탄수화물 |
| protein_g | DECIMAL | 단백질 |
| fat_g | DECIMAL | 지방 |
| fiber_g | DECIMAL | 식이섬유 |
| cholesterol_mg | DECIMAL | 콜레스테롤 (mg — `sodium_mg` 와 단위를 맞췄다) |
| saturated_fat_g | DECIMAL | 포화지방산 |
| trans_fat_g | DECIMAL | 트랜스지방산 |
| sodium_mg | DECIMAL | 나트륨 |
| dataset_version | VARCHAR | 데이터 버전 |

---

## `satiety_logs`

사용자가 입력한 식전·식후 포만감 및 다시 허기를 느낀 시간을 저장한다. 이후 Satiety 평가와 개인별 포만감 변화 분석에 활용한다

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 포만감 기록 ID |
| meal_id | UUID FK | 식사 ID |
| satiety_before | INT | 식사 전 포만감(%). `CHECK (0 ~ 100)` |
| satiety_after | INT | 식사 후 포만감(%). `CHECK (0 ~ 100)` |
| hunger_return_minutes | INT | 다시 허기를 느끼기까지 시간 |
| user_comment | TEXT | 사용자 추가 의견 |
| logged_at | TIMESTAMP | 입력 시각 |

> `meal_id` 는 UNIQUE — 식사당 포만감 기록 하나.

---

## `qqs_evaluations`

식사별 Quantity·Quality·Satiety 점수를 저장한다. 화면의 Q/Q/S 그래프 및 기간별 점수 추이의 원본 데이터가 된다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 평가 ID |
| meal_id | UUID FK, UNIQUE | 식사 ID |
| stage_at_evaluation | ENUM | 평가 당시 투약 단계 |
| quantity_score | DECIMAL, NULL | Quantity 점수 |
| quality_score | DECIMAL, NULL | Quality 점수 |
| satiety_score | DECIMAL, NULL | Satiety 점수|
| computed_at | TIMESTAMP | 평가 시각 |

> **재평가하면 덮어쓴다** (식사당 1행). `meal_feedbacks` 도 같다.
> `total_score` 도 `*_weight` 도 없다 — 단계별 차이는 기준선의 엄격함으로 구현한다 (D9).

---

### `meal_feedbacks`

해당 한 끼의 QQS 결과를 바탕으로 식사 평가 설명과 "다음 끼니에는 무엇을 해볼지"를 저장하고 보여준다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 끼니 피드백 ID |
| user_id | UUID FK | 사용자 ID |
| meal_id | UUID FK, UNIQUE | 대상 식사 ID |
| body | TEXT | 식사 평가 요약 |
| suggestions | TEXT | 다음 끼니 행동 제안 |
| reasoning | TEXT | 피드백 생성 근거 |
| model_version | VARCHAR | 사용한 AI 모델 |
| safety_status | ENUM, DEFAULT REVIEW_REQUIRED | SAFE / BLOCKED / REVIEW_REQUIRED — 의료 피드백이 섞이지 않아 그대로 노출해도 되는지 |
| created_at | TIMESTAMP | 생성 시각 |

> 기본값이 `REVIEW_REQUIRED` 인 건 의도된 것이다. 가드레일을 통과해야만 `SAFE` 가 된다 (규칙 1).

### `daily_feedbacks`

하루 동안 먹은 여러 끼니를 종합하여 "오늘 식사는 전체적으로 어땠는지"를 저장한다. 홈의 오늘 요약이나 일별 기록에 활용할 수 있다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 일일 피드백 ID |
| user_id | UUID FK | 사용자 ID |
| feedback_date | DATE | 피드백 대상 날짜 |
| summary | TEXT | 하루 식사 종합 평가 |
| quantity_score | DECIMAL | 하루 Quantity 점수 |
| quality_score | DECIMAL | 하루 Quality 점수 |
| satiety_score | DECIMAL | 하루 Satiety 점수 |
| model_version | VARCHAR | 사용한 AI 모델 |
| created_at | TIMESTAMP | 생성 시각 |
| safety_status | ENUM, DEFAULT REVIEW_REQUIRED | SAFE / BLOCKED / REVIEW_REQUIRED |

> `UNIQUE (user_id, feedback_date)` — 하루에 하나.

### `long_term_feedbacks`

여러 날의 데이터를 분석하여 주간·월간 Q/Q/S 추이와 장기적인 행동 제안을 저장한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| id | UUID PK | 장기 피드백 ID |
| user_id | UUID FK | 사용자 ID |
| period_type | ENUM | WEEKLY / MONTHLY |
| period_start | DATE | 분석 시작일 |
| period_end | DATE | 분석 종료일 |
| trend_summary | TEXT | 기간 동안의 변화 추이 |
| recommendation | TEXT | 장기적인 행동 제안 |
| chart_data | JSONB | 대시보드용 집계 데이터 |
| model_version | VARCHAR | 사용한 AI 모델 |
| created_at | TIMESTAMP | 생성 시각 |
| safety_status | ENUM, DEFAULT REVIEW_REQUIRED | SAFE / BLOCKED / REVIEW_REQUIRED |

> `UNIQUE (user_id, period_type, period_start)`.

#### `daily_feedback_sources`

하나의 `daily_feedback`이 어떤 `meal_feedback`들을 기반으로 생성됐는지 연결한다. 피드백 생성 근거 추적용이다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| daily_feedback_id | UUID FK, PK | 일일 피드백 |
| meal_feedback_id | UUID FK, PK | 사용된 끼니 피드백 |

#### `long_term_feedback_sources`

하나의 장기 피드백이 어떤 일일 피드백들을 기반으로 만들어졌는지 기록한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| long_term_feedback_id | UUID FK, PK | 장기 피드백 |
| daily_feedback_id | UUID FK, PK | 사용된 일일 피드백 |