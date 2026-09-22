r"""큐 상태 보기.

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.queue_status

"처리 중" 이라는 상태 컬럼은 없다. 워커는 행 잠금을 쥐고 있을 뿐이고 그건 커밋 전이라
다른 세션에 안 보인다. 그래서 대신 **작업 트랜잭션을 열어 둔 커넥션**을 보여 준다.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import SessionLocal

_SUMMARY = text(
    """
    SELECT status, count(*) AS n, min(created_at) AS oldest
      FROM task_queue
     GROUP BY status
     ORDER BY status
    """
)

# 잠긴 행을 pg_locks 로 되짚는 건 튜플 단위라 실용적이지 않아, task_queue 를 만지며
# 트랜잭션을 연 채인 커넥션을 센다. 근사라서 **워커가 아닌 세션도 잡힌다** —
# 누군가 같은 시각에 이 스크립트를 돌리고 있으면 그 세션도 여기 나온다.
_IN_FLIGHT = text(
    """
    SELECT pid, now() - xact_start AS elapsed
      FROM pg_stat_activity
     WHERE xact_start IS NOT NULL
       AND query ILIKE '%task_queue%'
       AND pid <> pg_backend_pid()
     ORDER BY xact_start
    """
)

_FAILED = text(
    """
    SELECT id, type, attempts, updated_at, left(last_error, 80) AS last_error
      FROM task_queue
     WHERE status = 'FAILED'
     ORDER BY updated_at DESC
     LIMIT 10
    """
)


def main() -> None:
    with SessionLocal() as db:
        print(f"{'상태':<10} {'건수':>6}  가장 오래된 것")
        print("-" * 48)
        for status, count, oldest in db.execute(_SUMMARY):
            print(f"{status:<10} {count:>6}  {oldest}")

        in_flight = db.execute(_IN_FLIGHT).all()
        print(f"\n처리 중(작업 트랜잭션을 연 커넥션): {len(in_flight)}")
        print("  (근사치 — task_queue 를 만지며 트랜잭션을 연 커넥션을 센다. 워커가 아닌 세션도 잡힐 수 있다)")
        for pid, elapsed in in_flight:
            print(f"  pid={pid} 경과={elapsed}")

        failed = db.execute(_FAILED).all()
        if failed:
            print(f"\nFAILED {len(failed)}건 — 재시도를 다 쓰고 격리된 작업이다")
            for row in failed:
                print(f"  {row.type:<16} attempts={row.attempts} {row.updated_at} {row.last_error}")
            print("\n다시 넣으려면:")
            print("  UPDATE task_queue SET status='PENDING', attempts=0, next_run_at=now()")
            print("   WHERE id = '<id>';")


if __name__ == "__main__":
    main()
