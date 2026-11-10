# evals — AI 평가와 회귀 검증

프롬프트·모델·후처리를 바꿨을 때 **출력 계약과 규칙을 계속 지키는지** 확인한다.
같은 입력을 반복해 축별 통과율을 비교하고, 자동 판정이 어려운 품질은 저장된 응답을 사람이 검토한다.
LLM judge는 사용하지 않는다.

서비스 실행·API 계약은 [AI README](../README.md), 팀 공통 개발 규칙은 [CLAUDE.md](../../CLAUDE.md)를 따른다.

## 어떤 검증을 실행할까

| 목적 | 진입점 | 모델 호출 | DB·서버 |
| --- | --- | --- | --- |
| 판정 함수·하네스·케이스의 회귀 확인 | `../tests/test_eval_*.py` | 없음 | 외부 의존 없음. BE·stub 소스는 필요 |
| 끼니 피드백·사진 인식·코치 품질 평가 | `meal_feedback_eval` · `analyze_meal_eval` · `chat_eval` | 있음 | 앱은 프로세스 안의 TestClient로 호출 |
| 음식명 프롬프트 A/B 비교 | `food_name_ab` → `replay_food_db` → `score_food_name_ab` | A/B 생성 단계만 있음 | DB 재생 단계는 로컬 PostgreSQL 읽기 전용 |
| 음식명 정규화 전후 비교 | `food_name_normalization_eval` → `replay_food_db` | 없음 | 저장된 응답·동일 DB snapshot 사용 |
| 사진 업로드부터 BE 저장까지 확인 | `live_food_matching_eval` → `inspect_live_food_db` | `--run`에서 있음 | 별도 Compose DB·API·워커·AI |

골든셋 평가는 실제 앱 라우트의 파싱·가드레일을 거친 결과를 잰다.
A/B 도구는 비전 클라이언트와 파서를 직접 호출하며, 로컬 통합 검증은 BE 큐와 저장까지 포함한다.
서로 다른 호출 경로의 결과를 같은 조건의 개선율로 합산하지 않는다.

## 이 파트가 지켜야 하는 규칙

1. **실제 모델 실행 전 범위와 호출 수를 정한다.** 공용 크레딧을 사용하므로
   에이전트의 실제 호출은 메인 세션에서 사용자 승인(N · 예상 호출 수)을 받은 뒤 실행한다.
2. **입력·라벨·판정 조건을 먼저 고정한다.** 결과를 보고 같은 실험의 정답을 바꾸지 않는다.
   holdout은 개선용 데이터와 구분한다.
3. **평가 도구 수정과 서비스 동작 수정을 구분한다.** 평가 코드에서 발견한 앱 문제는
   재현 입력과 근거를 남기고 서비스 코드의 회귀 테스트로 연결한다.
4. **키·게이트웨이 URL·DB 접속 문자열을 결과나 Git에 남기지 않는다.**
   baseline과 실험 원문은 가림 처리 후에도 공유 전 내용을 확인한다.
5. **과거 결과를 소급 수정하지 않는다.** 새 실행은 새 결과 경로에 저장하고,
   기준선을 교체할 때는 비교 조건과 변경 이유를 함께 남긴다.
6. 판정 함수·케이스·하네스 테스트는 `ai/tests/`에 둔다.
   `ai/evals/`에는 `test_*.py`나 `tests/`를 만들지 않는다.

## 디렉터리 구조

```text
ai/evals/
├── harness.py                     반복 · 집계 · 게이트 · baseline · CLI
├── checks.py                      MEAL 판정과 공통 텍스트 판정
├── meal_feedback_eval.py           MEAL 케이스
├── analyze_meal_checks.py          사진 인식 판정
├── analyze_meal_eval.py            사진 라벨 → 케이스
├── fixture_server.py               사진 검증 · 로컬 이미지 서버
├── chat_checks.py                  코치 판정 · stub 계약 로딩
├── chat_eval.py                    코치 케이스
├── daily_checks.py                 DAILY 호환 판정
├── daily_feedback_eval.py          DAILY 호환 케이스 (실측 제외)
├── baselines/                     평가 기준선 JSON
├── fixtures/                      사진 · 음식명 매칭 manifest · 출처
├── food_name_ab.py                 기존/후보 프롬프트 비교
├── replay_food_db.py               저장 응답의 BE 매칭·영양 계산 재생
├── score_food_name_ab.py           사전 지정 DB 항목 연결 비교
├── food_name_normalization_eval.py 이름 정규화 전후 비교
├── docker-compose.live.yml         독립 로컬 통합 환경
├── live_db_bootstrap.py            실험 DB 스키마 준비·검증
├── live_db_seed.sh                 고정 seed 검증·복원
├── live_vision_app.py              실험용 비전 시도 기록
├── live_food_matching_eval.py      사진 업로드·분석 상태 수집
└── inspect_live_food_db.py         실행에 속한 DB 저장 결과 조회
```

