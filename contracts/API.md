# API 명세

---

## 규약

```
Base path   /api/v1
네이밍       camelCase
ID          문자열
날짜         ISO 8601 (+09:00)
응답 래퍼    { success, data, error }
분기 기준    error.code (HTTP status 아님)
비동기       202 + 폴링
목록         cursor 페이지네이션
```

```json
{ "success": true,  "data": { }, "error": null }
{ "success": false, "data": null, "error": { "code": "STAGE_NOT_SET", "message": "…" } }
```

아래 모든 예시는 `data` 안의 내용만 표기합니다.

---

## 열거형

```
OnboardingStatus   PROFILE_REQUIRED | MEDICATION_REQUIRED | READY
MedicationStage    INITIAL | TITRATION | MAINTENANCE
DoseDirection      INCREASE | DECREASE | MAINTAIN
AppetiteLevel      1 | 2 | 3 | 4 | 5
GiSymptom          NAUSEA | VOMITING | HEARTBURN | CONSTIPATION | DIARRHEA | BLOATING | ABDOMINAL_PAIN
GiSeverity         MILD | MODERATE | SEVERE
MealType           BREAKFAST | LUNCH | DINNER | SNACK
InputType          PHOTO | TEXT
MealStatus         ANALYZING | REVIEW_REQUIRED | CONFIRMED | EVALUATED | FAILED
StepKey            FOOD_RECOGNITION | DB_MATCHING | STAGE_RULE_APPLY
StepState          PENDING | RUNNING | DONE | FAILED
NutritionSource    PUBLIC_DB | USER_INPUT
NutrientCode       CALORIE | PROTEIN | FAT | CARB | FIBER | SODIUM
NutrientState      SHORT | OK | OVER
FeedbackStatus     PENDING | READY | FAILED | GENERATING
SafetyStatus       SAFE | BLOCKED | REVIEW_REQUIRED
StaleReason        MEAL_DELETED | MEAL_EDITED | STAGE_CHANGED | NEW_MEALS
DashboardPeriod    7d | 28d | all
```

스코어는 모두 0~100 정수. 포만감은 0~100 (%).

---

## 엔드포인트

| 메서드 | 경로 |
| --- | --- |
| POST | `/users/profile` |
| GET | `/users/me` |
| PATCH | `/users/me` |
| POST | `/medications` |
| GET | `/medications/current` |
| GET | `/medications/dose-events` |
| POST | `/user-states` |
| GET | `/user-states/latest` |
| GET | `/home` |
| POST | `/meals` |
| GET | `/meals/{mealId}` |
| PATCH | `/meals/{mealId}/items` |
| POST | `/meals/{mealId}/items` |
| DELETE | `/meals/{mealId}/items/{itemId}` |
| GET | `/nutrition/candidates` |
| PUT | `/meals/{mealId}/items/{itemId}/nutrition` |
| POST | `/meals/{mealId}/confirm` |
| GET | `/meals/{mealId}/evaluation` |
| GET | `/meals/{mealId}/feedback` |
| POST | `/meals/{mealId}/satiety-checkins` |
| GET | `/meals` |
| GET | `/meals/calendar` |
| DELETE | `/meals/{mealId}` |
| GET | `/dashboard` |
| GET | `/insights/long-term` |
| POST | `/insights/long-term/refresh` |

---

## 사용자

### POST /users/profile

```json
// request
{ "nickname": "종호", "heightCm": 174.0, "weightKg": 79.0, "baselineIntake": 2200 }

// 201
{ "userId": "usr_01H8", "nickname": "종호",
  "heightCm": 174.0, "weightKg": 79.0, "baselineIntake": 2200,
  "onboardingStatus": "MEDICATION_REQUIRED",
  "createdAt": "2026-08-21T09:12:00+09:00" }
```

### GET /users/me

```json
{ "userId": "usr_01H8", "nickname": "종호",
  "heightCm": 174.0, "weightKg": 78.4, "baselineIntake": 2200,
  "onboardingStatus": "READY" }
```

| `onboardingStatus` | 진입 화면 |
| --- | --- |
| `PROFILE_REQUIRED` | 프로필 입력 |
| `MEDICATION_REQUIRED` | 투약 정보 입력 |
| `READY` | 홈 |

### PATCH /users/me

```json
// request
{ "weightKg": 78.4 }

// 200 — GET /users/me 와 동일 구조
```

수정 가능 필드: `nickname`, `heightCm`, `weightKg`, `baselineIntake`

---

## 투약

### POST /medications

등록·수정 겸용.

