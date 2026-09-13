"""모든 모델 호출의 단일 진입점.

에이전트가 클라이언트를 직접 만들지 않는다. STUB_MODE 분기는 이 파일에만 있고,
실제 LLM 을 붙일 때 고치는 파일도 여기 하나다.

구현 묶음은 **지연 import** 한다. 이미지마다 한쪽만 설치되기 때문이다 (S8):

    glp1-ai-stub  →  agents/stub/ 만 있다. agents/llm/ 은 없다.
    glp1-ai       →  agents/llm/ 만 있다. agents/stub/ 은 없다.

최상단에서 import 하면 상대 이미지에서 기동하자마자 ImportError 가 난다.
"""

from __future__ import annotations

from functools import lru_cache
from types import ModuleType

from agents.schemas import (
    AnalyzeMealRequest,
    AnalyzeMealResponse,
    LongFeedbackRequest,
    LongFeedbackResponse,
    ShortFeedbackRequest,
    ShortFeedbackResponse,
)
from app.config import Scenario, get_settings

_REAL_MODE_MESSAGE = (
    "실제 LLM 구현(agents/llm/)이 이 이미지에 없다. "
    "스텁으로 띄우려면 STUB_MODE=true, 실사용이라면 agents/llm/ 을 구현할 것."
)


@lru_cache
def _impl() -> ModuleType:
    """STUB_MODE 에 따라 구현 묶음을 고른다."""
    if get_settings().stub_mode:
        from agents import stub

        return stub

    try:
        from agents import llm
    except ImportError as exc:
        # `from agents import llm` 은 하위 모듈이 없을 때 ModuleNotFoundError 가 아니라
        # 평범한 ImportError 를 낸다. 상위 클래스로 잡아야 한다.
        raise NotImplementedError(_REAL_MODE_MESSAGE) from exc
    return llm


def analyze_meal(
    req: AnalyzeMealRequest, scenario: Scenario, *, blocked: bool = False
) -> AnalyzeMealResponse:
    agent = _impl().analyze_meal
    return agent.blocked(req) if blocked else agent.build(req, scenario)


def short_feedback(
    req: ShortFeedbackRequest, scenario: Scenario, *, blocked: bool = False
) -> ShortFeedbackResponse:
    agent = _impl().short_feedback
    return agent.blocked(req) if blocked else agent.build(req, scenario)


def long_feedback(
    req: LongFeedbackRequest, scenario: Scenario, *, blocked: bool = False
) -> LongFeedbackResponse:
    agent = _impl().long_feedback
    return agent.blocked(req) if blocked else agent.build(req, scenario)
