r"""큐 상태 보기.

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.queue_status

처리 중은 `status = 'PROCESSING'` 이다. lease 가 이미 지난 것은 워커가 죽은 것이고,
다음 claim 이 회수한다.
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

_IN_FLIGHT = text(
    """
    SELECT type, attempts, now() - updated_at AS elapsed, lease_expires_at < now() AS expired
      FROM task_queue
     WHERE status = 'PROCESSING'
     ORDER BY updated_at
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
        expired = sum(1 for row in in_flight if row.expired)
        print(f"\n처리 중(PROCESSING): {len(in_flight)}  — lease 만료 {expired}건은 다음 claim 이 회수한다")
        for row in in_flight:
            mark = " (만료)" if row.expired else ""
            print(f"  {row.type:<16} attempts={row.attempts} 경과={row.elapsed}{mark}")

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
