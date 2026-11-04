"""eval 사진 fixture 전달 — 이미지 GET 만 로컬 HTTP 서버로 대체하고 모델 POST 는 실제로 둔다.

`app.food_analyzer` 는 `imageUrl` 을 httpx 로 직접 GET 해 data URI 로 바꾼다. fixture 디렉터리를
127.0.0.1 임의 포트로 띄우고 그 주소를 `imageUrl` 에 붙이면 `ai/app` 수정 · monkeypatch 없이
실제 다운로드 경로(content-type → data URI)까지 거친다.

CASES 의 `imageUrl` 에는 fixture **파일 이름만** 두고, 실행 시 `with_image_base` 가 서버 주소를 붙인다.
서버 수명은 eval `main()` 이 `with serve_fixtures(...)` 로 `cli_main` 전체를 감싸 정한다.
harness.py 는 chat · DAILY 와 공유하는 파일이라 이 helper 를 따로 둔다.

서버는 fixture 디렉터리 안의 jpg · jpeg · png 파일만 내준다. 디렉터리 밖 경로 · 디렉터리 목록 ·
표에 없는 확장자는 모두 404 다. 저장소에 커밋할 사진은 `fixture_problems` 로 미리 검사한다(stdlib 만, Pillow 없음).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePath
from urllib.parse import unquote

from evals.harness import Observation, Post

MAX_FIXTURE_BYTES: int = 200_000
"""fixture 한 장의 크기 상한(바이트). 저장소에 커밋하므로 줄여서 둔다."""

_HOST = "127.0.0.1"
"""바인드 주소. 외부 인터페이스에 열면 eval 동안 사진이 같은 네트워크에 노출된다."""

_CONTENT_TYPES: dict[str, str] = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
"""확장자(소문자만) → content-type. OS mimetypes 레지스트리는 PC 마다 값이 달라(`image/pjpeg` 등) 쓰지 않는다.
`food_analyzer` 가 이 헤더로 data URI 의 media type 을 만든다. `fixture_problems` 의 확장자 검사도 이 표를 따른다."""

_POLL_INTERVAL = 0.05
"""`serve_forever` 가 종료 요청을 확인하는 간격(초). 기본 0.5 면 `with` 를 나올 때마다 그만큼 기다린다."""

_JPEG_SIGNATURE = b"\xff\xd8"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_SIGNATURES: dict[str, bytes] = {"image/jpeg": _JPEG_SIGNATURE, "image/png": _PNG_SIGNATURE}
"""content-type → 파일 첫 바이트. `fixture_problems` 가 확장자와 실제 형식이 같은지 본다."""

_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
"""길이 필드가 없는 JPEG 마커 — TEM · RST0~7."""
_EXIF_HEADER = b"Exif\x00\x00"


# ─────────────────────────── 서버 ───────────────────────────


class _FixtureHandler(BaseHTTPRequestHandler):
    """`root` 아래의 jpg · jpeg · png 파일만 GET 으로 내준다. 그 밖은 모두 404 다.

    HTTP/1.0(기본값)을 유지한다 — 응답마다 연결이 닫혀 `shutdown` 이 keep-alive 연결을 기다리지 않는다.
    """

    def __init__(self, *args, root: Path, **kwargs) -> None:
        self._root = root  # 부모 __init__ 이 요청을 바로 처리하므로 먼저 둔다
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        target = self._resolve(self.path)
        if target is None:
            self.send_error(404)
            return
        try:
            data = target.read_bytes()
        except OSError:  # 해석 뒤 지워졌거나 읽을 수 없다 — 없는 파일과 같게 본다
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES[target.suffix])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _resolve(self, request_path: str) -> Path | None:
        """요청 경로를 `root` 안의 내줄 파일로 해석한다. 내주면 안 되면 None.

        퍼센트 디코드 뒤 `resolve()` 한 결과가 `root` 안에 있는지 `is_relative_to` 로 본다 — 문자열 접두어
        비교는 형제 폴더(`fixtures_x`)를 통과시킨다. `..` · 백슬래시는 해석 단계에서 `root` 밖으로 나가므로
        같은 검사에 걸린다. 절대경로 · 드라이브 문자 · UNC(`\\\\host\\share`)는 디스크를 건드리기 전에 거른다.
        """
        raw = request_path.split("?", 1)[0].split("#", 1)[0]
        relative = unquote(raw).lstrip("/")
        if PurePath(relative).anchor:  # 결합하면 `root` 를 무시하는 경로 — resolve() 가 원격 공유에 닿지 않게
            return None
        try:
            target = (self._root / relative).resolve()
            if not target.is_relative_to(self._root) or target.suffix not in _CONTENT_TYPES:
                return None
            return target if target.is_file() else None  # 디렉터리 목록은 내주지 않는다
        except (OSError, ValueError):  # 해석할 수 없는 경로(NUL 문자 · 잘못된 이름) — 내줄 파일이 없다
            return None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 부모 시그니처를 따른다
        """요청 로그를 끈다 — eval 출력에 요청마다 한 줄씩 섞이지 않게 한다."""


@contextmanager
def serve_fixtures(directory: Path) -> Iterator[str]:
    """`directory` 를 127.0.0.1 임의 포트로 띄우고 base URL 을 내준다. 블록을 벗어나면(예외 포함) 닫는다.

    base URL 은 `http://127.0.0.1:<port>` 이고 끝에 슬래시가 없다. 닫을 때 `shutdown()`(요청 루프 종료)과
    `server_close()`(소켓 해제)를 둘 다 부른다 — 하나만 부르면 루프가 돌거나 포트가 남는다.
    `ThreadingHTTPServer` 의 요청 스레드는 daemon 이라 처리 중인 요청이 종료를 붙잡지 않는다.
    """
    root = Path(directory).resolve()
    server = ThreadingHTTPServer((_HOST, 0), partial(_FixtureHandler, root=root))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": _POLL_INTERVAL}, daemon=True)
    thread.start()
    try:
        yield f"http://{_HOST}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def with_image_base(post: Post, base_url: str) -> Post:
    """요청의 `imageUrl`(파일 이름)에 `base_url` 을 붙여 보내는 Post 를 만든다. 원래 요청 dict 는 바꾸지 않는다.

    같은 `Case.request` 로 N회 돌기 때문에 복사본만 고친다 — 원본을 고치면 회차마다 주소가 겹쳐 붙는다.
    """

    def post_with_base(path: str, request: dict) -> Observation:
        return post(path, {**request, "imageUrl": f"{base_url}/{request['imageUrl']}"})

    return post_with_base


# ─────────────────────────── fixture 검사 ───────────────────────────


def _jpeg_has_exif(data: bytes) -> bool:
    """JPEG 이고, 내용이 `Exif\\0\\0` 로 시작하는 APP1 세그먼트가 있는가. SOS · EOI 에서 멈춘다.

    SOS 뒤는 압축된 영상 데이터라 세그먼트 구조가 아니다. 구조가 깨져 있으면 거기서 멈춘다.
    """
    if not data.startswith(_JPEG_SIGNATURE):
        return False
    i = len(_JPEG_SIGNATURE)
    while i + 1 < len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xFF:  # 채움 바이트 — 다음 바이트가 마커다
            i += 1
        elif marker in _JPEG_STANDALONE:
            i += 2
        elif marker in (0xDA, 0xD9) or i + 4 > len(data):  # SOS · EOI, 또는 길이 필드가 잘렸다
            return False
        else:
            length = int.from_bytes(data[i + 2 : i + 4], "big")  # 길이 필드 2바이트 포함
            if length < 2:
                return False
            if marker == 0xE1 and data.startswith(_EXIF_HEADER, i + 4, i + 2 + length):  # APP1
                return True
            i += 2 + length
    return False


def _png_has_exif(data: bytes) -> bool:
    """PNG 이고, `eXIf` 청크가 있는가. IEND 에서 멈춘다."""
    if not data.startswith(_PNG_SIGNATURE):
        return False
    i = len(_PNG_SIGNATURE)
    while i + 8 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        kind = data[i + 4 : i + 8]
        if kind == b"eXIf":
            return True
        if kind == b"IEND":
            return False
        i += 12 + length  # 길이 4 + 종류 4 + 본문 + CRC 4
    return False


def name_problems(name: str) -> list[str]:
    """fixture 파일 이름의 문제 — `test_` 시작 · 소문자 jpg/jpeg/png 아닌 확장자. 파일을 읽지 않는다.

    이름 규칙의 정본이다 — `fixture_problems` 와 라벨 검사(`analyze_meal_eval.build_cases`)가 함께 쓴다.
    확장자는 서버 content-type 표(`_CONTENT_TYPES`)를 따르므로 소문자만 받는다.
    """
    problems: list[str] = []
    if name.startswith("test_"):
        problems.append(f"{name}: 이름이 test_ 로 시작한다 — ai/evals 안에는 test_* 를 두지 않는다")
    if PurePath(name).suffix not in _CONTENT_TYPES:
        problems.append(f"{name}: 확장자가 소문자 .jpg · .jpeg · .png 가 아니다")
    return problems


def fixture_problems(path: Path, *, max_bytes: int = MAX_FIXTURE_BYTES) -> list[str]:
    """fixture 한 장의 문제 목록 — 없음 · 확장자 · 시그니처 · 크기 · EXIF · `test_` 이름. 문제가 없으면 `[]`.

    문구마다 파일 이름을 넣는다 — 라벨 전체를 검사해 한 번에 모아 보여 주기 위해서다. 파일이 없으면 예외 대신
    그 한 줄만 돌려준다. 이름 규칙은 `name_problems` 를 따른다.
    """
    path = Path(path)
    name = path.name
    if not path.is_file():
        return [f"{name}: 파일이 없다"]

    data = path.read_bytes()
    problems = name_problems(name)
    expected = _CONTENT_TYPES.get(path.suffix)
    if len(data) > max_bytes:
        problems.append(f"{name}: 크기 {len(data)} 바이트가 상한 {max_bytes} 바이트를 넘는다")
    if expected is not None and not data.startswith(_SIGNATURES[expected]):
        problems.append(f"{name}: 시그니처가 확장자({path.suffix})와 다르다")
    # EXIF 는 확장자가 아니라 실제 형식으로 본다 — 확장자가 틀린 사진의 EXIF 도 함께 알린다
    if _jpeg_has_exif(data) or _png_has_exif(data):
        problems.append(f"{name}: EXIF 블록이 있다 — 촬영 위치 · 기기 정보가 저장소에 커밋된다")
    return problems
