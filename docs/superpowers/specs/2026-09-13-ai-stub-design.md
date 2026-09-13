# AI 스텁 서버 설계

- 작성일: 2026-09-13
- 브랜치: `be/4-feat-ai-stub`
- 대상 디렉터리: `ai-stub/`
- 관련 문서: [`../../../ai-stub/README.md`](../../../ai-stub/README.md) · [`../../../ai/README.md`](../../../ai/README.md) · [`../../../backend/README.md`](../../../backend/README.md)

---

## 1. 배경

BE Worker 는 식사 분석·피드백 생성 전 구간에서 AI Service 를 HTTP 로 호출한다.
AI Service 의 실제 구현은 아직 없고, BE Worker 도 아직 없다. 둘이 서로를 기다리면 양쪽 다 멈춘다.

BE 가 먼저 나아갈 수 있도록, **요청을 받아 고정된 더미 응답을 돌려주는 서버**를 만든다.

## 2. 목적

1. BE Worker 가 실제 LLM 없이 식사 파이프라인을 개발·테스트할 수 있게 한다.
2. BE↔AI JSON 계약을 코드로 확정한다. `ai-stub/schemas.py` 가 계약의 단일 원본이다.
3. Worker 의 실패 경로(재시도·DLQ·가드레일 차단)를 재현할 수 있게 한다.

## 3. 범위

**포함** — 엔드포인트 3개, 요청·응답 스키마, 고정 더미 응답, 시나리오 3종, pytest, Dockerfile·compose

**제외** — 실제 LLM 호출, BE `/internal/v1` 역호출, BE Worker 구현, 프롬프트 엔지니어링,
그리고 **실제 AI Service 의 내부 구조**(그건 AI 파트가 `ai/` 에 만든다)

## 4. 결정 사항

기존 `docs/decisions.md` 의 D 번호와 충돌하지 않도록 S 접두사를 쓴다.

| # | 결정 | 근거 |
|---|---|---|
| S1 | 스텁을 `ai-stub/` 에 **독립적으로** 둔다. `ai/` 는 AI 파트 몫으로 비워 둔다 | 이 서버는 실제 AI 의 축소판이 아니라 더미다. 공유할 로직이 없으므로 `ai/` 안에 모드로 넣을 이유가 없다. 소유권도 갈린다 — 이건 BE 가 만들고 BE 가 쓴다 |
| S2 | 일일 피드백은 `/short-feedback` 에 `scope: MEAL / DAILY` 로 받는다 | 라우터를 늘리면 스키마·테스트가 같이 는다(`ai/README.md`). `daily_feedbacks.summary` 는 추이가 아니라 요약이라 short 의 결과물 성격과 같다 |
| S3 | 스텁은 BE `/internal/v1` 을 역호출하지 않는다 | BE 에 `/internal/v1` 이 아직 없다. `candidateFoodRefId` 는 고정값 또는 `null` 로 돌려준다 — `meal_items.food_ref_id` 가 NULL 을 허용한다 |
| S4 | 응답은 **고정값**이다. 입력을 보고 내용을 바꾸지 않는다 | 되돌려주는 건 `mealId` 뿐이다. BE 가 응답을 파싱해 DB 에 넣는 코드를 짜는 데는 이걸로 충분하고, 테스트가 assert 를 걸 수 있다 |
| S5 | 시나리오는 `X-Stub-Scenario` **헤더**로 고른다 | 본문에 매직 문자열을 심으면 실제 데이터를 오염시키고, 진짜 AI 로 바꿀 때 본문 스키마가 달라진다. 헤더는 실제 구현이 무시하면 그만이다 |
| S6 | AI 응답에 집계 숫자를 두지 않는다 — `chartData` · 일일 점수는 BE 가 채운다 | `qqs_evaluations` 집계로 구할 수 있는 값이다. LLM 이 만들면 숫자가 틀려도 검증할 방법이 없다 |
| S7 | `contracts/` 를 폐기한다. 계약의 단일 원본은 `schemas.py` 이고 OpenAPI 는 런타임에 FastAPI 가 서빙한다 | 생성물을 커밋하면 원본(Pydantic)과 사본(JSON)이 갈라진다. 이 계약의 소비자는 BE Worker 하나뿐이고, 그쪽은 어차피 스텁을 띄워 놓고 개발한다 |

