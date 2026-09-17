"""Сигналы: операционная загрузка (курьеры/кухня) и детект аномалий трафика.

Операционный пейсинг — уникальная ценность продукта: расход рекламы гейтится реальной
пропускной способностью доставки. Нет смысла лить трафик в город, где курьеры перегружены,
а кухни в стоп-листах — заказы всё равно не довезём вовремя, деньги в пролёте.
"""
from __future__ import annotations

import csv
import math
import os
import random
import statistics

from .models import Metrics

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# --------------------------------------------------------------------------------------
# Операционный сигнал — capacity в диапазоне 0..1 на город.
#   1.0  = мощностей с запасом, лить можно на полный бюджет
#   <1.0 = перегруз, пейсинг режет бюджет пропорционально
#   ~0   = критический перегруз, кампании ставятся на паузу
# --------------------------------------------------------------------------------------
class OperationsSignal:
    """v1: считает загрузку курьеров.

    Baseline берётся из РЕАЛЬНОГО файла data/couriers_by_day.csv
    ('среднее заказов на курьера' — прокси UTR). Текущее значение по каждому городу
    симулируется вокруг этого baseline, т.к. живого per-city реалтайма пока нет.

    Когда появится реалтайм UTR по городам и оцифрованные стоп-листы кухонь —
    достаточно заменить _current_load() и добавить kitchen_factor(). Контракт не меняется.
    """

    def __init__(self, seed: int | None = 7):
        self.rng = random.Random(seed)
        self.baseline = self._load_baseline()
        # целевая загрузка = историческая медиана + 15% запаса; выше — перегрев
        self.target = self.baseline * 1.15

    def _load_baseline(self) -> float:
        path = os.path.join(DATA_DIR, "couriers_by_day.csv")
        vals: list[float] = []
        try:
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    v = row.get("Среднее заказов на курьера")
                    if v:
                        try:
                            fv = float(v)
                            if fv > 0:
                                vals.append(fv)
                        except ValueError:
                            pass
        except FileNotFoundError:
            pass
        return statistics.median(vals) if vals else 120.0

    def _current_load(self, city: str) -> float:
        """Прокси текущей загрузки курьеров по городу (orders/courier).

        ЗАГЛУШКА реалтайма: дрейф вокруг исторического baseline со сдвигом по городу.
        Almaty намеренно чаще перегружается — чтобы демо пейсинга было видно.
        """
        bias = {"Almaty": 1.55, "Astana": 0.95, "Shymkent": 0.8}.get(city, 1.0)
        return max(0.0, self.rng.gauss(self.baseline * bias, self.baseline * 0.15))

    def capacity(self, city: str) -> float:
        load = self._current_load(city)
        # масштабируемо: при load == target -> 1.0; при 2x target -> ~0
        cap = 1.0 - max(0.0, (load - self.target)) / self.target
        # TODO(kitchen): cap = min(cap, kitchen_factor(city))  когда оцифруют стоп-листы
        return round(max(0.0, min(1.0, cap)), 2)

    def snapshot(self, cities: list[str]) -> dict[str, float]:
        return {c: self.capacity(c) for c in cities}


# --------------------------------------------------------------------------------------
# Детектор аномалий — z-score по скользящему baseline из истории метрик кампании.
# Только СИГНАЛИЗИРУЕТ (и опционально тормозит расход); никогда не разгоняет.
# --------------------------------------------------------------------------------------
class AnomalyDetector:
    def __init__(self, z_threshold: float = 2.5, min_history: int = 6):
        self.z = z_threshold
        self.min_history = min_history

    def _z(self, series: list[float], current: float) -> float:
        if len(series) < self.min_history:
            return 0.0
        mu = statistics.fmean(series)
        sd = statistics.pstdev(series)
        if sd == 0:
            return 0.0
        return (current - mu) / sd

    def check(self, history: list[Metrics], current: Metrics) -> list[tuple[str, str]]:
        """Возвращает список (severity, message)."""
        out: list[tuple[str, str]] = []
        if len(history) < self.min_history:
            return out

        spend_z = self._z([m.spend for m in history], current.spend)
        if spend_z >= self.z:
            out.append(("crit", f"Всплеск расхода: z={spend_z:.1f} "
                                f"(текущий {current.spend:.0f} против baseline)"))

        cr_hist = [m.cr for m in history if m.clicks > 0]
        if current.clicks > 0 and len(cr_hist) >= self.min_history:
            cr_z = self._z(cr_hist, current.cr)
            if cr_z <= -self.z:
                out.append(("crit", f"Обвал конверсии: CR={current.cr:.1%} "
                                    f"(z={cr_z:.1f}) — проверить лендинг/оплату"))

        if current.orders > 0 and math.isfinite(current.cpa):
            cpa_hist = [m.cpa for m in history if m.orders > 0 and math.isfinite(m.cpa)]
            if len(cpa_hist) >= self.min_history:
                cpa_z = self._z(cpa_hist, current.cpa)
                if cpa_z >= self.z:
                    out.append(("warn", f"Рост CPA: {current.cpa:.0f} (z={cpa_z:.1f})"))
        return out
