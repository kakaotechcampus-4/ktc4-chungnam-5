# AI Service — 이미지 두 개를 한 소스에서 빌드한다 (S8).
#
#   docker build --target stub -t glp1-ai-stub .
#   docker build --target real -t glp1-ai      .
#
# 공유되는 것: 라우터 · 계약 스키마 · 가드레일. 계약이 갈라질 수 없다.
# 갈라지는 것: agents/stub/ 와 agents/llm/, 그리고 LLM 의존성.
#
# 스텁 이미지에는 LLM SDK 도 agents/llm/ 도 들어가지 않는다.
# 환경변수를 잘못 줘도 팀 공용 크레딧을 쓸 방법이 물리적으로 없다.

# ─────────────────────────── 공통 ───────────────────────────

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# requirements 를 먼저 복사해 레이어 캐시를 살린다
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 두 이미지가 공유하는 코드
COPY app/ ./app/
COPY guardrail/ ./guardrail/
COPY agents/__init__.py agents/schemas.py agents/runner.py ./agents/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ─────────────────────────── 스텁 ───────────────────────────

FROM base AS stub

COPY agents/stub/ ./agents/stub/

# 이 이미지는 스텁으로만 뜬다. false 로 덮어써도 agents/llm/ 이 없어서 기동 후 실패한다.
ENV STUB_MODE=true \
    STUB_LATENCY_MS=0 \
    STUB_DEFAULT_SCENARIO=SUCCESS

# ─────────────────────────── 실사용 ───────────────────────────

FROM base AS real

# LLM SDK 등 실사용 전용 의존성. 스텁 이미지에는 설치되지 않는다.
COPY requirements-llm.txt ./
RUN pip install --no-cache-dir -r requirements-llm.txt

# agents/llm/ 은 아직 없다. 구현하면 아래 줄의 주석을 푼다.
# 그때까지 이 이미지는 기동은 되고 요청이 오면 NotImplementedError 를 낸다.
# COPY agents/llm/ ./agents/llm/

ENV STUB_MODE=false
