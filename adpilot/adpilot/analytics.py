"""Временные ряды для раздела «Аналитика».

Операционная загрузка — из РЕАЛЬНОГО файла курьеров (data/couriers_by_day.csv).
KPI кампаний — синтетический дневной ряд (данные площадок пока мок), масштабируется
под суммарный бюджет подключённых кампаний, с недельной сезонностью и лёгким трендом.
Ряд детерминирован по дате — чтобы графики не «прыгали» на каждом автообновлении.
"""
from __future__ import annotations

import csv
import os

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "couriers_by_day.csv")


def _seeded(date: str, salt: int) -> float:
    """Детерминированный псевдослучай 0..1 из строки даты (без глобального random)."""
    h = 2166136261
    for ch in f"{date}:{salt}":
        h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
    return (h % 10000) / 10000.0


def courier_series() -> list[dict]:
    out: list[dict] = []
    try:
        with open(os.path.normpath(DATA), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = row.get("Дата регистрации")
                try:
                    per = float(row.get("Среднее заказов на курьера") or 0)
                    orders = int(row.get("Выполнено заказов (всего)") or 0)
                    couriers = int(row.get("Курьеров с заказами") or 0)
                except ValueError:
                    continue
                if d:
                    out.append({"date": d, "per_courier": round(per, 1),
                                "orders": orders, "couriers": couriers})
    except FileNotFoundError:
        pass
    return out


def campaign_series(store, n_days: int = 35) -> list[dict]:
    dates = [r["date"] for r in courier_series()][-n_days:]
    # базовый дневной масштаб из бюджета активных кампаний
    with store.lock():
        base = sum(c.daily_budget for c in store.campaigns.values()) or 180000
    out: list[dict] = []
    for i, d in enumerate(dates):
        weekday = i % 7
        season = 1.15 if weekday in (4, 5) else (0.85 if weekday == 0 else 1.0)  # пт/сб выше
        trend = 0.8 + 0.4 * (i / max(1, len(dates) - 1))                          # плавный рост
        noise = 0.85 + 0.3 * _seeded(d, 1)
        spend = base * 0.45 * season * trend * noise
        cpa = 780 * (0.9 + 0.3 * _seeded(d, 2)) / (0.9 + 0.2 * (trend - 0.8))
        orders = spend / cpa if cpa else 0
        aov = 4300 * (0.95 + 0.1 * _seeded(d, 3))
        roas = (orders * aov) / spend if spend else 0
        out.append({"date": d, "spend": round(spend), "orders": round(orders),
                    "cpa": round(cpa), "roas": round(roas, 2)})
    return out


def analytics_payload(store) -> dict:
    cs = campaign_series(store)
    courier = {r["date"]: r["per_courier"] for r in courier_series()}
    return {
        "labels": [r["date"] for r in cs],
        "spend": [r["spend"] for r in cs],
        "orders": [r["orders"] for r in cs],
        "cpa": [r["cpa"] for r in cs],
        "roas": [r["roas"] for r in cs],
        "courier": [courier.get(r["date"], 0) for r in cs],
    }
