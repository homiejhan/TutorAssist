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
  viewer: null,           // {title, pdf, source, mode}
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
      if (S.focus && !["queued", "running"].includes(ev.run.status)) loadDocs();
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
function show(view) {
  S.view = view;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
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
    note.textContent = "No calendar connected. Add your calendar's iCal link in Settings, or add sessions by hand.";
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
              const meta = ev.known
                ? [minutes(ev.minutes), ev.source === "manual" ? "added by hand" : ""].filter(Boolean).join(", ")
                : "not matched to a student";
              const selected = S.focus && ev.known && S.focus.student === ev.student && S.focus.date === d.date;
              const open = () => selectStudent(ev.student, d.date);
              return h("div", {
                class: "session" + (past ? " past" : "") + (next ? " next" : "") + (ev.known ? " pick" : "") + (selected ? " selected" : ""),
                role: ev.known ? "button" : undefined, tabindex: ev.known ? "0" : undefined,
                "aria-label": ev.known ? `Open ${ev.student}'s ${dayName(d.date).toLowerCase()} session` : undefined,
                onclick: ev.known ? (e) => { if (!e.target.closest(".acts")) open(); } : undefined,
                onkeydown: ev.known ? (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); open(); } } : undefined,
              },
                h("div", { class: "time" }, h("span", { class: "hl" }, c.t, h("span", { class: "ampm" }, c.ap))),
                h("div", {},
                  h("div", { class: "who" }, who),
                  h("div", { class: "meta" + (ev.known ? "" : " unmatched") }, meta)),
                h("div", { class: "acts" },
                  ev.known && h("button", { class: "link", onclick: () => startPrep(d.date, [ev.student]) }, "Prep"),
                  ev.known && h("button", { class: "link", onclick: () => openFeedback(ev.student, d.date) }, "Log feedback"),
                  ev.source === "manual" && h("button", { class: "link danger", onclick: () => removeManual(ev.id) }, "Remove")));
            })
          : h("div", { class: "day-empty" }, d.date === today ? "No sessions today." : "No sessions."))));
  renderSideStudents();
}

