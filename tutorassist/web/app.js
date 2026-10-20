/* TutorAssist — front end (no build step). */
"use strict";

const TOKEN = document.querySelector('meta[name="tutor-token"]').content;
const S = {
  state: null,            // /api/state
  view: "desk",
  convId: null,           // conversation shown in the thread
  conv: null,             // {conversation, runs:[{run, events}]}
  models: {},             // runId -> view model for live rendering
  streams: {},            // runId -> EventSource
  attachments: [],        // inbox paths queued in the composer
  student: null,          // name selected in Students
  draft: null,            // unsaved student edits
  dirty: false,
  matSel: null,
  focus: null,            // {student, date}: the student/session open on the desk
  docs: null,             // /api/students/{name}/documents for the focused student
  viewer: null,           // {title, pdf, source, mode, full}
  peek: null,             // {runId, prompt, open}: the reply shown over a full-screen document
  layout: { side: true, top: true },  // desk sidebar and header shown (kept in localStorage)
  library: null,
  oq: "",
};

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/* replaceChildren that skips null/false (h() children already do). */
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
  return el;
}

const ICONS = {
  x: "M6 6l12 12M18 6L6 18",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  download: "M12 4v11M7 10.5l5 5 5-5M5 20h14",
  doc: "M7 3.5h7l4 4v13H7zM14 3.5v4h4",
};
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name]);
  if (name === "more") { path.setAttribute("stroke-width", "3"); }
  svg.append(path);
  return svg;
}

/* A small ⋯ menu anchored to its button; closes on outside click or Escape. */
function menuButton(items, label = "More") {
  const wrap = h("span", { style: "position:relative;display:inline-flex" });
  const btn = h("button", { class: "icon-btn", title: label, "aria-label": label, "aria-haspopup": "menu", "aria-expanded": "false" }, icon("more"));
  let menu = null;
  const close = () => { if (!menu) return; menu.remove(); menu = null; btn.setAttribute("aria-expanded", "false"); document.removeEventListener("click", outside, true); };
  const outside = (e) => { if (!wrap.contains(e.target)) close(); };
  btn.addEventListener("click", () => {
    if (menu) return close();
    menu = h("div", { class: "menu", role: "menu", onkeydown: (e) => { if (e.key === "Escape") { close(); btn.focus(); } } },
      items.filter(Boolean).map((it) => h("button", { role: "menuitem", class: it.danger ? "danger" : "", onclick: () => { close(); it.run(); } }, it.label)));
    wrap.append(menu);
    btn.setAttribute("aria-expanded", "true");
    menu.querySelector("button")?.focus();
    setTimeout(() => document.addEventListener("click", outside, true));
  });
  wrap.append(btn);
  return wrap;
}

async function api(method, path, body) {
  const opts = { method, headers: { "x-tutor-token": TOKEN } };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}

async function upload(path, files) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const r = await fetch(path, { method: "POST", headers: { "x-tutor-token": TOKEN }, body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`);
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 3200);
}

const fail = (e) => toast(e.message || String(e), true);

function md(text, { breaks = true } = {}) {
  text = text || "";
  const html = DOMPurify.sanitize(marked.parse(text, { breaks }));
  const div = h("div", { class: "md", html });
  // Markdown renumbers ordered lists 1, 2, 3…; keep the numbers the author wrote
  // (open questions are referred to by number, e.g. "item 12").
  const numbers = [];
  const walk = (tokens) => {
    for (const t of tokens || []) {
      if (t.type === "list" && t.ordered) numbers.push(t.items.map((it) => parseInt(it.raw, 10)));
      if (t.type === "list") t.items.forEach((it) => walk(it.tokens));
      else if (t.tokens) walk(t.tokens);
    }
  };
  try { walk(marked.lexer(text)); } catch { /* numbering is cosmetic */ }
  div.querySelectorAll("ol").forEach((ol, i) => {
    const nums = numbers[i] || [];
    [...ol.children].forEach((li, j) => { if (Number.isFinite(nums[j])) li.value = nums[j]; });
  });
  linkifyPaths(div);
  return div;
}

const PATH_RE = /((?:material|inbox|uploads|reference)\/[^\s`'"<>()\[\]]+\.(?:pdf|txt|png|jpe?g|md))/g;
function fileUrl(path) {
  return "/files/" + path.split("/").map(encodeURIComponent).join("/");
}
function linkifyPaths(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (n.parentElement.closest("a, pre")) continue;
    PATH_RE.lastIndex = 0;
    if (PATH_RE.test(n.nodeValue)) nodes.push(n);
  }
  for (const n of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    n.nodeValue.replace(PATH_RE, (m, p, idx) => {
      frag.append(n.nodeValue.slice(last, idx));
      frag.append(h("a", { href: fileUrl(p), onclick: (e) => { e.preventDefault(); p.endsWith(".pdf") ? openDoc(p) : openViewer({ title: p.split("/").pop(), file: p }); } }, p));
      last = idx + m.length;
    });
    frag.append(n.nodeValue.slice(last));
    n.replaceWith(frag);
  }
}

const tz = () => S.state?.config?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
function todayISO() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz() }).format(new Date());
}
function parseDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}
function dayName(iso) {
  const t = todayISO();
  const diff = Math.round((parseDate(iso) - parseDate(t)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return parseDate(iso).toLocaleDateString(undefined, { weekday: "long" });
}
function shortDate(iso) {
  return parseDate(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function clock(hhmm) {
  if (!hhmm) return { t: "all day", ap: "" };
  let [hh, mm] = hhmm.split(":").map(Number);
  const ap = hh >= 12 ? "pm" : "am";
  hh = hh % 12 || 12;
  return { t: mm ? `${hh}:${String(mm).padStart(2, "0")}` : String(hh), ap };
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function minutes(m) {
  if (!m && m !== 0) return "";
  const hrs = Math.floor(m / 60), mins = m % 60;
  return hrs ? `${hrs} h${mins ? ` ${mins} min` : ""}` : `${mins} min`;
}
const FIELD_NAMES = { coverNextSession: "cover next", strengths: "strengths", weaknesses: "weaknesses",
  subject: "subject", hw: "homework", status: "status", textbook: "textbook", name: "name" };

function students() {
  return S.state?.roster?.students || [];
}

/* ------------------------------------------------------------------ state + live updates */
async function loadState() {
  S.state = await api("GET", "/api/state");
  renderChrome();
  renderSchedule();
  renderConvs();
  if (S.state.setupNeeded && !$("#overlay .setup")) openSetup();
}

let refreshTimer;
function softRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => loadState().catch(() => {}), 250);
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.t === "run") {
      softRefresh();
      if (S.focus && !["queued", "running"].includes(ev.run.status)) { loadDocs(); reloadIfChanged(ev.run); }
      if (ev.run.conversationId === S.convId) {
        const known = S.conv?.runs?.some((r) => r.run.id === ev.run.id);
        if (!known) openConversation(S.convId, false);
        else if (!S.streams[ev.run.id] && ["queued", "running"].includes(ev.run.status)) streamRun(ev.run.id);
      }
    } else if (ev.t === "changed") {
      if (ev.what === "roster.json") {
        softRefresh();
        if (S.view === "students" && !S.dirty) setTimeout(renderStudents, 300);
      } else if (ev.what === "schedule.json") softRefresh();
      else if (ev.what === "open-questions.md") loadQuestions();
      else if (ev.what === "books.json" && S.view === "library") loadLibrary();
    }
  };
}

function renderChrome() {
  const busy = S.state.busy;
  $("#busy").hidden = !busy;
}

/* ------------------------------------------------------------------ views */
function moveNavHl() {
  const hl = $(".nav-hl");
  const b = $(".tabs button.active");
  if (!hl || !b || b.hidden) { if (hl) hl.classList.remove("on"); return; }
  hl.style.left = `${b.offsetLeft + 6}px`;
  hl.style.width = `${b.offsetWidth - 12}px`;
  hl.classList.add("on");
}

function show(view) {
  S.view = view;
  if (view === "questions") $("#tab-questions").hidden = false;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  moveNavHl();
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === view));
  if (view === "students") renderStudents();
  if (view === "materials") loadMaterials();
  if (view === "library") loadLibrary();
  if (view === "questions") loadQuestions();
  if (view === "settings") renderSettings();
  history.replaceState(null, "", "#" + view);
}

/* ------------------------------------------------------------------ schedule */
function renderSchedule() {
  const sch = S.state.schedule;
  const note = $("#cal-note");
  note.hidden = true;
  note.className = "cal-note";
  if (sch.error) {
    note.hidden = false;
    note.textContent = `Calendar: ${sch.error}`;
  } else if (!S.state.config.calendar.icsUrl) {
    note.hidden = false;
    note.className = "cal-note warn";
    fill(note, "No calendar yet. ", h("button", { class: "link", onclick: () => show("settings") }, "Connect one"));
  } else if (sch.warning) {
    note.hidden = false;
    note.className = "cal-note warn";
    note.textContent = sch.warning;
  }
  const today = todayISO();
  const days = [...sch.days];
  if (!days.some((d) => d.date === today)) days.unshift({ date: today, events: [] });
  const nowHM = new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
  let nextMarked = false;
  const list = $("#days");
  fill(list, 
    ...days.filter((d) => d.date >= today).map((d) =>
      h("li", { class: "day" },
        h("div", { class: "day-label" }, h("strong", {}, dayName(d.date)), h("span", {}, shortDate(d.date))),
        d.events.length
          ? d.events.map((ev) => {
              const c = clock(ev.time);
              const past = d.date === today && ev.time && endHM(ev) <= nowHM;
              let next = false;
              if (!past && !nextMarked) { next = true; nextMarked = true; }
              const who = ev.student || ev.summary;
              const meta = ev.known ? minutes(ev.minutes) : "Not on your roster";
              const selected = S.focus && ev.known && S.focus.student === ev.student && S.focus.date === d.date;
              const open = () => selectStudent(ev.student, d.date);
              return h("div", {
                class: "session" + (past ? " past" : "") + (next ? " next" : "") + (ev.known ? " pick" : "") + (selected ? " selected" : ""),
                role: ev.known ? "button" : undefined, tabindex: ev.known ? "0" : undefined,
                "aria-label": ev.known ? `Open ${ev.student}'s ${dayName(d.date).toLowerCase()} session` : undefined,
                onclick: ev.known ? (e) => { if (!e.target.closest(".x")) open(); } : undefined,
                onkeydown: ev.known ? (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); open(); } } : undefined,
              },
                h("div", { class: "time" }, h("span", { class: "hl" }, c.t, h("span", { class: "ampm" }, c.ap))),
                h("div", {},
                  h("div", { class: "who" }, who),
                  h("div", { class: "meta" + (ev.known ? "" : " unmatched") }, meta)),
                ev.source === "manual"
                  ? h("button", { class: "icon-btn x", title: "Remove this session", "aria-label": `Remove ${who}'s session`, onclick: () => removeManual(ev.id) }, icon("x"))
                  : h("span", {}));
            })
          : h("div", { class: "day-empty" }, "No sessions"))));
  renderSideStudents();
}

