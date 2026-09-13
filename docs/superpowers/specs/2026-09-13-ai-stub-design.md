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

그리고 `contracts/`는 폐기하기로 했다(S7). R7이 쓰던 수단이 사라지므로,
AI 파트가 BE 없이 개발하는 방법은 `/internal/v1`을 구현하는 시점에 팀이 다시 정해야 한다 — 13절 참조.
이 문서의 범위는 BE→AI 방향뿐이다.

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
- pytest 테스트
- multi-stage Dockerfile (`stub` · `real` 두 타깃)과 `infra/docker-compose.ai.yml`

**제외**

- 실제 LLM 호출 (`STUB_MODE=false` 경로는 `NotImplementedError`로 남긴다)
- BE `/internal/v1` 역호출 (`tools/`는 비워 둔다 — S3 참조)
- BE Worker 구현 자체 (별도 브랜치)
- 프롬프트 엔지니어링

## 4. 결정 사항

기존 `docs/decisions.md`의 D 번호와 충돌하지 않도록 S 접두사를 쓴다.

| # | 결정 | 근거 |
|---|---|---|
| S1 | 스텁을 별도 프로젝트가 아니라 `ai/` 안의 **모드**로 둔다 (소스 기준. 이미지는 S8 에서 나눈다) | 라우터·스키마·가드레일을 실제 구현과 공유한다. 버려지는 코드는 `agents/stub/`뿐이고, LLM을 붙일 때 계약이 그대로 남는다 |
| S2 | 일일 피드백은 `/short-feedback`에 `scope: MEAL / DAILY`로 받는다 | 라우터 신설은 프롬프트·스키마·테스트를 3배로 늘린다(`ai/README.md`). `daily_feedbacks.summary`는 추이가 아니라 요약이라 short의 결과물 성격과 같다 |
| S3 | 스텁은 BE `/internal/v1`을 역호출하지 않는다 | BE에 `/internal/v1`이 아직 없다(`app/internal/v1/`은 빈 패키지). 붙일 대상이 생기는 시점에 `tools/`를 채우는 것이 순서다. `candidateFoodRefId`는 픽스처 또는 `null`로 돌려준다 — `meal_items.food_ref_id`가 NULL을 허용한다 |
| S4 | 응답은 결정적 픽스처 + 시나리오 트리거로 만든다 | 고정 응답은 정상 경로만 검증한다. 입력 해시 기반 랜덤은 재현은 되지만 테스트에서 assert를 쓸 수 없다 |
| S5 | 시나리오는 `X-Stub-Scenario` **헤더**로 제어한다 | 본문에 매직 문자열을 심으면 프로덕션 데이터를 오염시킬 수 있고, 실제 구현으로 전환할 때 본문 스키마가 달라진다. 헤더는 실제 구현이 무시하면 그만이다 |
| S6 | AI 응답에 집계 숫자를 두지 않는다 — `chartData`·일일 점수는 BE가 채운다 | `ai/README.md` 규칙 2의 연장. `long_term_feedbacks.chart_data`와 `daily_feedbacks`의 점수 3개는 `qqs_evaluations` 집계로 구할 수 있다. LLM이 만들면 숫자가 틀려도 검증할 방법이 없다 |
| S8 | 스텁과 실사용을 **별개 이미지**로 빌드한다 — `glp1-ai-stub` · `glp1-ai`. 소스는 `ai/` 하나이고 Dockerfile 을 multi-stage 로 나눈다 | 컨테이너만 나누면 둘이 같은 이미지를 공유한다. 환경변수 하나 잘못 주면 스텁이 팀 공용 크레딧을 쓴다. 이미지를 나누면 스텁에는 LLM 의존성도 `agents/llm/` 도 **물리적으로 없다** — 설정 실수로 뚫리지 않는다. 소스는 나누지 않으므로 `schemas.py` 는 여전히 한 벌이다 (S7) |
| S7 | `contracts/`를 폐기한다. 계약의 단일 원본은 `agents/schemas.py`이고, OpenAPI는 **런타임에** FastAPI가 서빙한다 | 생성물을 커밋하면 원본(Pydantic)과 사본(JSON)이 갈라지고, 갱신 스크립트를 매번 돌려야 한다. 이 계약의 소비자는 BE Worker 하나뿐이고, 그쪽은 어차피 스텁을 띄워 놓고 개발한다 — `http://localhost:8001/docs`를 보면 된다 |

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

