"""The gym page: one self-contained HTML document served at /gym/.

Plain HTTP over the tailnet, so no service worker (browsers allow those only
over HTTPS): the page must be loaded once with signal. After that, sets logged
without signal wait in localStorage and are sent when the connection returns;
each carries a client-made id, so a retry never stores a set twice. All data is
put into the DOM as text, never as HTML.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gym</title>
<style>
  :root { --bg:#fff; --fg:#111; --muted:#666; --card:#f3f3f3; --accent:#1a7f37; --warn:#b35900; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#111; --fg:#eee; --muted:#999; --card:#1f1f1f; --accent:#3fb950; --warn:#e3a008; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:12px 16px 40px; background:var(--bg); color:var(--fg);
         font:16px/1.4 system-ui, sans-serif; }
  header { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
  h1 { font-size:20px; margin:4px 0 12px; }
  #sync { font-size:13px; color:var(--muted); }
  #sync.waiting { color:var(--warn); }
  .card { background:var(--card); border-radius:10px; padding:12px; margin-bottom:12px; }
  .name { font-weight:600; font-size:17px; }
  .target, .last, .note { font-size:14px; color:var(--muted); }
  .sets { margin:8px 0; display:flex; flex-wrap:wrap; gap:6px; }
  .set { background:var(--bg); border-radius:6px; padding:4px 8px; font-size:15px; }
  .set.pending { color:var(--warn); }
  .set button { border:0; background:none; color:var(--muted); font-size:15px; padding:0 0 0 6px; }
  .row { display:flex; gap:8px; align-items:center; }
  input { width:4.2em; min-width:0; font-size:18px; padding:8px; border-radius:6px; border:1px solid var(--muted);
          background:var(--bg); color:var(--fg); }
  button.log { flex:1; font-size:18px; padding:10px; white-space:nowrap; border:0; border-radius:6px;
               background:var(--accent); color:#fff; }
  .add { display:flex; gap:8px; margin-top:16px; }
  select, .add button { font-size:16px; padding:8px; border-radius:6px; }
  select { flex:1; min-width:0; background:var(--bg); color:var(--fg); }
  .empty { color:var(--muted); }
</style>
</head>
<body>
<header><h1 id="day">Gym</h1><span id="sync"></span></header>
<main id="cards"><p class="empty">Loading…</p></main>
<div class="add">
  <select id="pick"></select>
  <button id="addbtn">Add</button>
  <button id="newbtn">New</button>
</div>
<script>
"use strict";
const KEY = "gym_pending";
const pad = n => String(n).padStart(2, "0");
const now = new Date();
const date = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
let S = null;          // server state for today
let extra = [];        // exercise ids added on the page this session

function pending() {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
}
function savePending(p) {
  try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) {}
}
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "onclick") n.onclick = v; else n.setAttribute(k, v);
  }
  for (const k of kids) n.append(k);  // strings become text nodes, never HTML
  return n;
}
const kg = w => (w === 0 ? "BW" : w + " kg");

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}

async function load() {
  try { S = await api("state?date=" + date); } catch (e) {}
  render();
  flush();
}

async function flush() {
  let p = pending();
  while (p.length) {
    let saved;
    try { saved = await api("sets", p[0]); } catch (e) { break; }  // no signal: try later
    if (S && !S.sets.some(s => s.client_id === saved.client_id)) S.sets.push(saved);
    p = pending().filter(x => x.client_id !== saved.client_id);
    savePending(p);
  }
  render();
}

function log(exId, wInput, rInput) {
  const w = parseFloat(wInput.value), r = parseInt(rInput.value, 10);
  if (isNaN(w) || isNaN(r) || r < 1) { rInput.focus(); return; }
  const cid = Date.now().toString(36) + Math.random().toString(36).slice(2);
  const p = pending();
  p.push({ client_id: cid, date: date, exercise_id: exId, weight_kg: w, reps: r });
  savePending(p);
  render();
  flush();
}

async function remove(set) {
  if (set.pending) {
    savePending(pending().filter(x => x.client_id !== set.client_id));
  } else {
    try { await api("sets/delete", { id: set.id }); } catch (e) { alert("No signal — try again."); return; }
    S.sets = S.sets.filter(s => s.id !== set.id);
  }
  render();
}

function card(ex, plan) {
  const done = S.sets.filter(s => s.exercise_id === ex.id);
  const waiting = pending().filter(s => s.exercise_id === ex.id && s.date === date)
                           .map(s => Object.assign({ pending: true }, s));
  const all = done.concat(waiting);
  const last = S.last[String(ex.id)];
  const prev = all[all.length - 1];
  const w0 = prev ? prev.weight_kg : plan ? plan.weight_kg : last ? last.sets[0][0] : "";
  const r0 = plan ? plan.reps : prev ? prev.reps : last ? last.sets[0][1] : "";
  const wIn = el("input", { type: "number", inputmode: "decimal", step: "0.5", value: w0, "aria-label": "kg" });
  const rIn = el("input", { type: "number", inputmode: "numeric", step: "1", value: r0, "aria-label": "reps" });
  const c = el("div", { class: "card" }, el("div", { class: "name" }, ex.name));
  if (ex.notes) c.append(el("div", { class: "note" }, ex.notes));
  if (plan) {
    c.append(el("div", { class: "target" },
      "Plan: " + plan.sets + " × " + plan.reps + " @ " + kg(plan.weight_kg) + "  (" + all.length + "/" + plan.sets + ")"));
    if (plan.note) c.append(el("div", { class: "note" }, plan.note));
  }
  if (last) {
    c.append(el("div", { class: "last" },
      "Last " + last.date.slice(8) + "." + last.date.slice(5, 7) + ".: " + last.sets.map(s => s[0] + "×" + s[1]).join(", ")));
  }
  const row = el("div", { class: "sets" });
  for (const s of all) {
    row.append(el("span", { class: "set" + (s.pending ? " pending" : "") },
      kg(s.weight_kg) + " × " + s.reps,
      el("button", { onclick: () => remove(s), "aria-label": "delete" }, "✕")));
  }
  c.append(row);
  c.append(el("div", { class: "row" }, wIn, "kg", rIn, "reps",
    el("button", { class: "log", onclick: () => log(ex.id, wIn, rIn) }, "Log set")));
  return c;
}

function render() {
  const n = pending().length;
  const sync = document.getElementById("sync");
  sync.textContent = n ? n + " set" + (n > 1 ? "s" : "") + " waiting for signal" : (S ? "saved" : "offline");
  sync.className = n ? "waiting" : "";
  document.getElementById("day").textContent =
    "Gym · " + now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const cards = document.getElementById("cards");
  cards.replaceChildren();
  if (!S) { cards.append(el("p", { class: "empty" }, "No connection to the server. Is Tailscale on?")); return; }
  const byId = new Map(S.exercises.map(e => [e.id, e]));
  const order = [];
  for (const p of S.plan) order.push(p.exercise_id);
  for (const s of S.sets.concat(pending())) if (!order.includes(s.exercise_id)) order.push(s.exercise_id);
  for (const id of extra) if (!order.includes(id)) order.push(id);
  if (!order.length) cards.append(el("p", { class: "empty" }, "No plan for today. Add an exercise below."));
  for (const id of order) {
    const ex = byId.get(id);
    if (ex) cards.append(card(ex, S.plan.find(p => p.exercise_id === id)));
  }
  const pick = document.getElementById("pick");
  pick.replaceChildren(...S.exercises.filter(e => e.active && !order.includes(e.id))
    .map(e => el("option", { value: e.id }, e.name)));
}

document.getElementById("addbtn").onclick = () => {
  const id = parseInt(document.getElementById("pick").value, 10);
  if (!isNaN(id)) { extra.push(id); render(); }
};
document.getElementById("newbtn").onclick = async () => {
  const name = (prompt("New exercise name") || "").trim();
  if (!name) return;
  try { const ex = await api("exercises", { name: name }); extra.push(ex.id); await load(); }
  catch (e) { alert("No signal — try again."); }
};
window.addEventListener("online", flush);
setInterval(flush, 15000);
load();
</script>
</body>
</html>
"""