function renderSideStudents() {
  const list = [...students()].sort((a, b) => (a.status === "paused") - (b.status === "paused") || a.name.localeCompare(b.name));
  fill($("#side-students"), list.map((st) => h("li", {},
    h("button", { class: (S.focus?.student === st.name ? "on" : "") + (st.status === "paused" ? " paused" : ""),
      "aria-pressed": S.focus?.student === st.name ? "true" : "false",
      onclick: () => (S.focus?.student === st.name ? clearFocus() : selectStudent(st.name)) }, st.name))),
    list.length ? null : h("li", { class: "side-empty" }, "No students yet"));
}

function endHM(ev) {
  const iso = ev.end?.dateTime;
  if (!iso) return "99:99";
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

async function removeManual(id) {
  try {
    const sched = await api("DELETE", `/api/schedule/manual/${encodeURIComponent(id.replace(/^manual:/, ""))}`);
    S.state.schedule = sched;
    renderSchedule();
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------------ runs */
async function startPrep(date, only = []) {
  const today = todayISO();
  if (!only.length && date === today) {
    const todays = S.state.schedule.days.find((d) => d.date === today);
    if (!todays || !todays.events.length) {
      toast("No sessions today. Pick a student to prep a single session.");
      return;
    }
  }
  try {
    const r = await api("POST", "/api/runs", { kind: "prep", date, students: only });
    show("desk");
    await openConversation(r.conversation.id);
    softRefresh();
  } catch (e) { fail(e); }
}

async function sendChat(text) {
  const body = { kind: "chat", text, attachments: S.attachments };
  if (S.convId) body.conversationId = S.convId;
  if (S.focus) { body.student = S.focus.student; body.date = S.focus.date; }
  // The document on screen, so "make problem 3 easier" means that sheet.
  const open = S.viewer && (S.viewer.pdf || S.viewer.source);
  if (S.focus && open) body.document = open;
  const r = await api("POST", "/api/runs", body);
  S.attachments = [];
  renderAttachments();
  await openConversation(r.conversation.id);
  softRefresh();
  return r;
}

/* ------------------------------------------------------------------ conversations + thread */
function renderConvs() {
  const all = S.state.conversations;
  const convs = S.focus ? all.filter((c) => c.student === S.focus.student) : all;
  $("#convs-title").textContent = S.focus ? `About ${S.focus.student}` : "Conversations";
  const runs = S.state.runs;
  fill($("#convs"), 
    ...convs.map((c) => {
      const last = runs[c.runIds[c.runIds.length - 1]];
      const st = last?.status || "done";
      return h("li", {},
        h("button", { class: c.id === S.convId ? "active" : "", onclick: () => openConversation(c.id) },
          h("span", { class: "ct" }, h("span", { class: "dot " + st }), c.title),
          h("span", { class: "cw" }, ago(c.updatedAt))));
    }),
    convs.length ? null : h("li", { class: "side-empty" }, "Nothing yet"));
  $("#composer-input").placeholder = S.focus ? `Ask about ${S.focus.student}…` : "Ask anything…";
}

async function openConversation(id, scroll = true) {
  const meta = S.state?.conversations?.find((c) => c.id === id);
  if (meta?.student && meta.student !== S.focus?.student && students().some((x) => x.name === meta.student)) {
    S.focus = { student: meta.student, date: null };
    saveFocus();
    loadDocs();
  }
  S.convId = id;
  for (const es of Object.values(S.streams)) es.close();
  S.streams = {};
  S.models = {};
  try {
    S.conv = await api("GET", `/api/conversations/${id}`);
  } catch (e) {
    S.convId = null; S.conv = null; renderThread(); return fail(e);
  }
  renderConvs();
  renderThread();
  for (const { run } of S.conv.runs) {
    if (["queued", "running"].includes(run.status)) streamRun(run.id);
  }
  if (scroll) scrollThread(true);
}

function newConversation(focusInput = true) {
  S.convId = null;
  S.conv = null;
  for (const es of Object.values(S.streams)) es.close();
  S.streams = {};
  renderConvs();
  renderThread();
  if (focusInput) $("#composer-input").focus();
}

function renderThread() {
  const th = $("#thread");
  if (!S.conv && S.focus) {
    const ask = ["Make the eval shorter", "Swap problem 3 for an easier one", "What should we cover next?"];
    fill(th, h("div", { class: "welcome" },
      h("h2", {}, `Ask about ${S.focus.student}`),
      h("p", {}, (() => {
        const w = when(S.focus.date || todayISO());
        return `Changes apply to ${["today", "tomorrow", "yesterday"].includes(w) ? `${w}'s session` : `the session ${w}`}.`;
      })()),
      h("div", { class: "chips" }, ask.map((q) => h("button", { class: "chip", onclick: () => prefill(q) }, q)))));
    return;
  }
  if (!S.conv) {
    const name = S.state?.config?.tutorName;
    const hour = Number(new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", hour12: false }).format(new Date()));
    const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const today = S.state?.schedule?.days?.find((d) => d.date === todayISO());
    const nowHM = new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
    const known = today ? today.events.filter((e) => e.known) : [];
    const upcoming = known.filter((e) => !e.time || endHM(e) > nowHM);
    fill(th, h("div", { class: "welcome" },
      h("h1", {}, name ? `${part}, ${name.split(" ")[0]}.` : `${part}.`),
      h("p", {}, !known.length ? "No sessions today."
        : upcoming.length ? `${upcoming.length} session${upcoming.length === 1 ? "" : "s"} still to go today.`
        : "That's all of today's sessions."),
      upcoming.length ? h("div", { class: "chips" }, upcoming.map((e) => {
        const c = clock(e.time);
        return h("button", { class: "chip", onclick: () => selectStudent(e.student, todayISO()) },
          h("span", { class: "ct" }, `${c.t} ${c.ap}`), e.student);
      })) : null));
    return;
  }
  fill(th, ...S.conv.runs.flatMap(({ run, events }) => {
    const model = buildModel(run, events);
    S.models[run.id] = model;
    return [userMsg(run), (model.el = h("div", { class: "msg agent", id: "run-" + run.id }))];
  }));
  for (const { run } of S.conv.runs) paintAgent(S.models[run.id]);
}

function userMsg(run) {
  let text = run.prompt.replace(/\n\nAttached files \(read them\):[\s\S]*$/, "");
  return h("div", { class: "msg user" },
    h("div", { class: "bubble" }, text,
      run.attachments?.length ? h("div", { class: "att" }, `${run.attachments.length} attachment${run.attachments.length > 1 ? "s" : ""}: ${run.attachments.map((a) => a.split("/").pop()).join(", ")}`) : null));
}

function buildModel(run, events) {
  const m = { run, texts: [], buffer: "", tools: [], todos: null, files: run.files || [], roster: run.rosterChanges || [],
    result: null, errors: [], status: "", lastSeq: -1, el: null };
  for (const ev of events) applyEvent(m, ev);
  return m;
}

function applyEvent(m, ev) {
  if (ev.seq !== undefined) {
    if (ev.seq <= m.lastSeq) return;
    m.lastSeq = ev.seq;
  }
  switch (ev.t) {
    case "status": m.status = ev.text; break;
    case "init": m.status = "Reading CLAUDE.md and the roster…"; break;
    case "delta": m.buffer += ev.text; break;
    case "text":
      if (!ev.sub) { m.texts.push(ev.text); m.buffer = ""; }
      break;
    case "tool": m.tools.push({ ...ev, ok: null }); m.status = ""; break;
    case "tool_result": {
      const t = m.tools.find((x) => x.id === ev.id);
      if (t) t.ok = ev.ok;
      break;
    }
    case "todos": m.todos = ev.items; break;
    case "build": {
      const first = (ev.text || "").split("\n")[0];
      m.tools.push({ id: "build-" + (ev.seq ?? m.tools.length), summary: first.replace(/^Built /, "Built PDF: "), ok: !!ev.ok });
      if (!ev.ok && ev.final) m.errors.push(ev.text);
      break;
    }
    case "files": m.files = ev.files; m.extra = ev.extra || []; break;
    case "roster": m.roster = ev.changes; break;
    case "result": m.result = ev; break;
    case "error": m.errors.push(ev.text); break;
    case "done": m.run = { ...m.run, status: ev.status }; break;
  }
}

function paintAgent(m) {
  if (!m.el) return;
  const run = m.run;
  const running = ["queued", "running"].includes(run.status);
  const stateText = { queued: "Waiting for the previous run", running: "Working", done: "Done", error: "Didn't finish", stopped: "Stopped" }[run.status] || run.status;
  const named = {
    prep: { running: "Prepping", done: "Prep done" },
    feedback: { running: "Logging feedback", done: "Feedback logged" },
  }[run.kind] || {};
  const label = named[run.status] || (run.kind === "prep" && !running ? `Prep: ${stateText.toLowerCase()}` : stateText);
  const head = h("div", { class: "agent-head" },
    h("span", { class: "state " + run.status }, running ? h("span", { class: "pulse" }) : null, label),
    running
      ? h("button", { class: "link danger", onclick: () => api("POST", `/api/runs/${run.id}/cancel`).catch(fail) }, "Stop")
      : h("span", { class: "cost" }, duration(run)));

  // Narration (all text blocks but the last) and tools form the activity log.
  const finalText = agentText(m);
  const steps = [];
  m.tools.forEach((t) => steps.push(t));
  let activity = null;
  if (steps.length || (running && m.status)) {
    const items = steps.map((t) => h("li", { class: (t.ok === false ? "fail" : "") + (t.sub ? " sub" : "") },
      h("span", { class: "mark" }, t.ok === null ? "·" : t.ok ? "✓" : "✗"), h("span", {}, t.summary)));
    activity = h("details", { class: "activity", open: running ? true : undefined },
      h("summary", {}, running ? `${steps.length} step${steps.length === 1 ? "" : "s"} so far` : `${steps.length} step${steps.length === 1 ? "" : "s"}`),
      h("ol", {}, items),
      running && m.status ? h("div", { class: "now" }, m.status) : null);
    if (running) requestAnimationFrame(() => { const ol = activity.querySelector("ol"); if (ol) ol.scrollTop = ol.scrollHeight; });
  }
  const todos = m.todos && m.todos.length
    ? h("ul", { class: "todos" }, m.todos.map((t) => h("li", { class: t.status },
        h("span", { class: "box" }, t.status === "completed" ? "☑" : "☐"), t.text)))
    : null;
  const body = finalText ? md(finalText) : null;
  if (body && running && !m.result) body.classList.add("streaming");

  const pdfs = (m.files || []).filter((f) => f.endsWith(".pdf"));
  const extra = m.extra || m.run.extraFiles || [];
  const files = (pdfs.length || extra.length) ? h("div", { class: "files" },
    pdfs.map((f) => fileChip(f)),
    extra.length ? h("span", { class: "muted small" }, "Other files: ",
      extra.map((f, i) => [i ? ", " : "", h("a", { href: fileUrl(f), onclick: (e) => { e.preventDefault(); openViewer({ title: f.split("/").pop(), file: f }); } }, f.split("/").pop())])) : null) : null;
  const roster = m.roster?.length ? h("div", { class: "roster-note" }, h("strong", {}, "Updated "),
    m.roster.map((c) => `${c.name} (${c.change === "updated" ? c.fields.map((f) => FIELD_NAMES[f] || f).join(", ") : c.change})`).join("; ")) : null;
  const errs = m.errors.length && run.status !== "stopped" ? h("div", { class: "msg-error" }, m.errors.join("\n")) : null;
  fill(m.el, head, todos, activity, body, files, roster, errs);
  if (S.peek?.runId === run.id) paintPeek();
}

/* The reply so far: the final answer once there is one, else the latest text. */
function agentText(m) {
  const running = ["queued", "running"].includes(m.run.status);
  const last = m.texts[m.texts.length - 1];
  return m.result?.text || (running ? m.buffer || last : last) || "";
}

function duration(run) {
  if (!run.startedAt || !run.endedAt) return "";
  const s = (new Date(run.endedAt) - new Date(run.startedAt)) / 1000;
  return s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`;
}

function docInfo(path) {
  const name = path.split("/").pop();
  const m = name.match(/^(.+)_(\d{4}-\d{2}-\d{2})_(Guided|Eval|Eval_Answers|Homework|Homework_Answers)\.pdf$/);
  if (!m) return null;
  return { student: m[1], date: m[2], type: m[3], source: `.work/sources/${m[2]}/${m[1]}_${m[3]}.txt` };
}

const TYPE_LABEL = { Guided: "Guided worksheet", Eval: "Eval", Eval_Answers: "Eval answers", Homework: "Homework", Homework_Answers: "Homework answers" };

function fileChip(path) {
  const d = docInfo(path);
  const label = d ? [h("strong", {}, d.student), " ", TYPE_LABEL[d.type]] : path.split("/").pop();
  return h("a", { class: "file-chip", href: fileUrl(path), title: path, onclick: (e) => {
    e.preventDefault();
    openDoc(path);
  } }, h("span", { class: "kind" }, "PDF"), label);
}

/* Open a session PDF in the viewer, with its Worksheet Studio source when there is one. */
async function openDoc(path) {
  const d = docInfo(path);
  let source = null;
  if (d) {
    // one Eval / Homework source makes both the student copy and the answers
    const tries = [d.source];
    if (d.type.endsWith("_Answers")) tries.push(d.source.replace(/_Answers\.txt$/, ".txt"));
    for (const t of tries) {
      const r = await fetch(fileUrl(t), { method: "HEAD" }).catch(() => null);
      if (r && r.ok) { source = t; break; }
    }
  }
  openViewer({ title: d ? `${d.student}: ${TYPE_LABEL[d.type]} (${shortDate(d.date)})` : path.split("/").pop(), pdf: path, source });
}

function streamRun(runId) {
  if (S.streams[runId]) return;
  const m = S.models[runId];
  const es = new EventSource(`/api/runs/${runId}/stream?after=${m ? m.lastSeq : -1}`);
  S.streams[runId] = es;
  let pending = false;
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    const model = S.models[runId];
    if (!model) return;
    const nearBottom = isNearBottom();
    applyEvent(model, ev);
    if (!pending) {
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        paintAgent(model);
        if (nearBottom) scrollThread();
      });
    }
    if (ev.t === "done") {
      es.close();
      delete S.streams[runId];
      api("GET", `/api/runs/${runId}`).then((r) => {
        model.run = r.run;
        model.files = r.run.files;
        model.roster = r.run.rosterChanges;
        paintAgent(model);
        reloadIfChanged(r.run);
      }).catch(() => {});
      softRefresh();
    }
  };
  es.onerror = () => {
    // The browser reconnects automatically; on reconnect resume after the last event we saw.
    es.close();
    delete S.streams[runId];
    setTimeout(() => { if (S.models[runId] && ["queued", "running"].includes(S.models[runId].run.status)) streamRun(runId); }, 2000);
  };
}

function isNearBottom() {
  const th = $("#thread");
  return th.scrollHeight - th.scrollTop - th.clientHeight < 120;
}
function scrollThread(force) {
  const th = $("#thread");
  if (force || isNearBottom()) th.scrollTop = th.scrollHeight;
}


/* ------------------------------------------------------------------ selected student (desk) */
function when(date) {
  if (!date) return "";
  const n = dayName(date);
  if (["Today", "Tomorrow", "Yesterday"].includes(n)) return n.toLowerCase();
  return "on " + parseDate(date).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function chipLabel(date) {
  const n = dayName(date);
  return ["Today", "Tomorrow", "Yesterday"].includes(n) ? n : shortDate(date);
}

function saveFocus() {
  try { localStorage.setItem("ta.focus", JSON.stringify(S.focus)); } catch { /* private mode etc. */ }
}
function restoreFocus() {
  try {
    const f = JSON.parse(localStorage.getItem("ta.focus") || "null");
    if (f && students().some((x) => x.name === f.student)) return f;
  } catch { /* ignore */ }
  return null;
}

/* Put a suggestion in the composer for the tutor to send (or edit first). */
function prefill(text) {
  const ta = $("#composer-input");
  ta.value = text;
  ta.dispatchEvent(new Event("input"));
  ta.focus();
}

let focusEntered = null;  // the student whose panel already played its entrance
function selectStudent(name, date = null) {
  if (S.focus?.student !== name) { S.viewer = null; S.docs = null; paintViewer(); }
  S.focus = { student: name, date };
  saveFocus();
  if (S.view !== "desk") show("desk");
  renderSchedule();
  paintFocus();
  const conv = S.state.conversations.find((c) => c.student === name);
  if (conv) { if (conv.id !== S.convId) openConversation(conv.id); else { renderConvs(); } }
  else newConversation(false);
  loadDocs();
}

function clearFocus() {
  S.focus = null; S.docs = null; S.viewer = null; focusEntered = null;
  saveFocus();
  paintFocus(); paintViewer(); renderSchedule(); renderConvs();
  if (!S.conv) renderThread();
}

async function loadDocs() {
  if (!S.focus) return;
  const name = S.focus.student;
  let d;
  try { d = await api("GET", `/api/students/${encodeURIComponent(name)}/documents`); } catch { return; }
  if (S.focus?.student !== name) return;
  S.docs = d;
  if (!S.focus.date) {
    // Today's session if there is one, else the next one coming up, else the latest with documents.
    const t = todayISO(), ss = d.sessions;
    const pick = ss.find((x) => x.date === t) || [...ss].reverse().find((x) => x.date > t) || ss.find((x) => x.date < t);
    S.focus.date = pick?.date || t;
    saveFocus();
    renderSchedule(); renderConvs();
    if (!S.conv) renderThread();
  }
  paintFocus();
}

function emptySession(date) {
  return { date, session: null, other: [],
    docs: Object.entries(TYPE_LABEL).map(([type, label]) => ({ type, label, pdf: null, source: null })) };
}

function paintFocus() {
  const box = $("#focus");
  if (!S.focus) { box.hidden = true; return; }
  box.hidden = false;
  const st = students().find((x) => x.name === S.focus.student) || { name: S.focus.student };
  const sessions = S.docs?.student === S.focus.student ? [...S.docs.sessions] : [];
  if (S.focus.date && !sessions.some((x) => x.date === S.focus.date)) {
    sessions.push(emptySession(S.focus.date));
    sessions.sort((a, b) => b.date.localeCompare(a.date));
  }
  const cur = sessions.find((x) => x.date === S.focus.date) || emptySession(S.focus.date || todayISO());
  const date = cur.date;

  $("#focus-name").textContent = st.name;
  const bits = [st.subject];
  if (cur.session?.time) {
    const c = clock(cur.session.time);
    bits.push(`${when(date)} at ${c.t} ${c.ap}` + (cur.session.minutes ? `, ${minutes(cur.session.minutes)}` : ""));
  }
  $("#focus-meta").textContent = bits.filter(Boolean).join(" — ");

  const hasPrep = cur.docs.some((x) => ["Guided", "Eval", "Eval_Answers"].includes(x.type) && x.pdf);
  const prep = async () => {
    if (hasPrep && !confirm(`Redo prep for ${st.name} ${when(date)}? The new documents replace the current ones.`)) return;
    startPrep(date, [st.name]);
  };
  const upcoming = date >= todayISO();
  fill($("#focus-actions"),
    !hasPrep && upcoming ? h("button", { class: "btn small primary", onclick: prep }, "Prep") : null,
    menuButton([
      hasPrep || !upcoming ? { label: hasPrep ? "Redo prep" : "Prep this session", run: prep } : null,
      { label: "Edit student", run: () => { show("students"); pickRosterStudent(st.name); } },
    ]),
    h("button", { class: "icon-btn", onclick: clearFocus, title: "Close", "aria-label": "Close student" }, icon("x")));

  fill($("#focus-dates"), sessions.slice(0, 12).map((x) => {
    const n = x.docs.filter((d) => d.pdf).length;
    return h("button", { class: "date-chip", role: "tab", "aria-selected": x.date === date ? "true" : "false",
      onclick: () => { S.focus.date = x.date; saveFocus(); paintFocus(); renderSchedule(); renderConvs(); if (!S.conv) renderThread(); } },
      chipLabel(x.date), n ? h("span", { class: "n" }, n) : null);
  }));

  // The entrance plays once per student you open, not on every repaint. The class stays
  // on briefly so the tiles that arrive with the first document load fan in too.
  if (focusEntered !== st.name) {
    focusEntered = st.name;
    box.classList.add("enter");
    clearTimeout(paintFocus.t);
    paintFocus.t = setTimeout(() => box.classList.remove("enter"), 1000);
  }
  fill($("#doc-tiles"), cur.docs.map((d, i) => {
    const title = `${st.name}: ${d.label} (${shortDate(date)})`;
    if (d.pdf) {
      return h("div", { class: "tile-wrap", style: `--i:${i}` },
        h("button", { class: "tile made" + (S.viewer?.pdf === d.pdf ? " on" : ""), title: `Open ${d.label}`,
          onclick: () => { openViewer({ title, pdf: d.pdf, source: d.source }); } },
          h("span", { class: "glyph" }, icon("doc")), h("span", { class: "tt" }, d.label)),
        h("a", { class: "icon-btn tile-dl", href: fileUrl(d.pdf) + "?download=1", download: d.pdf.split("/").pop(),
          title: `Download ${d.label}`, "aria-label": `Download ${d.label}` }, icon("download")));
    }
    const isHw = d.type.startsWith("Homework");
    let hint;
    if (d.source) hint = "Text only. Open to build";
    else if (!isHw && date < todayISO()) hint = "Not made";
    else if (!isHw) hint = "Not prepped yet";
    else if (st.hw) hint = "After feedback";
    else hint = "No homework";
    const open = d.source ? () => openViewer({ title, pdf: null, source: d.source, mode: "source" }) : undefined;
    return h("div", { class: "tile-wrap", style: `--i:${i}` }, h(open ? "button" : "div", { class: "tile missing" + (open ? " can" : ""), onclick: open, title: `${d.label}: ${hint}` },
      h("span", { class: "glyph" }, icon("doc")), h("span", { class: "tt" }, d.label), h("span", { class: "ts" }, hint)));
  }));

  fill($("#doc-other"), cur.other.length ? [
    h("span", {}, "Other files:"),
    cur.other.map((o) => h("button", { class: "link", onclick: () => openViewer({ title: o.name, file: o.path }) }, o.name))] : null);
}

/* ------------------------------------------------------------------ document viewer */
function openViewer(v) {
  if (!leaveEditor()) return;
  S.viewer = { ...v, mode: v.mode || (v.pdf ? "pdf" : "file"), full: !!S.viewer?.full };
  paintViewer();
  paintFocus();
}

/* Full screen moves the same viewer element on top of everything (CSS only), so the
   PDF isn't reloaded and keeps its scroll position. */
function setFull(on) {
  if (!S.viewer) return;
  S.viewer.full = on;
  paintViewerChrome();
  if (on) {
    paintPeek();
    setTimeout(() => $("#fs-input").focus(), 60);
  } else {
    $("#viewer-expand").focus({ preventScroll: true });
  }
}

/* After a run, show the new version of the open document if the run rewrote it. */
function reloadIfChanged(run) {
  const v = S.viewer;
  if (!v || !run || v.reloadedFor === run.id) return;
  const files = run.files || [];
  if (!(v.pdf && files.includes(v.pdf)) && !(v.source && files.includes(v.source))) return;
  v.reloadedFor = run.id;
  v.changedBy = run.id;
  v.bust = Date.now();
  const frame = $("#viewer-body iframe");
  if (v.mode === "source") {
    if (v.draft === undefined || v.draft === v.saved) { v.draft = undefined; v.saved = undefined; paintViewer(); }
  } else if (frame) {
    frame.src = fileUrl(v.pdf) + `?v=${v.bust}`;
  }
  paintPeek();
}
function closeViewer() {
  if (!leaveEditor()) return;
  S.viewer = null;
  paintViewer();
  paintFocus();
}
/* The source editor holds unsaved text in S.viewer.draft; ask before throwing it away. */
function leaveEditor() {
  const v = S.viewer;
  if (!v || v.draft === undefined || v.draft === v.saved) return true;
  return confirm("Discard your unsaved changes to this worksheet's text?");
}
const isSource = (path) => /^\.work\/sources\/\d{4}-\d{2}-\d{2}\/[^/]+\.txt$/.test(path || "");

function sourceEditor(v) {
  const status = h("span", { class: "src-status" }, "Rebuilding doesn't use Claude.");
  const ta = h("textarea", { class: "src-text", spellcheck: "false", disabled: true }, "");
  const save = h("button", { class: "btn small primary", disabled: true }, "Save & rebuild PDF");
  const revert = h("button", { class: "btn small", disabled: true }, "Undo changes");
  const sync = () => {
    const dirty = v.draft !== v.saved;
    save.disabled = !dirty && !!v.pdf;
    revert.disabled = !dirty;
    if (!v.pdf && !dirty) save.textContent = "Build PDF";
    else save.textContent = "Save & rebuild PDF";
  };
  ta.addEventListener("input", () => { v.draft = ta.value; sync(); });
  revert.addEventListener("click", () => { v.draft = v.saved; ta.value = v.saved; sync(); });
  save.addEventListener("click", async () => {
    save.disabled = true;
    status.className = "src-status";
    status.textContent = "Building…";
    try {
      const r = await api("PUT", "/api/sources", { path: v.source, text: v.draft });
      v.saved = v.draft;
      status.className = "src-status " + (r.ok ? "ok" : "bad");
      status.textContent = r.message.replace(/\nFix the \.txt and save it again.*$/m, "");
      v.bust = Date.now();
      if (r.outputs?.length && !v.pdf) v.pdf = r.outputs[0].pdf;
      if (S.focus) loadDocs();
    } catch (e) {
      status.className = "src-status bad";
      status.textContent = e.message;
    }
    sync();
  });
  const load = v.draft !== undefined ? Promise.resolve(v.draft)
    : fetch(fileUrl(v.source)).then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.statusText))))
        .then((t) => { v.saved = t; v.draft = t; return t; });
  load.then((t) => { ta.value = t; ta.disabled = false; sync(); })
    .catch((e) => { status.textContent = `Couldn't load the text (${e.message}).`; });
  return h("div", { class: "src-editor" }, ta, h("div", { class: "src-bar" }, save, revert, status));
}
function paintViewerChrome() {
  const v = S.viewer;
  const full = !!v?.full;
  const box = $("#viewer");
  box.classList.toggle("full", full);
  box.classList.toggle("src", !!v && v.mode === "source");
  document.body.classList.toggle("doc-full", full);
  $("#viewer-expand").hidden = full;
  $("#viewer-exit").hidden = !full;
  $("#viewer-close").hidden = full;
  if (!full) $("#fs-reply").hidden = true;
}

function paintViewer() {
  const box = $("#viewer");
  const v = S.viewer;
  if (!v) { box.hidden = true; fill($("#viewer-body")); paintViewerChrome(); return; }
  box.hidden = false;
  paintViewerChrome();
  $("#viewer-title").textContent = v.title;
  fill($("#viewer-seg"), v.pdf && v.source
    ? [["pdf", "PDF"], ["source", isSource(v.source) ? "Edit text" : "Text"]].map(([k, label]) => h("button", { "aria-pressed": v.mode === k ? "true" : "false",
        onclick: () => { v.mode = k; paintViewer(); } }, label))
    : null);
  const path = v.mode === "source" ? v.source : (v.pdf || v.file);
  const dl = $("#viewer-download");
  dl.href = fileUrl(path) + "?download=1";
  dl.setAttribute("download", path.split("/").pop());
  const body = $("#viewer-body");
  if (v.mode === "source" && isSource(v.source)) {
    fill(body, sourceEditor(v));
  } else if (path.toLowerCase().endsWith(".pdf")) {
    fill(body, h("iframe", { src: fileUrl(path) + (v.bust ? `?v=${v.bust}` : ""), title: v.title }));
  } else if (/\.(png|jpe?g|gif|webp|heic)$/i.test(path)) {
    fill(body, h("div", { style: "flex:1;overflow:auto;padding:12px" }, h("img", { src: fileUrl(path), alt: v.title, style: "max-width:100%" })));
  } else {
    const pre = h("pre", {}, "Loading…");
    fill(body, pre);
    fetch(fileUrl(path)).then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.statusText))))
      .then((t) => { pre.textContent = t; }).catch((e) => { pre.textContent = `Couldn't load this file (${e.message}).`; });
  }
}

