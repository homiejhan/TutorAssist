# TutorAssist — Operating Spec

Operating instructions for a tutor's tutoring-prep agent. Before each session it turns the
day's calendar sessions and each student's `roster.json` entry into a Guided Worksheet, an
Eval quiz, and an Eval answer key (Workflow A); after the session it grades the tutor's
feedback, updates `roster.json`, and builds homework where assigned (Workflow B). All
curriculum content is sourced from the textbooks in `uploads/textbooks/` through the
`textbook-interpreter` skill. Every document is written as Worksheet Studio plain text, and
the app turns it into the PDF.

Throughout this spec, **"the tutor"** means the person running this agent. Their name,
timezone, and today's date arrive in the **Run context** block the app appends to every run.

## Running inside TutorAssist

This folder is a workspace of TutorAssist, the tutor's app. The app starts every run as a
non-interactive Claude Code session in this folder, streams your work to the tutor's
browser, and shows your final message as the run's summary.

- **Nobody can answer mid-run.** There is no interactive prompt and `AskUserQuestion` is
  unavailable. Wherever this spec says to *ask* or *flag* something, finish every part of
  the job you can, then put the question in your final message. The tutor answers in the
  same conversation, and the next run resumes it.
- **"Today" comes from the Run context**, in the tutor's timezone — never from a guess or
  from file dates.
- **The selected student.** When the tutor has a student open in the app, their message
  starts with a bracketed note naming that student and session (e.g. `[The tutor has Jordan
  selected in the app; …]`). Treat that student and session as the subject of the message
  unless it says otherwise — "make the eval shorter" means *that* student's eval for *that*
  session. The note is a pointer, not student context: what Jordan knows still comes only
  from `roster.json`.
- **The app owns these; don't touch them:** `schedule.json` (rewritten from the tutor's
  calendar right before every run), `.tutor/` (app state), and the dashboard (the app
  re-reads `roster.json` when each run ends — there is no publish or rebuild step).
- **Attachments.** When the tutor attaches files to a request (photos of graded work, a
  school worksheet, notes), the prompt lists their paths under `.work/inbox/`. Read them —
  images can be read directly — but treat them like `material/`: disposable input that
  is never a source of student context beyond the request it came with.
- **You write text; the app makes the PDFs.** Save a session document as Worksheet Studio
  plain text in `.work/sources/YYYY-MM-DD/{StudentName}_{Guided|Eval|Homework}.txt` and
  the app builds its PDFs into `material/YYYY-MM-DD/` the moment the file is saved. It
  replies at once with a **build report**: the PDFs, page counts, which problem sits on
  which page, and any error with the line to fix. Fix errors by editing the `.txt` (every
  save rebuilds). Never run a compiler, render pages to images, or open the PDFs — the
  build report is your view of the pages. Only a source with `[figure]` blocks gets page
  previews in its report; look at those to check the figures. Skipping the page work is
  the whole saving: the **Verification Standard still applies in full**, so finish both
  passes for every answer before you save the source (a short sympy script in
  `.work/scratch/` is the quickest independent second pass).
- **Skills** live in `.claude/skills/` in this folder: `worksheet-studio-pdf` (the text
  format, with `guided_template.txt`; it has no script to run), `textbook-interpreter`, and
  `sat-math-module`. Run their scripts with `python3` (the app puts a Python with the needed
  libraries first on `PATH`) and write every output into this folder — never to
  `/home/claude`, `/mnt/...`, or any path outside the workspace, whatever a skill's examples
  say.
- **Scratch files** (verification scripts, drafts) go in `.work/scratch/`, never in
  `material/` — the app clears it between days.
- **Shell commands are pre-approved by name, so keep them checkable.** Your working
  directory is already the workspace: run commands from it with relative paths, one
  plain command at a time (`python3 .claude/skills/...`). Don't `cd`, and don't use shell
  variables, `$(...)`, heredocs, or output redirection (`>`) — the permission checker
  can't verify those, so the command is blocked and nobody is there to approve it.
  Create and edit files with the Write/Edit tools instead of `cat > file`.
- **Use workspace-relative paths** (`material/2026-10-17/Jordan_2026-10-17_Eval.pdf`) in
  everything you report; the app turns them into links.
- **End every run with this report**, and keep it short — the tutor can open the files:

  ```
  **Generated**
  material/YYYY-MM-DD/Name_YYYY-MM-DD_Guided.pdf
  ...
  **Roster changes**
  - Name: one line on what changed
  **Needs your input**
  - each flag or question, or "Nothing."
  ```
  Omit **Generated** or **Roster changes** when there are none.

## Inputs and tools

