# ai — AI Service (FastAPI)

식사 사진·텍스트에서 **음식을 인식**하고, Q/Q/S 점수를 근거로 **피드백 문장을 생성**한다.

- 스택: **Python / FastAPI** (D2)
- 컨테이너 1개. BE와는 HTTP로만 대화한다 (D4)

---

## 이 파트가 지켜야 하는 절대 규칙

1. **AI Service는 DB를 모른다.** DB 접속 정보·S3 쓰기 권한·사용자 JWT를 가지지 않는다.
   데이터가 필요하면 BE `/internal/v1`을 **tool로 역호출**한다 (D5).
   → DB 스키마가 바뀌어도 JSON 계약만 유지되면 AI 코드는 안 깨진다.
2. **AI는 성분값을 생성하지 않는다.** 출력 스키마에 `kcal`·단백질 등 **숫자 필드를 두지 않는다.**
   AI는 "어떤 음식인지(`candidateFoodRefId`)"만 지목하고, 성분은 BE가 `food_refs`에서 채운다.
   프롬프트 부탁이 아니라 **스키마로 강제**한다.
3. **의료 조언 금지.** 입력·출력 양쪽에 가드레일을 건다. 위반 시 `safetyStatus=BLOCKED` →
   상담 안내로 대체. "이만큼 드세요" 같은 명령형 처방 톤도 금지 — 서술형("평소 대비 이만큼")으로만.
4. **tool 호출 상한 3회.** 컨텍스트 선주입으로 호출 자체를 줄이고, 상한은 설정값으로 뺀다.
5. **모델은 엔드포인트로 고정.** 팀 공용 크레딧이므로 임의 모델 호출을 구조로 막는다.
   게이트웨이는 URL 이 곧 모델이라, 설정에 있는 URL 두 개 외에는 호출할 수 없다.

---

## 디렉터리 구조

```
ai/
├─ app/          FastAPI 앱 · 라우터 3개 · 설정
├─ agents/       Agent별 프롬프트 + 엔드포인트 선택(vision/reasoning) + 출력 스키마
├─ tools/        BE /internal/v1 클라이언트 (호출 상한 · 타임아웃 · 로깅)
├─ guardrail/    의료 질문 감지 · 출력 검증 (safetyStatus 판정)
└─ tests/        pytest (fixtures 포함)
```

### 라우터 3개

| 엔드포인트             | 하는 일                      | 모델 성격   | 지연    |
| ---------------------- | ---------------------------- | ----------- | ------- |
| `POST /analyze-meal`   | 사진·텍스트 → 음식 후보 목록 | 저비용 비전 | 10~30초 |
| `POST /short-feedback` | 한 끼 Q/Q/S → 짧은 코멘트    | 소형 추론   | 5~15초  |
| `POST /long-feedback`  | 주간 추이 → 장기 피드백      | 추론        | 5~15초  |

> 라우터를 **늘리기 전에 통합을 먼저 검토**한다. 엔드포인트가 늘면 프롬프트·스키마·테스트가 3배로 는다.

### tool 3종 (BE 역호출)

- `search_foods(배열)` — 음식명 후보 → `food_refs` 검색. **배열로 받아 왕복을 줄인다**
- `get_recent_meals` — 최근 식사 맥락
- `get_qqs_series` — Q/Q/S 시계열 (장기 피드백용)

---

## 인식 결과는 "확정"이 아니라 "후보"다

비전 모델의 음식·양 추정은 틀린다(R3). 그래서:

- 규칙 기반 후처리로 보강하되, **모순·불확실은 확정하지 말고 후보로 넘긴다.**
- 애매한 항목에는 `clarifyQuestion`을 붙여 사용자가 `REVIEW_REQUIRED` 단계에서 고치게 한다.
- 사용자 수정 이력(`user_corrections`)은 다음 인식의 개인화 신호가 된다.

---

## 설정 (`app/config.py`)

**모든 모델 호출은 단일 진입점을 거친다.** 에이전트가 클라이언트를 직접 만들지 않는다.

- 엔드포인트 URL이 `https://mlapi.run/...`인지 **부팅 시점에 검증** — 다른 호스트면 기동 거부
- API 키·엔드포인트 URL은 `repr`에서 제외 — 로그·스택트레이스 유출 방지 (URL 도 비밀로 다룬다)
- LLM SDK 없이 `httpx`로 직접 POST 한다 — SDK는 base URL 뒤에 경로를 붙여 이 엔드포인트와 맞지 않는다
- 타임아웃·재시도 횟수를 설정값으로 노출 (재시도는 비용 × 지연의 곱셈이다)