/* ------------------------------------------------------------------ full-screen prompt + reply */
function paintPeek() {
  const box = $("#fs-reply");
  const p = S.peek;
  const m = p && S.models[p.runId];
  if (!p || !p.open || !S.viewer?.full) { box.hidden = true; return; }
  box.hidden = false;
  const run = m?.run || { id: p.runId, status: "queued" };
  const running = ["queued", "running"].includes(run.status);
  const label = { queued: "Waiting", running: "Working", done: "Done", error: "Didn't finish", stopped: "Stopped" }[run.status] || run.status;
  const text = m ? agentText(m) : "";
  const step = m?.status || (m?.tools?.length ? m.tools[m.tools.length - 1].summary : "") || "Reading your message…";
  const reply = text ? md(text) : null;
  if (reply && running && !m.result) reply.classList.add("streaming");
  const bodyEl = box.querySelector(".fs-reply-body");
  const stick = !bodyEl || bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 40;
  const body = h("div", { class: "fs-reply-body" },
    h("p", { class: "fs-q", title: p.prompt }, p.prompt),
    reply || (running ? h("p", { class: "fs-wait" }, step) : null),
    S.viewer?.changedBy === run.id ? h("p", { class: "fs-note" }, "Updated the sheet you're looking at.") : null,
    m?.errors?.length && run.status !== "stopped" ? h("div", { class: "msg-error" }, m.errors.join("\n")) : null);
  fill(box,
    h("div", { class: "fs-reply-head" },
      h("span", { class: "state " + run.status }, running ? h("span", { class: "pulse" }) : null, label),
      running ? h("button", { class: "link danger", onclick: () => api("POST", `/api/runs/${run.id}/cancel`).catch(fail) }, "Stop") : null,
      h("button", { class: "icon-btn", title: "Hide reply", "aria-label": "Hide reply", onclick: () => { p.open = false; paintPeek(); } }, icon("x"))),
    body);
  if (stick) body.scrollTop = body.scrollHeight;
}

