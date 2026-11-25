"use strict";
/* Diet Planner – interfejs. Wszystkie adresy są względne (działa pod Ingress HA). */

const SLOTS = { sniadanie: "Śniadanie", przekaska: "Przekąska", obiad: "Obiad", kolacja: "Kolacja" };
const DEPT_ORDER = ["Warzywa i owoce", "Mięso i ryby", "Nabiał i jaja", "Pieczywo i produkty zbożowe",
  "Produkty suche, mrożonki i konserwy", "Tłuszcze i przyprawy", "Inne"];
const TAGS = ["gluten", "laktoza", "jaja", "ryby", "orzechy", "mięso"];
const MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"];
const K = {
  sex: "Dane podstawowe / Płeć", age: "Dane podstawowe / Wiek", height: "Dane podstawowe / Wzrost (cm)",
  weight: "Dane podstawowe / Aktualna waga (kg)", activity: "Dane podstawowe / Poziom aktywności",
  goal: "Cel / Cel główny", kcal: "Zapotrzebowanie / Zapotrzebowanie kaloryczne (kcal/dzień)",
  source: "Zapotrzebowanie / Źródło wartości", protein: "Zapotrzebowanie / Białko (g/dzień)",
  carbs: "Zapotrzebowanie / Węglowodany (g/dzień)", fat: "Zapotrzebowanie / Tłuszcze (g/dzień)",
  fiber: "Zapotrzebowanie / Błonnik (g/dzień)",
};
const ACTIVITY = [["bardzo", 1.9, "bardzo wysoka"], ["wysok", 1.725, "wysoka"], ["umiark", 1.55, "umiarkowana"], ["lekk", 1.375, "lekka"], ["siedz", 1.2, "siedzący tryb"]];
const TEXTAREA_RE = /^(Lubi|Nie lubi|Alergie|Nietolerancje|Ograniczenia medyczne|Treningi|Uwagi|Dni specjalne|Ulubione kuchnie|Preferowane|Używane suplementy)/;
const NUMBER_RE = /\((cm|kg|g\/dzień|l\/dzień|kcal\/dzień)\)|^Wiek$|^Waga/;
const CHECK = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const CHEVRON = '<svg class="chevron" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>';

const S = {
  rtab: "lib", inbox: null, books: null, booksAi: null, instr: null, instrAt: 0, instrModel: "import",
  data: null, view: "dzis", week: null, weekSel: null, wday: null, nextPrep: null, plan: null, planWeek: null, day: null,
  open: new Set(), person: null, edits: {}, rfilter: "", rsearch: "", busy: false, lastPlanJson: "",
  stab: "osoby", mtab: {}, mperson: null,
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => parseFloat(String(v ?? "").replace(",", "."));
const fmt = n => Math.round(n).toLocaleString("pl-PL");

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const ct = r.headers.get("content-type") || "";
  const out = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error((out && out.error) || r.statusText); e.status = r.status; throw e; }
  return out;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 5000 : 2600);
}

/* ---------- Daty i tygodnie ---------- */
function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day);
  const y = t.getUTCFullYear(), w = Math.ceil(((t - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7);
  return `${y}-W${String(w).padStart(2, "0")}`;
}
function weekStart(week) {
  const [y, w] = [+week.slice(0, 4), +week.slice(-2)];
  const jan4 = new Date(Date.UTC(y, 0, 4)); const d = jan4.getUTCDay() || 7;
  return new Date(Date.UTC(y, 0, 4 - d + 1 + (w - 1) * 7));
}
function shiftWeek(week, n) { const d = weekStart(week); d.setUTCDate(d.getUTCDate() + 7 * n + 3); return isoWeek(new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())); }
function weekRange(week) {
  const a = weekStart(week), b = new Date(a); b.setUTCDate(a.getUTCDate() + 6);
  return a.getUTCMonth() === b.getUTCMonth() ? `${a.getUTCDate()}–${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]}`
    : `${a.getUTCDate()} ${MONTHS[a.getUTCMonth()]} – ${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]}`;
}
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/* ---------- Biblioteka ---------- */
const prodMap = () => Object.fromEntries(S.data.products.map(p => [p.name, p]));
const recipeMap = () => Object.fromEntries(S.data.recipes.map(r => [r.id, r]));
function nutr(ingr) {
  const P = prodMap(); const t = [0, 0, 0, 0];
  for (const [k, g] of Object.entries(ingr)) { const p = P[k]; if (!p) continue;
    t[0] += p.kcal * g / 100; t[1] += p.protein * g / 100; t[2] += p.carbs * g / 100; t[3] += p.fat * g / 100; }
  return t;
}
const recipeIngr = r => Object.fromEntries(r.ingredients.map(i => [i.product, i.grams]));
function recipeTags(r) { const P = prodMap(); return [...new Set(r.ingredients.flatMap(i => (P[i.product] || {}).tags || []))].filter(t => t !== "mięso"); }
function recipeExcluded(r) {
  const ex = S.data.exclusions, P = prodMap();
  return r.ingredients.some(i => ((P[i.product] || {}).tags || []).some(t => ex.tags.includes(t)) || ex.words.some(w => i.product.toLowerCase().includes(w)));
}
function amountText(prod, g) {
  if (!prod) return `${g} g`;
  if (prod.piece) return `${Math.round(g / prod.piece)} ${prod.piece_name || "szt."}`;
  return `${g} ${prod.unit || "g"}`;
}

/* ---------- Nazwy i źródła ---------- */
const MON_SHORT = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];
function weekLabel(week) {
  const a = weekStart(week), b = new Date(a); b.setUTCDate(a.getUTCDate() + 6);
  return a.getUTCMonth() === b.getUTCMonth() ? `${a.getUTCDate()}–${b.getUTCDate()} ${MON_SHORT[b.getUTCMonth()]}`
    : `${a.getUTCDate()} ${MON_SHORT[a.getUTCMonth()]} – ${b.getUTCDate()} ${MON_SHORT[b.getUTCMonth()]}`;
}
function niceName(n) {  // tytuły z książek pisane WIELKIMI LITERAMI → zwykłe zdanie
  n = String(n || "").trim();
  const letters = n.replace(/[^A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]/g, "");
  if (letters.length > 3 && letters === letters.toUpperCase()) n = n.toLowerCase().replace(/^./, c => c.toUpperCase());
  return n;
}
function bookTitle(b) {  // nazwa pliku książki → czytelny tytuł
  let t = String(b || "").trim();
  if (!t || /^\(?anonymous\)?$/i.test(t)) return "";
  t = t.replace(/\.(pdf|epub|mobi|azw3?)$/i, "").replace(/-([a-z0-9]{6})$/i, (m, x) => /\d|[bcdfghjklmnpqrstvwxz]{3}/i.test(x) ? "" : m).replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (t === t.toUpperCase() || t === t.toLowerCase()) t = t.toLowerCase();
  return t.replace(/^./, c => c.toUpperCase());
}
function exclusionHits(r) {
  const ex = S.data.exclusions, P = prodMap(), hits = new Set();
  for (const i of r.ingredients) {
    for (const t of (P[i.product] || {}).tags || []) if (ex.tags.includes(t)) hits.add(t);
    for (const w of ex.words) if (i.product.toLowerCase().includes(w)) hits.add(i.product);
  }
  return [...hits];
}

/* ---------- Ładowanie ---------- */
async function loadState() { S.data = await api("api/state"); S.person ??= S.data.persons[0]?.id; }
async function loadPlan(week, quiet = false) {
  try {
    const p = await api(`api/plans/${week}`);
    const j = JSON.stringify(p);
    if (quiet && j === S.lastPlanJson) return false;
    S.plan = p; S.lastPlanJson = j;
  } catch (e) { if (e.status === 404) { S.plan = null; S.lastPlanJson = ""; } else throw e; }
  S.planWeek = week;
  return true;
}
function setPlan(p) { S.plan = p; S.lastPlanJson = JSON.stringify(p); S.planWeek = p.week; updateBadge(); }

/* ---------- Router ---------- */
async function route() {
  let v = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "dzis";
  if (v === "plan") v = "tydzien";
  if (v === "profile") { v = "ustawienia"; S.stab = "osoby"; }
  S.view = ["dzis", "tydzien", "zakupy", "przepisy", "ustawienia"].includes(v) ? v : "dzis";
  // „Dziś” zawsze pokazuje bieżący tydzień; „Tydzień” i „Zakupy” – tydzień wybrany strzałkami.
  if (S.view === "dzis") S.week = S.data.current_week;
  else if (S.view === "tydzien" || S.view === "zakupy") S.week = S.weekSel || S.data.current_week;
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.view === S.view));
  if (["dzis", "tydzien", "zakupy"].includes(S.view) && S.planWeek !== S.week) await loadPlan(S.week);
  if (S.view === "dzis") await loadNextPrep();
  if (S.view === "przepisy" && S.rtab !== "lib") await loadRecipeTab();
  render();
}
function render() {
  const v = { dzis: viewToday, tydzien: viewWeek, zakupy: viewShop, przepisy: viewRecipes, ustawienia: viewSettings }[S.view];
  $("#view").innerHTML = v();
  updateBadge();
  if (S.view === "ustawienia" && S.stab === "osoby") validateProfile();
  if (S.view === "ustawienia" && S.stab === "posilki") validateMeals();
}
function updateBadge() {
  const items = S.plan?.shopping.items || [];
  const left = items.filter(i => !i.checked).length;
  $("#cart-badge").textContent = S.plan && S.planWeek === S.week && left ? left : "";
  const rb = $("#recipe-badge"); if (rb) rb.textContent = S.data?.inbox_count || "";
}

/* ---------- Widok: Plan ---------- */
function weekHead(title, menu = false) {
  const cw = S.data.current_week;
  const when = S.week === cw ? "Ten tydzień" : S.week === shiftWeek(cw, 1) ? "Przyszły tydzień" : S.week === shiftWeek(cw, -1) ? "Poprzedni tydzień" : weekRange(S.week);
  return `<div class="head"><div><h1>${title}</h1><p class="sub">${when}</p></div>
    <div class="row"><div class="weeknav"><button class="btn ghost icon" data-act="week" data-n="-1" aria-label="Poprzedni tydzień">‹</button>
    <span>${weekLabel(S.week)}</span><button class="btn ghost icon" data-act="week" data-n="1" aria-label="Następny tydzień">›</button></div>
    ${menu ? `<button class="btn ghost icon more" data-act="planmenu" aria-label="Opcje planu" title="Opcje planu">⋯</button>` : ""}</div></div>`;
}
function planStale(p) {
  const why = [];
  const meals = (S.data.settings.meals || []).filter(m => m.name.trim());
  if (meals.length) {
    const tot = meals.reduce((a, m) => a + (num(m.share) || 0), 0) || 1;
    const cur = meals.map(m => `${m.name.trim().toLowerCase()}|${m.time || ""}|${Math.round((num(m.share) || 0) / tot * 100)}`).join(";");
    const was = p.slots.map(x => `${x.name.trim().toLowerCase()}|${x.time || ""}|${Math.round(x.share * 100)}`).join(";");
    if (cur !== was) why.push("posiłki");
  }
  if (S.data.persons.some(pe => p.targets[pe.id] && p.targets[pe.id].kcal !== pe.target.kcal)) why.push("cele kcal");
  return why;
}
function minutesOf(t) { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ""); return m ? +m[1] * 60 + +m[2] : null; }

