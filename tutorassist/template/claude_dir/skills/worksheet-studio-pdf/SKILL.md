---
name: "worksheet-studio-pdf"
description: Use whenever the user asks for a tutoring worksheet, problem set, diagnostic exam, quiz, or answer key as a finished PDF — including ones that need coordinate-plane graphs (lines, parabolas, inequalities, piecewise functions, blank grids, points/shapes, vectors), to-scale geometry figures (triangles from SSS/SAS/ASA, parallelograms, trapezoids, regular polygons, circles, arcs, sectors, lines, rays, vectors — with side/angle labels, congruence tick marks, angle arcs, right-angle marks, parallel arrows, dashed lines), tables (tables of values, fill-in tables, data tables), compact layouts for drill problems (4x4 grids, 2-4 columns), or guided lessons (key-idea boxes, numbered steps, adjustable spacing). Generates plain-text content in Worksheet Studio's format and compiles it directly to a typeset PDF via worksheet_gen.py + pdflatex, skipping the manual copy-paste into https://homiejhan.github.io/pdfgenerator/. Also use if the user just wants the plain-text output for pasting into the website themselves.
---

> **Running inside TutorAssist.** This copy of the skill lives in
> `.claude/skills/worksheet-studio-pdf/` inside the tutor's workspace, and the workspace is
> your working directory. Wherever the instructions below mention `/home/claude`,
> `/mnt/user-data/...`, `/mnt/skills/...` or a `present_files` step, use the
> workspace instead: write `.txt` sources to `.work/sources/YYYY-MM-DD/` exactly as
> `CLAUDE.md` says (the app turns them into the session PDFs in `material/`), put scratch
> work in `.work/scratch/`, run scripts as `python3 .claude/skills/<skill>/<script>`, and
> skip any copy-to-outputs or present step — the app links every file listed in your
> final report.
> **The PDF generator is built into the app.** You only write the `.txt`; saving it builds the PDFs and gets you a build report (see *Workflow* below). There is no script to run here.

# Worksheet Studio — LaTeX backend skill

Source of truth for the text format: https://github.com/homiejhan/pdfgenerator
(README "Input format specification"). `worksheet_gen.py` in this skill is a
line-for-line port of that repo's `js/app.js` Model parser, re-targeted at a
real LaTeX toolchain (`pdflatex`) instead of the site's
KaTeX/html2canvas/jsPDF pipeline. Anything that parses correctly on the
website parses identically here — and since KaTeX's supported math subset is
itself a subset of real LaTeX, `$...$` / `$$...$$` spans pass through to
`pdflatex` almost verbatim, so output is vector text (searchable/selectable),
not a rasterized image like the website's export.

> **One intentional divergence from the website parser.** `**bold**` and
> `*italic*` spans that *wrap inline math* (e.g. `**Step 1: solve $x$.**`)
> now render correctly. The website applies markdown to each text fragment
> *after* splitting math out, so a `**…**` pair straddling a `$…$` span never
> matches and the literal asterisks leak into the output. `inline_format`
> here swaps math for placeholder tokens first, applies markdown to the whole
> line, then restores the math — so emphasis around math works. This is the
> only place output can differ from the site, and it differs in the direction
> you want (bold that would break on the website works here).

> **LaTeX-only extensions: graphs, figures, tables, compact layouts,
> formatting and spacing.** `[graph] ... [/graph]` and `[figure] ... [/figure]`
> blocks, `| pipe |` tables, `[grid N]` / `[columns N]` layouts, and the
> formatting/spacing syntax (`(1)` steps, `-` bullets, `[box]`, `>` callouts,
> `##` headings, `[space]`, `[lines N]`, `[newpage]`, `[settings]`, ...; see
> the sections below) render only through this script. The website does not know them and would
> show the raw lines as text, so a worksheet that uses them can't be
> round-tripped through the site.

## Workflow (inside TutorAssist)

TutorAssist has this skill's PDF generator built in, so the only thing you produce is
plain text:

