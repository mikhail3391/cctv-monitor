"""CCTV Monitor — учёт камер видеонаблюдения. Небольшой сервис на FastAPI.

Основа: учебный app_final.py (занятие 04 «FastAPI: реализуем приложение вместе»).
"""

import os
import time
from dataclasses import dataclass

from fastapi import FastAPI

APP_VERSION = "1.0.0"
STARTED_AT = time.time()


@dataclass(frozen=True)
class Settings:
    """Настройки сервиса. Берутся из переменных окружения."""

    app_name: str
    author: str


def get_settings() -> Settings:
    """Читает переменные окружения при каждом вызове (удобно для тестов)."""
    return Settings(
        app_name=os.getenv("APP_NAME", "CCTV Monitor"),
        author=os.getenv("APP_AUTHOR", "Не указан"),
    )


app = FastAPI(title="CCTV Monitor", version=APP_VERSION)


@app.get("/")
def root():
    settings = get_settings()
    return {"message": f"{settings.app_name}: сервис работает", "docs": "/docs"}


@app.get("/health")
def health():
    """Проверка работоспособности: если вернулся ok — сервис жив."""
    return {"status": "ok", "uptime_seconds": round(time.time() - STARTED_AT)}


@app.get("/version")
def version():
    settings = get_settings()
    return {
        "app": settings.app_name,
        "version": APP_VERSION,
        "author": settings.author,
    }