사진 분석은 이미지 다운로드와 모든 모델 시도를 합쳐 `LLM_TIMEOUT_SEC` 안에 끝낸다.
모델 POST는 `LLM_MAX_RETRIES + 1`회까지이며(기본 2회), 전송 오류·408/429·일시적
5xx와 잘린 JSON·스키마 오류가 이 횟수를 공유한다. 400/401/403 등 요청 오류,
501, 모델의 명시적 거부는 추가 모델 재시도를 하지 않는다. BE 큐의 작업 재시도는
기존 별도 정책을 따른다. 재시도해도 프롬프트·사진은 동일하며 부분 JSON을 복구해
불완전한 음식 목록이나 빈 성공 응답으로 반환하지 않는다.

`/analyze-meal`은 모델 응답/통신 오류를 502, 시간 초과를 504, 비전 URL 미설정을
503으로 반환한다. 정상적인 `items: []`는 기존대로 허용한다. 운영 로그에는 시도 번호,
결과 분류, HTTP 상태, 허용된 종료 사유, 원문 길이만 남기고 음식명·응답 원문·URL·키는
추가하지 않는다. `invalid_json`과 `invalid_schema`를 구분하며, 종료 사유가 없다는
이유만으로 토큰 제한을 추측하지 않는다. 실제 잘린 응답의 회귀 검증은
`tests/test_vision_response_recovery.py`에서 모델 전송을 모킹해 수행한다.

## 환경변수

`.env.example` 참고.

| 키                                         | 설명                      |
| ------------------------------------------ | ------------------------- |
| `LLM_API_KEY`                              | Serverless API 키  |
| `LLM_VISION_URL`                           | 음식 인식 (Gemini Flash)  |
| `LLM_REASONING_URL`                        | 피드백 모델 엔드포인트  |
| `LLM_COACH_URL`                            | AI Coach 전용 모델 엔드포인트 |
| `LLM_TIMEOUT_SEC` · `LLM_MAX_RETRIES`      | 기본 45초 / 1회           |
| `BE_INTERNAL_BASE_URL`                     | BE `/internal/v1` 주소    |
| `INTERNAL_SERVICE_TOKEN`                   | BE와 공유하는 서비스 토큰 |
| `TOOL_CALL_LIMIT`                          | 기본 3                    |

---