function renderSideStudents() {
  const list = [...students()].sort((a, b) => (a.status === "paused") - (b.status === "paused") || a.name.localeCompare(b.name));
  fill($("#side-students"), list.map((st) => h("li", {},
    h("button", { class: (S.focus?.student === st.name ? "on" : "") + (st.status === "paused" ? " paused" : ""),
      "aria-pressed": S.focus?.student === st.name ? "true" : "false",
      onclick: () => (S.focus?.student === st.name ? clearFocus() : selectStudent(st.name)) }, st.name))),
    list.length ? null : h("li", { class: "muted small" }, "No students yet."));
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
      toast("Today's calendar has no sessions. Use Prep on a session in the schedule, or add one by hand.");
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
  const r = await api("POST", "/api/runs", body);
  S.attachments = [];
  renderAttachments();
  await openConversation(r.conversation.id);
  softRefresh();
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
    convs.length ? null : h("li", { class: "muted small" }, S.focus ? `Nothing about ${S.focus.student} yet.` : "Nothing yet."));
  const about = S.focus ? `about ${S.focus.student}'s session ${when(S.focus.date)}` : "";
  $("#composer-hint").textContent = S.convId
    ? `Replies continue this conversation${about ? ", " + about : ""}.`
    : `Starts a new conversation${about ? " " + about : ""}.`;
  $("#composer-input").placeholder = S.focus
    ? `Message about ${S.focus.student}…`
    : "Ask the agent anything — “make Jordan's eval shorter”, “what should Maya cover next?”";
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
    fill(th, h("div", { class: "welcome" },
      h("h2", {}, `Working on ${S.focus.student}`),
      h("p", {}, `Anything you type here is about ${S.focus.student} and the session ${when(S.focus.date)}: “make the eval shorter”, “swap problem 3 for an easier one”, “what should we cover next?”. Changes replace that session's documents instead of adding new files.`)));
    return;
  }
  if (!S.conv) {
    const name = S.state?.config?.tutorName;
    const today = S.state?.schedule?.days?.find((d) => d.date === todayISO());
    const n = today ? today.events.filter((e) => e.known).length : 0;
    fill(th, h("div", { class: "welcome" },
      h("h1", {}, name ? `Hi ${name.split(" ")[0]}.` : "Welcome."),
      h("p", {}, n
        ? `You have ${n} session${n === 1 ? "" : "s"} today. Prep builds each student's guided worksheet, eval, and answer key from their roster entry and your textbooks.`
        : "No sessions on today's calendar. You can prep any upcoming session from the schedule, log feedback, or ask the agent something below."),
      h("div", { class: "row" },
        n ? h("button", { class: "btn primary", onclick: () => startPrep(todayISO()) }, "Prep today") : null,
        h("button", { class: "btn", onclick: () => openFeedback() }, "Log feedback"))));
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
  const kind = { prep: "Prep", feedback: "Feedback", chat: "Agent" }[run.kind] || "Agent";
  const head = h("div", { class: "agent-head" },
    h("span", { class: "state " + run.status }, running ? h("span", { class: "pulse" }) : null, `${kind}: ${stateText}`),
    running
      ? h("button", { class: "link danger", onclick: () => api("POST", `/api/runs/${run.id}/cancel`).catch(fail) }, "Stop")
      : h("span", { class: "cost" }, duration(run)));

  // Narration (all text blocks but the last) and tools form the activity log.
  const finalText = m.result?.text || (running ? m.buffer || m.texts[m.texts.length - 1] : m.texts[m.texts.length - 1]) || "";
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
    extra.length ? h("span", { class: "muted small" }, "Also kept in the app (not in your folders): ",
      extra.map((f, i) => [i ? ", " : "", h("a", { href: fileUrl(f), onclick: (e) => { e.preventDefault(); openViewer({ title: f.split("/").pop(), file: f }); } }, f.split("/").pop())])) : null) : null;
  const roster = m.roster?.length ? h("div", { class: "roster-note" }, h("strong", {}, "Roster updated: "),
    m.roster.map((c) => `${c.name} (${c.change === "updated" ? c.fields.map((f) => FIELD_NAMES[f] || f).join(", ") : c.change})`).join("; ")) : null;
  const errs = m.errors.length && run.status !== "stopped" ? h("div", { class: "msg-error" }, m.errors.join("\n")) : null;
  fill(m.el, head, todos, activity, body, files, roster, errs);
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
  S.focus = null; S.docs = null; S.viewer = null;
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
  const bits = [st.subject, st.hw ? "homework assigned" : "no homework"].filter(Boolean);
  if (cur.session?.time) {
    const c = clock(cur.session.time);
    bits.push(`session ${when(date)} at ${c.t} ${c.ap}` + (cur.session.minutes ? ` for ${minutes(cur.session.minutes)}` : ""));
  }
  const meta = bits.join(", ");
  $("#focus-meta").textContent = meta ? meta[0].toUpperCase() + meta.slice(1) + "." : "";

  const hasPrep = cur.docs.some((x) => ["Guided", "Eval", "Eval_Answers"].includes(x.type) && x.pdf);
  const prep = async () => {
    if (hasPrep && !confirm(`Redo prep for ${st.name} ${when(date)}? The new guided worksheet, eval and answers replace the current ones.`)) return;
    startPrep(date, [st.name]);
  };
  fill($("#focus-actions"),
    h("button", { class: "btn small", onclick: prep }, hasPrep ? "Redo prep" : "Prep this session"),
    h("button", { class: "btn small", onclick: () => openFeedback(st.name, date) }, "Log feedback"),
    h("button", { class: "btn small", onclick: () => { show("students"); pickRosterStudent(st.name); } }, "Edit student"),
    h("button", { class: "btn small", onclick: clearFocus, title: "Back to all students" }, "Close"));

  fill($("#focus-dates"), sessions.slice(0, 12).map((x) => {
    const n = x.docs.filter((d) => d.pdf).length;
    return h("button", { class: "date-chip", role: "tab", "aria-selected": x.date === date ? "true" : "false",
      onclick: () => { S.focus.date = x.date; saveFocus(); paintFocus(); renderSchedule(); renderConvs(); if (!S.conv) renderThread(); } },
      chipLabel(x.date), n ? h("span", { class: "n" }, `(${n})`) : null);
  }));

  fill($("#doc-tiles"), cur.docs.map((d) => {
    if (d.pdf) {
      const title = `${st.name}: ${d.label} (${shortDate(date)})`;
      return h("div", { class: "tile-wrap" },
        h("button", { class: "tile" + (S.viewer?.pdf === d.pdf ? " on" : ""),
          onclick: () => { openViewer({ title, pdf: d.pdf, source: d.source }); } },
          h("span", { class: "tt" }, d.label),
          h("span", { class: "ts" }, "Ready to view"),
          h("span", { class: "ta" }, h("span", { class: "muted" }, "PDF"), d.source ? h("span", { class: "muted" }, "+ source") : null)),
        h("a", { class: "tile-dl", href: fileUrl(d.pdf) + "?download=1", download: d.pdf.split("/").pop(),
          title: `Download ${d.label}`, "aria-label": `Download ${d.label}` }, "↓"));
    }
    const isHw = d.type.startsWith("Homework");
    let hint, action = null;
    if (d.source) {
      hint = "Not built yet";
      action = h("button", { class: "link", onclick: () => openViewer({ title: `${st.name}: ${d.label} (${shortDate(date)})`, pdf: null, source: d.source, mode: "source" }) }, "Open the text");
    } else if (!isHw && date < todayISO()) hint = "Not made for this session";
    else if (!isHw) { hint = "Made by Prep"; action = h("button", { class: "link", onclick: prep }, "Prep"); }
    else if (st.hw) { hint = "Made after you log feedback"; action = h("button", { class: "link", onclick: () => openFeedback(st.name, date) }, "Log feedback"); }
    else hint = "Homework isn't assigned";
    return h("div", { class: "tile-wrap" }, h("div", { class: "tile missing" },
      h("span", { class: "tt" }, d.label), h("span", { class: "ts" }, hint), h("span", { class: "ta" }, action)));
  }));

  fill($("#doc-other"), cur.other.length ? [
    h("span", {}, "Also from this session, kept in the app:"),
    cur.other.map((o) => h("button", { class: "link", onclick: () => openViewer({ title: o.name, file: o.path }) }, o.name))] : null);
}

