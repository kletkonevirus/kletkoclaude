"use strict";
const $ = s => document.querySelector(s);
const KZT = n => n == null ? "—" : new Intl.NumberFormat("ru-RU").format(Math.round(n)) + " ₸";
const pct = n => n == null ? "—" : Math.round(n * 100) + "%";
const capColor = v => v >= 0.9 ? "var(--ok)" : v >= 0.5 ? "var(--warn)" : "var(--crit)";
const badge = p => `<span class="badge ${p}">${p}</span>`;

async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  return r.json().catch(() => ({}));
}

let toastTimer = null;
function toast(msg, ok = true) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + (ok ? "ok" : "err");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = "toast", 2200);
}

function modal(html) {
  $("#modal-root").innerHTML =
    `<div class="modal-bg" onclick="if(event.target===this)closeModal()"><div class="modal">${html}</div></div>`;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

const TITLES = { overview: "Обзор", analytics: "Аналитика", integrations: "Интеграции", campaigns: "Кампании", rules: "Правила", creatives: "Креативы" };
const RENDER = {};
let view = "overview", timer = null;

function setView(v) {
  view = v;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === v));
  $("#view-title").textContent = TITLES[v];
  $("#topbar-right").innerHTML = "";
  if (timer) { clearInterval(timer); timer = null; }
  render();
  if (v === "overview" || v === "campaigns") timer = setInterval(render, 3000);
}
function render() { (RENDER[view] || (() => {}))(); }