function wireFullScreen() {
  const ta = $("#fs-input");
  const send = $("#fs-send");
  const grow = () => {
    ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
    send.disabled = !ta.value.trim();
  };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#fs-ask").requestSubmit(); }
  });
  $("#fs-ask").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    send.disabled = true;
    try {
      const r = await sendChat(text);
      ta.value = "";
      S.peek = { runId: r.run.id, prompt: text, open: true };
      paintPeek();
    } catch (err) { fail(err); } finally { grow(); }
  });
  $("#viewer-expand").addEventListener("click", () => setFull(true));
  $("#viewer-exit").addEventListener("click", () => setFull(false));
}

/* ------------------------------------------------------------------ layout: hide the header or the desk sidebar */
function loadLayout() {
  try { Object.assign(S.layout, JSON.parse(localStorage.getItem("ta.layout") || "{}")); } catch { /* defaults */ }
}
function setLayout(part, on) {
  S.layout[part] = on;
  try { localStorage.setItem("ta.layout", JSON.stringify(S.layout)); } catch { /* not kept */ }
  applyLayout();
  // Hand focus to whatever brings it back, so the keyboard isn't left on a hidden button.
  const next = { side: on ? "#btn-side" : "#pull-side", top: on ? "#btn-top" : "#pull-top" }[part];
  setTimeout(() => $(next)?.focus({ preventScroll: true }), on ? 340 : 0);
}
function applyLayout() {
  const { side, top } = S.layout;
  document.body.classList.toggle("side-off", !side);
  document.body.classList.toggle("top-off", !top);
  $("#pull-side").hidden = side;
  $("#pull-top").hidden = top;
  if (top) setTimeout(moveNavHl, 340);
}

