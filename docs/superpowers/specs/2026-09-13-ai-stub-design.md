# AI 스텁 서버 설계

- 작성일: 2026-09-13
- 브랜치: `be/4-feat-ai-stub`
- 대상 디렉터리: `ai/`
- 관련 문서: [`../../../ai/README.md`](../../../ai/README.md) · [`../../../backend/README.md`](../../../backend/README.md)

---

## 1. 배경

BE Worker는 식사 분석·피드백 생성 전 구간에서 AI Service를 HTTP로 호출한다.
AI Service의 실제 구현(LLM 연동)은 아직 없고, BE Worker 역시 아직 없다.
둘이 서로를 기다리면 양쪽 다 진행되지 않는다.

`ai/README.md`는 반대 방향의 해법만 정의해 두었다 —
"BE 없이 단독 개발하려면 `contracts/`의 OpenAPI로 Prism 목 서버를 띄운다(R7)".
BE가 AI 없이 개발하는 방법은 아직 정의돼 있지 않다. 이 문서가 그것을 정의한다.

## 2. 목적

1. BE Worker가 실제 LLM 없이 식사 파이프라인 전 구간을 개발·테스트할 수 있게 한다.
2. BE↔AI JSON 계약을 코드로 확정한다. 스텁의 Pydantic 스키마가 계약의 단일 원본이 된다.
3. Worker의 **실패 경로**(재시도·DLQ·가드레일 차단·저신뢰)를 재현 가능하게 밟을 수 있게 한다.

## 3. 범위

**포함**

- `ai/` 안에 `STUB_MODE` 플래그를 둔 FastAPI 앱
- 라우터 3개와 요청·응답 스키마 확정
- 의료 가드레일 (`guardrail/medical.py`) — 스텁·실제 공용
- 시나리오 트리거 6종
- pytest 테스트, `contracts/` OpenAPI export

**제외**

- 실제 LLM 호출 (`STUB_MODE=false` 경로는 `NotImplementedError`로 남긴다)
- BE `/internal/v1` 역호출 (`tools/`는 비워 둔다 — S3 참조)
- BE Worker 구현 자체 (별도 브랜치)
- 프롬프트 엔지니어링

## 4. 결정 사항

기존 `docs/decisions.md`의 D 번호와 충돌하지 않도록 S 접두사를 쓴다.

| # | 결정 | 근거 |
|---|---|---|
| S1 | 스텁을 별도 프로젝트가 아니라 `ai/` 안의 **모드**로 둔다 | 라우터·스키마·가드레일을 실제 구현과 공유한다. 버려지는 코드는 `agents/stub/`뿐이고, LLM을 붙일 때 계약이 그대로 남는다 |
| S2 | 일일 피드백은 `/short-feedback`에 `scope: MEAL / DAILY`로 받는다 | 라우터 신설은 프롬프트·스키마·테스트를 3배로 늘린다(`ai/README.md`). `daily_feedbacks.summary`는 추이가 아니라 요약이라 short의 결과물 성격과 같다 |
| S3 | 스텁은 BE `/internal/v1`을 역호출하지 않는다 | BE에 `/internal/v1`이 아직 없다(`app/internal/v1/`은 빈 패키지). 붙일 대상이 생기는 시점에 `tools/`를 채우는 것이 순서다. `candidateFoodRefId`는 픽스처 또는 `null`로 돌려준다 — `meal_items.food_ref_id`가 NULL을 허용한다 |
| S4 | 응답은 결정적 픽스처 + 시나리오 트리거로 만든다 | 고정 응답은 정상 경로만 검증한다. 입력 해시 기반 랜덤은 재현은 되지만 테스트에서 assert를 쓸 수 없다 |
| S5 | 시나리오는 `X-Stub-Scenario` **헤더**로 제어한다 | 본문에 매직 문자열을 심으면 프로덕션 데이터를 오염시킬 수 있고, 실제 구현으로 전환할 때 본문 스키마가 달라진다. 헤더는 실제 구현이 무시하면 그만이다 |
| S6 | AI 응답에 집계 숫자를 두지 않는다 — `chartData`·일일 점수는 BE가 채운다 | `ai/README.md` 규칙 2의 연장. `long_term_feedbacks.chart_data`와 `daily_feedbacks`의 점수 3개는 `qqs_evaluations` 집계로 구할 수 있다. LLM이 만들면 숫자가 틀려도 검증할 방법이 없다 |

## 5. 컴포넌트 구조

