"""모든 응답 스키마가 공유하는 베이스.

파이썬 코드는 snake_case, JSON 응답은 camelCase 로 나간다.
매 스키마마다 alias_generator 를 반복 설정하지 않으려고 여기서 한 번만 정의한다.
"""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, PlainSerializer, WithJsonSchema

_KST_ZONE = ZoneInfo("Asia/Seoul")


def to_camel(snake_str: str) -> str:
    first, *rest = snake_str.split("_")
    return first + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


def _to_kst_iso(value: datetime) -> str:
    return value.astimezone(_KST_ZONE).isoformat()


KstDatetime = Annotated[
    datetime,
    PlainSerializer(_to_kst_iso, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
"""응답 시각 타입. JSON 으로 나갈 때 Asia/Seoul 로 바꿔 +09:00 을 붙인 ISO 8601 로 쓴다 (팀 관례).

DB 는 UTC 로 돌려주므로 그대로 두면 "+00:00" 으로 나간다. 파이썬 안(model_dump)에서는
datetime 그대로다. 시간대 없는(naive) 값은 서버 로컬 시간으로 해석되니 넣지 않는다.
"""
