# Golden dataset

AI 기능별로 **입력과 지켜야 할 결과**를 묶은 평가 케이스다. 프롬프트 · 모델을 바꿀 때마다 같은 케이스를 다시 돌려 좋아진 곳과 깨진 곳을 함께 본다.
요청 모양은 `ai-stub/schemas.py` 의 계약을 따른다.

| 폴더 | 대상 | 케이스 |
| --- | --- | --- |
| `food_analysis/` | `POST /analyze-meal` | 14 |
| `meal_feedback/` | `POST /short-feedback` (`scope=MEAL`) | 12 |
| `daily_feedback/` | `POST /short-feedback` (`scope=DAILY`) | 3 |
| `long_feedback/` | `POST /long-feedback` | 2 |
| `chat_coach/` | `POST /chat` | 14 |

케이스 하나가 파일 하나이고, `id` 는 파일 이름과 같다.

## 공통 필드

| 필드 | 뜻 |
| --- | --- |
| `id` · `description` · `tags` | 케이스 이름, 확인하려는 것 한 줄, 분류 태그 |
| `input` | AI 에 보내는 요청 그대로 (음식 분석은 아래 별도 필드) |
| `hard` | 코드로 확실히 판정하는 조건. 어기면 실패 — `mustNotContain`, 코치는 `cardsMustNotContain` |
| `signals` | 키워드로 찾는 의심 신호. 실패가 아니라 사람 · judge 가 본다 — `mustMentionAny`, `mustNotContain` |
| `soft` | LLM judge 에게 물을 예/아니오 질문. "예" 가 좋은 답이다 |
| `reference` | 통과하는 답의 예. 정답 문장이 아니라 기준을 보여 주는 예시다 |

## 기능별 필드

- **음식 분석**
  - `imageUrl` · `rawText` — 사진과 글. 글만 있는 케이스는 `imageUrl` 이 `null` 이다
  - `expected[]` — 음식마다 허용 이름 `names` 와 정답 양 `amountG`. 양은 항상 g 이다
  - `mayAlsoContain` — 내도 틀리지 않은 곁들임 · 고명
  - `amountVerified` — 정답 g 을 실측했는지. `false` 면 양 오차는 채점하지 않는다
  - `amountTolerance` — 양 허용 오차 비율 (없으면 검사기 기본값)
  - `expectedSafety` — 허용하는 `safetyStatus`. 없으면 보지 않는다
  - `expectClarify` — `clarifyQuestion` 이 있어야 하는 음식 이름
- **끼니 피드백** — `expectedJudgements`: `short_feedback.judge_nutrients` 의 기대값 (BE 채점 기준 v2)
- **AI 코치** — `expectedSafety`(허용하는 안전 판정), `expectedCards`(`required` · `none` · `optional`)

## 사진 필요

`food_analysis` 의 사진 케이스 10건은 아직 사진이 없다. `tags` 에 `photo:needed` 가 붙어 있고 `imageUrl` 은 `example.invalid` 자리표시다.
직접 찍은 뒤 사진 주소와 저울로 잰 `amountG` 를 채우고 `amountVerified` 를 `true` 로 바꾼다. 실제 사용자 사진은 쓰지 않는다.

| 케이스 | 찍을 것 |
| --- | --- |
| 01 | 된장찌개 · 잡곡밥 · 깍두기 한 상 |
| 02 | 제육볶음 · 밥 · 미역국 · 배추김치 · 콩나물무침 백반 |
| 03 | 뜯은 참치마요 삼각김밥 + 컵라면 |
| 04 | 토마토 파스타 + 마늘빵 |
| 05 | 토스트 · 계란프라이 · 우유 |
| 08 | 조명이 어둡고 초점이 흐린 비빔밥 |
| 09 | 짜장면 한 그릇 |
| 10 | 8조각 중 5조각 남은 피자 박스 |
| 11 | 책상 위 노트북과 고양이 (음식 없음) |
| 12 | 주사 펜과 상자 (제품명이 안 보이게) |

## 팀 합의가 필요한 케이스

`tags` 에 `agreement:needed` 가 붙은 케이스는 기대 결과를 팀이 정해야 한다. 지금 값은 제안이다.

| 케이스 | 질문 |
| --- | --- |
| `food_analysis/09` | 사진과 글이 다르면 글(사용자 정정)을 따르나 |
| `food_analysis/12` | 약 사진은 `SAFE` + 빈 목록인가, `BLOCKED` 인가 |
| `meal_feedback/10` | 메모에 부작용 증상이 있으면 안전 판정을 올리나 |
| `chat_coach/09` | 심한 증상 질문은 `BLOCKED` 인가, `REVIEW_REQUIRED` 인가 |
| `chat_coach/10` | 극단적 절식 요청은 어떤 판정 · 어떤 말로 돌려보내나 |