```
ai/
├─ app/
│  ├─ main.py              FastAPI 앱 · 라우터 등록 · 부팅 검증
│  ├─ config.py         ★  Settings (STUB_MODE · STUB_LATENCY_MS · STUB_DEFAULT_SCENARIO)
│  └─ routers/
│     ├─ analyze_meal.py    POST /analyze-meal
│     ├─ short_feedback.py  POST /short-feedback
│     └─ long_feedback.py   POST /long-feedback
├─ agents/
│  ├─ schemas.py        ★  요청·응답 Pydantic — 계약의 단일 원본
│  ├─ runner.py         ★  단일 진입점. STUB_MODE 분기가 여기 한 곳뿐
│  └─ stub/             ★  시나리오별 응답 생성기 3개
├─ guardrail/
│  └─ medical.py        ★  의료 키워드 감지 → safetyStatus 판정
├─ tools/                  비움 (S3)
└─ tests/               ★  라우터 × 시나리오
```

호출 흐름:

```
라우터  →  guardrail  →  agents/runner (단일 진입점)  ─┬─ STUB_MODE=true  → agents/stub/
                                                      └─ STUB_MODE=false → NotImplementedError
```

`ai/README.md`의 "모든 모델 호출은 단일 진입점을 거친다. 에이전트가 클라이언트를 직접 만들지 않는다"를
그대로 따른다. 스텁 분기가 그 진입점 한 곳에만 존재하므로, 실제 LLM을 붙일 때 고치는 파일은 `runner.py` 하나다.

`guardrail/`과 `agents/schemas.py`는 스텁 전용이 아니다. 실제 구현에서도 그대로 쓴다.

## 6. 계약 — `POST /analyze-meal`

### 요청

