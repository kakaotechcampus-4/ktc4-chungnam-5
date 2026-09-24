"""파일 저장소 — 식사 사진 업로드.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 로컬 개발은 디스크에 저장하고,
운영은 S3Storage 로 교체될 자리다 (아직 미구현 — S3 버킷·IAM 정책이 필요하다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict


class FileStorage(Protocol):
    """도메인은 이 모양만 안다 — 로컬 디스크인지 S3 인지 모른다."""

    def save(self, key: str, data: bytes) -> None: ...

    def url(self, key: str) -> str: ...


class StorageSettings(BaseSettings):
    """`core.Settings` 에 넣지 않은 이유는 `infra/queue.py` 의 `QueueSettings` 와
    같다 — 이 어댑터가 전역 설정을 import 하지 않게 하려는 것이다."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LOCAL_STORAGE_DIR: str = "var/media"
    """로컬 디스크에 파일을 저장할 디렉터리. `backend/` 기준 상대 경로."""
    LOCAL_STORAGE_BASE_URL: str = "http://127.0.0.1:8000/media"
    """local 구현이 돌려주는 URL 의 앞부분. `main.py` 의 정적 파일 마운트와 짝이어야 한다."""


class LocalDiskStorage:
    """로컬 개발용 — 디스크에 저장하고, `main.py` 가 `/media` 로 서빙하는 정적 파일
    URL 을 돌려준다.

    **진짜 presigned URL 이 아니다.** 인증 없이 누구나 접근 가능한 URL 이라, 실제
    S3Storage 로 교체하기 전까지는 로컬 개발 전용이다.
    """

    def __init__(self, *, directory: Path, base_url: str) -> None:
        self._directory = directory
        self._base_url = base_url.rstrip("/")
        self._directory.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> None:
        path = self._directory / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def url(self, key: str) -> str:
        return f"{self._base_url}/{key}"


def build_file_storage(settings: StorageSettings | None = None) -> FileStorage:
    settings = settings or StorageSettings()
    return LocalDiskStorage(
        directory=Path(settings.LOCAL_STORAGE_DIR),
        base_url=settings.LOCAL_STORAGE_BASE_URL,
    )


def get_file_storage() -> FileStorage:
    """FastAPI `Depends()` 전용 래퍼.

    `build_file_storage(settings: StorageSettings | None = None)` 를 그대로
    `Depends()` 에 넘기면, FastAPI 가 `settings` 를 "요청 본문에서 채울 필드"로
    착각한다 — `StorageSettings` 가 pydantic 모델이라서다. 파라미터가 없는 이
    래퍼로 그 문제를 피한다.
    """
    return build_file_storage()
