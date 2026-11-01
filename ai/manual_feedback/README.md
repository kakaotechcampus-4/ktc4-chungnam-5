# 피드백 수동 품질 테스트용 폴더

JSON을 수정하고 실제 LLM이 어떤 피드백을 만드는지 확인한다. BE·DB·음식 분석·실행 중인 API 서버는 필요 없다.
실제 `/short-feedback` 처리 함수를 직접 실행하므로 요청 검증·입출력 안전 검사·프롬프트·모델 호출·파서를 거친다.
HTTP 서버와 BE 워커 통신은 검증하지 않는다. 자동 채점이나 합격 판정 도구가 아니다.

## 실행

프로젝트 루트에서 실행한다. `python`은 `ai/requirements.txt`를 설치한 가상환경의 Python이다.
`ai/.env`에 `LLM_API_KEY`, `LLM_REASONING_URL`이 필요하다. 실행 위치와 무관하게 이 파일을 읽는다.

```bash
# JSON·판정·프롬프트만 확인. 키·네트워크·크레딧 불필요
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/meal.json --dry-run

# 실제 생성. 실행당 최대 한 번의 모델 요청, 자동 재시도 없음
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/meal.json

# 성분·점수가 없는 끼니
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/meal_unknown.json

# 첫 끼니 / 결식 / 저녁 (끝에 --dry-run을 붙이면 과금 없이 확인)
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/breakfast.json --dry-run
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/skipped.json --dry-run
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/dinner.json --dry-run

# 전환 기간에 유지하는 기존 DAILY 요청
python ai/scripts/try_short_feedback.py ai/manual_feedback/cases/daily.json
```

`cases/meal.json`은 아침 기록을 포함한 점심 요청이다. 음식명·성분·qqs·stage·satiety·weightKg를 바꾸고 다시 실행한다.
우선 한 항목씩 바꾸면 어느 입력이 출력에 영향을 줬는지 비교하기 쉽다.
현재 예제는 임의 데이터이며 BE 채점 엔진이 생성한 정답 사례가 아니다. 점수·영양 수치의 실제 일관성은 검증하지 않는다.
`nutrition`은 음식 100g당 수치가 아닌 **기록한 섭취량 전체의 성분**이다.
`null`은 모름이며 0과 다르다. `stage`는 PRE_DOSE / INITIAL / TITRATION / MAINTENANCE / REDUCED 중 하나다.
DAILY는 영양 판정 대신 끼니별 요약과 점수로 생성한다.

`weightKg`가 없거나 null이면 단백질 목표 대비 평가를 생략한다.
`previousMeals`에는 앞선 확정 기록만 들어가고, 없는 끼니를 결식으로 해석하지 않는다.
`skipped=true`면 items=[], satiety=null, QQS 세 축 모두 null이어야 한다.
`dailyNutritionTotals`는 현재 끼니를 포함한 현재 시점까지의 BE 합계다. 현재·이전 끼니를 바꾸어도
도구가 이 값을 재합산하지 않으므로, 비교 실험 시 누적 합계도 일관되게 수정한다.
결측 성분이 0으로 합산될 수 있어 누적값은 실제 섭취량보다 작을 수 있다.

## 결과 읽기

터미널에서 BE 입력 → Python 끼니 판정·누적 진행률 → system/user 프롬프트 → 모델 원본 문자열 → 최종 API 응답을 확인한다.
`body` 하나가 현재 끼니 분석과 앞선 기록을 고려한 누적 흐름을 담는다. `reasoning`은 추천 방향,
`suggestions[].foodName/advice`는 사용자에게 보여줄 추천이며 저녁은 빈 배열이다.
모델 원본은 게이트웨이 전체 응답이 아닌 `choices[0].message.content`다.
입력 차단이면 모델을 부르지 않으므로 원본은 null이다. 출력 차단이면 원본은 남고 최종 응답은 BLOCKED다.
파싱 실패 시 원본을 보존하고 최종 응답 대신 API 오류를 기록한다. HTTP 실패는 상태 코드만 기록한다.
`completed`는 실행 성공을 뜻하며 내용의 품질이나 의료 안전성을 보장하지 않는다.

실제 실행 결과는 `manual_feedback/results/날짜-시간.json`에 자동 저장된다. dry-run은 저장하지 않는다.
results는 Git에서 제외한다. 키·엔드포인트·인증 헤더는 보고서에 넣지 않지만 입력 기록·생성 문장은 포함한다.
예제에는 가상 데이터만 사용한다. 입력 JSON에 임의 필드를 추가하면 요청 스키마 기본 동작에 따라 무시될 수 있다.

다음 항목을 비교한다.

- 음식명·영양 판정·포만감이 입력과 일치하는가?
- 입력에 없는 수치나 인과관계를 만들어내지 않는가?
- 점수를 모르는 축을 평가하거나 과식·소식을 임의로 단정하지 않는가?
- 누적 합계를 다시 더하거나, 하루 도중 목표 미달을 즉시 부족으로 단정하지 않는가?
- 결식과 미입력을 구분하고, 체중이 없을 때 단백질 목표를 추정하지 않는가?
- 추천이 이번 식단의 부족·초과를 보완하는가?
- 원본과 최종 응답 사이에서 파서나 안전 규칙이 내용을 제거했는가?

문제를 발견하면 입력과 결과 파일을 보관하고, 원인이 판정·프롬프트·모델·파서 중 어디인지 확인한다.
종료 코드: 0=실행 완료 또는 dry-run, 1=호출/응답 처리 실패, 2=입력/설정 오류.
