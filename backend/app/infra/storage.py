"""파일 저장소 — 식사 사진 업로드.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 로컬 개발은 디스크에(`LocalDiskStorage`),
운영 · 스테이징은 S3 에(`S3Storage`) 저장한다. 어느 쪽인지는 `STORAGE_TYPE` 이 정한다
(배포 계획서 I절).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import boto3
from botocore.config import Config
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FileStorage(Protocol):
    """도메인은 이 모양만 안다 — 로컬 디스크인지 S3 인지 모른다.

    주소가 둘인 이유: 로컬 디스크에서는 **누가 받아 가느냐** 에 따라 주소가 다르다.
    AI 워커는 서버 안 주소(`http://api:8000/media/…`)로 받고, 앱은 자기 서버 주소에 붙일
    경로(`/media/…`)를 받는다 — 앱에 서버 안 주소를 주면 열 수 없다. S3 에서는 둘 다 같은
    presigned URL 이다.
    """

    def save(self, key: str, data: bytes) -> None: ...

    def url(self, key: str) -> str:
        """서버 안(AI 워커)에서 사진을 받아 갈 주소."""
        ...

    def client_url(self, key: str) -> str:
        """앱(FE)에 내려줄 주소. `ApiImage` 가 그대로 그리거나 서버 주소를 붙여 그린다."""
        ...


class StorageSettings(BaseSettings):
    """`core.Settings` 에 넣지 않은 이유는 `infra/queue.py` 의 `QueueSettings` 와
    같다 — 이 어댑터가 전역 설정을 import 하지 않게 하려는 것이다."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    STORAGE_TYPE: Literal["local", "s3"] = "local"
    """기본은 로컬 디스크다 — 이 코드가 들어와도 `.env` 를 바꾸기 전까지 아무것도 안 바뀐다."""

    LOCAL_STORAGE_DIR: str = "var/media"
    """로컬 디스크에 파일을 저장할 디렉터리. `backend/` 기준 상대 경로."""
    LOCAL_STORAGE_BASE_URL: str = "http://127.0.0.1:8000/media"
    """local 구현이 워커에게 주는 URL 의 앞부분. `main.py` 의 정적 파일 마운트와 짝이어야 한다."""

    S3_BUCKET: str | None = None
    """`STORAGE_TYPE=s3` 일 때 필수. 운영 · 스테이징 버킷이 따로다 (서버의 `.env.*` 에서 정한다)."""
    S3_REGION: str = "ap-northeast-2"
    """**서울 고정.** 팀 계정은 서울 외 리전 요청을 조직 정책(SCP)으로 막는다 — boto3 가 리전을
    모르고 기본값(미국)으로 보내면 권한이 있어도 거부된다."""
    S3_PRESIGN_EXPIRES_SEC: int = Field(default=3600, ge=60, le=604800)
    """presigned URL 유효 시간. 1시간 — 화면을 그보다 오래 켜 두면 사진이 깨지고, 다시
    불러오면 새 URL 을 받는다. 상한 7일은 SigV4 presigned URL 의 최대값이다."""

    @model_validator(mode="after")
    def _bucket_required_for_s3(self) -> StorageSettings:
        # 업로드하는 순간이 아니라 설정을 읽는 순간 실패한다 — 버킷을 빠뜨린 채 배포하면
        # api 가 뜨긴 하는데 사진만 전부 500 이 나는 것보다 낫다.
        if self.STORAGE_TYPE == "s3" and not self.S3_BUCKET:
            raise ValueError("STORAGE_TYPE=s3 이면 S3_BUCKET 이 필요하다")
        return self


class LocalDiskStorage:
    """로컬 개발용 — 디스크에 저장하고, `main.py` 가 `/media` 로 서빙하는 정적 파일
    URL 을 돌려준다.

    **진짜 presigned URL 이 아니다.** 인증 없이 누구나 접근 가능한 URL 이라 지금은
    Caddy 의 데모 키가 `/media` 를 막는다. 데모 키를 없애기 전에 `S3Storage` 로 바꾼다.
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

    def client_url(self, key: str) -> str:
        # 앱은 이 경로에 자기가 붙은 서버 주소를 붙인다 (FE `ApiConfig.mediaUrl`).
        return f"/media/{key}"


_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".gif": "image/gif",
}
"""업로드 키의 확장자 → ContentType. 틀리면 브라우저 · 앱이 사진을 그리지 않고 내려받기만
한다. 표준 `mimetypes` 는 HEIC(아이폰 기본 사진)를 모른다."""


def _content_type(key: str) -> str:
    return _CONTENT_TYPES.get(Path(key).suffix.lower(), "application/octet-stream")


@lru_cache(maxsize=4)
def _s3_client(region: str) -> Any:
    """boto3 클라이언트는 만들 때 비싸다(수십 ms) — 리전마다 하나를 재사용한다.
    스레드 안전하므로 FastAPI 스레드풀에서 같이 써도 된다.

    자격 증명은 넘기지 않는다 — boto3 기본 체인이 EC2 인스턴스 역할(`ktc-ec2-ssm-role`)에서
    받는다. AWS 액세스 키를 `.env` 에 넣지 않는다 (팀 계정은 키 발급 자체가 막혀 있다).
    `addressing_style=virtual` 은 presigned URL 을 `<버킷>.s3.<리전>.amazonaws.com` 로 만든다.
    """
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


class S3Storage:
    """운영 · 스테이징용 — S3 비공개 버킷에 저장하고 presigned URL 로 내준다.

    버킷은 퍼블릭 액세스를 전부 막아 두었다. 사진은 서명된 URL 로만, 유효 시간 안에만 열린다.
    URL 을 만드는 건 서명 계산뿐이라 AWS 호출이 없다 — 목록 화면에서 사진마다 불러도 된다.
    """

    def __init__(self, *, bucket: str, region: str, expires_sec: int) -> None:
        self._bucket = bucket
        self._expires_sec = expires_sec
        self._client = _s3_client(region)

    def save(self, key: str, data: bytes) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=_content_type(key)
        )

    def url(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._expires_sec,
        )

    def client_url(self, key: str) -> str:
        # 앱도 워커와 같은 주소로 S3 에서 직접 받는다. 완전한 URL 이라 FE 는 그대로 쓴다.
        return self.url(key)


def build_file_storage(settings: StorageSettings | None = None) -> FileStorage:
    settings = settings or StorageSettings()
    if settings.STORAGE_TYPE == "s3":
        return S3Storage(
            bucket=settings.S3_BUCKET or "",  # validator 가 비어 있지 않음을 보장한다
            region=settings.S3_REGION,
            expires_sec=settings.S3_PRESIGN_EXPIRES_SEC,
        )
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