const DAY_FULL = ["niedziela", "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota"];
const plural = (n, one, few, many) => n === 1 ? one : ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many);
const fmtMin = m => m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? " " + (m % 60) + " min" : ""}` : `${m} min`;
const shortDate = iso => `${+iso.slice(8)}.${iso.slice(5, 7)}`;
function addDays(iso, n) { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); }
function staleBanner(p) {
  const stale = planStale(p);
  return stale.length ? `<div class="banner"><span>Od ułożenia planu zmieniły się ${stale.join(" i ")}. Przelicz, żeby porcje pasowały – dania zostaną te same.</span>
    <button class="btn small primary" data-act="refreshplan">Przelicz plan</button></div>` : "";
}
function emptyPlan(text) {
  return `<div class="card empty"><div class="big">🗓️</div><h2 style="justify-content:center">Brak planu na ten tydzień</h2>
    <p>${text}</p><button class="btn primary" data-act="plansheet">Ułóż plan</button></div>`;
}
async function loadNextPrep() {
  // W niedzielę „Dziś” pokazuje sesję meal prepu z planu na następny tydzień.
  S.nextPrep = null;
  if (new Date().getDay() !== 0) return;
  const w = shiftWeek(S.data.current_week, 1);
  try { const p = await api(`api/plans/${w}`); if (p.prep_mode === "weekly" && p.prep?.sessions?.some(x => x.day < 0)) S.nextPrep = { week: w, ses: p.prep.sessions.find(x => x.day < 0) }; }
  catch { /* brak planu na przyszły tydzień */ }
}
function sessionLine(ses, extra = "") {
  const done = ses.tasks.filter(x => x.checked).length;
  return `ok. <b>${fmtMin(ses.minutes)}</b>${ses.active && ses.active < ses.minutes ? ` · przy kuchni ~${fmtMin(ses.active)}` : ""} · ${ses.tasks.length} ${plural(ses.tasks.length, "zadanie", "zadania", "zadań")}${done ? ` · ${done}/${ses.tasks.length} ✓` : ""}${extra}`;
}

/* ---------- Widok: Dziś ---------- */
function viewToday() {
  const cw = S.data.current_week, today = todayIso(), now = new Date();
  let h = `<div class="head"><div><h1>Dziś</h1><p class="sub">${DAY_FULL[now.getDay()]}, ${now.getDate()} ${MONTHS[now.getMonth()]}</p></div>
    <div class="row"><a class="btn ghost" href="#/tydzien">Cały tydzień →</a></div></div>`;
  if (S.nextPrep) {
    h += `<div class="card cook prepcall"><div class="ico">🧑‍🍳</div><div><b>Dziś meal prep na przyszły tydzień</b>
      <small>${sessionLine(S.nextPrep.ses)}</small></div><button class="btn small primary" data-act="openprep" data-w="${S.nextPrep.week}">Zaczynam</button></div>`;
  }
  const p = S.plan && S.planWeek === cw ? S.plan : null;
  if (!p) return h + emptyPlan("Ułóż plan na ten tydzień – aplikacja dobierze dania, przeliczy porcje i przygotuje listę zakupów.");
  const di = p.days.findIndex(d => d.date === today);
  if (di < 0) return h + `<div class="card empty"><p>Dzisiejszego dnia nie ma w planie.</p><a class="btn" href="#/tydzien">Zobacz tydzień</a></div>`;
  S.day = di;
  return h + staleBanner(p) + dayHtml(p, di);
}

/* ---------- Widok: Tydzień ---------- */
function viewWeek() {
  let h = weekHead("Tydzień", !!S.plan);
  const p = S.plan;
  if (!p) return h + emptyPlan("Aplikacja dobierze dania według ustawień i profili, przeliczy porcje dla każdej osoby i przygotuje listę zakupów.");
  h += staleBanner(p);
  const prep = p.prep_mode === "weekly" ? p.prep : null;
  if (S.wday === "prep" && prep?.sessions?.length) {
    return h + `<div class="daynav"><button class="btn small ghost" data-act="backweek">← Tydzień</button></div>` + prepView(p);
  }
  if (typeof S.wday === "number" && S.wday >= 0 && S.wday < p.days.length) {
    const i = S.wday, d = p.days[i];
    S.day = i;
    return h + `<div class="daynav"><button class="btn small ghost" data-act="backweek">← Tydzień</button><span class="spacer"></span>
      <button class="btn ghost icon" data-act="day" data-i="${i - 1}" ${i ? "" : "disabled"} aria-label="Poprzedni dzień">‹</button>
      <b>${esc(d.name)} ${shortDate(d.date)}</b>
      <button class="btn ghost icon" data-act="day" data-i="${i + 1}" ${i < p.days.length - 1 ? "" : "disabled"} aria-label="Następny dzień">›</button></div>` + dayHtml(p, i);
  }
  h += cookingCard(p);
  if (p.warnings.length && !planStale(p).length) h += `<details class="alert"><summary>${p.warnings.length === 1 ? "1 uwaga do planu" : p.warnings.length + " uwagi do planu"}</summary>${p.warnings.map(w => `<p>${esc(w)}</p>`).join("")}</details>`;
  const today = todayIso();
  h += `<div class="weekgrid">${p.days.map((d, i) => {
    const fresh = prep?.fresh?.[String(i)] || {};
    return `<div class="card dayrow ${d.date === today ? "today" : ""}" data-act="day" data-i="${i}" role="button" tabindex="0">
      <div class="dr-head"><b>${esc(d.name)}</b><span class="sub">${shortDate(d.date)}</span>${d.date === today ? '<span class="chip pc p0">dziś</span>' : ""}<span class="spacer"></span>
        <span class="dr-kcal">${p.persons.map((pe, k) => `<i class="pc p${k % 2}" title="${esc(pe.name)}">${esc(pe.name[0])} ${fmt((d.totals[pe.id] || [0])[0])}</i>`).join("")}</span></div>
      <ul class="dr-meals">${d.meals.map(m => { const sl = p.slots[m.slot], sp = m.split, f = fresh[String(m.slot)];
        const ic = f ? (f.from_freezer.length ? "🧊" : f.from_fridge.length ? "📦" : "") : "";
        const chip = !sp ? "" : sp.mode === "shared" ? `🍲 ${p.persons.map(pe => sp.parts[pe.id]).join(":")}` : `🥞 ${p.persons.map(pe => sp.counts[pe.id]).join("+")}`;
        return `<li><span class="t">${esc(sl.time || "")}</span><span class="n">${esc(niceName(m.name))}</span>${ic ? `<span title="Z meal prepu">${ic}</span>` : ""}${chip ? `<small class="spl">${chip}</small>` : ""}</li>`; }).join("")}</ul></div>`;
  }).join("")}</div>`;
  return h;
}
function cookingCard(p) {
  if (p.prep_mode === "weekly" && p.prep?.sessions?.length) {
    const sun = addDays(p.days[0].date, -1);
    return `<div class="card cooking"><h2>🧑‍🍳 Gotowanie w tym tygodniu${p.prep_limit ? ` <small>limit niedzieli ${fmtMin(p.prep_limit)}</small>` : ""}</h2>${p.prep.sessions.map(ses => `<div class="ck-row">
      <div><b>${ses.day < 0 ? `Niedziela ${shortDate(sun)}` : `Środa wieczorem ${shortDate(p.days[2].date)}`}</b><br><small class="sub">${sessionLine(ses)}</small></div>
      <button class="btn small ${ses.day < 0 ? "primary" : ""}" data-act="day" data-i="prep">Lista zadań</button></div>`).join("")}
      <p class="sub">Reszta to szybkie wykończenia w dniu posiłku – zobaczysz je przy daniach.</p></div>`;
  }
  if (p.schedule?.length) {
    return `<div class="card cooking"><h2>👩‍🍳 Gotowanie na zapas</h2><ul class="ck-list">${p.schedule.map(c =>
      `<li><b>${esc(c.label)}</b> – ${esc(niceName(c.name))} <small class="sub">(obiad na ${esc(c.for)} · ~${c.time} min)</small></li>`).join("")}</ul></div>`;
  }
  return "";
}

/* ---------- Jeden dzień (Dziś i podgląd dnia w Tygodniu) ---------- */
function dayHtml(p, di) {
  S.day = di;
  const today = todayIso(), day = p.days[di], isToday = day.date === today;
  const prep = p.prep_mode === "weekly" ? p.prep : null;
  let h = `<div class="card daysum"><div class="people">${p.persons.map((pe, i) => {
    const t = day.totals[pe.id] || [0, 0, 0, 0], tg = p.targets[pe.id].kcal;
    return `<div class="person p${i % 2}"><div class="name"><b>${esc(pe.name)}</b><span>${fmt(t[0])} <small>/ ${fmt(tg)} kcal</small></span></div>
      <div class="bar"><i style="width:${Math.min(100, t[0] / tg * 100)}%"></i></div>
      <div class="macros">B ${t[1]} g · W ${t[2]} g · T ${t[3]} g</div></div>`;
  }).join("")}</div></div>`;
  if (prep) {
    const thaw = prep.thaw?.[String(di)] || [];
    if (thaw.length) h += `<div class="card cook thaw"><div class="ico">🧊</div><div><b>Wieczorem przenieś z zamrażarki do lodówki</b>
      <small>${thaw.map(esc).join(" · ")}</small></div></div>`;
    for (const x of prep.sessions.filter(x => x.day === di)) {
      h += `<div class="card cook"><div class="ico">🧑‍🍳</div><div><b>${isToday ? "Dziś wieczorem" : "Wieczorem"}: krótka sesja meal prep</b>
        <small>${sessionLine(x)}</small></div><button class="btn small" data-act="openprep" data-w="${p.week}">Lista</button></div>`;
    }
  }
  for (const c of p.schedule.filter(s => Math.max(0, s.cook_day) === di)) {
    h += `<div class="card cook"><div class="ico">👩‍🍳</div><div><b>${c.cook_day < 0 ? "Ugotuj przed tygodniem" : "Dziś gotujesz na zapas"}: ${esc(niceName(c.name))}</b>
      <small>${esc(c.label)} · obiad na: ${esc(c.for)} · ok. ${c.time} min</small></div></div>`;
  }
  // Dziś: wyróżnij najbliższy posiłek, wcześniejsze przygaś.
  let next = -1;
  const now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes();
  if (isToday) next = day.meals.findIndex(m => { const t = minutesOf(p.slots[m.slot].time); return t === null || t + 45 >= nowMin; });
  const R = recipeMap();
  day.meals.forEach((m, si) => {
    const slot = p.slots[m.slot], r = R[m.recipe];
    const key = `${S.planWeek}:${S.day}-${si}`, open = S.open.has(key);
    const past = isToday && next >= 0 && si < next || isToday && next === -1;
    const isNext = isToday && si === next;
    const t = minutesOf(slot.time), diff = t === null ? null : t - nowMin;
    const when = isNext && diff !== null ? (diff > 0 ? `za ${diff >= 60 ? Math.floor(diff / 60) + " h " : ""}${diff % 60 ? diff % 60 + " min" : ""}`.trim() : "teraz") : "";
    const chips = p.persons.map((pe, i) => { const n = m.nutrition[pe.id] || [0, 0, 0, 0];
      return `<span class="chip pc p${i % 2}"><b>${esc(pe.name)}</b> ${fmt(n[0])} kcal</span>`; }).join("");
    const sp = m.split, spChip = !sp ? "" : sp.mode === "shared"
      ? `<span class="chip split" title="Garnek dzielony na równe części">🍲 ${p.persons.map(pe => sp.parts[pe.id]).join(":")}</span>`
      : `<span class="chip split" title="${esc(sp.unit)}">🥞 ${p.persons.map(pe => sp.counts[pe.id]).join(" + ")} szt.</span>`;
    const batch = m.batch && !prep ? `<span class="chip" title="Ugotujesz raz na kilka dni">na zapas: ${m.batch.map(x => p.days[x].short).join(" + ")}</span>` : "";
    const body = open && r ? mealBody(p, day, m, si, key, r, prep) : "";
    h += `<article class="card meal ${open ? "open" : ""} ${past ? "past" : ""} ${isNext ? "next" : ""}"><div class="meal-head" data-act="meal" data-key="${key}">
      <div class="time">${esc(slot.time || "—")}</div>
      <div class="meal-title"><small>${esc(slot.name)}${when ? ` · <b class="when">${when}</b>` : ""}</small><h3>${esc(niceName(m.name))}</h3><div class="chips">${chips}${spChip}${batch}</div></div>${CHEVRON}</div>${body}</article>`;
  });
  return h;
}
function mealBody(p, day, m, si, key, r, prep) {
  const P = prodMap(), sp = m.split;
  const isCook = !prep && m.batch && m.batch[0] === S.day;
  const days = isCook ? m.batch.length : 1;
  const sum = k => p.persons.reduce((a, pe) => a + ((m.portions[pe.id] || {})[k] || 0), 0);
  const totOf = k => (sp ? (sp.total[k] || sum(k)) : sum(k)) * days;
  const tab = S.mtab[key] || "porcja";
  const hasAll = !!sp || isCook;
  if (!S.mperson || (S.mperson !== "all" && !p.persons.some(pe => pe.id === S.mperson))) S.mperson = p.persons[0]?.id;
  const who = S.mperson === "all" && !hasAll ? p.persons[0].id : S.mperson;
  const tabs = `<div class="mtabs" role="tablist">${[["porcja", "Porcja"], ["gotowanie", "Gotowanie"], ["makro", "Makro"]].map(([k, v]) =>
    `<button role="tab" aria-selected="${tab === k}" class="${tab === k ? "on" : ""}" data-act="mtab" data-key="${key}" data-t="${k}">${v}</button>`).join("")}</div>`;
  let inner = "";
  if (tab === "porcja") {
    const seg = `<div class="seg small">${p.persons.map(pe => `<button class="${who === pe.id ? "active" : ""}" data-act="mperson" data-id="${pe.id}">${esc(pe.name)}${sp ? ` <small>${sp.mode === "shared" ? sp.parts[pe.id] + "/" + sp.total_parts : sp.counts[pe.id] + " szt."}</small>` : ""}</button>`).join("")}
      ${hasAll ? `<button class="${who === "all" ? "active" : ""}" data-act="mperson" data-id="all">${isCook ? `Całość na ${days} dni` : "Całość"}</button>` : ""}</div>`;
    const rows = r.ingredients.map(i => { const k = i.product, g = who === "all" ? totOf(k) : (m.portions[who] || {})[k] || 0;
      return `<li><span>${esc(k)}</span><b>${who !== "all" && sp ? "≈ " : ""}${amountText(P[k], Math.round(g))}</b></li>`; }).join("");
    const n = who === "all" ? null : m.nutrition[who] || [0, 0, 0, 0];
    inner = `${splitBanner(p, m, si, isCook)}${seg}<ul class="portion">${rows}</ul>
      ${n ? `<p class="sub">${fmt(n[0])} kcal · białko ${Math.round(n[1])} g · węglowodany ${Math.round(n[2])} g · tłuszcze ${Math.round(n[3])} g</p>` : ""}`;
  } else if (tab === "gotowanie") {
    const fr = prep?.fresh?.[String(S.day)]?.[String(m.slot)];
    const freshBox = fr && (fr.from_fridge.length || fr.from_freezer.length) ? `<div class="freshbox"><b>⚡ Dziś na świeżo · ok. ${fr.minutes} min</b>
      <p class="sub">Z lodówki: ${esc([...fr.from_fridge, ...fr.from_freezer].join(", "))}${fr.from_freezer.length ? " (rozmrożone)" : ""}</p>
      <ol>${fr.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol></div>` : "";
    inner = `${freshBox}${instructionsHtml(r, `${S.planWeek}:${key}`, isCook ? days : 0)}<p class="sub">Łącznie ok. ${r.time} min. Kliknij krok, żeby go odhaczyć.</p>`;
  } else {
    inner = `<div class="tscroll"><table class="ing"><thead><tr><th></th>${p.persons.map(pe => `<th>${esc(pe.name)}</th>`).join("")}</tr></thead><tbody>
      ${[["kcal", 0, ""], ["Białko", 1, " g"], ["Węglowodany", 2, " g"], ["Tłuszcze", 3, " g"]].map(([l, ix, u]) =>
        `<tr><td>${l}</td>${p.persons.map(pe => `<td><b>${fmt((m.nutrition[pe.id] || [0, 0, 0, 0])[ix])}${u}</b></td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="sub">Porcje są dobrane do dziennego celu kcal każdej osoby.</p>`;
  }
  return `<div class="meal-body">${tabs}<div class="mpanel">${inner}</div>
    <div class="row mactions"><button class="btn small" data-act="swap" data-slot="${si}">↻ Zamień danie</button>
    <button class="btn small" data-act="quick" data-slot="${si}">⚡ Z tego, co mam</button>
    <button class="btn small ghost" data-act="recipe" data-id="${esc(r.id)}">Przepis</button><span class="spacer"></span>${rateHtml(r)}</div></div>`;
}
function planMenu() {
  const p = S.plan;
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>Plan · ${weekLabel(p.week)}</h2>
    <div class="pick">
      <button data-act="refreshplan"><b>⟳ Przelicz porcje</b><br><small>Te same dania, aktualne ustawienia posiłków i cele kcal. Odhaczone zakupy zostają.</small></button>
      <button data-act="plansheet" data-again="1"><b>↻ Ułóż plan od nowa</b><br><small>Nowe dania na cały tydzień – generator albo agent diety.</small></button>
      <a class="pickbtn" href="api/plans/${p.week}/export.md" download="${p.week}.md"><b>⤓ Pobierz plan</b><br><small>Plik Markdown z planem, przepisami i listą zakupów.</small></a>
    </div>
    ${p.agent ? `<details class="info"><summary>🧠 Dlaczego taki plan</summary>${p.agent.wishes ? `<p class="sub">Życzenia: ${esc(p.agent.wishes)}</p>` : ""}<p>${esc(p.agent.summary)}</p>
      ${Object.entries(p.agent.notes || {}).map(([d, n]) => `<p><b>${esc(p.days[+d]?.name || "")}:</b> ${esc(n)}</p>`).join("")}</details>` : ""}
    <details class="info"><summary>Założenia planu</summary><ul>${[...p.assumptions, ...p.warnings].map(a => `<li>${esc(a)}</li>`).join("")}</ul>
      <p class="sub">Ułożony: ${esc(p.generated.replace("T", " "))}${p.refreshed ? ` · przeliczony: ${esc(p.refreshed.replace("T", " "))}` : ""}</p></details>`);
}

const EQUIP_ICON = { piekarnik: "🔥", garnek: "🍲", patelnia: "🍳", blender: "🌀", "bez obróbki": "🔪" };
function taskShort(x) {
  const P = prodMap(), items = Object.entries(x.amounts || {});
  const txt = items.slice(0, 2).map(([k, g]) => `${k} ${amountText(P[k], g)}`).join(", ");
  return txt + (items.length > 2 ? ` +${items.length - 2}` : "");
}
function taskRow(x) {
  return `<div class="trow ${x.checked ? "done" : ""}">
    <button class="check ${x.checked ? "on" : ""}" data-act="preptoggle" data-id="${esc(x.id)}" aria-label="${x.checked ? "Odznacz" : "Odhacz"}: ${esc(x.name)}">${x.checked ? "✓" : ""}</button>
    <details><summary><span class="tn">${EQUIP_ICON[x.equipment] || ""} ${esc(x.name)}</span><span class="tm">~${x.minutes} min</span>
      <small class="ta">${esc(taskShort(x))}</small></summary>
      <ol>${x.steps.map(st => `<li>${esc(st)}</li>`).join("")}</ol>
      <p class="sub">${esc(x.amount_text)}${x.recipes.length ? ` · do: ${esc(x.recipes.map(niceName).join(", "))}` : ""}</p></details></div>`;
}
function prepView(p) {
  return p.prep.sessions.map(ses => {
    const done = ses.tasks.filter(x => x.checked).length;
    const phase = x => x.phase || (["piekarnik", "garnek"].includes(x.equipment) ? "start" : "meanwhile");
    const start = ses.tasks.filter(x => phase(x) === "start"), mid = ses.tasks.filter(x => phase(x) !== "start");
    const byDay = {};
    ses.tasks.forEach(x => x.containers.forEach(c => (byDay[c.day] ||= []).push(c)));
    const pack = Object.keys(byDay).map(Number).sort((a, b) => a - b).map(d => `<div class="pack"><b>${esc(p.days[d].short)} ${shortDate(p.days[d].date)}</b><ul>
      ${byDay[d].map(c => { const parts = c.label.split(" · ");
        return `<li>${c.where === "freezer" ? "🧊" : "📦"} ${esc(parts[0])} <small class="sub">· ${esc(parts[2] || "")} · ${esc(c.who)}</small></li>`; }).join("")}</ul></div>`).join("");
    const sec = (n, title, hint, list) => list.length ? `<div class="card prepsec"><h3><span class="num">${n}</span> ${title}</h3><p class="sub">${hint}</p>${list.map(taskRow).join("")}</div>` : "";
    return `<div class="card prephead"><h2>🧑‍🍳 ${esc(ses.label)}</h2>
        <p>${sessionLine(ses)}</p><div class="bar"><i style="width:${ses.tasks.length ? done / ses.tasks.length * 100 : 0}%"></i></div>
        <p class="sub">${ses.day < 0 ? "Zacznij od kroku 1 – to, co się piecze i gotuje, robi się samo, a Ty w tym czasie robisz krok 2. Stuknij zadanie, żeby zobaczyć kroki." : "Krótka sesja na czwartek–niedzielę: to, co nie dotrwałoby od niedzieli, a nie nadaje się do mrożenia."}</p></div>
      ${sec(1, "Na start", "Piekarnik i garnki – nastaw i zostaw.", start)}
      ${sec(start.length ? 2 : 1, "W międzyczasie", "Patelnia, krojenie, sosy.", mid)}
      <div class="card prepsec"><h3><span class="num">${(start.length ? 1 : 0) + (mid.length ? 1 : 0) + 1}</span> Pakowanie</h3>
        <p class="sub">Przestudzone porcje do pojemników z karteczką dnia. 🧊 = do zamrażarki, 📦 = do lodówki.</p>${pack}</div>`;
  }).join("");
}
function prepPlanHtml(r) {
  const pp = r.prep_plan; if (!pp || !(pp.components?.length || pp.on_day?.steps?.length)) return "";
  return `<details class="info prepinfo"><summary>🧑‍🍳 Meal prep: ${pp.components.length ? pp.components.length + " etap(y) na zapas" : "w całości na świeżo"} · wykończenie ~${pp.on_day.minutes} min</summary>
    ${pp.components.map(c => `<p><b>${EQUIP_ICON[c.equipment] || ""} ${esc(c.name)}</b> <small class="sub">· ${esc(c.products.join(", "))} · lodówka ${c.fridge_days} dni · ${c.freezes ? "mrozi się" : "nie mrozić"}</small></p>
      <ol>${c.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>`).join("")}
    <p><b>⚡ W dniu posiłku</b></p><ol>${pp.on_day.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol></details>`;
}
function splitBanner(p, m, si, isCook) {
  const sp = m.split; if (!sp) return "";
  const who = f => p.persons.map(pe => `${esc(pe.name)} <b>${f(pe)}</b>`).join(", ");
  if (sp.mode === "pieces") {
    return `<div class="splitbox">🥞 <b>${sp.total_units} szt.</b> (${esc(sp.unit)})${isCook && m.batch.length > 1 ? ` na dzień – ugotuj ${sp.total_units * m.batch.length} szt. na ${m.batch.length} dni` : ""}: ${who(pe => sp.counts[pe.id])}</div>`;
  }
  if (isCook && m.batch.length > 1) {
    const per = m.batch.map(d => { const s2 = p.days[d].meals[si].split; return s2?.mode === "shared" ? { d, s2 } : null; }).filter(Boolean);
    const same = per.every(x => x.s2.total_parts === sp.total_parts && p.persons.every(pe => x.s2.parts[pe.id] === sp.parts[pe.id]));
    return `<div class="splitbox">🍲 Ugotuj całość na ${m.batch.length} dni i rozłóż na <b>${m.batch.length} równe pojemniki</b> (po jednym na dzień).
      ${same ? `Każdego dnia podziel pojemnik na <b>${sp.total_parts} równe części</b>: ${who(pe => sp.parts[pe.id])}.`
        : `Pojemnik dziel na równe części: ${per.map(x => `${p.days[x.d].short} – ${x.s2.total_parts} części: ${who(pe => x.s2.parts[pe.id])}`).join("; ")}.`}</div>`;
  }
  return `<div class="splitbox">🍲 Gotujecie razem → podziel na <b>${sp.total_parts === 2 ? "2 równe części" : sp.total_parts + " równych części"}</b>: ${who(pe => sp.parts[pe.id])}</div>`;
}
function swapSheet(si) {
  const p = S.plan, m = p.days[S.day].meals[si], slot = p.slots[si];
  const persons = p.persons.filter(pe => p.targets[pe.id]);
  // Porcje w planie są skalowane do kcal każdej osoby, więc porównujemy makro po przeskalowaniu do tej samej porcji.
  const cur = Object.fromEntries(persons.map(pe => [pe.id, m.nutrition[pe.id] || [0, 0, 0, 0]]));
  const sgn = v => (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(Math.round(v));
  const rows = S.data.recipes.filter(r => r.slots.includes(slot.slot) && r.id !== m.recipe).map(r => {
    const base = macroOf(recipeIngr(r)); if (!base.kcal) return null;
    let score = 0;
    const per = persons.map(pe => {
      const c = cur[pe.id], f = (c[0] || p.targets[pe.id].kcal * slot.share) / base.kcal;
      const d = [base.p * f - c[1], base.c * f - c[2], base.f * f - c[3]];
      score += Math.abs(d[0]) * 1.5 + Math.abs(d[1]) + Math.abs(d[2]) * 1.5;
      return { pe, d, b: base.p * f };
    });
    return { r, per, score, ex: recipeExcluded(r) };
  }).filter(Boolean).sort((a, b) => a.ex - b.ex || a.score - b.score);
  const cls = v => Math.abs(v) < 3 ? "" : v > 0 ? "up" : "down";
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>Zamień: ${esc(slot.name)}</h2>
    <p class="sub">${esc(p.days[S.day].name)}${m.batch ? " – zmiana obejmie wszystkie dni tego meal prepu" : ""}. Porcje i lista zakupów przeliczą się same.</p>
    <p class="sub">Teraz: <b>${esc(niceName(m.name))}</b> · ${persons.map(pe => { const c = cur[pe.id]; return `${esc(pe.name)} B ${Math.round(c[1])} / W ${Math.round(c[2])} / T ${Math.round(c[3])} g`; }).join(" · ")}.
      Dania są ułożone od <b>najbardziej podobnego makro</b>; liczby to różnica w gramach przy tej samej kaloryczności porcji.</p>
    <div class="pick"><button data-act="doswap" data-slot="${si}" data-recipe=""><b>🎲 Losowo</b><br><small>Dowolne pasujące danie</small></button>
    ${rows.map((x, i) => `<button data-act="doswap" data-slot="${si}" data-recipe="${esc(x.r.id)}" ${x.ex ? "disabled style='opacity:.5'" : ""}>
        <b>${esc(niceName(x.r.name))}</b>${i < 3 && !x.ex ? ' <span class="chip pc p0">podobne makro</span>' : ""}<br>
        <small>ok. ${x.r.time} min${x.r.meal_prep ? " · na zapas" : ""}${x.ex ? " · wykluczone przez profil" : ""}</small>
        ${x.ex ? "" : `<span class="mdelta">${x.per.map(({ pe, d }) => `<span>${esc(pe.name)}: <i class="${cls(d[0])}">B ${sgn(d[0])}</i> <i class="${cls(d[1])}">W ${sgn(d[1])}</i> <i class="${cls(d[2])}">T ${sgn(d[2])}</i></span>`).join("")}</span>`}
      </button>`).join("")}</div>`);
}


/* ---------- Szybki zamiennik ---------- */
let Q = null;
function quickSheet(si) { Q = { slot: si, text: "", image: null, products: null, unknown: [], suggestions: [], loading: "", draft: null }; renderQuick(); }
function renderQuick() {
  const p = S.plan, slot = p.slots[Q.slot], ai = S.data.ai || {};
  let h = `<button class="btn ghost icon x" data-act="close">✕</button><h2>⚡ Szybki zamiennik</h2>
    <p class="sub">${esc(p.days[S.day].name)} · ${esc(slot.name)}. Napisz, co masz, albo zrób zdjęcie lodówki.</p>
    <textarea data-q="text" rows="2" placeholder="np. jajka, szpinak, feta, chleb">${esc(Q.text)}</textarea>
    <div class="row" style="margin-top:8px">
      <label class="btn small" ${ai.available ? "" : `title="${esc(ai.reason || "")}"`} style="${ai.available ? "" : "opacity:.5;pointer-events:none"}">📷 Zdjęcie
        <input type="file" accept="image/*" capture="environment" data-q="image" hidden></label>
      ${Q.image ? `<img src="${Q.image}" alt="" style="height:40px;border-radius:8px"> <button class="btn ghost small" data-act="qnoimg">usuń</button>` : ""}
      <span class="spacer"></span><button class="btn primary small" data-act="qsearch" ${Q.loading ? "disabled" : ""}>${Q.loading === "search" ? "Szukam…" : "Szukaj"}</button></div>
    ${ai.available ? "" : `<p class="sub" style="margin:6px 0 0">Zdjęcia i nowe dania wymagają klucza API. ${esc(ai.reason || "")}</p>`}`;
  if (Q.products) {
    h += `<h3 style="font-size:14px;margin:16px 0 6px">Masz w domu</h3><div class="chips">
      ${Q.products.map((n, i) => `<span class="chip pc p0">${esc(n)} <b data-act="qrm" data-i="${i}" style="cursor:pointer">✕</b></span>`).join("") || '<span class="sub">nic nie rozpoznano</span>'}
      ${Q.unknown.map(n => `<span class="chip" title="Brak w bazie produktów">${esc(n)}?</span>`).join("")}</div>`;
    h += `<h3 style="font-size:14px;margin:16px 0 6px">Propozycje z bazy</h3><div class="pick">${Q.suggestions.map(s => {
      const need = s.have.length + s.missing.length;
      return `<button data-act="doswap" data-slot="${Q.slot}" data-recipe="${esc(s.recipe)}"><b>${esc(s.name)}</b> <span class="chip ${s.missing.length ? "" : "pc p0"}">${s.have.length}/${need}</span><br>
        <small>ok. ${s.time} min${s.missing.length ? " · brakuje: " + esc(s.missing.join(", ")) : " · masz wszystko"}</small></button>`; }).join("") || '<p class="sub">Żadne danie z bazy nie pasuje.</p>'}</div>`;
    if (ai.available) h += `<div class="row" style="margin-top:12px"><button class="btn small" data-act="qinvent" ${Q.loading ? "disabled" : ""}>${Q.loading === "invent" ? "Wymyślam…" : "✨ Zaproponuj nowe danie (AI)"}</button></div>`;
  }
  if (Q.draft) {
    const n = nutr(Object.fromEntries(Q.draft.ingredients.map(i => [i.product, i.grams])));
    h += `<div class="prodform"><b>${esc(Q.draft.name)}</b> <small class="sub">ok. ${Q.draft.time} min · porcja bazowa ${fmt(n[0])} kcal</small>
      <p class="sub" style="margin:6px 0">${Q.draft.ingredients.map(i => `${esc(i.product)} ${i.grams} g`).join(" · ")}</p>
      ${instructionsHtml(Q.draft, "quick")}
      <button class="btn small primary" data-act="qaccept">Dodaj do przepisów i wybierz</button></div>`;
  }
  openModal(h);
}
async function quickSearch() {
  Q.loading = "search"; renderQuick();
  try {
    const body = { day: S.day, slot: Q.slot, text: Q.text };
    if (Q.products && !Q.image) { body.items = [...Q.products, ...Q.text.split(/[,;\n]/)]; }
    if (Q.image) body.image = Q.image;
    const out = await api(`api/plans/${S.week}/quick`, body);
    Object.assign(Q, { products: out.products, unknown: out.unknown, suggestions: out.suggestions, image: null, text: "" });
  } catch (e) { toast(e.message, true); }
  Q.loading = ""; if (Q) renderQuick();
}
async function quickResearch() {
  Q.loading = "search"; renderQuick();
  try {
    const out = await api(`api/plans/${S.week}/quick`, { day: S.day, slot: Q.slot, items: Q.products });
    Object.assign(Q, { products: out.products, suggestions: out.suggestions });
  } catch (e) { toast(e.message, true); }
  Q.loading = ""; renderQuick();
}
function shrinkImage(file) {
  return new Promise((ok, fail) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, 1280 / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
      ok(c.toDataURL("image/jpeg", 0.82));
    };
    img.onerror = () => fail(new Error("Nie udało się wczytać zdjęcia"));
    img.src = url;
  });
}

/* ---------- Widok: Zakupy ---------- */
function viewShop() {
  let h = weekHead("Zakupy");
  const p = S.plan;
  if (!p) return h + `<div class="card empty"><div class="big">🛒</div><p>Brak planu na ten tydzień, więc nie ma listy zakupów.</p>
    <a class="btn primary" href="#/tydzien">Przejdź do planu</a></div>`;
  const items = p.shopping.items, done = items.filter(i => i.checked), left = items.filter(i => !i.checked);
  h += `<div class="card progress"><div class="bar"><i style="width:${items.length ? done.length / items.length * 100 : 0}%"></i></div>
    <b>${done.length}/${items.length}</b></div>
    <form class="add" data-form="additem"><input name="text" placeholder="Dodaj własną pozycję, np. papier do pieczenia" autocomplete="off">
    <button class="btn primary">Dodaj</button></form>`;
  if (!left.length && items.length) h += `<div class="card empty"><div class="big">🎉</div><p>Wszystko kupione!</p></div>`;
  const groups = {};
  left.forEach(i => (groups[i.dept] ??= []).push(i));
  const order = [...DEPT_ORDER, ...Object.keys(groups).filter(d => !DEPT_ORDER.includes(d))];
  for (const d of order) {
    if (!groups[d]) continue;
    h += `<div class="dept"><h3>${esc(d)}</h3><div class="items">${groups[d].sort((a, b) => a.name.localeCompare(b.name, "pl")).map(itemHtml).join("")}</div></div>`;
  }
  if (done.length) {
    const sorted = [...done].sort((a, b) => (b.checked_at || "").localeCompare(a.checked_at || ""));
    h += `<details class="bought" ${S.boughtOpen ? "open" : ""}><summary>${CHEVRON} Kupione (${done.length})</summary>
      <div class="items">${sorted.map(itemHtml).join("")}</div></details>
      <div class="row" style="margin-top:14px"><button class="btn small" data-act="clearShop">Odznacz wszystko i usuń kupione własne pozycje</button></div>`;
  }
  return h;
}
function itemHtml(i) {
  return `<div class="item ${i.checked ? "done" : ""}" data-act="toggle" data-id="${esc(i.id)}">
    <span class="box">${CHECK}</span>
    <span class="nm">${esc(i.name)}${i.checked && i.checked_by ? `<span class="by">kupił(a): ${esc(i.checked_by)}</span>` : ""}</span>
    ${i.extra && !i.checked ? `<span class="amt extra" title="Plan się zmienił – kupiono już ${esc(i.bought_text || "")}">dokup +${esc(i.extra_text)}<small>${esc(i.bought_text || "")} już masz</small></span>`
      : `<span class="amt">${esc(i.amount_text || "")}</span>`}
    ${i.custom ? `<button class="btn ghost icon" data-act="rmitem" data-id="${esc(i.id)}" aria-label="Usuń">✕</button>` : ""}</div>`;
}
async function toggleItem(el) {
  const id = el.dataset.id, it = S.plan.shopping.items.find(i => i.id === id);
  if (!it) return;
  const checked = !it.checked;
  el.classList.toggle("done", checked); el.classList.add("leaving");
  setTimeout(async () => {
    it.checked = checked; it.checked_at = checked ? new Date().toISOString().slice(0, 19) : null;
    render();
    try { setPlan(await api(`api/plans/${S.week}/shopping`, { op: "toggle", id, checked })); if (S.view === "zakupy") render(); }
    catch (e) { toast("Nie zapisano: " + e.message, true); await loadPlan(S.week); render(); }
  }, 220);
}

/* ---------- Widok: Przepisy ---------- */
const facts = r => r.nutrition?.per_portion ? r.nutrition.per_portion : (n => ({ kcal: n[0], protein: n[1], carbs: n[2], fat: n[3] }))(nutr(recipeIngr(r)));
function rateHtml(r) {
  const rt = r.rating || {};
  return `<span class="rate"><span class="sub">Smakowało?</span><button class="btn small ghost" data-act="rate" data-id="${esc(r.id)}" data-d="1" title="Tak – częściej w planach" aria-label="Smakowało">👍${rt.up ? " " + rt.up : ""}</button>
    <button class="btn small ghost" data-act="rate" data-id="${esc(r.id)}" data-d="-1" title="Nie – rzadziej w planach" aria-label="Nie smakowało">👎${rt.down ? " " + rt.down : ""}</button></span>`;
}
function addSheet() {
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>Dodaj przepis</h2>
    <div class="pick">
      <button data-act="newrecipe"><b>✍️ Wpisz ręcznie</b><br><small>Nazwa, składniki z gramaturą i kroki.</small></button>
      <button data-act="textsheet"><b>✨ Wklej tekst</b><br><small>Agent przepisów rozpozna składniki i kroki z dowolnego tekstu (AI).</small></button>
      <button data-act="addbook"><b>📚 Z książki kucharskiej</b><br><small>PDF z tekstem, EPUB lub MOBI – agent wyciągnie wszystkie przepisy (AI).</small></button>
    </div>`);
}
function recipeTabs() {
  const n = S.data.inbox_count || 0;
  return `<div class="seg">${[["lib", "Biblioteka"], ["inbox", `Do przejrzenia${n ? ` <b class="cnt">${n}</b>` : ""}`], ["books", "Książki"]]
    .map(([k, v]) => `<button class="${S.rtab === k ? "active" : ""}" data-act="rtab" data-k="${k}">${v}</button>`).join("")}</div>`;
}
function viewRecipes() {
  const head = `<div class="head"><div><h1>Przepisy</h1><p class="sub">${S.data.recipes.length} dań · ${S.data.products.length} produktów w bazie</p></div>
    <div class="row"><button class="btn primary" data-act="addsheet">+ Dodaj przepis</button></div></div>${recipeTabs()}`;
  if (S.rtab === "inbox") return head + viewInbox();
  if (S.rtab === "books") return head + viewBooks();
  const q = S.rsearch.toLowerCase();
  const list = S.data.recipes.filter(r => (!S.rfilter || r.slots.includes(S.rfilter)) &&
    (!q || r.name.toLowerCase().includes(q) || r.ingredients.some(i => i.product.includes(q)) || (r.source?.book || "").toLowerCase().includes(q)))
    .sort((a, b) => a.name.localeCompare(b.name, "pl"));
  const old = S.data.recipes.filter(r => !Array.isArray(r.steps) || !("prep_notes" in r)).length;
  const nIn = S.data.inbox_count || 0;
  const note = (nIn ? `<div class="banner soft"><span><b>${nIn}</b> ${nIn === 1 ? "przepis czeka" : "przepisów czeka"} na przejrzenie – sprawdź i dodaj do biblioteki.</span><button class="btn small primary" data-act="rtab" data-k="inbox">Przejrzyj</button></div>` : "") +
    (old ? `<div class="alert info">${old} przepisów ma jeszcze skrócony opis bez kroków. <a href="#/ustawienia" data-act="gostab" data-k="ai">Rozpisz instrukcje →</a></div>` : "");
  return head + note + `<input class="search" type="search" placeholder="Szukaj dania, składnika albo książki…" value="${esc(S.rsearch)}" data-input="rsearch">
    <div class="filters">${[["", "Wszystkie"], ...Object.entries(SLOTS)].map(([k, v]) => `<button class="${S.rfilter === k ? "active" : ""}" data-act="rfilter" data-k="${k}">${v}</button>`).join("")}</div>
    <div class="grid-r">${list.map(r => { const n = facts(r); const hits = exclusionHits(r); const rt = r.rating || {};
      const book = bookTitle(r.source?.book), sv = servingOf(r), tags = recipeTags(r);
      const frz = (r.prep_plan?.components || []).some(c => c.freezes);
      return `<div class="card rcard ${hits.length ? "conflict" : ""}" data-act="recipe" data-id="${esc(r.id)}">${slotIcon(r)}<div class="rmain"><h3>${esc(niceName(r.name))}</h3>
        <div class="meta">${r.slots.map(s => SLOTS[s]).join(" · ")} · ${r.time} min${r.prep_plan?.components?.length ? ` · wykończenie ~${r.prep_plan.on_day.minutes} min` : ""}</div>
        <div class="chips">${hits.length ? `<span class="chip ex">wykluczone: ${esc(hits.join(", "))}</span>` : ""}
          ${sv.mode === "shared" ? '<span class="chip">🍲 garnek</span>' : sv.mode === "pieces" ? `<span class="chip">🥞 ${esc(sv.unit)}</span>` : ""}${frz ? '<span class="chip">🧊 mrozi się</span>' : ""}
          ${rt.up ? `<span class="chip">👍 ${rt.up}</span>` : ""}${rt.down ? `<span class="chip">👎 ${rt.down}</span>` : ""}
          ${book ? `<span class="chip book" title="${esc(book)}">📖 ${esc(book)}</span>` : ""}</div>
        ${tags.length ? `<div class="allergens">zawiera: ${tags.join(", ")}${r.nutrition?.estimated_products?.length ? ' · <span title="Część wartości odżywczych to szacunek">~ kcal szacunkowe</span>' : ""}</div>` : ""}</div>
        <div class="rkcal"><b>${fmt(n.kcal)}</b><small>kcal / porcja</small><span>B ${Math.round(n.protein)} · W ${Math.round(n.carbs)} · T ${Math.round(n.fat)}</span></div></div>`; }).join("") || '<p class="sub">Brak wyników.</p>'}</div>`;
}
const SLOT_ICON = { sniadanie: "🍳", przekaska: "🍎", obiad: "🍲", kolacja: "🌙" };
function slotIcon(r) {
  const s0 = (r.slots || [])[0] || "obiad";
  return `<div class="ricon s-${s0}" aria-hidden="true">${SLOT_ICON[s0] || "🍽️"}</div>`;
}
function aiNote() {
  const a = S.data.ai || {};
  return a.available ? "" : `<div class="alert">AI jest wyłączone: ${esc(a.reason || "brak konfiguracji")}</div>`;
}
function viewInbox() {
  if (S.tabErr) return tabError();
  if (!S.inbox) return '<div class="loading">Wczytywanie…</div>';
  if (!S.inbox.length) return `<div class="card empty"><div class="big">📥</div><p>Brak przepisów do przejrzenia. Dodaj przepis z tekstu albo zaimportuj książkę.</p></div>`;
  const clean = S.inbox.filter(d => !d.warnings.length).length;
  return `<div class="row" style="margin-bottom:12px"><span class="sub">${S.inbox.length} szkiców od agenta przepisów. Kalorie policzyła aplikacja z bazy produktów.</span><span class="spacer"></span>
    ${clean ? `<button class="btn primary small" data-act="acceptclean">Zatwierdź wszystkie bez uwag (${clean})</button>` : ""}</div>
    <div class="grid-r">${S.inbox.map(d => { const r = d.recipe, n = d.nutrition.per_portion;
      return `<div class="card rcard draft">${slotIcon(r)}<div class="rmain"><h3>${esc(niceName(r.name))}</h3>
        <div class="meta">${r.slots.map(s => SLOTS[s]).join(" · ")} · ${r.time} min${r.meal_prep ? " · na zapas" : ""}${d.servings_source ? ` · w źródle ${d.servings_source} porcji` : ""}</div>
        <div class="chips">${r.source?.book ? `<span class="chip book">📖 ${esc(r.source.book)}</span>` : ""}${r.source?.chapter ? `<span class="chip">${esc(r.source.chapter)}</span>` : ""}
          ${d.new_products.map(p => `<span class="chip tag" title="${p.kcal} kcal/100 g – wartości szacunkowe">nowy: ${esc(p.name)}</span>`).join("")}</div>
        <div class="meta"><b>${fmt(n.kcal)} kcal</b> · B ${Math.round(n.protein)} / W ${Math.round(n.carbs)} / T ${Math.round(n.fat)} · ${d.nutrition.grams} g</div>
        <div class="meta">${r.ingredients.map(i => `${esc(i.product)} ${i.grams} g`).join(" · ")}</div>
        ${d.warnings.length ? `<ul class="warnlist">${d.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
        ${d.blocking ? `<p class="blocknote">Przed zatwierdzeniem uzupełnij brakujące elementy w edycji.</p>` : ""}
        <div class="row">${d.blocking ? `<button class="btn small primary" data-act="editdraft" data-id="${d.id}">Popraw i zatwierdź</button>`
          : `<button class="btn small primary" data-act="accept" data-id="${d.id}">Zatwierdź</button>
          <button class="btn small" data-act="editdraft" data-id="${d.id}">Edytuj</button>`}<span class="spacer"></span>
          <button class="btn small ghost danger" data-act="reject" data-id="${d.id}">Odrzuć</button></div></div></div>`; }).join("")}</div>`;
}
const STATUS = { new: "nowy", ready: "gotowy do importu", importing: "importowanie…", paused: "wstrzymany", done: "zaimportowany", error: "błąd" };
function viewBooks() {
  if (S.tabErr) return tabError();
  if (!S.books) return '<div class="loading">Wczytywanie…</div>';
  const mb = b => (b / 1048576).toFixed(1).replace(".", ",") + " MB";
  return aiNote() + `<div class="card"><h2>Twoje książki kucharskie <small>PDF z tekstem · EPUB · MOBI/AZW3</small></h2>
    <p class="sub">Wrzuć pliki do folderu <code>books/</code> w katalogu danych (na Home Assistant: udział Samba <code>addon_configs</code> → <code>…_diet_planner/books</code>) albo wgraj je tutaj.
    Agent przepisów wyciągnie z nich przepisy, przeliczy składniki na produkty z bazy i odda je do przejrzenia. Pliki z DRM nie dadzą się odczytać.</p>
    <label class="btn"><input type="file" accept=".pdf,.epub,.mobi,.azw3,.azw" data-upload hidden>⤒ Wgraj plik</label></div>
    ${S.books.map(b => { const pct = b.chunks_total ? Math.round(b.chunks_done / b.chunks_total * 100) : 0; const e = b.estimate;
      return `<div class="card book"><h2>${esc(b.title)} <small>${esc(STATUS[b.status] || b.status)}</small></h2>
        <p class="sub">${esc(b.file)} · ${mb(b.size)}${b.chunks_total ? ` · fragmenty ${b.chunks_done}/${b.chunks_total}` : ""}${b.drafts ? ` · ${b.drafts} przepisów do przejrzenia` : ""}</p>
        ${b.chunks_total ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}
        ${e ? `<p class="sub">Szacowany koszt importu: <b>${usdTxt(e.usd)}</b> (${e.chunks} fragm., ~${fmt(e.tokens_in / 1000)} tys. tokenów, model ${esc(e.model)})</p>` : ""}
        ${b.errors.length ? `<details class="info"><summary>Błędy (${b.errors.length})</summary><ul>${b.errors.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
        <div class="row">${b.running ? `<button class="btn small" data-act="bcancel">⏸ Wstrzymaj</button>`
          : `${!e ? `<button class="btn small" data-act="bestimate" data-f="${esc(b.file)}">Oszacuj koszt</button>` : ""}
             ${b.status !== "done" && b.status !== "error" ? `<button class="btn small primary" data-act="bimport" data-f="${esc(b.file)}" ${S.data.ai?.available ? "" : "disabled"}>${b.status === "paused" ? "▶ Wznów import" : "Importuj przepisy"}</button>` : ""}`}
          <span class="spacer"></span>${b.running ? "" : `<button class="btn small ghost danger" data-act="bdelete" data-f="${esc(b.file)}">Usuń plik</button>`}</div></div>`; }).join("") ||
      '<div class="card empty"><div class="big">📚</div><p>Brak książek w folderze.</p></div>'}`;
}
async function loadRecipeTab() {
  S.tabErr = null;
  try {
    if (S.rtab === "inbox") { S.inbox = (await api("api/inbox")).items; S.data.inbox_count = S.inbox.length; }
    if (S.rtab === "books") { S.books = (await api("api/books")).books; }
  } catch (e) {
    S.tabErr = e.status === 404 ? "Serwer działa na starej wersji – zamknij go i uruchom ponownie start.command." : e.message;
  }
}
function tabError() {
  return `<div class="card empty"><div class="big">⚠️</div><p>Nie udało się wczytać listy.</p><p class="sub">${esc(S.tabErr)}</p>
    <button class="btn small" data-act="rtab" data-k="${S.rtab}">Spróbuj ponownie</button></div>`;
}
function textSheet() {
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>✨ Przepis z tekstu</h2>
    <p class="sub">Wklej przepis (np. z książki, strony albo notatki). Agent przepisów rozpozna składniki, przeliczy je na gramy na porcję i doda szkic do zakładki „Do przejrzenia”. Kalorie policzy aplikacja.</p>
    ${aiNote()}<textarea id="t-text" rows="10" placeholder="Nazwa, składniki z ilościami, sposób przygotowania…"></textarea>
    <label class="l" style="margin-top:10px">Źródło (opcjonalnie)</label><input id="t-src" placeholder="np. tytuł książki">
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" data-act="close">Anuluj</button>
    <button class="btn primary" data-act="dotext" ${S.data.ai?.available ? "" : "disabled"}>Utwórz szkic</button></div>`);
}
function planSheet(again) {
  const a = S.data.ai || {};
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>${again ? "Ułóż plan od nowa" : "Ułóż plan"} · ${S.week.replace("-", " ")}</h2>
    ${again ? '<p class="sub">Obecny plan i odhaczenia na liście zakupów zostaną zastąpione.</p>' : ""}
    <label class="l" style="margin-top:8px">Meal prep w tym tygodniu</label>
    <div class="filters prepmodes">${[["none", "Bez meal prepu"], ["every2", "Obiady co 2 dni"], ["weekly", "Cały tydzień w niedzielę"]].map(([k, v]) =>
      `<button class="${(S.prepMode ||= S.plan?.prep_mode || S.data.prep_mode_default || "every2") === k ? "active" : ""}" data-act="prepmode" data-m="${k}">${v}</button>`).join("")}</div>
    <p class="sub">„Cały tydzień”: w niedzielę przygotowujesz bazy, sosy i mięsa na cały tydzień (część trafia do zamrażarki albo na krótką sesję w środę), a w dniu posiłku zostaje szybkie wykończenie.</p>
    <div class="preplimit" ${S.prepMode === "weekly" ? "" : "hidden"}><label class="l">Ile czasu masz w niedzielę?</label>
      <div class="filters">${[[60, "1 h"], [90, "1,5 h"], [120, "2 h"], [180, "3 h"], [0, "Bez limitu"]].map(([k, v]) =>
        `<button class="${(S.prepLimit ??= S.plan?.prep_limit ?? 120) === k ? "active" : ""}" data-act="preplimit" data-m="${k}">${v}</button>`).join("")}</div>
      <p class="sub">Plan zamieni najbardziej pracochłonne dania na prostsze, żeby sesja zmieściła się w tym czasie.</p></div>
    <div class="pick"><button data-act="dogenerate" data-mode="code"><b>⚙️ Generator</b><br><small>Szybko i bez AI: losuje dania z biblioteki według ustawień, alergii i ocen.</small></button></div>
    <div class="card" style="margin-top:12px"><b>🧠 Agent diety (AI)</b><p class="sub">Dobiera dania pod Wasze preferencje, sezon, oceny 👍/👎, historię i życzenia, chętnie z Twoich książek. Porcje i kalorie dalej liczy aplikacja, a alergie są pilnowane przez kod.</p>
      ${a.available ? "" : `<div class="alert">${esc(a.reason || "AI niedostępne")}</div>`}
      <textarea id="g-wishes" rows="3" placeholder="Życzenia na ten tydzień, np. więcej ryb, w piątek pizza, coś z dyni"></textarea>
      <div class="row" style="margin-top:10px"><span class="sub">Model: ${esc(a.tasks?.diet?.label || a.model || "—")}${a.tasks?.diet?.fallback_label ? ` (zapasowy: ${esc(a.tasks.diet.fallback_label)})` : ""} · trwa ok. 30–90 s</span><span class="spacer"></span>
      <button class="btn primary" data-act="dogenerate" data-mode="agent" ${a.available ? "" : "disabled"}>Ułóż z agentem</button></div></div>`);
}
/* ---------- Instrukcje przepisu ---------- */
function textList(v) {
  return (Array.isArray(v) ? v : String(v || "").split("\n")).map(x => String(x || "").replace(/^\s*(?:\d+[.)]|[-•*])\s*/, "").trim()).filter(Boolean);
}
function stepsOf(r) {
  if (Array.isArray(r.steps)) return textList(r.steps);
  const t = String(r.steps || "").trim();
  if (t.includes("\n")) return textList(t);
  return t.split(/(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9])/).map(x => x.trim()).filter(Boolean);
}
const doneSteps = new Set();  // przekreślone kroki – tylko w tej karcie przeglądarki
function instructionsHtml(r, key, cookDays = 0) {
  const prep = textList(r.prep_notes), steps = stepsOf(r), storage = r.meal_prep ? String(r.storage || "").trim() : "";
  const k = key || r.id || r.name;
  return `<div class="instr">
    ${prep.length || cookDays > 1 ? `<div class="before"><b>🔥 Przed gotowaniem</b><ul>
      ${cookDays > 1 ? `<li>Gotujesz na zapas – ilości z kolumny „Ugotuj na ${cookDays} dni”.</li>` : ""}
      ${prep.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
    ${steps.length ? `<b class="ih">Przygotowanie</b><ol class="stepl">${steps.map((x, i) => { const id = `${k}#${i}`;
      return `<li class="${doneSteps.has(id) ? "done" : ""}" data-act="step" data-s="${esc(id)}" title="Kliknij, aby odhaczyć">${esc(x)}</li>`; }).join("")}</ol>` : ""}
    ${storage ? `<div class="storage"><b>🧊 Przechowywanie i odgrzewanie</b><p>${esc(storage)}</p></div>` : ""}
    ${!Array.isArray(r.steps) || !("prep_notes" in r) ? `<p class="sub old">Opis w starym, skrótowym formacie – rozpiszesz go w Ustawieniach → Instrukcje, porcjowanie i meal prep.</p>` : ""}
  </div>`;
}
/* ---------- Porcjowanie ---------- */
const SERVING = { shared: "wspólny garnek – dzielony na równe części", pieces: "w sztukach", individual: "każdy składa swoją porcję" };
function servingOf(r) {
  const sv = r.serving || {};
  if (SERVING[sv.mode] && (sv.mode !== "pieces" || (sv.unit && sv.units_per_portion > 0))) return { ...sv, auto: false };
  const n = (r.name || "").toLowerCase();
  const shared = ["zupa", "krem z", "gulasz", "zapiekank", "curry", "chili", "makaron", "spaghetti", "pęczotto", "risotto", "leczo", "potrawk", "gołąbk", "bigos", "fasolka po", "duszon", "w sosie"].some(w => n.includes(w))
    || (r.meal_prep && r.slots.some(s => s === "obiad" || s === "kolacja"));
  return { mode: shared ? "shared" : "individual", auto: true };
}
function servingLine(r) {
  const sv = servingOf(r);
  return `<p class="sub">Porcjowanie: <b>${sv.mode === "pieces" ? `w sztukach – ${g1(sv.units_per_portion)} × ${esc(sv.unit)} na porcję` : SERVING[sv.mode]}</b>${sv.auto ? ' <span class="chip" title="Zgadnięte po nazwie – agent przepisów ustali to dokładnie (Ustawienia → Instrukcje i porcjowanie)">auto</span>' : ""}</p>`;
}