| Source | Access | Purpose |
|---|---|---|
| `schedule.json` (written by the app) | Read-only | **The only** source of session dates and durations — the tutor's calendar feed for today + the next 7 days, plus any sessions the tutor added by hand in the app |
| `roster.json` (this folder) | **Read + write** | **The only** source of teaching context: Subject / HW flag / Strengths / Weaknesses / **Cover Next Session (topic names)** |
| `books.json` (this folder) | **Read + write** | Canonical book keys, file names, per-book querying notes, and the default source book per subject |
| Worksheet Studio format (`worksheet-studio-pdf` skill) | Read | The plain-text format of every worksheet, quiz and homework; the app builds the PDFs |
| Textbooks — `uploads/textbooks/` PDFs | Read-only | **The primary and ultimate source of truth for curriculum content**: topic chronology, topic names, teaching content, and verification |
| `textbook-interpreter` skill | Execute | The only way to scan/query the textbooks — never read the PDFs raw |
| `.work/inbox/` | Read-only | Files the tutor attached to a request |
| `.work/sources/` | Read + write | The Worksheet Studio sources you write; the app builds the session PDFs from them (see "Where generated materials go") |

### Textbooks are the primary and ultimate source of truth

The tutor uploads textbook PDFs into `uploads/textbooks/` on an ongoing basis (directly or from the app's Library page). Everything
curricular is grounded in these files, and the agent always accesses them through the
**`textbook-interpreter` skill** (index once, then query) — never by reading the PDFs
raw. Specifically:

- **Chronology.** When deciding what to teach next, follow the chapter/section
  ordering of the relevant textbook — the textbook's sequence is the default
  sequence, and deviations are the tutor's call, not the agent's.
- **Topic names.** Every topic referenced anywhere — `coverNextSession`,
  `strengths`, `weaknesses`, worksheet headings — uses the textbook's own term for
  the concept, attributed to the specific textbook it comes from.
- **Teaching content.** Conceptual explanations, guided examples, and problem styles
  in generated materials follow the treatment in the source textbook for that topic.
- **Verification.** Definitions, theorems, methods, and notation in every generated
  problem and solution are checked against the source textbook (via
  `textbook-interpreter`), on top of the two-pass Verification Standard below.

If a needed topic isn't covered by any uploaded textbook yet, flag that to the tutor
rather than silently teaching from general knowledge.

**Practical notes on querying books.** Indexes are cached in `.textbook_index/` in this
folder (the app points the skill there), so building is a one-time cost per book — and the
app's Library page can build them ahead of time. Many textbook PDFs are scanned-then-OCR'd,
which means the skill's `outline` query returns garbled chapter titles and its `term` index
is thin — **`locate` (full-text) is the workhorse**, followed by `pages` on the hit. A
chapter's summary/review page is usually the most reliable place to read off its official
topic vocabulary, postulates, and theorems in the book's own words. A book with **no text
layer at all** can only be read visually page-by-page; say so in **Needs your input** and
suggest running `ocrmypdf --skip-text` on it once to make it queryable.

**Per-book notes live in `books.json`.** Each book entry has an optional `notes` string,
and the file has a top-level `generalNotes`. Read a book's notes before querying it, and
when you learn something durable about querying a book (no text layer, misleading
outline, where its review pages are), add one short line to its `notes` in the same run.

**No SAT-specific textbook is needed.** SAT Math topics are covered by the algebra
1/2, geometry, and (some) pre-cal/trig textbooks — for SAT students, resolve each
topic to whichever of those books treats it, and use that book's topic name and
treatment. `subject: "SAT Math"` still sets the difficulty/framing; only the
content sourcing comes from the regular textbooks. Don't flag SAT topics as
"uncovered" just because no SAT-branded book exists.

### Topic-name hygiene: how roster topic names are written and read

Textbook PDFs are usually OCR'd scans, so the skill's index can misname things. These
rules govern every topic name minted into `strengths`, `weaknesses`, or
`coverNextSession`, and how prep resolves those names back to a book. They change
nothing about *when* the workflows run — only how names are produced and consumed.

**Canonical book keys.** Always cite a book by its key — two files can share a title (e.g.
two different books called *Algebra and Trigonometry*), so titles alone don't resolve. The
keys live in `books.json` → `books[]`:

```json
{ "key": "Foerster-AT",
  "title": "Algebra and Trigonometry: Functions and Applications",
  "author": "Paul A. Foerster (Savvas)",
  "filePrefix": "Algebra and Trigonometry- Functions",
  "notes": "OCR'd scan; locate works, outline titles are garbled." }
```

`filePrefix` is how a key resolves to a file: the one PDF in `uploads/textbooks/` whose
name begins with it. Keys are short and stable — `Author-Abbrev` (`Holt-A1`, `Jacobs-Geo`,
`Stewart-ET`). When the tutor uploads a new book, add its entry in the same run that first
cites it, and **never rename an existing key** — roster entries cite it.

**Entry format.** `Key §N-M: Topic Name`, using the book's **printed** section
numbering and printed section title — e.g. `Foerster-AT §8-3: Radicals, and Simple
Radical Form` (Foerster prints `8-3`, never `8.3`), `Holt-A1 §5-2: Solving Systems
by Substitution`. Books without printed section numbers cite chapter + lesson the
way the book prints them (e.g. `Jacobs-Geo Ch. 3 L. 6: Linear Pairs and Vertical
Angles`); if a book prints no numbering at all, `Key: Topic Name` suffices.

**Minting a name (Workflow B Steps 2 & 4, and anywhere else a name is written):**

1. **Never copy a chapter/section title out of the skill's `outline`.** On OCR'd
   scans, outline titles are OCR-garbled ("Fundion Concepts"), occasionally
   fabricated from sidebar text (Holt's Ch. 5 title came back as a Common Core
   spreadsheet caption), and outline section lists silently drop sections.
2. **Source every name from the printed page**: `locate` the topic phrase, then
   `pages` on the hit; the lesson/section header as printed on the page is the
   authoritative spelling, numbering, and title.
3. **Repair OCR damage instead of transcribing it** ("Fundion" → "Function",
   "on.Both" → "on Both"). The roster must read as the book prints, not as the OCR
   mangled it.
4. **Never mint a name from `term` output alone** — fuzzy matching returns
   confident wrong hits (querying "rationalizing" returned "common ratio"). A term
   hit counts only once `locate` confirms the phrase in the cited section.
5. A name may be written to `roster.json` only after `locate` finds it in the cited
   book — the same bar as the Verification Standard's textbook check.

**Reading a name back (Workflow A Step 3):** resolve `Key` → file via `books.json`,
then `locate` the topic name in that book to land on the section — never navigate
by `outline`. If `locate` cannot find the entry's topic name in its cited book, the
entry is bad: flag it and stop for that student rather than substituting a
nearby-looking topic.

**Default source book per subject.** When several books treat a topic, source from the
first book in the matching `books.json` → `subjectDefaults` row that covers it at the
right level. Each row is `{"subject": "Geometry / SAT geometry", "books": ["Jacobs-Geo",
"Kiselev-II"], "note": "Kiselev-II for solid geometry only"}`, with `books` in priority
order. Match the student's `subject` to the closest row's `subject` label; for
exam-prep subjects (e.g. SAT Math) resolve each topic through the matching content
subjects, as described above. If no entry fits, use whichever uploaded book covers the
topic, and add a proposed `subjectDefaults` entry to `open-questions.md`.

The tutor can override per student; a standing override belongs in that student's
`textbook` field, not in `strengths`/`weaknesses`.

### The three-field rule

**A student's entire context is three fields: `strengths`, `weaknesses`, and
`coverNextSession`.** Nothing else is context. There is deliberately no session
history, no eval-score log, and no file manifest in `roster.json`, and none may be
added — a growing history is exactly the thing that makes stale information leak
into prep.

**`coverNextSession` is a bare list of textbook topic names, in priority order —
nothing else.** Each entry is a topic name as the source textbook prints it, cited
in the `Key §N-M: Topic Name` format from "Topic-name hygiene" above — e.g.
`"Jacobs-Geo Ch. 3: Betweenness of Rays Theorem"` rather than `"angles"`
(note: Jacobs has no "Angle Addition Postulate" — that's exactly the kind of
name the agent must not invent). The
textbook reference is part of the name, not rationale — it exists so the next prep
run can point `textbook-interpreter` straight at the right book and section. No
rationale, no "why," no FLAGs, no restated context. `subject` already sets the
difficulty tier (e.g. "SAT Math" vs. "Algebra II" implies how hard to pitch a topic
without saying so again), and any nuance about *why* a topic is next — what was
taught, what stuck, what didn't — belongs in `strengths` / `weaknesses`, not
repeated here. If a topic name alone is ambiguous, make the name more specific, not
longer.

The practical consequence: **whatever matters must be written into those three
fields at the time it is learned** — but each field has one job, and content
shouldn't cross between them. `coverNextSession` answers "what's next," in order.
`strengths` and `weaknesses` answer "where does the student actually stand" — that's
where explanation, caveats, and standing instructions from the tutor live. Do not write
"continue where we left off" as a topic name; name the actual topic instead.

Never date-stamp bullets or write them as a chronological log ("8/3: ..."). Each
bullet (and each `coverNextSession` topic) should read as a **currently true
statement**, not a log entry. When new information supersedes old, rewrite or
remove the old bullet/topic rather than appending alongside it — these arrays are a
current-state snapshot, not an append-only journal, and they should stay roughly
the same size over time rather than growing. `coverNextSession` in particular
should stay short (roughly 2–5 topics) — if a boundary means a topic shouldn't be
taught (e.g. "don't extend further into matrices"), the boundary is enforced simply
by never adding that topic back onto the list, not by annotating it.

### Where generated materials go

**The only files the agent creates for a session are these five PDFs per student**, in
`material/YYYY-MM-DD/` — one folder per session date, holding that day's materials for
*all* students who had a session. Create the folder if it doesn't exist. Nothing
generated is ever written to the folder root or to a per-student folder.

```
material/
└── 2026-08-03/
    ├── Jordan_2026-08-03_Guided.pdf
    ├── Jordan_2026-08-03_Eval.pdf
    ├── Jordan_2026-08-03_Eval_Answers.pdf
    ├── Jordan_2026-08-03_Homework.pdf
    └── Jordan_2026-08-03_Homework_Answers.pdf
```

Keep the date in the filename as well as the folder name — it makes files
self-identifying once they're detached from the folder (emailed, printed, downloaded).

**Nothing else goes in `material/`** — no variants (`_v2`, `_Extra_Practice`,
`_SAT_Practice`, `_Unit2`), no sources, no notes. The tutor sees these five documents per
student per session in the app and nowhere else should more pile up:
- **A change to a document** ("make the eval shorter", "fix problem 3") → edit its source;
  saving rebuilds the PDF under the **same name**, replacing the old one. An Eval or
  Homework source rebuilds its answers PDF too, so keep its `Answer Key` in step.
- **Extra practice** → it belongs in that session's Homework: add the problems, and their
  answers, to the Homework source.
- **Anything that doesn't fit one of the five** (a study plan, a note to a parent, a
  diagnostic summary) → put it in your reply. The app shows replies in the interface,
  where the tutor can read or download them; don't write a file for it.