1. Write the worksheet in the format below, with the Write tool, to
   `.work/sources/YYYY-MM-DD/{Student}_{Type}.txt`, where Type is `Guided`, `Eval` or
   `Homework` (CLAUDE.md says which documents a session gets).
2. The moment the file is saved, the app builds it into `material/YYYY-MM-DD/` and
   answers with a build report: the PDFs it made, the page count, which problem sits on
   which page, and anything wrong (a format error names the bad line; a LaTeX error
   quotes the text it choked on). Fix the `.txt` with Edit and it rebuilds. Never run a
   compiler, never render pages to images and never open the PDFs: the build report is
   your view of the pages.
3. An Eval or Homework source holds the questions *and* their `Answer Key` section. The
   app makes two PDFs from it: the student copy with no key, and `{Type}_Answers` with
   only the key. A Guided worksheet keeps its key at the end of the same PDF.
4. Always verify every answer computationally before writing the Answer Key.
   For graphs, also check that what's drawn matches what's asked: compute
   intercepts/intersections/vertices, and pick an `x:`/`y:` window that
   actually shows them.
   For figures, check that every label you *write* matches the drawn
   geometry (a side labeled 5 should be 5 long in the construction) — build
   shapes from the same measurements the problem states, or use `measure`
   to have the figure print the computed value.
5. Only when a source has `[figure]` blocks, the build report also lists preview images
   of the pages. Read those to check that labels are legible and nothing collides.

## Format spec (exhaustive — matches the parser exactly)

- **Line 1 must be the title** — even if it looks like a problem marker, the
  first non-blank line is *always* consumed as the title. **Line 2** (if
  present before any problem marker) becomes the subtitle. Never start a
  worksheet directly with `Problem 1.` — put a title line first, or problem 1
  silently becomes the title and numbering starts at 2.
- **Problem markers**, start of line, case-insensitive: `Problem 1.` `Problem 1)`
  `Problem 1:` `Question 1.` `Q1.` `#1.` `1.` `1)` `1:`. Pick ONE style per
  worksheet and use it consistently. Numbers sequential from 1.
- **Star/challenge**: leading `*` or `★` before the marker, e.g. `*Problem 5.`
  Renders as a gold star next to the problem number.
- **Sub-parts**: `(a)`, `(b)` ... `(h)`, parens required, `.` or `)` + space
  after the letter. Only for genuine sub-questions of the preceding problem.
- **Multiple choice**: plain paragraphs separated by blank lines — NOT a
  special syntax. Never put all four options on one line.