/* ------------------------------------------------------------------ document viewer */
function openViewer(v) {
  if (!leaveEditor()) return;
  S.viewer = { ...v, mode: v.mode || (v.pdf ? "pdf" : "file") };
  paintViewer();
  paintFocus();
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
  const status = h("span", { class: "src-status" }, "Edit the text, then rebuild — no agent run needed.");
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
function paintViewer() {
  const box = $("#viewer");
  const v = S.viewer;
  if (!v) { box.hidden = true; fill($("#viewer-body")); return; }
  box.hidden = false;
  $("#viewer-title").textContent = v.title;
  fill($("#viewer-seg"), v.pdf && v.source
    ? [["pdf", "PDF"], ["source", isSource(v.source) ? "Edit text" : "Source text"]].map(([k, label]) => h("button", { "aria-pressed": v.mode === k ? "true" : "false",
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

/* ------------------------------------------------------------------ composer */
function renderAttachments() {
  fill($("#attachments"), ...S.attachments.map((p, i) =>
    h("span", { class: "att-chip" }, p.split("/").pop(),
      h("button", { type: "button", "aria-label": "Remove", onclick: () => { S.attachments.splice(i, 1); renderAttachments(); } }, "×"))));
}

function wireComposer() {
  const ta = $("#composer-input");
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; };
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
      grow();
    } catch (err) { fail(err); } finally { $("#composer-send").disabled = false; }
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
  const notes = h("textarea", { class: "plain", rows: 4, placeholder: "What clicked, what didn't, any change of plan — e.g. “struggled with negative exponents; skip the Upper Bound Theorem”." });
  const files = [];
  const fileList = h("div", { class: "attachments", style: "margin-left:0;margin-top:8px" });
  const paint = () => fill(fileList, ...files.map((p, i) => h("span", { class: "att-chip" }, p.split("/").pop(),
    h("button", { type: "button", onclick: () => { files.splice(i, 1); paint(); } }, "×"))));
  const dz = dropzone(async (fs) => {
    try { const r = await upload("/api/inbox", fs); files.push(...r.paths); paint(); } catch (e) { fail(e); }
  }, "Drop photos or scans of the student's work, or click to choose");
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
  } }, "Send to agent");
  openDialog([
    h("h2", {}, "Log session feedback"),
    h("p", { class: "sub" }, "The agent grades the eval against that day's answer key, updates the student's strengths, weaknesses, and next topics, and builds homework if it's assigned."),
    h("div", { class: "fields" },
      h("div", { class: "field" }, h("label", {}, "Student"), sel),
      h("div", { class: "field" }, h("label", {}, "Session date"), dateIn),
      h("div", { class: "field full" }, h("label", {}, "Eval answers"), h("p", { class: "help" }, "In question order. Leave blank if there was no eval."), answers),
      h("div", { class: "field full" }, h("label", {}, "Notes"), notes),
      h("div", { class: "field full" }, h("label", {}, "Photos of work"), dz, fileList)),
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
    h("p", { class: "sub" }, "For sessions that aren't on your calendar. The agent reads them exactly like calendar events."),
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
    fill(ed, h("div", { class: "empty" }, h("p", {}, "Students live in roster.json — the agent reads it before every prep and rewrites it after every piece of feedback."),
      h("button", { class: "btn primary", onclick: newStudent }, "Add your first student")));
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
      d.__new ? null : h("div", { style: "display:flex;gap:10px" },
        h("button", { class: "btn small", onclick: () => { show("desk"); selectStudent(d.name); } }, "Open documents"),
        h("button", { class: "btn small", onclick: () => openFeedback(d.name) }, "Log feedback"),
        h("button", { class: "btn small", onclick: () => startPrep(todayISO(), [d.name]) }, "Prep today"))),
    h("div", { class: "fields" },
      h("div", { class: "field wide" }, h("label", {}, "Name"), h("p", { class: "help" }, "Matched against calendar event titles."), input("name")),
      h("div", { class: "field wide" }, h("label", {}, "Subject"), h("p", { class: "help" }, "Also sets how hard topics are pitched."),
        input("subject", { list: "dl-subjects" }), h("datalist", { id: "dl-subjects" }, subjects.map((s) => h("option", { value: s })))),
      h("div", { class: "field" }, h("label", {}, "Status"), status),
      h("div", { class: "field" }, h("span", { class: "lbl" }, "Homework"), h("label", { class: "toggle" }, hw, "Assign after sessions")),
      h("div", { class: "field wide" }, h("label", {}, "Textbook override"), h("p", { class: "help" }, "Optional. Leave blank to use the subject's default book."), input("textbook", { placeholder: "e.g. Foerster-AT — Algebra and Trigonometry (Foerster)" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Cover next session"),
        h("p", { class: "help" }, "Textbook topic names in priority order, written as Key §N-M: Topic Name. Prep teaches from the top."),
        listEditor("coverNextSession", { queue: true, placeholder: "Add a topic" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Strengths"), h("p", { class: "help" }, "Currently true statements, not a dated log."), listEditor("strengths", { placeholder: "Add a strength" })),
      h("div", { class: "field full" }, h("span", { class: "lbl" }, "Weaknesses"), h("p", { class: "help" }, "Also where standing instructions about this student live."), listEditor("weaknesses", { placeholder: "Add a weakness" }))),
    h("div", { class: "editor-actions" },
      h("button", { class: "btn primary", id: "save-student", disabled: (!S.dirty || busy) ? true : undefined, onclick: saveStudent }, d.__new ? "Add student" : "Save changes"),
      S.dirty ? h("button", { class: "btn", onclick: () => { S.draft = null; S.dirty = false; renderStudents(); } }, "Discard") : null,
      busy ? h("span", { class: "muted small" }, "Saving is paused while the agent works.") : null,
      h("span", { class: "spacer" }),
      d.__new ? null : h("button", { class: "btn danger", onclick: deleteStudent }, "Delete student"))));
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
    toast("Saved to roster.json");
  } catch (e) { fail(e); }
}

async function deleteStudent() {
  if (!confirm(`Remove ${S.student} from the roster? Their materials stay on disk.`)) return;
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
    fill(box, h("p", { class: "muted small" }, "Nothing generated yet. Prep a session and its worksheets show up here, one folder per day."));
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
            f.source ? h("button", { class: "link src", onclick: () => previewFile({ name: f.source.split("/").pop(), path: f.source, ext: ".txt" }) }, "source") : null)),
        g.other.length ? h("div", { class: "muted small", style: "margin:4px 8px 0" }, "Kept in the app:") : null,
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
    h("a", { class: "btn small", href: fileUrl(f.path) + "?download=1", download: f.name }, "Download"),
    inFolder ? h("button", { class: "btn small", onclick: () => api("POST", "/api/reveal", { path: f.path }).catch(fail) }, "Show in folder") : null);
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
    fill(tbl, h("tbody", {}, h("tr", {}, h("td", { class: "muted" }, "No textbooks yet. Upload the PDFs your students' classes use, plus any books you like teaching from."))));
  } else {
    fill(tbl, 
      h("thead", {}, h("tr", {}, h("th", {}, "Book file"), h("th", {}, "Key"), h("th", {}, "Search index"), h("th", {}, "Size"), h("th", {}))),
      h("tbody", {}, files.map((f) => {
        const job = jobs[f.file];
        let pill;
        if (job?.status === "running") pill = h("span", { class: "pill" }, "Indexing…");
        else if (job?.status === "error") pill = h("span", { class: "pill bad", title: job.detail }, "Indexing failed");
        else if (f.indexed && f.quality === "none") pill = h("span", { class: "pill bad", title: "Scanned with no text layer — run OCR on it to make it searchable." }, "No text layer");
        else if (f.indexed) pill = h("span", { class: "pill ok" }, f.quality === "good" ? "Indexed" : `Indexed (${f.quality} text)`);
        else pill = h("span", { class: "pill" }, "Not yet");
        return h("tr", {},
          h("td", {}, h("a", { href: fileUrl(f.path), target: "_blank", rel: "noopener" }, f.file)),
          h("td", {}, f.key || h("span", { class: "muted" }, "none yet")),
          h("td", {}, pill),
          h("td", { class: "muted" }, `${f.sizeMB} MB`),
          h("td", {}, !f.indexed && job?.status !== "running" ? h("button", { class: "btn small", onclick: () => indexBook(f.file) }, "Index now") : null));
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
        h("td", {}, h("button", { class: "link danger", onclick: () => { rows.splice(i, 1); paint(); } }, "Remove")))))),
    h("div", { style: "display:flex;gap:10px;margin-top:10px" },
      h("button", { class: "btn small", onclick: () => { rows.push({ subject: "", books: [], note: "" }); paint(); } }, "Add subject"),
      h("button", { class: "btn small primary", onclick: async () => {
        try { await api("PUT", "/api/books", { subjectDefaults: rows.filter((r) => r.subject.trim()) }); toast("Saved to books.json"); loadLibrary(); } catch (e) { fail(e); }
      } }, "Save")));
  paint();
}

