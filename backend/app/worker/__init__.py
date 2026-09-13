"""비동기 작업 처리.

두 갈래다 — **큐 소비**와 **스케줄 배치**.

    loop.py       큐 폴링 루프. 삭제 시점이 여기 있다
    dispatch.py   작업 타입 → 핸들러
    jobs/         작업 하나당 파일 하나
    schedule.py   시간 트리거 배치 (아직 없음)

## 여기는 조합 레이어다

직접 하지 않고 부른다 (규칙 5).

    SQL           → crud/
    계산·채점      → services/
    HTTP·큐·S3    → infra/
    순서를 정하는 것 → 여기

`jobs/analyze_meal.py` 가 그 모양이다 — crud 로 읽고, infra 로 AI 를 부르고,
services 로 환산하고, crud 로 저장한다.

## 스케줄 배치로 들어갈 것들

API 요청 없이 시간이 트리거하는 일이다. 큐를 거치지 않는다.

    매일      그날 meal_feedbacks 를 모아 daily_feedbacks 생성
    주간·월간  daily_feedbacks 를 모아 long_term_feedbacks 생성
    주기적    개인 baseline 재계산 (users.baseline_meal_kcal, D7)
    주기적    ANALYZING 인 채 오래된 식사를 FAILED 로 정리
    정해진 시각 포만감 입력 리마인더 FCM

네 번째가 안전망이다. API 가 커밋은 했는데 queue.send() 가 실패하면 식사가
ANALYZING 에 갇힌다. 그걸 줍는다.
"""