> S1 은 한 번 뒤집힌 결정이다. 처음에는 `ai/` 안에 `STUB_MODE` 플래그를 두고 실제 구현과
> 라우터·스키마·가드레일을 공유하는 안으로 잡았다. 그 전제 — "스텁이 실제 구현의 절반이 된다" —
> 가 틀렸다. 필요한 건 더미뿐이고, 공유할 로직이 없으면 같은 디렉터리에 둘 이유도 없다.

## 5. 구조

```
ai-stub/
├─ main.py          FastAPI 앱 · 엔드포인트 3개 · 시나리오 헤더
├─ schemas.py       요청·응답 계약  ★ 단일 원본
├─ fixtures.py      고정 더미 응답
├─ tests/
├─ Dockerfile
└─ requirements.txt
```

파일 3개, 250줄 남짓이다. 층을 두지 않는다 — 더미에 추상화를 얹을 이유가 없다.

`infra/docker-compose.ai-stub.yml` 로 띄운다. 호스트 포트 8001 (BE API 가 8000 을 쓴다).

## 6. 계약 — `POST /analyze-meal`

### 요청

```json
{
  "mealId": "meal_456",
  "imageUrl": "https://...presigned-GET",
  "rawText": "김밥 한 줄",
  "mealType": "LUNCH",
  "eatenAt": "2026-08-21T12:40:00+09:00",
  "stage": "MAINTENANCE"
}
```

`imageUrl` 은 presigned **URL** 이다 — `image_key` 가 아니다. AI 는 S3 권한이 없으므로
BE 가 URL 을 만들어 넘긴다. `imageUrl` · `rawText` 중 최소 하나는 필수이며,
`meals` 의 `CHECK (image_key IS NOT NULL OR raw_text IS NOT NULL)` 과 같은 제약을 Pydantic 으로 건다.

`stage` 는 컨텍스트 선주입이다. AI 는 DB 를 모르므로 `mealId` 로 나머지를 조회할 수 없다.

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

`items[]` 는 `meal_items` 행에 1:1 대응한다.

| 응답 필드 | `meal_items` 컬럼 | 비고 |
|---|---|---|
| `originalFoodName` | `original_food_name` | |
| `candidateFoodRefId` | `food_ref_id` (nullable) | 공개 API 의 `matched` · `nutritionSource` 는 이 값의 해석 결과다 |
| `estimatedAmount` + `unit` | `estimated_amount_g` | **BE 가 g 로 환산해 저장한다** |
| `confidence` | `confidence` | `CHECK (0 ~ 1)` 을 Pydantic `ge=0, le=1` 로 |
| 응답 전체 | `raw_ai_result` | JSONB 원본 저장 |

**양은 g 가 아니라 자연 단위로 온다.** 공개 API 명세가 `{"amount": 2, "unit": "개"}` 를 돌려주고
"g 환산은 서버" 라고 못박았다. 비전 모델은 "계란 100g" 이 아니라 "계란 2개" 로 본다.
개→g 환산에는 `food_refs` 의 1회 제공량이 필요한데 AI 는 그 테이블을 볼 수 없다.

**성분 숫자 필드는 스키마에 없다.** `kcal` · `proteinG` 같은 필드가 아예 존재하지 않는다.
공개 API 의 `nutrition` 블록은 전부 BE 가 `food_refs` 에서 채운 값이다.

**`clarifyQuestion` 은 항목별, 노출은 식사별.** AI 는 애매한 항목마다 붙이고,
공개 API 는 식사 단위로 하나를 돌려준다. BE 가 `confidence` 최저 항목의 질문 하나를 고른다.

`display_name` 은 AI 가 주지 않는다 — 최초에는 `original_food_name` 을 복사한다.
`source` 는 BE 가 `MODEL` 로 채운다.

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

`mealId` · `items` · `satiety` 대신 `date` 와 `meals[]`(그날 끼니 요약 배열)가 온다.
`qqs` 는 BE 가 그날 `qqs_evaluations` 를 집계한 값이다.

### 응답