```json
// request
{ "drugName": "위고비", "doseMg": 1.0, "startedAt": "2026-06-14" }

// 200
{ "medicationId": "med_01H8",
  "drugName": "위고비", "doseMg": 1.0, "startedAt": "2026-06-14",
  "doseCount": 10,
  "nextDoseDate": "2026-08-23",
  "daysUntilNextDose": 2,
  "stage": "MAINTENANCE",
  "stageReason": "약효가 줄고 식욕이 돌아오는 구간",
  "ruleVersion": "v1",
  "doseChanged": false,
  "doseEvent": null,
  "stageChanged": false,
  "decidedAt": "2026-08-21T09:12:00+09:00" }
```

`doseCount` · `nextDoseDate` · `daysUntilNextDose` 는 서버 계산.

```
doseCount          = floor((today − startedAt) / 7) + 1
nextDoseDate       = startedAt + 7 × doseCount
daysUntilNextDose  = nextDoseDate − today
```

`doseMg` 가 현재 값과 다르면 `dose_events` 를 자동 기록.

```json
{ "doseMg": 1.7,
  "doseChanged": true,
  "doseEvent": { "doseEventId": "de_003", "doseMg": 1.7,
                 "direction": "INCREASE", "effectiveFrom": "2026-08-21" },
  "stage": "TITRATION",
  "stageChanged": true }
```

| 조건 | `direction` |
| --- | --- |
| 새 값 > 현재 값 | `INCREASE` |
| 새 값 < 현재 값 | `DECREASE` |
| 같음 | 기록 없음 |

### GET /medications/current

`POST /medications` 응답과 동일 구조. 미등록 시 `error.code = STAGE_NOT_SET`.

### GET /medications/dose-events

```json
{ "events": [
  { "doseEventId": "de_001", "doseMg": 0.25, "direction": "MAINTAIN", "effectiveFrom": "2026-06-14" },
  { "doseEventId": "de_002", "doseMg": 0.5,  "direction": "INCREASE", "effectiveFrom": "2026-07-12" },
  { "doseEventId": "de_003", "doseMg": 1.0,  "direction": "INCREASE", "effectiveFrom": "2026-08-09" }
] }
```

---

## 컨디션

### POST /user-states

```json
// request
{ "weightKg": 78.4,
  "appetiteLevel": 3,
  "giSymptoms": [ { "code": "NAUSEA", "severity": "MODERATE" } ],
  "note": null,
  "recordedAt": "2026-08-21T21:30:00+09:00" }

// 201
{ "userStateId": "ust_0142",
  "weightKg": 78.4,
  "weightChangeKg": -0.6,
  "weightChangeBaseline": "LAST_WEEK",
  "appetiteLevel": 3,
  "giSymptoms": [ { "code": "NAUSEA", "severity": "MODERATE" } ],
  "note": null,
  "recordedAt": "2026-08-21T21:30:00+09:00" }
```

증상 없음은 `giSymptoms: []`.

### GET /user-states/latest

위와 동일 구조.

---

## 홈

### GET /home

```json
{ "date": "2026-08-21",
  "medication": {
    "drugName": "위고비", "doseMg": 1.0, "doseCount": 10,
    "stage": "MAINTENANCE",
    "nextDoseDate": "2026-08-23", "daysUntilNextDose": 2,
    "doseChangeScheduled": false },
  "stomach": {
    "satietyPct": 68,
    "sourceMealId": "meal_456",
    "sourceMealAt": "2026-08-21T12:40:00+09:00",
    "minutesSinceMeal": 320,
    "feedbackSummary": "유지기 기준 포만감이 부족한 식사였어요." },
  "today": {
    "recordedCount": 2,
    "meals": [
      { "mealId": "meal_450", "mealType": "BREAKFAST",
        "eatenAt": "2026-08-21T08:20:00+09:00",
        "displayName": "토스트, 그릭요거트", "thumbnailUrl": "https://…",
        "scores": { "quantity": 74, "quality": 90, "satiety": 80 } }
    ],
    "missingMealTypes": ["DINNER"] } }
```

`stomach` 은 기록된 식단 중 가장 최근 것 기준.

---

## 식사 입력 · 분석

### POST /meals

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

### GET /meals/{mealId}

`status` 에 따라 폴링 · 확인 · 상세 조회에 모두 쓰입니다.

