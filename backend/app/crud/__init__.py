"""DB 접근 계층.

**DB 를 만지는 곳은 여기뿐이다**(규칙 5). `services/` 는 세션을 다루지 않고,
조합은 `api/` · `internal/` · `worker/` 가 한다.

커밋은 부르는 쪽이 한다. 한 작업이 여러 crud 를 묶어 쓰기 때문에, 각 함수가
스스로 커밋하면 중간에 실패했을 때 절반만 저장된 상태가 남는다.

    from app.crud import meal as crud_meal
    crud_meal.set_status(db, row, MealStatus.REVIEW_REQUIRED)
    db.commit()

UNIQUE 제약이 있는 테이블은 `create` 가 아니라 `upsert` 를 둔다 —
`create` 를 두 번 부르면 무결성 위반으로 커밋이 통째로 깨진다.

    qqs_evaluations      UNIQUE (meal_id)
    meal_feedbacks       UNIQUE (meal_id)
    satiety_logs         UNIQUE (meal_id)
    daily_feedbacks      UNIQUE (user_id, feedback_date)
    long_term_feedbacks  UNIQUE (user_id, period_type, period_start)

근거 링크 테이블(`daily_feedback_sources` · `long_term_feedback_sources`)에는 모듈을
두지 않는다. 부모의 `sources` 를 통해서만 쓴다 — 단독으로 다룰 일이 없다.
"""

from app.crud import evaluation, feedback, food, handoff, meal, medication, user

__all__ = ["evaluation", "feedback", "food", "handoff", "meal", "medication", "user"]
