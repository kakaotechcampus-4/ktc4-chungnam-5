"""JSON 요청으로 피드백을 수동 평가한다. 서버·BE·DB 없이 실제 API 처리 함수를 실행한다."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import get_settings
from app.main import short_feedback
from app.schemas import Scope, ShortFeedbackRequest
from app import short_feedback as feedback


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def prepare(req: ShortFeedbackRequest) -> dict:
    judgements = feedback.judge_nutrients(req) if req.scope is Scope.MEAL else []
    return {
        "input": req.model_dump(mode="json", by_alias=True),
        "judgements": [asdict(j) for j in judgements],
        "cumulativeProgress": ([asdict(p) for p in feedback.cumulative_progress(req)]
                               if req.scope is Scope.MEAL else []),
        "systemPrompt": (feedback.MEAL_SYSTEM_PROMPT if req.scope is Scope.MEAL
                         else feedback.DAILY_SYSTEM_PROMPT),
        "userPrompt": (feedback.build_meal_prompt(req, judgements) if req.scope is Scope.MEAL
                       else feedback.build_daily_prompt(req)),
        "modelCalled": False,
        "rawResponse": None,
        "finalResponse": None,
    }


async def evaluate(req: ShortFeedbackRequest, report: dict) -> None:
    # 실제 API 처리 경로를 쓰고 모델 호출만 감싸 프롬프트와 원문을 기록한다.
    call_model = feedback._call_reasoning_model

    async def capture(system: str, user: str) -> str:
        report.update(modelCalled=True, systemPrompt=system, userPrompt=user)
        try:
            raw = await call_model(system, user)
        except httpx.HTTPStatusError as exc:
            report["gatewayStatus"] = exc.response.status_code
            raise
        report["rawResponse"] = raw
        return raw

    started = time.monotonic()
    # 이 CLI 프로세스 안에서만 관찰한다. 결과 확인을 위해 모델을 다시 부르지 않는다.
    with patch.object(feedback, "_call_reasoning_model", capture):
        try:
            response = await short_feedback(req)
            report["finalResponse"] = response.model_dump(mode="json", by_alias=True)
            report["status"] = "completed"
        except HTTPException as exc:
            report.update(status="failed", apiStatus=exc.status_code, error=exc.detail)
        finally:
            report["elapsedSeconds"] = round(time.monotonic() - started, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="BE 계약 형식의 JSON 요청")
    parser.add_argument("--dry-run", action="store_true", help="입력·판정·프롬프트만 확인 (과금 없음)")
    args = parser.parse_args(argv)
    input_path = args.input.resolve()
    try:
        req = ShortFeedbackRequest.model_validate_json(input_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        print("입력 오류: " + dump([
            {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
            for error in exc.errors()
        ]))
        return 2
    except OSError:
        print("입력 파일을 읽을 수 없습니다.")
        return 2

    report = prepare(req)
    report["status"] = "dry-run"
    if not args.dry_run:
        # 실행 위치와 무관하게 ai/.env를 읽으며, 사용자 설정 파일은 변경하지 않는다.
        os.chdir(AI_ROOT)
        try:
            settings = get_settings()
            if not settings.LLM_API_KEY.get_secret_value().strip():
                print("설정 오류: ai/.env의 LLM_API_KEY를 입력하세요.")
                return 2
            # 수동 평가 한 번당 최대 한 요청. 재시도로 추가 과금하지 않는다.
            settings.LLM_MAX_RETRIES = 0
            print("실제 LLM을 최대 1회 호출합니다. 크레딧이 사용됩니다.", flush=True)
            asyncio.run(evaluate(req, report))
        except ValidationError as exc:
            print("설정 오류: " + ", ".join(str(e["loc"][0]) for e in exc.errors()))
            return 2
        except Exception as exc:
            # 예외 원문·traceback은 URL·키를 포함할 수 있으므로 출력하지 않는다.
            report.update(status="failed", errorType=type(exc).__name__)

    sections = [("1. BE 입력", "input"), ("2. Python 끼니 영양 판정", "judgements"),
                ("2-1. Python 누적 영양 진행률", "cumulativeProgress"),
                ("3. System 프롬프트", "systemPrompt"), ("4. User 프롬프트", "userPrompt"),
                ("5. 모델 원본 응답", "rawResponse"), ("6. 최종 API 응답", "finalResponse")]
    for title, key in sections:
        value = report[key]
        print(f"\n=== {title} ===\n" + (value if isinstance(value, str) else dump(value)))
    print("\n실행 결과: " + dump({k: v for k, v in report.items()
                                     if k not in {key for _, key in sections}}))
    if not args.dry_run:
        results = AI_ROOT / "manual_feedback" / "results"
        results.mkdir(parents=True, exist_ok=True)
        output = results / (datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S-%f") + ".json")
        output.write_text(dump(report) + "\n", encoding="utf-8")
        print(f"\n결과 저장: {output}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
