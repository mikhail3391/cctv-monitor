"""Тесты сервиса CCTV Monitor. Запуск: python -m pytest -v"""

import logging

import pytest
from fastapi.testclient import TestClient

import app_final


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Перед каждым тестом: пустое хранилище и настройки по умолчанию."""
    app_final.reset_storage()
    for name in ("APP_NAME", "APP_AUTHOR", "RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    with TestClient(app_final.app) as test_client:
        yield test_client


def add_camera(client, name="Вход", location="Холл, 1 этаж", bitrate_mbps=4):
    payload = {"name": name, "location": location, "bitrate_mbps": bitrate_mbps}
    return client.post("/cameras", json=payload)


# --- Служебные маршруты ----------------------------------------------------


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_version_returns_version_and_settings_from_env(client, monkeypatch):
    monkeypatch.setenv("APP_NAME", "Наши камеры")
    monkeypatch.setenv("APP_AUTHOR", "Тестовый Автор")
    data = client.get("/version").json()
    assert data == {
        "app": "Наши камеры",
        "version": app_final.APP_VERSION,
        "author": "Тестовый Автор",
    }


def test_root_mentions_docs(client):
    assert client.get("/").json()["docs"] == "/docs"


# --- Камеры ----------------------------------------------------------------


def test_create_and_get_camera(client):
    created = add_camera(client, name="  Вход  ", bitrate_mbps=6)
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == 1
    assert body["name"] == "Вход"  # пробелы по краям убраны
    assert body["status"] == "online"  # новая камера сразу online
    assert body["bitrate_mbps"] == 6

    fetched = client.get(f"/cameras/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "location": "Холл"},
        {"name": "   ", "location": "Холл"},
        {"name": "Вход"},
        {"name": "Вход", "location": "Холл", "bitrate_mbps": 0},
        {"name": "Вход", "location": "Холл", "bitrate_mbps": 500},
    ],
)
def test_create_camera_rejects_invalid_data(client, payload):
    assert client.post("/cameras", json=payload).status_code == 422


def test_get_missing_camera_returns_404(client):
    response = client.get("/cameras/999")
    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_list_cameras_filters_by_status_and_location(client):
    add_camera(client, name="Вход", location="Холл, 1 этаж")
    add_camera(client, name="Парковка", location="Двор")
    add_camera(client, name="Склад", location="Холл, подвал")
    client.patch("/cameras/3/status", json={"status": "offline"})

    offline = client.get("/cameras", params={"status": "offline"}).json()
    assert [c["name"] for c in offline] == ["Склад"]

    in_hall = client.get("/cameras", params={"location": "ХОЛЛ"}).json()
    assert [c["name"] for c in in_hall] == ["Вход", "Склад"]

    assert client.get("/cameras", params={"status": "broken"}).status_code == 422


def test_status_change_records_events_once(client):
    add_camera(client)
    assert client.patch("/cameras/1/status", json={"status": "offline"}).json()["status"] == "offline"
    client.patch("/cameras/1/status", json={"status": "offline"})  # повтор — без нового события
    client.patch("/cameras/1/status", json={"status": "online"})

    types = [e["type"] for e in client.get("/events").json()]
    assert sorted(types) == ["offline", "online"]


def test_status_change_rejects_wrong_value_and_missing_camera(client):
    add_camera(client)
    assert client.patch("/cameras/1/status", json={"status": "weird"}).status_code == 422
    assert client.patch("/cameras/99/status", json={"status": "online"}).status_code == 404


# --- События и сводка ------------------------------------------------------


def test_create_event_and_filter(client):
    add_camera(client, name="Вход")
    add_camera(client, name="Парковка")
    client.post("/cameras/1/events", json={"type": "motion", "note": "Кто-то вошёл"})
    client.post("/cameras/2/events", json={"type": "tamper"})
    client.post("/cameras/2/events", json={"type": "motion"})

    all_events = client.get("/events").json()
    assert len(all_events) == 3
    assert all_events[0]["id"] == 3  # сначала самые новые

    only_cam2 = client.get("/events", params={"camera_id": 2}).json()
    assert {e["camera_id"] for e in only_cam2} == {2}

    only_tamper = client.get("/events", params={"type": "tamper"}).json()
    assert [e["camera_id"] for e in only_tamper] == [2]

    assert len(client.get("/events", params={"limit": 1}).json()) == 1


def test_event_errors(client):
    add_camera(client)
    assert client.post("/cameras/99/events", json={"type": "motion"}).status_code == 404
    # offline/online сервис записывает сам, вручную их присылать нельзя
    assert client.post("/cameras/1/events", json={"type": "offline"}).status_code == 422
    assert client.get("/events", params={"limit": 0}).status_code == 422


def test_events_with_and_without_timezone_can_be_sorted(client):
    add_camera(client)
    client.post("/cameras/1/events", json={"type": "motion", "occurred_at": "2026-10-25T09:00:00"})
    client.post(
        "/cameras/1/events", json={"type": "motion", "occurred_at": "2026-10-25T13:00:00+03:00"}
    )
    response = client.get("/events")
    assert response.status_code == 200
    assert [e["id"] for e in response.json()] == [2, 1]  # 13:00+03:00 = 10:00 UTC


def test_summary_counts_cameras_and_events(client):
    add_camera(client, name="Вход")
    add_camera(client, name="Парковка")
    client.patch("/cameras/2/status", json={"status": "offline"})
    client.post("/cameras/1/events", json={"type": "motion"})

    data = client.get("/summary").json()
    assert data["cameras_total"] == 2
    assert data["cameras_online"] == 1
    assert data["cameras_offline"] == 1
    assert data["offline_names"] == ["Парковка"]
    assert data["events_by_type"] == {"offline": 1, "motion": 1}


# --- Место под архив -------------------------------------------------------


def test_storage_estimate_calculates_size(client):
    # 8 камер x 4 Мбит/с = 32 Мбит/с; 32 x 10,8 ГБ = 345,6 ГБ в сутки
    data = client.get(
        "/storage/estimate", params={"cameras": 8, "bitrate_mbps": 4, "days": 14}
    ).json()
    assert data["gb_per_day"] == 345.6
    assert data["raw_gb"] == 4838.4
    assert data["total_tb"] == 5.81  # с запасом 20 %


def test_storage_estimate_uses_retention_days_from_env(client, monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "30")
    data = client.get("/storage/estimate", params={"cameras": 1, "bitrate_mbps": 1}).json()
    assert data["days"] == 30
    assert data["raw_gb"] == 324  # 10,8 ГБ x 30 дней


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"cameras": 0, "bitrate_mbps": 4},
        {"cameras": 1, "bitrate_mbps": 0},
        {"cameras": 1, "bitrate_mbps": 4, "days": 0},
        {"cameras": 1, "bitrate_mbps": 4, "reserve_percent": 150},
    ],
)
def test_storage_estimate_rejects_invalid_parameters(client, params):
    assert client.get("/storage/estimate", params=params).status_code == 422


def test_storage_required_uses_registered_cameras(client):
    add_camera(client, bitrate_mbps=4)
    add_camera(client, name="Парковка", bitrate_mbps=8)
    data = client.get("/storage/required", params={"reserve_percent": 0}).json()
    assert data["cameras"] == 2
    assert data["total_bitrate_mbps"] == 12
    assert data["raw_gb"] == 1814.4  # 12 x 10,8 ГБ x 14 дней


def test_wrong_retention_in_env_falls_back_to_default(client, monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "много")
    data = client.get("/storage/estimate", params={"cameras": 1, "bitrate_mbps": 1}).json()
    assert data["days"] == app_final.DEFAULT_RETENTION_DAYS


# --- Логи ------------------------------------------------------------------


def test_logs_offline_and_tamper_warnings(client, caplog):
    add_camera(client, name="Склад", location="Подвал")
    with caplog.at_level(logging.INFO, logger="cctv_monitor"):
        client.patch("/cameras/1/status", json={"status": "offline"})
        client.post("/cameras/1/events", json={"type": "tamper"})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("перешла в offline" in m and "Склад" in m for m in warnings)
    assert any("Возможное вмешательство" in m for m in warnings)
    messages = [r.getMessage() for r in caplog.records]
    assert any("PATCH /cameras/1/status -> 200" in m for m in messages)


def test_startup_warns_about_wrong_retention(monkeypatch, caplog):
    monkeypatch.setenv("RETENTION_DAYS", "-5")
    with caplog.at_level(logging.INFO, logger="cctv_monitor"):
        with TestClient(app_final.app):
            pass
    messages = [r.getMessage() for r in caplog.records]
    assert any("Неверный RETENTION_DAYS" in m for m in messages)
    assert any("Сервис запущен" in m for m in messages)