## 키 없는 자동 회귀 테스트

[AI README의 환경 설정](../README.md)을 마친 뒤 저장소 루트에서 실행한다.

```bash
cd ai
python -m pytest tests/test_eval_*.py -q --tb=short
```

PowerShell 등에서 파일 패턴 확장이 다르면 `python -m pytest tests -q --tb=short`로
전체 AI 테스트를 실행한다. 모델 전송은 대체하므로 실제 키·운영 DB가 필요 없다.

검증 대상은 판정의 양방향 예시(통과할 답·실패할 답), 케이스 계약, 호출 수·오류 집계,
fixture 규칙, BE 정본과 AI 사본 상수의 일치다.
일부 테스트가 BE 소스와 `ai-stub/schemas.py`를 읽으므로 모노레포 전체 체크아웃에서 실행한다.

## 실제 모델 평가

### 설정과 호출 수

`ai/`에서 모듈로 실행하며 `.env`도 이 디렉터리 기준으로 읽는다.
모델 키·기본 URL·재시도 정책은 [AI README](../README.md)에 정리돼 있다.

현재 케이스 기본값 기준이다. 축마다 모델을 따로 부르지 않고 응답 하나를 여러 축으로 검사한다.

| 평가 | 케이스 | 기본 라우트 호출 | `--n 3` 라우트 호출 | 설정 |
| --- | ---: | ---: | ---: | --- |
| MEAL | 22 | 84 | 66 | `LLM_API_KEY` · `LLM_REASONING_URL` |
| 사진 인식 | 8 | 24 | 24 | 공통 Settings + `LLM_VISION_URL` |
| 코치 | 16 | 68 | 48 | `LLM_API_KEY` · `LLM_COACH_URL` |

- MEAL·사진 모델 POST 상한은 **계획 호출 수 × (`LLM_MAX_RETRIES` + 1)**다.
  기본 재시도 1이면 각각 최대 168회·48회다. 입력 차단·이미지 GET 실패 등은 모델 호출을 줄일 수 있다.
- 코치는 재시도하지 않는다. 현재 `dose_change` · `roleplay_doctor` · `ignore_instructions`가
  모델 전에 차단돼 **기본 모델 호출 계획은 53회**, `--n 3`이면 39회다.
  가드레일이나 입력을 바꾸면 이 수치도 다시 확인한다.
- 사진 평가는 공통 Settings를 쓰므로 유효한 reasoning URL도 필요하다.
  공통 키 검사 자체는 빈 vision URL을 허용한다. vision URL이 비어 있으면 SKIP이 아니라
  라우트 503에 따른 계약 실패가 된다.
- 코치는 별도 CoachSettings를 사용하므로 vision/reasoning 설정이 없어도 실행할 수 있다.

### 실행 예시

아래는 **실제 모델을 호출하는 명령**이다. 필요한 평가만 선택한다.

```bash
# ai/에서 실행
python -X utf8 -m evals.meal_feedback_eval --n 3
python -X utf8 -m evals.analyze_meal_eval --n 3
python -X utf8 -m evals.chat_eval --n 3

# 일부 케이스만 확인
python -X utf8 -m evals.meal_feedback_eval --only presentation_a1,presentation_a2,presentation_a3 --n 1
python -X utf8 -m evals.analyze_meal_eval --only piece_of_pizza --n 1
python -X utf8 -m evals.chat_eval --only binge_today,dose_change --n 1

# 기준선과 비교하고, 새 결과 파일에 저장 (기존 기준선을 덮어쓰지 않음)
python -X utf8 -m evals.meal_feedback_eval --n 3 \
  --baseline evals/baselines/meal_feedback_baseline.json \
  --save manual_feedback/meal_eval_run1.json
```

마지막 명령의 줄 연결은 macOS·Linux 셸 기준이다. PowerShell에서는 한 줄로 입력한다.
`--save`는 기존 파일을 덮어쓸 수 있고 게이트 FAIL 결과도 저장하므로, 실행마다 새 경로를 사용한다.
검토한 기준선은 `evals/baselines/`에서 버전 관리한다.

### 공통 옵션과 종료 코드

이 옵션은 하네스를 사용하는 MEAL·사진·코치·DAILY에 적용된다.
A/B·DB 재생·로컬 통합 도구는 각 절의 전용 옵션을 사용한다.

| 옵션 | 동작 |
| --- | --- |
| `--n N` | 케이스별 반복 횟수를 N으로 덮어씀. 1 이상 |
| `--only id1,id2` | 지정한 케이스만 실행. `--save`와 함께 사용 불가 |
| `--pass-ratio` | 별도 임계가 없는 non-critical 축의 기준. 기본 0.66 |
| `--baseline PATH` | 기존 통과율과 비교. 파일이 없으면 알리고 실행은 계속함 |
| `--save PATH` | 규칙 버전·프롬프트 해시·축별 결과 저장 |
| `--max-errors` | 허용할 하네스·판정 크래시 수. 기본 0 |

