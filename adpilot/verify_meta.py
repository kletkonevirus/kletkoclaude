"""Живая проверка подключения к Meta (только ЧТЕНИЕ — ничего не меняет).

Запуск (после того как положили токен в .env или окружение):
    python verify_meta.py

Проверяет: валиден ли токен, виден ли кабинет, тянутся ли кампании и метрики.
Никаких write-запросов — безопасно.
"""
import sys

from adpilot import config
from adpilot.adapters_meta import GraphClient, GraphError, MetaAdapter
from adpilot.models import Account, Platform

config.load_env()
cfg = config.meta_config()
if not cfg:
    print("✗ Не заданы META_ACCESS_TOKEN и META_AD_ACCOUNT_ID (см. .env.example)")
    sys.exit(1)

acct = cfg["ad_account_id"]
acct = acct if acct.startswith("act_") else f"act_{acct}"
client = GraphClient(cfg["access_token"], cfg["version"])

try:
    info = client.get(acct, {"fields": "name,currency,account_status,amount_spent"})
    print(f"✓ Кабинет: {info.get('name')} · валюта {info.get('currency')} · "
          f"статус {info.get('account_status')}")
except GraphError as e:
    print(f"✗ Токен/кабинет недоступны: {e}")
    print("  Проверьте: scope ads_read+ads_management, System User назначен на кабинет, id верный.")
    sys.exit(1)

ad = MetaAdapter(**cfg)
try:
    camps = ad.create_campaigns(Account(platform=Platform.META, name="verify", external_id=acct))
    print(f"✓ Кампаний получено: {len(camps)}")
    for c in camps[:5]:
        print(f"   • {c.name} [{c.status.value}] бюджет≈{c.daily_budget:g} ₸")
    if camps:
        m = ad.refresh_metrics(camps[0])
        print(f"✓ Метрики первой кампании (сегодня): расход={m.spend:g} "
              f"клики={m.clicks} заказы={m.orders} выручка={m.revenue:g}")
    print("\nГотово — адаптер видит живой кабинет. Можно запускать: python run.py")
except GraphError as e:
    print(f"✗ Ошибка чтения кампаний/метрик: {e}")
    sys.exit(1)
