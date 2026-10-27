"""CCTV Monitor — учёт камер видеонаблюдения. Небольшой сервис на FastAPI.

Основа: учебный app_final.py (занятие 04 «FastAPI: реализуем приложение вместе»).
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request

APP_VERSION = "1.0.0"
STARTED_AT = time.time()

logger = logging.getLogger("cctv_monitor")


def setup_logging() -> None:
    """Включает логи в консоль. Уровень задаётся переменной LOG_LEVEL."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    is_valid = isinstance(level, int)
    if not is_valid:
        level = logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
    )
    logger.setLevel(level)
    if not is_valid:
        logger.warning("Неизвестный LOG_LEVEL=%r, использую INFO", level_name)


setup_logging()


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logger.info("Сервис запущен: app=%r version=%s", settings.app_name, APP_VERSION)
    yield
    logger.info("Сервис остановлен")


app = FastAPI(title="CCTV Monitor", version=APP_VERSION, lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Пишет в лог каждый запрос: метод, путь, код ответа, время."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "%s %s -> %s (%.1f мс)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


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
