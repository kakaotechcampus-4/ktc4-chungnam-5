"""eval 사진 fixture 전달 — 이미지 GET 만 로컬 HTTP 서버로 대체하고 모델 POST 는 실제로 둔다.

`app.food_analyzer` 는 `imageUrl` 을 httpx 로 직접 GET 해 data URI 로 바꾼다. fixture 디렉터리를
127.0.0.1 임의 포트로 띄우고 그 주소를 `imageUrl` 에 붙이면 `ai/app` 수정 · monkeypatch 없이
실제 다운로드 경로(content-type → data URI)까지 거친다.

CASES 의 `imageUrl` 에는 fixture **파일 이름만** 두고, 실행 시 `with_image_base` 가 서버 주소를 붙인다.
서버 수명은 eval `main()` 이 `with serve_fixtures(...)` 로 `cli_main` 전체를 감싸 정한다.
harness.py 는 chat · DAILY 와 공유하는 파일이라 이 helper 를 따로 둔다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from evals.harness import Post

MAX_FIXTURE_BYTES: int = 200_000
"""fixture 한 장의 크기 상한(바이트). 저장소에 커밋하므로 줄여서 둔다."""


@contextmanager
def serve_fixtures(directory: Path) -> Iterator[str]:
    """`directory` 를 127.0.0.1 임의 포트로 띄우고 base URL 을 내준다. 블록을 벗어나면(예외 포함) 닫는다."""
    raise NotImplementedError("serve_fixtures 미구현")
    yield  # pragma: no cover - 제너레이터로 만들기 위한 줄


def with_image_base(post: Post, base_url: str) -> Post:
    """요청의 `imageUrl`(파일 이름)에 `base_url` 을 붙여 보내는 Post 를 만든다. 원래 요청 dict 는 바꾸지 않는다."""
    raise NotImplementedError("with_image_base 미구현")


def fixture_problems(path: Path, *, max_bytes: int = MAX_FIXTURE_BYTES) -> list[str]:
    """fixture 한 장의 문제 목록 — 없음 · 확장자 · 시그니처 · 크기 · EXIF · `test_` 이름. 문제가 없으면 `[]`."""
    raise NotImplementedError("fixture_problems 미구현")
