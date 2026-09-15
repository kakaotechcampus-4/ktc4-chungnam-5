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
5. **모델은 화이트리스트로 고정.** 팀 공용 크레딧이므로 임의 모델 호출을 구조로 막는다.

---

## 디렉터리 구조

```
ai/
├─ app/          FastAPI 앱 · 라우터 3개 · 설정
├─ agents/       Agent별 프롬프트 + 모델 선택 + 출력 스키마
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

- `ALLOWED_MODELS` 화이트리스트 — 목록 밖 모델은 기동 시 거부
- API 키·base URL은 `repr`에서 제외 — 로그·스택트레이스 유출 방지
- base URL이 https인지 **부팅 시점에 검증**
- 타임아웃·재시도 횟수를 설정값으로 노출 (재시도는 비용 × 지연의 곱셈이다)

## 환경변수

`.env.example` 참고.

| 키                                         | 설명                      |
| ------------------------------------------ | ------------------------- |
| `LLM_API_KEY`                              | 모델 API 키               |
| `LLM_BASE_URL`                             | https 필수                |
| `LLM_MODEL_VISION` · `LLM_MODEL_REASONING` | 화이트리스트 내 값만      |
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