### 이미지 두 개 (S8)

한 소스에서 두 이미지를 빌드한다. 나뉘는 것은 `agents/` 아래 한 겹과 의존성뿐이다.

| | `glp1-ai-stub` | `glp1-ai` |
|---|---|---|
| Dockerfile target | `stub` | `real` |
| `app/` · `guardrail/` · `schemas.py` · `runner.py` | ✅ | ✅ |
| `agents/stub/` | ✅ | ❌ |
| `agents/llm/` | ❌ | ✅ |
| `requirements-llm.txt` (LLM SDK) | ❌ | ✅ |
| `STUB_MODE` 기본값 | `true` | `false` |
| 포트 (호스트) | 8001 | 8002 |

```bash
docker build --target stub -t glp1-ai-stub ai/
docker build --target real -t glp1-ai      ai/
```

`infra/docker-compose.ai.yml` 로 띄운다. **기본으로는 스텁만 뜬다** — 실사용은
`--profile real` 을 명시해야 올라간다. 팀 공용 크레딧을 실수로 쓰지 않게 한 장치다.

이 구성 때문에 `runner.py` 는 구현 묶음을 **지연 import** 한다.
최상단에서 `from agents.stub import ...` 를 하면 실사용 이미지가 기동하자마자 ImportError 로 죽는다.