| 종료 코드 | 의미 |
| --- | --- |
| `0` | 게이트 PASS **또는 SKIP**. 출력 메시지를 함께 확인 |
| `1` | 게이트 FAIL |
| `2` | 잘못된 인자·케이스 0개·사진 fixture 문제 등 실행 전 오류 |

설정이 없거나 무효이면 모델 호출 없이 SKIP한다.
첫 요청이 404·501이면 미연결 SKIP하며, 정상 연결이면 이 첫 응답을 첫 회차로 재사용한다.
500·502·503·504 등은 SKIP이 아니라 `contract_parseable` 실패다.

### 결과 해석과 baseline

공통 채널은 `TestClient(app, raise_server_exceptions=False)`다.
실제 앱의 라우트·응답 변환·가드레일을 통과한 결과를 검사하므로, 앱이 강제한 동작을 모델 능력으로 해석하지 않는다.

| 등급 | 게이트 기준 |
| --- | --- |
| `critical` | 판정한 회차 중 한 번이라도 실패하면 FAIL |
| `non_critical` | 축별 임계 이상 통과. 별도 값이 없으면 0.66 |
| `ambiguous` | 관측용. 게이트에서 제외하고 통과·실패 응답을 모두 `samples`에 남김 |

결과 키는 `<case_id>/<axis_id>`다. `passed` · `n` · `errors` · `grade` ·
`pass_ratio` · `notes` · `samples`를 저장한다.

- 계약 실패 회차는 다른 축의 분모에 넣지 않는다.
- 하네스·판정 함수 예외는 `errors`로 세고 통과율 분모에서 뺀다.
  기본값에서는 크래시 한 번도 `_crashes` 게이트를 실패시킨다. 판정한 회차가 없는 축도 FAIL이다.
- 콘솔의 총 통과는 **케이스×판정 축** 집계다. 음식 인식 정확도나 사용자 만족률이 아니다.
- baseline 비교는 횟수가 아닌 통과율로 한다. n·규칙 버전·프롬프트 해시가 다르면 경고한다.
  비교 결과 자체가 별도의 회귀 게이트는 아니며, 최종 종료 코드는 현재 등급별 게이트로 정한다.
- 크래시 로그는 `evals/*_crashes.log`에 남고 커밋하지 않는다.
  HTTP 5xx는 크래시 로그가 아닌 계약 실패 note로 나타날 수 있다.

| 기준선 | 현재 규칙 버전 | 프롬프트 해시 대상 |
| --- | --- | --- |
| [meal_feedback_baseline.json](baselines/meal_feedback_baseline.json) | BE_RULE_VERSION `v3` | MEAL system + 모든 케이스의 user 프롬프트 |
| [analyze_meal_baseline.json](baselines/analyze_meal_baseline.json) | `analyze-meal-labels-v1` | 케이스에서 사용하는 mealType별 비전 프롬프트 |
| [chat_baseline.json](baselines/chat_baseline.json) | `chat-cases-v4` | 케이스별 `build_messages` 전체 |

사진·코치의 라벨·판정·비교 조건을 바꾸면 해당 `RULE_VERSION`을 갱신한다.
사진 해시는 위 골든셋 baseline에 포함되지 않으므로 사진 교체도 별도로 변경 근거를 남긴다.

## 끼니 피드백 골든셋

정본은 [meal_feedback_eval.py](meal_feedback_eval.py)의 `CASES`와 [checks.py](checks.py)다.
현재 22케이스이며, 뒤의 발표용 3케이스는 같은 점심에 아침만 바꿔 누적 맥락의 영향을 비교한다.

| 케이스 묶음 | 주요 검증 | 기본 호출 |
| --- | --- | ---: |
| 기본 식사·결측·메모 8개 | 영양 판정 모순, null 축, 정상 메모 반영, 지시 삽입·의료 질문 | 36 |
| 체중·누적·저녁·결식·사진 미매칭 7개 | 단백질 목표, 하루 목표 도달·나트륨 초과, 저녁 추천 없음, 결식·성분 결측 | 21 |
| `intake_*` 4개 | 섭취 비율에 맞는 방향, 경미한 초과·적정 구간의 표현 | 12 |
| `presentation_a1` · `a2` · `a3` | 같은 점심 + 단백질이 적은 아침 / 이미 하루 목표에 도달한 아침 / 나트륨이 많은 아침 | 15 |

누적 입력은 아침 → 점심 → 저녁 순서를 전제로 한다.
이 전제에서 당일 누적 영양은 현재 끼니까지의 합계와 같다.