/* ------------------------------- Обзор ------------------------------- */
RENDER.overview = async () => {
  const s = await api("/api/state");
  $("#tick").textContent = "тик #" + s.tick;
  const k = s.kpi;
  const kpis = [
    ["Кабинеты", k.accounts], ["Кампаний", `${k.active}/${k.campaigns}`],
    ["Расход", KZT(k.spend)], ["Заказы", k.orders],
    ["CPA", KZT(k.cpa)], ["ROAS", k.roas],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const caps = Object.entries(s.capacity).map(([c, v]) =>
    `<div class="cap"><span class="name">${c}</span>
      <span class="bar"><span class="fill" style="width:${Math.round(v*100)}%;background:${capColor(v)}"></span></span>
      <span class="val">${pct(v)}</span></div>`).join("") || `<div class="empty">нет подключённых кабинетов</div>`;

  const apr = s.approvals.length ? s.approvals.map(a =>
    `<div class="apr"><div class="r">${a.reason}</div>
      <div class="mut" style="font-size:12px">${a.campaign} · <b>${a.rule}</b></div>
      <div class="row-actions" style="margin-top:9px">
        <button class="btn btn-ok btn-sm" onclick="approve('${a.id}')">Подтвердить</button>
        <button class="btn btn-danger btn-sm" onclick="decline('${a.id}')">Отклонить</button>
      </div></div>`).join("") : `<div class="empty">Очередь пуста — автопилот справляется сам</div>`;

  const alerts = s.alerts.length ? s.alerts.map(a =>
    `<div class="al"><span class="dot ${a.level}"></span><span>${a.message}
      <span class="src">· ${a.source}</span></span></div>`).join("") : `<div class="empty">тихо</div>`;

  const audit = s.audit.length ? s.audit.map(a =>
    `<div class="al"><span>${a.type} <span class="src">${a.rule} · ${a.reason}</span></span></div>`).join("")
    : `<div class="empty">действий ещё не было</div>`;

  $("#content").innerHTML = `
    <div class="kpis">${kpis}</div>
    <div class="grid g2">
      <div class="card"><h2>Операционная загрузка (доставка)</h2>${caps}
        <div class="mut" style="font-size:12px;margin-top:8px">&lt;100% → пейсинг режет бюджет · &lt;35% → пауза. Кухня подключится, когда оцифруют.</div></div>
      <div class="card"><h2>⏳ Ждут подтверждения (разгон бюджета)</h2>${apr}</div>
      <div class="card"><h2>🔔 Сигналы и действия</h2>${alerts}</div>
      <div class="card"><h2>🧾 Аудит автодействий</h2>${audit}</div>
    </div>`;
};
window.approve = async id => { await api("/api/approvals/approve", "POST", { id }); toast("Подтверждено"); render(); };
window.decline = async id => { await api("/api/approvals/reject", "POST", { id }); toast("Отклонено"); render(); };

/* --------------------------- Аналитика (графики) --------------------------- */
const shortDate = d => (d || "").slice(5);      // 2026-07-14 -> 07-14
const fmtK = n => Math.abs(n) >= 1000 ? Math.round(n / 1000) + "k" : "" + Math.round(n);

// Компактный линейный график на инлайновом SVG (без внешних библиотек).
function svgChart(labels, series, fmt = fmtK, height = 240) {
  const W = 720, H = height, pad = { l: 50, r: 14, t: 12, b: 28 };
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
  const all = series.flatMap(s => s.data);
  const max = Math.max(1, ...all) * 1.12;
  const n = labels.length || 1;
  const X = i => pad.l + pw * (n === 1 ? 0.5 : i / (n - 1));
  const Y = v => pad.t + ph * (1 - v / max);
  let g = "";
  for (let k = 0; k <= 4; k++) {
    const val = max * k / 4, y = Y(val);
    g += `<line x1="${pad.l}" y1="${y}" x2="${W-pad.r}" y2="${y}" stroke="var(--line)" stroke-width="1"/>`;
    g += `<text x="${pad.l-8}" y="${y+3}" text-anchor="end" font-size="10" fill="var(--mut)">${fmt(val)}</text>`;
  }
  const step = Math.max(1, Math.ceil(n / 7));
  let xl = "";
  labels.forEach((d, i) => { if (i % step === 0) xl += `<text x="${X(i)}" y="${H-8}" text-anchor="middle" font-size="10" fill="var(--mut)">${shortDate(d)}</text>`; });
  let paths = "";
  series.forEach(s => {
    const pts = s.data.map((v, i) => `${X(i)},${Y(v)}`).join(" ");
    paths += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    s.data.forEach((v, i) => { paths += `<circle cx="${X(i)}" cy="${Y(v)}" r="2.4" fill="${s.color}"><title>${labels[i]}: ${fmt(v)}</title></circle>`; });
  });
  const legend = series.map(s => `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:14px"><span style="width:12px;height:3px;background:${s.color};display:inline-block;border-radius:2px"></span><span class="mut" style="font-size:12px">${s.name}</span></span>`).join("");
  return `<div style="margin-bottom:6px">${legend}</div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}${xl}${paths}</svg>`;
}

function weekAgg(labels, d) {
  const L = [], sp = [], or = [], cpa = [], roas = [], cour = [];
  for (let i = 0; i < labels.length; i += 7) {
    const e = Math.min(i + 7, labels.length);
    let s = 0, o = 0, rev = 0, cSum = 0, cN = 0;
    for (let j = i; j < e; j++) { s += d.spend[j]; o += d.orders[j]; rev += d.roas[j] * d.spend[j]; cSum += d.courier[j]; cN++; }
    L.push(labels[i]); sp.push(Math.round(s)); or.push(o);
    cpa.push(o ? Math.round(s / o) : 0); roas.push(s ? +(rev / s).toFixed(2) : 0);
    cour.push(cN ? +(cSum / cN).toFixed(1) : 0);
  }
  return { labels: L, spend: sp, orders: or, cpa, roas, courier: cour };
}

let gran = "day";
RENDER.analytics = async () => {
  const raw = await api("/api/analytics");
  const d = gran === "week" ? weekAgg(raw.labels, raw) : raw;
  $("#topbar-right").innerHTML = `
    <button class="btn ${gran==='day'?'btn-primary':'btn-ghost'} btn-sm" onclick="setGran('day')">Дни</button>
    <button class="btn ${gran==='week'?'btn-primary':'btn-ghost'} btn-sm" onclick="setGran('week')">Недели</button>`;
  const acc = "var(--accent)";
  $("#content").innerHTML = `
    <div class="grid g2">
      <div class="card"><h2>Расход, ₸</h2>${svgChart(d.labels, [{name:"Расход",color:acc,data:d.spend}])}</div>
      <div class="card"><h2>Заказы</h2>${svgChart(d.labels, [{name:"Заказы",color:"#2ec26a",data:d.orders}], n=>""+Math.round(n))}</div>
      <div class="card"><h2>CPA, ₸</h2>${svgChart(d.labels, [{name:"CPA",color:"#e8b23a",data:d.cpa}])}</div>
      <div class="card"><h2>ROAS</h2>${svgChart(d.labels, [{name:"ROAS",color:"#c07bff",data:d.roas}], n=>n.toFixed(1))}</div>
      <div class="card full"><h2>Загрузка курьеров (заказов на курьера в день) · реальные данные</h2>
        ${svgChart(d.labels, [{name:"Заказов/курьер",color:"#ff7ab6",data:d.courier}], n=>""+Math.round(n))}
        <div class="mut" style="font-size:12px;margin-top:6px">Источник: data/couriers_by_day.csv. Именно этот сигнал гейтит расход в операционном пейсинге.</div>
      </div>
    </div>`;
};
window.setGran = g => { gran = g; render(); };

/* ---------------------------- Интеграции ---------------------------- */
RENDER.integrations = async () => {
  const d = await api("/api/integrations");
  $("#topbar-right").innerHTML = `<button class="btn btn-primary" onclick="connectModal()">+ Подключить кабинет</button>`;
  const cards = d.accounts.length ? d.accounts.map(a => `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3>${a.name}</h3>${badge(a.platform)}
      </div>
      <div class="mut" style="margin:6px 0">ID кабинета: ${a.external_id}</div>
      <div><span class="pill ${a.status==='connected'?'active':'paused'}">${a.status}</span>
        <span class="mut" style="font-size:12px">· ${a.note}</span></div>
      ${a.status === 'connected'
        ? `<button class="btn btn-danger btn-sm" style="margin-top:12px" onclick="disconnect('${a.id}')">Отключить</button>`
        : ``}
    </div>`).join("") : `<div class="empty">Нет подключённых кабинетов. Нажмите «Подключить кабинет».</div>`;
  $("#content").innerHTML = `<div class="cards">${cards}</div>`;
  window._platforms = d.platforms;
};
window.connectModal = () => {
  const opts = (window._platforms || ["meta", "google", "tiktok"]).map(p => `<option value="${p}">${p}</option>`).join("");
  modal(`<h3>Подключить рекламный кабинет</h3>
    <div class="mut" style="font-size:12px">Прототип: подключение мокируется. В проде здесь OAuth площадки.</div>
    <label>Площадка</label><select id="m-platform">${opts}</select>
    <label>Название</label><input id="m-name" placeholder="Choco · Meta">
    <label>ID кабинета</label><input id="m-ext" placeholder="act_123456789">
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Отмена</button>
      <button class="btn btn-primary" onclick="doConnect()">Подключить</button>
    </div>`);
};
window.doConnect = async () => {
  const platform = $("#m-platform").value;
  const r = await api("/api/integrations/connect", "POST",
    { platform, name: $("#m-name").value, external_id: $("#m-ext").value });
  closeModal();
  if (r.ok) { toast("Кабинет подключён — кампании загружены"); render(); }
  else toast("Не удалось подключить", false);
};
window.disconnect = async id => {
  if (!confirm("Отключить кабинет? Его кампании исчезнут из системы.")) return;
  await api("/api/integrations/disconnect", "POST", { account_id: id });
  toast("Кабинет отключён"); render();
};

/* ----------------------------- Кампании ----------------------------- */
RENDER.campaigns = async () => {
  const d = await api("/api/campaigns");
  const rows = d.campaigns.map(c => {
    const st = c.status === "active" ? `<span class="pill active">active</span>` : `<span class="pill paused">paused</span>`;
    const cpaCls = c.cpa == null ? "mut" : (c.cpa > c.target_cpa ? "bad" : "good");
    const roasCls = c.roas >= c.target_roas ? "good" : "bad";
    const bud = c.pace < 0.99 ? `${KZT(c.effective_budget)} <span class="mut">(${pct(c.pace)})</span>` : KZT(c.daily_budget);
    const toggle = c.status === "active"
      ? `<button class="btn btn-ghost btn-sm" onclick="pauseC('${c.id}')">⏸ Пауза</button>`
      : `<button class="btn btn-ok btn-sm" onclick="resumeC('${c.id}')">▶ Старт</button>`;
    return `<tr>
      <td>${badge(c.platform)}</td><td>${c.city}</td>
      <td>${c.name}<div class="mut" style="font-size:11px">${c.last_action || ""}</div></td>
      <td>${st}</td><td>${bud}</td><td>${KZT(c.spend)}</td><td>${c.orders}</td>
      <td class="${cpaCls}">${c.cpa == null ? "—" : KZT(c.cpa)}<span class="mut" style="font-size:11px"> /${KZT(c.target_cpa)}</span></td>
      <td class="${roasCls}">${c.roas}<span class="mut" style="font-size:11px"> /${c.target_roas}</span></td>
      <td><div class="row-actions">${toggle}
        <button class="btn btn-ghost btn-sm" onclick="editBudget('${c.id}',${c.daily_budget})">Бюджет</button>
        <button class="btn btn-ghost btn-sm" onclick="editTargets('${c.id}',${Math.round(c.target_cpa)},${c.target_roas})">Таргеты</button>
      </div></td></tr>`;
  }).join("");
  $("#content").innerHTML = d.campaigns.length ? `
    <div class="card" style="padding:0;overflow:auto">
      <table><thead><tr>
        <th>Площадка</th><th>Город</th><th>Кампания</th><th>Статус</th><th>Бюджет</th>
        <th>Расход</th><th>Заказы</th><th>CPA</th><th>ROAS</th><th>Управление</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>`
    : `<div class="empty">Нет кампаний. Подключите кабинет в разделе «Интеграции».</div>`;
};
window.pauseC = async id => { await api("/api/campaigns/pause", "POST", { id }); toast("Пауза"); render(); };
window.resumeC = async id => { await api("/api/campaigns/resume", "POST", { id }); toast("Запущено"); render(); };
window.editBudget = (id, cur) => {
  modal(`<h3>Дневной бюджет</h3>
    <label>Сумма, ₸</label><input id="m-bud" type="number" value="${cur}">
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Отмена</button>
      <button class="btn btn-primary" onclick="saveBudget('${id}')">Сохранить</button>
    </div>`);
};
window.saveBudget = async id => {
  await api("/api/campaigns/budget", "POST", { id, daily_budget: Number($("#m-bud").value) });
  closeModal(); toast("Бюджет обновлён"); render();
};
window.editTargets = (id, cpa, roas) => {
  modal(`<h3>Целевые показатели</h3>
    <div class="form-row"><div><label>Target CPA, ₸</label><input id="m-cpa" type="number" value="${cpa}"></div>
      <div><label>Target ROAS</label><input id="m-roas" type="number" step="0.1" value="${roas}"></div></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Отмена</button>
      <button class="btn btn-primary" onclick="saveTargets('${id}')">Сохранить</button>
    </div>`);
};
window.saveTargets = async id => {
  await api("/api/campaigns/targets", "POST",
    { id, target_cpa: Number($("#m-cpa").value), target_roas: Number($("#m-roas").value) });
  closeModal(); toast("Таргеты обновлены"); render();
};

/* ------------------------------ Правила ----------------------------- */
RENDER.rules = async () => {
  const d = await api("/api/rules");
  const cards = d.rules.map(r => {
    const params = Object.entries(r.params).map(([k, v]) =>
      `<div><label>${k}</label><input class="p-input" data-rule="${r.name}" data-key="${k}" type="number" step="0.05" value="${v}"></div>`).join("");
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <div><h3>${r.title}</h3><div class="mut" style="font-size:12px">${r.description}</div></div>
        <label class="switch"><input type="checkbox" ${r.enabled ? "checked" : ""}
          onchange="toggleRule('${r.name}',this.checked)"><span class="slider"></span></label>
      </div>
      <div class="form-row" style="margin-top:12px;flex-wrap:wrap">${params}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px">
        <label style="display:flex;gap:8px;align-items:center;margin:0">
          <span title="Вкл = движок исполняет сам; выкл = только предложение в очередь">Авто-исполнение</span>
          <label class="switch"><input type="checkbox" id="auto-${r.name}" ${r.auto ? "checked" : ""}><span class="slider"></span></label>
        </label>
        <button class="btn btn-ghost btn-sm" onclick="saveRule('${r.name}')">Сохранить</button>
      </div>
    </div>`;
  }).join("");
  $("#content").innerHTML = `<div class="cards">${cards}</div>
    <div class="mut" style="font-size:12px;margin-top:14px">⚠️ Асимметрия по умолчанию: снижение/пауза — авто; разгон бюджета — через подтверждение. «Авто-исполнение» у разгона включайте осознанно.</div>`;
};
window.toggleRule = async (rule, enabled) => { await api("/api/rules/toggle", "POST", { rule, enabled }); toast(enabled ? "Правило включено" : "Правило выключено"); };
window.saveRule = async name => {
  const params = {};
  document.querySelectorAll(`.p-input[data-rule="${name}"]`).forEach(i => params[i.dataset.key] = Number(i.value));
  const auto = $("#auto-" + name).checked;
  await api("/api/rules/params", "POST", { rule: name, params, auto });
  toast("Пороги сохранены"); render();
};

/* ----------------------------- Креативы ----------------------------- */
const fmtIcon = { image: "🖼", video: "🎬", carousel: "🎠" };
RENDER.creatives = async () => {
  const d = await api("/api/creatives");
  const opts = d.campaigns.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
  const tiles = d.creatives.length ? d.creatives.map(cr => {
    const thumb = cr.image_url
      ? `<div class="thumb"><img class="thumb-img" src="${cr.image_url}" alt="${cr.name}"><img class="zoom" src="${cr.image_url}" alt=""></div>`
      : `<div class="thumb placeholder">${fmtIcon[cr.fmt] || "🖼"}</div>`;
    return `<div class="cr-card">${thumb}
      <div class="cr-meta"><b>${cr.name}</b>
        <div class="mut" style="font-size:12px">${cr.fmt} · ${cr.campaign}</div>
        <div class="mut" style="font-size:11px">${cr.asset_id}</div></div></div>`;
  }).join("") : `<div class="empty">Креативов пока нет — загрузите первый слева.</div>`;

  $("#content").innerHTML = `
    <div class="grid g2">
      <div class="card"><h2>Загрузить креатив</h2>
        <div class="mut" style="font-size:12px">Выберите файл изображения — появится превью. В проде файл уходит в API площадки.</div>
        <label>Файл изображения</label>
        <input type="file" id="cr-file" accept="image/*" onchange="previewFile()">
        <div id="cr-preview"></div>
        <label>Кампания</label><select id="cr-camp">${opts || "<option value=''>нет кампаний</option>"}</select>
        <div class="form-row">
          <div><label>Название</label><input id="cr-name" placeholder="hook_v3_burger"></div>
          <div><label>Формат</label><select id="cr-fmt"><option>image</option><option>video</option><option>carousel</option></select></div>
        </div>
        <button class="btn btn-primary" style="margin-top:14px" onclick="uploadCr()">⬆ Загрузить креатив</button>
      </div>
      <div class="card"><h2>Галерея креативов <span class="mut" style="font-size:12px">· наведите на миниатюру для увеличения</span></h2>
        <div class="cr-grid">${tiles}</div></div>
    </div>`;
};
function fileToDataURL(file) {
  return new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(file); });
}
window.previewFile = async () => {
  const f = $("#cr-file").files[0];
  const box = $("#cr-preview");
  if (!f) { box.innerHTML = ""; return; }
  if (!$("#cr-name").value) $("#cr-name").value = f.name.replace(/\.[^.]+$/, "");
  const url = await fileToDataURL(f);
  box.innerHTML = `<div class="thumb" style="margin:10px 0"><img class="thumb-img" src="${url}"><img class="zoom" src="${url}"></div>`;
};
window.uploadCr = async () => {
  const campaign_id = $("#cr-camp").value;
  if (!campaign_id) return toast("Нет кампаний — подключите кабинет", false);
  const f = $("#cr-file").files[0];
  let image = null, name = $("#cr-name").value;
  if (f) {
    if (f.size > 4 * 1024 * 1024) return toast("Файл больше 4 МБ", false);
    name = name || f.name.replace(/\.[^.]+$/, "");
    image = await fileToDataURL(f);
  }
  const r = await api("/api/creatives/upload", "POST",
    { campaign_id, name: name || "creative", fmt: $("#cr-fmt").value, image });
  if (r.ok) { toast("Креатив загружен"); render(); } else toast("Ошибка загрузки", false);
};

/* ------------------------------- init ------------------------------- */
document.querySelectorAll("#nav a").forEach(a => a.addEventListener("click", () => setView(a.dataset.view)));
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
setView("overview");