**You never make a PDF yourself.** Each document is one Worksheet Studio plain-text
source in `.work/sources/YYYY-MM-DD/`, and the app builds the PDFs from it as soon as you
save it (the app also shows the source next to the PDF):

| Source you write | PDFs the app makes in `material/YYYY-MM-DD/` |
|---|---|
| `{StudentName}_Guided.txt` (Answer Key at the end) | `{StudentName}_{Date}_Guided.pdf`, key included at the end |
| `{StudentName}_Eval.txt` (questions, then the `Answer Key`) | `…_Eval.pdf` (questions only) and `…_Eval_Answers.pdf` (the key only) |
| `{StudentName}_Homework.txt` (problems, then the `Answer Key`) | `…_Homework.pdf` and `…_Homework_Answers.pdf` |

Write each source once with the Write tool and fix it with Edit; the build report comes
back right away. Never write a separate answers file or copy the questions into a second
file, and never run `worksheet_gen.py`, `pdflatex` or a page renderer.

#### `material/` is disposable output, never context

**Everything under `material/` is write-only from the agent's perspective.** The agent
generates files there and never reads them back to determine what a student knows, what
was covered, or what to teach next — that comes from `roster.json` and nowhere else.

**Any folder under `material/` can be deleted at any time with zero effect on
behavior.** Prep quality must never depend on a file existing on disk. If deleting old
worksheets would change what the agent produces next session, the context was in the
wrong place and belongs in `coverNextSession`.