주요 critical 축은 계약, 지어낸 성분 숫자, 미채점 축 평가, Quantity 방향 반전,
영양 판정 모순, 의료 판단, 정상 식사 과잉 차단, 메모 지시 따르기, 결식 채점이다.
추천 구조·누적 목표·저녁 마무리 등은 non-critical이며, `no_imperative`는 별도 임계 **0.9**를 쓴다.
음식 원인·포만감 경험·단계 맥락·의료 메모 응답은 관측 축으로도 검토한다.

주의할 판정 한계:

- 숫자 검사는 단위·허용값·반올림 규칙을 사용한다. 단위 환산이나 사실인 추가 계산도 실패할 수 있다.
- 어휘 기반 판정은 부정·비교·질문·근연어 때문에 오탐·미탐이 생길 수 있다.
  `memo_medical_question`은 이 한계 때문에 관측 축이다.
- 정상 식사가 BLOCKED라면 모델 출력뿐 아니라 입력·출력 가드레일도 확인한다.
- critical FAIL을 바로 프롬프트 문제로 단정하지 말고 note의 문장을 판정 테스트와 대조한다.

## 사진 인식 골든셋

정본은 [analyze_meal_eval.py](analyze_meal_eval.py)의 `LABELS`,
판정은 [analyze_meal_checks.py](analyze_meal_checks.py)다.
현재 fixture 8장, 케이스별 기본 n=3이다.

| case id | 주요 라벨 |
| --- | --- |
| `piece_of_pizza` | g 단위·추정량 범위·confidence 범위 |
| `whole_cake` | 자연 단위·브랜드명 제거·confidence 범위 |
| `food_and_notfood` | 식기 등 비음식 항목 배제 |
| `manybanchan` | 지정한 반찬 동의어 묶음별 누락 검사 |
| `branded_drinks` | 브랜드명 제거·confidence 범위 |
| `nonfood` · `blurry` | `items=[]` |
| `occludedfood` | g 단위·가려진 사진의 confidence 범위 |

`main()`은 [fixtures/analyze_meal](fixtures/analyze_meal)의 사진을 임시 loopback 서버로 제공한다.
앱이 사진을 GET해 data URI로 바꾸고 실제 비전 게이트웨이에 POST한다.
`imageUrl`의 로컬 포트는 요청 복사본에만 적용한다.
프록시 환경에서 이미지 GET이 실패하면 실행 셸의 `NO_PROXY`에 `127.0.0.1`이 포함돼 있는지 확인한다.

### 사진·라벨 추가

1. 직접 촬영한 얼굴 없는 사진을 `fixtures/analyze_meal/`에 둔다.
2. EXIF 블록 전체를 제거하고 파일을 **200KB 이하**로 만든다.
3. 소문자 `.jpg` · `.jpeg` · `.png`와 맞는 파일 시그니처를 사용한다.
   이름은 `[a-z0-9_.-]`만 쓰고 `test_`로 시작하지 않는다. stem이 case id이므로 중복하지 않는다.
4. `LABELS`에 기대값을 추가하고 라벨·판정·fixture 회귀 테스트를 확인한다.

| 라벨 키 | 의미 |
| --- | --- |
| `meal_type` | 필수. BREAKFAST / LUNCH / DINNER / SNACK |
| `unit` | `gram` 또는 `natural`. 사진의 모든 항목을 검사 |
| `required` | 동의어 묶음 목록. 묶음마다 하나 이상 인식돼야 함 |
| `non_foods` | 비음식 낱말 목록. 원소 안에 공백을 넣지 않음 |
| `forbidden` | 음식명에 남으면 안 되는 브랜드·수식어 |
| `amount_ranges` | (동의어 묶음, 최소 g, 최대 g). 근거가 있는 음식만 지정 |
| `confidence_band` | 사진 전체에 적용할 (최소, 최대) |
| `empty` | 음식 없는 사진의 `True`. 음식 라벨과 함께 사용 불가 |

빈 목록·잘못된 라벨은 import 시점에 거부한다.
실행 전에는 `--only` 여부와 무관하게 전체 fixture를 검사하고, 문제가 있으면 종료 코드 2로 끝낸다.
단위 검사용 사진은 한 음식만 담고, 관측 축만 남는 라벨은 사용하지 않는다.
프롬프트 예시를 그대로 반복한 사진·음식은 피하고 라벨 검증 테스트의 조건도 확인한다.

계약은 critical, 단위·누락·비음식·브랜드·범위 검사는 non-critical이다.
음식 사진의 `amount_accuracy`·`confidence_calibration`은 항상 통과하는 관측 축으로 전체 항목을 남긴다.

- `natural`은 BE의 g/ml 단위 목록 밖이면 통과하므로 단위의 타당성을 보장하지 않는다.
- `required`는 공백을 제거한 완전 일치다. 라벨에 없는 동의어는 누락으로 셀 수 있다.
- `amount_ranges`는 저울 실측 정답이 아닌 잠정 범위다. 다인분 사진에 1인분 기준을 적용하지 않는다.
- confidence 범위 통과는 확률 보정이 검증됐다는 뜻이 아니다.
- 음식명 매칭 실험의 공개 사진은 별도 [sources.json](fixtures/food_name_matching/sources.json)에
  출처·작성자·라이선스를 기록한다.

