# BE — 이미지 하나로 컨테이너 둘을 띄운다 (D4).
#
#   api    : uvicorn app.main:app          (기본 CMD)
#   worker : python -m app.worker_main     (compose 에서 command 로 덮어쓴다)
#
# 같은 코드를 공유하되 실행 경로가 갈린다. API 는 큐에 넣고 202 를 돌려주고,
# Worker 가 꺼내서 AI 를 부른다 — API 는 AI 를 직접 부르지 않는다.
#
# 끝 단계가 둘이다.
#
#   app     : BE 만. 로컬 BE 개발(infra/docker-compose.be.yml)이 쓴다
#   release : app + 평가용 웹(/srv/web). 운영 · 스테이징(infra/docker-compose.prod.yml)이 쓴다
#
# web 단계는 release 만 쓰므로 app 을 빌드할 때는 Flutter 이미지를 받지도 않는다.
# release 는 compose 의 additional_contexts 로 frontend 폴더를 받는다(이름: frontend).

# ── 평가용 웹 ─────────────────────────────────────────────────
FROM debian:bookworm-slim AS web

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# FE 와 같은 Flutter 버전이어야 한다 — pubspec.lock 의 SDK 고정 패키지가 버전마다 달라
# 다르면 --enforce-lockfile 에서 빌드가 멈춘다. 공식 저장소의 릴리스 태그를 그대로 받는다
# (Flutter 이미지는 새 patch 버전이 늦게 올라와 버전을 맞출 수 없을 때가 있다).
ARG FLUTTER_VERSION=3.44.8
RUN git clone --depth 1 --branch ${FLUTTER_VERSION} https://github.com/flutter/flutter.git /opt/flutter
ENV PATH=/opt/flutter/bin:$PATH
RUN flutter config --no-analytics --no-cli-animations \
    && flutter precache --web

WORKDIR /src

# 의존성 먼저 — FE 코드만 바뀐 배포는 pub get 레이어를 캐시에서 쓴다
COPY --from=frontend pubspec.yaml pubspec.lock ./
RUN flutter pub get --enforce-lockfile

COPY --from=frontend . .
# 데모 키는 넣지 않는다. 웹 파일은 키 없이 내려가므로 넣으면 누구나 꺼낸다 —
# 키는 평가자에게 주는 링크(?k=)에 담긴다 (frontend/README.md "웹").
RUN flutter build web --release --base-href /web/

# ── BE ────────────────────────────────────────────────────────
FROM python:3.12-slim AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
# 운영 compose 의 api 가 기동할 때 alembic upgrade head 를 돌린다
COPY alembic.ini ./
COPY alembic/ ./alembic/

# 어느 버전 · 커밋으로 만든 이미지인지. infra/deploy.sh 가 git 에서 읽어 넘긴다
# (운영: 릴리스 태그 + 전체 SHA, 스테이징: SHA 만). /health 가 ENV 를 그대로 낸다.
# 배포마다 값이 바뀌므로 맨 끝에 둔다 — 앞에 두면 pip install 레이어 캐시가 매번 깨진다.
ARG GIT_SHA=""
ARG APP_VERSION=""
ENV GIT_SHA=$GIT_SHA \
    APP_VERSION=$APP_VERSION
LABEL org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.version=$APP_VERSION

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── 운영 · 스테이징 ───────────────────────────────────────────
# app 을 그대로 잇고 웹만 더한다. GIT_SHA · APP_VERSION · CMD 는 app 에서 물려받는다.
# app.main 이 /srv/web 이 있을 때만 /web 을 mount 한다.
FROM app AS release

COPY --from=web /src/build/web ./web/