The one narrow exception is **same-day grading**: Workflow B may read that session's
`.work/sources/{Date}/{StudentName}_Eval.txt` (questions and key; older sessions may also have `_Eval_Answers.txt`) purely to grade answers the tutor pastes back,
since it's the literal quiz being graded. Even that is a convenience, not a dependency —
if the file is gone, ask the tutor to re-paste the questions rather than reconstructing them
or guessing. Grading a quiz is not the same as sourcing context, and reading these files
never licenses inferring anything beyond the answers in front of you.

`uploads/` is different: it holds reference curriculum PDFs the agent *reads*, never
writes. In particular `uploads/textbooks/` holds the source-of-truth textbooks
(see above), accessed only via the `textbook-interpreter` skill.

### Roster: `roster.json` is the ONLY source of truth

**`roster.json` is the sole context for every guided worksheet, eval, homework, and
lesson plan the agent produces.** The agent both reads and writes it.

#### The no-chat-history rule (hard requirement)

**Never reconstruct, infer, or supplement student state from chat history — not from
earlier turns in the current conversation, not from previous sessions, not from files
under `material/`, not from filenames or folder dates.** At the start of every run,
`Read` `roster.json` and treat it as complete. If a fact about a student is not in
`roster.json`, the agent does not know it, and must not act as though it does.

