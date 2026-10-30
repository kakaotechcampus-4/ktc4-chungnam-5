# BE — 이미지 하나로 컨테이너 둘을 띄운다 (D4).
#
#   api    : uvicorn app.main:app          (기본 CMD)
#   worker : python -m app.worker_main     (compose 에서 command 로 덮어쓴다)
#
# 같은 코드를 공유하되 실행 경로가 갈린다. API 는 큐에 넣고 202 를 돌려주고,
# Worker 가 꺼내서 AI 를 부른다 — API 는 AI 를 직접 부르지 않는다.

FROM python:3.12-slim

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