```json
{
  "body": "유지기 기준으로 보면 포만감이 부족한 식사예요.",
  "reasoning": "지금은 포만감 유지가 중요한데 단백질 비중이 낮았어요.",
  "suggestions": [
    { "foodName": "두부 반 모", "candidateFoodRefId": "D004512",
      "advice": "단백질을 조금 더 채우는 쪽이에요" }
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

공개 API 가 `summary` 라고 부르는 필드를 AI 계약은 `body` 라고 부른다 — DB 컬럼명을 따랐다.
BE 가 내보낼 때 이름을 바꾼다.

### Q/Q/S 는 요청에 실려 온다

AI 는 점수를 계산하지 않는다. 채점은 Rule Engine(순수 함수)이 이미 끝냈고 AI 는 그 숫자를 문장으로 옮긴다.

### `items[].nutrition` 은 예외가 아니다

`reasoning` 예시("단백질 비중이 낮았어요")를 쓰려면 AI 가 성분을 알아야 한다.
**생성**은 금지지만 받는 것은 아니다. `nutrition` 은 BE 가 `food_refs` 에서 조회해 넣어 주는
컨텍스트 선주입이고 방향이 BE→AI 다. 응답 스키마에는 성분 필드가 없으므로 지어낼 경로가 없다.

### suggestions 에서 AI 가 채우는 것과 BE 가 채우는 것

공개 API 의 제안 한 건은 `{foodName, nutrients[], advice}` 다. `nutrients` 는 숫자이므로 AI 가 만들지 않는다.

| 필드 | 채우는 쪽 |
|---|---|
| `foodName` · `advice` | AI |
| `candidateFoodRefId` | AI — 지목만. `null` 이면 BE 가 `foodName` 으로 `food_refs` 검색 |
| `nutrients[]` | **BE** — `food_refs` 조회 |

`expectedSatietyPct`(`{"current": 62, "after": 79}`)도 **BE 가 채운다.**
`current` 는 `qqs_evaluations.satiety_score` 이고 `after` 는 제안 음식을 더해 Rule Engine 을 다시 돌린 값이다.
같은 채점기를 써야 화면의 숫자와 점수가 어긋나지 않는다.

## 8. 계약 — `POST /long-feedback`

### 요청

```json
{
  "userId": "uuid",
  "periodType": "WEEKLY",
  "periodStart": "2026-08-15",
  "periodEnd": "2026-08-21",
  "stage": "MAINTENANCE",
  "series": [{ "date": "2026-08-15", "quantity": 70, "quality": 61, "satiety": 75 }],
  "dailySummaries": ["...", "..."]
}
```

### 응답

`trendSummary` · `recommendation` · `modelVersion` · `safetyStatus`.

**`chartData` 는 없다**(S6). `long_term_feedbacks.chart_data` 는 BE 가 `qqs_evaluations` 를 집계해 채운다.

### periodType — 명세와 DB 가 어긋난다

공개 API 는 `?period=7d | 28d | all` 이고 `long_term_feedbacks.period_type` ENUM 은 `WEEKLY | MONTHLY` 다.
**`all` 에 대응하는 ENUM 값이 없다.**

AI 계약은 `WEEKLY | MONTHLY | ALL` 세 값을 받는다 — AI 에게 필요한 "어느 구간인가" 는
`periodStart` · `periodEnd` · `series` 가 이미 말해 주므로 세 값을 받아도 문제가 없다.
문제는 BE 쪽 저장이다. 12절에 열린 질문으로 남긴다.

`DAILY` 는 여기 없다 — `/short-feedback` 이 받는다 (S2).

### AI 가 받지 않는 것

| 공개 API 필드 | 채우는 쪽 | 근거 |
|---|---|---|
| `dataSufficient` | BE | `daily_feedbacks` 행 수를 세면 안다. `false` 면 **AI 를 호출하지 않는다** |
| `stale` · `staleReason` | BE | DB 변경 이벤트다 |
| `generatedAt` | BE | `long_term_feedbacks.created_at` |
| `status` | BE | 잡의 진행 상태 |

## 9. 시나리오

`X-Stub-Scenario` 헤더로 고른다. 없으면 `SUCCESS`.

| 값 | 스텁 동작 | 확인되는 BE 경로 |
|---|---|---|
| `SUCCESS` (기본) | 정상 더미 | `ANALYZING → REVIEW_REQUIRED` |
| `BLOCKED` | `safetyStatus=BLOCKED`, 본문 비움 | `medical_handoff_logs` 기록, 피드백 미노출 |
| `ERROR_500` | 500 | 재시도 → DLQ → `status=FAILED` |

`ERROR_500` 이 특히 중요하다. `backend/README.md` 의
"**AI 가 죽어도 Q/Q/S 는 남는다** — 피드백 생성 실패는 부분 실패로 처리한다" 를
실제로 테스트하려면 AI 를 마음대로 죽일 수 있어야 한다.

의료 키워드 감지는 **스텁이 하지 않는다.** 그건 실제 AI 의 가드레일 로직이고,
BE 가 확인해야 하는 건 "`BLOCKED` 을 받았을 때 어떻게 하는가" 뿐이다. 헤더로 부르면 된다.

## 10. 테스트

pytest + `TestClient`, 18개. 응답이 Pydantic 스키마를 만족하는지 검증한다 —
계약 위반이 테스트에서 바로 잡힌다. 더미 내용 자체는 테스트하지 않는다.

요청 검증도 건다. BE 가 잘못된 바디를 보내면 지금 422 로 알려준다.
없으면 진짜 AI 가 붙는 날 한꺼번에 터진다.

## 11. 후속 작업

1. BE Worker 식사 파이프라인 — 이 스텁을 상대로 개발
2. BE `/internal/v1` 구현 (서비스 토큰 인증 + tool 3종)
3. AI Service 실제 구현 — AI 파트가 `ai/` 에. 이 스텁의 `schemas.py` 가 계약 기준이 된다
4. 실제 AI 가 붙은 뒤에도 스텁은 CI 에 남긴다 — Worker 통합 테스트를 모델 호출 없이 돌린다

## 12. 열린 질문

### 이 브랜치 밖

- `docs/architecture.md` 와 `docs/decisions.md` 가 양쪽 README 에서 참조되지만 아직 없다.
  D 번호(D2·D4·D5·D9·D13, R3·R7)의 원본이 어디인지 확인 필요.
- `contracts/` 폐기(S7)에 따라 `ai/README.md` 의 두 군데를 고쳐야 한다 —
  "실행" 절의 Prism 목 서버 안내와 `[../contracts/]` 링크.
- `backend/README.md` 가 `cd ../infra && docker compose up -d` 로 `glp1-db` 를 띄우라고 하는데
  `infra/docker-compose.yml` 이 커밋돼 있지 않다.

### 공개 API 명세와 DB 스키마의 불일치

명세를 `meals` · `meal_items` · `satiety_logs` · `meal_feedbacks` · `long_term_feedbacks` 와
대조하며 찾은 것들이다. 전부 BE DB 쪽 결정이라 이 스펙에서 확정하지 않는다.

| # | 불일치 | 선택지 |
|---|---|---|
| 1 | 명세는 `amount` + `unit`(`개`·`ml`)을 돌려주는데 `meal_items` 에는 `estimated_amount_g` 만 있고 단위 컬럼이 없다 | `raw_ai_result` JSONB 에서 읽기 / `unit` 컬럼 추가 |
| 2 | 명세의 `suggestions` 는 `{foodName, nutrients[], advice}` 배열인데 `meal_feedbacks.suggestions` 는 TEXT 다 | TEXT 에 JSON 직렬화 / JSONB 로 변경 / 테이블 분리 |
| 3 | 명세의 `satiety.checkins[]` 에 대응하는 저장 위치가 없다. `satiety_logs` 는 식사당 1행이다 | `satiety_checkins` 테이블 추가 / JSONB 컬럼 |
| 4 | 명세의 `?period=all` 에 대응하는 `long_term_feedbacks.period_type` ENUM 값이 없다 | ENUM 에 `ALL` 추가 / `MONTHLY` 로 접고 UNIQUE 충돌 감수 |
| 5 | `ai/README.md` 규칙 3 은 명령형 처방 톤을 금지하는데("이만큼 드세요" 금지, 서술형만), 명세의 예시 문구가 명령형이다 — "단백질을 10g 더 채워요", "단백질을 앞으로 당겨 보세요" | 명세 예시를 서술형으로 고치기 / 규칙 3 의 경계를 다시 정의하기 |

1·3번은 이미 머지 대기 중인 BE-2 스키마에 걸린다. 컬럼을 추가하는 쪽을 고르면 alembic 마이그레이션이 하나 더 필요하다.
5번은 실제 AI 의 가드레일 구현에 영향을 준다 — 스텁 범위는 아니다.

---

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