/* ------------------------------------------------------------------ composer */
function renderAttachments() {
  fill($("#attachments"), ...S.attachments.map((p, i) =>
    h("span", { class: "att-chip" }, p.split("/").pop(),
      h("button", { type: "button", "aria-label": "Remove", onclick: () => { S.attachments.splice(i, 1); renderAttachments(); } }, "×"))));
  const ta = $("#composer-input");
  $("#composer-send").disabled = !ta.value.trim() && !S.attachments.length;
}

function wireComposer() {
  const ta = $("#composer-input");
  const grow = () => {
    ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    $("#composer-send").disabled = !ta.value.trim() && !S.attachments.length;
  };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text && !S.attachments.length) return;
    $("#composer-send").disabled = true;
    try {
      await sendChat(text);
      ta.value = "";
    } catch (err) { fail(err); } finally { grow(); }
  });
  $("#attach-input").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    try {
      const r = await upload("/api/inbox", files);
      S.attachments.push(...r.paths);
      renderAttachments();
    } catch (err) { fail(err); }
  });
}

/* ------------------------------------------------------------------ dialogs */
function openDialog(content, cls = "") {
  const ov = $("#overlay");
  const dlg = h("div", { class: "dialog " + cls, role: "dialog", "aria-modal": "true" }, content);
  fill(ov, dlg);
  ov.hidden = false;
  const first = dlg.querySelector("input, select, textarea, button");
  if (first) setTimeout(() => first.focus(), 30);
  return dlg;
}
function closeDialog() {
  $("#overlay").hidden = true;
  fill($("#overlay"), );
}

function studentSelect(selected, { activeOnly = true, allowEmpty = false } = {}) {
  const list = students().filter((s) => !activeOnly || s.status !== "paused" || s.name === selected);
  return h("select", {},
    allowEmpty ? h("option", { value: "" }, "Choose…") : null,
    list.map((s) => h("option", { value: s.name, selected: s.name === selected ? true : undefined }, s.name)));
}

function dropzone(onFiles, label) {
  const input = h("input", { type: "file", multiple: true, accept: "image/*,application/pdf", hidden: true });
  const dz = h("div", { class: "dropzone", tabindex: "0", role: "button" }, label);
  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); onFiles([...e.dataTransfer.files]); });
  input.addEventListener("change", () => { onFiles([...input.files]); input.value = ""; });
  return h("div", {}, dz, input);
}

function openFeedback(student, date) {
  if (!students().length) { toast("Add a student first (Students tab)."); return; }
  const sel = studentSelect(student, { allowEmpty: !student });
  const dateIn = h("input", { type: "date", value: date || todayISO() });
  const answers = h("textarea", { class: "plain", rows: 6, placeholder: "1. x = 4\n2. B\n3. (skipped)\n…" });
  const notes = h("textarea", { class: "plain", rows: 3, placeholder: "What clicked, what didn't…" });
  const files = [];
  const fileList = h("div", { class: "attachments", style: "margin-left:0;margin-top:8px" });
  const paint = () => fill(fileList, ...files.map((p, i) => h("span", { class: "att-chip" }, p.split("/").pop(),
    h("button", { type: "button", onclick: () => { files.splice(i, 1); paint(); } }, "×"))));
  const dz = dropzone(async (fs) => {
    try { const r = await upload("/api/inbox", fs); files.push(...r.paths); paint(); } catch (e) { fail(e); }
  }, "Add photos of their work");
  const send = h("button", { class: "btn primary", onclick: async () => {
    if (!sel.value) return toast("Choose a student.", true);
    if (!answers.value.trim() && !notes.value.trim() && !files.length) return toast("Add eval answers, notes, or photos.", true);
    send.disabled = true;
    try {
      const r = await api("POST", "/api/runs", { kind: "feedback", student: sel.value, date: dateIn.value,
        answers: answers.value, notes: notes.value, attachments: files });
      closeDialog();
      show("desk");
      await openConversation(r.conversation.id);
      softRefresh();
    } catch (e) { fail(e); send.disabled = false; }
  } }, "Log feedback");
  openDialog([
    h("h2", {}, "Log feedback"),
    h("p", { class: "sub" }, "Grades the eval, updates the student, and makes homework."),
    h("div", { class: "fields" },
      h("div", { class: "field" }, h("label", {}, "Student"), sel),
      h("div", { class: "field" }, h("label", {}, "Session"), dateIn),
      h("div", { class: "field full" }, h("label", {}, "Eval answers"), answers),
      h("div", { class: "field full" }, h("label", {}, "Notes"), notes),
      h("div", { class: "field full" }, dz, fileList)),
    h("div", { class: "dialog-actions" }, h("button", { class: "btn", onclick: closeDialog }, "Cancel"), send),
  ]);
}

function openAddSession() {
  const names = students().map((s) => s.name);
  const who = h("input", { type: "text", list: "dl-students", placeholder: "Student name" });
  const dl = h("datalist", { id: "dl-students" }, names.map((n) => h("option", { value: n })));
  const date = h("input", { type: "date", value: todayISO() });
  const start = h("input", { type: "time", value: "16:00" });
  const end = h("input", { type: "time", value: "17:00" });
  openDialog([
    h("h2", {}, "Add a session"),
    h("div", { class: "fields" },
      h("div", { class: "field full" }, h("label", {}, "Student"), who, dl),
      h("div", { class: "field" }, h("label", {}, "Date"), date),
      h("div", { class: "field" }),
      h("div", { class: "field" }, h("label", {}, "Starts"), start),
      h("div", { class: "field" }, h("label", {}, "Ends"), end)),
    h("div", { class: "dialog-actions" },
      h("button", { class: "btn", onclick: closeDialog }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        try {
          S.state.schedule = await api("POST", "/api/schedule/manual", { student: who.value, date: date.value, start: start.value, end: end.value });
          closeDialog();
          renderSchedule();
        } catch (e) { fail(e); }
      } }, "Add session")),
  ]);
}

/* ------------------------------------------------------------------ students */
function renderStudents() {
  const list = [...students()].sort((a, b) => (a.status === "paused") - (b.status === "paused") || a.name.localeCompare(b.name));
  if (!S.student || !list.some((s) => s.name === S.student)) {
    if (!S.draft || S.draft.__new === undefined) S.student = list[0]?.name || null;
  }
  fill($("#student-list"), ...list.map((s) =>
    h("li", { class: s.status }, h("button", { class: s.name === S.student && !S.draft?.__new ? "active" : "", onclick: () => pickRosterStudent(s.name) },
      h("span", { class: "sn" }, s.name),
      h("span", { class: "ss" }, [s.subject, s.status === "paused" ? "paused" : ""].filter(Boolean).join(", "))))),
    list.length ? null : h("li", { class: "muted small" }, "No students yet."));
  if (S.draft?.__new || S.dirty) return paintEditor();
  const cur = list.find((s) => s.name === S.student);
  S.draft = cur ? JSON.parse(JSON.stringify(cur)) : null;
  S.dirty = false;
  paintEditor();
}

function pickRosterStudent(name) {
  if (S.dirty && !confirm("Discard unsaved changes?")) return;
  S.student = name;
  S.draft = null;
  S.dirty = false;
  renderStudents();
}

function newStudent() {
  if (S.dirty && !confirm("Discard unsaved changes?")) return;
  S.draft = { __new: true, name: "", subject: "", hw: true, status: "active", coverNextSession: [], strengths: [], weaknesses: [] };
  S.dirty = false;
  renderStudents();
}