`agents/llm/` 이 없는 실사용 이미지는 기동은 되고, 요청이 오면 `501 Not Implemented` 와
무엇을 구현해야 하는지 적은 메시지를 돌려준다.

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
  "mealId": "meal_456",
  "modelVersion": "stub-vision-0",
  "safetyStatus": "SAFE",
  "items": [
    { "originalFoodName": "참치김밥", "candidateFoodRefId": "D000123",
      "estimatedAmount": 250, "unit": "g", "confidence": 0.62,
      "clarifyQuestion": "김밥 속재료가 참치가 맞나요?" },
    { "originalFoodName": "삶은 계란", "candidateFoodRefId": null,
      "estimatedAmount": 2, "unit": "개", "confidence": 0.96,
      "clarifyQuestion": null }
  ]
}
```

`items[]`는 `meal_items` 행에 1:1 대응한다.

| 응답 필드 | `meal_items` 컬럼 | 비고 |
|---|---|---|
| `originalFoodName` | `original_food_name` | |
| `candidateFoodRefId` | `food_ref_id` (nullable) | 공개 API의 `matched`·`nutritionSource`는 이 값의 해석 결과다 |
| `estimatedAmount` + `unit` | `estimated_amount_g` | **BE가 g로 환산해 저장한다** — 아래 참조 |
| `confidence` | `confidence` | `CHECK (0 ~ 1)`을 Pydantic `ge=0, le=1`로 |
| 응답 전체 | `raw_ai_result` | JSONB 원본 저장 |

### 양은 g가 아니라 자연 단위로 받는다

공개 API 명세가 `{ "amount": 2, "unit": "개" }`를 돌려주고 "**g 환산은 서버**"라고 못박았다.
비전 모델은 "계란 100g"이 아니라 "계란 2개"라고 본다 — 그게 사진에서 읽히는 형태다.
개→g 환산에는 `food_refs`의 1회 제공량이 필요한데 AI는 그 테이블을 볼 수 없다(규칙 1).

따라서 AI는 `estimatedAmount` + `unit`(`g` / `개` / `ml` / `공기` 등)을 주고,
BE가 `food_ref_id`로 `food_refs`를 조회해 `estimated_amount_g`에 넣는다.
`candidateFoodRefId`가 `null`이면 환산이 불가능하므로 원본 단위를 `raw_ai_result`에 두고
`matched: false`로 내보낸다 — 명세의 "`matched: false` → 영양정보 없음"과 같은 상태다.

### clarifyQuestion은 항목별, 노출은 식사별

AI는 애매한 **항목마다** `clarifyQuestion`을 붙인다(`ai/README.md`의 "애매한 항목에는 `clarifyQuestion`을 붙여").
반면 공개 API `GET /meals/{mealId}`는 식사 단위로 `clarifyQuestion` 하나를 돌려준다.

BE가 좁힌다 — `confidence`가 가장 낮은 항목의 질문 하나를 고른다. 정보를 버리지 않으려고 AI 쪽은 배열로 받는다.
나중에 FE가 여러 질문을 받을 수 있게 되면 계약을 안 고쳐도 된다.

### 성분값은 여전히 AI가 만들지 않는다

`kcal`·`proteinG` 같은 필드는 응답 스키마에 존재하지 않는다. 규칙 2를 프롬프트가 아니라 타입으로 강제하는 지점이다.
공개 API `GET /meals/{mealId}`의 `nutrition` 블록은 전부 BE가 `food_refs`에서 채운 값이다.

`display_name`은 AI가 주지 않는다 — 최초에는 `original_food_name`을 복사하고, 사용자가 고치면 그때 갱신된다.
`source`는 BE가 `MODEL`로 채운다.

### 픽스처 동작

`rawText`가 있으면 쉼표로 나눠 각 조각을 `originalFoodName`으로 그대로 되돌려준다.
없으면 고정 픽스처 3종을 쓴다. 개발 중 로그를 눈으로 따라가기 위한 편의이며, 계약에 영향을 주지 않는다.

## 7. 계약 — `POST /short-feedback`

### 요청 (`scope=MEAL`)

```json
{
  "scope": "MEAL",
  "userId": "uuid",
  "mealId": "meal_456",
  "stage": "MAINTENANCE",
  "qqs": { "quantity": 75, "quality": 80, "satiety": 68 },
  "items": [
    { "displayName": "참치김밥", "amount": 250, "unit": "g",
      "nutrition": { "kcal": 400, "proteinG": 12, "fatG": 10,
                     "carbG": 65, "fiberG": 4, "sodiumMg": 780 } }
  ],
  "satiety": { "beforePct": 20, "afterPct": 68,
               "checkins": [{ "checkinOffsetHours": 3, "satietyPct": 40 }],
               "hungerReturnMinutes": 60,
               "userComment": "저녁까지 안 배고팠어요" }
}
```

### 요청 (`scope=DAILY`)

```json
{
  "scope": "DAILY",
  "userId": "uuid",
  "date": "2026-08-21",
  "stage": "MAINTENANCE",
  "qqs": { "quantity": 68, "quality": 71, "satiety": 74 },
  "meals": [
    { "mealType": "BREAKFAST", "summary": "...", "qqs": { "quantity": 70, "quality": 65, "satiety": 72 } }
  ]
}
```

`qqs`는 **요청에 실려 온다.** AI는 점수를 계산하지 않는다 — 채점은 Rule Engine(순수 함수)이 이미 끝냈고,
AI는 그 숫자를 문장으로 옮기는 역할만 한다. `scope=DAILY`의 `qqs`는 BE가 그날 `qqs_evaluations`를 집계한 값이다(S6).

### `items[].nutrition`은 규칙 2의 예외가 아니다

공개 API의 `reasoning` 예시는 "지금은 포만감 유지가 중요한데 **단백질 비중이 낮았어요**"다.
이 문장을 쓰려면 AI가 성분을 알아야 한다.

규칙 2는 AI가 성분을 **생성**하는 것을 금지한다. 받는 것은 금지하지 않는다.
`nutrition`은 BE가 `food_refs`에서 조회해 넣어 주는 **컨텍스트 선주입**(규칙 4)이고, 방향이 BE→AI다.
응답 스키마에는 여전히 성분 필드가 없으므로 AI가 숫자를 지어낼 경로는 없다.

### 응답

```json
{
  "body": "유지기 기준 포만감이 부족한 식사예요.",
  "reasoning": "지금은 포만감 유지가 중요한데 단백질 비중이 낮았어요.",
  "suggestions": [
    { "foodName": "두부 반 모", "candidateFoodRefId": "D004512",
      "advice": "단백질을 조금 더 채우는 쪽이에요" },
    { "foodName": "나물 한 접시", "candidateFoodRefId": null,
      "advice": "식이섬유가 포만감을 늘리는 데 도움이 돼요" }
  ],
  "modelVersion": "stub-short-0",
  "safetyStatus": "SAFE"
}
```

| 응답 필드 | `scope=MEAL` → `meal_feedbacks` | `scope=DAILY` → `daily_feedbacks` |
|---|---|---|
| `body` | `body` | `summary` |
| `reasoning` | `reasoning` | **`null`** — 대응 컬럼 없음 |
| `suggestions` | `suggestions` | **`null`** — 대응 컬럼 없음 |
| `modelVersion` | `model_version` | `model_version` |
| `safetyStatus` | `safety_status` | `safety_status` |

`daily_feedbacks`의 `quantity_score`·`quality_score`·`satiety_score`는 응답에 없다 — BE가 집계해 채운다(S6).

공개 API가 `summary`라고 부르는 필드를 AI 계약은 `body`라고 부른다. DB 컬럼명(`meal_feedbacks.body`)을 따랐다.
BE가 공개 API로 내보낼 때 이름을 바꾼다.

### suggestions에서 AI가 채우는 것과 BE가 채우는 것

공개 API의 제안 한 건은 이렇게 생겼다:

```json
{ "foodName": "두부 반 모",
  "nutrients": [{ "code": "PROTEIN", "amountG": 10 }],
  "advice": "단백질을 10g 더 채워요" }
