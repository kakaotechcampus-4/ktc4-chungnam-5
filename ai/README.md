# ai — AI Service (FastAPI)

든든지기의 **음식 사진 인식 · 끼니 피드백 · AI Coach**를 담당한다.
Q/Q/S 채점과 음식 DB 매칭은 BE가 수행하고, AI는 인식 후보와 설명 문장을 반환한다.

- 스택: **Python 3.12 / FastAPI / Pydantic / httpx**
- 컨테이너 1개. BE와는 HTTP로 통신하며 DB에 직접 접속하지 않는다.
- 로컬 포트: BE `8000` · ai-stub `8001` · 실제 AI `8002`

---

## 이 파트가 지켜야 하는 절대 규칙

1. **AI Service는 DB를 모른다.** DB 접속 정보·S3 쓰기 권한·사용자 JWT를 가지지 않는다.
   현재는 BE가 요청에 필요한 맥락과 이미지 URL을 넣어 준다. BE `/internal/v1` tool 역호출은 미구현이다.
2. **AI는 음식의 영양성분 값을 생성하지 않는다.** 인식 결과와 추천 카드에 성분 필드를 두지 않는다.
   음식명·추정량을 반환하고 성분은 BE가 `food_refs`에서 채운다.
   피드백에 쓰는 성분은 BE 입력이며, 목표·충족률은 Python 코드로 계산한다.
3. **Q/Q/S는 BE가 채점한다.** 총점·가중치를 만들지 않는다.
   `null` 축은 측정 불가이므로 0점으로 해석하거나 평가하지 않는다.
   Quantity의 방향은 `intake`의 평소 대비 섭취 비율과 단계별 구간으로 설명한다.
   `intake`가 없으면 점수만 보고 과식·소식을 추측하지 않는다.
4. **의료 판단·처방 톤을 금지한다.** 증량·감량·단약·복용 지시를 하지 않는다.
   자유 텍스트를 생성하는 피드백·코치에는 입력·출력 가드레일을 적용한다.
   현재 정규식은 기본 방어이며, 모든 우회 표현이나 문맥을 판별하지 못한다.
5. **모델은 설정한 게이트웨이 URL로 선택한다.** 호출 코드는 `app/config.py`의 설정을 사용한다.
   API 키·모델 URL·약제·용량·이미지 키·사용자 기록·모델 원문을 로그나 커밋에 남기지 않는다.
6. **실제 모델 평가는 자동 회귀 테스트와 구분한다.** 공용 크레딧을 사용하므로
   [eval 실행 규칙](evals/README.md)에 따라 호출 수와 비용을 확인하고 실행한다.

---

## 디렉터리 구조

```text
ai/
├── app/
│   ├── main.py             라우트 · HTTP 오류 변환 · 피드백 안전성 검사
│   ├── config.py           공통 모델 설정 · 코치 전용 설정
│   ├── schemas.py          BE ↔ AI 요청·응답 계약 (JSON camelCase)
│   ├── food_analyzer.py    이미지 다운로드 · 비전 호출 · 인식 결과 검증
│   ├── food_names.py       검증된 음식명 동의 표기 정규화
│   ├── short_feedback.py  목표·영양 판정 · 누적 맥락 · 피드백 생성
│   ├── safety_guard.py    피드백 의료 표현 검사
│   └── ai_coach.py        코치 프롬프트 · 모델 호출 · 카드 정리 · 의료 표현 검사
├── tests/                 모델 통신을 대체하는 pytest 회귀 테스트
├── evals/                 실제 모델 평가 · 골든셋 · baseline · 평가 도구
├── scripts/               AI Coach 실제 호출 smoke test
├── agents/                예약 디렉터리 (.gitkeep만 있음)
├── tools/                 예약 디렉터리 (.gitkeep만 있음)
└── guardrail/             예약 디렉터리 (.gitkeep만 있음)
```

현재 프롬프트와 가드레일 구현은 `app/`에 있다.
계약의 전체 필드·검증 조건은 [app/schemas.py](app/schemas.py),
BE/stub 계약은 [ai-stub/schemas.py](../ai-stub/schemas.py)를 참고한다.

## API와 구현 상태

| 엔드포인트 | 역할 | 현재 상태 |
| --- | --- | --- |
| `GET /health` | 프로세스 응답 확인 | `{"status":"ok"}`. 모델 설정·연결은 검사하지 않음 |
| `POST /analyze-meal` | 식사 사진 → 음식명·추정량·단위·신뢰도·확인 질문 | 구현됨. 사진만 받음 |
| `POST /short-feedback` | Q/Q/S와 식사 기록 → 설명·근거·다음 끼니 제안 | 구현됨. MEAL 사용, DAILY는 호환 경로 유지 |
| `POST /chat` | 질문·대화 이력·BE 맥락 → 답변·음식 추천 카드 | 구현됨 |
| `POST /long-feedback` | 장기 추이 피드백 | **미구현. 항상 HTTP 501** |

