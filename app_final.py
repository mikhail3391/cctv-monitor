"""CCTV Monitor — учёт камер видеонаблюдения. Небольшой сервис на FastAPI.

Основа: учебный app_final.py (занятие 04 «FastAPI: реализуем приложение вместе»).
"""

import datetime
import itertools
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
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


# Типы событий. motion и tamper присылает пользователь (или система детекции),
# offline и online сервис записывает сам, когда меняется статус камеры.
EventType = Literal["motion", "tamper", "offline", "online"]
ManualEventType = Literal["motion", "tamper"]


class EventIn(BaseModel):
    """Событие, которое присылает пользователь."""

    type: ManualEventType
    note: Optional[str] = Field(default=None, max_length=200, examples=["Движение у двери"])
    occurred_at: Optional[datetime.datetime] = Field(
        default=None, description="Время события. Если не указано — сейчас (UTC)."
    )


class Event(BaseModel):
    """Записанное событие камеры."""

    id: int
    camera_id: int
    type: EventType
    note: Optional[str] = None
    occurred_at: datetime.datetime


# Хранилище в памяти: для учебного проекта база данных не нужна.
CAMERAS: dict[int, Camera] = {}
EVENTS: list[Event] = []
_camera_ids = itertools.count(1)
_event_ids = itertools.count(1)


def reset_storage() -> None:
    """Очищает все данные и сбрасывает нумерацию (нужно тестам)."""
    global _camera_ids, _event_ids
    CAMERAS.clear()
    EVENTS.clear()
    _camera_ids = itertools.count(1)
    _event_ids = itertools.count(1)


def find_camera(camera_id: int) -> Camera:
    """Возвращает камеру или отвечает 404."""
    camera = CAMERAS.get(camera_id)
    if camera is None:
        logger.warning("Камера id=%s не найдена", camera_id)
        raise HTTPException(status_code=404, detail=f"Камера id={camera_id} не найдена")
    return camera


def record_event(
    camera: Camera,
    event_type: EventType,
    note: Optional[str] = None,
    occurred_at: Optional[datetime.datetime] = None,
) -> Event:
    """Сохраняет событие камеры и пишет его в лог."""
    if occurred_at is not None and occurred_at.tzinfo is None:
        # Время без часового пояса считаем UTC, чтобы события можно было сравнивать.
        occurred_at = occurred_at.replace(tzinfo=datetime.timezone.utc)
    event = Event(
        id=next(_event_ids),
        camera_id=camera.id,
        type=event_type,
        note=note,
        occurred_at=occurred_at or datetime.datetime.now(datetime.timezone.utc),
    )
    EVENTS.append(event)
    if event_type == "tamper":
        logger.warning(
            "Возможное вмешательство: камера id=%s %r (%s)",
            camera.id,
            camera.name,
            camera.location,
        )
    logger.info("Событие id=%s: камера id=%s, тип=%s", event.id, camera.id, event.type)
    return event


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
    record_event(camera, camera.status)
    return camera


# ---------------------------------------------------------------------------
# События и сводка
# ---------------------------------------------------------------------------


@app.post("/cameras/{camera_id}/events", response_model=Event, status_code=201)
def create_event(camera_id: int, data: EventIn):
    """Записывает событие камеры: движение (motion) или вмешательство (tamper)."""
    camera = find_camera(camera_id)
    return record_event(camera, data.type, data.note, data.occurred_at)


@app.get("/events", response_model=list[Event])
def list_events(
    camera_id: Optional[int] = None,
    event_type: Optional[EventType] = Query(default=None, alias="type"),
    limit: int = Query(50, ge=1, le=500, description="Сколько последних событий вернуть"),
):
    """События от новых к старым. Можно отфильтровать по камере и по типу."""
    items = EVENTS
    if camera_id is not None:
        items = [e for e in items if e.camera_id == camera_id]
    if event_type:
        items = [e for e in items if e.type == event_type]
    return sorted(items, key=lambda e: (e.occurred_at, e.id), reverse=True)[:limit]


@app.get("/summary")
def summary():
    """Общая картина: сколько камер работает и какие события были."""
    cameras = list(CAMERAS.values())
    offline = [c.name for c in cameras if c.status == "offline"]
    by_type: dict[str, int] = {}
    for event in EVENTS:
        by_type[event.type] = by_type.get(event.type, 0) + 1
    return {
        "cameras_total": len(cameras),
        "cameras_online": len(cameras) - len(offline),
        "cameras_offline": len(offline),
        "offline_names": offline,
        "events_total": len(EVENTS),
        "events_by_type": by_type,
    }
