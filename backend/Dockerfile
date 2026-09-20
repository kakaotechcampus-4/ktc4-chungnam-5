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

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
