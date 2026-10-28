# Golden dataset

AI 기능마다 **입력, 응답이 지켜야 할 조건, 통과율**을 고정한 평가 케이스다. 프롬프트나 모델을 바꿀 때마다 같은 케이스를 다시 돌려, 좋아진 곳과 깨진 곳을 같이 본다.

- 정답 문장을 고정하지 않는다. 좋은 문장은 많지만 **금지 행동은 짧게 열거된다.** 그래서 금지 조건을 코드로 판정한다.
- LLM judge는 두지 않는다. 부를 수 있는 모델이 생성 모델뿐이라 자기채점이 된다. 질적 항목(`review`)은 사람이 본다.
- BE QQS 채점은 대상이 아니다. 결정적이라 BE pytest 몫이다.

| 폴더 | 대상 | 케이스 |
| --- | --- | --- |
| `meal_feedback/` | `POST /short-feedback` (`scope=MEAL`) | 9 |

## 구조

```
evals/
├─ golden.py                 케이스 읽기 · 형식 검사 · 집계 · 게이트 · baseline 비교 (영역 공통)
├─ meal_feedback.py          끼니 피드백 검사
├─ run.py                    eval 실행기
└─ golden/
   ├─ README.md
   └─ meal_feedback/
      ├─ _common.json        이 영역 공통 검사 · 등급 · 근거(코드 위치)
      └─ MF-nn-….json        케이스 하나 = 파일 하나, id = 파일명
tests/test_golden_meal_feedback.py   데이터셋 검증 (verify 층)
```

## 케이스 필드

| 필드 | 뜻 |
| --- | --- |
| `id` · `description` | `MF-nn-kebab-case`, 파일명과 같다 · 확인하려는 것 한 줄 |
| `grade` | `critical` · `normal` · `observe`. 아래 "등급" 참고 |
| `source` | `design`(사전 설계) · `incident`(실제 사고) · `review`(리뷰 중 발견). `incident` 면 `incident` 에 무슨 일이었는지 적는다 |
| `group` | `정상` · `경계값` · `결측` · `안전` · `인젝션`. 분포로 빈틈을 찾는다 |
| `tags` | 자유 분류 (`stage:`, `meal:`, `gap:` …). 합의 전이면 `agreement:needed` + `agreement` 에 질문 |
| `input` | BE가 AI에 보내는 요청 그대로 (`app/schemas.py`) |
| `expectedJudgements` | `judge_nutrients` 의 기대 결과. 데이터셋 검증이 실제 규칙과 맞춰 본다 |
| `hard` | 케이스에만 있는 금지 표현 `mustNotContain`, 메모 인젝션 케이스는 `memoMustNotFollow`. 어기면 실패, 등급은 케이스 `grade` |
| `signals` | `mustMentionAny`(원인 음식, 관측) · `mustNotContain`(의심 표현, 관측) · `memoReflectAny`(정상 메모 반영, normal 검사) |
| `review` | 사람이 볼 질문. 자동 판정이 아니다 |
| `reference` | 통과하는 답의 **예**. 정답 문장이 아니다. 모든 검사를 통과해야 한다 |
| `counterexamples` | 틀린 답의 예 `{why, response, failsOn}`. `failsOn` 에 적은 검사에서**만** 실패해야 한다 |

## 등급

| 등급 | 기준 | 게이트 |
| --- | --- | --- |
| `critical` | 사용자가 그대로 믿고 행동할 수 있는 것 · 화면의 다른 정보와 모순되는 것 · BE가 저장 못 하는 것 | N번 중 1번만 실패해도 FAIL (Pass^k) |
| `normal` | 품질 · 톤 | 임계 통과율(기본 90%) 아래면 FAIL |
| `observe` | 정답이 합의 · 실측 전이거나, 판정이 오판하기 쉬운 것 | 기록만 한다 |

공통 검사의 등급은 `_common.json` 이 정한다. 케이스 `grade` 가 `observe` 면 그 케이스의 검사가 모두 관측으로 내려간다. 합의되면 승격한다.

## 두 층

| 층 | 하는 일 | 키 | 언제 |
| --- | --- | --- | --- |
| verify | 형식 · 기대값과 규칙 일치 · 모범 답 통과 · 반례 실패 · BE 기준 사본 동기화 · 게이트 로직 | 불필요 | CI (`pytest`) |
| eval | 실제 모델을 케이스마다 N번 호출해 통과율 · 게이트 · baseline 비교 | 필요 | 프롬프트 · 모델 바꾸기 전후 |

```bash
cd ai
pytest tests/test_golden_meal_feedback.py                                   # verify

python -X utf8 -m evals.run meal_feedback --mode reference                  # 검사기 점검 (모범 답)
python -X utf8 -m evals.run meal_feedback --n 3 --save evals/baseline/meal_feedback.json
python -X utf8 -m evals.run meal_feedback --n 3 --baseline evals/baseline/meal_feedback.json
```

- eval은 서비스와 같은 경로(`TestClient(app)` → FastAPI 라우트)로 부른다. 모듈 함수를 직접 부르면 `main.py` 의 래핑과 가드레일을 건너뛰고 재게 된다.
- 키가 없거나 엔드포인트가 501이면 SKIP 하고 0으로 끝난다.
- baseline에는 `beRuleVersion` 과 프롬프트 해시가 같이 남는다. 점수가 바뀐 이유가 규칙인지 프롬프트인지 가른다.

## 케이스 추가 규칙

1. 입력 음식은 few-shot 예시 음식(참치김밥 · 삶은 달걀 2개 · 방울토마토 한 줌)과 겹치지 않게 둔다.
2. `expectedJudgements` 는 손으로 적되, verify가 규칙과 다르면 실패한다.
3. 반례를 하나 이상 넣는다. 반례가 없는 검사는 실패를 잡는지 확인한 적이 없는 검사다.
4. 운영 중 이상한 응답을 보면 `source: review` 케이스로 되돌려 넣는다.

## 키워드 판정의 한계

키워드 검사는 표현을 다 잡지 못한다. 잡은 것은 확실히 틀린 것이고, 못 잡은 것은 `review` 몫이다. 반대로 맞는 답을 잡으면(오탐) 검사 패턴을 고치고, 그 답을 `reference` 나 새 케이스로 남겨 다시 오탐하지 않게 한다.

## 팀 합의가 필요한 것

| 케이스 | 질문 |
| --- | --- |
| `MF-09` | 메모에 부작용 증상이 있으면 `safetyStatus` 를 올리는가, 본문에 상담 안내를 넣는가 |
