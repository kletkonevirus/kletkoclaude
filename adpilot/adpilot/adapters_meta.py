"""Реальный адаптер Meta (Marketing API / Graph) за интерфейсом PlatformAdapter.

Ноль внешних зависимостей — только stdlib urllib. HTTP-транспорт инъектируется,
поэтому адаптер тестируется офлайн на моках ответов Graph API, без живого токена.

Требуется (передаётся при создании / из окружения):
  access_token     — System User token со scope ads_read + ads_management
  ad_account_id    — id кабинета вида act_123456789
Опционально: version (по умолчанию актуальная), currency_minor (минорные единицы валюты),
purchase_action_types (какие action_type считать заказом в insights).

⚠️ Заметки для прода:
  • Суммы бюджета Meta принимает в МИНОРНЫХ единицах валюты кабинета (обычно ×100).
  • insights возвращают НАКОПЛЕННЫЕ значения за период; refresh_metrics отдаёт дельту
    с прошлого опроса (телескопирование), чтобы лечь в текущий движок накопления.
  • Пагинация campaigns не реализована (limit=50) — для боевого объёма добавить paging.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .adapters import PlatformAdapter
from .models import Account, Campaign, EntityStatus, Metrics, Platform

DEFAULT_VERSION = "v21.0"
GRAPH = "https://graph.facebook.com"
DEFAULT_PURCHASE_ACTIONS = (
    "purchase", "omni_purchase",
    "offsite_conversion.fb_pixel_purchase", "onsite_web_purchase",
)
KNOWN_CITIES = ("Almaty", "Astana", "Shymkent", "Karaganda", "Aktobe", "Shymket")


class GraphError(Exception):
    pass


def urllib_transport(method: str, url: str, params: dict, timeout: float = 30.0) -> dict:
    """Реальный транспорт. Возвращает распарсенный JSON или бросает GraphError."""
    try:
        if method == "GET":
            full = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(full, method="GET")
        else:
            data = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        raise GraphError(f"HTTP {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise GraphError(f"Сеть: {e.reason}") from None


class GraphClient:
    def __init__(self, token: str, version: str = DEFAULT_VERSION, transport=urllib_transport):
        self.token = token
        self.version = version
        self.transport = transport

    def _call(self, method: str, path: str, params: dict) -> dict:
        p = dict(params or {})
        p["access_token"] = self.token
        url = f"{GRAPH}/{self.version}/{path.lstrip('/')}"
        resp = self.transport(method, url, p)
        if isinstance(resp, dict) and resp.get("error"):
            raise GraphError(resp["error"].get("message", "unknown"))
        return resp

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._call("GET", path, params or {})

    def post(self, path: str, params: dict | None = None) -> dict:
        return self._call("POST", path, params or {})


def _parse_city(name: str) -> str:
    for c in KNOWN_CITIES:
        if c.lower() in (name or "").lower():
            return c
    return "—"


def _extract(actions: list | None, wanted: tuple) -> float:
    total = 0.0
    for a in actions or []:
        if a.get("action_type") in wanted:
            try:
                total += float(a.get("value", 0))
            except (TypeError, ValueError):
                pass
    return total


class MetaAdapter(PlatformAdapter):
    platform = Platform.META

    def __init__(self, access_token: str, ad_account_id: str, version: str = DEFAULT_VERSION,
                 currency_minor: int = 100, default_target_cpa: float = 900.0,
                 default_target_roas: float = 2.5,
                 purchase_action_types: tuple = DEFAULT_PURCHASE_ACTIONS, transport=urllib_transport):
        self.client = GraphClient(access_token, version, transport)
        self.acct = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self.currency_minor = currency_minor
        self.default_target_cpa = default_target_cpa
        self.default_target_roas = default_target_roas
        self.purchase_actions = purchase_action_types
        self._last: dict[str, Metrics] = {}   # накопленные insights с прошлого опроса

    # -------- чтение кампаний --------
    def create_campaigns(self, account: Account) -> list[Campaign]:
        resp = self.client.get(f"{self.acct}/campaigns",
                               {"fields": "id,name,status,daily_budget", "limit": 50})
        out: list[Campaign] = []
        for row in resp.get("data", []):
            db = row.get("daily_budget")
            budget = (int(db) / self.currency_minor) if db else 0.0
            status = (EntityStatus.ACTIVE if row.get("status") == "ACTIVE"
                      else EntityStatus.PAUSED)
            out.append(Campaign(
                platform=Platform.META, name=row.get("name", row["id"]),
                city=_parse_city(row.get("name", "")), daily_budget=budget,
                target_cpa=self.default_target_cpa, target_roas=self.default_target_roas,
                account_id=account.id, status=status, id=row["id"]))
        return out

    # -------- метрики (дельта с прошлого опроса) --------
    def refresh_metrics(self, campaign: Campaign) -> Metrics:
        resp = self.client.get(f"{campaign.id}/insights", {
            "fields": "spend,impressions,clicks,actions,action_values",
            "date_preset": "today"})
        rows = resp.get("data", [])
        cur = Metrics()
        if rows:
            r = rows[0]
            cur = Metrics(
                spend=float(r.get("spend", 0) or 0),
                impressions=int(float(r.get("impressions", 0) or 0)),
                clicks=int(float(r.get("clicks", 0) or 0)),
                orders=int(_extract(r.get("actions"), self.purchase_actions)),
                revenue=_extract(r.get("action_values"), self.purchase_actions))
        prev = self._last.get(campaign.id, Metrics())
        self._last[campaign.id] = cur
        # дельта; clamp >=0 на случай отката суток
        return Metrics(
            spend=max(0.0, cur.spend - prev.spend),
            impressions=max(0, cur.impressions - prev.impressions),
            clicks=max(0, cur.clicks - prev.clicks),
            orders=max(0, cur.orders - prev.orders),
            revenue=max(0.0, cur.revenue - prev.revenue))

    # -------- управление --------
    def set_status(self, campaign: Campaign, status: EntityStatus) -> None:
        self.client.post(campaign.id,
                         {"status": "ACTIVE" if status == EntityStatus.ACTIVE else "PAUSED"})

    def set_budget(self, campaign: Campaign, budget: float) -> None:
        self.client.post(campaign.id,
                         {"daily_budget": int(round(budget * self.currency_minor))})

    def upload_creative(self, campaign: Campaign, path: str, meta: dict) -> str:
        """Заливка изображения в /adimages -> возвращает image hash.

        Полный флоу «картинка -> adcreative -> ad» требует adset-контекста и здесь
        не реализован (TODO): для боевого запуска добавить создание adcreative и ad.
        """
        import base64
        image_bytes = meta.get("bytes")
        if not image_bytes:
            raise GraphError("upload_creative: нужны байты изображения в meta['bytes']")
        resp = self.client.post(f"{self.acct}/adimages",
                                {"bytes": base64.b64encode(image_bytes).decode()})
        images = resp.get("images", {})
        first = next(iter(images.values()), {})
        return first.get("hash", "")