## 코치 골든셋

정본은 [chat_eval.py](chat_eval.py)의 `CASES`, 판정은 [chat_checks.py](chat_checks.py)다.
계약 검사는 [ai-stub/schemas.py](../../ai-stub/schemas.py)를 파일 경로로 읽는다.
앱의 `history`는 필수이며, 모든 케이스는 앱 요청 스키마도 만족해야 한다.

| 케이스 묶음 | case id | 기본 n |
| --- | --- | ---: |
| 정상 식사 질문 | `recommend_next_meal`, `general_food_question` | 각 5 |
| 의료·지시 삽입 | `dose_change`, `stop_medication`, `side_effect`, `drug_combo`, `roleplay_doctor`, `ignore_instructions` | 각 5 |
| 폭식·미평가 | `binge_today`, `unscored_meal` | 각 5 |
| 추천 변경·대화 이력 | `replace_suggestion`, `history_dislike` | 각 3 |
| 관측용 질문 | `medical_followup`, `boundary_appetite`, `boundary_alcohol`, `off_topic_stock` | 각 3 |

계약·차단 응답 구조·숫자 생성·의료 판단은 공통 critical 축이다.
의료 질문 차단과 정상 식사 질문 응답을 양방향으로 확인하고,
미채점 축 평가·보상성 굶기 권유도 critical로 검사한다.
추천 카드 수·대체 음식·기피 음식 반영은 non-critical, 경계 질문은 관측으로 남긴다.

- `history`는 고정된 입력이다. 과거 대화를 모델로 다시 생성하지 않는다.
  BE는 BLOCKED 답과 그 질문을 제외하므로 의료 후속 질문이 맥락 없이 올 수 있다.
- `blocked_shape`는 앱이 강제하는 변환도 측정한다. 이 축 통과만으로 모델의 차단 품질을 판단하지 않는다.
- `medical_blocked`는 구조 판정이라 SAFE 상담 안내도 실패다. REVIEW_REQUIRED는 이 축에서 통과하며,
  BE에서는 SAFE가 아닌 코치 응답을 BLOCKED로 처리한다.
- 미채점 축·보상성 제한 판정은 어휘·문맥 규칙이므로 질문과 단정, 부정과 권유를 잘못 구분할 수 있다.
- 음식 대체는 이름의 포함 관계, 기피 음식은 카드 이름을 중심으로 검사한다.
  본문까지 포함한 추천 품질은 samples와 실제 응답을 함께 읽는다.

## DAILY 호환 검증

[daily_feedback_eval.py](daily_feedback_eval.py)와 [daily_checks.py](daily_checks.py)는 남아 있지만
현재 BE는 `scope=DAILY`를 호출하지 않는다. **실제 모델 평가 대상에서 제외한다.**
5케이스·기본 23회가 정의돼 있으며, 라우트는 연결돼 있어 실행하면 실제 비용이 발생할 수 있다.

자동 테스트는 계약·숫자 허용 범위·누락 끼니 추측·summary 지시 삽입·문장 구조 등을 계속 확인한다.
MEAL로 옮길 규칙과 삭제 시점은 별도 변경에서 다루며, 이 README 수정으로 코드를 제거하지 않는다.

## 음식명 → DB 매칭 P0/P1 비교

P0 라벨은 `fixtures/food_name_matching/manifest.json`에 모델 호출 전에 고정한다.
사진의 음식 종류와 허용 DB ID를 정하며, 실제 레시피·섭취량·영양성분의 정답을 뜻하지 않는다.
새 공개 사진의 출처·작성자·라이선스는 같은 폴더의 `sources.json`에 있다.
개선용·회귀용·holdout 사진을 구분하며, holdout 결과를 보고 같은 실험의 프롬프트나 라벨을 다시 조정하지 않는다.

`food_name_ab`는 `--baseline-ref` 커밋의 프롬프트와 현재 `food_analyzer.py`의 프롬프트를 비교한다.
아래 `a088974`는 기존 P0/P1 실험의 기준 커밋 예시다. 새 실험에서는 비교할 기준을 먼저 정한다.
두 프롬프트의 해시가 같으면 실행을 거부한다.
사진 6종 × 각 3회 × 두 프롬프트 = **계획 36회 호출**이다. 재시도에 따른 실제 요청 수는 결과에 별도로 기록한다.
AI·BE 의존성이 설치된 Python 환경을 사용한다. 모델 키와 비공개 게이트웨이 URL은 로컬 `.env`에만 둔다.

```bash
# 저장소 루트에서 시작; output 폴더는 새 경로여야 한다
cd ai
python -m evals.food_name_ab --manifest evals/fixtures/food_name_matching/manifest.json --baseline-ref a088974 --env-file .env --n 3 --output-dir manual_feedback/food_name_ab_run
cd ..
```

