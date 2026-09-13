"""라우터 공통 — 시나리오 해석과 그 부수효과.

시나리오는 가드레일보다 먼저 평가된다. 테스트가 입력 문구를 꾸며내지 않고
경로를 고를 수 있어야 하기 때문이다.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Scenario, get_settings


def resolve_scenario(
    x_stub_scenario: Annotated[str | None, Header()] = None,
) -> Scenario:
    if x_stub_scenario is None:
        return get_settings().stub_default_scenario
    try:
        return Scenario(x_stub_scenario.strip().upper())
    except ValueError:
        known = ", ".join(s.value for s in Scenario)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"알 수 없는 X-Stub-Scenario: {x_stub_scenario}. 가능한 값: {known}",
        ) from None


ScenarioDep = Annotated[Scenario, Depends(resolve_scenario)]


async def apply_scenario(scenario: Scenario) -> None:
    """가드레일보다 먼저 도는 부수효과. 응답을 만들지 않고 흐름만 바꾼다."""
    if scenario is Scenario.ERROR_500:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="스텁이 의도적으로 낸 오류 (X-Stub-Scenario: ERROR_500)",
        )
    if scenario is Scenario.SLOW:
        await asyncio.sleep(get_settings().stub_latency_ms / 1000)
