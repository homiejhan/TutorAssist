---
name: "sat-math-module"
description: Write a complete, original, SAT-quality Math practice module — by default 27 questions in 35 minutes (~20 multiple choice + ~7 student-produced response), covering the four digital SAT domains (Algebra, Advanced Math, Problem-Solving & Data Analysis, Geometry & Trigonometry) in the official 35/35/15/15 mix, ordered easy→hard, with a verified answer key — as a Worksheet Studio .txt that the worksheet-studio-pdf skill compiles to a PDF, including [graph] scatterplots/lines and | pipe | tables. Use this whenever the user asks for an SAT math section, module, mock test, timed set, practice test, or "SAT-style" / "SAT-level" / "College Board-style" math questions, PSAT math practice, a diagnostic in SAT format, or more questions like the SAT — even if they only say "make me a math section", "another module", or "27 SAT questions". Also use it when a tutoring worksheet is requested for an SAT student. Not for non-SAT curricula (Algebra 1 CBE review, calculus, etc.) unless SAT style is requested.
---

> **Running inside TutorAssist.** This copy of the skill lives in
> `.claude/skills/sat-math-module/` inside the tutor's workspace, and the workspace is
> your working directory. Wherever the instructions below mention `/home/claude`,
> `/mnt/user-data/...`, `/mnt/skills/...` or a `present_files` step, use the
> workspace instead: write `.txt` sources to `.work/sources/YYYY-MM-DD/` exactly as
> `CLAUDE.md` says (the app turns them into the session PDFs in `material/`), put scratch
> work in `.work/scratch/`, run scripts as `python3 .claude/skills/<skill>/<script>`, and
> skip any copy-to-outputs or present step — the app links every file listed in your
> final report.
> sympy is preinstalled for the verification step. The app builds the PDF from the `.txt` as soon as you save it (no compiler to run).

# SAT Math module writer