function listEditor(key, { queue = false, placeholder = "" } = {}) {
  const items = S.draft[key] || (S.draft[key] = []);
  const wrap = h("div", { class: "listedit" + (queue ? " queue" : "") });
  const paint = () => {
    fill(wrap, 
      h(queue ? "ol" : "ul", {}, items.map((txt, i) => {
        const ta = h("textarea", { rows: 1, "aria-label": `${key} item ${i + 1}` });
        ta.value = txt;
        const fit = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
        ta.addEventListener("input", () => { items[i] = ta.value; markDirty(); fit(); });
        requestAnimationFrame(fit);
        return h("li", {},
          h("span", { class: "n" }, queue ? i + 1 : "•"),
          ta,
          h("span", { class: "ops" },
            queue && i > 0 ? h("button", { title: "Move up", "aria-label": "Move up", onclick: () => { [items[i - 1], items[i]] = [items[i], items[i - 1]]; markDirty(); paint(); } }, "↑") : null,
            queue && i < items.length - 1 ? h("button", { title: "Move down", "aria-label": "Move down", onclick: () => { [items[i + 1], items[i]] = [items[i], items[i + 1]]; markDirty(); paint(); } }, "↓") : null,
            h("button", { title: "Remove", "aria-label": "Remove", onclick: () => { items.splice(i, 1); markDirty(); paint(); } }, "×")));
      })),
      h("button", { class: "link add", onclick: () => { items.push(""); markDirty(); paint(); requestAnimationFrame(() => { const t = wrap.querySelectorAll("textarea"); t[t.length - 1]?.focus(); }); } },
        items.length ? "Add another" : placeholder));
  };
  paint();
  return wrap;
}

function markDirty() {
  S.dirty = true;
  const b = $("#save-student");
  if (b) b.disabled = false;
}

function paintEditor() {
  const ed = $("#student-editor");
  if (!S.draft) {
    fill(ed, h("div", { class: "empty" }, h("h2", {}, "No students yet"),
      h("button", { class: "btn primary", onclick: newStudent }, "Add a student")));
    return;
  }
  const d = S.draft;
  const input = (key, attrs = {}) => {
    const el = h("input", { type: "text", ...attrs });
    el.value = d[key] || "";
    el.addEventListener("input", () => { d[key] = el.value; markDirty(); });
    return el;
  };
  const subjects = [...new Set([...students().map((s) => s.subject), ...(S.library?.books?.subjectDefaults || []).map((r) => r.subject)].filter(Boolean))];
  const status = h("select", { onchange: (e) => { d.status = e.target.value; markDirty(); } },
    ["active", "paused"].map((v) => h("option", { value: v, selected: d.status === v ? true : undefined }, v === "active" ? "Active" : "Paused")));
  const hw = h("input", { type: "checkbox", checked: d.hw ? true : undefined, onchange: (e) => { d.hw = e.target.checked; markDirty(); } });
  const busy = S.state.busy;
  fill(ed, h("div", { class: "editor-inner" },
    h("div", { class: "editor-head" },
      h("h1", {}, d.__new ? "New student" : d.name),
      d.__new ? null : h("div", { style: "display:flex;gap:8px" },
        h("button", { class: "btn small", onclick: () => { show("desk"); selectStudent(d.name); } }, "Open on desk"),
        h("button", { class: "btn small", onclick: () => openFeedback(d.name) }, "Log feedback"))),
    h("div", { class: "fields" },
      h("div", { class: "field wide" }, h("label", {}, "Name"), input("name", { placeholder: "As it appears on your calendar" })),
      h("div", { class: "field wide" }, h("label", {}, "Subject"),
        input("subject", { list: "dl-subjects", placeholder: "e.g. SAT Math" }), h("datalist", { id: "dl-subjects" }, subjects.map((s) => h("option", { value: s })))),
      h("div", { class: "field" }, h("label", {}, "Status"), status),
      h("div", { class: "field" }, h("span", { class: "lbl" }, "Homework"), h("label", { class: "toggle" }, hw, "After each session")),
      h("div", { class: "field wide" }, h("label", {}, "Textbook"), input("textbook", { placeholder: "The subject's default book" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Cover next session"),
        h("p", { class: "help" }, "Prep teaches from the top."),
        listEditor("coverNextSession", { queue: true, placeholder: "Add a topic, like Holt-A1 §4-3: Slope" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Strengths"), listEditor("strengths", { placeholder: "Add a strength" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Weaknesses"), listEditor("weaknesses", { placeholder: "Add a weakness" }))),
    h("div", { class: "editor-actions" },
      h("button", { class: "btn primary", id: "save-student", disabled: (!S.dirty || busy) ? true : undefined, onclick: saveStudent }, d.__new ? "Add student" : "Save changes"),
      S.dirty ? h("button", { class: "btn", onclick: () => { S.draft = null; S.dirty = false; renderStudents(); } }, "Discard") : null,
      busy ? h("span", { class: "muted small" }, "You can save once the agent finishes.") : null,
      h("span", { class: "spacer" }),
      d.__new ? null : h("button", { class: "btn ghost danger", onclick: deleteStudent }, "Delete student"))));
}

async function saveStudent() {
  const d = S.draft;
  const payload = { ...d };
  delete payload.__new;
  for (const k of ["coverNextSession", "strengths", "weaknesses"]) payload[k] = (payload[k] || []).map((s) => s.trim()).filter(Boolean);
  try {
    const r = await api("PUT", "/api/roster/student", { student: payload, originalName: d.__new ? null : S.student });
    S.student = r.student.name;
    S.draft = null;
    S.dirty = false;
    await loadState();
    renderStudents();
    toast("Saved");
  } catch (e) { fail(e); }
}

async function deleteStudent() {
  if (!confirm(`Delete ${S.student}? Their documents stay in your folder.`)) return;
  try {
    await api("DELETE", `/api/roster/student/${encodeURIComponent(S.student)}`);
    S.student = null; S.draft = null; S.dirty = false;
    await loadState();
    renderStudents();
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------------ materials */
async function loadMaterials() {
  let data;
  try { data = await api("GET", "/api/materials"); } catch (e) { return fail(e); }
  const filter = $("#mat-filter");
  const who = filter.value;
  const names = [...new Set(data.days.flatMap((d) => [...d.files, ...d.other].map((f) => f.student)))].sort();
  fill(filter, h("option", { value: "" }, "Everyone"), names.map((n) => h("option", { value: n, selected: n === who ? true : undefined }, n)));
  const box = $("#mat-days");
  const days = data.days.map((d) => ({ ...d, files: d.files.filter((f) => !who || f.student === who),
    other: d.other.filter((f) => !who || f.student === who) })).filter((d) => d.files.length || d.other.length);
  if (!days.length) {
    fill(box, h("p", { class: "side-empty" }, "Nothing yet. Prepped documents show up here."));
    return;
  }
  const order = Object.keys(TYPE_LABEL);
  fill(box, ...days.map((d) => {
    const byStudent = {};
    d.files.forEach((f) => (byStudent[f.student] ||= { files: [], other: [] }).files.push(f));
    d.other.forEach((f) => (byStudent[f.student] ||= { files: [], other: [] }).other.push(f));
    return h("div", { class: "mat-day" },
      h("h3", {}, `${dayName(d.date)}, ${parseDate(d.date).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}`),
      Object.entries(byStudent).map(([name, g]) => h("div", { class: "mat-student" },
        h("div", { class: "msn" }, name),
        g.files.sort((a, b) => order.indexOf(a.type.replace(/ /g, "_")) - order.indexOf(b.type.replace(/ /g, "_"))).map((f) =>
          h("div", { class: "mat-row" }, matButton(f, TYPE_LABEL[f.type.replace(/ /g, "_")] || f.type),
            f.source ? h("button", { class: "link src", onclick: () => previewFile({ name: f.source.split("/").pop(), path: f.source, ext: ".txt" }) }, "text") : null)),
        g.other.length ? h("div", { class: "muted small", style: "margin:6px 10px 0" }, "Other files") : null,
        g.other.map((f) => h("div", { class: "mat-row" }, matButton({ ...f, ext: "." + f.name.split(".").pop().toLowerCase() }, f.name))))));
  }));
}

function matButton(f, label) {
  return h("button", { class: "mat-file" + (S.matSel === f.path ? " active" : ""), onclick: (e) => {
    $$(".mat-file").forEach((b) => b.classList.remove("active"));
    e.currentTarget.classList.add("active");
    previewFile(f);
  } }, label);
}

async function previewFile(f) {
  S.matSel = f.path;
  const p = $("#mat-preview");
  const inFolder = f.path.startsWith("material/");
  const bar = h("div", { class: "bar" },
    h("span", { class: "t" }, f.name),
    inFolder ? h("button", { class: "btn small ghost", onclick: () => api("POST", "/api/reveal", { path: f.path }).catch(fail) }, "Show in folder") : null,
    h("a", { class: "icon-btn", href: fileUrl(f.path) + "?download=1", download: f.name, title: "Download", "aria-label": "Download" }, icon("download")));
  if (f.ext === ".pdf") {
    fill(p, bar, h("iframe", { src: fileUrl(f.path), title: f.name }));
  } else if (/\.(png|jpe?g|gif|webp)$/i.test(f.path)) {
    fill(p, bar, h("div", { style: "flex:1;overflow:auto;padding:12px" }, h("img", { src: fileUrl(f.path), alt: f.name, style: "max-width:100%" })));
  } else {
    const txt = await fetch(fileUrl(f.path)).then((r) => r.text());
    fill(p, bar, h("pre", {}, txt));
  }
}

/* ------------------------------------------------------------------ library */
let libTimer;
async function loadLibrary() {
  try { S.library = await api("GET", "/api/library"); } catch (e) { return fail(e); }
  const { files, books, jobs } = S.library;
  const tbl = $("#tb-table");
  if (!files.length) {
    fill(tbl, h("tbody", {}, h("tr", {}, h("td", { class: "muted" }, "No textbooks yet."))));
  } else {
    fill(tbl, 
      h("thead", {}, h("tr", {}, h("th", {}, "Book"), h("th", {}, "Key"), h("th", {}, "Status"), h("th", {}, "Size"), h("th", {}))),
      h("tbody", {}, files.map((f) => {
        const job = jobs[f.file];
        let pill;
        if (job?.status === "running") pill = h("span", { class: "pill" }, "Indexing…");
        else if (job?.status === "error") pill = h("span", { class: "pill bad", title: job.detail }, "Indexing failed");
        else if (f.indexed && f.quality === "none") pill = h("span", { class: "pill bad", title: "Scanned with no text layer — run OCR on it to make it searchable." }, "No text");
        else if (f.indexed) pill = h("span", { class: "pill ok", title: f.quality === "good" ? "" : `${f.quality} text quality` }, "Ready");
        else pill = h("span", { class: "pill" }, "Not indexed");
        return h("tr", {},
          h("td", {}, h("a", { href: fileUrl(f.path), target: "_blank", rel: "noopener" }, f.file)),
          h("td", {}, f.key || h("span", { class: "muted" }, "—")),
          h("td", {}, pill),
          h("td", { class: "muted" }, `${f.sizeMB} MB`),
          h("td", {}, !f.indexed && job?.status !== "running" ? h("button", { class: "btn small", onclick: () => indexBook(f.file) }, "Index") : null));
      })));
  }
  clearTimeout(libTimer);
  if (Object.values(jobs).some((j) => j.status === "running") && S.view === "library") libTimer = setTimeout(loadLibrary, 3000);
  paintDefaults(books);
  paintKeys(books);
}

async function indexBook(file) {
  try { await api("POST", "/api/library/index", { file }); loadLibrary(); } catch (e) { fail(e); }
}

function paintDefaults(books) {
  const rows = books.subjectDefaults.map((r) => ({ ...r, books: [...(r.books || [])] }));
  const box = $("#subject-defaults");
  const paint = () => fill(box, 
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Subject"), h("th", {}, "Books, in order"), h("th", {}, "Note"), h("th", {}))),
      h("tbody", {}, rows.map((r, i) => h("tr", {},
        h("td", {}, cellInput(r.subject, (v) => (r.subject = v))),
        h("td", {}, cellInput(r.books.join(", "), (v) => (r.books = v.split(",").map((x) => x.trim()).filter(Boolean)))),
        h("td", {}, cellInput(r.note || "", (v) => (r.note = v))),
        h("td", { style: "width:44px" }, h("button", { class: "icon-btn", title: "Remove", "aria-label": `Remove ${r.subject || "row"}`, onclick: () => { rows.splice(i, 1); paint(); } }, icon("x"))))))),
    h("div", { class: "table-actions" },
      h("button", { class: "btn small", onclick: () => { rows.push({ subject: "", books: [], note: "" }); paint(); } }, "Add subject"),
      h("button", { class: "btn small primary", onclick: async () => {
        try { await api("PUT", "/api/books", { subjectDefaults: rows.filter((r) => r.subject.trim()) }); toast("Saved"); loadLibrary(); } catch (e) { fail(e); }
      } }, "Save")));
  paint();
}

function paintKeys(books) {
  const rows = books.books.map((b) => ({ ...b }));
  const box = $("#book-keys");
  if (!rows.length) {
    fill(box, h("p", { class: "muted small" }, "None yet. The agent adds one the first time it uses a book."));
    return;
  }
  fill(box, 
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Key"), h("th", {}, "Title"), h("th", {}, "Author"), h("th", {}, "File name starts with"), h("th", {}, "Notes"))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, h("strong", {}, r.key)),
        h("td", {}, cellInput(r.title || "", (v) => (r.title = v))),
        h("td", {}, cellInput(r.author || "", (v) => (r.author = v))),
        h("td", {}, cellInput(r.filePrefix || "", (v) => (r.filePrefix = v))),
        h("td", {}, cellInput(r.notes || "", (v) => (r.notes = v))))))),
    h("div", { class: "table-actions" }, h("button", { class: "btn small primary", onclick: async () => {
      try { await api("PUT", "/api/books", { books: rows }); toast("Saved"); loadLibrary(); } catch (e) { fail(e); }
    } }, "Save")));
}

