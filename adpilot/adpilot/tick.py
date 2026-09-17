"""Оркестрация одного цикла: pull дельты -> детект аномалий -> накопление -> правила."""
from __future__ import annotations

from .adapters import _accumulate
from .models import Alert, AlertLevel, Metrics
from .rules import RuleContext, RulesEngine
from .signals import AnomalyDetector, OperationsSignal
from .store import Store

WINDOWS_PER_DAY = 96


class Orchestrator:
    def __init__(self, store: Store, adapters: dict, engine: RulesEngine,
                 ops: OperationsSignal, anomaly: AnomalyDetector):
        self.store = store
        self.adapters = adapters
        self.engine = engine
        self.ops = ops
        self.anomaly = anomaly

    def tick(self) -> None:
        with self.store.lock():
            self.store.tick_count += 1
            # новый «день» — обнуляем накопленные метрики (история окон сохраняется)
            if self.store.tick_count % WINDOWS_PER_DAY == 0:
                for c in self.store.campaigns.values():
                    c.metrics = Metrics()

            cities = sorted({c.city for c in self.store.campaigns.values()})
            self.store.op_capacity = self.ops.snapshot(cities)

            for c in list(self.store.campaigns.values()):
                adapter = self.adapters[c.platform.value]
                delta = adapter.refresh_metrics(c)   # метрики за одно окно

                # аномалии ищем на уровне ОКНА (всплеск/обвал именно сейчас)
                for sev, msg in self.anomaly.check(c.history, delta):
                    lvl = AlertLevel.CRIT if sev == "crit" else AlertLevel.WARN
                    self.store.add_alert(Alert(lvl, f"anomaly:{c.name}", msg))

                c.history.append(delta)
                if len(c.history) > 30:
                    c.history.pop(0)
                _accumulate(c.metrics, delta)        # накапливаем «за день»

                ctx = RuleContext(campaign=c,
                                  op_capacity=self.store.op_capacity.get(c.city, 1.0),
                                  anomalies=[], params={})
                for action in self.engine.evaluate(ctx):
                    self.engine.apply(action)
