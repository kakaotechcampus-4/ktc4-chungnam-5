"""모든 모델 호출의 단일 진입점.

에이전트가 클라이언트를 직접 만들지 않는다. STUB_MODE 분기는 이 파일에만 있고,
실제 LLM 을 붙일 때 고치는 파일도 여기 하나다.
"""

from __future__ import annotations

from agents.schemas import (
    AnalyzeMealRequest,
    AnalyzeMealResponse,
    LongFeedbackRequest,
    LongFeedbackResponse,
    ShortFeedbackRequest,
    ShortFeedbackResponse,
)
from agents.stub import analyze_meal as _analyze_stub
from agents.stub import long_feedback as _long_stub
from agents.stub import short_feedback as _short_stub
from app.config import Scenario, get_settings

_REAL_MODE_MESSAGE = (
    "실제 LLM 연동은 아직 없다. STUB_MODE=true 로 두거나 runner 의 이 분기를 구현할 것."
)


def _require_stub_mode() -> None:
    if not get_settings().stub_mode:
        raise NotImplementedError(_REAL_MODE_MESSAGE)


def analyze_meal(
    req: AnalyzeMealRequest, scenario: Scenario, *, blocked: bool = False
) -> AnalyzeMealResponse:
    _require_stub_mode()
    if blocked:
        return _analyze_stub.blocked(req)
    return _analyze_stub.build(req, scenario)


def short_feedback(
    req: ShortFeedbackRequest, scenario: Scenario, *, blocked: bool = False
) -> ShortFeedbackResponse:
    _require_stub_mode()
    if blocked:
        return _short_stub.blocked(req)
    return _short_stub.build(req, scenario)


def long_feedback(
    req: LongFeedbackRequest, scenario: Scenario, *, blocked: bool = False
) -> LongFeedbackResponse:
    _require_stub_mode()
    if blocked:
        return _long_stub.blocked(req)
    return _long_stub.build(req, scenario)
