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
    retention_days: int


DEFAULT_RETENTION_DAYS = 14
MAX_RETENTION_DAYS = 3650


def parse_retention(raw: str) -> Optional[int]:
    """Превращает строку в число дней хранения. Если значение неверное — None."""
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= MAX_RETENTION_DAYS else None


def read_retention() -> int:
    """Читает RETENTION_DAYS. Если не задан или неверный — берёт значение по умолчанию."""
    raw = os.getenv("RETENTION_DAYS")
    parsed = parse_retention(raw) if raw is not None else None
    return parsed if parsed is not None else DEFAULT_RETENTION_DAYS


def check_settings() -> None:
    """При запуске предупреждает в логе о неверных значениях настроек."""
    raw = os.getenv("RETENTION_DAYS")
    if raw is not None and parse_retention(raw) is None:
        logger.warning(
            "Неверный RETENTION_DAYS=%r (нужно целое число от 1 до %d), использую %d",
            raw,
            MAX_RETENTION_DAYS,
            DEFAULT_RETENTION_DAYS,
        )


def get_settings() -> Settings:
    """Читает переменные окружения при каждом вызове (удобно для тестов)."""
    return Settings(
        app_name=os.getenv("APP_NAME", "CCTV Monitor"),
        author=os.getenv("APP_AUTHOR", "Не указан"),
        retention_days=read_retention(),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_settings()
    settings = get_settings()
    logger.info(
        "Сервис запущен: app=%r version=%s retention_days=%d",
        settings.app_name,
        APP_VERSION,
        settings.retention_days,
    )
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


# ---------------------------------------------------------------------------
# Место под архив записей
# ---------------------------------------------------------------------------

# 1 Мбит/с за сутки = 86400 Мбит = 10 800 МБ = 10,8 ГБ (считаем в десятичных ГБ и ТБ).
GB_PER_DAY_PER_MBPS = 86_400 / 8 / 1000


def calculate_storage(total_mbps: float, days: int, reserve_percent: float) -> dict:
    """Сколько места нужно, чтобы хранить записи указанное число дней."""
    gb_per_day = total_mbps * GB_PER_DAY_PER_MBPS
    raw_gb = gb_per_day * days
    total_gb = raw_gb * (1 + reserve_percent / 100)
    return {
        "total_bitrate_mbps": round(total_mbps, 2),
        "days": days,
        "reserve_percent": reserve_percent,
        "gb_per_day": round(gb_per_day, 2),
        "raw_gb": round(raw_gb, 1),
        "total_gb": round(total_gb, 1),
        "total_tb": round(total_gb / 1000, 2),
    }


@app.get("/storage/estimate")
def estimate_storage(
    cameras: int = Query(ge=1, le=10_000, description="Количество камер"),
    bitrate_mbps: float = Query(gt=0, le=100, description="Битрейт одной камеры, Мбит/с"),
    days: Optional[int] = Query(
        default=None,
        ge=1,
        le=MAX_RETENTION_DAYS,
        description="Сколько дней хранить записи. По умолчанию — RETENTION_DAYS.",
    ),
    reserve_percent: float = Query(20, ge=0, le=100, description="Запас места, %"),
):
    """Калькулятор: сколько места нужно под архив для заданных камер."""
    days = days or get_settings().retention_days
    result = calculate_storage(cameras * bitrate_mbps, days, reserve_percent)
    result["cameras"] = cameras
    logger.info(
        "Расчёт хранилища: %d камер по %.1f Мбит/с, %d дн. -> %.2f ТБ",
        cameras,
        bitrate_mbps,
        days,
        result["total_tb"],
    )
    return result


@app.get("/storage/required")
def required_storage(
    reserve_percent: float = Query(20, ge=0, le=100, description="Запас места, %"),
):
    """Сколько места нужно под архив для всех зарегистрированных камер."""
    days = get_settings().retention_days
    total_mbps = sum((c.bitrate_mbps for c in CAMERAS.values()), 0.0)
    result = calculate_storage(total_mbps, days, reserve_percent)
    result["cameras"] = len(CAMERAS)
    logger.info(
        "Нужно места под %d камер на %d дн.: %.2f ТБ", len(CAMERAS), days, result["total_tb"]
    )
    return result