function cellInput(value, onChange) {
  const el = h("input", { type: "text" });
  el.value = value;
  el.addEventListener("input", () => onChange(el.value));
  return el;
}

/* ------------------------------------------------------------------ open questions */
async function loadQuestions() {
  try { S.oq = (await api("GET", "/api/open-questions")).markdown; } catch { return; }
  const open = S.oq.split(/^## +Resolved/m)[0];
  const n = (open.match(/^\s*\d+\.\s+\*\*/gm) || []).length;
  const c = $("#oq-count");
  c.hidden = !n;
  c.textContent = n;
  const tab = $("#tab-questions");
  const was = tab.hidden;
  tab.hidden = !n && S.view !== "questions";
  if (was !== tab.hidden) moveNavHl();
  if (S.view !== "questions") return;
  const body = $("#oq-body");
  const rendered = md(S.oq, { breaks: false });
  const firstH1 = rendered.querySelector("h1");
  if (firstH1 && /open questions/i.test(firstH1.textContent)) {
    firstH1.remove();
    // The file's intro is for the agent; the page's own subtitle covers it.
    while (rendered.firstElementChild?.tagName === "P") rendered.firstElementChild.remove();
  }
  // An "Answer" link on each open numbered item.
  let resolved = false;
  for (const el of [...rendered.children]) {
    if (/^H[1-3]$/.test(el.tagName)) resolved = /resolved/i.test(el.textContent);
    if (el.tagName === "OL" && !resolved) {
      for (const li of el.children) {
        const label = li.querySelector("strong")?.textContent?.replace(/[:.]$/, "") || li.textContent.slice(0, 50);
        li.append(" ", h("button", { class: "link", onclick: () => answerQuestion(label) }, "Answer"));
      }
    }
  }
  fill(body, rendered);
}

function answerQuestion(label) {
  newConversation();
  show("desk");
  const ta = $("#composer-input");
  ta.value = `Re: open question "${label}" — `;
  ta.focus();
  ta.dispatchEvent(new Event("input"));
}

function editQuestions() {
  const ta = h("textarea", { class: "plain", rows: 24, style: "font-family:ui-monospace,Menlo,monospace;font-size:13px" });
  ta.value = S.oq;
  openDialog([
    h("h2", {}, "Edit open questions"),
    h("p", { class: "sub" }, "open-questions.md in your folder"),
    ta,
    h("div", { class: "dialog-actions" },
      h("button", { class: "btn", onclick: closeDialog }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        try { await api("PUT", "/api/open-questions", { markdown: ta.value }); closeDialog(); loadQuestions(); } catch (e) { fail(e); }
      } }, "Save")),
  ], "wide");
}

/* ------------------------------------------------------------------ settings */
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function timezoneSelect(value) {
  const zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : [value];
  if (!zones.includes(value)) zones.unshift(value);
  return h("select", {}, zones.map((z) => h("option", { value: z, selected: z === value ? true : undefined }, z.replace(/_/g, " "))));
}

function calendarHelp() {
  return h("details", { class: "small" }, h("summary", { class: "muted", style: "cursor:pointer" }, "Where do I find this link?"),
    h("ol", { class: "steps-help" },
      h("li", {}, h("strong", {}, "Google: "), "Settings → your calendar → Integrate calendar → Secret address in iCal format."),
      h("li", {}, h("strong", {}, "Apple: "), "right-click the calendar → Share Calendar → Public Calendar."),
      h("li", {}, h("strong", {}, "Outlook: "), "Settings → Calendar → Shared calendars → Publish a calendar.")),
    h("p", { class: "muted small" }, "Keep the link private. It stays on this computer."));
}

function calendarFields(cfg, onTest) {
  const url = h("input", { type: "url", placeholder: "https://calendar.google.com/calendar/ical/…/basic.ics" });
  url.value = cfg.calendar.icsUrl;
  const filter = h("input", { type: "text", placeholder: "Optional, like “tutoring”" });
  filter.value = cfg.calendar.filter;
  const result = h("div", { class: "test-result" });
  const test = h("button", { class: "btn", onclick: async () => {
    result.textContent = "Checking…";
    try {
      const r = await api("POST", "/api/calendar/test", { icsUrl: url.value, filter: filter.value });
      if (!r.ok) { fill(result, h("span", { style: "color:var(--pen)" }, r.error)); return; }
      fill(result, 
        h("div", {}, `Found ${r.total} event${r.total === 1 ? "" : "s"} in the next 8 days${filter.value ? `, ${r.kept} matching the filter` : ""}.`),
        r.sample.length ? h("ul", {}, r.sample.map((s) => h("li", {}, `${s.summary}`,
          h("span", { class: "muted" }, s.student ? ` (student: ${s.student})` : " (no matching student yet)")))) : null);
      onTest && onTest(r);
    } catch (e) { result.textContent = e.message; }
  } }, "Test link");
  return { url, filter, el: h("div", { class: "fields" },
    h("div", { class: "field full" }, h("label", {}, "Calendar link (iCal / ICS)"), url, calendarHelp()),
    h("div", { class: "field" }, h("label", {}, "Only events containing"), filter),
    h("div", { class: "field", style: "justify-content:flex-end;align-items:flex-start" }, test),
    h("div", { class: "field full" }, result)) };
}

function checksList(checks) {
  const icon = { ok: "✓", warn: "!", fail: "✗" };
  return h("ul", { class: "checks" }, checks.map((c) => h("li", { class: c.status },
    h("span", { class: "ic" }, icon[c.status]),
    h("div", {}, h("div", { class: "lb" }, c.label), h("div", { class: "dt" }, c.detail),
      c.status !== "ok" && c.fix ? h("div", { class: "fx", title: "Copy this into Terminal" }, c.fix) : null))));
}

