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

# API — http://127.0.0.1:8000/docs
uvicorn app.main:app --reload

# ── 아래는 아직 없다 (구현되면 주석을 푼다) ──────────────────
# cp .env.example .env             # .env.example 미작성
# python -m app.worker_main        # worker_main.py 미작성
# pytest                           # pytest 미설치 · 테스트 미작성
# cd ../infra && docker compose up # infra/docker-compose.yml 미작성
```

## 환경변수

`.env.example` 참고. `core/`의 Pydantic `BaseSettings`로 로드해, 없거나 형식이 틀리면
**부팅 시점에 실패**하도록 검증한다 (런타임에 이상하게 죽는 것보다 기동 시 명확히 죽는 게 낫다).

| 키 | 설명 |
|---|---|
| `DB_URL` · `DB_USER` · `DB_PASSWORD` | PostgreSQL 접속 |
| `JWT_SECRET` | 액세스 토큰 서명 키 |
| `KAKAO_CLIENT_ID` · `KAKAO_CLIENT_SECRET` | 카카오 OAuth (D3) |
| `INTERNAL_SERVICE_TOKEN` | `/internal/v1` 호출용. AI Service와 공유 |
| `AI_SERVICE_BASE_URL` | AI 컨테이너 주소 |
| `STORAGE_TYPE` | `s3` \| `local` |
| `S3_BUCKET` | **`glp1-team-*` 패턴이어야 함.** 인스턴스 역할 권한이 이 패턴으로 한정 |
| `QUEUE_TYPE` | `sqs` \| `local` |
| `SQS_QUEUE_URL` · `SQS_DLQ_URL` | 비동기 파이프라인 |
| `FCM_*` | 푸시 발송 |

> 🚨 AWS 액세스 키는 두지 않는다. 서버는 **인스턴스 역할**로 S3·SQS에 접근한다.

---

## 테스트 전략

| 대상 | 방식 |
|---|---|
| Rule Engine | **순수 함수 단위 테스트.** 입력→기대 점수 표로 고정(`pytest.mark.parametrize`). 단계를 바꾸면 점수가 실제로 달라지는지 검증(R1) |
| 레이어 경계 | import-linter — `services/*` 상호 참조 금지 · `services` → `crud` 단방향을 CI에서 강제 |
| API | `httpx.ASGITransport` + Testcontainers(Postgres) |
| infra 추상화 | 로컬 구현으로 테스트, 외부 의존 0 |

---

## 참고

- API 명세 · 스키마 · 단계 프로파일은 [`../contracts/`](../contracts/)가 **코드보다 먼저**다.
- 개발 규칙: [`../CLAUDE.md`](../CLAUDE.md)