function paintKeys(books) {
  const rows = books.books.map((b) => ({ ...b }));
  const box = $("#book-keys");
  if (!rows.length) {
    fill(box, h("p", { class: "muted small" }, "None yet — the agent adds a key the first time it cites a book."));
    return;
  }
  fill(box, 
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Key"), h("th", {}, "Title"), h("th", {}, "Author"), h("th", {}, "File name starts with"), h("th", {}, "Notes for the agent"))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, h("strong", {}, r.key)),
        h("td", {}, cellInput(r.title || "", (v) => (r.title = v))),
        h("td", {}, cellInput(r.author || "", (v) => (r.author = v))),
        h("td", {}, cellInput(r.filePrefix || "", (v) => (r.filePrefix = v))),
        h("td", {}, cellInput(r.notes || "", (v) => (r.notes = v))))))),
    h("div", { style: "margin-top:10px" }, h("button", { class: "btn small primary", onclick: async () => {
      try { await api("PUT", "/api/books", { books: rows }); toast("Saved to books.json"); loadLibrary(); } catch (e) { fail(e); }
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
  if (S.view !== "questions") return;
  const body = $("#oq-body");
  const rendered = md(S.oq, { breaks: false });
  const firstH1 = rendered.querySelector("h1");
  if (firstH1 && /open questions/i.test(firstH1.textContent)) firstH1.remove();
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
    h("p", { class: "sub" }, "This is open-questions.md in your workspace."),
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
      h("li", {}, h("strong", {}, "Google Calendar: "), "on a computer, open Settings, click the calendar under “Settings for my calendars”, then copy “Secret address in iCal format” from the Integrate calendar section. Tip: keep tutoring on its own calendar so nothing else shows up."),
      h("li", {}, h("strong", {}, "Apple Calendar (iCloud): "), "right-click the calendar, choose Share Calendar, tick Public Calendar, and copy the link."),
      h("li", {}, h("strong", {}, "Outlook: "), "Settings, Calendar, Shared calendars, Publish a calendar, then copy the ICS link.")),
    h("p", { class: "muted small" }, "The link is private — anyone who has it can read that calendar. It stays on this computer."));
}

function calendarFields(cfg, onTest) {
  const url = h("input", { type: "url", placeholder: "https://calendar.google.com/calendar/ical/…/basic.ics" });
  url.value = cfg.calendar.icsUrl;
  const filter = h("input", { type: "text", placeholder: "Optional, e.g. tutoring" });
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
    h("div", { class: "field" }, h("label", {}, "Only events containing"), h("p", { class: "help" }, "Leave empty if the calendar is only for tutoring."), filter),
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
    [["", "Claude Code's default"], ["opus", "Opus (best worksheets, uses more of your plan)"], ["sonnet", "Sonnet (faster, lighter on usage)"]]
      .map(([v, l]) => h("option", { value: v, selected: cfg.model === v ? true : undefined }, l)),
    cfg.model && !["opus", "sonnet"].includes(cfg.model) ? h("option", { value: cfg.model, selected: true }, cfg.model) : null);
  const shell = h("select", {},
    h("option", { value: "standard", selected: cfg.shellAccess !== "full" ? true : undefined }, "Standard — Python, LaTeX and file commands only"),
    h("option", { value: "full", selected: cfg.shellAccess === "full" ? true : undefined }, "Full — any command, without asking"));
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
    try { await api("PUT", "/api/config", body); await loadState(); toast("Settings saved"); } catch (e) { fail(e); }
  };
  fill(box, 
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", { class: "lede" }, `Workspace: ${S.state.workspace}`)),
      h("button", { class: "btn", onclick: () => api("POST", "/api/reveal", {}).catch(fail) }, "Show folder")),
    S.state.specUpdate ? h("div", { class: "card" }, h("h2", {}, "Updated agent instructions available"),
      h("p", { class: "muted" }, "You've edited CLAUDE.md, so the app saved its newer version as CLAUDE.md.new instead of overwriting yours. Merge by hand, or replace yours."),
      h("button", { class: "btn danger", onclick: async () => {
        if (!confirm("Replace your CLAUDE.md with the new version? Your edits will be lost.")) return;
        try { await api("POST", "/api/workspace/sync", { forceSpec: true }); await loadState(); renderSettings(); toast("CLAUDE.md replaced"); } catch (e) { fail(e); }
      } }, "Use the new CLAUDE.md")) : null,
    h("div", { class: "card" }, h("h2", {}, "You"), h("p", { class: "muted" }, "Times on your calendar and the agent's idea of “today” use this timezone."),
      h("div", { class: "fields" },
        h("div", { class: "field" }, h("label", {}, "Your name"), name),
        h("div", { class: "field" }, h("label", {}, "Timezone"), tzSel))),
    h("div", { class: "card" }, h("h2", {}, "Calendar"), h("p", { class: "muted" }, "Sessions and their lengths come from here. The app re-reads it every 15 minutes and right before each run."), cal.el),
    h("div", { class: "card" }, h("h2", {}, "Daily prep"),
      h("p", { class: "muted" }, "Preps every session on the day automatically. The app starts by itself when you log in; if the computer was asleep at prep time, it catches up when it wakes."),
      h("div", { class: "fields" },
        h("div", { class: "field full" }, h("label", { class: "toggle" }, apOn, "Prep automatically")),
        h("div", { class: "field" }, h("label", {}, "At"), apTime),
        h("div", { class: "field" }, h("span", { class: "lbl" }, "On"), h("div", { class: "days-pick" }, dayBoxes)),
        h("div", { class: "field full" }, h("label", { class: "toggle" }, apOnly, "Skip days with no sessions")))),
    h("div", { class: "card" }, h("h2", {}, "Claude"),
      h("p", { class: "muted" }, "The agent runs on Claude Code on this computer, signed in with your own Claude account (or an API key if you set one). The app never sees your credentials."),
      h("div", { class: "fields" },
        h("div", { class: "field" }, h("label", {}, "Model"), model),
        h("div", { class: "field" }, h("label", {}, "Shell access"), shell))),
    h("div", { class: "editor-actions", style: "position:static;background:none;padding:0 0 18px" }, h("button", { class: "btn primary", onclick: save }, "Save settings")),
    h("div", { class: "card" }, h("div", { class: "side-head" }, h("h2", {}, "Health check"),
      h("span", {}, h("button", { class: "link", onclick: () => runChecks(false) }, "Re-check"), "   ",
        h("button", { class: "link", onclick: () => runChecks(true) }, "Compile a test worksheet"))), checksBox));
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
    h("summary", { class: "muted", style: "cursor:pointer" }, "Bring over an existing tutoring folder"),
    h("div", { class: "fields", style: "margin-top:10px" },
      h("div", { class: "field full" }, h("p", { class: "help" }, "A folder with CLAUDE.md and roster.json. It's copied; the original isn't changed."), importPath),
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
      toast("All set. Settings has everything if you need to change it later.");
    } catch (e) { fail(e); done.disabled = false; }
  } }, "Finish setup");
  const dlg = openDialog([
    h("h2", {}, "Finish setting up"),
    h("p", { class: "sub" }, "Claude Code, LaTeX and your workspace are ready. Two things only you know:"),
    h("div", { class: "fields" },
      h("div", { class: "field" }, h("label", {}, "Your name"), name),
      h("div", { class: "field" }, h("label", {}, "Timezone"), tzSel)),
    h("div", { style: "margin-top:16px" }, cal.el),
    h("label", { class: "toggle", style: "margin-top:4px" }, autoOn, "Prep every session automatically each morning at ", autoTime),
    importBox,
    h("div", { class: "dialog-actions" },
      h("span", { class: "muted small" }, "You can skip the calendar and add it later in Settings."),
      h("span", { class: "spacer" }), done),
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
  fill(box, h("strong", {}, "Prep won't work until this is fixed: "),
    bad.map((c) => h("span", { class: "issue" }, `${c.label}: ${c.detail} `, c.fix ? h("code", {}, c.fix) : null)),
    h("span", { class: "muted" }, " Run that in Terminal, then reload this page."));
}

/* ------------------------------------------------------------------ boot */
function wire() {
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#btn-prep-today").addEventListener("click", () => startPrep(todayISO()));
  $("#btn-feedback").addEventListener("click", () => openFeedback());
  $("#btn-refresh-cal").addEventListener("click", async () => {
    try { S.state.schedule = await api("POST", "/api/schedule/refresh"); renderSchedule(); toast("Calendar refreshed"); } catch (e) { fail(e); }
  });
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
    toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`);
    try { await upload("/api/library/upload", files); toast("Uploaded — indexing in the background"); loadLibrary(); } catch (err) { fail(err); }
  });
  $("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay" && !$("#overlay .setup")) closeDialog(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#overlay").hidden) { if (!$("#overlay .setup")) closeDialog(); }
    else if (S.viewer) closeViewer();
  });
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  wireComposer();
}

(async function boot() {
  wire();
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
