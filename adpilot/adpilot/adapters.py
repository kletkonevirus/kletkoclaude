"""Коннекторы к рекламным площадкам.

ГЛАВНАЯ ИДЕЯ: движок автоправил работает только через этот интерфейс и НЕ знает,
Meta это, Google или мок. Чтобы подключить реальный кабинет — пишешь новый класс,
реализующий PlatformAdapter, и регистрируешь его. Код движка/сигналов не меняется.

Реальные адаптеры (позже) оборачивают:
  Meta   -> Marketing API (Graph)          — требует App Review для write-доступа
  Google -> Google Ads API                 — требует developer token (standard access)
  TikTok -> TikTok Business/Marketing API   — требует одобрения приложения
Их можно подключать как напрямую по REST, так и через MCP-сервер площадки — интерфейс тот же.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod

from .models import Account, Campaign, EntityStatus, Metrics, Platform

CPM = {"meta": 2600, "google": 3200, "tiktok": 2100}   # тенге за 1000 показов


class PlatformAdapter(ABC):
    """Единый контракт. Всё, что движку нужно уметь делать с площадкой."""
    platform: Platform

    @abstractmethod
    def create_campaigns(self, account: Account) -> list[Campaign]:
        """Подтянуть кампании подключённого кабинета (в моке — сгенерировать)."""

    @abstractmethod
    def refresh_metrics(self, campaign: Campaign) -> Metrics:
        """Вернуть метрики за ОДНО последнее окно (дельту), не накопленные."""

    @abstractmethod
    def set_status(self, campaign: Campaign, status: EntityStatus) -> None: ...

    @abstractmethod
    def set_budget(self, campaign: Campaign, budget: float) -> None: ...

    @abstractmethod
    def upload_creative(self, campaign: Campaign, path: str, meta: dict) -> str:
        """Залить креатив, вернуть id ассета. В моке — просто фиксируем факт."""


class MockAdapter(PlatformAdapter):
    """Генерирует правдоподобную динамику, чтобы прототип работал сегодня — до доступов к API.

    Юнит-экономика привязана к таргету кампании: у каждой кампании своя «эффективность»
    (истинный CPA как доля от target_cpa). Так среди 9 кампаний есть победители (ROAS выше
    таргета → кандидаты на разгон), середняки и лузеры (CPA выше стоп-лосса → авто-пауза).
    Изредка вбрасывает аномалию (всплеск расхода / обвал конверсии) для демо детектора.
    """
    CITIES = ["Almaty", "Astana", "Shymkent"]
    WINDOWS_PER_DAY = 96  # 15-минутные окна

    def __init__(self, platform: Platform, seed: int | None = None):
        self.platform = platform
        self.rng = random.Random(seed)

    def create_campaigns(self, account: Account) -> list[Campaign]:
        base_cpa = {"meta": 900, "google": 750, "tiktok": 1100}[self.platform.value]
        # разброс эффективности: победитель / норма / лузер
        eff_pool = [0.62, 0.85, 1.05, 1.35, 1.95]
        out: list[Campaign] = []
        for city in self.CITIES:
            target_cpa = base_cpa * self.rng.uniform(0.9, 1.1)
            target_roas = self.rng.uniform(2.2, 3.0)
            c = Campaign(
                platform=self.platform,
                name=f"{self.platform.value.title()} · {city} · Prospecting",
                city=city,
                daily_budget=self.rng.choice([40000, 60000, 80000]),
                target_cpa=target_cpa,
                target_roas=target_roas,
                account_id=account.id,
            )
            # эффективность и AOV прячем в служебные поля кампании
            c.__dict__["_eff"] = self.rng.choice(eff_pool)
            # AOV подобран так, что при истинном CPA == target ROAS ≈ target
            c.__dict__["_aov"] = target_cpa * target_roas
            # прогрев: заполним историю и накопленные метрики, чтобы витрина не была пустой
            for _ in range(8):
                d = self._window(c)
                c.history.append(d)
                _accumulate(c.metrics, d)
            out.append(c)
        return out

    def refresh_metrics(self, campaign: Campaign) -> Metrics:
        return self._window(campaign)

    def _window(self, c: Campaign) -> Metrics:
        if c.status == EntityStatus.PAUSED:
            return Metrics()
        eff = c.__dict__.get("_eff", 1.0)
        aov = c.__dict__.get("_aov", 4000.0)
        window_budget = c.effective_budget / self.WINDOWS_PER_DAY
        spend = max(0.0, window_budget * self.rng.uniform(0.75, 1.15))

        true_cpa = c.target_cpa * eff * self.rng.uniform(0.9, 1.1)
        orders = spend / true_cpa if true_cpa else 0.0
        revenue = orders * aov * self.rng.uniform(0.9, 1.1)

        # вспомогательные (для витрины) показы/клики
        impressions = int(spend / CPM[c.platform.value] * 1000)
        cr = 0.07
        clicks = int(orders / cr) if orders else int(impressions * 0.012)

        # редкая аномалия
        if self.rng.random() < 0.05:
            if self.rng.random() < 0.5:      # всплеск расхода
                spend *= 3.0
                orders = spend / true_cpa
                revenue = orders * aov
                impressions = int(spend / CPM[c.platform.value] * 1000)
                clicks = int(orders / cr) if orders else clicks
            else:                             # обвал конверсии (лендинг/оплата)
                orders *= 0.1
                revenue = orders * aov

        return Metrics(spend=round(spend), impressions=impressions, clicks=clicks,
                       orders=round(orders), revenue=round(revenue))

    def set_status(self, campaign: Campaign, status: EntityStatus) -> None:
        campaign.status = status  # реальный адаптер: PATCH статуса кампании

    def set_budget(self, campaign: Campaign, budget: float) -> None:
        campaign.daily_budget = budget  # реальный адаптер: PATCH дневного бюджета

    def upload_creative(self, campaign: Campaign, path: str, meta: dict) -> str:
        return f"mock_asset_{abs(hash(path)) % 10_000}"


def _accumulate(total: Metrics, d: Metrics) -> None:
    total.spend += d.spend
    total.impressions += d.impressions
    total.clicks += d.clicks
    total.orders += d.orders
    total.revenue += d.revenue