```

`nutrients`는 숫자다. 규칙 2에 따라 AI가 만들지 않는다.

| 필드 | 채우는 쪽 | 근거 |
|---|---|---|
| `foodName` | AI | 어떤 음식을 제안할지는 판단의 영역 |
| `candidateFoodRefId` | AI | 지목만 한다. `null`이면 BE가 `foodName`으로 `food_refs`를 검색 |
| `advice` | AI | 문장 |
| `nutrients[]` | **BE** | `food_ref_id`로 `food_refs` 조회. AI가 지어내면 검증할 방법이 없다 |

`expectedSatietyPct`(`{ "current": 62, "after": 79 }`)도 **BE가 채운다.**
`current`는 `qqs_evaluations.satiety_score`이고, `after`는 제안 음식을 더한 상태로 Rule Engine을 다시 돌린 값이다.
같은 채점기를 쓰므로 화면의 숫자와 점수가 어긋나지 않는다 — LLM에게 맡기면 그 보장이 깨진다.

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

### periodType — 명세와 DB가 어긋난다

공개 API는 `?period=7d | 28d | all`이고, `long_term_feedbacks.period_type` ENUM은 `WEEKLY | MONTHLY`다.
`7d`≈`WEEKLY`, `28d`≈`MONTHLY`로 대응되지만 **`all`에 대응하는 ENUM 값이 없다.**

AI 계약은 `WEEKLY | MONTHLY | ALL` 세 값을 받는다. AI에게 필요한 건 "어느 구간을 요약하는가"이고
그건 `periodStart`·`periodEnd`·`series`가 이미 말해 준다 — `periodType`은 어조를 고르는 힌트에 가깝다.
세 값을 그대로 받아도 AI 쪽에서는 아무 문제가 없다.

문제는 BE 쪽이다. `all`을 저장하려면 ENUM에 값을 추가하거나, `MONTHLY`로 접고
`UNIQUE (user_id, period_type, period_start)` 충돌을 감수해야 한다. 14절에 열린 질문으로 남긴다.

`DAILY`는 여기 없다 — `/short-feedback`이 받는다 (S2).

### AI가 받지 않는 것

| 공개 API 필드 | 채우는 쪽 | 근거 |
|---|---|---|
| `dataSufficient` | BE | `daily_feedbacks` 행 수를 세면 안다. **`false`면 AI를 호출하지 않는다** — 근거가 없는데 문장을 만들게 할 이유가 없다 |
| `stale` · `staleReason` | BE | `MEAL_DELETED` 같은 무효화 사유는 DB 변경 이벤트다 |
| `generatedAt` | BE | `long_term_feedbacks.created_at` |
| `status` | BE | 잡의 진행 상태 |

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
| `LOW_CONFIDENCE` | `confidence`를 `0.8` 미만으로 + `clarifyQuestion` 부착 | 명세의 "0.8 미만은 FE 강조" 분기 |
| `NO_MATCH` | `candidateFoodRefId` 전부 `null` | `matched: false` · `nutrition: null` 경로 |
| `BLOCKED` | `safetyStatus=BLOCKED` | `medical_handoff_logs` 기록 |
| `ERROR_500` | 5xx 반환 | 재시도 → DLQ → `status=FAILED` |
| `SLOW` | `STUB_LATENCY_MS`만큼 지연 후 응답 | `timeoutMs: 30000` 초과 처리 |

공개 API가 `steps[]`로 파이프라인을 FE에 노출한다 —
`FOOD_RECOGNITION` · `DB_MATCHING` · `STAGE_RULE_APPLY`. 시나리오는 이 중 첫 단계를 조작한다.

| step | 담당 | 스텁이 건드리는가 |
|---|---|---|
| `FOOD_RECOGNITION` | AI `/analyze-meal` | **그렇다** — 6개 시나리오 전부 이 단계에 작용한다 |
| `DB_MATCHING` | BE — `candidateFoodRefId` → `food_refs` | 간접적. `NO_MATCH`가 이 단계를 빈손으로 만든다 |
| `STAGE_RULE_APPLY` | BE Rule Engine | 아니다. AI와 무관하다 |

세 단계가 AI·BE로 정확히 갈리는 게 이 설계가 맞게 잡혔다는 신호다.
`ERROR_500`으로 `FOOD_RECOGNITION`을 죽여도 `STAGE_RULE_APPLY`는 독립적으로 돌 수 있다 —
"AI가 죽어도 Q/Q/S는 남는다"가 `steps[]` 수준에서 그대로 보인다.

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

### 계약 문서

별도 export 파일을 만들지 않는다(S7). FastAPI가 `/openapi.json`과 `/docs`를 자동으로 서빙하므로,
스텁을 띄우면 그게 곧 계약 문서다.

```
uvicorn app.main:app --reload --port 8001
# http://localhost:8001/docs
```

계약 변경을 PR diff에서 보고 싶다면 `agents/schemas.py`의 diff를 보면 된다 — 그게 원본이다.
생성된 JSON을 커밋하면 원본과 사본이 갈라지는 문제만 생긴다.

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
2. **R7 대체 수단 결정** — `contracts/`가 사라지므로(S7), AI 파트가 BE 없이 개발할 때 쓸
   `/internal/v1` 목을 어떻게 제공할지 1번과 함께 정해야 한다.
   이 스텁과 대칭으로 BE 쪽에 `INTERNAL_STUB_MODE`를 두는 방법이 가장 일관적이다
3. 스텁 `tools/` 채우기 — 역호출 경로 통합 검증 (S3 해제)
4. BE Worker 식사 파이프라인 — 이 스텁을 상대로 개발
5. AI 실제 LLM 연동 — `runner.py`의 `STUB_MODE=false` 분기만 채우면 된다

## 14. 열린 질문

### 이 브랜치 밖

- 스텁 작업 디렉터리가 `ai/`인데 브랜치 접두사가 `be/`다. 팀에서 파트별 접두사를 엄격히 보는지 확인 필요.
- `docs/architecture.md`와 `docs/decisions.md`가 `ai/README.md`·`backend/README.md`에서 참조되지만 아직 없다. D 번호(D2·D4·D5·D9·D13, R3·R7)의 원본이 어디인지 확인 필요.
- `contracts/` 폐기에 따라 `ai/README.md`의 두 군데를 고쳐야 한다 — "실행" 절의 Prism 목 서버 안내와
  `[../contracts/]` 링크. 이 브랜치에서 같이 고칠지, AI 파트에 넘길지 정할 것.

### 공개 API 명세와 DB 스키마의 불일치

명세를 `meals`·`meal_items`·`satiety_logs`·`meal_feedbacks`·`long_term_feedbacks`와 대조하며 찾은 것들이다.
전부 BE DB 쪽 결정이라 이 스펙에서 확정하지 않는다. 다만 AI 계약이 어느 쪽으로 가든 버틸 수 있게
원본 단위·구조를 보존하는 방향으로 설계해 뒀다.

| # | 불일치 | 선택지 |
|---|---|---|
| 1 | 명세는 `amount` + `unit`(`개`·`ml`)을 돌려주는데 `meal_items`에는 `estimated_amount_g`만 있고 단위 컬럼이 없다 | `raw_ai_result` JSONB에서 읽기 / `unit` 컬럼 추가 |
| 2 | 명세의 `suggestions`는 `{foodName, nutrients[], advice}` 배열인데 `meal_feedbacks.suggestions`는 TEXT다 | TEXT에 JSON 직렬화 / JSONB로 변경 / `feedback_suggestions` 테이블 분리 |
| 3 | 명세의 `satiety.checkins[]`(`checkinOffsetHours`·`satietyPct`)에 대응하는 저장 위치가 없다. `satiety_logs`는 식사당 1행이고 체크인 배열을 담을 컬럼이 없다 | `satiety_checkins` 테이블 추가 / JSONB 컬럼 |
| 4 | 명세의 `?period=all`에 대응하는 `long_term_feedbacks.period_type` ENUM 값이 없다 | ENUM에 `ALL` 추가 / `MONTHLY`로 접고 `UNIQUE (user_id, period_type, period_start)` 충돌 감수 |
| 5 | 규칙 3은 명령형 처방 톤을 금지하는데("이만큼 드세요" 금지, 서술형만), 명세의 예시 문구가 명령형이다 — "단백질을 10g 더 채워요", "단백질을 앞으로 당겨 보세요" | 명세 예시를 서술형으로 고치기 / 규칙 3의 경계를 다시 정의하기 |

5번은 가드레일 구현에 직접 영향을 준다. 출력 검증을 어디까지 엄격하게 걸지가 이 결정에 달려 있다.
스텁 픽스처는 일단 규칙 3을 따라 서술형으로만 쓴다 — 나중에 느슨하게 푸는 쪽이 조이는 쪽보다 쉽다.

1·3번은 이미 머지 대기 중인 BE-2 스키마에 걸린다. 컬럼을 추가하는 선택지를 고르면 alembic 마이그레이션이 하나 더 필요하다.

## 참고 API 명세

### 식사 입력 · 분석

#### POST /meals

```
multipart:  inputType=PHOTO, image=<file>, mealType=DINNER,
            eatenAt=2026-08-21T18:10:00+09:00, satietyBeforePct=20
