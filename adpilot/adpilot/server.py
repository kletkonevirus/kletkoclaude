"""Веб-сервер: статика SPA + JSON REST API. Чистый stdlib http.server, без зависимостей.

GET  /                      -> web/index.html (одностраничное приложение)
GET  /app.js /styles.css    -> статика
GET  /api/state             -> обзор (KPI, capacity, алерты, подтверждения)
GET  /api/integrations      -> подключённые кабинеты
GET  /api/campaigns         -> кампании с данными для управления
GET  /api/rules             -> конфиги автоправил
GET  /api/creatives         -> залитые креативы
POST /api/integrations/connect|disconnect
POST /api/campaigns/pause|resume|budget|targets
POST /api/rules/toggle|params
POST /api/creatives/upload
POST /api/approvals/approve|reject
"""
from __future__ import annotations

import base64
import json
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import analytics, service
from .models import EntityStatus, Platform
from .rules import RulesEngine
from .store import Store

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8"}


# ----------------------------------- сериализация --------------------------------------

def _cpa(m):
    return None if not math.isfinite(m.cpa) else round(m.cpa)


def ser_campaign(c) -> dict:
    m = c.metrics
    return {
        "id": c.id, "account_id": c.account_id, "platform": c.platform.value,
        "name": c.name, "city": c.city, "status": c.status.value,
        "daily_budget": c.daily_budget, "effective_budget": c.effective_budget,
        "pace": c.pace_multiplier, "pause_reason": c.pause_reason,
        "spend": m.spend, "orders": m.orders, "cpa": _cpa(m), "target_cpa": round(c.target_cpa),
        "roas": round(m.roas, 2), "target_roas": round(c.target_roas, 2),
        "ctr": round(m.ctr, 4), "cr": round(m.cr, 4), "last_action": c.last_action,
    }


def ser_account(a) -> dict:
    return {"id": a.id, "platform": a.platform.value, "name": a.name,
            "external_id": a.external_id, "status": a.status.value, "note": a.note}


def ser_rule(cfg) -> dict:
    return {"name": cfg.name, "title": cfg.title, "description": cfg.description,
            "auto": cfg.auto, "enabled": cfg.enabled, "params": cfg.params}


def ser_creative(store, cr) -> dict:
    c = store.campaigns.get(cr.campaign_id)
    has_img = cr.id in store.creative_images
    return {"id": cr.id, "campaign_id": cr.campaign_id,
            "campaign": c.name if c else "—", "name": cr.name,
            "fmt": cr.fmt, "asset_id": cr.asset_id,
            "has_image": has_img,
            "image_url": f"/api/creatives/{cr.id}/image" if has_img else None}


def overview(store: Store) -> dict:
    with store.lock():
        camps = list(store.campaigns.values())
        active = [c for c in camps if c.status == EntityStatus.ACTIVE]
        spend = sum(c.metrics.spend for c in camps)
        orders = sum(c.metrics.orders for c in camps)
        revenue = sum(c.metrics.revenue for c in camps)
        return {
            "tick": store.tick_count,
            "capacity": store.op_capacity,
            "kpi": {
                "accounts": len([a for a in store.accounts.values()
                                 if a.status.value == "connected"]),
                "campaigns": len(camps), "active": len(active),
                "spend": round(spend), "orders": orders,
                "cpa": round(spend / orders) if orders else None,
                "roas": round(revenue / spend, 2) if spend else 0.0,
            },
            "alerts": [{"level": a.level.value, "source": a.source,
                        "message": a.message} for a in list(store.alerts)[:40]],
            "approvals": [{"id": r.id, "campaign": r.campaign_name,
                           "reason": r.action.reason, "rule": r.action.rule}
                          for r in store.pending_approvals()],
            "audit": [{"rule": a.rule, "type": a.type.value, "reason": a.reason}
                      for a in list(store.audit)[:25]],
        }


# ------------------------------------- обработчик --------------------------------------

