"""Офлайн-проверка MetaAdapter на моках ответов Graph API (без живого токена).

Запуск:  python tools/test_meta_offline.py
Проверяет: построение запросов, парсинг кампаний/insights, дельту метрик,
маппинг статусов, конверсию бюджета в минорные единицы, заливку изображения.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adpilot.adapters_meta import MetaAdapter
from adpilot.models import Account, EntityStatus, Platform

CALLS = []  # журнал всех исходящих запросов


def fake_transport(method, url, params):
    CALLS.append((method, url, {k: v for k, v in params.items() if k != "access_token"}))
    assert params.get("access_token") == "TESTTOKEN", "токен должен подставляться"
    if method == "GET" and "/campaigns" in url:
        return {"data": [
            {"id": "c1", "name": "Meta · Almaty · Prospecting", "status": "ACTIVE", "daily_budget": "60000"},
            {"id": "c2", "name": "Astana Retargeting", "status": "PAUSED", "daily_budget": "40000"},
        ]}
    if method == "GET" and "/insights" in url:
        # разные накопленные значения по двум опросам (для проверки дельты)
        step = fake_transport.insight_step
        return {"data": [{
            "spend": str(1000 * step), "impressions": str(8000 * step), "clicks": str(150 * step),
            "actions": [{"action_type": "purchase", "value": str(10 * step)},
                        {"action_type": "link_click", "value": "999"}],
            "action_values": [{"action_type": "purchase", "value": str(43000 * step)}],
        }]}
    if method == "POST" and url.endswith("/adimages"):
        return {"images": {"bytes": {"hash": "img_hash_abc", "url": "https://x/y.png"}}}
    if method == "POST":
        return {"success": True}
    return {}


fake_transport.insight_step = 1


def main():
    ad = MetaAdapter("TESTTOKEN", "act_999", transport=fake_transport)
    acc = Account(platform=Platform.META, name="Choco Meta", external_id="act_999")

    camps = ad.create_campaigns(acc)
    assert len(camps) == 2, camps
    c1, c2 = camps
    assert c1.id == "c1" and c1.city == "Almaty" and c1.status == EntityStatus.ACTIVE
    assert c1.daily_budget == 600.0, c1.daily_budget          # 60000 минор / 100
    assert c2.status == EntityStatus.PAUSED
    print(f"campaigns: {[(c.name, c.city, c.status.value, c.daily_budget) for c in camps]}")

    # первый опрос -> дельта = полное накопленное; второй -> дельта прироста
    d1 = ad.refresh_metrics(c1)
    fake_transport.insight_step = 3
    d2 = ad.refresh_metrics(c1)
    print(f"delta#1: spend={d1.spend} orders={d1.orders} rev={d1.revenue} clicks={d1.clicks}")
    print(f"delta#2: spend={d2.spend} orders={d2.orders} rev={d2.revenue}")
    assert d1.spend == 1000 and d1.orders == 10 and d1.revenue == 43000
    assert d2.spend == 2000 and d2.orders == 20, (d2.spend, d2.orders)   # (3-1)*step
    assert d1.clicks == 150   # link_click НЕ должен попасть в orders

    ad.set_status(c1, EntityStatus.PAUSED)
    ad.set_budget(c1, 800.0)                                   # -> daily_budget 80000 минор
    h = ad.upload_creative(c1, "burger.png", {"bytes": b"\x89PNGfake"})
    assert h == "img_hash_abc", h

    # проверяем последние POST-запросы
    posts = [c for c in CALLS if c[0] == "POST"]
    status_call = next(c for c in posts if c[2].get("status"))
    budget_call = next(c for c in posts if c[2].get("daily_budget"))
    assert status_call[2]["status"] == "PAUSED"
    assert budget_call[2]["daily_budget"] == 80000, budget_call[2]
    print(f"POST status -> {status_call[2]}")
    print(f"POST budget -> {budget_call[2]}  (800 ₸ * 100 минор)")
    print(f"upload_creative -> hash={h}")
    print("\nВСЕ ПРОВЕРКИ ПРОШЛИ ✓")


if __name__ == "__main__":
    main()
