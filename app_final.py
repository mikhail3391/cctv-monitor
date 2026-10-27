"""CCTV Monitor — учёт камер видеонаблюдения. Небольшой сервис на FastAPI.

Основа: учебный app_final.py (занятие 04 «FastAPI: реализуем приложение вместе»).
"""

import os

from fastapi import FastAPI


app = FastAPI(title="CCTV Monitor")


@app.get("/")
def root():
    return {"message": "Сервис работает"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/hello")
def hello(name: str = "студент"):
    return {"message": f"Привет, {name}!"}


@app.get("/info")
def info():
    app_name = os.getenv("APP_NAME", "Учебный сервис")
    app_author = os.getenv("APP_AUTHOR", "Не указан")

    return {
        "app_name": app_name,
        "author": app_author,
    }