- **Answer Key**: a line reading `Answer Key`, `Answers`, or `Solutions`
  (optional trailing `:`) switches into answer mode; same numbering rules
  apply. Numbers must match problems above but don't need to be in order
  (they're sorted). Optional section — omit entirely if no key.
- **Math**: `$...$` inline, `$$...$$` displayed on its own line (renders as
  a centered `\[...\]` block when it's the only content on that line).
  KaTeX's LaTeX subset passes through to real LaTeX almost verbatim:
  `\frac{a}{b}`, `\sqrt{x}`, `x^2`, `x_1`, `\pi \theta`, `\le \ge \ne`,
  `\times \cdot`, `\in`, `\{ \}`, etc.
- **Currency**: `\$` ALWAYS for a literal dollar sign, NEVER a bare `$`
  outside math — this is the #1 source of broken output (a bare `$` desyncs
  math-span pairing for the rest of the line). Conveniently `\$` is also
  *already* the correct native LaTeX escape, so no translation happens —
  what parses on the website compiles here unchanged.
- **Light formatting**: `**bold**`, `*italic*` (single asterisk, don't
  nest). A bold/italic span **may enclose inline math** — e.g.
  `**Step 2: Compute $b^2$.**` renders the whole label bold with the math
  intact. Plain-text special characters (`& % # _ { } ~ ^`) outside math
  spans are auto-escaped for LaTeX — write them normally (e.g. `50%`,
  `x_1` as prose, `Smith & Sons`) and don't hand-escape them yourself.
- Every `$...$` / `$$...$$` span must close on the SAME line. A line that
  is only `$$...$$` is centered on its own line even when it sits inside a
  paragraph.
- `"straight quotes"` around a word or phrase become curly quotes; an inch
  mark (`5"`) is left alone.

## Formatting: titles, steps, bullets, boxes, line breaks

Use these to give lessons and worked examples real structure instead of
walls of text. All are LaTeX-only (the website shows the raw lines).

- **Problem title** — if a problem's first line is *only* bold text
  (`Problem 1. **§4.1 Common Factors**`), it becomes a larger navy title on
  the header line ("Problem 1. §4.1 Common Factors") with a thin rule under
  it, and the header is kept off the bottom of a page.
- **Numbered steps** — lines starting `(1)`, `(2)`, ... become a step list
  with navy number badges (hanging indent, one step per line). Lines
  starting `Step 1.` / `Step 1:` become a list labeled **Step 1.** in navy.
  (Never use `1.` / `1)` for steps — those start a new problem.)
- **Bullets** — lines starting `- ` (or `• `) become a bullet list. Good for
  the lines of a worked example: `- **Group:** $(x^3+4x^2)+(3x+12)$`.
- **Continuation lines** — a line indented 2+ spaces (or a tab) directly
  under a step, bullet, or (a) sub-part continues that item *on a new line*;
  a `$$...$$` continuation line is centered under the item. Works in the
  answer key too (`1. (a) ...` then an indented `$$...$$` line).
- **Callout boxes** — `[box]` ... `[/box]` on their own lines; anything can
  go inside (paragraphs, steps, bullets, math, tables, graphs).
  `[box: Key idea]` adds a bold title; a style word before the colon picks
  the color: `[box gold: Sign rule]`, `[box red: Common mistake]`,
  `[box green: Check]`, `[box gray]`, `[box outline: Note]` (navy is the
  default; aliases: key/note = navy, tip = gold, warning = red).
  `[box Key idea]` without a colon also works when the words aren't a style.
  Lines inside a box that look like `1.` or `Answers` stay box text.
- **Quick callout** — consecutive lines starting `> ` become one untitled
  navy box: `> **Vocabulary.** The *coefficient* is ...`.
- **Sub-headings** — `## Watch the sign` (navy bold) or `### Smaller`.
- **Labeled paragraphs** — a paragraph that opens with a bold run-in label
  (`**Example A.** Factor ...`, `**Now you try.**`) automatically gets extra
  space above it and is kept with the lines that follow it.
- **Line breaks** — end a line with ` \\` to force a new line inside the
  same paragraph (no paragraph gap). A blank line still starts a new
  paragraph.
- **Divider** — a line of `---` (or `[rule]`) draws a light horizontal rule.

## Spacing controls

- **`--density compact | normal | relaxed | spacious`** (default normal) —
  scales line spacing (1.0 / 1.0 / 1.1 / 1.2), paragraph gaps, the extra gap
  above labeled paragraphs and headings, list-item gaps, gaps around boxes,
  and the gap between problems. `normal` reproduces the previous output
  apart from the new features. Use `compact` to fit a long worksheet on
  fewer pages, `relaxed` / `spacious` for younger students or lesson notes.
- **`--spacing`** — workspace under each problem (none 0 · compact 0.4in ·
  standard 0.9in · large 1.6in · xlarge 2.4in).
- **`--part-spacing`** — workspace under every (a)/(b) sub-part (same
  values; default none). The last sub-part's space counts toward the
  problem's workspace (you get the larger of the two, not both).
- **`[settings density: relaxed, spacing: compact, part-spacing: standard]`**
  — a line right after the title/subtitle that stores these in the file
  (lengths like `1.2in` are allowed). Command-line flags override it.
- **`[workspace large]`** (or `none`, `1.5in`, ...) anywhere inside a
  problem overrides `--spacing` for that one problem.
- **`[space]`** on its own line inserts vertical space at that point:
  `[space]` = standard (0.9in), `[space compact|large|xlarge]`,
  `[space 0.5in]`, `[space 2cm]`, `[space 12pt]`, or a negative length
  (`[space -6pt]`) to pull things closer. Directly under a sub-part it
  replaces that part's `--part-spacing`.
- **`[lines 4]`** — 4 ruled writing lines (for written explanations); also
  replaces the part spacing of a sub-part right above it.
- **`[newpage]`** — start a new page. Put it between problems (as the last
  line of a problem it goes after that problem's workspace). Ignored inside
  boxes and grid/columns cells.

## Guided notes / lesson worksheets (recommended pattern)

For "guided worksheet", "lesson", "notes + practice" requests, structure
each section like this (see `example_guided.txt`) instead of writing long
paragraphs with inline "(1) ... (2) ..." steps:

```
[settings density: normal, spacing: compact, part-spacing: standard]

Problem 1. **§4.2 Factoring by Grouping**

[box: Key idea]
One or two sentences of explanation. Then:
(1) First step on its own line.
(2) Second step on its own line.
[/box]

[box gold: Sign rule]
The one rule students trip on.
[/box]

**Example A.** Factor $x^3 + 4x^2 + 3x + 12$.
- **Group:** $(x^3 + 4x^2) + (3x + 12)$
- **Factor each pair:** $x^2(x+4) + 3(x+4)$
- **Check:**
  $$(x+4)(x^2+3) = x^3 + 4x^2 + 3x + 12$$

**Now you try.**
(a) Factor $y^3 + 5y^2 + 2y + 10$.
(b) Factor $3a^3 - 9a^2 - 2a + 6$.
```

- One idea per line; definitions and rules in boxes; each worked-example
  step as a bullet (or `Step N.` line) with the bold label first.
- Practice sub-parts on consecutive lines with `part-spacing: standard`
  (or `large` for multi-step problems) so every part gets work room.
- Read the build report; if a section spills a few lines onto a new page,
  try `density: compact` or `spacing: none` before cutting content.
- In TutorAssist the house format for guided worksheets is
  `guided_template.txt` in this folder: follow its structure.

## Graphs (`[graph] ... [/graph]`)

A block inside a problem, a sub-part, or an answer-key entry. The `[graph]`
and `[/graph]` tags go on their own lines. Inside: optional settings, then one
item per line, in any order. An empty block is a blank grid for students.

```
[graph]
x: -5 to 5
y: -5 to 5
y = 2x - 3 [label: $f$]
g(x) = -x^2 + 4 [red, dashed]
point A (1, -1)
[/graph]
```

**Settings** (`key: value`, all optional):
- `x: -5 to 5`, `y: 0 to 350` — window. Default `-10 to 10` on both. Also
  accepts `-5..5`, `[0, 10]`, `-2pi to 2pi`.
- `step: 1`, `xstep: 2`, `ystep: 50` — grid spacing. Auto by default: one
  line per unit for integer windows 4–24 wide, π-fractions when the bounds
  are π multiples, otherwise a round number. Tick numbers are thinned only as
  much as needed to avoid overlap.
- `grid: off`, `numbers: off` — hide grid lines / tick numbers.
- `xlabel: Hours worked`, `ylabel: Cost (\$)` — default `$x$` / `$y$`;
  `none` hides. Short labels sit at the arrow tips; long labels on a
  first-quadrant graph sit centered along the axes.
- `size: small | medium | large` (1.7 / 3.0 / 4.2 in wide) or e.g. `3.5in`.
  Default medium. Height keeps equal units when the window is roughly square.
- `title: A)` — bold navy caption above the graph.

**Items** (one per line):
- `y = 2x - 3` or `f(x) = x^2 - 4` — plot a function of x.
- `y > -1/2 x + 1` (also `<`, `<=`, `>=`, `≤`, `≥`, `\le`, `\ge`) — shaded
  inequality; `<`/`>` draw a dashed boundary, `<=`/`>=` a solid one.
- `... for -3 <= x < 1` — restrict the domain. Endpoint dots are automatic:
  closed for `<=`, open for `<`. Consecutive restricted pieces share one color
  (= one piecewise function); where a closed and an open dot coincide, the
  closed one shows. One-sided works too: `for x >= 0`.
- `x = 2`, `x < -1` — vertical line / shaded half-plane.
- `point (1, 3)`, `point A (1, 3)` (labeled *A*), `open point (2, 1)`,
  `points (0, 1) (1, 3) (2, 5)` (scatter, no labels).
- `segment P(0, 0) to Q(3, 4)` — endpoint dots, names labeled.
- `line (0, 1) (2, 5)` — infinite line through two points.
- `polygon A(1, 1) B(4, 1) C(4, 3)` — lightly filled; named vertices labeled
  outward (good for transformations: draw ABC and A'B'C' in two colors).
- `circle (0, 0) radius 3`
- `text (4, 5) Region $R$` — free text at a point.

**Options**: a bracket at the END of an item line —
`[red, dashed, label: $f(x)$]`. Colors: `navy blue red green orange purple
gold gray black`; styles: `dashed dotted solid`, `thick thin`; `noshade`
(inequality/polygon without fill); `label: ...` (text or `$math$`, placed
automatically clear of dots and axes). Without a color, curves/lines/shapes
cycle navy → red → green → orange → purple; points and text are black.

**Expressions** (right side of `y =`, and any number in a coordinate,
window, or step): implicit multiplication (`2x`, `3(x+1)`, `(x+1)(x-2)`),
`^` powers, `|x - 2|` absolute value, `sqrt() cbrt() abs() sin() cos() tan()
sec() csc() cot() arcsin() arccos() arctan() ln() log()` (log = base 10)
`exp() floor() ceil()`, constants `pi` and `e`, and LaTeX forms `\frac{1}{2}x`,
`\sqrt{x}`, `\sqrt[3]{x}`, `\cdot`, `\pi`. Functions need parentheses unless
the argument is a single number or `x` (`sin x` is fine). The variable must be
`x`. Undefined points (`sqrt` of a negative, division by zero) leave gaps;
asymptotes and jumps are detected, so `1/(x-1)` and `tan x` draw correctly.
**Pitfall:** `x^(1/3)` is undefined for negative x — use `cbrt(x)` or
`\sqrt[3]{x}` for a cube root.

**Layout**: graph blocks separated only by blank lines sit side by side and
wrap. Three `size: small` graphs fit per row — the pattern for "Which graph
shows ...?" multiple choice (`title: A)`, `title: B)`, ...). A problem that
contains a graph or table is kept on one page as a unit.

Graph items also include `vector (0, 0) to (3, 2)` / `vector A(1, 4) to B(4, 5)` —
an arrow from the first point to the second (names labeled, no dots). For
geometry marks (ticks, angle arcs) use a `[figure]`, with `grid: on` if the
picture needs graph paper.

## Geometry figures (`[figure] ... [/figure]`)

A to-scale geometry diagram with no axes — the tool for geometry problems.
Same placement rules as `[graph]` (inside a problem, sub-part, or answer;
tags on their own lines; consecutive `[graph]`/`[figure]` blocks separated
only by blank lines sit side by side). Every point is computed exactly in a
coordinate frame, then the whole figure is scaled uniformly to fit `size`,
so angles and proportions are true. Annotations (ticks, arcs, labels) have
fixed paper sizes, so they look the same at any scale. Lines are read top
to bottom; a point must exist before a later line uses it.

```
[figure]
triangle ABC AB=8 angle A=50 angle B=60
side AB 8 cm
side BC $x$ [ticks: 1]
angle A 50°
angle C measure
[/figure]
```

**Settings** (`key: value`): `size: small | medium | large` (1.6 / 2.5 /
3.5 in for the larger of width/height) or `3in`; `title: A)`; `labels: off`
(hide all vertex names); `grid: on` or `grid: 2` (light graph-paper grid,
step in figure units — for counting squares or vectors on a grid);
`rotate: 20` (turn the whole figure counterclockwise, degrees).

**Point names** are a capital letter plus optional digits/primes: `A`, `B'`,
`P1` (renders $P_1$). Names can run together: `ABC` = A, B, C. Lowercase
letters are never point names. `A(2, 3)` or `A (2, 3)` gives coordinates.
A name has one position for the whole figure: shapes may share a point,
but giving an existing name a different position is an error (use a new
name, e.g. `A'`).

**Shapes from coordinates**
- `triangle A(0,0) B(6,0) C(2,4)`, `polygon A(0,0) B(4,0) C(5,3) D(1,3)`
  (also `quadrilateral`, `pentagon`, `hexagon`), or `polygon ABCD` with
  already-defined points. Concave polygons are fine.

**Shapes from measurements** — first vertex at the origin (or `at (x, y)` /
`at P`), first side along the +x axis, the rest counterclockwise above it:
- `triangle ABC AB=5 BC=6 CA=7` (SSS) · `triangle ABC AB=5 AC=7 angle A=40`
  (SAS) · `triangle ABC AB=8 angle A=30 angle B=70` (ASA/AAS) ·
  `triangle ABC sides 5 6 7` (= AB, BC, CA). Any determining set works; the
  solver uses the laws of sines/cosines and errors on impossible input
  (triangle inequality, angles summing to 180° or more). SSA takes the
  acute solution. Angles may be written `angle A=40`, `∠A=40`, `m∠A = 40°`.
- `right triangle ABC legs 3 4` — right angle at the middle letter (B):
  AB = 3 along the base, BC = 4 straight up.
- `rectangle ABCD width 6 height 4` (or `6 by 4`) · `square ABCD side 4`
- `parallelogram ABCD base 7 side 4 angle 65` (angle at A; `height 3` may
  replace `side`) · `rhombus ABCD side 5 angle 60`
- `trapezoid ABCD bottom 10 top 6 height 4` — isosceles by default;
  `offset 0` makes a right trapezoid (offset = how far D sits right of A).
- `regular ABCDEF side 3` (or `radius 2`) — n = number of vertex names.
- **Building on an existing side:** if the first two vertices already
  exist, the shape is built on that segment (length and direction taken
  from it): `triangle ABC AB=4 BC=3 CA=4.5` then `square BAED` puts a
  square on side AB, on the side away from C (vertex order sets the side;
  `[flip]` switches it). A stated length that disagrees with the existing
  segment is an error.
- Per-shape keywords: `at (x, y)` / `at P` (anchor the first vertex),
  `rotate 30` (turn this shape about its first vertex).

**Points and constructions** (drawn as a dot + name):
- `point A (2, 3)` · `A = (2, 3)` · `points A(0,0) B(4,1)`
- `point M midpoint A B` · `point D foot C A B` (foot of the perpendicular
  from C to line AB — altitudes) · `point P from A 5 at 30` (5 units from A
  in the 30° direction) · `point P on A B 0.25` (a quarter of the way from
  A to B) · `point X intersect A B C D` (line AB meets line CD)
- `[hidden]` defines a point without drawing it (a construction helper);
  `[nolabel]` draws the dot without the name; `[label: $A(2,3)$]` replaces
  the name text.

**Lines and curves**
- `segment A B` (endpoint dots, except at shape corners) · `line A B`
  (arrows both ways, extends past A and B) · `ray A B` (from A through B) ·
  `vector A B` or `vector (0, 0) to (3, 2)` (arrow tip at the second point).
  `[label: $\vec u$]` writes a name beside the midpoint.
- `circle O radius 3` (or `diameter 6`, or `circle O through A`; O is put at
  the origin if it doesn't exist yet) · `arc O radius 3 from 0 to 120`
  (degrees, counterclockwise) or `arc O from A to B` · `sector O radius 4
  from 30 to 100` (lightly filled pie slice). A named center gets a dot and
  label (`[nocenter]` skips them).
- `text (2, 3) Region $R$` — free text centered at a point.

**Side annotations** — `side AB <label> [options]`:
- The label is anything after the names: `side AB 5`, `side AB 8 cm`,
  `side AB $x + 2$`, or nothing (`side AB [ticks: 1]`). `side AB measure`
  prints the true computed length (`5`, `4.5`, `$\sqrt{34}$`, `$3\sqrt{2}$`).
- Labels go outside the shape automatically (true outward side, concave
  shapes included); `[flip]` puts a label on the other side.
- `[ticks: 1|2|3]` congruence tick marks · `[parallel: 1|2]` parallel
  arrows (combine freely).
- If AB isn't drawn yet, `side` draws it.

**Angle annotations** — `angle ABC <label> [options]` (vertex in the
middle) or `angle B <label>` when B is a corner of a shape (uses that
shape's interior angle, reflex included at a concave corner):
- `angle ABC 40°` · `angle B $x^\circ$` · `angle C $(2y+5)^\circ$` ·
  `angle C measure` (prints the true angle, e.g. 70°) · `angle B` alone
  draws an unlabeled arc.
- `right angle ABC` (or `angle ABC right`) draws the square corner mark.
- `[arcs: 2|3]` congruent-angle arcs (`arcs: 0` or `noarc` = label only) ·
  `[reflex]` marks the outside (> 180°) angle.
- The degree sign `°` can be typed directly, in text or inside `$...$`.

**Options** (bracket at the end of a line): the `[graph]` colors (default
stroke is black); `dashed`, `dotted`, `thick`, `thin`; `fill` (light tint
inside a polygon/circle); `flip` (a measured shape: mirror it below its
base; a side label: other side); `lengths` / `angles` on a shape (label
every side / interior angle with its computed value — for answer keys);
`nodots`; `hidden`; `nolabel`; `label: ...`.

**Guidelines**
- Build shapes from the problem's actual numbers so the picture is honest;
  if a figure is deliberately "not drawn to scale", say so in the problem.
- Don't let a figure give away the answer: label the unknown (`$x$`) in the
  question; `measure` / `lengths` / `angles` belong in the answer key.
- Inside `[columns]` or `[grid]` cells, use `size: small`.

## Compact layouts (`[grid N]` and `[columns N]`)

For short problems (solve an equation, evaluate, simplify) that would waste
space as full-width "Problem N." blocks. Put a layout tag on its own line
before the problems and a closing tag after them. Problems inside use the
normal markers (`1.`, `Problem 1.`, `*5.`, …) but render with a compact
inline number ("**1.** $2x+3=11$") instead of a "Problem 1." header line,
and inline math is display-size there, so `\frac` fractions are full size.

```
[grid 4]
1. $x + 7 = 12$
2. $3x = 21$
...
16. $0.5x + 1.5 = 4$
[/grid]

[columns 2]
17. Solve for $y$: $2x + 3y = 12$.
18. Solve and check: $5(x - 1) = 3x + 7$.
[/columns]
```

- `[grid N]` (N = 1–4) — a boxed table read left to right, N cells per row;
  16 problems in `[grid 4]` make a 4×4 sheet. A row never splits across a
  page, but the grid can break between rows. Options after the number:
  `plain` (no borders), `space: none|compact|standard|large` (workspace in
  each cell; default = the `--spacing` flag). Example: `[grid 4, space: compact]`.
- `[columns N]` (N = 1–4) — problems flow top-to-bottom down N balanced
  newspaper columns with a light rule between them; each problem stays in
  one piece. Options: `norule`, `space: ...`. Good for short problems of
  mixed length, sub-parts, and small figures.
- More than 4 columns is an error. `[columns 1]` returns to normal layout.
- The closing tag is optional: a layout runs until `[/grid]` /
  `[/columns]`, the next layout tag, or the Answer Key. A tag right after
  the title/subtitle lays out the whole worksheet that way.
- Mix freely: a grid of drill problems, then normal "Problem N." word
  problems, then a `[columns 2]` section.
- **Answer key:** answers to problems that sat in a grid/columns block are
  packed N across in the key automatically. Answers can also be wrapped in
  their own `[grid N]` / `[columns N]` block.

## Tables (`| pipe |` rows)

Consecutive lines starting with `|` form a table (the leading `|` is
required; a blank line ends the table):

```
| Months ($m$) | Cost ($C$) |
|:------------:|-----------:|
| 1            | \$40       |
| 3            |            |
```

- A second row of dashes makes row 1 a shaded navy header and sets
  alignment per column: `:---` left, `:---:` center, `---:` right (default
  center). Without a dash row there's no header — use that for horizontal
  tables of values like `| $x$ | $-1$ | $0$ | $1$ |` / `| $y$ | | | |`.
- Cells take everything a normal line does: `$math$`, `**bold**`, `\$`.
  A pipe inside math is fine (`$|x|$`); a literal pipe in text is `\|`.
- Empty body cells are widened and heightened as student write-in space.
- Long text columns wrap automatically to the page width.
- Tables work in problems, between sub-parts (lettering continues), and in
  the answer key — an answer line can start with one: `3. | $x$ | $1$ |`.
- Small tables, graphs, and figures shrink automatically to fit a narrow
  column or grid cell (never enlarged).

## Style conventions (from established tutoring materials)

- Navy (`#19468C`) bold headers and boxed answer key, gold (`#B8892B`) star
  for challenge problems, US Letter, Palatino-family text (`mathpazo`) with
  `microtype` (`protrusion=true,expansion=false`).
- Running header on every page: title on the left, page number on the right,
  thin rule underneath — matches the website's per-page header.
- Each problem is kept together on one page when it fits (a problem taller
  than a page flows onto the next one instead of being cut off); the answer key box uses `tcolorbox`'s `breakable` option so it
  flows cleanly across pages instead of the website's per-page-box logic.
- `pdflatex` is run twice per file (stable layout).
- Original problems only — never copied from College Board, MySAT, or
  textbook sources. Base difficulty/style on them, don't reproduce them.
- All answers computationally verified before delivery.

## Files
- The generator is built into TutorAssist; there is no script in this folder.
- `guided_template.txt` — TutorAssist's house format for a guided worksheet:
  per topic a titled problem with a key-idea box (and a gold rule box when a rule
  trips students up), worked examples as bold-labeled bullets with a check, a
  **Now you try.** block of (a)/(b)/(c) parts, and a worked Answer Key per part.
  Read it before writing any guided worksheet.
- `example_input.txt` — a minimal worked example (title, MCQ, sub-parts,
  a starred problem, currency, and an answer key).
- `example_graphs_tables.txt` — graphs and tables in context: a fill-in
  table of values with a blank grid, a row of three multiple-choice
  inequality graphs, a piecewise function with sub-parts, a word-problem
  table, and an answer key containing a graph. Read it before writing a
  worksheet that needs either feature.
- `example_guided.txt` — a guided-notes lesson: titled problems, a key-idea
  box with `(1)` steps, a `>` vocabulary callout, a `Step N.` worked example
  with centered math continuation lines, a red "common mistake" box, a `##`
  heading, bullets, `\\` line breaks, a `---` divider, `[space]`,
  `[lines 3]`, `[workspace none]`, and a `[settings ...]` line. Read it
  before writing any lesson-style or guided worksheet.
- `example_figures_layouts.txt` — a `[grid 4]` drill block, SAS/ASA
  triangles with angle labels, a parallelogram with parallel marks, a
  trapezoid with a dashed altitude and right-angle mark, a `[columns 2]`
  section with tick-marked isosceles and right triangles, and a compact
  answer key. Read it before writing a worksheet with figures or compact
  layouts.
