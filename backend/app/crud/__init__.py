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

## meals 는 soft delete 다

`DELETE /meals/{mealId}` 는 행을 지우지 않고 `meals.deleted_at` 에 값을 넣는다. 행도,
자식 행(`meal_items` · `qqs_evaluations` · `meal_feedbacks` · `satiety_logs`)도 전부
그대로 남는다. FK 가 `ON DELETE CASCADE` 라 hard delete 였다면 평가 이력이 같이
날아갔을 것이고, 그걸 피하려고 고른 방식이다. 대가는 **필터를 각자 기억해야 한다**는 것:

  - `meals` 를 읽는 모든 쿼리에 `deleted_at IS NULL` 을 붙인다. 부분 인덱스
    `ix_meals_user_id_eaten_at` 도 이 조건이 있어야 탄다.
  - 자식 테이블을 집계할 때는 `meals` 를 join 해서 거른다. join 없이
    `qqs_evaluations` 를 그대로 합치면 **삭제된 식사가 점수에 계속 반영된다.**

## 커밋은 부르는 쪽이 한다

crud 함수 안에는 `commit()` 이 없다. 한 작업이 여러 crud 를 묶어 쓰기 때문에,
함수마다 커밋하면 중간에 실패했을 때 절반만 저장된 상태가 남는다 —
"옛 항목은 지워졌는데 새 항목은 절반만 들어간" 식사가 그런 예다.

세션을 얻는 방법은 부르는 쪽에 따라 다르다.

**API** — 요청 하나당 세션 하나. `get_db` 가 세션을 주고 끝나면 닫는다(커밋은 안 한다).
커밋은 **`services/` 함수가 응답을 만들어 return 하기 직전에** 한다(규칙 5). 엔드포인트
안에서 커밋하지 않는다.

    # api/v1/endpoints/meals.py
    @router.post("/meals", status_code=202)
    def create_meal(body: MealCreate, db: Session = Depends(get_db)):
        return ok(meal_service.create_meal(db, user_id=user_id, request=body))

    # services/meal.py
    def create_meal(db, *, user_id, request):
        snapshot = crud_snapshot.add(db, user_id, stage=stage)
        db.flush()                      # snapshot.id 가 필요하다
        meal = crud_meal.create(db, medication_snapshot_id=snapshot.id, ...)
        queue.enqueue(db, "meal.analyze", {"mealId": str(meal.id)})
        db.commit()                     # ← 도메인 쓰기와 작업 등록이 한 번에
        return MealCreatedResponse(meal_id=meal.id, ...)

**큐가 DB 테이블이라 순서를 사람이 지킬 필요가 없다.** `enqueue()` 는 넘겨받은 세션에
INSERT 만 하고 커밋하지 않으므로, 작업 등록이 도메인 쓰기와 같은 트랜잭션에 들어간다 —
커밋이 실패하면 작업도 같이 사라지고, `meals` 는 들어갔는데 작업은 없는(또는 그 반대인)
상태가 애초에 만들어지지 않는다. 별도 큐 미들웨어를 쓸 때 필요했던 "커밋을 먼저 하고
그 다음에 큐에 넣는다" 규칙은 더 이상 없다.

커밋을 `get_db` 에 맡기지 않는 이유도 같은 종류다. FastAPI 0.106+ 에서 yield 의존성의
종료 코드는 응답을 클라이언트에 **이미 보낸 뒤** 실행되므로, 거기서 커밋하면 "201 을
받았는데 저장은 안 된" 상태가 생긴다(`db/session.py` 참고). 대신 **`services/` 가
커밋을 빠뜨리면 에러 없이 조용히 버려진다** — 쓰기 경로를 짤 때 이걸 먼저 확인한다.

**Worker** — 세션을 직접 열지 않는다. 큐가 `SELECT … FOR UPDATE SKIP LOCKED` 로 작업
행을 잠근 세션을 그대로 넘겨주고(`worker/dispatch.py` 의 `db`), 핸들러는 그 세션으로
도메인을 쓴다. 작업 하나가 트랜잭션 하나다.

    # worker/jobs/analyze_meal.py
    def run(db, task, ai):
        crud_meal_item.delete_model_items(db, meal)
        for item in items:
            crud_meal_item.add(db, meal, ...)
        crud_meal.set_status(db, meal, MealStatus.REVIEW_REQUIRED)
        return {"items": len(items)}    # ← 커밋하지 않는다

**핸들러도, 핸들러가 부르는 `services/` 함수도 커밋하면 안 된다.** 커밋은 큐가 작업을
DONE 으로 옮기면서 한 번에 한다. 중간에 커밋해 버리면 AI 호출이 끝나기 한참 전에 행
잠금이 풀려 다른 워커가 같은 작업을 집고, 이후 정말 실패해도 이미 커밋된 도메인 변경은
롤백되지 않는다. 이 레포의 `services/` 는 관례상 자기가 커밋하므로(`services/meal.py`
참고), 워커에서 그런 함수를 붙여야 하면 **커밋 없는 버전으로 쪼개서** 부른다.

중간에 터지면 도메인 변경과 작업 완료가 함께 롤백되고 식사는 `ANALYZING` 으로 남는다.
행은 PENDING 그대로라 다음 폴링에 다시 집힌다 — 그래서 `analyze_meal` 은 상태를 먼저
보고 이미 처리된 건 건너뛴다.
"""
