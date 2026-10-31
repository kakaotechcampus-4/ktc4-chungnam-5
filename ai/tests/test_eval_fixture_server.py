"""`evals/fixture_server.py` 단위 테스트 — 사진 fixture 전달(로컬 서버 · imageUrl 치환)과 fixture 검사.

모델 · 키 · 외부 네트워크 없음. 서버는 127.0.0.1 에만 뜨고, fixture 는 `tmp_path` 에 최소 바이트로 만든다(Pillow 없음).
통합 테스트(행 42 · 43)는 `harness.default_post()` 로 `main.py` 를 거치고, **모델 POST 만 Fake** 로 둔다 —
이미지 GET 은 실제로 로컬 서버에 간다. 설정은 `app.food_analyzer.get_settings` 를 가짜로 바꿔 `.env` 를 읽지 않는다.
테스트 이름의 `r<번호>` 는 계획 §6-2 · §11-2 명세표 행 번호다.
"""

from __future__ import annotations

import base64
import copy
import http.client
import mimetypes
import socket
import struct
import zlib
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from app.schemas import AnalyzeMealRequest
from evals.fixture_server import fixture_problems, serve_fixtures, with_image_base
from evals.harness import Observation, default_post

_SECRET = b"TOP-SECRET-OUTSIDE-FIXTURE-DIR"


# ─────────────────────────── 최소 이미지 바이트 ───────────────────────────


def _segment(marker: int, payload: bytes) -> bytes:
    """JPEG 세그먼트 — 마커 2바이트 + 길이(자기 자신 2바이트 포함) + 본문."""
    return struct.pack(">BBH", 0xFF, marker, len(payload) + 2) + payload


_APP0 = _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
_APP1_EXIF = _segment(0xE1, b"Exif\x00\x00" + b"II*\x00\x08\x00\x00\x00")
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


def _jpeg(*, exif: str | None = None) -> bytes:
    """SOI · APP0(JFIF) · EOI 최소 JPEG. `exif="first"` 면 SOI 바로 뒤, `"after_app0"` 면 APP0 뒤에 APP1 Exif."""
    if exif == "first":
        return _SOI + _APP1_EXIF + _APP0 + _EOI
    if exif == "after_app0":
        return _SOI + _APP0 + _APP1_EXIF + _EOI
    return _SOI + _APP0 + _EOI


def _secret_jpeg() -> bytes:
    """비밀 표식을 COM 세그먼트에 담은 진짜 JPEG — 확장자 · 시그니처 검사를 통과하는 '새면 안 되는 사진'."""
    return _SOI + _APP0 + _segment(0xFE, _SECRET) + _EOI


def _jpeg_of_size(size: int) -> bytes:
    """EXIF 없는 JPEG 을 COM 세그먼트(0 바이트 채움)로 정확히 `size` 바이트로 만든다."""
    pad = size - len(_jpeg()) - 4
    assert pad >= 0, "size 가 최소 JPEG 보다 작다"
    data = _SOI + _APP0 + _segment(0xFE, b"\x00" * pad) + _EOI
    assert len(data) == size
    return data