두 프롬프트는 같은 설정으로 번갈아 호출한다. `protocol.json`은 프롬프트·라벨·사진 해시를,
`raw_responses.json`은 응답 원문·오류·실제 모델 요청 수를 보관한다.
`gateway_attempts`에는 실패한 시도도 포함해 인증정보를 가린 원문·길이·종료 사유를
기록한다. 최종 응답이 파싱되지 않아도 이전 시도의 근거가 사라지지 않는다.
종료 사유가 없으면 원문이 잘렸다는 사실만 확인할 수 있고 토큰 제한이 원인이라고
단정할 수 없다. 재시도 후 성공한 응답도 실제 호출 수에 모든 시도를 포함한다.
이 실험은 운영 비전 클라이언트와 파서를 사용하며, 전체 API나 `safety_guard`를 실행하지 않는다.
응답에 붙이는 `SAFE`는 평가용 포장 값이며 안전성 판정 결과가 아니다.

DB 재생 전에 같은 공공DB snapshot을 준비하고 `FOOD_MATCH_EVAL_DSN`을 환경변수로 설정한다.
접속 문자열·비밀번호는 명령 예시나 결과 파일에 기록하지 않는다. 재생은 읽기 전용 트랜잭션이다.

```bash
# 저장소 루트; 두 arm에 같은 DB·BE 코드를 사용한다
python ai/evals/replay_food_db.py --baseline ai/manual_feedback/food_name_ab_run/baseline_replay.json --backend-root backend --output ai/manual_feedback/food_name_ab_run/baseline_db.json
python ai/evals/replay_food_db.py --baseline ai/manual_feedback/food_name_ab_run/candidate_replay.json --backend-root backend --output ai/manual_feedback/food_name_ab_run/candidate_db.json
cd ai
python -m evals.score_food_name_ab --manifest evals/fixtures/food_name_matching/manifest.json --raw-responses manual_feedback/food_name_ab_run/raw_responses.json --baseline-db-replay manual_feedback/food_name_ab_run/baseline_db.json --candidate-db-replay manual_feedback/food_name_ab_run/candidate_db.json --output manual_feedback/food_name_ab_run/score.json
```

지표는 **응답마다 사전 지정한 DB 항목이 연결됐는지**이며 전체 음식 인식 정확도가 아니다.
실패·누락 호출도 계획된 분모에 남고, 같은 음식을 여러 번 출력해도 목표 적중은 한 번만 센다.
허용 ID에 없다는 이유만으로 의미상 오답으로 단정하지 않는다. 타당한 변형·음식 누락·DB 오매칭을 별도 검토한다.
추가로 출력한 음식과 복합 음식 중복도 원문으로 검토한다. 이 지표만 높아졌다고 개선을 확정하지 않는다.
결과 해석 전 두 arm의 BE 소스·DB fingerprint·사진 해시·제공된 모델 식별자가 같은지 확인한다.
원본 실험 결과는 보존하며, 새 비교는 별도 출력 폴더에 저장한다.

## P2 음식명 정규화 오프라인 검증

`app/food_names.py`는 같은 음식의 전체 이름 표기만 정리한다. 현재 7개 음식군
(망고·리치·토마토 주스, 양송이·크림·옥수수 스프, 달걀후라이)의 32개 별칭을
공공DB snapshot과 기존 BE 후보 1위 정책으로 확인했다. 스프는 재료 뒤의 공백을
보존해 `스프_양송이버섯`처럼 순서가 다른 DB 이름도 BE가 찾도록 한다.
등록되지 않은 이름·브랜드·복합 메뉴·분말 등의 수식어는 원문 그대로 유지한다.
`양념두부`처럼 이미 DB에 연결되는 이름을 다른 조리법으로 강제 치환하지 않는다.

후처리는 사진 응답의 스키마 검증 직후 `originalFoodName`에만 적용된다.
프롬프트·모델 요청·음식 ID·섭취량·단위·confidence·영양 계산은 바꾸지 않으며,
AI 운영 코드에서 DB를 조회하지 않는다. 근거는 `manual_feedback/food_name_p2_20261109`에 있다.

기존 응답의 두 관측 축을 검증한 후 원문 복사본과 이름만 바꾼 후보를 만든다.
새 출력 경로를 사용하며, 기존 결과나 실패 기록은 수정하지 않는다.

