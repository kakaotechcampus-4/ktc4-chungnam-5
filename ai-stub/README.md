# ai-stub — AI 레이어 더미 서버

AI 레이어가 아직 없다. BE 를 개발하는 동안 **요청을 받아 고정된 더미 응답을 돌려주는** 서버다.
모델을 부르지 않고, DB 도 S3 도 모른다.

실제 AI Service 는 [`../ai/`](../ai/) 에 AI 파트가 따로 만든다. 이 디렉터리는 그때까지의 발판이고,
붙은 뒤에도 CI 에서 Worker 통합 테스트를 돌릴 때 계속 쓸 수 있다 — 모델을 부르지 않아 빠르고 공짜다.

---

## 실행

**PowerShell (Windows)**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

uvicorn main:app --reload --port 8001   # http://localhost:8001/docs
pytest
```

`Activate.ps1` 이 실행 정책에 막히면 현재 세션에만 풀어 준다:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

**macOS · Linux**

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

활성화 없이 쓰려면 venv 의 파이썬을 직접 부른다. 결과는 같다:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001
.\.venv\Scripts\python.exe -m pytest
```

BE 쪽 `.env` 에 `AI_SERVICE_BASE_URL=http://localhost:8001` 을 넣는다.
BE API 가 8000 을 쓰므로 8001 로 띄운다.

컨테이너로 띄우려면:

```bash
docker compose -f ../infra/docker-compose.ai-stub.yml up -d
```

---

## 엔드포인트 3개

| 엔드포인트 | 하는 일 | 호출하는 쪽 |
|---|---|---|
| `POST /analyze-meal` | 사진·텍스트 → 음식 후보 목록 | BE Worker |
| `POST /short-feedback` | 한 끼(`scope=MEAL`) · 하루(`scope=DAILY`) 코멘트 | BE Worker |
| `POST /long-feedback` | 주간·월간·전체 추이 피드백 | BE Worker |

**BE API 는 이 서버를 직접 부르지 않는다.** API 는 큐에 넣고 202 를 돌려주고, Worker 가 꺼내서 호출한다.

계약의 자세한 모양은 `schemas.py` 를 보거나 서버를 띄우고 `/docs` 를 연다.

---

## 시나리오

`X-Stub-Scenario` 헤더로 응답을 고른다. 없으면 `SUCCESS`.

| 값 | 응답 | 이걸로 확인하는 BE 경로 |
|---|---|---|
| `SUCCESS` (기본) | 정상 더미 | `ANALYZING → REVIEW_REQUIRED` |
| `BLOCKED` | `safetyStatus=BLOCKED`, 본문 비움 | `medical_handoff_logs` 기록, 피드백 미노출 |
| `ERROR_500` | 500 | 재시도 → DLQ → `status=FAILED` |

```bash
curl -X POST localhost:8001/analyze-meal \
  -H 'Content-Type: application/json' \
  -H 'X-Stub-Scenario: ERROR_500' \
  -d '{"mealId":"m1","mealType":"LUNCH","eatenAt":"2026-08-21T12:40:00+09:00",
       "stage":"MAINTENANCE","rawText":"김밥"}'
```

본문이 아니라 헤더로 고르는 이유는, 실제 AI 가 붙으면 이 헤더를 그냥 무시하면 되기 때문이다.
요청 본문에 매직 문자열을 심으면 그게 실제 데이터에 섞여 들어간다.

---

## 더미지만 계약은 진짜다

응답 내용은 고정값이지만 **모양은 실제 계약과 같다.** BE 가 이 모양에 맞춰 매핑 코드를 쓰면
나중에 진짜 AI 로 바꿔도 안 깨진다. 요청도 검증한다 — 잘못 보내면 지금 422 로 알려준다.

계약에서 헷갈리기 쉬운 세 가지:

- **양은 g 가 아니다.** `{"estimatedAmount": 2, "unit": "개"}` 로 온다.
  비전 모델은 "계란 100g" 이 아니라 "계란 2개" 로 본다. g 환산은 `food_refs` 가 필요하므로 BE 몫이다.
- **성분값이 없다.** `kcal` · `proteinG` 같은 필드는 응답에 아예 없다.
  AI 는 `candidateFoodRefId` 로 음식을 지목만 하고, 성분은 BE 가 `food_refs` 에서 채운다.
  `candidateFoodRefId` 가 `null` 이면 매칭 실패다 — 공개 API 의 `matched: false` 에 해당한다.
- **점수를 만들지 않는다.** Q/Q/S 는 요청에 실어 보낸다. 채점은 BE Rule Engine 이 이미 끝냈고
  AI 는 그 숫자를 문장으로 옮길 뿐이다. `long_term_feedbacks.chart_data` 와
  `daily_feedbacks` 의 점수 3개도 같은 이유로 응답에 없다.

---

## 설계 문서

[`../docs/superpowers/specs/2026-09-13-ai-stub-design.md`](../docs/superpowers/specs/2026-09-13-ai-stub-design.md)

계약 전문, 공개 API 명세, 그리고 **명세와 DB 스키마가 어긋나는 5건**이 정리돼 있다.
