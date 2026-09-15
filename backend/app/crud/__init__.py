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

## 커밋은 부르는 쪽이 한다

crud 함수 안에는 `commit()` 이 없다. 한 작업이 여러 crud 를 묶어 쓰기 때문에,
함수마다 커밋하면 중간에 실패했을 때 절반만 저장된 상태가 남는다 —
"옛 항목은 지워졌는데 새 항목은 절반만 들어간" 식사가 그런 예다.

세션을 얻는 방법은 부르는 쪽에 따라 다르다.

**API** — 요청 하나당 세션 하나. `get_db` 가 세션을 주고 끝나면 닫는다(커밋은 안 한다).

    @router.post("/meals", status_code=202)
    def create_meal(body: MealCreate, db: Session = Depends(get_db)):
        snapshot = crud_snapshot.add(db, user_id, stage=stage)
        db.flush()                      # snapshot.id 가 필요하다
        meal = crud_meal.create(db, medication_snapshot_id=snapshot.id, ...)
        db.commit()                     # ← 큐에 넣기 전에 커밋한다
        queue.send({"type": "meal.analyze", "mealId": str(meal.id)})
        return {"mealId": meal.id, "status": "ANALYZING", "pollIntervalMs": 1500}

순서가 중요하다. `queue.send()` 를 먼저 하면, 커밋이 실패했을 때 워커가 **DB 에 없는
식사**를 처리하려다 3번 재시도 끝에 DLQ 로 보낸다.

**Worker** — 요청 맥락이 없으니 세션을 직접 연다. 작업 하나가 트랜잭션 하나다.

    with SessionLocal() as db:
        crud_meal_item.delete_model_items(db, meal)
        for item in items:
            crud_meal_item.add(db, meal, ...)
        crud_meal.set_status(db, meal, MealStatus.REVIEW_REQUIRED)
        db.commit()                     # ← 전부 끝나고 한 번

중간에 터지면 전부 롤백되고 식사는 `ANALYZING` 으로 남는다. 큐가 재배달하므로
다시 시도된다 — 그래서 `analyze_meal` 은 상태를 먼저 보고 이미 처리된 건 건너뛴다.
"""
