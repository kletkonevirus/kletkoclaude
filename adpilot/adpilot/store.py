"""Потокобезопасное хранилище состояния (in-memory для прототипа).

В проде здесь будет БД + аудит-лог в отдельной таблице. Интерфейс намеренно узкий,
чтобы замена на Postgres/SQLite не задела остальной код.
"""
from __future__ import annotations

import threading
from collections import deque

from .models import (Account, Action, Alert, ApprovalRequest, Campaign,
                     Creative, RuleConfig)


class Store:
    def __init__(self, max_log: int = 500):
        self._lock = threading.RLock()
        self.accounts: dict[str, Account] = {}                     # подключённые кабинеты
        self.campaigns: dict[str, Campaign] = {}
        self.creatives: list[Creative] = []
        self.creative_images: dict[str, tuple[str, bytes]] = {}    # creative_id -> (mime, bytes)
        self.rule_configs: dict[str, RuleConfig] = {}              # имя правила -> настройка
        self.alerts: deque[Alert] = deque(maxlen=max_log)
        self.audit: deque[Action] = deque(maxlen=max_log)          # исполненные действия
        self.approvals: dict[str, ApprovalRequest] = {}            # очередь подтверждений
        self.op_capacity: dict[str, float] = {}                    # город -> capacity 0..1
        self.tick_count: int = 0

    def lock(self):
        return self._lock

    def add_campaign(self, c: Campaign) -> None:
        with self._lock:
            self.campaigns[c.id] = c

    def add_alert(self, a: Alert) -> None:
        with self._lock:
            self.alerts.appendleft(a)

    def record_action(self, a: Action) -> None:
        with self._lock:
            self.audit.appendleft(a)

    def add_approval(self, r: ApprovalRequest) -> None:
        with self._lock:
            self.approvals[r.id] = r

    def pending_approvals(self) -> list[ApprovalRequest]:
        from .models import ApprovalState
        with self._lock:
            return [r for r in self.approvals.values() if r.state == ApprovalState.PENDING]

    def add_account(self, a: Account) -> None:
        with self._lock:
            self.accounts[a.id] = a

    def add_creative(self, c: Creative) -> None:
        with self._lock:
            self.creatives.append(c)

    def campaigns_for_account(self, account_id: str) -> list[Campaign]:
        with self._lock:
            return [c for c in self.campaigns.values() if c.account_id == account_id]