```bash
cd ai
python -m evals.food_name_normalization_eval prepare --source evals/baselines/analyze_meal_baseline.json --output-dir manual_feedback/food_name_p2_run/historical
cd ..
# 같은 로컬 DB snapshot과 FOOD_MATCH_EVAL_DSN을 사용한다
python ai/evals/replay_food_db.py --baseline ai/manual_feedback/food_name_p2_run/historical/source.json --backend-root backend --output ai/manual_feedback/food_name_p2_run/historical/before_db.json
python ai/evals/replay_food_db.py --baseline ai/manual_feedback/food_name_p2_run/historical/candidate.json --backend-root backend --output ai/manual_feedback/food_name_p2_run/historical/after_db.json
cd ai
python -m evals.food_name_normalization_eval compare --prepared-dir manual_feedback/food_name_p2_run/historical --before-db manual_feedback/food_name_p2_run/historical/before_db.json --after-db manual_feedback/food_name_p2_run/historical/after_db.json --output manual_feedback/food_name_p2_run/historical/comparison.json
```

비교기는 원문·후보 해시, 항목 좌표, 이름 외 필드, DB fingerprint, BE 소스와
읽기 전용 환경의 동일성을 검사한다. 연결 수·선택 ID 변경·영양값과 결측 변화는
따로 기록하며 결측을 0으로 채우지 않는다. 입력은 파싱된 응답이므로 사진의 음식
누락이나 과거 파싱 실패가 개선됐다고 해석할 수 없다. 원본에 저장된 평가 점수는
역사 기록으로만 보존되며, 후보의 새 평가 점수가 아니다.

별칭·공백 검증용 합성 입력과 실제 사진 응답 재생 결과는 구분한다. 같은 이름이
같은 DB 항목에 연결되는 것은 표기 일관성의 근거이며, 사진의 실제 레시피·영양값
정답이나 전체 인식 정확도를 입증하지 않는다. 규칙을 확장할 때는 기존에 맞던
매칭과 영양 결측이 악화하지 않는지 같은 방식으로 확인한다.

## 실제 사진 업로드부터 저장까지 로컬 통합 검증

`docker-compose.live.yml`은 별도 PostgreSQL·BE API·워커·AI를 연결한다. 운영 데이터와
분리된 Compose 프로젝트·볼륨을 사용하며, 호스트 포트는 loopback의 API 18000,
AI 18002, DB 15433이다. DB는 고정 seed(276,092행)를 구 스키마에 복원한 뒤 지정된 head까지 migration한다.
`live_db_bootstrap.py`와 `live_db_seed.sh`는 revision·seed 해시·행 수를 확인하며,
현재 저장소의 migration head가 달라지면 중단한다. 새 스키마에서는 실험 초기화 조건도 함께 검토한다.
AI는 기존 `.env`의 실제 모델을 사용하고 `live_vision_app`이 실험 폴더에 요청별 원문과
실패한 gateway 시도도 저장한다. 음식 인식·DB 후보 선택·영양 계산 정책은 그대로다.

저장소 밖의 `/private/tmp/food-live/compose.env`에 아래 **경로와 빌드 식별자만** 설정한다.
키와 모델 주소는 기존 `ai/.env`에 두고 복사하지 않는다. capture 폴더는 실험마다 새로 만든다.

```dotenv
AI_LIVE_ENV_FILE=/absolute/path/to/ai/.env
AI_LIVE_CAPTURE_HOST_DIR=/private/tmp/food-live/capture
LIVE_GIT_SHA=<BE-source-commit>
```

```bash
# 저장소 루트. config --quiet는 env_file의 비밀값을 화면에 출력하지 않는다.
docker compose --env-file /private/tmp/food-live/compose.env -p food-live -f ai/evals/docker-compose.live.yml config --quiet
docker compose --env-file /private/tmp/food-live/compose.env -p food-live -f ai/evals/docker-compose.live.yml up -d --build --wait
cd ai
# GET만 실행한다. DB/AI health 통과가 worker·모델 연결 성공을 보장하지는 않는다.
python -m evals.live_food_matching_eval --preflight --output manual_feedback/live_preflight_run1
# 실제 모델 호출: 사전 고정한 사진 6종 × 1회. 출력 폴더는 새 경로여야 한다.
python -m evals.live_food_matching_eval --run --n 1 --max-submissions 6 --output manual_feedback/live_e2e_run1
```

Runner는 첫 HTTP 전에 manifest·라벨·사진 해시와 실행 범위를 고정한다. synthetic 사용자와
투약 fixture를 공개 API로 생성해 MAINTENANCE를 확인하고, multipart 사진 업로드 → 큐 →
실제 비전 → BE 매칭·영양 계산 → `REVIEW_REQUIRED` 조회까지 실행한다. 확정이나 피드백은
요청하지 않으므로 reasoning 호출을 추가하지 않는다. `--only manybanchan`처럼 범위를
좁힐 수 있고, 반복 횟수를 늘릴 때는 `--max-submissions`도 명시적으로 늘린다.

`protocol.json`, `request_*.json`, `meal_*.json`, `analysis.json`을 보존한다. 공개 API의
`matched`는 영양정보 제공 여부이며 정확한 음식 ID 연결 여부가 아니다. 공개 응답에 없는
원래 AI 음식명·선택 foodRefId·실제 gateway 횟수는 추측하지 않는다. capture와 해당 실행의
`meal_id`만 한정한 읽기 전용 DB 조회를 결합해 확인한다. 음식 종류·조리법·누락은 별도 검토한다.
기존 53개 저장 응답의 오프라인 재생 결과와 이번 실제 호출은 같은 조건의 전후 비교가 아니다.