def make_handler(store: Store, engine: RulesEngine, adapters: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return {}

        # --------------------------------- GET ---------------------------------
        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                return self._get_api(path)
            self._static(path)

        def _static(self, path):
            fname = "index.html" if path == "/" else path.lstrip("/")
            full = os.path.normpath(os.path.join(WEB_DIR, fname))
            if not full.startswith(WEB_DIR) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ext = os.path.splitext(full)[1]
            with open(full, "rb") as f:
                self._send(200, f.read(), CTYPES.get(ext, "application/octet-stream"))

        def _get_api(self, path):
            if path == "/api/state":
                return self._json(overview(store))
            if path == "/api/integrations":
                return self._json({"accounts": [ser_account(a) for a in store.accounts.values()],
                                   "platforms": [p.value for p in Platform]})
            if path == "/api/campaigns":
                rows = sorted(store.campaigns.values(), key=lambda c: (c.city, c.platform.value))
                return self._json({"campaigns": [ser_campaign(c) for c in rows]})
            if path == "/api/rules":
                return self._json({"rules": [ser_rule(c) for c in store.rule_configs.values()]})
            if path == "/api/creatives":
                return self._json({"creatives": [ser_creative(store, cr) for cr in store.creatives],
                                   "campaigns": [{"id": c.id, "name": c.name}
                                                 for c in store.campaigns.values()]})
            if path.startswith("/api/creatives/") and path.endswith("/image"):
                cid = path.split("/")[3]
                img = store.creative_images.get(cid)
                if not img:
                    return self._send(404, b"no image", "text/plain")
                return self._send(200, img[1], img[0] or "application/octet-stream")
            if path == "/api/analytics":
                return self._json(analytics.analytics_payload(store))
            self._json({"error": "unknown"}, 404)

        # --------------------------------- POST --------------------------------
        def do_POST(self):
            path = urlparse(self.path).path
            b = self._body()
            try:
                ok, extra = self._post_api(path, b)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            self._json({"ok": ok, **(extra or {})}, 200 if ok else 400)

        def _post_api(self, path, b) -> tuple[bool, dict | None]:
            if path == "/api/integrations/connect":
                acc = service.connect_account(
                    store, adapters, Platform(b["platform"]),
                    b.get("name") or b["platform"].title(),
                    b.get("external_id") or "act_mock")
                return True, {"account": ser_account(acc)}
            if path == "/api/integrations/disconnect":
                return service.disconnect_account(store, b["account_id"]), None
            if path == "/api/campaigns/pause":
                return service.pause_campaign(store, adapters, b["id"]), None
            if path == "/api/campaigns/resume":
                return service.resume_campaign(store, adapters, b["id"]), None
            if path == "/api/campaigns/budget":
                return service.set_budget(store, adapters, b["id"], float(b["daily_budget"])), None
            if path == "/api/campaigns/targets":
                return service.set_targets(store, b["id"], b.get("target_cpa"),
                                           b.get("target_roas")), None
            if path == "/api/rules/toggle":
                cfg = store.rule_configs.get(b["rule"])
                if not cfg:
                    return False, None
                cfg.enabled = bool(b["enabled"])
                return True, None
            if path == "/api/rules/params":
                cfg = store.rule_configs.get(b["rule"])
                if not cfg:
                    return False, None
                for k, v in (b.get("params") or {}).items():
                    if k in cfg.params:
                        cfg.params[k] = type(cfg.params[k])(v)
                if "auto" in b:
                    cfg.auto = bool(b["auto"])
                return True, None
            if path == "/api/creatives/upload":
                image = None
                data_url = b.get("image")   # "data:image/png;base64,...."
                if data_url and data_url.startswith("data:") and "," in data_url:
                    head, b64 = data_url.split(",", 1)
                    mime = head[5:].split(";")[0] or "image/png"
                    try:
                        image = (mime, base64.b64decode(b64))
                    except Exception:
                        image = None
                cr = service.upload_creative(store, adapters, b["campaign_id"],
                                             b.get("name") or "creative",
                                             b.get("fmt") or "image", image)
                return (cr is not None), None
            if path in ("/api/approvals/approve", "/api/approvals/reject"):
                return self._decision(path, b.get("id", "")), None
            return False, None

        def _decision(self, path, apr_id) -> bool:
            from .models import ApprovalState
            with store.lock():
                req = store.approvals.get(apr_id)
                if not req or req.state != ApprovalState.PENDING:
                    return False
                if path.endswith("approve"):
                    req.state = ApprovalState.APPROVED
                    engine.execute_approved(req.action)
                else:
                    req.state = ApprovalState.REJECTED
                return True

    return Handler


def serve(store: Store, engine: RulesEngine, adapters: dict, host: str, port: int):
    return ThreadingHTTPServer((host, port), make_handler(store, engine, adapters))
