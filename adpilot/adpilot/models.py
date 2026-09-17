"""Доменная модель. Единая для всех площадок — адаптеры нормализуют свои сущности сюда."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now() -> float:
    return time.time()


class Platform(str, Enum):
    META = "meta"
    GOOGLE = "google"
    TIKTOK = "tiktok"


class EntityStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass
class Metrics:
    """Снимок метрик за окно. Деньги — в тенге."""
    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    orders: int = 0          # конверсии (заказы)
    revenue: float = 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def cr(self) -> float:
        return self.orders / self.clicks if self.clicks else 0.0

    @property
    def cpa(self) -> float:
        return self.spend / self.orders if self.orders else float("inf")

    @property
    def roas(self) -> float:
        return self.revenue / self.spend if self.spend else 0.0


@dataclass
class Campaign:
    platform: Platform
    name: str
    city: str
    daily_budget: float
    target_cpa: float
    target_roas: float
    account_id: str = ""             # к какому подключённому кабинету относится
    status: EntityStatus = EntityStatus.ACTIVE
    id: str = field(default_factory=lambda: _id("cmp"))
    # runtime-состояние
    metrics: Metrics = field(default_factory=Metrics)
    pace_multiplier: float = 1.0     # доля бюджета, разрешённая операционным пейсингом (0..1)
    last_action: str = ""
    pause_reason: str = ""           # почему стоит на паузе: operational | guardrail | ""
    cooldowns: dict = field(default_factory=dict)          # rule -> tick, до которого правило молчит
    history: list[Metrics] = field(default_factory=list)   # для baseline аномалий

    @property
    def effective_budget(self) -> float:
        return round(self.daily_budget * self.pace_multiplier)


class ActionType(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    THROTTLE = "throttle"          # снизить бюджет (безопасно)
    INCREASE_BUDGET = "increase_budget"  # РИСКОВО — только с подтверждением


# Направление действия относительно расхода денег.
# Безопасное = не увеличивает расход -> исполняется автономно.
# Рисковое = увеличивает расход -> идёт в очередь подтверждений.
SAFE_ACTIONS = {ActionType.PAUSE, ActionType.THROTTLE}
RISKY_ACTIONS = {ActionType.RESUME, ActionType.INCREASE_BUDGET}


@dataclass
class Action:
    campaign_id: str
    type: ActionType
    rule: str
    reason: str
    payload: dict = field(default_factory=dict)   # напр. {"multiplier": 0.5} или {"delta_pct": 20}
    auto: bool = False            # True = исполнять без подтверждения даже если тип «рисковый»
                                  # (напр. возврат к уже одобренному бюджету после операц. паузы)
    cooldown: int = 0             # на сколько тиков «замолчать» это правило для кампании после срабатывания
    id: str = field(default_factory=lambda: _id("act"))
    ts: float = field(default_factory=now)

    @property
    def needs_approval(self) -> bool:
        return self.type in RISKY_ACTIONS and not self.auto


class AlertLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRIT = "crit"


@dataclass
class Alert:
    level: AlertLevel
    source: str
    message: str
    id: str = field(default_factory=lambda: _id("alr"))
    ts: float = field(default_factory=now)


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalRequest:
    action: Action
    campaign_name: str
    state: ApprovalState = ApprovalState.PENDING
    id: str = field(default_factory=lambda: _id("apr"))
    ts: float = field(default_factory=now)


class AccountStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class Account:
    """Подключённый рекламный кабинет. В проде хранит зашифрованные токены/refresh."""
    platform: Platform
    name: str
    external_id: str                 # ID кабинета на стороне площадки
    status: AccountStatus = AccountStatus.CONNECTED
    id: str = field(default_factory=lambda: _id("acc"))
    connected_ts: float = field(default_factory=now)
    note: str = ""                   # напр. «мок» или последняя ошибка авторизации


@dataclass
class Creative:
    campaign_id: str
    name: str
    fmt: str                         # image | video | carousel
    asset_id: str                    # id ассета на стороне площадки
    mime: str = ""                   # mime загруженного превью (если есть картинка)
    id: str = field(default_factory=lambda: _id("cr"))
    ts: float = field(default_factory=now)


@dataclass
class RuleConfig:
    """Настройка автоправила: вкл/выкл + пороги. Меняется из интерфейса на лету."""
    name: str                        # совпадает с именем функции-правила
    title: str
    description: str
    auto: bool                       # True = исполняется автоматически, False = только предложение
    enabled: bool = True
    params: dict = field(default_factory=dict)
