"""API 명세서의 에러 코드가 `ErrorCode` 에 존재하는지.

명세서는 리포 밖에 있다. 그래서 이 파일이 명세서를 코드 쪽에 고정하는 자리다 —
명세에 있는 코드가 enum 에서 빠지면 여기서 깨진다.

`ErrorBody.code` 가 `ErrorCode` 타입이라, enum 에 있는 값은 그대로 /openapi.json
의 `ErrorCode` 스키마에 열거된다. FE 는 그 목록을 보고 분기를 짠다.
"""

from __future__ import annotations

import pytest

from app.core.errors import ErrorCode
from app.core.response import error_responses
from app.main import app

# 명세서의 "code → HTTP" 표 전체. 값은 명세서가 적어둔 status 다.
#
# 200 인 넷은 실패가 아니라 "결과는 있는데 단서가 붙는다" 는 뜻이다. 그래서
# `ApiError` 로 던지지 않고 `ok_with_code()` 로 data 와 함께 실어 보낸다.
SPEC_ERROR_CODES: dict[str, int] = {
    "PROFILE_REQUIRED": 409,
    "STAGE_NOT_SET": 409,
    "NOT_CONFIRMED": 409,
    "FOOD_NOT_RECOGNIZED": 422,
    "ANALYSIS_TIMEOUT": 504,
    "LOW_CONFIDENCE": 200,
    "NUTRITION_NOT_MATCHED": 200,
    "MEDICAL_QUESTION_DETECTED": 200,
    "FEEDBACK_GENERATION_FAILED": 200,
}


@pytest.mark.parametrize("code_name", sorted(SPEC_ERROR_CODES))
def test_spec_error_code_exists_in_enum(code_name):
    assert code_name in ErrorCode.__members__


def test_spec_error_codes_are_listed_in_openapi():
    """FE 가 실제로 읽는 곳은 /openapi.json 의 ErrorCode 열거다."""
    schema = app.openapi()
    listed = set(schema["components"]["schemas"]["ErrorCode"]["enum"])

    assert set(SPEC_ERROR_CODES) <= listed


# 200 인 넷은 `error_responses()` 를 타지 않는다 — 실패 응답이 아니다.
SPEC_FAILURE_STATUSES = {
    name: status for name, status in SPEC_ERROR_CODES.items() if status != 200
}


@pytest.mark.parametrize(
    ("code_name", "http_status"), sorted(SPEC_FAILURE_STATUSES.items())
)
def test_spec_status_is_declarable_and_names_its_code(code_name, http_status):
    """명세의 status 는 `responses=` 로 선언할 수 있어야 하고, 그 설명에 해당
    code 가 적혀 있어야 한다.

    `_ERROR_DESCRIPTIONS` 에 빠진 status 는 `error_responses()` 에서 KeyError 로
    앱을 죽인다 — 라우트가 그 status 를 선언하는 순간이다. 설명 쪽은 /docs 에
    그대로 보이는 문구라, 명세에 있는 code 가 안 적혀 있으면 FE 가 못 찾는다.
    """
    declared = error_responses(http_status)

    assert code_name in declared[http_status]["description"]
