"""`main.mount_web` — 평가용 웹(Flutter web 빌드)을 `/web` 으로 내려준다.

웹 파일은 운영 이미지에만 들어간다(`backend/Dockerfile` 의 `release` 단계). 로컬 BE 개발과
테스트에는 디렉터리가 없으므로 mount 하지 않는다.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import mount_web


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    """Flutter web 빌드 결과를 흉내 낸 디렉터리."""
    (tmp_path / "index.html").write_text("<html>web</html>")
    (tmp_path / "main.dart.js").write_text("console.log('web')")
    return tmp_path


def _client(directory: Path) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    mount_web(app, directory)
    return TestClient(app)


def test_web_root_serves_index_html(web_dir: Path) -> None:
    response = _client(web_dir).get("/web/")

    assert response.status_code == 200
    assert response.text == "<html>web</html>"


@pytest.mark.parametrize("path", ["/web/", "/web/main.dart.js"])
def test_web_files_are_revalidated_every_time(web_dir: Path, path: str) -> None:
    """Flutter web 은 `main.dart.js` 파일명에 해시가 없다 — 캐시를 두면 배포 뒤에도 옛 화면이 남는다."""
    response = _client(web_dir).get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_unchanged_web_file_is_not_sent_again(web_dir: Path) -> None:
    """no-cache 는 "매번 확인" 이지 "매번 받기" 가 아니다 — 안 바뀐 파일은 304."""
    client = _client(web_dir)
    etag = client.get("/web/main.dart.js").headers["etag"]

    response = client.get("/web/main.dart.js", headers={"If-None-Match": etag})

    assert response.status_code == 304


def test_web_without_trailing_slash_redirects_to_web_root(web_dir: Path) -> None:
    response = _client(web_dir).get("/web", follow_redirects=False)

    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"].endswith("/web/")


def test_web_is_not_mounted_without_build(tmp_path: Path) -> None:
    """로컬 BE 개발 · 테스트 — 웹 빌드가 없으면 `/web` 길 자체가 없다."""
    response = _client(tmp_path / "missing").get("/web/")

    assert response.status_code == 404


def test_web_mount_leaves_other_paths_alone(web_dir: Path) -> None:
    client = _client(web_dir)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/ping").json() == {"pong": "ok"}