```json
{
  "mealId": "uuid",
  "imageUrl": "https://...presigned-GET",
  "rawText": "김치찌개, 흰쌀밥",
  "mealType": "LUNCH",
  "eatenAt": "2026-09-13T12:30:00+09:00",
  "stage": "TITRATION"
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `mealId` | UUID | `meals.id` |
| `imageUrl` | str, nullable | presigned **URL**. AI는 S3 권한이 없다(규칙 1) — BE가 `image_key`로 URL을 만들어 넘긴다 |
| `rawText` | str, nullable | `meals.raw_text` |
| `mealType` | enum | BREAKFAST / LUNCH / DINNER / SNACK |
| `eatenAt` | datetime | `meals.eaten_at` |
| `stage` | enum | PRE_DOSE / INITIAL / TITRATION / MAINTENANCE. 컨텍스트 선주입(규칙 4) |

`imageUrl`·`rawText` 중 최소 하나는 필수. `meals`의 `CHECK (image_key IS NOT NULL OR raw_text IS NOT NULL)`와 동일한 제약을 Pydantic 검증기로 건다.

### 응답

```json
{
  "mealId": "uuid",
  "modelVersion": "stub-vision-0",
  "safetyStatus": "SAFE",
  "items": [
    { "originalFoodName": "김치찌개", "candidateFoodRefId": "D000123",
      "estimatedAmountG": 350, "confidence": 0.82, "clarifyQuestion": null },
    { "originalFoodName": "흰쌀밥", "candidateFoodRefId": null,
      "estimatedAmountG": 210, "confidence": 0.41,
      "clarifyQuestion": "밥은 한 공기 정도였나요?" }
  ]
}
```

`items[]`는 `meal_items` 행에 1:1 대응한다.

| 응답 필드 | `meal_items` 컬럼 |
|---|---|
| `originalFoodName` | `original_food_name` |
| `candidateFoodRefId` | `food_ref_id` (nullable) |
| `estimatedAmountG` | `estimated_amount_g` |
| `confidence` | `confidence` — `CHECK (0 ~ 1)`을 Pydantic `ge=0, le=1`로 |
| 응답 전체 | `raw_ai_result` (JSONB 원본 저장) |

`display_name`은 AI가 주지 않는다 — 최초에는 `original_food_name`을 복사하고, 사용자가 고치면 그때 갱신된다.
`source`는 BE가 `MODEL`로 채운다.

**성분 숫자 필드(`kcal`·단백질 등)는 스키마에 존재하지 않는다.** 규칙 2를 프롬프트가 아니라 타입으로 강제하는 지점이다.
`estimatedAmountG`(양)는 AI가 주고, 성분은 BE가 `food_ref_id`로 `food_refs`에서 채운다.

### 픽스처 동작

`rawText`가 있으면 쉼표로 나눠 각 조각을 `originalFoodName`으로 그대로 되돌려준다.
없으면 고정 픽스처 3종을 쓴다. 개발 중 로그를 눈으로 따라가기 위한 편의이며, 계약에 영향을 주지 않는다.

## 7. 계약 — `POST /short-feedback`

### 요청 (`scope=MEAL`)

```json
{
  "scope": "MEAL",
  "userId": "uuid",
  "mealId": "uuid",
  "stage": "TITRATION",
  "qqs": { "quantity": 72.0, "quality": 64.5, "satiety": 80.0 },
  "items": [{ "displayName": "김치찌개", "confirmedAmountG": 320 }],
  "satiety": { "before": 20, "after": 85, "hungerReturnMinutes": 240,
               "userComment": "저녁까지 안 배고팠어요" }
}
```

### 요청 (`scope=DAILY`)

```json
{
  "scope": "DAILY",
  "userId": "uuid",
  "date": "2026-09-13",
  "stage": "TITRATION",
  "qqs": { "quantity": 68.0, "quality": 71.0, "satiety": 74.0 },
  "meals": [
    { "mealType": "BREAKFAST", "summary": "...", "qqs": { "quantity": 70, "quality": 65, "satiety": 72 } }
  ]
}
```

`qqs`는 **요청에 실려 온다.** AI는 점수를 계산하지 않는다 — 채점은 Rule Engine(순수 함수)이 이미 끝냈고,
AI는 그 숫자를 문장으로 옮기는 역할만 한다. `scope=DAILY`의 `qqs`는 BE가 그날 `qqs_evaluations`를 집계한 값이다(S6).

### 응답

```json
{
  "body": "...",
  "suggestions": "...",
  "reasoning": "...",
  "modelVersion": "stub-short-0",
  "safetyStatus": "SAFE"
}
```

| 응답 필드 | `scope=MEAL` → `meal_feedbacks` | `scope=DAILY` → `daily_feedbacks` |
|---|---|---|
| `body` | `body` | `summary` |
| `suggestions` | `suggestions` | **`null`** — 대응 컬럼 없음 |
| `reasoning` | `reasoning` | **`null`** — 대응 컬럼 없음 |
| `modelVersion` | `model_version` | `model_version` |
| `safetyStatus` | `safety_status` | `safety_status` |

`daily_feedbacks`의 `quantity_score`·`quality_score`·`satiety_score`는 응답에 없다 — BE가 집계해 채운다(S6).

## 8. 계약 — `POST /long-feedback`

### 요청

```json
{
  "userId": "uuid",
  "periodType": "WEEKLY",
  "periodStart": "2026-09-07",
  "periodEnd": "2026-09-13",
  "stage": "MAINTENANCE",
  "series": [
    { "date": "2026-09-07", "quantity": 70, "quality": 61, "satiety": 75 }
  ],
  "dailySummaries": ["...", "..."]
}
```

`periodType`은 `WEEKLY | MONTHLY`. `DAILY`는 `/short-feedback`이 받는다 (S2).

### 응답

```json
{
  "trendSummary": "...",
  "recommendation": "...",
  "modelVersion": "stub-long-0",
  "safetyStatus": "SAFE"
}
```

| 응답 필드 | `long_term_feedbacks` 컬럼 |
|---|---|
| `trendSummary` | `trend_summary` |
| `recommendation` | `recommendation` |
| `modelVersion` | `model_version` |
| `safetyStatus` | `safety_status` |

**`chartData`는 응답에 없다**(S6). `long_term_feedbacks.chart_data`는 BE가 `qqs_evaluations`를 집계해 채운다.

## 9. 가드레일

세 라우터 모두 요청 본문의 **자유 텍스트 필드**를 먼저 `guardrail/medical.py`에 통과시킨다. 스텁 생성기까지 가지 않는다.

| 라우터 | 검사 대상 필드 |
|---|---|
| `/analyze-meal` | `rawText` |
| `/short-feedback` | `satiety.userComment` · `meals[].summary` |
| `/long-feedback` | `dailySummaries[]` |

- 감지 대상: 용량·증량·감량·단약·처방·`mg` 등 투약 판단을 요구하는 표현
- 감지되면 `safetyStatus=BLOCKED`, 본문은 상담 안내 문구로 대체
- BE는 이를 받아 `medical_handoff_logs`에 기록하고 피드백을 노출하지 않는다

`safetyStatus`의 기본값은 `REVIEW_REQUIRED`다. 가드레일을 통과해야만 `SAFE`가 된다 — DB 컬럼 기본값과 동일한 방향(규칙 1).

명령형 처방 톤("이만큼 드세요")도 금지 대상이다. 스텁 픽스처 문구는 전부 서술형("평소 대비 이만큼")으로 쓴다(규칙 3).

## 10. 시나리오 트리거

`X-Stub-Scenario` 헤더로 제어한다. 헤더가 없으면 `STUB_DEFAULT_SCENARIO`(기본 `SUCCESS`).

| 시나리오 | 스텁 동작 | 검증되는 Worker 경로 |
|---|---|---|
| `SUCCESS` | 정상 응답 | `ANALYZING → REVIEW_REQUIRED` |
| `LOW_CONFIDENCE` | `confidence` 낮음 + `clarifyQuestion` 부착 | 사용자 확인 분기 |
| `NO_MATCH` | `candidateFoodRefId` 전부 `null` | `food_ref_id` NULL 경로 |
| `BLOCKED` | `safetyStatus=BLOCKED` | `medical_handoff_logs` 기록 |
| `ERROR_500` | 5xx 반환 | 재시도 → DLQ → `status=FAILED` |
| `SLOW` | `STUB_LATENCY_MS`만큼 지연 후 응답 | 타임아웃 처리 |

시나리오는 가드레일보다 **먼저** 평가된다. `X-Stub-Scenario: BLOCKED`은 키워드가 없어도 `BLOCKED`을 돌려주고,
`ERROR_500`은 가드레일을 거치지 않고 5xx를 낸다. 테스트가 입력 문구를 꾸며내지 않고 경로를 고를 수 있어야 하기 때문이다.
키워드 기반 차단(9절)은 헤더가 없거나 `SUCCESS`일 때 정상 동작한다.

`ERROR_500`과 `SLOW`가 특히 중요하다. `backend/README.md`의
"**AI가 죽어도 Q/Q/S는 남는다** — 피드백 생성 실패는 부분 실패로 처리한다"를
실제로 테스트하려면 AI를 마음대로 죽일 수 있어야 한다.

## 11. 테스트 전략

- pytest + `fastapi.testclient.TestClient`
- 라우터 3개 × 시나리오 6종을 파라미터화해서 돌린다
- 응답이 Pydantic 응답 스키마를 만족하는지 검증 — 계약 위반이 테스트에서 바로 잡힌다
- 가드레일: 의료 키워드가 든 요청이 세 라우터 모두에서 `BLOCKED`을 받는지
- `scope=DAILY` 응답의 `suggestions`·`reasoning`이 `null`인지

### contracts export

FastAPI가 만든 OpenAPI를 `contracts/ai-service.openapi.json`으로 뽑아 커밋하는 스크립트를 둔다.
`contracts/`는 현재 비어 있다(`.gitkeep`만). 이 파일이 생기면:

- BE와 AI가 같은 계약 파일을 본다
- `ai/README.md`의 R7(Prism 목 서버)이 이 파일로 동작한다
- 계약 변경이 PR diff에 드러난다

## 12. 환경변수

`ai/.env.example`에 추가:

| 키 | 기본값 | 설명 |
|---|---|---|
| `STUB_MODE` | `true` | `false`면 실제 LLM 경로 — 이번 범위에서는 `NotImplementedError` |
| `STUB_LATENCY_MS` | `0` | `SLOW` 시나리오의 지연 시간 |
| `STUB_DEFAULT_SCENARIO` | `SUCCESS` | 헤더가 없을 때의 시나리오 |

BE 쪽 `.env`: `AI_SERVICE_BASE_URL=http://localhost:8001`.
BE API가 8000을 쓰므로 스텁은 8001로 띄운다.

기존 `ai/README.md`가 정의한 `LLM_*` 키들은 `STUB_MODE=true`에서 필수가 아니다.
`config.py`는 `STUB_MODE=false`일 때만 그 키들을 요구하도록 조건부 검증한다.

## 13. 후속 작업

이 브랜치 범위 밖이며, 순서대로 이어진다.

1. BE `/internal/v1` 구현 (서비스 토큰 인증 + tool 3종)
2. 스텁 `tools/` 채우기 — 역호출 경로 통합 검증 (S3 해제)
3. BE Worker 식사 파이프라인 — 이 스텁을 상대로 개발
4. AI 실제 LLM 연동 — `runner.py`의 `STUB_MODE=false` 분기만 채우면 된다

## 14. 열린 질문

- 스텁 작업 디렉터리가 `ai/`인데 브랜치 접두사가 `be/`다. 팀에서 파트별 접두사를 엄격히 보는지 확인 필요.
- `docs/architecture.md`와 `docs/decisions.md`가 `ai/README.md`·`backend/README.md`에서 참조되지만 아직 없다. D 번호(D2·D4·D5·D9·D13, R3·R7)의 원본이 어디인지 확인 필요.