async function renderSettings() {
  const cfg = JSON.parse(JSON.stringify(S.state.config));
  const box = $("#settings-body");
  const name = h("input", { type: "text" });
  name.value = cfg.tutorName;
  const tzSel = timezoneSelect(cfg.timezone);
  const cal = calendarFields(cfg);
  const apOn = h("input", { type: "checkbox", checked: cfg.autoPrep.enabled ? true : undefined });
  const apTime = h("input", { type: "time", value: cfg.autoPrep.time });
  const apOnly = h("input", { type: "checkbox", checked: cfg.autoPrep.onlyWhenSessions ? true : undefined });
  const dayBoxes = DAY_NAMES.map((d, i) => h("label", {}, h("input", { type: "checkbox", value: i, checked: cfg.autoPrep.days.includes(i) ? true : undefined }), d));
  const model = h("select", {},
    [["", "Default"], ["opus", "Opus (best quality)"], ["sonnet", "Sonnet (lighter on usage)"]]
      .map(([v, l]) => h("option", { value: v, selected: cfg.model === v ? true : undefined }, l)),
    cfg.model && !["opus", "sonnet"].includes(cfg.model) ? h("option", { value: cfg.model, selected: true }, cfg.model) : null);
  const shell = h("select", {},
    h("option", { value: "standard", selected: cfg.shellAccess !== "full" ? true : undefined }, "Standard (Python and files)"),
    h("option", { value: "full", selected: cfg.shellAccess === "full" ? true : undefined }, "Full (any command)"));
  const checksBox = h("div", {}, h("p", { class: "muted small" }, "Checking…"));
  const runChecks = async (full) => {
    fill(checksBox, h("p", { class: "muted small" }, full ? "Compiling a test worksheet…" : "Checking…"));
    try { fill(checksBox, checksList((await api("GET", `/api/doctor?${full ? "full" : "fresh"}=true`)).checks)); }
    catch (e) { fill(checksBox, h("p", { class: "msg-error" }, e.message)); }
  };
  const save = async () => {
    const body = {
      tutorName: name.value.trim(), timezone: tzSel.value,
      calendar: { ...cfg.calendar, icsUrl: cal.url.value.trim(), filter: cal.filter.value },
      autoPrep: { enabled: apOn.checked, time: apTime.value || "06:30", onlyWhenSessions: apOnly.checked,
        days: dayBoxes.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => Number(i.value)) },
      model: model.value, shellAccess: shell.value,
    };
    try { await api("PUT", "/api/config", body); await loadState(); toast("Saved"); } catch (e) { fail(e); }
  };
  fill(box, 
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", { class: "lede small" }, S.state.workspace)),
      h("button", { class: "btn", onclick: () => api("POST", "/api/reveal", {}).catch(fail) }, "Show folder")),
    S.state.specUpdate ? h("div", { class: "card" }, h("h2", {}, "New agent rules available"),
      h("p", { class: "muted" }, "You've edited CLAUDE.md, so the new version is in CLAUDE.md.new."),
      h("button", { class: "btn danger", onclick: async () => {
        if (!confirm("Replace your CLAUDE.md with the new version? Your edits will be lost.")) return;
        try { await api("POST", "/api/workspace/sync", { forceSpec: true }); await loadState(); renderSettings(); toast("CLAUDE.md replaced"); } catch (e) { fail(e); }
      } }, "Use the new CLAUDE.md")) : null,
    h("div", { class: "card" }, h("h2", {}, "You"),
      h("div", { class: "fields" },
        h("div", { class: "field" }, h("label", {}, "Your name"), name),
        h("div", { class: "field" }, h("label", {}, "Timezone"), tzSel))),
    h("div", { class: "card" }, h("h2", {}, "Calendar"), h("p", { class: "muted" }, "Your sessions come from here."), cal.el),
    h("div", { class: "card" }, h("h2", {}, "Daily prep"),
      h("p", { class: "muted" }, "Preps the day's sessions on its own, even if your computer was asleep."),
      h("div", { class: "fields" },
        h("div", { class: "field full" }, h("label", { class: "toggle" }, apOn, "Prep automatically")),
        h("div", { class: "field" }, h("label", {}, "At"), apTime),
        h("div", { class: "field" }, h("span", { class: "lbl" }, "On"), h("div", { class: "days-pick" }, dayBoxes)),
        h("div", { class: "field full" }, h("label", { class: "toggle" }, apOnly, "Skip days with no sessions")))),
    h("div", { class: "card" }, h("h2", {}, "Claude"),
      h("p", { class: "muted" }, "Runs on your own Claude account."),
      h("div", { class: "fields" },
        h("div", { class: "field" }, h("label", {}, "Model"), model),
        h("div", { class: "field" }, h("label", {}, "Shell access"), shell))),
    h("div", { class: "editor-actions", style: "position:static;background:none;padding:0 0 18px" }, h("button", { class: "btn primary", onclick: save }, "Save settings")),
    h("div", { class: "card" }, h("div", { class: "side-head" }, h("h2", {}, "Health check"),
      h("span", { style: "display:flex;gap:16px" }, h("button", { class: "link", onclick: () => runChecks(false) }, "Check again"),
        h("button", { class: "link", onclick: () => runChecks(true) }, "Test a worksheet"))), checksBox));
  runChecks(false);
}

/* ------------------------------------------------------------------ first-run setup (one screen) */
function openSetup() {
  // Everything else (Claude Code, LaTeX, importing an old folder, start at login) was
  // done by `tutor setup` in the terminal. This screen asks only what it couldn't know.
  const cfg = JSON.parse(JSON.stringify(S.state.config));
  const name = h("input", { type: "text", placeholder: "Your name" });
  name.value = cfg.tutorName || S.state.suggestedName || "";
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tzSel = timezoneSelect(cfg.tutorName ? cfg.timezone : browserTz || cfg.timezone);
  const cal = calendarFields(cfg);
  const autoOn = h("input", { type: "checkbox", checked: true });
  const autoTime = h("input", { type: "time", value: cfg.autoPrep.time || "06:30", style: "width:auto" });
  const importPath = h("input", { type: "text", placeholder: "/Users/you/Claude/Projects/my-tutoring-folder" });
  const importOut = h("div", { class: "test-result" });
  const importBox = students().length ? null : h("details", { class: "small", style: "margin-top:14px" },
    h("summary", { class: "muted", style: "cursor:pointer" }, "Import an existing tutoring folder"),
    h("div", { class: "fields", style: "margin-top:10px" },
      h("div", { class: "field full" }, h("p", { class: "help" }, "A folder with CLAUDE.md and roster.json. It's copied, not moved."), importPath),
      h("div", { class: "field full" }, h("button", { class: "btn small", onclick: async () => {
        importOut.textContent = "Copying… (textbooks can take a minute)";
        try {
          const r = await api("POST", "/api/setup/import", { path: importPath.value.trim(), withMaterial: true });
          fill(importOut, h("ul", {}, r.notes.map((n) => h("li", {}, n))));
          await loadState();
        } catch (e) { fill(importOut, h("span", { style: "color:var(--pen)" }, e.message)); }
      } }, "Import"), importOut)));
  const done = h("button", { class: "btn primary", onclick: async () => {
    if (!name.value.trim()) return toast("Add your name.", true);
    done.disabled = true;
    try {
      await api("PUT", "/api/config", {
        tutorName: name.value.trim(), timezone: tzSel.value,
        calendar: { ...cfg.calendar, icsUrl: cal.url.value.trim(), filter: cal.filter.value },
        autoPrep: { ...cfg.autoPrep, enabled: autoOn.checked, time: autoTime.value || "06:30" },
        setupComplete: true,
      });
      closeDialog();
      await loadState();
      renderThread();
      toast("You're all set");
    } catch (e) { fail(e); done.disabled = false; }
  } }, "Finish setup");
  const dlg = openDialog([
    h("h2", {}, "Welcome to TutorAssist"),
    h("p", { class: "sub" }, "A few details and you're ready."),
    h("div", { class: "fields" },
      h("div", { class: "field" }, h("label", {}, "Your name"), name),
      h("div", { class: "field" }, h("label", {}, "Timezone"), tzSel)),
    h("div", { style: "margin-top:16px" }, cal.el),
    h("label", { class: "toggle", style: "margin-top:4px" }, autoOn, "Prep each morning at ", autoTime),
    importBox,
    h("div", { class: "dialog-actions" }, done),
  ], "wide");
  dlg.classList.add("setup");
}

/* ------------------------------------------------------------------ health banner */
async function checkHealth() {
  let checks = [];
  try { checks = (await api("GET", "/api/doctor")).checks; } catch { return; }
  const bad = checks.filter((c) => c.status === "fail" && !["calendar"].includes(c.id));
  const box = $("#health");
  if (!bad.length) { box.hidden = true; return; }
  box.hidden = false;
  fill(box, h("strong", {}, "Prep can't run yet. "),
    bad.map((c) => h("span", { class: "issue" }, `${c.label}: ${c.detail} `, c.fix ? h("code", {}, c.fix) : null)),
    h("span", { class: "muted" }, " Run that in Terminal, then reload."));
}

/* ------------------------------------------------------------------ boot */
function wire() {
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#btn-prep-today").addEventListener("click", () => startPrep(todayISO()));
  // With a student open, feedback is for them and the session on screen.
  $("#btn-side").addEventListener("click", () => setLayout("side", false));
  $("#pull-side").addEventListener("click", () => setLayout("side", true));
  $("#btn-top").addEventListener("click", () => setLayout("top", false));
  $("#pull-top").addEventListener("click", () => setLayout("top", true));
  wireFullScreen();
  $("#btn-feedback").addEventListener("click", () => openFeedback(S.focus?.student, S.focus?.date || undefined));
  $("#btn-refresh-cal").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.classList.remove("spin"); void b.offsetWidth; b.classList.add("spin");
    try { S.state.schedule = await api("POST", "/api/schedule/refresh"); renderSchedule(); if (!S.conv) renderThread(); } catch (err) { fail(err); }
  });
  window.addEventListener("resize", moveNavHl);
  document.fonts?.ready.then(moveNavHl);
  $("#btn-add-session").addEventListener("click", openAddSession);
  $("#btn-new-conv").addEventListener("click", () => newConversation());
  $("#viewer-close").addEventListener("click", closeViewer);
  $("#btn-add-student").addEventListener("click", newStudent);
  $("#btn-edit-oq").addEventListener("click", editQuestions);
  $("#mat-filter").addEventListener("change", loadMaterials);
  $("#tb-input").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    toast(`Uploading ${files.length} book${files.length > 1 ? "s" : ""}…`);
    try { await upload("/api/library/upload", files); toast("Uploaded. Indexing now"); loadLibrary(); } catch (err) { fail(err); }
  });
  $("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay" && !$("#overlay .setup")) closeDialog(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#overlay").hidden) { if (!$("#overlay .setup")) closeDialog(); }
    else if ($(".menu")) return;  // the menu closes itself
    else if (S.viewer?.full) setFull(false);  // first Escape leaves full screen, the next closes the viewer
    else if (S.viewer) closeViewer();
  });
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  wireComposer();
}

(async function boot() {
  // Restore a hidden header/sidebar without animating it on page load.
  document.body.classList.add("preload");
  loadLayout();
  applyLayout();
  requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.remove("preload")));
  wire();
  moveNavHl();
  try {
    await loadState();
  } catch (e) {
    fill(document.body, h("div", { class: "empty" }, "Can't reach TutorAssist. Is `tutor` still running?"));
    return;
  }
  const f = restoreFocus();
  if (f) selectStudent(f.student, f.date); else renderThread();
  connectEvents();
  loadQuestions();
  checkHealth();
  api("GET", "/api/library").then((r) => (S.library = r)).catch(() => {});
  const initial = location.hash.slice(1);
  if (["students", "materials", "library", "questions", "settings"].includes(initial)) show(initial);
  setInterval(() => { if (S.view === "desk") renderSchedule(); }, 60000);
})();
