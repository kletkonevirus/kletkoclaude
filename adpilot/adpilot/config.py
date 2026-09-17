"""Лёгкая загрузка секретов из окружения / файла .env (без зависимостей).

Секреты НИКОГДА не хранятся в коде и не коммитятся. Кладите их в .env (он в .gitignore)
или в системные переменные окружения.
"""
from __future__ import annotations

import os


def load_env(path: str = ".env") -> None:
    """Подхватить KEY=VALUE из .env в окружение (не перезатирая уже заданное)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def meta_config() -> dict | None:
    """Вернуть конфиг Meta, если заданы токен и ad account, иначе None."""
    token = os.environ.get("META_ACCESS_TOKEN")
    acct = os.environ.get("META_AD_ACCOUNT_ID")
    if not token or not acct:
        return None
    return {
        "access_token": token,
        "ad_account_id": acct,
        "version": os.environ.get("META_API_VERSION", "v21.0"),
        "currency_minor": int(os.environ.get("META_CURRENCY_MINOR", "100")),
    }