def _chunk(kind: bytes, data: bytes) -> bytes:
    """PNG 청크 — 길이 + 종류 + 본문 + CRC32(종류 + 본문)."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_IHDR = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
_IDAT = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
_IEND = _chunk(b"IEND", b"")
_EXIF_CHUNK = _chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08")


def _png(*, exif: str | None = None) -> bytes:
    """시그니처 · IHDR · IDAT · IEND 최소 PNG. `exif="before_idat"` / `"after_idat"` 면 eXIf 청크를 그 자리에 둔다."""
    if exif == "before_idat":
        return _PNG_SIG + _IHDR + _EXIF_CHUNK + _IDAT + _IEND
    if exif == "after_idat":
        return _PNG_SIG + _IHDR + _IDAT + _EXIF_CHUNK + _IEND
    return _PNG_SIG + _IHDR + _IDAT + _IEND


def _write(directory, name: str, data: bytes):
    path = directory / name
    path.write_bytes(data)
    return path


def _assert_problem(problems: list[str], name: str) -> None:
    """문제 목록이 비지 않았고, 어느 문제에든 파일 이름이 들어 있다 — 문구 전체는 고정하지 않는다."""
    assert problems, f"{name} 에 문제가 있어야 한다"
    assert any(name in p for p in problems), problems


# ─────────────────────────── HTTP 헬퍼 ───────────────────────────


def _get(url: str, *, timeout: float | httpx.Timeout = 5.0) -> httpx.Response:
    """로컬 서버 GET — 환경 프록시를 타지 않는다. http 전용이라 TLS 검증을 끈다(Client 생성 비용만 줄인다)."""
    return httpx.get(url, timeout=timeout, trust_env=False, verify=False)


_CLOSED_TIMEOUT = httpx.Timeout(5.0, connect=0.3)
"""닫힌 서버로의 GET — 연결 단계만 짧게 기다린다(거부 · 타임아웃 어느 쪽이든 닫힌 것으로 본다)."""


def _raw_get(base_url: str, raw_path: str) -> tuple[int, bytes]:
    """요청 경로를 클라이언트에서 정규화하지 않고 그대로 보낸다 (`/../` · `%2e%2e` 가 서버에 그대로 간다)."""
    url = httpx.URL(base_url)
    conn = http.client.HTTPConnection(url.host, url.port, timeout=5.0)
    try:
        conn.putrequest("GET", raw_path, skip_accept_encoding=True)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


# ─────────────────────────── serve_fixtures ───────────────────────────


def test_r34_serves_file_bytes_on_loopback(tmp_path, monkeypatch):
    """서버는 127.0.0.1 에만 뜨고(base URL 과 실제 바인드 주소 모두), fixture 파일 바이트를 그대로 200 으로 준다.

    외부 인터페이스에 열리면 eval 을 돌리는 동안 사진이 같은 네트워크에 노출된다.
    base URL 만 127.0.0.1 로 적고 `0.0.0.0` · `""` 에 바인드하는 구현을 막으려고 `bind` 호출을 기록한다.
    """
    data = _jpeg()
    _write(tmp_path, "meal.jpg", data)
    binds: list[object] = []
    original_bind = socket.socket.bind

    def _recording_bind(self, address):
        binds.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", _recording_bind)

    with serve_fixtures(tmp_path) as base:
        assert httpx.URL(base).host == "127.0.0.1"
        assert binds, "서버 소켓 bind 가 기록되지 않았다"
        assert all(isinstance(a, tuple) and a[0] == "127.0.0.1" for a in binds), binds
        response = _get(f"{base}/meal.jpg")

    assert response.status_code == 200
    assert response.content == data


@pytest.mark.parametrize(
    ("name", "expected"),
    [("meal.jpg", "image/jpeg"), ("meal.jpeg", "image/jpeg"), ("meal.png", "image/png")],
    ids=["r35-jpg", "r35-jpeg", "r35-png"],
)
def test_r35_content_type_follows_extension_not_os_registry(tmp_path, monkeypatch, name, expected):
    """content-type 은 확장자대로 정해진다 — OS mimetypes 레지스트리가 다른 값을 줘도 바뀌지 않는다.

    `food_analyzer` 는 이 헤더로 data URI 의 media type 을 만든다. Windows 레지스트리는
    `.jpg` 를 `image/pjpeg` 등으로 줄 수 있어, 레지스트리에 기대면 PC 마다 모델 입력이 달라진다.
    """
    _write(tmp_path, name, _png() if name.endswith(".png") else _jpeg())
    # OS 레지스트리가 엉뚱한 값을 준다고 가정한다
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: ("application/octet-stream", None))
    monkeypatch.setitem(mimetypes.types_map, ".jpg", "image/pjpeg")
    monkeypatch.setitem(mimetypes.types_map, ".jpeg", "image/pjpeg")
    monkeypatch.setitem(mimetypes.types_map, ".png", "image/x-png")

    with serve_fixtures(tmp_path) as base:
        response = _get(f"{base}/{name}")

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0].strip() == expected


def test_r36_missing_file_is_404(tmp_path):
    """없는 파일은 404 다 — 빈 200 을 주면 모델에 빈 이미지가 간다."""
    _write(tmp_path, "meal.jpg", _jpeg())

    with serve_fixtures(tmp_path) as base:
        response = _get(f"{base}/nope.jpg")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "raw_path",
    [
        "/../secret.jpg",
        "/%2e%2e/secret.jpg",
        "/%2E%2E/secret.jpg",
        "/..%2fsecret.jpg",
        "/%2e%2e%2fsecret.jpg",
        "/..%5csecret.jpg",
        "/..\\secret.jpg",
        "/./../secret.jpg",
        "/../fixtures_x/secret.jpg",
        "/%2e%2e/fixtures_x/secret.jpg",
        "/{abs}",
        "/{abs_enc}",
    ],
    ids=[
        "r37-dotdot",
        "r37-enc-lower",
        "r37-enc-upper",
        "r37-enc-slash",
        "r37-enc-all",
        "r37-enc-backslash",
        "r37-backslash",
        "r37-dot-dotdot",
        "r37-sibling-prefix",
        "r37-sibling-prefix-enc",
        "r37-absolute",
        "r37-absolute-enc",
    ],
)
def test_r37_does_not_serve_files_outside_directory(tmp_path, raw_path):
    """fixture 디렉터리 밖의 파일은 어떤 경로 표기로도 내주지 않는다 (200 아님 · 본문에 비밀 없음).

    서버는 eval 동안 열려 있다 — 상위 폴더(저장소 · 홈)의 파일이 경로 조작으로 새면 안 된다.
    비밀 파일은 진짜 JPEG(`secret.jpg`)이라 확장자 · 시그니처 필터로는 막히지 않는다. 이름이 서빙 폴더로
    시작하는 형제 폴더(`fixtures_x`)는 문자열 접두어 비교를, 절대경로(`/C:/…` 와 그 퍼센트 인코딩)는
    `directory / 경로` 결합이 절대경로로 바뀌는 우회를 잡는다.
    """
    secret = _write(tmp_path, "secret.jpg", _secret_jpeg())
    sibling = tmp_path / "fixtures_x"
    sibling.mkdir()
    _write(sibling, "secret.jpg", _secret_jpeg())
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write(fixtures, "meal.jpg", _jpeg())
    raw_path = raw_path.format(abs=secret.as_posix(), abs_enc=quote(secret.as_posix(), safe=""))

    with serve_fixtures(fixtures) as base:
        status, body = _raw_get(base, raw_path)

    assert status != 200
    assert _SECRET not in body


def test_r38_connection_refused_after_with_exits(tmp_path):
    """`with` 를 나오면 서버가 닫혀 같은 주소로의 연결이 거부된다 — eval 이 끝난 뒤 포트가 남지 않는다."""
    _write(tmp_path, "meal.jpg", _jpeg())

    with serve_fixtures(tmp_path) as base:
        assert _get(f"{base}/meal.jpg").status_code == 200

    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        _get(f"{base}/meal.jpg", timeout=_CLOSED_TIMEOUT)


class _Boom(Exception):
    """블록 안에서 던지는 테스트용 예외."""


def test_r39_server_closed_even_when_block_raises(tmp_path):
    """블록 안에서 예외가 나도 서버가 닫히고, 예외는 그대로 밖으로 나간다.

    eval 이 중간에 죽어도(Ctrl+C · 하네스 버그) 포트가 남지 않아야 다음 실행이 깨끗하다.
    """
    _write(tmp_path, "meal.jpg", _jpeg())
    seen: list[str] = []

    with pytest.raises(_Boom):
        with serve_fixtures(tmp_path) as base:
            seen.append(base)
            raise _Boom

    assert seen
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        _get(f"{seen[0]}/meal.jpg", timeout=_CLOSED_TIMEOUT)


# ─────────────────────────── with_image_base ───────────────────────────


class FakePost:
    """`Post` 대역 — 받은 (path, request) 를 기록하고 정해 둔 Observation 을 돌려준다."""

    def __init__(self, outcome: Observation) -> None:
        self._outcome = outcome
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, request: dict) -> Observation:
        self.calls.append((path, request))
        return self._outcome


_BASE = "http://127.0.0.1:54321"


def _request(image_url: str = "meal.jpg") -> dict:
    """`AnalyzeMealRequest` 를 만족하는 요청 JSON — imageUrl 에는 fixture 파일 이름만 둔다."""
    return {
        "mealId": "eval-fixture-1",
        "mealType": "LUNCH",
        "eatenAt": "2026-10-29T12:30:00+09:00",
        "stage": "MAINTENANCE",
        "imageUrl": image_url,
    }


def test_r40_with_image_base_rewrites_only_image_url():
    """`with_image_base` 는 imageUrl 만 `<base>/<파일 이름>` 으로 바꾸고 나머지 필드 · path · 응답은 그대로 둔다."""
    outcome = Observation(status_code=200, payload={"items": []})
    fake = FakePost(outcome)
    request = _request()

    result = with_image_base(fake, _BASE)("/analyze-meal", request)

    assert result is outcome
    assert len(fake.calls) == 1
    path, sent = fake.calls[0]
    assert path == "/analyze-meal"
    assert sent == {**request, "imageUrl": f"{_BASE}/meal.jpg"}


def test_r41_with_image_base_does_not_mutate_request():
    """원래 요청 dict 는 바뀌지 않는다 — 같은 Case.request 로 N회 돌 때 주소가 겹쳐 붙으면 안 된다."""
    fake = FakePost(Observation(status_code=200, payload={"items": []}))
    request = _request()
    snapshot = copy.deepcopy(request)
    post = with_image_base(fake, _BASE)

    post("/analyze-meal", request)
    post("/analyze-meal", request)

    assert request == snapshot
    assert [sent["imageUrl"] for _, sent in fake.calls] == [f"{_BASE}/meal.jpg"] * 2


# ─────────────────────────── 통합 (loopback, 모델 POST 만 Fake) ───────────────────────────

_MODEL_REPLY = {"choices": [{"message": {"content": '{"items": []}'}}]}


@pytest.fixture
def model_posts(monkeypatch) -> list[tuple[str, dict]]:
    """설정을 가짜로 바꾸고 모델 POST(`httpx.AsyncClient.post`)만 Fake 로 둔다 — 호출을 기록한다.

    `.env` 를 읽지 않고 게이트웨이에 가지 않는다. 이미지 GET(`AsyncClient.get`)은 실제로 로컬 서버에 간다.
    """
    fake_settings = SimpleNamespace(
        LLM_VISION_URL="https://mlapi.run/fake-vision",
        LLM_TIMEOUT_SEC=5.0,
        LLM_MAX_RETRIES=0,
        LLM_API_KEY=SimpleNamespace(get_secret_value=lambda: "verify-dummy"),
    )
    monkeypatch.setattr("app.food_analyzer.get_settings", lambda: fake_settings)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    calls: list[tuple[str, dict]] = []

    async def _fake_post(self, url, **kwargs):
        calls.append((str(url), kwargs))
        return httpx.Response(200, json=_MODEL_REPLY, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    return calls


def test_r42_integration_sends_fixture_bytes_as_data_uri(tmp_path, model_posts):
    """default_post + imageUrl 치환 + 로컬 서버를 엮으면 200 이고, 모델에 간 data URI 가 파일 바이트의 base64 다. POST 는 1회.

    eval 이 `ai/app` 수정 없이 실제 다운로드 경로(content-type → data URI)를 거친다는 것을 고정한다.
    """
    data = _jpeg()
    _write(tmp_path, "meal.jpg", data)
    request = _request("meal.jpg")
    AnalyzeMealRequest.model_validate(request)

    with serve_fixtures(tmp_path) as base:
        observation = with_image_base(default_post(), base)("/analyze-meal", request)

    assert observation.status_code == 200
    assert len(model_posts) == 1
    _, kwargs = model_posts[0]
    image_block = kwargs["json"]["messages"][0]["content"][0]
    assert image_block["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def test_r43_integration_missing_fixture_is_500_without_model_call(tmp_path, model_posts):
    """없는 fixture 이름이면 Observation 이 500 이고(하네스 크래시 아님), 모델 POST 는 0회다.

    사진이 빠진 케이스가 빈 이미지로 모델을 불러 크레딧을 쓰면 안 된다 — 계약 실패로 판정된다.
    """
    _write(tmp_path, "meal.jpg", _jpeg())
    request = _request("missing.jpg")
    AnalyzeMealRequest.model_validate(request)

    with serve_fixtures(tmp_path) as base:
        observation = with_image_base(default_post(), base)("/analyze-meal", request)

    assert observation.status_code == 500
    assert model_posts == []


# ─────────────────────────── fixture_problems ───────────────────────────


@pytest.mark.parametrize(
    ("name", "data"),
    [("meal.jpg", _jpeg()), ("meal.jpeg", _jpeg()), ("meal.png", _png())],
    ids=["r43a-jpg", "r43a-jpeg", "r43a-png"],
)
def test_r43a_small_image_without_exif_has_no_problem(tmp_path, name, data):
    """EXIF 없는 작은 jpg · jpeg · png 는 문제가 없다 — 정상 fixture 를 막으면 팀원이 사진을 못 올린다."""
    path = _write(tmp_path, name, data)

    assert fixture_problems(path) == []


@pytest.mark.parametrize("placement", ["first", "after_app0"], ids=["r43b-app1-first", "r43b-app1-after-app0"])
def test_r43b_jpeg_with_exif_app1_is_problem(tmp_path, placement):
    """JPEG 에 APP1 `Exif\\0\\0` 블록이 있으면 문제다 — 촬영 위치 · 기기 정보가 저장소에 커밋된다."""
    path = _write(tmp_path, "meal.jpg", _jpeg(exif=placement))

    _assert_problem(fixture_problems(path), "meal.jpg")


@pytest.mark.parametrize("placement", ["before_idat", "after_idat"], ids=["r43c-before-idat", "r43c-after-idat"])
def test_r43c_png_with_exif_chunk_is_problem(tmp_path, placement):
    """PNG 에 `eXIf` 청크가 있으면(IDAT 앞이든 뒤든) 문제다."""
    path = _write(tmp_path, "meal.png", _png(exif=placement))

    _assert_problem(fixture_problems(path), "meal.png")


_SMALL_MAX = 64


def test_r43d_exactly_max_bytes_has_no_problem(tmp_path):
    """크기가 정확히 `max_bytes` 면 문제가 없다 (상한 포함)."""
    path = _write(tmp_path, "meal.jpg", _jpeg_of_size(_SMALL_MAX))

    assert fixture_problems(path, max_bytes=_SMALL_MAX) == []


def test_r43d_over_max_bytes_is_problem(tmp_path):
    """크기가 `max_bytes` 를 1바이트라도 넘으면 문제다 — 저장소에 큰 사진이 쌓이지 않게 한다."""
    path = _write(tmp_path, "meal.jpg", _jpeg_of_size(_SMALL_MAX + 1))

    _assert_problem(fixture_problems(path, max_bytes=_SMALL_MAX), "meal.jpg")


@pytest.mark.parametrize(
    "name",
    ["meal.heic", "meal.gif", "meal.webp", "meal", "meal.JPG"],
    ids=["r43e-heic", "r43e-gif", "r43e-webp", "r43e-no-ext", "r43e-upper-JPG"],
)
def test_r43e_extension_not_jpg_or_png_is_problem(tmp_path, name):
    """확장자가 소문자 jpg · jpeg · png 가 아니면 문제다 — 내용이 JPEG 이어도 그렇다(서버 content-type 이 확장자로 정해진다).

    대문자 `.JPG` 도 문제다. 확장자는 소문자만 허용해 fixture 검사와 서버 content-type 표의 규칙을 하나로 둔다.
    """
    path = _write(tmp_path, name, _jpeg())

    _assert_problem(fixture_problems(path), name)


def test_r43f_name_starting_with_test_is_problem(tmp_path):
    """파일 이름이 `test_` 로 시작하면 문제다.

    `ai/evals/` 안에는 `test_*` 를 두지 않는다는 규칙을 따른다(테스트 파일로 분류돼 단계 잠금이 꼬인다).
    """
    path = _write(tmp_path, "test_meal.jpg", _jpeg())

    _assert_problem(fixture_problems(path), "test_meal.jpg")


@pytest.mark.parametrize("name", ["latest_meal.jpg", "my_test.jpg"], ids=["r43f-keep-latest", "r43f-keep-my-test"])
def test_r43f_name_containing_test_elsewhere_has_no_problem(tmp_path, name):
    """`test` 가 이름 가운데 · 끝에 있는 정상 JPEG 은 문제가 없다 — 첫머리 `test_` 만 거른다(과잉 거름 방지)."""
    path = _write(tmp_path, name, _jpeg())

    assert fixture_problems(path) == []


@pytest.mark.parametrize(
    ("name", "data"),
    [("meal.jpg", _png()), ("meal.png", _jpeg()), ("meal.jpg", b"not an image at all")],
    ids=["r43g-jpg-with-png", "r43g-png-with-jpeg", "r43g-jpg-garbage"],
)
def test_r43g_signature_mismatching_extension_is_problem(tmp_path, name, data):
    """확장자와 실제 시그니처(JPEG `FFD8` · PNG `89 PNG`)가 다르면 문제다 — 서버가 틀린 content-type 을 준다."""
    path = _write(tmp_path, name, data)

    _assert_problem(fixture_problems(path), name)


def test_r43h_missing_file_is_problem(tmp_path):
    """파일이 없으면 예외 대신 문제로 보고한다 — 라벨만 있고 사진이 빠진 경우를 목록으로 모아 보여 준다."""
    _assert_problem(fixture_problems(tmp_path / "ghost.jpg"), "ghost.jpg")
