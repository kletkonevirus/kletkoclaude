#!/usr/bin/env python3
"""AdPilot — точка входа. Запускает фоновый цикл автоправил + веб-дашборд.

    python run.py                 # порт 8787, тик каждые 3 сек
    python run.py --port 9000 --interval 5

Затем открой http://localhost:8787
Никаких зависимостей — только стандартная библиотека Python 3.11+.
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

# Windows-консоль по умолчанию cp1251 и не печатает → и эмодзи — переключаем на UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from adpilot import service
from adpilot.adapters import MockAdapter
from adpilot.models import Platform
from adpilot.rules import RulesEngine, default_rule_configs
from adpilot.server import serve
from adpilot.signals import AnomalyDetector, OperationsSignal
from adpilot.store import Store
from adpilot.tick import Orchestrator


def build(interval: float, demo: bool = True):
    from adpilot import config
    config.load_env()
    meta_cfg = config.meta_config()

    store = Store()
    adapters = {
        Platform.META.value: MockAdapter(Platform.META, seed=1),
        Platform.GOOGLE.value: MockAdapter(Platform.GOOGLE, seed=2),
        Platform.TIKTOK.value: MockAdapter(Platform.TIKTOK, seed=3),
    }
    # если заданы креды Meta — для площадки meta используем ЖИВОЙ адаптер
    if meta_cfg:
        from adpilot.adapters_meta import MetaAdapter
        adapters[Platform.META.value] = MetaAdapter(**meta_cfg)

    for cfg in default_rule_configs():
        store.rule_configs[cfg.name] = cfg
    engine = RulesEngine(store, adapters)
    orch = Orchestrator(store, adapters, engine, OperationsSignal(), AnomalyDetector())

    if meta_cfg:
        # подключаем РЕАЛЬНЫЙ кабинет Meta (тянет живые кампании через Graph API)
        try:
            service.connect_account(store, adapters, Platform.META,
                                    "Choco · Meta (live)", meta_cfg["ad_account_id"])
            print(f"Meta: подключён живой кабинет {meta_cfg['ad_account_id']}")
        except Exception as e:
            print(f"Meta: не удалось подключить кабинет ({e}); остаюсь на моке")
    elif demo:
        # подключаем демо-кабинет, чтобы при первом открытии были данные
        service.connect_account(store, adapters, Platform.META,
                                "Choco · Meta (demo)", "act_1029384756")
    return store, engine, orch, adapters


def loop(orch: Orchestrator, interval: float, stop: threading.Event):
    while not stop.is_set():
        try:
            orch.tick()
        except Exception as e:
            print(f"[tick error] {e}")
        stop.wait(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=float, default=3.0, help="секунд между тиками")
    ap.add_argument("--no-open", action="store_true", help="не открывать браузер")
    args = ap.parse_args()

    store, engine, orch, adapters = build(args.interval)

    stop = threading.Event()
    t = threading.Thread(target=loop, args=(orch, args.interval, stop), daemon=True)
    t.start()

    httpd = serve(store, engine, adapters, args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"AdPilot запущен → {url}  (Ctrl+C для остановки)")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка…")
    finally:
        stop.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
