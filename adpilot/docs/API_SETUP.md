# Подключение рекламных кабинетов — пошаговая инструкция

Как получить доступ к API Meta / Google / TikTok для AdPilot. Контекст: мы управляем
**своими** кабинетами Choco (не строим SaaS для чужих рекламодателей) — это облегчает путь.

**Главный принцип:** два разных шага.
1. **Одобрение приложения** (разово, дни–недели) — площадка разрешает вашему app делать write-вызовы.
2. **OAuth-подключение кабинета** (мгновенно) — вы даёте app доступ к конкретному аккаунту.

Разработку и тесты можно вести сразу в dev/test/sandbox — **не дожидаясь** одобрения.

---

## День 1 — запустить всё параллельно (порядок действий)

- [ ] **Meta:** создать/проверить Business Manager → **запустить Business Verification** (самый долгий скрытый шаг).
- [ ] **Meta:** создать app с продуктом Marketing API, завести System User, сгенерировать токен на свой кабинет.
- [ ] **Google:** создать Manager (MCC) аккаунт → в API Center **подать заявку на Basic Access**.
- [ ] **Google:** создать Cloud-проект, включить Google Ads API, сделать OAuth client.
- [ ] **TikTok:** создать developer app → настроить sandbox → **подать на production audit** (privacy policy + demo video).

Meta даёт результат почти сразу (свой кабинет). Google и TikTok — заявка обязательна даже для своих аккаунтов, поэтому подать её надо в первый день.

---

## 1. Meta (Marketing API)

**Что получаем:** `app_id`, `app_secret`, **System User token** (не протухает), `ad_account_id` (вид `act_123…`).

**Шаги:**
1. **Business Manager** — [business.facebook.com](https://business.facebook.com) → создать/открыть портфолио бизнеса.
   Запустить **Business Verification**: Business Settings → Security Center / Business Info (документы юрлица). Начать первым — идёт дольше всего.
2. **Создать приложение** — [developers.facebook.com](https://developers.facebook.com) → My Apps → Create App → тип **Business** → добавить продукт **Marketing API**. Привязать app к Business Manager.
3. **System User** — Business Settings → Users → **System Users** → Add → имя `adpilot-bot` → роль **Admin**.
4. **Выдать активы** — у System User → **Add Assets** → добавить ваш **Ad Account** (и Page/Pixel) с полным доступом.
5. **Сгенерировать токен** — у System User → **Generate New Token** → выбрать ваш app → scopes **`ads_read` + `ads_management`** → Generate → **сохранить токен в секретах**.
   System User токены не истекают (в отличие от пользовательских: 1–2 ч / до 60 дней). ([гайд](https://singhamandeep.com/meta-system-user-access-tokens/))
6. **Уровень доступа** — в dev-режиме app уже работает с активами своего Business Manager. Для боевых лимитов подать на **Marketing API Access Tier** (Limited → Full; порог повышения — 500 вызовов за 15 дней). ([Meta blog](https://developers.meta.com/blog/updates-to-ads-management-standard-access-feature/))

**Можно сразу (без App Review):** управлять своими кабинетами. **App Review** нужен только для доступа к **чужим** кабинетам.

---

## 2. Google (Google Ads API)

**Что получаем:** `developer_token`, `client_id`, `client_secret`, `refresh_token`, `customer_id` (целевой аккаунт), `login_customer_id` (MCC).

**Шаги:**
1. **Manager (MCC) аккаунт** — [ads.google.com](https://ads.google.com) → создать управляющий аккаунт, связать под ним ваш рекламный аккаунт.
2. **Developer token** — MCC → **Tools & Settings → Setup → API Center** ([ads.google.com/aw/apicenter](https://ads.google.com/aw/apicenter)). Токен создаётся на уровне **Test**.
3. **Заявка на Basic Access** — там же, «Apply for Basic Access», описать use-case. Срок **5–14 рабочих дней**; brand verification Cloud-проекта может ускорить. Сейчас возможен бэклог. ([access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels), [про задержки](https://ppc.land/google-faces-developer-token-application-backlog-as-new-api-tier-debuts/))
4. **Cloud-проект + OAuth** — [console.cloud.google.com](https://console.cloud.google.com) → новый проект → включить **Google Ads API** → APIs & Services → **Credentials** → Create Credentials → **OAuth client ID** (тип Web application для сервера). Настроить OAuth consent screen. Scope: `https://www.googleapis.com/auth/adwords`. Получить `client_id` + `client_secret`. ([OAuth overview](https://developers.google.com/google-ads/api/docs/oauth/overview))
5. **Refresh token** — один раз пройти OAuth-флоу для своего аккаунта (скриптом/OAuth Playground) → сохранить `refresh_token`.

**⚠️ Ключевое:** Test-токен работает **только с тестовыми аккаунтами**. Ваш **реальный** аккаунт — только после **Basic Access**.

---

## 3. TikTok (Marketing API)

**Что получаем:** `app_id`/`client_key`, `client_secret`, `access_token`, `refresh_token`, `advertiser_id`.

**Шаги:**
1. **Developer-аккаунт** — [developers.tiktok.com](https://developers.tiktok.com) (вход через TikTok for Business) + onboarding в Business Center.
2. **Создать app** → получить `client_key` + `client_secret`. Настроить redirect URI.
3. **Scopes (минимально необходимые)** — advertiser management, campaign management, creative/file upload, reporting. Лишние scope замедляют ревью.
4. **Sandbox (по умолчанию)** — создать песочницу, привязать свой advertiser-аккаунт, протестировать **весь** OAuth + флоу управления/заливки. ([TikTok sandbox](https://developers.tiktok.com/blog/introducing-sandbox))
5. **Production audit** — подать app на ревью. Приложить: **privacy policy URL**, **demo-видео по каждому запрошенному scope**, описание обработки данных, подтверждение соответствия гайдлайнам. Срок **~1–2 недели** (5–10 раб. дней). ([гайд Phyllo](https://www.getphyllo.com/post/tiktok-api-integration-guide-2026-setup-endpoints-common-pitfalls))
6. **Business verification** — для более высоких объёмов + data-security compliance аудит.
7. **OAuth** — рекламодатель авторизует → обмен auth code на `access_token` + `refresh_token`.

**⚠️ Ключевое:** Sandbox — фейковые данные. Реальный кабинет — только после production audit.

---

## Итог: какие креды будут на руках

| Площадка | Креды для AdPilot |
|---|---|
| Meta | `app_id`, `app_secret`, `system_user_token`, `ad_account_id` |
| Google | `developer_token`, `client_id`, `client_secret`, `refresh_token`, `customer_id`, `login_customer_id` |
| TikTok | `client_key`, `client_secret`, `access_token`, `refresh_token`, `advertiser_id` |

## Куда это кладётся в коде

- Реальный адаптер = класс, реализующий `PlatformAdapter` (см. `adpilot/adapters.py`). Движок правил и UI **не меняются**.
- Креды — **только в переменных окружения / секрет-менеджере**, никогда в коде и не в git (добавить `.env` в `.gitignore`).
- Хранить токены **зашифрованно per-account**, реализовать авто-refresh (Google/TikTok токены истекают; Meta System User — нет).

## Безопасность перед боевым write-доступом (обязательно)

- Жёсткие потолки бюджета на уровне AdPilot (не только на площадке).
- Неизменяемый аудит-лог всех write-действий.
- Least privilege: только нужные scopes.
- Первый боевой запуск — на одном кабинете с малым бюджетом.
