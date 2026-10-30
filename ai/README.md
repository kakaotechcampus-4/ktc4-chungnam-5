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
| `LLM_REASONING_URL`                        | 피드백 (Claude Sonnet 5)  |
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

## short-feedback 연결 상태

- `short_feedback.py`가 영양 판정 → scope별 system/user 메시지 → reasoning 게이트웨이 호출 → 응답 파싱을 맡는다.
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
- JSON을 편집해 실제 모델의 중간 과정·피드백을 확인하려면 [수동 품질 테스트 도구](manual_feedback/README.md)를 사용한다.
  실험 결과와 `.env`는 커밋하지 않는다.
