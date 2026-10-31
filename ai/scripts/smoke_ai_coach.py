"""합성 식사 기록으로 실제 /chat 호출. ai/에서 실행한다.

python scripts/smoke_ai_coach.py
python scripts/smoke_ai_coach.py '편의점에서 살 수 있는 것으로 추천해줘'
실제 모델 사용 비용이 발생한다. 키·URL·원시 게이트웨이 오류는 출력하지 않는다.
"""
import json
from pathlib import Path
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app

request = {
    "userId": "smoke-user", "conversationId": "smoke-conversation",
    "text": sys.argv[1] if len(sys.argv) > 1 else "시금치 대신 먹을 수 있는 반찬 두 가지 추천해줘",
    "history": [],
    "context": {
        "stage": "MAINTENANCE", "medication": None,
        "today": {"date": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(), "meals": [
            {"mealType": "LUNCH", "foods": ["참치김밥", "삶은 계란"],
             "qqs": {"quantity": 72.5, "quality": 60, "satiety": None}}]},
        "baseMeal": {"mealId": "smoke-meal", "mealType": "LUNCH",
                     "qqs": {"quantity": 72.5, "quality": 60, "satiety": None},
                     "suggestions": [{"foodName": "시금치나물", "advice": "채소를 곁들여요"}]},
        "longTerm": None,
    },
}
if __name__ == "__main__":
    start = time.monotonic()
    try:
        with TestClient(app) as client:
            response = client.post("/chat", json=request)
        print(json.dumps({"httpStatus": response.status_code,
                          "elapsedSeconds": round(time.monotonic() - start, 2),
                          "response": response.json()}, ensure_ascii=False, indent=2))
        sys.exit(0 if response.status_code == 200 else 1)
    except Exception:
        print("Smoke test failed; sensitive exception details suppressed.")
        sys.exit(1)