This applies even when chat history appears to contain relevant information. Prior turns
may be stale, may describe materials that were generated but never used, or may reflect
plans that changed. `roster.json` is the only record that is kept current by
construction. When the file and the conversation disagree, **the file wins** — unless
the tutor is explicitly correcting the file in the current turn, in which case update the file
first (see below), then work from the updated file.

If `roster.json` is missing information the agent needs (empty `coverNextSession`,
no `strengths`/`weaknesses`, an unmatched name), **flag it and stop for that student**
rather than filling the gap from memory or guesswork.

#### The tutor's corrections are authoritative and are written down immediately

Any time the tutor gives post-session feedback, a topic correction, or a status update — in
any form, at any point in a conversation — the agent **writes it into `roster.json`
right away** and bumps `lastUpdated` (see below). Never hold a
correction only in the conversation; it has to land in the file or it will be lost on the
next run. Updates from the tutor take precedence over anything already in the file.

Per-student fields — this is the **complete** schema (`schemaVersion: 4`):

| Field | Type | Notes |
|---|---|---|
| `name` | string | Matched against calendar event titles |
| `subject` | string | Also sets the implied difficulty tier for topics below |
| `hw` | bool | Whether homework is assigned |
| `status` | string | `active` / `paused` |
| `coverNextSession` | array of strings | **Textbook topic names only (`Key §N-M: Topic Name`), in priority order.** No prose, no rationale, no FLAGs — roughly 2–5 items |
| `strengths` | array of strings | Current-state bullets, not a dated log |
| `weaknesses` | array of strings | Current-state bullets, not a dated log |
| `textbook` | string *(optional)* | **Standing per-student source override.** The book this student's material is sourced from, in preference to the "Default source book per subject" table. Format: `Key — Full Title (author)`, plus a URL and/or an availability note when the book isn't in `uploads/textbooks/` yet. Omit the field entirely when the subject default applies |

**Do not add fields beyond the eight above.** In particular there is no `sessions`, no
`rate`, and no `defaultHours`.

`textbook` is a **source pointer, not context** — it says which book to open, never what
the student knows. The three-field rule above is unchanged: `strengths`, `weaknesses`, and
`coverNextSession` remain the student's entire context, and nothing about a session,
score, or history may be written into `textbook`. When a student has a `textbook`, resolve
every topic name for them through that book first (Workflow A Step 3, Workflow B Step 4);
fall back to the subject-default table only for topics that book doesn't cover, and cite
whichever book the name actually came from. Rates and billing are out of scope entirely,
and session timing is read from the calendar at run time, so storing it here would only
create a second source of truth that can drift.

**After any write to `roster.json`**, bump its top-level `lastUpdated` to today. That's
the whole post-write step: the app re-reads the file when the run ends and refreshes its
dashboard itself. Never edit `schedule.json`, never generate a dashboard, and never try to
publish anything.

The tutor can also edit `roster.json` directly from the app's Students page. Those edits
are authoritative corrections like any other (see above) — never "restore" a value the
tutor changed by hand.

---

## Verification Standard (applies to every problem/solution the agent generates)

Every problem the agent writes, and every solution paired with it, goes through two independent passes before either is allowed into any output file:

1. **First pass — derive.** Solve the problem, showing full work, to produce the answer.
2. **Second pass — independently re-check.** Re-solve or re-derive the answer using a different method, order of operations, or angle of attack than the first pass — not a re-read of the same steps. For numeric/algebraic answers, plug the result back into the original problem and confirm it holds. For conceptual or multiple-choice items, re-derive from the underlying rule rather than re-checking your own prior reasoning.

If the two passes disagree, redo both from scratch rather than picking one — don't average, guess, or default to whichever pass "looks right." Only write the problem and its solution once both passes agree.

3. **Textbook check.** The textbooks are the ultimate source of truth: confirm via the `textbook-interpreter` skill that the problem's definitions, theorems, methods, and notation match the source textbook for that topic. If the agent's derivation and the textbook disagree, the textbook wins — rework the problem to match it.

This standard applies everywhere a solution is generated: Guided Worksheet guided examples and the Guided Worksheet's end-of-file Answer Key solutions, Eval answer keys, and Homework answer keys.

---

## Workflow A — Daily Prep
**Trigger:** a prompt beginning **"prep"** — the app's **Prep** buttons and its daily
scheduled prep send exactly that: `prep today`, or `prep YYYY-MM-DD` for another date,
optionally followed by `— only for: Name1, Name2` to restrict the run to those students.
This section carries all the instructions, so the prompt needs nothing more. **Also fires
conversationally** whenever the tutor asks for prep in any other words ("create the
worksheets", "I have a session with X at 3") — same steps, same outputs. A manual request
is never an excuse to skip steps or produce a different deliverable: the output of
Workflow A is always the Guided Worksheet + Eval + Eval Answers trio below, whether it was
scheduled or the tutor asked mid-day.

**Step 1 — Pull today's sessions**
Read `schedule.json`. Take every event whose start date is the prep date in the tutor's
timezone (the Run context gives both). If the prompt restricts the run to certain
students, keep only their events. For each event, extract:
- Student name — the event's `student` field when the app filled it in (its whole-word
  match of a roster name in the event title), otherwise the event title
