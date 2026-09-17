"""СКЕЛЕТ адаптера Яндекс.Директ (API v5) за интерфейсом PlatformAdapter.

СТАТУС: каркас. Транспорт, авторизация, учёт баллов и разбор асинхронных отчётов — готовы.
Тела четырёх методов помечены TODO(yandex) — их и надо реализовать.

Читать вместе с `docs/YANDEX_DIRECT_HANDOFF.md` (раздел 5 «Пять грабель» — обязателен).
Эталон для копирования приёмов: `adapters_meta.py` (тот же стиль, тот же интерфейс).

Принципы проекта, которые здесь соблюдены и должны остаться:
  • ноль внешних зависимостей — только stdlib urllib;
  • транспорт инъектируется (`transport=`) → офлайн-тесты без сети и без токена;
  • секреты только из окружения/.env, никогда в коде.

⚠️ Главное, на чём легко ошибиться:
  1. Деньги в МИКРО-единицах (×1_000_000), а не ×100 как в Meta.
  2. Квота в баллах: опрашивать раз в минуты, батчить отчёты, читать заголовок Units.
  3. Отчёты асинхронные (201/202 + retryIn) и приходят в TSV, а не JSON.
  4. Конверсии Директа ≠ заказы Choco (заказы живут в DWH).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from .adapters import PlatformAdapter
from .models import Account, Campaign, EntityStatus, Metrics, Platform

API_LIVE = "https://api.direct.yandex.com/json/v5"
API_SANDBOX = "https://api-sandbox.direct.yandex.com/json/v5"
MICRO = 1_000_000                      # Директ отдаёт и принимает суммы в микро-единицах валюты

# Активный периметр Choco: Актау, Костанай, Усть-Каменогорск. Алматы НЕ входит.
KNOWN_CITIES = {
    "Aktau": ("aktau", "актау"),
    "Kostanay": ("kostanay", "костанай"),
    "Oskemen": ("oskemen", "ukg", "усть-каменогорск", "укг", "оскемен"),
}


class DirectError(Exception):
    pass


class QuotaExhausted(DirectError):
    """Баллы API исчерпаны — вызывающий должен уйти в бэкофф, а не ретраить сразу."""


def urllib_transport(url: str, body: dict, headers: dict, timeout: float = 60.0) -> tuple[int, dict, str]:
    """Реальный транспорт. Возвращает (status_code, response_headers, raw_text).

    Сырой текст, а не JSON: отчёты Директа приходят в TSV. Разбор — уровнем выше.
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise DirectError(f"Сеть: {e.reason}") from None


class DirectClient:
    """Транспорт + авторизация + учёт баллов + ожидание асинхронного отчёта."""

    def __init__(self, token: str, client_login: str | None = None,
                 sandbox: bool = True, transport=urllib_transport):
        self.token = token
        self.client_login = client_login
        self.base = API_SANDBOX if sandbox else API_LIVE
        self.transport = transport
        self.units_left: int | None = None      # остаток баллов из последнего ответа

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.client_login:                   # только для агентских аккаунтов
            h["Client-Login"] = self.client_login
        h.update(extra or {})
        return h

    def _track_units(self, headers: dict) -> None:
        """Заголовок Units: 'взято/осталось/суточный лимит'."""
        raw = headers.get("Units") or headers.get("units")
        if not raw:
            return
        try:
            self.units_left = int(str(raw).split("/")[1])
        except (IndexError, ValueError):
            pass

    def call(self, service: str, method: str, params: dict) -> dict:
        """Обычный JSON-вызов (campaigns, adgroups, keywords, adimages...)."""
        status, headers, text = self.transport(
            f"{self.base}/{service}", {"method": method, "params": params}, self._headers())
        self._track_units(headers)
        if status == 429 or (self.units_left is not None and self.units_left <= 0):
            raise QuotaExhausted("баллы API Директа исчерпаны")
        if status != 200:
            raise DirectError(f"HTTP {status}: {text[:300]}")
        resp = json.loads(text)
        if "error" in resp:
            e = resp["error"]
            raise DirectError(f"{e.get('error_code')}: {e.get('error_string')} — {e.get('error_detail')}")
        return resp.get("result", {})

    def report(self, definition: dict, max_wait: float = 120.0) -> list[dict]:
        """Отчёт: TSV + асинхронность. Возвращает список строк как dict по FieldNames.

        Директ может ответить 201/202 «отчёт готовится» с заголовком retryIn (секунды).
        processingMode=auto: маленькие отчёты отдаются сразу, большие — в очередь.
        """
        headers = self._headers({
            "processingMode": "auto",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
            "returnMoneyInMicros": "true",   # ЕДИНОЕ поведение с записью бюджета (см. грабли №1)
        })
        waited = 0.0
        while True:
            status, resp_headers, text = self.transport(
                f"{self.base}/reports", {"params": definition}, headers)
            self._track_units(resp_headers)
            if status == 200:
                return _parse_tsv(text, definition["FieldNames"])
            if status in (201, 202):
                delay = float(resp_headers.get("retryIn", 5) or 5)
                waited += delay
                if waited > max_wait:
                    raise DirectError("отчёт не готов за отведённое время")
                time.sleep(delay)
                continue
            if status == 429:
                raise QuotaExhausted("баллы API Директа исчерпаны")
            raise DirectError(f"HTTP {status}: {text[:300]}")


def _parse_tsv(text: str, field_names: list[str]) -> list[dict]:
    rows: list[dict] = []
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != len(field_names):
            continue
        rows.append(dict(zip(field_names, parts)))
    return rows