```json
 { "mealId": "meal_456",
  "status": "REVIEW_REQUIRED",
  "isRecalculation": false,
  "stage": "MAINTENANCE",
  "mealType": "LUNCH",
  "eatenAt": "2026-08-21T12:40:00+09:00",
  "imageUrl": "https://…",
  "steps": [
    { "key": "FOOD_RECOGNITION", "state": "DONE" },
    { "key": "DB_MATCHING",      "state": "RUNNING" },
    { "key": "STAGE_RULE_APPLY", "state": "PENDING" }
  ],
  "clarifyQuestion": "김밥 속재료가 참치가 맞나요?",
  "items": [
    { "itemId": "item_1", "displayName": "참치김밥",
      "amount": 250, "unit": "g",
      "confidence": 0.62, "matched": true,
      "nutritionSource": "PUBLIC_DB", "userConfirmed": false,
      "nutrition": { "kcal": 400, "proteinG": 12, "fatG": 10,
                     "carbG": 65, "fiberG": 4, "sodiumMg": 780 } },
    { "itemId": "item_2", "displayName": "삶은 계란",
      "amount": 2, "unit": "개",
      "confidence": 0.96, "matched": false,
      "nutritionSource": null, "userConfirmed": false,
      "nutrition": null }
  ] }
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

| 필드 | 설명 |
| --- | --- |
| `amount` + `unit` | `g` / `개` / `ml` 등. g 환산은 서버 |
| `confidence` | 0.0~1.0. 0.8 미만은 FE 강조 |
| `matched` | `false` → 영양정보 없음 |
| `isRecalculation` | 사용자 수정 후 재분석 여부 |

---

## 음식 수정

세 동작 모두 재계산을 유발하며 응답 형태가 동일합니다.

### PATCH /meals/{mealId}/items

```json
// request
{ "items": [ { "itemId": "item_1", "displayName": "참치김밥", "amount": 220, "unit": "g" } ] }

// 200
{ "status": "ANALYZING", "isRecalculation": true,
  "steps": [ { "key": "FOOD_RECOGNITION", "state": "DONE" },
             { "key": "DB_MATCHING",      "state": "RUNNING" },
             { "key": "STAGE_RULE_APPLY", "state": "PENDING" } ] }
```

### POST /meals/{mealId}/items

```json
// request
{ "displayName": "미역국", "amount": 200, "unit": "g" }

// 201
{ "itemId": "item_3", "matched": true, "nutrition": { … },
  "status": "ANALYZING", "isRecalculation": true }
```

### DELETE /meals/{mealId}/items/{itemId}

```json
{ "status": "ANALYZING", "isRecalculation": true }
```

---

## 영양정보 폴백

### GET /nutrition/candidates

```
?q=삶은 계란&limit=5
```

```json
{ "candidates": [
  { "foodRefId": "KFD_01023", "name": "달걀 (삶은 것)", "servingSizeG": 100,
    "nutrition": { "kcal": 155, "proteinG": 13, "fatG": 11,
                   "carbG": 1, "fiberG": 0, "sodiumMg": 124 } }
] }
```

### PUT /meals/{mealId}/items/{itemId}/nutrition

```json
// request — 후보 선택
{ "foodRefId": "KFD_01023" }

// request — 직접 입력
{ "manual": { "kcal": 150, "proteinG": 13, "fatG": 11,
              "carbG": 1, "fiberG": 0, "sodiumMg": 120 } }

// 200
{ "itemId": "item_2", "matched": true,
  "nutritionSource": "USER_INPUT",
  "nutrition": { … },
  "status": "ANALYZING", "isRecalculation": true }
```

---

## 평가

### POST /meals/{mealId}/confirm

```json
// request
{ "satietyAfterPct": 68 }

// 200
{ "mealId": "meal_456",
  "status": "EVALUATED",
  "stage": "MAINTENANCE",
  "scores": { "quantity": 75, "quality": 83, "satiety": 68 },
  "stageEmphasis": ["SATIETY", "QUALITY"],
  "nutrients": [
    { "code": "PROTEIN", "label": "단백질",   "current": 18,   "target": null, "unit": "g",  "state": null },
    { "code": "FIBER",   "label": "식이섬유", "current": 6,    "target": null, "unit": "g",  "state": null },
    { "code": "SODIUM",  "label": "나트륨",   "current": 1620, "target": null, "unit": "mg", "state": null }
  ],
  "evidence": {
    "dbSource": "식품안전나라 식품영양성분DB",
    "stageRuleVersion": "v1",
    "weightProfileVersion": "v1",
    "nutritionSources": ["PUBLIC_DB", "USER_INPUT"] },
  "feedbackStatus": "PENDING" }
```

`target` · `state` 는 `null` 허용. 미확정 시 게이지 미표시.

확정 전 호출 시 `409` · `error.code = NOT_CONFIRMED`.

### GET /meals/{mealId}/evaluation

`confirm` 응답과 동일 (`feedbackStatus` 제외).

### GET /meals/{mealId}/feedback

```json
{ "feedbackStatus": "READY",
  "summary": "유지기 기준 포만감이 부족한 식사예요.",
  "reasoning": "지금은 포만감 유지가 중요한데 단백질 비중이 낮았어요.",
  "suggestions": [
    { "foodName": "두부 반 모",
      "nutrients": [ { "code": "PROTEIN", "amountG": 10 } ],
      "advice": "단백질을 10g 더 채워요" },
    { "foodName": "나물 한 접시",
      "nutrients": [ { "code": "FIBER", "amountG": 4 } ],
      "advice": "식이섬유로 포만감을 늘려요" }
  ],
  "expectedSatietyPct": { "current": 62, "after": 79 },
  "safetyStatus": "SAFE" }
