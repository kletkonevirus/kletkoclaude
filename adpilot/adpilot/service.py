"""Сервисный слой: подключение кабинетов и управление кампаниями/креативами.

Общий для веб-сервера и стартового скрипта. Ручные действия человека (пауза, старт,
смена бюджета) исполняются напрямую — здесь человек и есть «подтверждающий», очередь
подтверждений нужна только для АВТОматических рисковых действий движка.
"""
from __future__ import annotations

from .models import (Account, AccountStatus, ApprovalState, Creative,
                     EntityStatus, Platform)
from .store import Store


# ------------------------------------ Интеграции ---------------------------------------

def connect_account(store: Store, adapters: dict, platform: Platform,
                    name: str, external_id: str, note: str = "мок") -> Account:
    acc = Account(platform=platform, name=name, external_id=external_id, note=note)
    store.add_account(acc)
    adapter = adapters[platform.value]
    for c in adapter.create_campaigns(acc):
        store.add_campaign(c)
    return acc


def disconnect_account(store: Store, account_id: str) -> bool:
    with store.lock():
        acc = store.accounts.get(account_id)
        if not acc:
            return False
        acc.status = AccountStatus.DISCONNECTED
        # убираем кампании кабинета и связанные подтверждения/креативы
        cids = [c.id for c in store.campaigns.values() if c.account_id == account_id]
        for cid in cids:
            store.campaigns.pop(cid, None)
        store.creatives[:] = [cr for cr in store.creatives if cr.campaign_id not in cids]
        for r in store.approvals.values():
            if r.action.campaign_id in cids and r.state == ApprovalState.PENDING:
                r.state = ApprovalState.REJECTED
        return True


# --------------------------------- Управление кампаниями --------------------------------

def pause_campaign(store: Store, adapters: dict, cid: str) -> bool:
    with store.lock():
        c = store.campaigns.get(cid)
        if not c:
            return False
        adapters[c.platform.value].set_status(c, EntityStatus.PAUSED)
        c.pause_reason = "manual"          # ручную паузу авто-восстановление не трогает
        c.last_action = "⏸ пауза · вручную"
        return True


def resume_campaign(store: Store, adapters: dict, cid: str) -> bool:
    with store.lock():
        c = store.campaigns.get(cid)
        if not c:
            return False
        adapters[c.platform.value].set_status(c, EntityStatus.ACTIVE)
        c.pause_reason = ""
        c.last_action = "▶ запущено · вручную"
        return True


def set_budget(store: Store, adapters: dict, cid: str, budget: float) -> bool:
    with store.lock():
        c = store.campaigns.get(cid)
        if not c or budget <= 0:
            return False
        adapters[c.platform.value].set_budget(c, round(budget))
        c.last_action = f"✏ бюджет {round(budget):,} ₸ · вручную".replace(",", " ")
        return True


def set_targets(store: Store, cid: str, target_cpa: float | None,
                target_roas: float | None) -> bool:
    with store.lock():
        c = store.campaigns.get(cid)
        if not c:
            return False
        if target_cpa and target_cpa > 0:
            c.target_cpa = float(target_cpa)
            c.__dict__["_aov"] = c.target_cpa * c.target_roas  # держим экономику мока согласованной
        if target_roas and target_roas > 0:
            c.target_roas = float(target_roas)
            c.__dict__["_aov"] = c.target_cpa * c.target_roas
        c.last_action = "✏ таргеты обновлены · вручную"
        return True


# ------------------------------------- Креативы ----------------------------------------

def upload_creative(store: Store, adapters: dict, campaign_id: str, name: str,
                    fmt: str, image: tuple[str, bytes] | None = None) -> Creative | None:
    """image = (mime, bytes) превью, если загрузили картинку."""
    with store.lock():
        c = store.campaigns.get(campaign_id)
        if not c:
            return None
        asset_id = adapters[c.platform.value].upload_creative(c, name, {"format": fmt})
        mime = image[0] if image else ""
        cr = Creative(campaign_id=campaign_id, name=name, fmt=fmt, asset_id=asset_id, mime=mime)
        store.add_creative(cr)
        if image:
            store.creative_images[cr.id] = image
        c.last_action = f"🖼 залит креатив «{name}»"
        return cr
