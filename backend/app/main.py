from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.response import register_exception_handlers

app = FastAPI(title="GLP-1 Meal Coach API", version="0.1.0")

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
