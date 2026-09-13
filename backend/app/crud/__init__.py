"""DB 접근 계층.

**DB 를 만지는 곳은 여기뿐이다**(규칙 5). `services/` 는 세션을 다루지 않고,
조합은 `api/` · `internal/` · `worker/` 가 한다.

## 한 파일 한 엔티티

여러 사람이 동시에 API 를 짜기 때문에 파일을 엔티티 단위로 나눈다. 자기 티켓의
엔티티 파일만 건드리면 충돌이 나지 않는다. **남의 파일 함수를 고쳐야 하면 그 담당자에게
먼저 말한다** — 함수를 새로 더하는 충돌은 "둘 다 유지" 로 끝나지만, 시그니처를 고치는
충돌은 누가 맞는지 판단해야 한다.

링크 테이블(`daily_feedback_sources` · `long_term_feedback_sources`)에는 파일을 두지
않는다. 부모의 `sources` 로만 쓴다.

## 여기 있는 것과 없는 것

**조회 함수를 미리 만들어 두지 않는다.** 쿼리 모양은 화면이 정한다 — 필요한 사람이
자기 PR 에서 자기 모양으로 추가하는 편이, 상상해서 만든 인자 조합에 억지로 맞추는 것보다
낫다. 지금 있는 것은 **DB 제약에서 나온 것**뿐이다. 모르고 부르면 커밋이 통째로 깨지는
지식이라 각자 재발견하면 비싸다.

    UNIQUE 라 create 가 아니라 upsert 인 것
      evaluation      qqs_evaluations      (meal_id)
      meal_feedback   meal_feedbacks       (meal_id)
      satiety         satiety_logs         (meal_id)
      daily_feedback  daily_feedbacks      (user_id, feedback_date)
      long_feedback   long_term_feedbacks  (user_id, period_type, period_start)

    부분 유니크라 먼저 닫아야 하는 것
      user_goal          진행 중 목표는 사용자당 하나 → close 먼저
      medication_record  effective_to IS NULL 인 행도 하나 → close 먼저

## 커밋

각 함수는 커밋하지 않는다. 한 작업이 여러 crud 를 묶어 쓰기 때문에, 함수마다 커밋하면
중간에 실패했을 때 절반만 저장된 상태가 남는다.

    from app.crud import meal as crud_meal
    crud_meal.set_status(db, row, MealStatus.REVIEW_REQUIRED)
    db.commit()
"""