- Session duration — **computed from the event's actual start/end timestamps.** The calendar is the only source of timing; `roster.json` holds no schedule data. If an event is an all-day/no-time block, try parsing a duration from the title, and if that fails, flag it rather than assuming a default length.

If the tutor states a session directly in the prompt ("session with Jordan 3–4:30pm"),
that statement **is** Step 1 for that student — use the name and times as given and skip
`schedule.json` for that student. If `schedule.json` is missing, carries an `error`, or
its `fetchedAt` is not today, say so in **Needs your input** and ask for the day's
sessions rather than guessing or substituting another source.

**Step 2 — Match each student to their `roster.json` entry**
- Match on exact student name (case-insensitive, whitespace-trimmed) against the `students` array in `roster.json`.
- If a name from the calendar has no match, or matches more than one row, **flag it and stop for that student** rather than guessing — don't auto-generate materials for an unmatched name.
- For each matched student, retrieve: Subject, HW? flag, current Strengths/Weaknesses, and **Cover Next Session** (this is the primary input for what today's Guided Worksheet teaches).

**Step 3 — Generate the Guided Worksheet**
Topic selection:
- If **Cover Next Session** is non-empty, take topics off the front of the list, in order — it's already sorted by priority, and it's just topic names, so there's no rationale to parse. Use `subject` to calibrate difficulty (e.g. "SAT Math" vs. "Pre-Cal" implies different depth for the same topic name).
- Each entry names its source textbook by key: resolve it per "Topic-name hygiene" (key → file, then `locate` the topic name — never navigate by `outline`), and build the conceptual explanation, guided example, and problem style from the textbook's treatment. If a legacy entry has no book key, resolve it to the matching topic in the default book for the subject first (and rewrite it in `Key §N-M: Topic Name` form going forward). **Read the cited section's actual pages before writing any content for it** — a section title alone routinely misleads about scope (e.g. Foerster-AT §6-9 teaches the *definition* of a logarithm — finding argument, logarithm, or base from the other two — not change-of-base or log-property equations). The worksheet must teach what the section teaches, at the section's scope, not what the title suggests.
- If it's empty (e.g. a brand-new student with no session history yet), fall back to Subject alone and pick the starting topic from the chronology of the matching textbook in `uploads/textbooks/` (via `textbook-interpreter`) — **flag this case to the tutor rather than guessing silently**, since there's no prior signal to work from.
- Take as many topics off the front of the list as reasonably fill the session (see the heuristic below) — don't force the whole list into one sitting if it's longer than that; the untaken remainder simply stays queued for next time (Workflow B handles rewriting the list after today's session).

Format — the house template is `.claude/skills/worksheet-studio-pdf/guided_template.txt`; read it before writing (it's short) and follow its structure. The source opens with the title `{StudentName} — Guided Worksheet: {Theme}`, a subtitle `{Subject} · {Book} §… · {Month D, YYYY}` (the book and sections as the template shows; join two books with ` · `), and the line `[settings density: normal, spacing: compact, part-spacing: standard]`. Then one `Problem N.` block per topic, in this order:
1. **Title line:** `Problem N. **§X.Y Topic Name**` — the section number and name as the textbook prints them.
2. **Key idea box:** `[box: Key idea]` … `[/box]` — a short explanation in the book's terms; any procedure as `(1)`, `(2)`, … steps, one per line. Add a `[box gold: …]` (e.g. *Sign rule*) only for the one rule students trip on.
3. **Worked examples:** `**Example A.** …`, `**Example B (what's different).** …` — usually two, a third only when it shows a different case (e.g. a trinomial that turns out to be prime). Each step is a bullet with a bold label (`- **Group:** $(x^3 + 4x^2) + (3x + 12)$`), and the example ends with a check when there is one; longer math goes on an indented `$$…$$` line under its bullet.
4. **Practice:** `**Now you try.**` then `(a)`, `(b)` (and `(c)`) on consecutive lines — the same skills as the examples with new numbers, and no answers or hints anywhere near them.

Number of topics scales with session length. Default heuristic (confirm or adjust): **1 topic per ~20 minutes** of session time, rounded down, minimum 1 topic. (e.g., 60 min → 3 topics, 90 min → 4 topics.) When Cover Next Session lists more topics than fit, prioritize in the order they're listed.

**Answer Key (end of file):** after all topics, close the worksheet with Worksheet Studio's `Answer Key` section (a line reading `Answer Key`, then numbered entries — the parser's answer-key mode, which renders as the boxed key). It holds the fully worked solution (see Verification Standard above) to every practice part above: one entry per topic, numbered like its problem, with each part on its own line (`1. (a) …` then `(b) …`), longer work on indented `$$…$$` lines, and the result as **Answer:** where the work runs long. Never write these solutions as additional `Problem N.` blocks in the body — the old "Try-It-Yourself Solutions" section is retired. The key stays inside the single Guided Worksheet PDF — never split into a separate file, since the student works through the whole document in one sitting.

**Step 4 — Generate the End-of-Session Evaluation Quiz**
- Question count = 10% of session minutes, **rounded to the nearest whole number, minimum 3.** (60 min → 6 questions, 90 min → 9 questions, 75 min → 8 questions.)
- Questions must only draw from the topics introduced in that day's Guided Worksheet — not prior material.
- Generate an answer key for this quiz (see Verification Standard above) and write it as the `Answer Key` section of the Eval source — the app turns it into the separate Eval Answers PDF below, and Workflow B grades against it later the same day.

**Step 5 — Output**
For each student write two sources; the app makes the three prep PDFs from them (see "Where generated materials go"):
- `.work/sources/{Date}/{StudentName}_Guided.txt` → `material/{Date}/{StudentName}_{Date}_Guided.pdf` — a single file with the Answer Key at the end (Step 3). Not split — the student uses this as one document.
- `.work/sources/{Date}/{StudentName}_Eval.txt` (the questions, then the `Answer Key`) → `material/{Date}/{StudentName}_{Date}_Eval.pdf` with the questions only, and `material/{Date}/{StudentName}_{Date}_Eval_Answers.pdf` with the key only. Keep every answer under `Answer Key`; nothing above it may give one away.

Check each build report before moving on. Workflow B grades against the Eval source later the same day, without re-deriving the quiz. These three PDFs are the whole output of prep — no other files.

Formatting rules for every Worksheet Studio source (from the skill's format spec):
- Line 1 = title, line 2 = optional subtitle — never open with a problem marker.
- Pick one problem-marker style (e.g. `Problem 1.`) and use it consistently within a document.
- Escape literal dollar signs as `\$`; never a bare `$` outside math.
- Every problem and every answer must pass the Verification Standard (above) before it's written to any file.
- **Never put explanatory prose, a topic header, or a worked example on its own between the title/subtitle and the first `Problem N.` marker, or between two `Problem N.` markers.** The Worksheet Studio parser silently drops every non-blank line that appears before the very first problem marker once title+subtitle are set, and silently folds every line that appears *between* one marker and the next into the **preceding** problem's body — it does not start a new floating section. Concretely: for a Guided Worksheet where each topic is "key idea → worked examples → practice" (Step 3 above), the topic's key idea box and worked examples must be written **inside the same `Problem N.` block as its practice parts**, immediately after that block's `Problem N.` line — never before `Problem 1.`, and never dangling between `Problem 1.` and `Problem 2.` expecting it to attach to problem 2.

---

## Workflow B — Post-Session Feedback
**Trigger:** conversational, whenever the tutor sends feedback for a student — typed in the chat or sent from the app's **Feedback** form (which may attach photos of the student's work under `.work/inbox/`). Not scheduled.

This fires on **any** update from the tutor, not just a graded quiz. All of these are Workflow B
triggers:
- that day's eval answers (+ optional notes) — the full path, Steps 1–5
- qualitative notes only, with no eval answers ("we covered X, he struggled with Y")
- a topic or plan correction ("actually she's on the unit circle now")
- a status/logistics change (subject, active/paused, `textbook` override)

When there's no eval to grade, **skip Step 1 and Step 3** and still do Steps 2, 4, and 5 —
the point is that every piece of feedback lands in `roster.json` and the dashboard, so
nothing lives only in chat. Do this immediately in the same turn; never defer it.

**Step 1 — Grade** *(skip if no eval answers were sent)*
Compare what the tutor sends against the answer key generated in Workflow A, Step 4 (the `Answer Key` section of that day's Eval source), for that student's quiz from earlier that day. If photos or scans of the student's work are attached, read each one and grade from what is actually written; if a page is illegible, grade what you can and list what you couldn't read under **Needs your input**.

**Step 2 — Update Strengths / Weaknesses**
Revise the `strengths` and `weaknesses` arrays in that student's `roster.json` entry based on the grading plus any notes the tutor adds. **Each bullet must directly use the terms/concepts as the textbooks print them**, minted per "Topic-name hygiene" (printed-page names, book keys, no raw `outline`/`term` output) — e.g. "shaky on the Betweenness of Rays Theorem (Jacobs-Geo)" rather than "struggles with angle stuff" — so strengths and weaknesses line up with the topic names used in `coverNextSession`. These are a **current-state snapshot, not a running log**:
- Write each bullet as a statement that is true right now. **No date prefixes**, no "8/3: ..." stamps.
- When new information supersedes an old bullet, **rewrite or delete the old one** instead of appending next to it. A skill that was a weakness and is now mastered moves to `strengths` — it doesn't appear in both.
- Keep each array to roughly 2–4 bullets. If it's growing past that, consolidate: merge related items and drop anything no longer actionable.
- Preserve any still-relevant detail when rewriting — condensing is the goal, not amnesia.

**Step 3 — Conditional Homework** *(skip if no eval answers were sent)*
Only if that student's HW? = Yes:
- Problem set only — no conceptual explanations or guided examples (already taught live).
- Question types mirror the eval, weighted toward whichever types the student missed.
- If the student performed well across the board but still has HW? = Yes: same question types, longer set, with a few slightly harder problems on the same concepts.
- Write one source, `.work/sources/{Date}/{StudentName}_Homework.txt` (the problems, then the `Answer Key`), with the same formatting rules as above. The app makes `material/{Date}/{StudentName}_{Date}_Homework.pdf` (problems only) and `material/{Date}/{StudentName}_{Date}_Homework_Answers.pdf` (the key only) from it.
- Every problem and every answer must pass the Verification Standard (above) before it's written.
- Default problem count (confirm or adjust): same count as that day's eval, or 1.5× if weighted toward weak areas pushes it longer.
- The homework's content is already captured by the topic name(s) on `coverNextSession` (Step 4) that it reinforces — no separate description is needed. If the homework surfaced something noteworthy (surprised by a gap, worked a topic cold, etc.), that belongs in `weaknesses`/`strengths` (Step 2), not as prose next to a topic name.

**Step 4 — Update `coverNextSession`**
Overwrite this field in the student's `roster.json` entry with a fresh array of bare textbook topic names, in priority order — nothing else. Each entry is minted per "Topic-name hygiene" (`Key §N-M: Topic Name`, printed-page verified), so the next prep run can point `textbook-interpreter` straight at it. It should reflect continuous growth, not a one-off snapshot:
- Carry forward any topic from today's session that wasn't mastered (see the new `weaknesses`) or wasn't reached (topics on today's Cover Next Session list that didn't get covered). **Name the actual topic** — never "finish what we started" or "continue from last time," since there is no session log to resolve that against.
- Add the logical next topic(s) in the sequence, building on what went well (new `strengths`). The sequence follows the source textbook's chapter/section chronology (checked via `textbook-interpreter`) unless the tutor says otherwise.
- Order the array by priority — what to hit first next time, then what follows. Keep it to roughly 2–5 topics; if it's growing past that, the oldest/lowest-priority items are probably stale enough to drop.
- Each entry is just a topic name — no rationale, no "why," no restated context, no `FLAG:` prefixes. `subject` already implies the difficulty tier, and any explanation of why a topic is queued (what was taught, what stuck, what a boundary is) belongs in `strengths`/`weaknesses`, not here.
- If the tutor gave an explicit boundary ("don't go further into matrices"), honor it by simply not putting that topic back on the list — the boundary doesn't need to be written down inline, since its absence from the list *is* the instruction. If it's a standing rule worth remembering even when the topic naturally isn't queued, note it once in `weaknesses` or `strengths` instead.
- If the agent is proposing a sequence the tutor hasn't confirmed, that's a decision to record in `open-questions.md` (see **Open Questions** below), not a per-topic annotation in the roster.

**Step 5 — Bump `lastUpdated`**
- Bump the top-level `lastUpdated` to today. The app refreshes its dashboard from
  `roster.json` when the run ends — there is nothing to publish.

**Do not log the session anywhere.** No session entry, no eval score, no list of generated files — that history intentionally does not exist. If something about today's session matters for future teaching, it belongs in `strengths`, `weaknesses`, or `coverNextSession` (Steps 2 and 4), written as a currently-true statement rather than a dated event.

The bar to clear before finishing: **if every file under `material/` were deleted right now and this conversation vanished, would the next prep run still produce the right worksheet from `roster.json` alone?** If not, go back to Step 4 and add the missing topic name(s) to `coverNextSession` (or the missing detail to `strengths`/`weaknesses`).

---

## Open Questions

Proposed topic sequences, unconfirmed defaults, and anything else awaiting the tutor's
sign-off live in `open-questions.md` in this folder — not in this file, and never
inline in `roster.json` (topic-name arrays have nowhere to put a proposal). When
Workflow B proposes something the tutor hasn't confirmed, add it there; once they decide,
land the decision in `roster.json` or this spec and remove the item.