```

```json
// JSON (텍스트 입력)
{ "inputType": "TEXT", "rawText": "김밥 한 줄",
  "mealType": "LUNCH", "eatenAt": "2026-08-21T12:40:00+09:00",
  "satietyBeforePct": 20 }

// 202
{ "mealId": "meal_456",
  "status": "ANALYZING",
  "steps": [
    { "key": "FOOD_RECOGNITION", "state": "RUNNING" },
    { "key": "DB_MATCHING",      "state": "PENDING" },
    { "key": "STAGE_RULE_APPLY", "state": "PENDING" }
  ],
  "pollIntervalMs": 1500,
  "timeoutMs": 30000 }
```

`satietyBeforePct` 는 선택 입력. 미입력 시 `null`.

#### GET /meals/{mealId}

`status` 에 따라 폴링 · 확인 · 상세 조회에 모두 쓰입니다.

```json
{
  "mealId": "meal_456",
  "status": "REVIEW_REQUIRED",
  "isRecalculation": false,
  "stage": "MAINTENANCE",
  "mealType": "LUNCH",
  "eatenAt": "2026-08-21T12:40:00+09:00",
  "imageUrl": "https://…",
  "steps": [
    { "key": "FOOD_RECOGNITION", "state": "DONE" },
    { "key": "DB_MATCHING", "state": "RUNNING" },
    { "key": "STAGE_RULE_APPLY", "state": "PENDING" }
  ],
  "clarifyQuestion": "김밥 속재료가 참치가 맞나요?",
  "items": [
    {
      "itemId": "item_1",
      "displayName": "참치김밥",
      "amount": 250,
      "unit": "g",
      "confidence": 0.62,
      "matched": true,
      "nutritionSource": "PUBLIC_DB",
      "userConfirmed": false,
      "nutrition": {
        "kcal": 400,
        "proteinG": 12,
        "fatG": 10,
        "carbG": 65,
        "fiberG": 4,
        "sodiumMg": 780
      }
    },
    {
      "itemId": "item_2",
      "displayName": "삶은 계란",
      "amount": 2,
      "unit": "개",
      "confidence": 0.96,
      "matched": false,
      "nutritionSource": null,
      "userConfirmed": false,
      "nutrition": null
    }
  ]
}
```

`status: EVALUATED` 일 때 추가되는 필드:

```json
{ "scores": { "quantity": 75, "quality": 80, "satiety": 68 },
  "nutrients": [ … ],
  "satiety": { "beforePct": 20, "afterPct": 68,
               "checkins": [ { "checkinOffsetHours": 3, "satietyPct": 40 } ],
               "hungerReturnMinutes": 60 },
  "feedback": { "summary": "…", "suggestions": [ … ] } }
