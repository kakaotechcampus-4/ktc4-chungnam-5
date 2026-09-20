"""모든 응답 스키마가 공유하는 베이스.

파이썬 코드는 snake_case, JSON 응답은 camelCase 로 나간다.
매 스키마마다 alias_generator 를 반복 설정하지 않으려고 여기서 한 번만 정의한다.
"""

from pydantic import BaseModel, ConfigDict


def to_camel(snake_str: str) -> str:
    first, *rest = snake_str.split("_")
    return first + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
