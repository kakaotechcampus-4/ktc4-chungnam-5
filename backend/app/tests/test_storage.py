"""`infra/storage.py` — 사진 저장소 두 구현과 고르는 규칙.

S3 는 `moto` 의 가짜 S3 로 시험한다. boto3 코드는 그대로 돌고 네트워크만 막힌다.
presigned URL 은 서명만 하는 로컬 계산이라 실제 AWS 없이도 모양을 확인할 수 있다.
"""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from moto import mock_aws
from pydantic import ValidationError

from app.infra.storage import (
    LocalDiskStorage,
    S3Storage,
    StorageSettings,
    build_file_storage,
)

BUCKET = "glp1-media-test"
REGION = "ap-northeast-2"


@pytest.fixture
def s3_client(monkeypatch):
    """가짜 S3 와 버킷. 서버 역할 대신 moto 가 주는 가짜 자격 증명을 쓴다."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        yield client


def _s3(expires_sec: int = 3600) -> S3Storage:
    return S3Storage(bucket=BUCKET, region=REGION, expires_sec=expires_sec)


# ── LocalDiskStorage ────────────────────────────────────────


def test_local_client_url_is_the_relative_media_path(tmp_path: Path) -> None:
    """앱은 `/media/...` 를 받아 자기 서버 주소를 붙인다(FE `ApiConfig.mediaUrl`). 지금 동작 그대로."""
    storage = LocalDiskStorage(directory=tmp_path, base_url="http://api:8000/media")

    assert storage.client_url("meals/a.jpg") == "/media/meals/a.jpg"


def test_local_url_is_the_internal_base_url(tmp_path: Path) -> None:
    """AI 워커가 사진을 받아 가는 서버 안 주소. 앱용 주소와 달라야 한다."""
    storage = LocalDiskStorage(directory=tmp_path, base_url="http://api:8000/media/")

    assert storage.url("meals/a.jpg") == "http://api:8000/media/meals/a.jpg"


# ── S3Storage ───────────────────────────────────────────────


def test_s3_save_puts_object_with_image_content_type(s3_client) -> None:
    _s3().save("meals/a.jpg", b"jpeg-bytes")

    obj = s3_client.get_object(Bucket=BUCKET, Key="meals/a.jpg")
    assert obj["Body"].read() == b"jpeg-bytes"
    assert obj["ContentType"] == "image/jpeg"


@pytest.mark.parametrize(
    ("key", "content_type"),
    [
        ("meals/a.png", "image/png"),
        ("meals/a.webp", "image/webp"),
        ("meals/a.heic", "image/heic"),
        ("meals/a", "application/octet-stream"),
    ],
)
def test_s3_content_type_follows_extension(s3_client, key, content_type) -> None:
    """ContentType 이 틀리면 브라우저 · 앱이 사진을 내려받기만 하고 그리지 않는다."""
    _s3().save(key, b"x")

    assert s3_client.head_object(Bucket=BUCKET, Key=key)["ContentType"] == content_type


def test_s3_url_is_a_seoul_presigned_get_url(s3_client) -> None:
    """서울 리전 주소 · 서명 v4 · 만료 시간. 계정이 서울 외 리전을 막으므로 리전이 주소에 드러나야 한다."""
    url = _s3(expires_sec=1800).url("meals/a.jpg")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == f"{BUCKET}.s3.{REGION}.amazonaws.com"
    assert parsed.path == "/meals/a.jpg"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["1800"]
    assert f"/{REGION}/s3/aws4_request" in query["X-Amz-Credential"][0]


def test_s3_client_url_is_also_presigned(s3_client) -> None:
    """S3 에서는 앱도 워커도 같은 presigned URL 로 받아 간다."""
    url = _s3().client_url("meals/a.jpg")

    assert urlparse(url).netloc == f"{BUCKET}.s3.{REGION}.amazonaws.com"
    assert "X-Amz-Signature" in parse_qs(urlparse(url).query)


# ── 설정 · 고르기 ───────────────────────────────────────────


def test_default_is_local_disk(monkeypatch, tmp_path: Path) -> None:
    """기본값은 로컬 디스크다 — 이 코드가 머지돼도 설정을 바꾸기 전까지 아무것도 안 바뀐다."""
    monkeypatch.delenv("STORAGE_TYPE", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))

    assert isinstance(build_file_storage(), LocalDiskStorage)


def test_s3_type_builds_s3_storage(monkeypatch, s3_client) -> None:
    monkeypatch.setenv("STORAGE_TYPE", "s3")
    monkeypatch.setenv("S3_BUCKET", BUCKET)

    storage = build_file_storage()

    assert isinstance(storage, S3Storage)
    assert urlparse(storage.url("k")).netloc == f"{BUCKET}.s3.{REGION}.amazonaws.com"


def test_s3_type_without_bucket_fails_fast(monkeypatch) -> None:
    """버킷 없이 s3 로 켜면 업로드 때가 아니라 설정을 읽는 순간 실패한다."""
    monkeypatch.setenv("STORAGE_TYPE", "s3")
    monkeypatch.delenv("S3_BUCKET", raising=False)

    with pytest.raises(ValidationError):
        StorageSettings(_env_file=None)


def test_defaults_are_seoul_and_one_hour(monkeypatch) -> None:
    for name in ("S3_REGION", "S3_PRESIGN_EXPIRES_SEC"):
        monkeypatch.delenv(name, raising=False)

    settings = StorageSettings(_env_file=None)

    assert settings.S3_REGION == "ap-northeast-2"
    assert settings.S3_PRESIGN_EXPIRES_SEC == 3600


# ── /media 정적 서빙 ────────────────────────────────────────


def _paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_media_is_mounted_for_local_disk(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from app.main import mount_media

    app = FastAPI()
    mount_media(app, StorageSettings(_env_file=None, LOCAL_STORAGE_DIR=str(tmp_path)))

    assert "/media" in _paths(app)


def test_media_is_not_mounted_for_s3() -> None:
    """S3 로 바꾸면 서버 디스크 사진을 내주는 길을 닫는다 — 데모 키를 없앴을 때 `/media` 가
    공개되지 않게 하는 것이 I절의 목적이다."""
    from fastapi import FastAPI

    from app.main import mount_media

    app = FastAPI()
    mount_media(app, StorageSettings(_env_file=None, STORAGE_TYPE="s3", S3_BUCKET=BUCKET))

    assert "/media" not in _paths(app)
