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

---

## 참고

- 개발 규칙: [`../CLAUDE.md`](../CLAUDE.md)


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