### 사진 인식

`imageUrl`은 필수다. 텍스트 음식 입력은 BE가 직접 매칭하므로 AI를 거치지 않는다.
AI가 이미지를 내려받아 비전 모델에 보내고, 응답 스키마를 검사한 뒤 등록된 음식명 동의 표기만 정규화한다.
예를 들어 계란 프라이·달걀 후라이는 `달걀후라이`로 모은다. 임의로 재료나 조리법을 바꾸지 않는다.

결과는 확정값이 아닌 **후보**다. BE의 `REVIEW_REQUIRED` 단계에서 사용자가 음식과 양을 수정한다.
성분 필드는 없으며, DB 검색 tool이 없어 AI가 검증된 `candidateFoodRefId`를 조회해 채우는 경로도 없다.
정상적인 `items: []`는 허용하지만, 잘린 JSON을 복구해 부분 결과나 빈 성공으로 반환하지 않는다.
자유 텍스트 조언을 생성하지 않는 이 라우트의 `safetyStatus`는 `SAFE`로 반환한다.

### 끼니 피드백

MEAL 한 번의 호출에 현재 끼니 평가와 누적 흐름을 함께 담는다.
응답은 `body` · `reasoning` · `suggestions` · `modelVersion` · `safetyStatus`다.
현재 끼니 → 앞선 기록과의 관계 → 다음 식단 방향으로 설명하며 별도의 DAILY 호출을 요구하지 않는다.

| 주요 입력 | 해석 |
| --- | --- |
| `stage` · `qqs` | 채점 시점의 단계와 BE 점수. `null` 축은 평가하지 않음 |
| `intake` | `ratioPct`와 `rangePct`. Quantity 방향의 근거이며 단계 구간을 AI가 따로 만들지 않음 |
| `items[].nutrition` | BE가 제공한 음식별 성분. 현재 끼니 합계 계산에 사용 |
| `weightKg` | 목표 계산용 체중. 없으면 단백질 목표·충족률을 추정하지 않음 |
| `previousMeals` | KST 당일 현재 끼니보다 앞선 확정 기록. 목록에 없는 끼니를 결식으로 추측하지 않음 |
| `dailyNutritionTotals` | AI는 현재 끼니를 포함한 **현재 끼니 시점까지**의 합계로 해석. 현재 끼니를 다시 더하지 않음 |
| `mealType` · `skipped` | 저녁의 SAFE 응답은 `suggestions=[]`. 명시적 결식은 음식·포만감·섭취 비율·Q/Q/S 점수가 없어야 함 |

이전 끼니에는 음식명·양·단위·Q/Q/S·영양 합계를 받고, 이전 피드백 문장은 요구하지 않는다.
누적 합계가 없으면 음식 목록으로 복원하지 않는다. 시간대별 부족 기준을 만들거나
나트륨의 남은 한도를 섭취 권장량으로 해석하지 않는다.

목표 계산은 BE 기준의 사본이며 현재 `BE_RULE_VERSION="v3"`다.
기준을 바꿀 때는 [short_feedback.py](app/short_feedback.py)와
[BE 정본 동기화 테스트](tests/test_eval_be_sync.py)를 함께 확인한다.
끼니 목표는 하루 목표를 3으로 나눈 뒤 `Decimal ROUND_HALF_UP`으로 소수 한 자리까지 계산한다.
지방·탄수화물 목표와 열량 적정 범위는 만들지 않는다.

실험과 초기 설정은 아침 → 점심 → 저녁 순서로 입력하는 것을 전제로 한다.
이 전제에서 BE가 전달하는 당일 전체 누적 영양은 현재 끼니까지의 합계와 같다.

입력·출력에서 의료 표현이 감지되면 `BLOCKED` 상담 안내로 대체하고 근거·추천은 제거한다.
입력 차단 시 모델을 호출하지 않는다. 모델이 생성한 식품 ID·영양 필드는 추천 파싱에서 버린다.

### AI Coach

BE가 넘긴 `text` · `history` · `context`를 사용하며 별도 DB 조회나 tool 호출은 하지 않는다.
`history`는 필수 배열이며 빈 배열도 허용한다. 맥락에는 단계·오늘 기록·기준 끼니·장기 요약 등이 들어간다.

