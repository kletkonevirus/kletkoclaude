"""ДЕТЕРМИНИРОВАННЫЙ движок автоправил.

Ключевой принцип безопасности — АСИММЕТРИЧНАЯ АВТОНОМНОСТЬ:
  • Безопасные действия (пауза, снижение бюджета) исполняются автоматически.
  • Рисковые действия (разгон бюджета, снятие с паузы) НЕ исполняются —
    формируется ApprovalRequest, решение за человеком.
Никакого LLM в этом контуре: правила предсказуемы, быстры и полностью аудируемы.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .models import (Action, ActionType, Alert, AlertLevel, ApprovalRequest,
                     Campaign, EntityStatus, RuleConfig)
from .store import Store


@dataclass
class RuleContext:
    campaign: Campaign
    op_capacity: float          # 0..1 для города кампании
    anomalies: list[tuple[str, str]]
    params: dict                # пороги текущего правила (из RuleConfig.params)


# Правило = функция, возвращающая список Action (может быть пустым).
Rule = Callable[[RuleContext], list[Action]]


# ---------------------------------- Встроенные правила ---------------------------------

def rule_operational_pacing(ctx: RuleContext) -> list[Action]:
    """Гейтим расход реальной пропускной способностью доставки (курьеры/кухня)."""
    c, cap = ctx.campaign, ctx.op_capacity
    pause_below = ctx.params.get("pause_below", 0.35)
    if c.status != EntityStatus.ACTIVE:
        return []   # на паузе пейсингом не управляем — сначала снятие с паузы
    if cap < pause_below:
        return [Action(c.id, ActionType.PAUSE, "operational_pacing",
                       f"Критический перегруз доставки в {c.city} (capacity={cap:.0%}) — "
                       f"стоп трафика, заказы не довезём вовремя")]
    if cap < 1.0 and abs(c.pace_multiplier - cap) > 0.05:
        return [Action(c.id, ActionType.THROTTLE, "operational_pacing",
                       f"Перегруз доставки в {c.city} (capacity={cap:.0%}) — "
                       f"режем бюджет до {cap:.0%}", payload={"multiplier": cap})]
    return []


def rule_cpa_guardrail(ctx: RuleContext) -> list[Action]:
    """Стоп-лосс по CPA: если стоимость заказа улетела выше таргета — пауза."""
    c = ctx.campaign
    m = c.metrics
    mult = ctx.params.get("cpa_multiplier", 1.8)
    min_orders = ctx.params.get("min_orders", 3)
    if c.status == EntityStatus.ACTIVE and m.orders >= min_orders and math.isfinite(m.cpa):
        if m.cpa > c.target_cpa * mult:
            return [Action(c.id, ActionType.PAUSE, "cpa_guardrail",
                           f"CPA {m.cpa:.0f} > {mult:g}× таргета ({c.target_cpa:.0f}) — стоп-лосс")]
    return []


def rule_scale_winner(ctx: RuleContext) -> list[Action]:
    """Разгон победителя — РИСКОВО: только предложение, уйдёт в очередь подтверждений.

    Условие: ROAS уверенно выше таргета И доставка не перегружена (иначе разгонять некуда).
    Кулдаун 30 тиков — чтобы не переспрашивать одно и то же после каждого решения.
    """
    c = ctx.campaign
    m = c.metrics
    mult = ctx.params.get("roas_multiplier", 1.25)
    step = ctx.params.get("budget_step_pct", 20)
    if (c.status == EntityStatus.ACTIVE and ctx.op_capacity >= 0.9
            and m.spend > 0 and m.roas >= c.target_roas * mult and c.pace_multiplier >= 0.99):
        return [Action(c.id, ActionType.INCREASE_BUDGET, "scale_winner",
                       f"ROAS {m.roas:.1f} ≥ {mult:g}× таргета ({c.target_roas:.1f}) и доставка "
                       f"свободна — предлагаю +{step:g}% бюджета",
                       payload={"delta_pct": step}, cooldown=30)]
    return []


def rule_pacing_recovery(ctx: RuleContext) -> list[Action]:
    """Когда доставка разгрузилась — вернуть кампанию к плановому бюджету.

    Возврат к УЖЕ ОДОБРЕННОМУ бюджету не увеличивает расход выше исходного плана,
    поэтому безопасен и исполняется автоматически (auto=True) — включая снятие
    с ОПЕРАЦИОННОЙ паузы. Паузу по стоп-лоссу CPA это правило НЕ трогает.
    """
    c, cap = ctx.campaign, ctx.op_capacity
    resume_above = ctx.params.get("resume_above", 0.5)
    # снять с операционной паузы, когда доставка уверенно разгрузилась (гистерезис)
    if (c.status == EntityStatus.PAUSED and c.pause_reason == "operational" and cap >= resume_above):
        return [Action(c.id, ActionType.RESUME, "pacing_recovery",
                       f"Доставка в {c.city} разгрузилась (capacity={cap:.0%}) — "
                       f"снимаю операционную паузу", auto=True)]
    # вернуть полный бюджет активной, если пейсинг ранее его резал
    if cap >= 0.98 and c.pace_multiplier < 0.99 and c.status == EntityStatus.ACTIVE:
        return [Action(c.id, ActionType.THROTTLE, "pacing_recovery",
                       f"Доставка в {c.city} разгрузилась (capacity={cap:.0%}) — "
                       f"возвращаю плановый бюджет", payload={"multiplier": 1.0})]
    return []


# Реестр: имя правила -> функция. Порядок исполнения задаётся default_rule_configs().
REGISTRY: dict[str, Rule] = {
    "operational_pacing": rule_operational_pacing,
    "pacing_recovery": rule_pacing_recovery,
    "cpa_guardrail": rule_cpa_guardrail,
    "scale_winner": rule_scale_winner,
}


def default_rule_configs() -> list[RuleConfig]:
    """Стартовый набор правил с порогами. Пользователь меняет их из интерфейса."""
    return [
        RuleConfig("operational_pacing", "Операционный пейсинг",
                   "Режет бюджет при перегрузе доставки; пауза при критическом перегрузе.",
                   auto=True, params={"pause_below": 0.35}),
        RuleConfig("pacing_recovery", "Восстановление после пейсинга",
                   "Возвращает плановый бюджет и снимает операционную паузу при разгрузке.",
                   auto=True, params={"resume_above": 0.5}),
        RuleConfig("cpa_guardrail", "Стоп-лосс по CPA",
                   "Ставит на паузу кампанию, у которой CPA выше порога × таргет.",
                   auto=True, params={"cpa_multiplier": 1.8, "min_orders": 3}),
        RuleConfig("scale_winner", "Разгон победителей",
                   "Предлагает поднять бюджет кампаниям с ROAS выше порога × таргет.",
                   auto=False, params={"roas_multiplier": 1.25, "budget_step_pct": 20}),
    ]


# ------------------------------------- Движок ------------------------------------------

class RulesEngine:
    def __init__(self, store: Store, adapters: dict):
        self.store = store
        self.adapters = adapters   # platform.value -> PlatformAdapter

    def evaluate(self, ctx: RuleContext) -> list[Action]:
        """Прогнать включённые правила (в порядке rule_configs) с их порогами."""
        actions: list[Action] = []
        for name, cfg in self.store.rule_configs.items():
            if not cfg.enabled:
                continue
            fn = REGISTRY.get(name)
            if fn is None:
                continue
            ctx.params = cfg.params
            try:
                for a in fn(ctx):
                    if cfg.auto:      # политика авто/подтверждение задаётся конфигом правила
                        a.auto = True
                    actions.append(a)
            except Exception as e:    # правило не должно ронять весь цикл
                self.store.add_alert(Alert(AlertLevel.WARN, "rules_engine",
                                           f"Правило {name} упало: {e}"))
        return actions

    def apply(self, action: Action) -> None:
        """Исполнить безопасное действие сразу; рисковое — отправить на подтверждение.

        Защита от шума: кулдаун на правило + дедуп очереди подтверждений.
        """
        c = self.store.campaigns[action.campaign_id]
        tick = self.store.tick_count

        # кулдаун: правило молчит для этой кампании до истечения срока
        if c.cooldowns.get(action.rule, -1) > tick:
            return

        if action.needs_approval:
            # дедуп: не плодить одинаковые запросы, пока прежний ещё висит в очереди
            for r in self.store.pending_approvals():
                if r.action.campaign_id == c.id and r.action.rule == action.rule:
                    return
            self.store.add_approval(ApprovalRequest(action=action, campaign_name=c.name))
            self.store.add_alert(Alert(AlertLevel.INFO, action.rule,
                                       f"⏸ Требует подтверждения: {action.reason} [{c.name}]"))
            if action.cooldown:
                c.cooldowns[action.rule] = tick + action.cooldown
            return

        self._execute(action, c)
        if action.cooldown:
            c.cooldowns[action.rule] = tick + action.cooldown

    def _execute(self, action: Action, c: Campaign) -> None:
        adapter = self.adapters[c.platform.value]
        if action.type == ActionType.PAUSE:
            adapter.set_status(c, EntityStatus.PAUSED)
            # операционная пауза обратима автоматически; guardrail — только человеком
            c.pause_reason = "operational" if action.rule == "operational_pacing" else "guardrail"
            c.last_action = f"⏸ пауза · {action.rule}"
        elif action.type == ActionType.RESUME:
            adapter.set_status(c, EntityStatus.ACTIVE)
            c.pause_reason = ""
            c.last_action = f"▶ возобновлено · {action.rule}"
        elif action.type == ActionType.THROTTLE:
            c.pace_multiplier = float(action.payload.get("multiplier", c.pace_multiplier))
            c.last_action = f"🔻 бюджет {c.pace_multiplier:.0%} · {action.rule}"
        elif action.type == ActionType.INCREASE_BUDGET:
            delta = action.payload.get("delta_pct", 0) / 100.0
            adapter.set_budget(c, round(c.daily_budget * (1 + delta)))
            c.last_action = f"🔺 бюджет +{action.payload.get('delta_pct')}% · {action.rule}"
        self.store.record_action(action)

    def execute_approved(self, action: Action) -> None:
        """Вызывается сервером после подтверждения человеком."""
        c = self.store.campaigns[action.campaign_id]
        self._execute(action, c)
        if action.cooldown:
            c.cooldowns[action.rule] = self.store.tick_count + action.cooldown
        self.store.add_alert(Alert(AlertLevel.INFO, "approval",
                                   f"✅ Подтверждено человеком: {action.reason} [{c.name}]"))
