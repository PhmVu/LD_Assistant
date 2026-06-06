from __future__ import annotations

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from routes import auth, chat, docs, draw, qa, dashboard


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="LD backend for chat AI, docs ingestion, and drawing guidance",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(docs.router)
app.include_router(draw.router)
app.include_router(qa.router)
app.include_router(dashboard.router)


@app.get("/api/ld/health")
async def health():
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
    }
