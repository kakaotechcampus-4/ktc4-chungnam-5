"""골든 eval 실행기 — 케이스마다 N 번 돌려 통과율을 내고, 게이트와 baseline 비교를 한다.

    cd ai
    python -X utf8 -m evals.run meal_feedback --mode reference            # 모범 답으로 검사기 점검
    python -X utf8 -m evals.run meal_feedback --n 3 --save evals/baseline/meal_feedback.json
    python -X utf8 -m evals.run meal_feedback --n 3 --baseline evals/baseline/meal_feedback.json

`--mode live`(기본)는 서비스와 같은 경로(`TestClient(app)` → FastAPI 라우트)로 부른다. 모듈 함수를
직접 부르면 `main.py` 의 safetyStatus 래핑과 가드레일을 건너뛴 채 재게 된다.
키가 없거나 엔드포인트가 아직 501 이면 SKIP 하고 0 으로 끝난다 — CI 를 막지 않는다.

종료 코드: 게이트 통과 0, 실패 1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from evals import golden
from evals import meal_feedback as mf


class Skip(Exception):
    pass


def _live_responder():
    if not os.environ.get("LLM_API_KEY"):
        raise Skip("LLM_API_KEY 가 없다")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    def respond(case: dict[str, Any]) -> dict[str, Any] | None:
        res = client.post("/short-feedback", json=case["input"])
        if res.status_code == 501:
            raise Skip("/short-feedback 이 아직 구현되지 않았다 (501)")
        return res.json() if res.status_code == 200 else None

    return respond


def _prompt_hash() -> str:
    from app.short_feedback import MEAL_SYSTEM_PROMPT

    return hashlib.sha256(MEAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="골든 eval")
    parser.add_argument("area", choices=[mf.AREA])
    parser.add_argument("--mode", choices=["live", "reference"], default="live")
    parser.add_argument("--n", type=int, default=3, help="케이스마다 반복 횟수 (reference 는 1)")
    parser.add_argument("--threshold", type=float, default=golden.DEFAULT_THRESHOLD)
    parser.add_argument("--only", help="이 문자열이 id 에 든 케이스만")
    parser.add_argument("--save", type=Path, help="결과를 baseline JSON 으로 저장")
    parser.add_argument("--baseline", type=Path, help="이 baseline 과 비교")
    args = parser.parse_args(argv)

    common = golden.load_common(args.area)
    cases = [c for c in golden.load_cases(args.area) if not args.only or args.only in c["id"]]

    try:
        if args.mode == "reference":
            n, respond = 1, lambda case: case["reference"]
        else:
            n, respond = args.n, _live_responder()
        runs = {case["id"]: [mf.check_response(case, respond(case), common) for _ in range(n)] for case in cases}
    except Skip as e:
        print(f"SKIP — {e}")
        return 0

    tallies = golden.aggregate(runs)
    for case_id, per_check in tallies.items():
        failing = [f"{check}({t.grade}) {t.passed}/{t.runs}" for check, t in per_check.items() if t.passed < t.runs]
        print(f"{'OK  ' if not failing else 'FAIL'} {case_id}" + (f"  — {', '.join(failing)}" if failing else ""))

    meta = {
        "area": args.area,
        "mode": args.mode,
        "n": n,
        "beRuleVersion": common["beRuleVersion"],
        "promptHash": _prompt_hash(),
        "date": dt.date.today().isoformat(),
    }
    result = golden.to_baseline(tallies, meta)

    if args.baseline:
        changes = golden.compare(json.loads(args.baseline.read_text(encoding="utf-8")), result)
        print("\n[baseline 비교]")
        print("\n".join(changes) if changes else "  변화 없음")
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline 저장: {args.save}")

    reasons = golden.gate(tallies, args.threshold)
    print("\n[게이트] " + ("PASS" if not reasons else "FAIL"))
    for reason in reasons:
        print(f"  {reason}")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
