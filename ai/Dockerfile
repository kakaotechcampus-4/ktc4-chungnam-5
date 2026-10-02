# AI Service — FastAPI 컨테이너 1개 (D4). BE 와는 HTTP 로만 대화한다.
#
#   docker compose -f ../infra/docker-compose.ai.yml up -d --build
#
# 앱이 지켜야 하는 것 (compose 의 healthcheck · BE 워커가 기대한다):
#   - 진입점은 app/main.py 의 `app`
#   - GET /health 가 200 을 돌려준다 (ai-stub 과 같다)
#   - 8000 에서 듣는다. 호스트 포트는 compose 가 정한다
#
# 비밀(LLM_API_KEY 등)은 이미지에 굽지 않는다. .dockerignore 가 .env 를 빼고,
# 값은 compose 가 실행 시점에 env_file 로 넣는다 — 이 repo 는 public 이다.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# README 의 디렉터리 구조 그대로. /srv 가 import 루트라 `from agents import ...` 가 된다.
COPY app/ ./app/
COPY agents/ ./agents/
COPY tools/ ./tools/
COPY guardrail/ ./guardrail/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