/* ---------- Makro ---------- */
const g1 = x => (Math.round(x * 10) / 10).toString().replace(".", ",");
function macroOf(ingr) {  // {kcal, p, c, f, fib, fibMissing[]} dla {produkt: gramy}
  const P = prodMap(), m = { kcal: 0, p: 0, c: 0, f: 0, fib: 0, fibMissing: [] };
  for (const [k, g] of Object.entries(ingr)) { const x = P[k]; if (!x) continue;
    m.kcal += x.kcal * g / 100; m.p += x.protein * g / 100; m.c += x.carbs * g / 100; m.f += x.fat * g / 100;
    if (x.fiber == null) m.fibMissing.push(k); else m.fib += x.fiber * g / 100; }
  return m;
}
function splitBar(m) {
  const e = m.p * 4 + m.c * 4 + m.f * 9 || 1, pc = [m.p * 4, m.c * 4, m.f * 9].map(v => Math.round(v / e * 100));
  return `<div class="split" role="img" aria-label="Energia: białko ${pc[0]}%, węglowodany ${pc[1]}%, tłuszcze ${pc[2]}%">
    <i class="sp" style="width:${pc[0]}%"></i><i class="sc" style="width:${pc[1]}%"></i><i class="sf" style="width:${pc[2]}%"></i></div>
    <div class="split-leg"><span><i class="sp"></i>Białko ${pc[0]}%</span><span><i class="sc"></i>Węglowodany ${pc[1]}%</span><span><i class="sf"></i>Tłuszcze ${pc[2]}%</span></div>`;
}
function macroTable(r) {
  const P = prodMap(), tot = macroOf(recipeIngr(r)), grams = r.ingredients.reduce((a, i) => a + i.grams, 0) || 1;
  const fibCell = (m) => m.fibMissing.length ? (m.fib ? `≥${g1(m.fib)}` : "–") : g1(m.fib);
  return `<div class="tscroll"><table class="ing macro"><thead><tr><th>Składnik</th><th>Ilość</th><th>kcal</th><th title="Białko">B</th><th title="Węglowodany">W</th><th title="Tłuszcze">T</th><th title="Błonnik">Bł</th></tr></thead><tbody>
    ${r.ingredients.map(i => { const m = macroOf({ [i.product]: i.grams }); const est = P[i.product]?.estimated || P[i.product]?.fiber_estimated;
      return `<tr><td>${esc(i.product)}${est ? ' <small class="sub" title="Wartości szacunkowe">~</small>' : ""}</td><td>${amountText(P[i.product], i.grams)}</td><td>${fmt(m.kcal)}</td><td>${g1(m.p)}</td><td>${g1(m.c)}</td><td>${g1(m.f)}</td><td>${fibCell(m)}</td></tr>`; }).join("")}
    <tr class="sum"><td><b>Razem</b></td><td><b>${fmt(grams)} g</b></td><td><b>${fmt(tot.kcal)}</b></td><td><b>${g1(tot.p)}</b></td><td><b>${g1(tot.c)}</b></td><td><b>${g1(tot.f)}</b></td><td><b>${fibCell(tot)}</b></td></tr>
    <tr class="per100"><td>na 100 g</td><td></td><td>${fmt(tot.kcal / grams * 100)}</td><td>${g1(tot.p / grams * 100)}</td><td>${g1(tot.c / grams * 100)}</td><td>${g1(tot.f / grams * 100)}</td><td>${tot.fibMissing.length ? "–" : g1(tot.fib / grams * 100)}</td></tr>
    </tbody></table></div>${splitBar(tot)}
    ${tot.fibMissing.length ? `<p class="sub">Brak danych o błonniku: ${esc(tot.fibMissing.join(", "))}. <button class="btn small ghost" data-act="fillfiber">Uzupełnij błonnik (AI)</button></p>` : ""}
    <p class="sub">~ = wartości szacunkowe · gramy w stanie surowym/suchym</p>`;
}
function recipeSheet(id) {
  const r = recipeMap()[id], P = prodMap(), n = nutr(recipeIngr(r));
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>${esc(niceName(r.name))}</h2>
    <p class="sub">${r.slots.map(s => SLOTS[s]).join(" · ")} · ok. ${r.time} min${r.meal_prep ? " · nadaje się na zapas" : ""}</p>
    ${bookTitle(r.source?.book) ? `<p class="sub">📖 ${esc(bookTitle(r.source.book))}${r.source.chapter ? " · " + esc(r.source.chapter) : ""}</p>` : ""}
    <p class="sub">Gramatury bazowe – w planie są automatycznie skalowane do kalorii każdej osoby.${r.nutrition?.estimated_products?.length ? ` Szacunkowe wartości: ${esc(r.nutrition.estimated_products.join(", "))}.` : ""}</p>
    ${servingLine(r)}
    ${macroTable(r)}
    ${instructionsHtml(r)}
    ${prepPlanHtml(r)}
    <div class="row"><button class="btn" data-act="editrecipe" data-id="${esc(r.id)}">Edytuj</button>${rateHtml(r)}<span class="spacer"></span>
    <button class="btn danger" data-act="delrecipe" data-id="${esc(r.id)}">Usuń</button></div>`);
}
let draft = null;
function recipeEditor(r) {
  draft = r ? JSON.parse(JSON.stringify(r)) : { name: "", slots: ["obiad"], time: 20, meal_prep: false, ingredients: [{ product: "", grams: "" }], steps: [], prep_notes: [], storage: "" };
  draft.__hadPrep = Array.isArray(draft.prep_notes) && "prep_notes" in (r || {});
  draft.steps = stepsOf(draft).join("\n");
  draft.prep_notes = textList(draft.prep_notes).join("\n");
  draft.storage = draft.storage || "";
  const sv0 = servingOf(draft);
  draft.serving = { mode: sv0.mode, unit: sv0.unit || "", units_per_portion: sv0.units_per_portion || "" };
  draft.__svAuto = sv0.auto ? sv0.mode : null;
  renderEditor();
}
function renderEditor() {
  const n = nutr(Object.fromEntries(draft.ingredients.filter(i => i.product).map(i => [i.product, num(i.grams) || 0])));
  openModal(`<button class="btn ghost icon x" data-act="close">✕</button><h2>${draft.id ? "Edytuj przepis" : "Nowy przepis"}</h2>
    <div class="fgrid" style="margin-top:12px"><div class="field wide"><label class="l">Nazwa</label><input data-d="name" value="${esc(draft.name)}"></div>
    <div class="field"><label class="l">Czas (min)</label><input inputmode="numeric" data-d="time" value="${esc(draft.time)}"></div>
    <div class="field"><label class="l">Posiłek</label><div class="checks">${Object.entries(SLOTS).map(([k, v]) => `<label><input type="checkbox" data-dslot="${k}" ${draft.slots.includes(k) ? "checked" : ""}>${v}</label>`).join("")}</div></div>
    <div class="field wide checks"><label><input type="checkbox" data-d="meal_prep" ${draft.meal_prep ? "checked" : ""}> Nadaje się do gotowania na zapas (do odgrzania)</label></div>
    <div class="field"><label class="l">Porcjowanie</label><select data-sv="mode">${Object.entries({ shared: "Wspólny garnek (części)", pieces: "W sztukach", individual: "Każdy osobno" }).map(([k, v]) => `<option value="${k}" ${draft.serving.mode === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>
    ${draft.serving.mode === "pieces" ? `<div class="field"><label class="l">Sztuka (np. placek)</label><input data-sv="unit" value="${esc(draft.serving.unit || "")}"></div>
    <div class="field"><label class="l">Sztuk na porcję bazową</label><input inputmode="decimal" data-sv="units_per_portion" value="${esc(draft.serving.units_per_portion || "")}"></div>` : ""}</div>
    <h3 style="font-size:14px;margin:16px 0 8px">Składniki (porcja bazowa)</h3>
    <datalist id="prods">${S.data.products.map(p => `<option value="${esc(p.name)}">`).join("")}</datalist>
    ${draft.ingredients.map((i, k) => `<div class="ing-row"><input list="prods" placeholder="Produkt" data-ing="${k}" data-f="product" value="${esc(i.product)}">
      <input inputmode="decimal" placeholder="g" data-ing="${k}" data-f="grams" value="${esc(i.grams)}">
      <button class="btn ghost icon" data-act="rming" data-k="${k}">✕</button></div>`).join("")}
    <div class="row"><button class="btn small" data-act="adding">+ Składnik</button><button class="btn small ghost" data-act="newprod">+ Nowy produkt do bazy</button>
      <span class="spacer"></span><small class="sub">${fmt(n[0])} kcal · B ${Math.round(n[1])} / W ${Math.round(n[2])} / T ${Math.round(n[3])}</small></div>
    <div id="prodform"></div>
    <label class="l" style="margin-top:14px">🔥 Przed gotowaniem <small class="sub">– jedna uwaga w linii, np. „Rozgrzej piekarnik do 200°C”</small></label>
    <textarea data-d="prep_notes" rows="3" placeholder="Rozgrzej piekarnik do 200°C (góra–dół)&#10;Wyłóż blachę papierem do pieczenia">${esc(draft.prep_notes)}</textarea>
    <label class="l" style="margin-top:14px">Przygotowanie krok po kroku <small class="sub">– jeden krok w linii, bez gramatur</small></label>
    <textarea data-d="steps" rows="8" placeholder="Pokrój cebulę w kostkę.&#10;Zeszklij ją na średnim ogniu, ok. 3 min.">${esc(draft.steps)}</textarea>
    ${draft.meal_prep ? `<label class="l" style="margin-top:14px">🧊 Przechowywanie i odgrzewanie</label>
    <textarea data-d="storage" rows="2" placeholder="W szczelnym pojemniku w lodówce do 2 dni. Odgrzej na patelni z łyżką wody.">${esc(draft.storage)}</textarea>` : ""}
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" data-act="close">Anuluj</button><button class="btn primary" data-act="saverecipe">Zapisz przepis</button></div>`);
}
function prodForm() {
  $("#prodform").innerHTML = `<div class="prodform"><b>Nowy produkt</b> <small class="sub">wartości na 100 g</small>
    <div class="fgrid" style="margin-top:8px"><div class="field wide"><input data-p="name" placeholder="Nazwa, np. tofu naturalne"></div>
    <div class="field"><label class="l">kcal</label><input data-p="kcal" inputmode="decimal"></div><div class="field"><label class="l">Białko</label><input data-p="protein" inputmode="decimal"></div>
    <div class="field"><label class="l">Węglowodany</label><input data-p="carbs" inputmode="decimal"></div><div class="field"><label class="l">Tłuszcze</label><input data-p="fat" inputmode="decimal"></div><div class="field"><label class="l">Błonnik</label><input data-p="fiber" inputmode="decimal"></div>
    <div class="field wide"><label class="l">Dział sklepu</label><select data-p="dept">${DEPT_ORDER.map(d => `<option>${d}</option>`).join("")}</select></div>
    <div class="field wide checks">${TAGS.map(t => `<label><input type="checkbox" data-ptag="${t}">${t}</label>`).join("")}</div></div>
    <div class="row" style="margin-top:8px"><button class="btn small primary" data-act="saveprod">Dodaj produkt</button></div></div>`;
}

/* ---------- Widok: Profile ---------- */
function editsFor(id, pub) {
  if (!S.edits[id]) S.edits[id] = { fields: Object.fromEntries(Object.entries(pub.fields).map(([k, f]) => [k, f.value])), meals: pub.meals ? pub.meals.map(m => ({ ...m })) : null };
  return S.edits[id];
}
function isDirty(id, pub) {
  const e = S.edits[id]; if (!e) return false;
  if (Object.entries(e.fields).some(([k, v]) => (v || "").trim() !== (pub.fields[k].value || "").trim())) return true;
  return !!pub.meals && JSON.stringify(e.meals) !== JSON.stringify(pub.meals);
}
function options(f) {
  if (f.label === "Płeć") return ["kobieta", "mężczyzna"];
  if (f.label.endsWith("?")) return ["tak", "nie"];
  const h = f.hint;
  if (!h || h.includes("np.") || h.includes(",") || !h.includes("/")) return null;
  return h.split("/").map(s => s.trim()).filter(s => s && s !== "itp.");
}
const CHOICES = {
  "Płeć": ["kobieta", "mężczyzna"],
  "Poziom aktywności": ["siedzący", "lekki", "umiarkowany", "wysoki", "bardzo wysoki"],
  "Cel główny": ["redukcja", "utrzymanie", "masa"],
  "Tempo zmiany wagi": ["wolne", "umiarkowane", "szybkie", "brak wytycznych"],
  "Rodzaj diety": ["wszystkożerna", "wegetariańska", "wegańska", "peskatariańska"],
  "Dzień rozpoczęcia tygodnia": ["poniedziałek", "sobota", "niedziela"],
  "Meal prep / gotowanie na zapas": ["nie", "tak, co 2 dni", "cały tydzień w niedzielę"],
  "Posiłki do pracy (zimne / do odgrzania)": ["zimne", "do odgrzania", "nie dotyczy"],
  "Sezonowość produktów": ["tak", "nie"],
};
let dlId = 0;
function fieldHtml(id, key, f) {
  const v = S.edits[id].fields[key] || "";
  const ch = CHOICES[f.label];
  if (ch) {
    const cur = v.trim().toLowerCase(), known = ch.some(o => o === cur);
    return `<div class="field wide ${v.trim() ? "" : "empty"}"><label class="l">${esc(f.label)}</label><div class="choice" role="group" aria-label="${esc(f.label)}">
      ${ch.map(o => `<button class="${cur === o ? "active" : ""}" data-act="choice" data-id="${esc(id)}" data-key="${esc(key)}" data-v="${esc(o)}">${esc(o)}</button>`).join("")}
      ${!known && cur ? `<button class="active" title="Wartość wpisana wcześniej">${esc(v)}</button>` : ""}</div><span class="note" data-note="${esc(key)}"></span></div>`;
  }
  const ph = f.hint ? `np. ${f.hint.replace(/^np\.\s*/, "")}` : "";
  const opts = options(f), dl = opts ? `dl${++dlId}` : "";
  const wide = TEXTAREA_RE.test(f.label) || key === "Uwagi dla planisty";
  const input = wide
    ? `<textarea data-key="${esc(key)}" placeholder="${esc(key === "Uwagi dla planisty" ? "Każda linia = osobny punkt" : ph)}">${esc(v)}</textarea>`
    : `<input data-key="${esc(key)}" value="${esc(v)}" placeholder="${esc(opts ? opts.join(" / ") : ph)}" ${NUMBER_RE.test(f.label) ? 'inputmode="decimal"' : ""} ${dl ? `list="${dl}"` : ""}>`;
  return `<div class="field ${wide ? "wide" : ""} ${v.trim() ? "" : "empty"}"><label class="l">${esc(f.label)}</label>${input}
    ${opts ? `<datalist id="${dl}">${opts.map(o => `<option value="${esc(o)}">`).join("")}</datalist>` : ""}<span class="note" data-note="${esc(key)}"></span></div>`;
}
function formSections(id, pub, extra = {}) {
  return pub.sections.map(s => (extra.before?.[s.name] || "") +
    `<div class="card"><h2>${esc(s.name)}</h2><div class="fgrid">${s.fields.map(k => fieldHtml(id, k, pub.fields[k])).join("")}</div></div>` +
    (extra.after?.[s.name] || "")).join("");
}
function saveBar(id, pub) {
  return `<div class="savebar" id="savebar" ${isDirty(id, pub) ? "" : "hidden"}><span>Niezapisane zmiany</span>
    <button class="btn" data-act="revert" data-id="${id}">Cofnij</button><button class="btn primary" data-act="save" data-id="${id}">Zapisz</button></div>`;
}
function viewProfile() {
  const persons = S.data.persons;
  if (!persons.length) return `<div class="card empty"><p>Brak profili w katalogu danych.</p></div>`;
  if (!S.data.profiles[S.person]) S.person = persons[0].id;
  const id = S.person, pub = S.data.profiles[id];
  editsFor(id, pub);
  const tg = persons.find(p => p.id === id).target;
  return `<div class="seg">${persons.map(p => `<button class="${p.id === id ? "active" : ""}" data-act="person" data-id="${p.id}">${esc(p.name)}</button>`).join("")}</div>
    <div class="card"><div class="target"><b>${fmt(tg.kcal)} kcal</b><span class="sub">cel dzienny · ${esc(tg.source)}</span></div></div>
    ${formSections(id, pub, { after: { "Zapotrzebowanie": calcHtml() } })}
    ${saveBar(id, pub)}`;
}
function calcHtml() {
  return `<div class="card calc" id="calc"><h2>Kalkulator zapotrzebowania <small>Mifflin–St Jeor · szacunek</small></h2>
    <div class="fgrid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr))">
      <div class="field"><label class="l">Korekta wg celu (%)</label><input id="c-adj" inputmode="decimal"></div>
      <div class="field"><label class="l">Białko (g/kg)</label><input id="c-prot" inputmode="decimal"></div>
      <div class="field"><label class="l">Tłuszcze (% kcal)</label><input id="c-fat" inputmode="decimal" value="27.5"></div>
      <div class="field"><label class="l">Błonnik (g)</label><input id="c-fib" inputmode="decimal" value="30"></div></div>
    <pre id="c-out"></pre><div class="alert" id="c-warn"></div>
    <button class="btn" id="c-apply" data-act="calcapply" disabled>Wstaw do pól zapotrzebowania</button></div>`;
}
function sexOf(f) { const s = (f[K.sex] || "").trim().toLowerCase(); return !s ? null : /^(m|mężczyzna|facet)/.test(s) ? "M" : /^(k|kobieta)/.test(s) ? "K" : null; }
function goalOf(f) { const g = (f[K.goal] || "").toLowerCase(); return g.includes("reduk") ? "redukcja" : g.includes("mas") ? "masa" : g.includes("utrzym") ? "utrzymanie" : null; }
let calcResult = null;
function calc(setDefaults) {
  if (!$("#calc")) return;
  const f = S.edits[S.person].fields;
  const sex = sexOf(f), age = num(f[K.age]), h = num(f[K.height]), w = num(f[K.weight]);
  const act = ACTIVITY.find(([p]) => (f[K.activity] || "").toLowerCase().includes(p));
  const goal = goalOf(f);
  if (setDefaults || !$("#c-adj").value) { $("#c-adj").value = { redukcja: -15, masa: 7.5, utrzymanie: 0 }[goal] ?? 0; $("#c-prot").value = goal === "utrzymanie" ? 1.4 : 1.8; }
  const miss = []; if (!sex) miss.push("płeć"); if (!age) miss.push("wiek"); if (!h) miss.push("wzrost"); if (!w) miss.push("waga"); if (!act) miss.push("poziom aktywności");
  calcResult = null; $("#c-warn").textContent = "";
  if (miss.length) { $("#c-out").textContent = "Uzupełnij najpierw: " + miss.join(", ") + "."; $("#c-apply").disabled = true; return; }
  const bmr = 10 * w + 6.25 * h - 5 * age + (sex === "M" ? 5 : -161), tdee = bmr * act[1];
  const adj = num($("#c-adj").value) || 0, kcal = Math.round(tdee * (1 + adj / 100) / 10) * 10;
  const protein = Math.round((num($("#c-prot").value) || 0) * w), fat = Math.round(kcal * (num($("#c-fat").value) || 0) / 100 / 9);
  const carbs = Math.max(0, Math.round((kcal - protein * 4 - fat * 9) / 4)), fiber = Math.round(num($("#c-fib").value) || 0);
  calcResult = { kcal, protein, fat, carbs, fiber };
  $("#c-out").textContent = `BMR = 10×${w} + 6,25×${h} − 5×${age} ${sex === "M" ? "+ 5" : "− 161"} = ${Math.round(bmr)} kcal\n` +
    `TDEE = ${Math.round(bmr)} × ${String(act[1]).replace(".", ",")} (${act[2]}) = ${Math.round(tdee)} kcal\n` +
    `Cel${goal ? " (" + goal + ")" : ""}: ${adj > 0 ? "+" : ""}${adj}% → ${kcal} kcal/dzień\n` +
    `Białko ${protein} g · Tłuszcze ${fat} g · Węglowodany ${carbs} g · Błonnik ${fiber} g`;
  const min = sex === "M" ? 1500 : 1200;
  if (kcal < min) $("#c-warn").textContent = `Wynik poniżej bezpiecznego minimum (~${min} kcal). Taką dietę ustal z lekarzem lub dietetykiem.`;
  $("#c-apply").disabled = false;
}
function validateProfile() {
  const f = S.edits[S.person]?.fields; if (!f) return;
  document.querySelectorAll("[data-note]").forEach(n => (n.textContent = ""));
  const kcal = num(f[K.kcal]), sex = sexOf(f), min = sex === "M" ? 1500 : sex === "K" ? 1200 : null;
  const note = document.querySelector(`[data-note="${CSS.escape(K.kcal)}"]`);
  if (note && min && kcal && kcal < min) note.textContent = `Poniżej bezpiecznego minimum (~${min} kcal) – skonsultuj z lekarzem.`;
  calc(false);
}

/* ---------- Widok: Ustawienia ---------- */
function mealsHtml() {
  const meals = S.edits.settings.meals;
  return `<div class="card"><h2>Posiłki <small>godzina · udział kcal · wspólny</small></h2>
    <table class="meals"><thead><tr><th>Nazwa</th><th>Godz.</th><th>%</th><th>Wspólny</th><th></th></tr></thead><tbody>
    ${meals.map((m, i) => `<tr><td><input data-meal="${i}" data-mf="name" value="${esc(m.name)}" placeholder="Nazwa"></td>
      <td style="width:110px"><input type="time" data-meal="${i}" data-mf="time" value="${esc(m.time)}"></td>
      <td style="width:70px"><input inputmode="decimal" data-meal="${i}" data-mf="share" value="${esc(m.share)}"></td>
      <td style="width:92px"><select data-meal="${i}" data-mf="shared">${["", "tak", "nie"].map(o => `<option value="${o}" ${m.shared === o ? "selected" : ""}>${o || "—"}</option>`).join("")}
        ${m.shared && !["tak", "nie"].includes(m.shared) ? `<option selected>${esc(m.shared)}</option>` : ""}</select></td>
      <td style="width:40px"><button class="btn ghost icon" data-act="delmeal" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table>
    <div class="row" style="margin-top:8px"><button class="btn small" data-act="addmeal">+ Dodaj posiłek</button></div>
    <div class="alert" id="meal-warn" style="margin:10px 0 0"></div></div>`;
}
const STABS = [["osoby", "Osoby"], ["posilki", "Posiłki i gotowanie"], ["ai", "Asystent AI"], ["archiwum", "Archiwum"]];
function viewSettings() {
  if (!STABS.some(([k]) => k === S.stab)) S.stab = "osoby";
  const head = `<div class="head"><div><h1>Ustawienia</h1><p class="sub">${{ osoby: "Profile domowników – z nich liczone są cele kcal", posilki: "Wspólne zasady planowania", ai: "Agenci AI, zużycie i zadania w tle", archiwum: "Zapisane plany i informacje" }[S.stab]}</p></div></div>
    <div class="stabs" role="tablist">${STABS.map(([k, v]) => `<button role="tab" aria-selected="${S.stab === k}" class="${S.stab === k ? "active" : ""}" data-act="gostab" data-k="${k}">${v}</button>`).join("")}</div>`;
  if (S.stab === "osoby") return head + viewProfile();
  if (S.stab === "ai") return head + aiCard() + instrCard();
  const pub = S.data.settings; editsFor("settings", pub);
  const weeks = [...S.data.weeks].reverse();
  const first = pub.sections[0]?.name;
  if (S.stab === "posilki") return head + formSections("settings", pub, { after: pub.meals ? { [first]: mealsHtml() } : {} }) + saveBar("settings", pub);
  return head + `
    <div class="card"><h2>Archiwum planów</h2><div class="weeks">${weeks.map(w => `<div class="w"><b>${w}</b><span class="sub">${weekRange(w)}</span><span class="spacer"></span>
      <button class="btn small" data-act="openweek" data-w="${w}">Otwórz</button><a class="btn small" href="api/plans/${w}/export.md" download="${w}.md">.md</a>
      <button class="btn small ghost danger" data-act="delweek" data-w="${w}">Usuń</button></div>`).join("") || '<p class="sub">Brak zapisanych planów.</p>'}</div></div>
    <div class="card"><h2>O aplikacji</h2><p class="sub">Dane zapisują się w plikach Markdown (profile, ustawienia, plany) i JSON (przepisy, produkty, listy zakupów).
      Na Home Assistant znajdziesz je w udziale <code>addon_configs</code>. Plan jest orientacyjny i nie zastępuje porady dietetyka.</p></div>`;
}
const usdTxt = v => v == null ? "nieznany (model spoza cennika)" : v < 0.01 ? "poniżej 0,01 $" : "~" + v.toFixed(2).replace(".", ",") + " $";
function aiCard() {
  const a = S.data.ai || {}, u = a.usage || { total: {}, by_purpose: {} };
  const usd = x => (x || 0).toFixed(2).replace(".", ",") + " $";
  const prov = a.providers || {}, tasks = a.tasks || {}, cat = a.catalog || [];
  S.aiEdit ||= Object.fromEntries(Object.entries(tasks).map(([t, v]) => [t, { model: v.model, fallback: v.fallback }]));
  const readyP = p => prov[p]?.ready;
  const opt = (id, sel) => `<option value="${esc(id)}" ${id === sel ? "selected" : ""}>${esc(id)}</option>`;
  const price = id => { const m = cat.find(x => x.id === id); return m ? `${String(m.price_in).replace(".", ",")} / ${String(m.price_out).replace(".", ",")} $` : "cena nieznana"; };
  const dirty = Object.entries(S.aiEdit).some(([t, v]) => v.model !== tasks[t]?.model || v.fallback !== tasks[t]?.fallback);
  const res = S.aiTest || {};
  return `<div class="card"><h2>Modele AI <small>${a.available ? "aktywne" : "wyłączone"}</small></h2>
    <div class="provs">${Object.entries(prov).map(([p, v]) => `<span class="chip ${v.ready ? "pc p0" : ""}" title="${esc(v.reason || "gotowy")}">${v.ready ? "✓" : "✕"} ${esc(v.label)}</span>`).join("")}</div>
    <p class="sub">Klucze API wpisujesz w <b>Konfiguracji dodatku</b> w HA (<code>anthropic_api_key</code>, <code>google_api_key</code>, <code>xai_api_key</code>), lokalnie w pliku <code>.env</code>. Nie są wysyłane do przeglądarki.</p>
    <datalist id="ai-models">${cat.map(m => `<option value="${esc(m.id)}">${esc(m.label)} · ${price(m.id)} za 1M tokenów${m.vision ? "" : " · bez zdjęć"}${readyP(m.id.split(":")[0]) ? "" : " · brak klucza"}</option>`).join("")}</datalist>
    <div class="aitasks">${Object.entries(S.aiEdit).map(([t, v]) => { const st = tasks[t] || {}, r = res[t];
      const warn = !readyP((v.model || "").split(":")[0]) ? (v.fallback && readyP(v.fallback.split(":")[0]) ? "główny bez klucza – zadziała zapasowy" : "brak klucza dla tego dostawcy") : "";
      return `<div class="aitask"><div class="aitask-name"><b>${esc((a.task_names || {})[t] || t)}</b>${(a.task_vision || []).includes(t) ? ' <small class="sub">· zdjęcia</small>' : ""}
          ${warn ? `<small class="warn-text">${esc(warn)}</small>` : ""}</div>
        <label><small class="sub">Model</small><input list="ai-models" data-ait="${t}" data-k="model" value="${esc(v.model)}" placeholder="dostawca:model" autocomplete="off"></label>
        <label><small class="sub">Zapasowy</small><input list="ai-models" data-ait="${t}" data-k="fallback" value="${esc(v.fallback)}" placeholder="— brak —" autocomplete="off"></label>
        <div class="aitask-act"><button class="btn small ghost" data-act="aitest" data-m="${esc(v.model)}" data-t="${t}" ${readyP((v.model || "").split(":")[0]) ? "" : "disabled"}>Test</button>
          ${r ? `<small class="${r.ok ? "ok-text" : "warn-text"}">${esc(r.msg)}</small>` : ""}</div></div>`; }).join("")}</div>
    <div class="row" style="margin-top:12px"><small class="sub">Format: <code>dostawca:model</code>, np. <code>google:gemini-3.8-flash</code>. Możesz wpisać też model spoza listy.</small><span class="spacer"></span>
      ${dirty ? `<button class="btn small ghost" data-act="aireset">Cofnij</button>` : ""}
      <button class="btn small primary" data-act="aisave" ${dirty ? "" : "disabled"}>Zapisz modele</button></div></div>
  <div class="card"><h2>Zużycie AI</h2>
    <p class="sub">Łącznie: ${usd(u.total.usd)}${u.total.unpriced ? " + modele bez cennika" : ""} (${fmt((u.total.input || 0) / 1000)} tys. tokenów wejścia, ${fmt((u.total.output || 0) / 1000)} tys. wyjścia)${u.last ? ` · ostatnio ${esc(u.last.replace("T", " "))}` : ""}</p>
    ${u.by_provider ? `<p class="sub">${Object.entries(u.by_provider).map(([k, v]) => `${esc((prov[k] || {}).label || k)}: ${usd(v.usd)}`).join(" · ")}</p>` : ""}
    ${Object.keys(u.by_purpose).length ? `<ul class="sub">${Object.entries(u.by_purpose).map(([k, v]) => `<li>${esc(k)}: ${usd(v.usd)}</li>`).join("")}</ul>` : ""}
    <p class="sub">Szacunek wg cenników dostawców; faktyczne rozliczenie znajdziesz w ich konsolach.</p>
    ${(() => { const nf = S.data.products.filter(p => p.fiber == null).length;
      return nf ? `<p class="sub">Błonnik: brak danych dla ${nf} produktów. <button class="btn small" data-act="fillfiber" ${a.available ? "" : "disabled"}>Uzupełnij błonnik (AI)</button></p>` : ""; })()}</div>`;
}
function instrCard() {
  const s = S.instr;
  if (!s || (!s.running && Date.now() - S.instrAt > 10000)) loadInstr();
  if (!s) { return '<div class="card"><h2>Instrukcje, porcjowanie i meal prep</h2><div class="loading">Wczytywanie…</div></div>'; }
  const e = s.estimate && s.estimate.model === s.models[S.instrModel] ? s.estimate : null;
  const total = s.done + s.pending, pct = total ? Math.round(s.done / total * 100) : 0;
  return `<div class="card" id="instr"><h2>Instrukcje, porcjowanie i meal prep <small>${s.running ? "rozpisywanie…" : s.pending ? `${s.pending} do rozpisania` : "gotowe"}</small></h2>
    <p class="sub">Agent przepisów rozpisuje każdy przepis na: <b>🔥 przed gotowaniem</b> (np. rozgrzanie piekarnika), <b>kroki</b> i <b>🧊 przechowywanie</b> przy daniach na zapas, a także ustala <b>porcjowanie</b> (wspólny garnek, sztuki albo każdy osobno) i <b>etapy meal prepu</b> (co zrobić w niedzielę, co mrozić, co na świeżo).
      Składniki, gramatury i kalorie się nie zmieniają. Przed pierwszym uruchomieniem zapisuje się kopia <code>recipes.before-instructions.json</code>.</p>
    ${s.pending ? `<p class="sub">Do rozpisania: ${s.pending_recipes} w bibliotece, ${s.pending_drafts} w „Do przejrzenia”.</p>` : ""}
    ${s.running || (s.status === "paused" && s.done) ? `<div class="bar"><i style="width:${pct}%"></i></div><p class="sub">${s.done} gotowe z ${total}</p>` : ""}
    ${s.errors?.length ? `<details class="info"><summary>Uwagi (${s.errors.length})</summary><ul>${s.errors.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    ${s.pending && !s.running ? `<div class="filters" style="margin:8px 0">${[["import", "Tańszy"], ["main", "Dokładniejszy"]].map(([k, v]) =>
        `<button class="${S.instrModel === k ? "active" : ""}" data-act="imodel" data-m="${k}">${v} · ${esc(s.models[k])}</button>`).join("")}</div>
      ${e ? `<p class="sub">Szacowany koszt: <b>${usdTxt(e.usd)}</b> (${e.recipes} przepisów, ${e.batches} zapytań)</p>` : ""}` : ""}
    <div class="row">${s.running ? `<button class="btn small" data-act="icancel">⏸ Wstrzymaj</button>`
      : s.pending ? `${e ? "" : `<button class="btn small" data-act="iestimate">Oszacuj koszt</button>`}
        <button class="btn small primary" data-act="istart" ${S.data.ai?.available ? "" : "disabled"}>${s.status === "paused" ? "▶ Wznów" : "✨ Rozpisz instrukcje"}</button>` : ""}</div></div>`;
}
let instrLoading = false;
async function loadInstr() {
  if (instrLoading) return; instrLoading = true;
  try { S.instr = await api("api/instructions"); S.instrAt = Date.now(); render(); } catch { /* spróbuj później */ } finally { instrLoading = false; }
}
async function pollInstr() {
  if (document.hidden || S.view !== "ustawienia" || S.stab !== "ai" || !S.instr?.running || !$("#modal").hidden) return;
  try { S.instr = await api("api/instructions"); if (!S.instr.running) { S.data = await api("api/state"); toast("Instrukcje rozpisane"); } render(); } catch { /* później */ }
}
setInterval(pollInstr, 3000);
function validateMeals() {
  const w = $("#meal-warn"); if (!w) return;
  const sum = S.edits.settings.meals.reduce((a, m) => a + (num(m.share) || 0), 0);
  w.textContent = Math.abs(sum - 100) > 0.01 ? `Udziały kcal sumują się do ${+sum.toFixed(1)}% – powinno być 100%.` : "";
}

function applyInbox(out) {
  S.inbox = out.items; S.data.inbox_count = out.items.length;
  if (out.recipes) S.data.recipes = out.recipes;
  if (out.products) S.data.products = out.products;
  render();
}
async function uploadBook(file) {
  toast(`Wgrywam ${file.name}…`);
  const r = await fetch("api/books/upload", { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file });
  const out = await r.json(); if (!r.ok) throw new Error(out.error || r.statusText);
  S.books = out.books; render(); toast("Wgrano – oszacuj koszt i zaimportuj");
}

/* ---------- Modal ---------- */
function openModal(html) { const m = $("#modal"); m.innerHTML = `<div class="sheet" role="dialog">${html}</div>`; m.hidden = false; }
function closeModal() { $("#modal").hidden = true; $("#modal").innerHTML = ""; draft = null; Q = null; }

/* ---------- Akcje ---------- */
async function guard(fn) {
  if (S.busy) return; S.busy = true;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { S.busy = false; }
}
function refreshSaveBar() {
  const id = S.stab === "osoby" ? S.person : "settings";
  const pub = id === "settings" ? S.data.settings : S.data.profiles[id];
  const bar = $("#savebar"); if (bar) bar.hidden = !isDirty(id, pub);
}

document.addEventListener("click", e => {
  const t = e.target.closest("[data-act]"); if (!t) return;
  const a = t.dataset.act;
  if (t.tagName === "A") return;
  e.preventDefault();
  if (a === "navtoggle") return toggleNav();
  const acts = {
    week: () => guard(async () => { S.weekSel = S.week = shiftWeek(S.week, +t.dataset.n); S.wday = null; S.open.clear(); await loadPlan(S.week); render(); }),
    day: () => { S.wday = t.dataset.i === "prep" ? "prep" : +t.dataset.i; S.open.clear(); render(); window.scrollTo({ top: 0, behavior: "smooth" }); },
    backweek: () => { S.wday = null; render(); window.scrollTo({ top: 0, behavior: "smooth" }); },
    openprep: () => guard(async () => {
      S.weekSel = t.dataset.w || S.week; S.wday = "prep";
      if (S.view === "tydzien") { S.week = S.weekSel; await loadPlan(S.week); render(); window.scrollTo({ top: 0 }); }
      else location.hash = "#/tydzien";
    }),
    preptoggle: () => guard(async () => {
      const task = S.plan.prep.sessions.flatMap(x => x.tasks).find(x => x.id === t.dataset.id); if (!task) return;
      task.checked = !task.checked; render();
      setPlan(await api(`api/plans/${S.week}/shopping`, { op: "prep_toggle", id: task.id, checked: task.checked })); render();
    }),
    prepmode: () => { S.prepMode = t.dataset.m; $$("#modal [data-act=prepmode]").forEach(b => b.classList.toggle("active", b.dataset.m === S.prepMode));
      const pl = $("#modal .preplimit"); if (pl) pl.hidden = S.prepMode !== "weekly"; },
    preplimit: () => { S.prepLimit = +t.dataset.m; $$("#modal [data-act=preplimit]").forEach(b => b.classList.toggle("active", +b.dataset.m === S.prepLimit)); },
    meal: () => { const k = t.dataset.key; S.open.has(k) ? S.open.delete(k) : S.open.add(k); render(); },
    plansheet: () => planSheet(!!t.dataset.again),
    dogenerate: () => guard(async () => {
      const mode = t.dataset.mode, wishes = $("#g-wishes")?.value || "";
      t.disabled = true; t.textContent = mode === "agent" ? "Agent układa plan…" : "Układam…";
      setPlan(await api(`api/plans/${S.week}/generate`, { seed: Date.now(), mode, wishes, prep_mode: S.prepMode, prep_limit: S.prepLimit ?? 120 }));
      S.data.weeks = [...new Set([...S.data.weeks, S.week])].sort(); S.wday = null; closeModal(); render();
      toast(mode === "agent" ? "Agent diety ułożył plan" : "Plan gotowy");
    }).finally(() => { if (t.isConnected) { t.disabled = false; } }),
    rate: () => guard(async () => {
      const out = await api("api/recipes/rate", { id: t.dataset.id, delta: +t.dataset.d }); S.data.recipes = out.recipes;
      if (!$("#modal").hidden && S.view === "przepisy") recipeSheet(t.dataset.id); render();
      toast(+t.dataset.d > 0 ? "Zapamiętane: smakowało" : "Zapamiętane: rzadziej w planach");
    }),
    rtab: () => guard(async () => { S.rtab = t.dataset.k; render(); await loadRecipeTab(); render(); }),
    textsheet: () => textSheet(),
    dotext: () => guard(async () => {
      const text = $("#t-text").value, source = $("#t-src").value;
      t.disabled = true; t.textContent = "Agent czyta przepis…";
      try {
        const out = await api("api/recipes/draft", { text, source });
        S.inbox = out.items; S.data.inbox_count = out.items.length; closeModal(); S.rtab = "inbox"; render();
        toast(out.drafts.length ? `Dodano do przejrzenia: ${out.drafts.length}` : "Nie znaleziono nowego przepisu (może już jest w bazie)");
      } finally { if (t.isConnected) { t.disabled = false; t.textContent = "Utwórz szkic"; } }
    }),
    accept: () => guard(async () => { const out = await api("api/inbox/accept", { id: t.dataset.id }); applyInbox(out); toast("Dodano do biblioteki"); }),
    acceptclean: () => guard(async () => { const out = await api("api/inbox/accept_clean", {}); applyInbox(out); toast(`Zatwierdzono: ${out.accepted}`); }),
    reject: () => guard(async () => { const out = await api("api/inbox/reject", { id: t.dataset.id }); applyInbox(out); }),
    editdraft: () => { const d = S.inbox.find(x => x.id === t.dataset.id); recipeEditor({ ...d.recipe, __inbox: d.id, __newprods: d.new_products }); },
    bestimate: () => guard(async () => { const out = await api("api/books/estimate", { file: t.dataset.f }); S.books = out.books; render(); }),
    bimport: () => {
      const b = S.books.find(x => x.file === t.dataset.f), e = b.estimate;
      const cost = e ? `Szacowany koszt: ${usdTxt(e.usd)} (model ${e.model}).` : "";
      if (!confirm(`Zaimportować przepisy z „${b.title}”? ${cost} Tekst książki zostanie wysłany do Claude API we fragmentach.`)) return;
      guard(async () => { S.books = (await api("api/books/import", { file: t.dataset.f })).books; render(); toast("Import ruszył – możesz przeglądać aplikację"); });
    },
    bcancel: () => guard(async () => { S.books = (await api("api/books/cancel", {})).books; render(); toast("Import zostanie wstrzymany po bieżącym fragmencie"); }),
    bdelete: () => { if (confirm(`Usunąć plik ${t.dataset.f} z folderu książek? Zaimportowane przepisy zostaną.`)) guard(async () => { S.books = (await api("api/books/delete", { file: t.dataset.f })).books; render(); }); },
    swap: () => swapSheet(+t.dataset.slot),
    quick: () => quickSheet(+t.dataset.slot),
    qsearch: () => quickSearch(),
    qnoimg: () => { Q.image = null; renderQuick(); },
    qrm: () => { Q.products.splice(+t.dataset.i, 1); quickResearch(); },
    qinvent: () => (async () => { Q.loading = "invent"; renderQuick();
      try { Q.draft = (await api(`api/plans/${S.week}/invent`, { day: S.day, slot: Q.slot, items: [...Q.products, ...Q.unknown] })).draft; }
      catch (err) { toast(err.message, true); }
      Q.loading = ""; renderQuick(); })(),
    qaccept: () => guard(async () => {
      const out = await api("api/recipes", Q.draft); S.data.recipes = out.recipes;
      setPlan(await api(`api/plans/${S.week}/swap`, { day: S.day, slot: Q.slot, recipe: out.recipe.id }));
      closeModal(); render(); toast("Dodano przepis i podmieniono danie"); }),
    doswap: () => guard(async () => { setPlan(await api(`api/plans/${S.week}/swap`, { day: S.day, slot: +t.dataset.slot, recipe: t.dataset.recipe || null, seed: Date.now() })); closeModal(); render(); toast("Danie zamienione"); }),
    toggle: () => { if (e.target.closest("[data-act=rmitem]")) return; toggleItem(t); },
    rmitem: () => guard(async () => { setPlan(await api(`api/plans/${S.week}/shopping`, { op: "remove", id: t.dataset.id })); render(); }),
    clearShop: () => { if (confirm("Odznaczyć wszystkie pozycje?")) guard(async () => { setPlan(await api(`api/plans/${S.week}/shopping`, { op: "clear" })); render(); }); },
    rfilter: () => { S.rfilter = t.dataset.k; render(); },
    recipe: () => recipeSheet(t.dataset.id),
    newrecipe: () => recipeEditor(null),
    editrecipe: () => recipeEditor(recipeMap()[t.dataset.id]),
    delrecipe: () => { if (confirm("Usunąć ten przepis z bazy? Plany, które go używają, pokażą brak dania.")) guard(async () => { S.data.recipes = (await api("api/recipes/delete", { id: t.dataset.id })).recipes; closeModal(); render(); toast("Usunięto przepis"); }); },
    adding: () => { draft.ingredients.push({ product: "", grams: "" }); renderEditor(); },
    rming: () => { draft.ingredients.splice(+t.dataset.k, 1); renderEditor(); },
    newprod: () => prodForm(),
    saveprod: () => guard(async () => {
      const p = { tags: [] }; document.querySelectorAll("[data-p]").forEach(i => (p[i.dataset.p] = i.value));
      document.querySelectorAll("[data-ptag]:checked").forEach(i => p.tags.push(i.dataset.ptag));
      const out = await api("api/products", p); S.data.products = out.products; if (out.recipes) S.data.recipes = out.recipes;
      const empty = draft.ingredients.find(i => !i.product); if (empty) empty.product = out.product.name; else draft.ingredients.push({ product: out.product.name, grams: "" });
      renderEditor(); toast("Dodano produkt");
    }),
    saverecipe: () => guard(async () => {
      if (!draft.__hadPrep && !String(draft.prep_notes || "").trim()) delete draft.prep_notes;  // stary przepis – zostaje do rozpisania
      delete draft.__hadPrep;
      if (draft.__svAuto && draft.serving.mode === draft.__svAuto) delete draft.serving;  // zgadnięte – zostaw dla agenta
      delete draft.__svAuto;
      if (draft.__inbox) {
        const { __inbox, __newprods, ...rec } = draft;
        rec.ingredients = rec.ingredients.filter(i => i.product && num(i.grams) > 0).map(i => ({ product: i.product, grams: num(i.grams) }));
        const out = await api("api/inbox/accept", { id: __inbox, recipe: rec }); closeModal(); applyInbox(out); toast("Dodano do biblioteki"); return;
      }
      const out = await api("api/recipes", draft); S.data.recipes = out.recipes; closeModal(); render(); toast("Zapisano przepis");
    }),
    close: () => closeModal(),
    aisave: () => guard(async () => {
      S.data.ai = await api("api/ai/models", { tasks: S.aiEdit }); S.aiEdit = null; S.aiTest = {}; render(); toast("Zapisano modele AI");
    }),
    aireset: () => { S.aiEdit = null; render(); },
    aitest: () => guard(async () => {
      const t0 = t.dataset.t; t.disabled = true; t.textContent = "…";
      try { const r = await api("api/ai/test", { model: t.dataset.m });
        (S.aiTest ||= {})[t0] = { ok: r.ok, msg: r.ok ? `działa · ${String(r.seconds).replace(".", ",")} s` : "odpowiedź niezgodna ze schematem" };
      } catch (e) { (S.aiTest ||= {})[t0] = { ok: false, msg: e.message }; }
      render();
    }),
    planmenu: () => planMenu(),
    refreshplan: () => guard(async () => {
      setPlan(await api(`api/plans/${S.week}/refresh`, {})); closeModal(); render(); toast("Plan przeliczony – dania bez zmian");
    }),
    mtab: () => { S.mtab[t.dataset.key] = t.dataset.t; render(); },
    mperson: () => { S.mperson = t.dataset.id; render(); },
    addsheet: () => addSheet(),
    addbook: () => guard(async () => { closeModal(); S.rtab = "books"; render(); await loadRecipeTab(); render(); }),
    gostab: () => { S.stab = t.dataset.k; if (S.view !== "ustawienia") location.hash = "#/ustawienia"; else render(); window.scrollTo({ top: 0 }); },
    choice: () => {
      const id = t.dataset.id, f = S.edits[id].fields; f[t.dataset.key] = t.dataset.v;
      render(); refreshSaveBar(); if (S.stab === "osoby" && t.dataset.key === K.goal) calc(true);
    },
    fillfiber: () => guard(async () => {
      t.disabled = true; t.textContent = "Uzupełniam…";
      const out = await api("api/products/fiber", {}); S.data.products = out.products; S.data.recipes = out.recipes;
      const id = $("#modal [data-act=editrecipe]")?.dataset.id; if (id) recipeSheet(id); else render();
      toast(out.filled ? `Uzupełniono błonnik dla ${out.filled} produktów (szacunek)` : "Brak produktów do uzupełnienia");
    }),
    step: () => { const id = t.dataset.s; doneSteps.has(id) ? doneSteps.delete(id) : doneSteps.add(id); t.classList.toggle("done", doneSteps.has(id)); },
    iestimate: () => guard(async () => { S.instr = await api("api/instructions/estimate", { model: S.instrModel }); render(); }),
    istart: () => {
      const e = S.instr?.estimate;
      if (!confirm(`Rozpisać instrukcje dla ${S.instr.pending} przepisów modelem ${S.instr.models[S.instrModel]}?${e ? ` Szacowany koszt: ${usdTxt(e.usd)}.` : ""}`)) return;
      guard(async () => { S.instr = await api("api/instructions/start", { model: S.instrModel }); render(); toast("Rozpisywanie ruszyło – działa w tle"); });
    },
    icancel: () => guard(async () => { S.instr = await api("api/instructions/cancel", {}); render(); toast("Wstrzymam po bieżącej paczce"); }),
    imodel: () => { S.instrModel = t.dataset.m; if (S.instr) S.instr.estimate = null; render(); },
    person: () => { S.person = t.dataset.id; render(); calc(true); },
    calcapply: () => {
      if (!calcResult) return; const f = S.edits[S.person].fields, r = calcResult;
      f[K.kcal] = String(r.kcal); f[K.protein] = String(r.protein); f[K.carbs] = String(r.carbs); f[K.fat] = String(r.fat);
      if (f[K.fiber] !== undefined) f[K.fiber] = String(r.fiber);
      f[K.source] = "wyliczone przez aplikację – szacunek (Mifflin–St Jeor)";
      render(); toast("Wstawiono – sprawdź i zapisz");
    },
    revert: () => { delete S.edits[t.dataset.id]; render(); },
    save: () => guard(async () => {
      const id = t.dataset.id, e2 = S.edits[id];
      if (id === "settings" && e2.meals?.some(m => !m.name.trim())) throw new Error("Każdy posiłek musi mieć nazwę.");
      const out = id === "settings" ? await api("api/settings", { fields: e2.fields, meals: e2.meals }) : await api(`api/profiles/${id}`, { fields: e2.fields });
      S.data = out.state; delete S.edits[id]; render();
      toast(out.changed.length ? `Zapisano: ${out.changed.length} zmian` : "Brak zmian");
    }),
    addmeal: () => { S.edits.settings.meals.push({ name: "", time: "", share: "", shared: "" }); render(); },
    delmeal: () => { S.edits.settings.meals.splice(+t.dataset.i, 1); render(); },
    openweek: () => guard(async () => { S.weekSel = S.week = t.dataset.w; S.wday = null; await loadPlan(S.week); location.hash = "#/tydzien"; }),
    delweek: () => { if (confirm(`Usunąć plan ${t.dataset.w}?`)) guard(async () => { await api(`api/plans/${t.dataset.w}/delete`, {}); S.data.weeks = S.data.weeks.filter(w => w !== t.dataset.w); if (S.planWeek === t.dataset.w) { S.plan = null; S.planWeek = null; } render(); }); },
  };
  acts[a]?.();
});
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

/* ---------- Zwijane menu (komputer) ---------- */
function syncNavToggle() {
  const c = $(".shell").classList.contains("nav-collapsed"), b = $(".nav-toggle"); if (!b) return;
  b.setAttribute("aria-expanded", String(!c));
  b.setAttribute("aria-label", c ? "Rozwiń menu" : "Zwiń menu");
  b.title = (c ? "Rozwiń menu" : "Zwiń menu") + " ( [ )";
  b.querySelector("span").textContent = c ? "Rozwiń menu" : "Zwiń menu";
}
function toggleNav() {
  const c = $(".shell").classList.toggle("nav-collapsed");
  try { localStorage.setItem("dp-nav", c ? "collapsed" : "open"); } catch { /* tryb prywatny – bez zapamiętania */ }
  syncNavToggle();
}
syncNavToggle();
document.addEventListener("keydown", e => {
  if (e.key !== "[" || e.ctrlKey || e.metaKey || e.altKey || !$("#modal").hidden) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  toggleNav();
});
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

document.addEventListener("input", e => {
  const t = e.target;
  if (t.dataset.input === "rsearch") { S.rsearch = t.value; const pos = t.selectionStart; render(); const s = $("[data-input=rsearch]"); s.focus(); s.setSelectionRange(pos, pos); return; }
  if (t.dataset.q === "text" && Q) { Q.text = t.value; return; }
  if (t.dataset.ait && S.aiEdit) { S.aiEdit[t.dataset.ait][t.dataset.k] = t.value.trim(); const b = $("[data-act=aisave]"); if (b) b.disabled = false; return; }
  if (t.dataset.d && draft) { draft[t.dataset.d] = t.type === "checkbox" ? t.checked : t.value; return; }
  if (t.dataset.sv && draft) { draft.serving[t.dataset.sv] = t.dataset.sv === "units_per_portion" ? num(t.value) || "" : t.value; return; }
  if (t.dataset.ing !== undefined && draft) { draft.ingredients[+t.dataset.ing][t.dataset.f] = t.value; return; }
  if (t.dataset.key) {
    const id = S.stab === "osoby" ? S.person : "settings";
    S.edits[id].fields[t.dataset.key] = t.value;
    t.closest(".field").classList.toggle("empty", !t.value.trim());
    if (S.stab === "osoby") { if (t.dataset.key === K.goal) calc(true); validateProfile(); }
    refreshSaveBar(); return;
  }
  if (t.dataset.meal !== undefined) { S.edits.settings.meals[+t.dataset.meal][t.dataset.mf] = t.value; validateMeals(); refreshSaveBar(); return; }
  if (["c-adj", "c-prot", "c-fat", "c-fib"].includes(t.id)) calc(false);
});
document.addEventListener("change", e => {
  const t = e.target;
  if (t.dataset.upload !== undefined && t.files[0]) { guard(() => uploadBook(t.files[0])); return; }
  if (t.dataset.q === "image" && Q && t.files[0]) { shrinkImage(t.files[0]).then(d => { Q.image = d; renderQuick(); }).catch(err => toast(err.message, true)); return; }
  if (t.dataset.dslot && draft) { draft.slots = [...document.querySelectorAll("[data-dslot]:checked")].map(i => i.dataset.dslot); }
  if (t.dataset.d === "meal_prep" && draft) { draft.meal_prep = t.checked; renderEditor(); }
  if (t.dataset.sv === "mode" && draft) { draft.serving.mode = t.value; renderEditor(); }
  if (t.dataset.ing !== undefined && draft) renderEditor();
  if (t.matches("select[data-meal]")) { S.edits.settings.meals[+t.dataset.meal].shared = t.value; refreshSaveBar(); }
});
document.addEventListener("submit", e => {
  if (e.target.dataset.form !== "additem") return;
  e.preventDefault();
  const inp = e.target.elements.text, text = inp.value.trim(); if (!text) return;
  guard(async () => { setPlan(await api(`api/plans/${S.week}/shopping`, { op: "add", text })); render(); $(".add input")?.focus(); });
});
document.addEventListener("toggle", e => { if (e.target.classList?.contains("bought")) S.boughtOpen = e.target.open; }, true);

window.addEventListener("beforeunload", e => {
  const dirty = Object.keys(S.edits).some(id => isDirty(id, id === "settings" ? S.data.settings : S.data.profiles[id]));
  if (dirty) { e.preventDefault(); e.returnValue = ""; }
});

/* ---------- Synchronizacja między urządzeniami ---------- */
async function pollBooks() {
  if (document.hidden || S.view !== "przepisy" || S.rtab !== "books" || !S.books?.some(b => b.running) || !$("#modal").hidden) return;
  try {
    const out = await api("api/books"); S.books = out.books;
    S.data.inbox_count = (await api("api/inbox")).items.length; render();
  } catch { /* spróbuj później */ }
}
setInterval(pollBooks, 3000);
async function poll() {
  if (document.hidden || S.busy || !S.week || !["dzis", "tydzien", "zakupy"].includes(S.view) || !$("#modal").hidden) return;
  if (document.activeElement && document.activeElement.matches("input, textarea")) return;
  try { if (await loadPlan(S.week, true)) render(); } catch { /* offline – spróbuj później */ }
}
setInterval(poll, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
window.addEventListener("hashchange", () => route().catch(e => toast(e.message, true)));
document.addEventListener("keydown", e => {  // wiersze dni w „Tydzień” działają też z klawiatury
  if ((e.key === "Enter" || e.key === " ") && e.target.matches?.('[role="button"][data-act]')) { e.preventDefault(); e.target.click(); }
});

(async () => {
  try {
    await loadState();
    S.week = S.data.current_week;
    // Brak planu na ten tydzień, a jest na następny (np. w niedzielę) → pokaż następny.
    const next = shiftWeek(S.week, 1);
    if (!S.data.weeks.includes(S.week) && S.data.weeks.includes(next)) S.week = next;
    S.weekSel = S.week;  // zakładka „Tydzień” startuje od tego tygodnia; „Dziś” zawsze od bieżącego
    await route();
  } catch (e) {
    $("#view").innerHTML = `<div class="card empty"><div class="big">⚠️</div><p>Nie udało się połączyć z serwerem: ${esc(e.message)}</p></div>`;
  }
})();