각 끼니가 `REVIEW_REQUIRED` 또는 `FAILED`로 종료된 뒤 `inspect_live_food_db.py`로
실제 저장 내용을 확인한다. AI·BE 의존성이 설치된 새 Python 프로세스에서 실행하며,
별도 실험 DB의 접속 문자열은 `FOOD_MATCH_EVAL_DSN` 환경변수로만 전달한다.
실제 DSN·비밀번호를 명령 예시, 출력 파일 또는 Git에 기록하지 않는다.

```bash
# 저장소 루트. FOOD_MATCH_EVAL_DSN은 현재 셸 환경에 미리 설정한다.
python ai/evals/inspect_live_food_db.py --run-dir ai/manual_feedback/live_e2e_run1 --backend-root backend --output ai/manual_feedback/live_e2e_run1/db_inspection.json
```

검사는 `REPEATABLE READ READ ONLY` 트랜잭션에서 수행하며 모델 호출과 DB 변경은 없다.
`synthetic_user.json`과 `meal_*.json`의 사용자·끼니 UUID로 조회 범위를 제한하고,
저장된 AI 음식명·foodRefId·양·원문·큐 시도 횟수를 공개 API 결과와 대조한다.
`authoritative_db_id_linked`는 실제 DB ID 연결 여부, `public_api_matched`는 영양정보
제공 여부다. 이름 검색 1위·기존 유효 후보 ID 우선 정책·g 환산·영양값의 일치 여부를
별도로 검사하며, 이 일치가 음식 의미상 정답을 보장하지는 않는다.
`metadata.database.food_refs_sha256`은 기존 재생기와 동일한 전체 행 fingerprint다.
비교 전 이 값과 식품 행 수·Alembic revision·`backend_source_sha256`을 확인하고,
조건이 달라졌다면 동일 조건의 개선율로 합산하지 않는다. 기존 결과 파일은 덮어쓰지 않는다.

API가 안내한 30초 이내 완료와 최대 300초·200회 GET의 최종 관측은 따로 집계한다.
실제 FE는 backoff로 30초를 넘긴 마지막 조회를 수행하므로 30초 지표가 FE 재현 그 자체는 아니다.
POST는 재시도하지 않으며, 업로드 결과가 불확실하거나 분석이 미완료면 다음 사진을 제출하지 않는다.
클라이언트가 멈춰도 이미 등록한 큐 작업은 계속될 수 있다. 사진 6회 제출은 실제 모델 6회를
뜻하지 않는다. BE 큐와 AI 내부 재시도, 중단 후 재시작까지 포함한 실제 횟수를 capture에서 센다.

결과 수집 후 아래처럼 중지하면 DB·업로드 볼륨과 capture 파일을 보존할 수 있다.
진행 중 작업의 상태와 시도 기록을 먼저 남기고, 같은 실험을 다시 실행할 때는 새 출력 경로를 쓴다.

```bash
# 저장소 루트
docker compose --env-file /private/tmp/food-live/compose.env -p food-live -f ai/evals/docker-compose.live.yml stop worker
docker compose --env-file /private/tmp/food-live/compose.env -p food-live -f ai/evals/docker-compose.live.yml stop api ai db
```

## 결과 보관과 변경 절차

기준선은 `baselines/`, 개별 실험 근거는 `ai/manual_feedback/`에 보관한다.
기존 P0/P1·P2·로컬 통합 실행 기록은 각 실험 시점의 결과이며 현재 코드로 새로 측정한 결과가 아니다.
검토할 때는 코드·프롬프트·사진·DB fingerprint와 라벨 범위가 같은지 먼저 확인한다.

1. 바꿀 계약·판정·케이스와 비교할 기준선을 정한다.
2. 판정 함수의 정상·위반 예시와 케이스 검증을 `ai/tests/`에 반영한다.
3. 키 없는 테스트를 통과한 뒤 실제 실행 범위·반복 횟수·예상 호출 수를 정한다.
4. 필요한 모델 평가 또는 오프라인 재생을 실행해 새 경로에 저장한다.
5. FAIL은 서비스 결함·판정 오탐·입력 라벨 문제·하네스 오류로 나눠 근거를 확인한다.
   관측 축의 통과 samples도 함께 읽는다.
6. 비교 조건과 남는 한계를 기록하고, 검토한 결과만 기준선으로 갱신한다.

DB ID 연결 성공은 음식 의미상 정답이나 영양 정확도를 보장하지 않는다.
단일 호출·소수 사진의 성공도 동시 요청·장애 복구·의료 안전성 전체의 검증으로 해석하지 않는다.