def _parse_city(name: str) -> str:
    low = (name or "").lower()
    for canon, aliases in KNOWN_CITIES.items():
        if any(a in low for a in aliases):
            return canon
    return "—"


class YandexDirectAdapter(PlatformAdapter):
    # TODO(yandex): добавить YANDEX = "yandex" в enum Platform (models.py) и заменить getattr
    platform = getattr(Platform, "YANDEX", Platform.META)

    def __init__(self, token: str, client_login: str | None = None, sandbox: bool = True,
                 default_target_cpa: float = 900.0, default_target_roas: float = 2.5,
                 transport=urllib_transport):
        self.client = DirectClient(token, client_login, sandbox, transport)
        self.default_target_cpa = default_target_cpa
        self.default_target_roas = default_target_roas
        self._last: dict[str, Metrics] = {}   # накопленные значения с прошлого опроса → для дельты

    # ---------------- чтение кампаний ----------------
    def create_campaigns(self, account: Account) -> list[Campaign]:
        """campaigns.get → список Campaign.

        TODO(yandex):
          params = {"SelectionCriteria": {},
                    "FieldNames": ["Id", "Name", "State", "Status", "DailyBudget"]}
          result = self.client.call("campaigns", "get", params)
          для каждой строки result["Campaigns"]:
            • daily_budget = row["DailyBudget"]["Amount"] / MICRO   (может отсутствовать)
            • status = ACTIVE если row["State"] == "ON" иначе PAUSED
            • city = _parse_city(row["Name"])
            • Campaign(platform=self.platform, id=str(row["Id"]), account_id=account.id,
                       target_cpa=self.default_target_cpa, target_roas=self.default_target_roas)
          Пагинация: result["LimitedBy"] → повторить с SelectionCriteria/Page.Offset.
        """
        raise NotImplementedError("TODO(yandex): campaigns.get — см. docs/YANDEX_DIRECT_HANDOFF.md §3")

    # ---------------- метрики (ДЕЛЬТА с прошлого опроса) ----------------
    def refresh_metrics(self, campaign: Campaign) -> Metrics:
        """Отчёт за сегодня → дельта относительно прошлого снимка.

        TODO(yandex):
          definition = {
            "SelectionCriteria": {"Filter": [{"Field": "CampaignId", "Operator": "EQUALS",
                                              "Values": [campaign.id]}]},
            "FieldNames": ["CampaignId", "Impressions", "Clicks", "Cost", "Conversions"],
            "ReportName": f"adpilot_{campaign.id}_today",   # ИМЯ ДОЛЖНО БЫТЬ УНИКАЛЬНЫМ
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "TODAY",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO"}
          rows = self.client.report(definition)
          total = накопленное за сегодня: Cost / MICRO, Impressions, Clicks, Conversions
          delta = total - self._last.get(campaign.id, Metrics())   ← ОБЯЗАТЕЛЬНО (грабли №3)
          self._last[campaign.id] = total
          return delta

        ⚠️ Экономия баллов: в проде НЕ вызывать по кампании отдельно. Сделать один батч-отчёт
           без фильтра по CampaignId, разложить строки по кампаниям и раздать из кеша.
        ⚠️ orders/revenue: Conversions — это цели Метрики, а НЕ заказы Choco (грабли №4).
           Заполнять можно, но авто-разгон бюджета по ним не включать.
        """
        raise NotImplementedError("TODO(yandex): reports — см. docs/YANDEX_DIRECT_HANDOFF.md §3")

    # ---------------- управление ----------------
    def set_status(self, campaign: Campaign, status: EntityStatus) -> None:
        """TODO(yandex): campaigns.suspend / campaigns.resume

        method = "suspend" if status == EntityStatus.PAUSED else "resume"
        self.client.call("campaigns", method, {"SelectionCriteria": {"Ids": [int(campaign.id)]}})
        campaign.status = status

        Пауза — автомат. Возобновление (resume) — это «разгон»: по правилу асимметричной
        автономности вызывается только после подтверждения человека, не движком.
        """
        raise NotImplementedError("TODO(yandex): campaigns.suspend/resume")

    def set_budget(self, campaign: Campaign, budget: float) -> None:
        """TODO(yandex): campaigns.update, DailyBudget.Amount

        Сумма в МИКРО: int(round(budget * MICRO)). Ошибиться здесь = ошибиться в 10 000 раз.
        params = {"Campaigns": [{"Id": int(campaign.id),
                                 "DailyBudget": {"Amount": int(round(budget * MICRO)),
                                                 "Mode": "STANDARD"}}]}
        Понижение — автомат. Повышение — только с подтверждения человека.
        """
        raise NotImplementedError("TODO(yandex): campaigns.update DailyBudget")

    def upload_creative(self, campaign: Campaign, path: str, meta: dict) -> str:
        """TODO(yandex): adimages.add → вернуть AdImageHash (для РСЯ).

        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        result = self.client.call("adimages", "add",
                                  {"AdImages": [{"ImageData": data,
                                                 "Name": meta.get("name", "adpilot")}]})
        return result["AdImages"][0]["AdImageHash"]

        Ограничения на размер/формат изображения — см. документацию adimages.
        Полный флоу «картинка → объявление» (ads.add) в этот метод не входит.
        """
        raise NotImplementedError("TODO(yandex): adimages.add")


def yandex_config_example() -> dict:
    """Форма конфига, которую должен вернуть config.yandex_config() (см. handoff §4, §7)."""
    return {"token": "<YANDEX_DIRECT_TOKEN>", "client_login": None, "sandbox": True}
