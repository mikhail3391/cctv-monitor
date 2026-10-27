"""CCTV Monitor — учёт камер видеонаблюдения. Небольшой сервис на FastAPI.

Основа: учебный app_final.py (занятие 04 «FastAPI: реализуем приложение вместе»).
"""

import itertools
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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


# ---------------------------------------------------------------------------
# Камеры
# ---------------------------------------------------------------------------

CameraStatus = Literal["online", "offline"]


class CameraIn(BaseModel):
    """Данные, которые присылает пользователь при добавлении камеры."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50, examples=["Вход, главная"])
    location: str = Field(min_length=1, max_length=50, examples=["Холл, 1 этаж"])
    bitrate_mbps: float = Field(
        default=4.0,
        gt=0,
        le=100,
        description="Битрейт видеопотока, Мбит/с (около 4 для Full HD).",
    )


class Camera(BaseModel):
    """Зарегистрированная камера."""

    id: int
    name: str
    location: str
    bitrate_mbps: float
    status: CameraStatus = "online"


class StatusIn(BaseModel):
    status: CameraStatus


# Хранилище в памяти: для учебного проекта база данных не нужна.
CAMERAS: dict[int, Camera] = {}
_camera_ids = itertools.count(1)


def reset_storage() -> None:
    """Очищает все данные и сбрасывает нумерацию (нужно тестам)."""
    global _camera_ids
    CAMERAS.clear()
    _camera_ids = itertools.count(1)


def find_camera(camera_id: int) -> Camera:
    """Возвращает камеру или отвечает 404."""
    camera = CAMERAS.get(camera_id)
    if camera is None:
        logger.warning("Камера id=%s не найдена", camera_id)
        raise HTTPException(status_code=404, detail=f"Камера id={camera_id} не найдена")
    return camera


@app.post("/cameras", response_model=Camera, status_code=201)
def create_camera(data: CameraIn):
    """Регистрирует камеру. Новая камера сразу считается online."""
    camera = Camera(
        id=next(_camera_ids),
        name=data.name,
        location=data.location,
        bitrate_mbps=data.bitrate_mbps,
    )
    CAMERAS[camera.id] = camera
    logger.info(
        "Добавлена камера id=%s %r (%s), %.1f Мбит/с",
        camera.id,
        camera.name,
        camera.location,
        camera.bitrate_mbps,
    )
    return camera


@app.get("/cameras", response_model=list[Camera])
def list_cameras(status: Optional[CameraStatus] = None, location: Optional[str] = None):
    """Список камер. Можно отфильтровать по статусу и по части названия места."""
    items = list(CAMERAS.values())
    if status:
        items = [c for c in items if c.status == status]
    if location:
        part = location.strip().lower()
        items = [c for c in items if part in c.location.lower()]
    return items


@app.get("/cameras/{camera_id}", response_model=Camera)
def get_camera(camera_id: int):
    """Одна камера по идентификатору."""
    return find_camera(camera_id)


@app.patch("/cameras/{camera_id}/status", response_model=Camera)
def set_camera_status(camera_id: int, data: StatusIn):
    """Меняет статус камеры: online или offline."""
    camera = find_camera(camera_id)
    if camera.status == data.status:
        return camera
    camera.status = data.status
    if camera.status == "offline":
        logger.warning(
            "Камера id=%s %r (%s) перешла в offline", camera.id, camera.name, camera.location
        )
    else:
        logger.info("Камера id=%s %r снова online", camera.id, camera.name)
    return camera