- SAFE는 공백 아닌 `text`가 필요하다. BLOCKED/REVIEW_REQUIRED는 `text=null`, `foodCards=[]`다.
- 잘못된 개별 카드는 제거하고, 모델이 만든 영양 필드와 미검증 식품 ID는 버린다.
- 명백한 의료 요청은 모델 호출 전에 차단하고 출력에도 의료 표현 검사를 적용한다.
- Q/Q/S의 null 축 평가 금지 등 프롬프트 규칙의 실제 준수 여부는 모델 평가에서 확인한다.

---

## 실행

### 로컬 실행

저장소 루트에서 시작한다. AI 단독 실행에는 DB나 BE 서버가 필요하지 않지만,
사진 분석에 사용하는 `imageUrl`은 AI 프로세스에서 접근할 수 있어야 한다.

```bash
cd ai
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# .env가 없을 때만 복사하고, 팀에서 받은 실제 키와 기본 URL을 채운다
cp -n .env.example .env

python -m uvicorn app.main:app --reload --port 8002
```

Windows PowerShell에서는 가상환경을 `py -3.12 -m venv .venv`로 만들고
`.\.venv\Scripts\Activate.ps1`로 활성화한다.
환경 파일은 `if (!(Test-Path .env)) { Copy-Item .env.example .env }`로 복사한다.

별도 터미널에서 확인한다.

```bash
curl http://127.0.0.1:8002/health
# {"status":"ok"}
```

요청·응답 스키마는 http://127.0.0.1:8002/docs 에서 확인한다.
`/health` 성공은 모델 호출 성공을 의미하지 않는다.

### Docker 실행과 BE 연결

`ai/.env`를 준비한 뒤 **저장소 루트에서** 실행한다.

```bash
docker compose -f infra/docker-compose.ai.yml up -d --build
curl http://127.0.0.1:8002/health
```

호스트 `8002`가 AI 컨테이너의 `8000`으로 연결된다.

| BE 실행 위치 | 실제 AI 연결 설정 |
| --- | --- |
| BE·AI 모두 호스트 | BE 환경변수 `AI_SERVICE_BASE_URL=http://localhost:8002` |
| BE·AI 모두 저장소 Compose | `BE_AI_SERVICE_BASE_URL=http://ai:8000`으로 BE Compose 실행 |

BE의 DB·마이그레이션 등은 [backend/README.md](../backend/README.md)에 따라 준비한다.
준비된 BE Compose를 실제 AI에 연결하는 명령은 다음과 같다(macOS · Linux).

```bash
BE_AI_SERVICE_BASE_URL=http://ai:8000 docker compose -f infra/docker-compose.be.yml up -d
```

BE의 기본 연결 대상은 ai-stub다. 컨테이너 간에는 호스트 포트 대신 서비스 이름을 쓴다.
BE가 넘기는 이미지 URL도 AI가 접근할 수 있어야 한다.
BE Compose의 `LOCAL_STORAGE_BASE_URL` 기본값은 `http://api:8000/media`다.

## 환경변수와 모델 호출

[.env.example](.env.example)을 참고한다. 로컬 `.env`는 **현재 작업 디렉터리** 기준으로 읽으므로
서버·테스트·평가 명령을 `ai/`에서 실행한다.

| 키 | 현재 사용 |
| --- | --- |
| `LLM_API_KEY` | 공통 게이트웨이 API 키 |
| `LLM_VISION_URL` | 사진 인식 기본 URL. 비어 있으면 사진 분석은 503 |
| `LLM_REASONING_URL` | 피드백 기본 URL. 공통 Settings의 필수 값이므로 사진 분석만 실행해도 유효한 값 필요 |
| `LLM_COACH_URL` | 코치 기본 URL. 별도 CoachSettings로 읽어 vision/reasoning 설정과 독립 |
| `LLM_TIMEOUT_SEC` | 기본 45초. 사진 분석 전체 제한, 피드백 httpx 타임아웃에 사용 |
| `LLM_MAX_RETRIES` | 기본 1. 사진 분석·피드백 모델 POST 최대 2회 |
| `BE_INTERNAL_BASE_URL` · `INTERNAL_SERVICE_TOKEN` · `TOOL_CALL_LIMIT` | 예시 파일에만 있는 예약 설정. 현재 Settings와 tool 구현에서 사용하지 않음 |

URL은 `https://mlapi.run/...` 접두사를 검사한다. 코드가 `/v1/chat/completions`를 붙이므로
설정에는 기본 URL을 넣는다. 비전 URL은 끝에 `/`를 붙이지 않는다.
`modelVersion`은 코드의 표시용 라벨이며 실제 모델은 게이트웨이 URL이 정한다.