Produces one timed SAT Math module as a Worksheet Studio `.txt` (the plain-text
format from https://github.com/homiejhan/pdfgenerator), then compiles it with
the `worksheet-studio-pdf` skill's `worksheet_gen.py`. Defaults: **27 questions,
35 minutes, 20 MC / 7 SPR, calculator permitted**. Accept overrides (question
count, minutes, a topic emphasis, "Module 2 hard" difficulty, no PDF) but keep
the SAT blueprint proportions unless told otherwise.

## Files in this skill

- `references/question_guide.md` — **read it fully before drafting.** Blueprint,
  College Board phrasing, every tested skill with stems and distractor recipes,
  number/SPR rules, contexts, difficulty ladder.
- `scripts/lint_module.py` — structural + blueprint linter for the `.txt`.
  Run it before compiling; it catches the parser traps below.
- `assets/example_module.txt` — a full 27-question module that compiles
  cleanly. Skim it for the exact shape of the file (title/subtitle, MC layout,
  SPR items, a scatterplot, a line graph, three table styles, tagged key).

## Workflow

1. **Plan the 27 slots first** (in scratch, not in the file): for each slot
   write `#, domain, skill code (A1…G4), MC/SPR, difficulty, visual?`. Enforce
   the blueprint from the guide: Algebra 9–10, Advanced Math 9–10, PSDA 4,
   Geo/Trig 4; SPRs at roughly 4, 8, 13, 17, 22, 25, 27; every skill hit at
   least once; no domain three times in a row; ≥1 table and ≥1 graph item;
   **at least 11 of 27 items set in a real-world context** (College Board
   runs about half — bare-equation items drift in unless you plan for it),
   spread across all four domains, not just PSDA. Lay the slots out with the
   **position archetype in guide §7** (warm-ups at 1–3, inequality/data at
   10–13, parameters from 20, one-insight SPRs to close) — and, for a second
   or later module, **rotate** SPR positions and slot-to-skill assignments as
   §7 prescribes, so Q23 isn't always trig and Q27 isn't always "find $c$".
2. **Decide each answer before its problem.** Pick clean numbers, then build
   the stem around them (guide §4). Write the four options with one specific
   misconception per distractor.
3. **Draft the `.txt`** as the session's source, `.work/sources/YYYY-MM-DD/<Name>_Homework.txt`
   (or `_Eval.txt`), following the format
   rules below. Write the key with a domain tag on every line.
4. **Verify every answer computationally** in a scratch Python script
   (sympy is available): solve each equation, recompute each table total,
   evaluate the fit line at the asked $x$, check that exactly one option
   satisfies each inequality item, re-derive every SPR. Fix anything that
   fails. This step is not optional — a wrong key is worse than no key.
5. **Lint:** `python3 <skill>/scripts/lint_module.py <slug>.txt`
   (add `--questions N --mc M --spr S` for non-default sizes). Fix all
   errors; read the warnings. If earlier modules exist (this conversation,
   memory, or files the user points to), pass them with
   `--previous a.txt b.txt`: the linter reports slots where the new module
   repeats an earlier module's skill or reuses its SPR positions, so the set
   doesn't become a fixed layout students can memorize.
6. **Build:** the app builds the PDFs every time you save the `.txt` and replies
   with a build report: pages, which questions sit on which page, and any error
   with the line to fix. Put a `[settings spacing: compact]` line under the title
   to keep a 27-item module near 8–10 pages. Fix the `.txt`, never anything else.
7. **Check the build report** for split option lists or a graph/table that broke
   across pages, and adjust the spacing settings if needed. Don't render pages.
8. **Deliver:** list the PDFs in your final report (the app shows the `.txt`
   source next to them).

If the user asks for the text only (no PDF), stop after step 5 and present
the `.txt`.

## Format rules for the `.txt` (the ones that actually break builds)

The full spec is in the worksheet-studio-pdf SKILL.md; these are the traps
an SAT module hits:

- **Line 1 = title** (e.g. `SAT Math Practice — Module A`). **Line 2 =
  subtitle**, and it is the only place for directions, because the parser
  drops any other text before problem 1. Use:
  `27 Questions · 35 Minutes · Calculator permitted · Questions without answer choices are student-produced responses`
- **Problem markers:** `Problem 1.` … `Problem 27.` — one style, sequential.
- **Never start a line inside a problem with a number followed by `.` `)`
  or `:`** — `3.5 kilograms` on its own line becomes "Problem 3". Keep
  numbers mid-line or in math (`$3.5$`).
- **Multiple choice:** after the stem, a blank line, then `A) …`, blank,
  `B) …`, blank, `C) …`, blank, `D) …`. Four options, always. Options are
  plain paragraphs; there is no MC syntax.
- **SPR items** are just a stem with no options (the subtitle explains
  this). Do not write "Enter your answer" — the real test doesn't.
- **Currency is `\$`**, never a bare `$`, in stems, options, tables, and
  the key. Every `$…$` closes on the same line. `%`, `&`, `#` in prose are
  auto-escaped — write them plainly; inside math use `\%`.
- **Tables:** consecutive lines starting with `|`; a `|:---:|` row makes a
  navy header. Two-way tables use an empty first header cell: `| | Yes | No | Total |`.
  Horizontal tables of values omit the dash row. A blank line ends a table.
- **Graphs:** `[graph]` … `[/graph]` on their own lines, inside the problem.
  Supported: functions `y = …`, inequalities, `points (…)(…)` scatter,
  `line (x1,y1) (x2,y2)`, `point A (x,y)`, `segment`, `polygon`, `circle`,
  `text (x,y) …`, window `x: 0 to 20`, `xstep:/ystep:`, `xlabel:/ylabel:`,
  `size: small|medium|large`, `title: A)` for side-by-side option graphs
  (three `small` per row). **Not supported:** bar graphs, histograms, box
  plots, dot plots, pie charts, figures without axes — use a frequency table
  or a `points` scatterplot instead, and describe geometric figures in words
  (the real test does this constantly: "Triangle $ABC$ is similar to…").
  Coordinate-plane geometry (a circle from its equation, a polygon and its
  image) can be drawn with `grid: off` / `numbers: off` if the axes are
  part of the problem.
- Scatterplot recipe: `x: 0 to 20`, `y: 0 to 70`, `xstep: 2`, `ystep: 10`,
  real axis labels, `size: large`, 8–12 `points`, then the fit line with a
  domain: `y = 2.5x + 10 for 0 <= x <= 20 [navy]`.
- **Answer key:** a line `Answer Key`, then one line per problem:
  `12. B — [PSDA: Two-variable data: models and scatterplots] one-line reason.`
  For SPRs the key starts with the number in math if negative (`8. $-5$ — …`)
  and offers every accepted form (`22. 9/17 — … ≈ .5294 (either form)`).
  Domain tags must be one of `Algebra`, `Advanced Math`, `PSDA`,
  `Geometry/Trig` followed by a colon and the skill name; the linter counts
  them against the blueprint. Keep reasons to one sentence, and name the
  misconception behind a tempting distractor when it's instructive.
- No sub-parts `(a)`, `(b)` — the SAT has none. No star/challenge markers.

## Quality bar (what "SAT quality" means here)

- Original items only. Never copy or lightly edit a released College Board,
  Khan Academy, or Bluebook question; borrow the *style*, not the item.
- Each distractor comes from one identifiable error; no throwaway options.
- Stems are the shortest complete statement of the situation; the question
  is the last sentence.
- Exactly one correct option per MC item — verify by testing all four.
- Hard ≠ long. Late items should hinge on an idea (discriminant, ratio of
  slopes, conditional probability, Vieta, completing the square).
- Answers fit the SPR entry rules (guide §4). Answers in the key match the
  form a student would enter.
- Calculator-neutral: nothing that becomes trivial only with a calculator,
  nothing that needs one for brute arithmetic.

## Variants

- **Two full modules / a full section:** produce two files (Module 1 and
  Module 2), 22 questions and 35 minutes each if the user wants official
  pacing, or 27 each if they want this skill's default. Make Module 2's
  hard third harder (upper-adaptive style) if asked for "hard module".
- **Topic-weighted set** (e.g. "mostly Advanced Math"): keep the format and
  ordering rules, change the domain counts, still tag every key line.
- **PSAT/NMSQT or PSAT 8/9:** same blueprint, drop the hardest ideas
  (no discriminant-with-parameter, no radians, simpler trig).
- **Answer key only / explanations expanded:** the key line can grow to
  2–3 sentences with the distractor rationale, still one entry per number.