```

`safetyStatus: BLOCKED` → `summary` · `suggestions` 미표시, 상담 안내로 대체.

### POST /meals/{mealId}/satiety-checkins

```json
// request
{ "checkinOffsetHours": 3, "satietyPct": 40,
  "hungerReturnMinutes": 60, "comment": "오후 4시쯤 다시 배가 고팠어요" }

// 201
{ "checkinId": "sck_012", "mealId": "meal_456",
  "checkinOffsetHours": 3, "satietyPct": 40, "hungerReturnMinutes": 60 }
```

---

## 기록

### GET /meals

```
?date=2026-08-21
?cursor=eyJ…&limit=20&from=2026-08-01&to=2026-08-31
```

```json
{ "items": [
    { "mealId": "meal_450", "mealType": "BREAKFAST",
      "eatenAt": "2026-08-21T08:20:00+09:00", "stage": "MAINTENANCE",
      "displayName": "토스트, 그릭요거트", "thumbnailUrl": "https://…",
      "scores": { "quantity": 74, "quality": 90, "satiety": 80 } }
  ],
  "nextCursor": "eyJ…" }
```

### GET /meals/calendar

```
?month=2026-08
```

```json
{ "month": "2026-08",
  "days": [
    { "date": "2026-08-21", "count": 2,
      "recordedMealTypes": ["BREAKFAST", "LUNCH"], "stage": "MAINTENANCE" }
  ],
  "summary": { "totalMeals": 42,
               "avgScores": { "quantity": 74, "quality": 81, "satiety": 65 } } }
```

### DELETE /meals/{mealId}

soft delete.

```json
{ "mealId": "meal_456",
  "deletedAt": "2026-08-22T10:04:00+09:00",
  "affectedInsights": [ { "period": "28d", "stale": true, "staleReason": "MEAL_DELETED" } ] }
```

---

## 대시보드 · 장기 피드백

### GET /dashboard

```
?period=7d | 28d | all
```

```json
{ "period": { "type": "7d", "from": "2026-08-15", "to": "2026-08-21" },
  "series": [
    { "date": "2026-08-21", "stage": "MAINTENANCE",
      "quantity": 75, "quality": 83, "satiety": 68 }
  ],
  "averages": { "quantity": 74, "quality": 81, "satiety": 65 },
  "byMealType": {
    "BREAKFAST": { "quantity": 70, "quality": 88, "satiety": 78 },
    "LUNCH":     { "quantity": 76, "quality": 80, "satiety": 66 },
    "DINNER":    { "quantity": 78, "quality": 74, "satiety": 58 } },
  "monthlyAverages": [
    { "month": "2026-07", "quantity": 71, "quality": 76, "satiety": 61 },
    { "month": "2026-08", "quantity": 74, "quality": 81, "satiety": 65 } ],
  "weightSeries": [ { "date": "2026-08-21", "weightKg": 78.4 } ],
  "stageChanges": [ { "date": "2026-08-24", "from": "MAINTENANCE", "to": "TITRATION" } ] }
```

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

이후 `GET /insights/long-term` 폴링.

---

## 상태 전이

```
ANALYZING  →  REVIEW_REQUIRED  →  CONFIRMED  →  EVALUATED        FAILED
```

확인 단계는 `confidence` 와 무관하게 항상 거칩니다.
수정 시 `ANALYZING` + `isRecalculation: true` 로 되돌아갑니다.

---

## 에러 코드

| code | HTTP | FE 처리 |
| --- | --- | --- |
| `PROFILE_REQUIRED` | 409 | 프로필 입력 화면 |
| `STAGE_NOT_SET` | 409 | 투약 정보 입력 화면 |
| `NOT_CONFIRMED` | 409 | 확인 화면으로 되돌림 |
| `FOOD_NOT_RECOGNIZED` | 422 | 재촬영 / 텍스트 입력 |
| `ANALYSIS_TIMEOUT` | 504 | 재시도 / 텍스트 입력 |
| `LOW_CONFIDENCE` | 200 | `clarifyQuestion` 표시 |
| `NUTRITION_NOT_MATCHED` | 200 | 해당 항목 직접 입력 |
| `MEDICAL_QUESTION_DETECTED` | 200 | 상담 안내 후 복귀 |
| `FEEDBACK_GENERATION_FAILED` | 200 | 점수 유지, 피드백만 재시도 |

`LOW_CONFIDENCE` · `NUTRITION_NOT_MATCHED` 는 정상 흐름의 분기로 200 응답 안의 플래그로 표현됩니다.