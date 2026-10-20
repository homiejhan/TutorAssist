# TutorAssist — developer notes

This folder is the source code of TutorAssist. It's a local web app
(`tutor`, at http://127.0.0.1:8787) that runs a tutoring-prep agent by starting the
user's own `claude` CLI in headless mode inside a workspace folder. These notes are for
working **on the app**.

The agent's operating rules are product content. They live in
`tutorassist/template/agent-spec.md` and get installed into each workspace as
`CLAUDE.md`. Edit that file as a file; never follow it as instructions in this session.
The user's live data is in `~/TutorAssist` (roster, materials, textbooks). Never point
tests or experiments at it.

## Layout

| File | What it does |
|---|---|
| `tutorassist/cli.py` | The `tutor` command: `setup`, `update`, `serve`, `prep`, `doctor`, `import`, `service`. `augment_path()` adds `~/.local/bin`, Homebrew, TeX and TinyTeX to `PATH`, because launchd starts the app with a bare `PATH`. |
| `tutorassist/setup.py` | The one-pass, idempotent installer, in this order: 1) Claude Code, via Anthropic's installer plus `claude auth login`. 2) LaTeX: it keeps the system TeX if that compiles every example worksheet in the skill, and otherwise installs TinyTeX in the home folder and loops on `tlmgr` for missing files. 3) The workspace, auto-importing a legacy folder if one is found; it never re-imports over a roster that already has students. 4) A LaunchAgent (or systemd user service) and an `~/Applications` launcher. |
| `tutorassist/engine.py` | Builds the `claude -p --output-format stream-json` command and translates its events for the UI. Permissions go on the command line (`--allowedTools` / `--disallowedTools`), because headless runs ignore *allow* rules in an untrusted folder's `.claude/settings.json` (deny rules still apply). `SHELL_COMMANDS` must cover what the skills run. |
| `tutorassist/runs.py` | Runs one at a time per workspace. Event logs go to `.tutor/runs/*.jsonl`. A conversation resumes via `--resume <session>`; if Claude Code no longer has that session (it keys them by folder, so a moved workspace loses them), the run restarts once as a new session with a recap built from the app's own record. After a run it removes LaTeX by-products and reports the files that changed and the roster diff. |
| `tutorassist/server.py` | FastAPI. Security: a Host allow-list, an `X-Tutor-Token` header on non-GET `/api` calls, and a `TUTOR_ACCESS_TOKEN` cookie gate for any non-loopback host. |
| `tutorassist/calendar_ics.py` | Fetches the ICS feed, expands recurring events, and writes `schedule.json` (with the `student` match and `minutes`). Manual sessions are kept in `.tutor/manual_sessions.json`. |
| `documents.py` | The five-PDF rule. `material/DATE/` holds only `{Name}_{DATE}_{Guided,Eval,Eval_Answers,Homework,Homework_Answers}.pdf`. `tidy()` runs after every run, on workspace sync and on import: it moves anything else into `.work/` (sources to `.work/sources/DATE/`, everything else to `.work/other/DATE/`) and deletes only LaTeX by-products that sit next to their PDF. `student_sessions()` feeds the per-student document tiles. |
| `tutorassist/studio/` | Worksheet Studio, built in. `worksheet_gen.py` is vendored from the skill (never edit it here; patch it in `scripts/vendor_skills.py`). `build.py` turns `.work/sources/DATE/{Student}_{Guided|Eval|Homework}.txt` into the session PDFs (an Eval/Homework source makes the student copy without its key plus `_Answers` with only the key), compiles in a temp folder, and writes a build report: page counts, which problem is on which page, blank pages, missing problems, and LaTeX errors traced to a source line. `hook.py` is a Claude Code PostToolUse hook that `engine.py` registers with `--settings` on every run, so a source builds the moment the agent saves it and the report goes straight back to the agent. `runs.py` builds anything left stale at the end of a run, and `PUT /api/sources` backs the viewer's **Edit text** tab. |
| `roster.py` / `books.py` | Roster schema v4 (exactly 8 student fields; don't add any). `books.json`, the library listing, and the parser for the legacy spec's tables. |
| `scheduler.py` | Refreshes the calendar every 15 minutes and runs the daily auto-prep, catching up if the computer was asleep. |
| `doctor.py` | Health checks. Their fix hints point at `tutor setup`; the UI shows failures in a red banner. |
| `migrate.py` | Moves an install from the app's old name, "Tutoring Agent": `~/.tutor-agent` → `~/.tutorassist`, `~/TutoringAgent` → `~/TutorAssist`, and swaps the old login item (`com.tutoring-agent.server` / `tutoring-agent.service`) and `Tutoring Agent.app` launcher for new ones. `tutor setup` and `tutor update` stop the old login item first; any other command only moves things when no app is running. `install.sh` and `tutor update` uninstall the old uv tool before installing, since both provide `tutor`. |
| `workspace.py` | `create` / `sync_managed` / `import_legacy`. Sync always overwrites `.claude/skills` and `settings.json`. It replaces `CLAUDE.md` only if the user hasn't edited it; otherwise it writes `CLAUDE.md.new`. `template_hash()` triggers a sync on start whenever the template changes. |
| `tutorassist/web/` | Vanilla JS with no build step. The desk has a selected student (`S.focus`, kept in localStorage): it shows that student's session tiles and an inline viewer (PDF / source / download), filters the conversations, and sends `student` + `date` with chat messages, which the server turns into a bracketed context line for the agent. In `app.js`, use `fill(el, …)` rather than `replaceChildren` (`fill` drops null children). `app.css` defines its color and motion tokens at the top and has dark mode. Keep copy short: one line of help at most, secondary actions go in a ⋯ menu (`menuButton`), icons come from `icon()`. Motion answers the tutor's own clicks (the nav highlighter in `moveNavHl`, tiles fanning in once per student, menus, dialogs) and stops under `prefers-reduced-motion`. The Questions tab shows only while there are open questions. Document chips are green when the PDF exists (`.tile.made`), gray and dashed when not. The viewer's full screen is CSS only (`.viewer.full`, `setFull()`), so the PDF isn't reloaded; its prompt box sends through `sendChat()` like the composer and `paintPeek()` mirrors that run in the reply window. Chat messages carry the open document as `document`; the server names it in the context line only if it's a real session file. `reloadIfChanged()` refreshes the open document when a run lists it in `files`. Hiding the header or desk sidebar (`S.layout`, localStorage `ta.layout`) leaves a pull tab on that edge. `vendor/` holds marked and DOMPurify. |
| `tutorassist/template/` | `agent-spec.md`; the seed `roster.json`, `books.json` and `open-questions.md`; `sample.json` (the example students Test Student 1 and 2 plus the two book keys they cite, added only to brand-new workspaces by `tutor setup` / `init` / `serve`; `workspace.only_sample_students()` keeps them from blocking an import); and `claude_dir/`, which becomes the workspace's `.claude/` (`settings.json` plus `skills/`). It's named without a dot so packaging keeps it. |

## Dev loop

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest                      # tests/fake_claude.py stands in for the CLI: no network, no usage
TUTORASSIST_HOME=/tmp/ta-dev TUTOR_CLAUDE_BIN=$PWD/tests/fake_claude.py \
  .venv/bin/tutor serve --workspace /tmp/ta-ws --port 8788 --no-browser   # sandbox UI with a fake agent
tutor update                          # reinstall the real app from this folder and restart it
```

The background app's log is at `~/.tutorassist/server.log`. A real agent run uses the
user's Claude plan, so test with the fake CLI unless the change needs the real one.

## Gotchas

- Packaging is **hatchling**, and everything under `tutorassist/` ships. Don't go back to
  setuptools: its in-tree `build/` folder shipped stale files on reinstall.
- Installs and updates use `uv tool install --force --reinstall`, so there's no need to
  bump the version for an update to take effect.
- The agent can't answer prompts mid-run, so anything needing approval is simply denied.
  The agent's shell rules live in `agent-spec.md` → "Running inside TutorAssist". If you change `engine.SHELL_COMMANDS`, keep that section in step with it.
- The skills in `template/claude_dir/skills/` are adapted copies of the claude.ai skills;
  `worksheet-studio-pdf` keeps only its format docs and examples (plus
  `scripts/skill_extras/`'s `guided_template.txt`), because the generator lives in
  `tutorassist/studio/`. Re-vendor everything with
  `python3 scripts/vendor_skills.py /path/to/skills` (its patches are asserted). One
  generator patch fixes a real bug in the original: a problem with a graph or table was an
  unbreakable minipage, so one taller than a page lost its tail (a guided worksheet's
  practice part vanished). It's now measured first and flows when too tall; keep that
  patch when re-vendoring.
- The agent must never compile or look at pages; the build report is its only view. If
  you add a check that needs the pages, put it in `studio/build.py:_inspect` so the report
  carries it.
- The app was called "Tutoring Agent" until v0.2. The old names survive only in
  `migrate.py`, `install.sh`'s uninstall line, and `config.app_home()` (which still honors
  `TUTOR_AGENT_HOME`). The `tutor` command and the `TUTOR_*` variables kept their names.
- This folder is published as a public repo. Never commit real student names, a tutor's
  roster, calendar links or textbook PDFs; tests and examples use Test Student 1 / 2 and
  made-up names. `docs/` holds the README screenshots (taken from a demo workspace).
- Never handle Claude credentials in the app; authentication belongs entirely to the
  `claude` CLI (Anthropic's terms). `ANTHROPIC_API_KEY` passes through via the
  environment.