```

| 필드              | 설명                                |
| ----------------- | ----------------------------------- |
| `amount` + `unit` | `g` / `개` / `ml` 등. g 환산은 서버 |
| `confidence`      | 0.0~1.0. 0.8 미만은 FE 강조         |
| `matched`         | `false` → 영양정보 없음             |
| `isRecalculation` | 사용자 수정 후 재분석 여부          |

---

#### 평가

### GET /meals/{mealId}/feedback

```json
{
  "feedbackStatus": "READY",
  "summary": "유지기 기준 포만감이 부족한 식사예요.",
  "reasoning": "지금은 포만감 유지가 중요한데 단백질 비중이 낮았어요.",
  "suggestions": [
    {
      "foodName": "두부 반 모",
      "nutrients": [{ "code": "PROTEIN", "amountG": 10 }],
      "advice": "단백질을 10g 더 채워요"
    },
    {
      "foodName": "나물 한 접시",
      "nutrients": [{ "code": "FIBER", "amountG": 4 }],
      "advice": "식이섬유로 포만감을 늘려요"
    }
  ],
  "expectedSatietyPct": { "current": 62, "after": 79 },
  "safetyStatus": "SAFE"
}
```

`safetyStatus: BLOCKED` → `summary` · `suggestions` 미표시, 상담 안내로 대체.

#### 장기 피드백
### GET /insights/long-term

```
?period=7d | 28d | all
```

```json
{ "period": { "from": "2026-07-25", "to": "2026-08-21" },
  "status": "READY",
  "dataSufficient": true,
  "trendSummary": "이번 달은 지난달보다 Quality가 올랐어요.",
  "recommendation": "저녁 포만감이 낮은 편이라 단백질을 앞으로 당겨 보세요.",
  "generatedAt": "2026-08-21T23:10:00+09:00",
  "stale": true,
  "staleReason": "MEAL_DELETED" }