**설정은 부팅 시 일괄 검증하지 않는다.** `get_settings()`와 `get_coach_settings()`의
첫 호출 때 읽고 캐시하므로 설정을 바꾸면 프로세스를 재시작한다.
사진 분석·피드백은 공통 Settings를 사용하고, 코치는 코치 전용 Settings를 사용한다.
모델 HTTP 호출은 각 기능 모듈의 `httpx.AsyncClient`에 구현돼 있다.

### 시간 제한·재시도·오류 응답

| 기능 | 시간 제한과 재시도 | HTTP 오류 |
| --- | --- | --- |
| 사진 분석 | 이미지 다운로드와 모든 시도 합계 `LLM_TIMEOUT_SEC`. 전송 오류·408/429·일시적 5xx(501 제외)·JSON/스키마 오류가 재시도 예산을 공유 | 비전 URL 미설정 503, 시간 초과 504, 모델 통신·응답 오류 502 |
| 피드백 | httpx 타임아웃 `LLM_TIMEOUT_SEC`. HTTP 상태 오류·전송 오류만 재시도하며 응답 파싱 오류는 재시도하지 않음. 전체 호출 시간 제한은 아님 | 시간 초과 504, HTTP·응답 오류 502 |
| 코치 | 전체 모델 호출 25초, 재시도 없음. 응답 토큰 상한 2000 | 설정·게이트웨이 오류 503, 시간 초과 504, 응답 계약 위반 502 |

사진 분석은 400/401/403 등 요청 오류·501·명시적 모델 거부를 재시도하지 않는다.
운영 로그에는 시도 번호·결과 분류·HTTP 상태·허용된 종료 사유·원문 길이 등 메타데이터만 남긴다.
BE 큐의 작업 재시도는 위 모델 내부 재시도와 별도다.

## 테스트 전략

### 키 없는 자동 회귀 테스트

`ai/`에서 실행한다.

```bash
python -m pytest -q --tb=short
```

| 대상 | 검증 내용 |
| --- | --- |
| API·스키마 | 요청 검증, 응답 계약, 상태 코드, 미구현 501 |
| 사진 인식 | 이미지·모델 전송 모킹, 잘린 JSON·스키마 오류·재시도, 음식명 정규화 |
| 끼니 피드백 | 목표·누적 계산, 결측·결식, 프롬프트, 추천·가드레일 |
| 코치 | 설정 분리, 입력·출력 차단, 카드 정리, 모델 오류 |
| eval 검증 | 판정 함수·케이스·하네스, AI 사본 상수와 BE 기준 일치 |

실제 API 키·모델 호출·운영 DB는 필요 없다.
일부 테스트는 BE 소스와 ai-stub 계약을 읽으므로 **모노레포 전체 체크아웃**에서 실행한다.

[AI tests](../.github/workflows/ai-tests.yml)는 `ai/**` 또는 워크플로 변경이 있는
`develop`·`main` 대상 PR과 해당 브랜치 push에서 Python 3.12로 실행한다.
따라서 `common/readme-refresh` 대상 파트 PR 자체에는 이 조건으로 실행되지 않는다.
JUnit 결과는 `ai-test-results` 아티팩트로 7일간 보관한다.

### 실제 모델 평가

골든셋·반복 횟수·통과 기준·baseline 비교는 [evals/README.md](evals/README.md)에서 관리한다.
자동 테스트 통과는 실제 모델의 품질·안전성·지연을 보장하지 않는다.
DAILY는 호환 코드가 남아 있지만 현재 BE가 호출하지 않으며 실제 평가 대상에서 제외한다.

코치 한 건의 연결 확인은 `ai/`에서 아래 명령으로 실행한다. **실제 모델 비용이 발생한다.**

```bash
python scripts/smoke_ai_coach.py
```

합성 식사 기록으로 앱의 `/chat` 라우트를 호출하고 상태 코드·소요 시간·처리된 답변을 출력한다.

## 미구현과 참고

- `/long-feedback`: 계약 확정과 모델 연결 필요. 현재 501.
- BE tool 역호출: `search_foods` · `get_recent_meals` · `get_qqs_series`는 초기 설계이며 미구현.
  tool 호출 상한 3회도 아직 적용되지 않는다.

개발·커밋 규칙은 [CLAUDE.md](../CLAUDE.md),
전체 실행·배포는 [루트 README](../README.md)와 [infra README](../infra/README.md)를 참고한다.