## 실행

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --reload --port 8000
pytest
```

### 자동 회귀 테스트 (CI)

[`AI tests`](../.github/workflows/ai-tests.yml)는 `ai/**` 또는 워크플로 파일이 바뀐
`develop`·`main` 대상 PR과 두 브랜치의 push에서 실행됩니다. PR에서는 대상 브랜치와
합친 코드를 운영 Docker와 같은 Python 3.12로 검사합니다.

- `requirements.txt` 설치 후 `python -m pytest -q --tb=short`로 전체 테스트를 실행합니다.
- 모델 호출은 모킹하므로 API 키나 운영 DB가 필요 없습니다. 실제 모델 평가와는 별도입니다.
- 결과는 PR Checks에 표시되며, JUnit 결과는 `ai-test-results` 아티팩트로 7일간 보관합니다.

---

## 참고

- 개발 규칙: [`../CLAUDE.md`](../CLAUDE.md)

## short-feedback 연결 상태

- `short_feedback.py`가 목표 계산·영양 판정 → scope별 system/user 메시지 → reasoning 게이트웨이 호출 → 응답 파싱을 맡는다.
- `food_analyzer.py`와 같이 `config.get_settings()`와 `httpx.AsyncClient`를 사용한다.
  `LLM_REASONING_URL` 뒤에 `/v1/chat/completions`를 붙이고 OpenAI 호환 `messages`를 전송한다.
  2026-10-30 선택한 reasoning 엔드포인트에서 실제 끼니 피드백 생성·파싱을 확인했다.
  다른 모델 엔드포인트의 호환성, 다양한 입력의 품질과 BE 연동은 별도 검증 대상이다.
- HTTP 오류·전송 오류는 `LLM_MAX_RETRIES`만큼 추가 호출한다. 응답 형식 오류는 내부 재호출하지 않는다.
  최종 타임아웃은 API 504, 그 외 모델 호출·응답 오류는 502로 반환해 BE 워커가 실패를 처리하게 한다.
  오류 응답에는 게이트웨이 주소·키·모델 원문을 포함하지 않는다.
- `main.py`가 `modelVersion=reasoning-gateway`와 `safetyStatus`를 붙인다.
  모델 버전 라벨은 실제 모델 식별자가 아니며 모델 선택은 설정 URL이 담당한다.
- `safety_guard.py`는 입력 기록과 출력(body·reasoning·추천)의 명시적인 약물 용량 변경·복용 지시,
  일부 질병 단정 표현을 검사한다. 감지 시 원문·추천을 제거하고 `BLOCKED` 상담 안내를 반환한다.
  입력 차단 시 모델을 호출하지 않는다. 정규식 기반 최소 규칙으로 우회 표현·모든 의료 판단을 포괄하지 못하며,
  의료 내용을 서술한 기록도 보수적으로 차단할 수 있다.
- 자동 테스트는 실제 과금 호출 없이 HTTP 전송을 모킹한다. 실행: `cd ai && python -m pytest -q`.

### 누적 피드백 입력 계약 (2026-11-01)

MEAL 요청은 현재 끼니와 이전 확정 기록을 한 번의 생성 경로에 넣는다.
`body` 하나에 현재 끼니 평가와 누적 흐름을 담고, 기존 `reasoning`, `suggestions`,
`modelVersion`, `safetyStatus` 응답 구조를 유지한다. 별도 누적 출력이나 추가 DAILY 호출을 만들지 않는다.
문장은 현재 끼니 평가 → 이전 기록과의 관계 → 다음 식단 구성 방향으로 이어진다.
body 수치는 핵심 근거 0~2개만 선택하고, reasoning과 추천 카드가 같은 종합 방향을 구체화한다.
추천 우선순위도 현재·누적을 함께 본다. 하루 목표에 이미 도달한 성분의 추가 보충을 우선하지 않고,
현재 끼니가 무난해도 누적 나트륨 한도 초과를 반영한다. 시간대별 부족 기준은 만들지 않는다.
기존 BE 전환을 위해 DAILY 요청과 새 필드가 없는 MEAL 요청도 계속 받는다.

| 추가 요청 필드 | 의미 |
| --- | --- |
| `mealType` | BREAKFAST / LUNCH / DINNER / SNACK. 저녁의 SAFE 응답은 추천을 강제로 `[]`로 반환 |
| `skipped` | 명시적 결식. true면 items=[], satiety=null, QQS 세 축 모두 null |
| `weightKg` | 양수 또는 null. 목표 계산에 사용하는 체중 kg. **회의 합의를 반영한 필드로 BE 전달 확인 필요** |
| `previousMeals` | KST 당일 현재 끼니보다 앞선 확정 기록을 식사 순서로 전달 |
| `dailyNutritionTotals` | 현재 끼니 포함, **현재 끼니 시점까지**의 영양 합계 또는 null |

`previousMeals[]`는 `mealType`, `skipped`, `qqs`, `items[]`(displayName·amount·unit),
`nutritionTotals`를 받는다. 이전 피드백 텍스트나 음식별 성분을 요구하지 않는다.
목록에 없는 끼니를 결식으로 추측하지 않는다. 이전 기록의 명시적 결식은 음식·합계·점수가 없어야 한다.

`dailyNutritionTotals`와 이전 기록의 `nutritionTotals`는 `Nutrition` 구조
(`kcal`, `proteinG`, `fatG`, `carbG`, `fiberG`, `sodiumMg`)다. AI는 BE 누적 합계에 현재 끼니를
다시 더하거나, 누적 합계가 null일 때 음식 목록으로 복원하지 않는다.
현재 끼니 자체의 성분 합계는 기존처럼 `items[].nutrition`에서 계산한다.

목표는 BE v2 기준의 사본으로 Python에서 계산한다. 단백질은 하루 체중×0.91g(PRE_DOSE),
나머지 단계는 체중×1.2g, 식이섬유는 하루 25g(INITIAL은 25×2/3g), 나트륨 한도는 하루 2300mg이다.
끼니 기준은 반올림 전 하루 기준÷3이며, 표시·판정용 목표는 Decimal ROUND_HALF_UP으로 소수 한 자리까지 쓴다.
체중이 없으면 단백질 목표·충족률을 추정하지 않는다. 지방·탄수화물 목표와 열량 적정 범위는 만들지 않는다.

누적은 하루 목표 대비 진행률·남은 양과 나트륨 한도 초과량을 계산한다. 시간대별 부족 기준은 만들지 않는다.
BE 합계는 결측 성분이 0으로 합산돼 실제보다 작을 수 있으므로 기록에서 확인된 수치로만 설명한다.
나트륨의 남은 한도를 섭취 권장량으로 해석하지 않는다.

BE 인터페이스 원문의 누적 범위는 늦게 입력한 끼니 뒤의 기록도 포함할 수 있다고 돼 있지만,
이 구현은 후속 합의인 **현재 끼니 시점까지**를 전제로 한다. 식사 시각이 없는 요청에서는
AI가 범위를 검증할 수 없으므로 BE가 이 범위를 보장해야 한다.
체중은 기존 BE 규칙인 식사 시각 이전 마지막 기록, 없으면 가장 최근 기록을 전달한다는 전제다.
로컬 BE 코드는 이번 작업에서 변경하지 않으며, 실제 BE 전송·합계 범위·체중 선택은 통합 검증 대상이다.

## AI Coach (`POST /chat`)

`app/ai_coach.py`에 프롬프트 구성, 입력·출력 의료 요청 기본 규칙,
모델 응답 파싱을 구현했다. 계약 스키마는 `app/schemas.py`, 라우트는 `app/main.py`에 있다.
BE가 제공한 context와 history를 사용하며 별도 조회는 하지 않는다.
ID는 기존 BE/stub와 맞춰 문자열로 받고, history는 필수 배열이다.

`ai_coach.call_model(messages)`는 `.env`의 `LLM_API_KEY`와 `LLM_COACH_URL`을 사용한다.
`CoachSettings`로 코치에 필요한 설정만 검증하므로 vision 설정과 독립적으로 실행된다.
OpenAI 호환 `/v1/chat/completions` 경로로 호출하며, 현재 게이트웨이는
`max_completion_tokens`를 요구하고 비스트리밍 응답은 최대 2000토큰까지 허용한다.
명백한 의료 요청은 모델 호출 전에 BLOCKED로 반환한다.
25초 전체 호출 제한을 적용하고 재시도하지 않는다. 게이트웨이/설정 오류는 503으로 반환한다.
타임아웃은 504, 모델 JSON 계약 위반은 502로 반환한다.
자동 테스트는 호출부 또는 HTTP 전송을 대체하므로 외부 API를 호출하지 않는다.

- SAFE는 공백 아닌 text가 필요하다. BLOCKED/REVIEW_REQUIRED는 본문과 카드를 비운다.
- 잘못된 개별 카드는 제거한다. 모델이 생성한 영양 필드와 미검증 식품 ID는 버린다.
- 질문·답변·음식명은 애플리케이션 로그에 기록하지 않는다.
- QQS null 축 평가 금지는 프롬프트 규칙이다. 실제 준수 여부는 모델 연결 후 평가해야 한다.
- 정규식 가드레일은 보조 규칙이며 의료 안전성을 보장하지 않는다.
  모델 연결 시 우회 표현·영문·문맥 의존 질문과 위험 출력에 대한 안전성 평가가 필요하다.

검증: `python -m pytest ai/tests -q` (저장소 루트에서 실행).


### 실제 모델 테스트

`ai/` 디렉터리에서 `.env`에 실제 키와 coach 기본 URL을 설정한 뒤 실행한다.
추가 패키지는 `requirements.txt`를 설치한다. 모델 사용 비용이 발생한다.

```bash
cd ai
python scripts/smoke_ai_coach.py
python scripts/smoke_ai_coach.py '편의점에서 살 수 있는 반찬으로 추천해줘'
```

합성 식사 기록으로 실제 앱의 `/chat` 라우트를 호출하고 상태 코드·소요 시간·처리된 답변을 출력한다.
기본 테스트는 실제 게이트웨이에서 HTTP 200, SAFE, 추천 카드 2개를 확인했다.
단일 호출 결과이며 추천 품질·안전성·지연에 대한 종합 평가는 별도로 필요하다.