```

`dataSufficient: false` → `recommendation` 미표시.

### POST /insights/long-term/refresh

```json
// request
{ "period": "28d" }

// 202
{ "status": "GENERATING", "pollIntervalMs": 1500 }
```

---

#### 일일 피드백

> 이 절은 기존 명세에 없어 새로 작성했다. 장기 피드백의 규약을 그대로 따랐다.

### GET /insights/daily

```
?date=2026-08-21     생략 시 오늘
```

```json
{
  "date": "2026-08-21",
  "status": "READY",
  "dataSufficient": true,
  "mealCount": 3,
  "scores": { "quantity": 68, "quality": 71, "satiety": 74 },
  "summary": "단백질이 고르게 들어간 하루였어요. 저녁만 포만감이 짧게 끝났어요.",
  "generatedAt": "2026-08-21T23:10:00+09:00",
  "stale": false,
  "staleReason": null,
  "safetyStatus": "SAFE"
}
```

| 필드 | 설명 |
| --- | --- |
| `status` | `GENERATING` / `READY` / `UNAVAILABLE` |
| `dataSufficient` | `false` → `summary` 미표시. 그날 `EVALUATED` 식사가 없으면 `false` |
| `mealCount` | 집계에 들어간 끼니 수. `dataSufficient` 판단 근거를 화면에 보여주기 위함 |
| `scores` | 그날 `qqs_evaluations` 집계. `daily_feedbacks`의 점수 3개 |
| `stale` · `staleReason` | 장기 피드백과 동일. `MEAL_DELETED` 등 |
| `safetyStatus` | `BLOCKED` → `summary` 미표시, 상담 안내로 대체 |

### POST /insights/daily/refresh

```json
// request
{ "date": "2026-08-21" }

// 202
{ "status": "GENERATING", "pollIntervalMs": 1500 }
```

**설계 근거**

- 경로를 `/insights/daily`로 잡았다. 하루 요약은 특정 식사에 속하지 않으므로 `/meals/{mealId}/feedback` 계열이 아니라
  기간 집계인 `/insights/*` 계열이다. `GET`·`POST .../refresh` 쌍도 장기 피드백과 같게 뒀다.
- `recommendation`이 없다. `long_term_feedbacks`에는 있지만 `daily_feedbacks`에는 `summary` 컬럼뿐이다.
  "다음에 뭘 할지"는 이미 끼니별 `suggestions`가 담당한다 — 하루 단위에서 또 제안하면 중복된다.
- 장기 피드백의 `period` 대신 `date`를 쓴다. `daily_feedbacks`의 `UNIQUE (user_id, feedback_date)`와 1:1로 맞는다.
- AI 쪽은 `/short-feedback`의 `scope=DAILY`가 받는다(S2). 라우터를 새로 만들지 않는다.

이후 `GET /insights/long-term` 폴링.