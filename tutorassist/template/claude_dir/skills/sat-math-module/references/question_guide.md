# SAT Math question-writing guide

Read this whole file before drafting a module. It is organized as:

1. Blueprint — how many of what, in what order
2. House voice — how College Board phrases things
3. Skill catalog — every tested skill with a ready stem and a distractor recipe
4. Number hygiene and the SPR rules
5. Contexts that read as authentic
6. Difficulty ladder — what makes an item easy, medium, hard

Everything here is distilled from the official *Digital SAT Sample Questions* booklet and the content-domain specification. Write **original** items in this style; never reproduce a released item.

---

## 1. Blueprint

The real digital SAT Math section is two 22-question, 35-minute modules
(44 questions, 70 minutes). This skill's default module is **27 questions in
35 minutes** — denser than the real thing (~78 s/question vs ~95 s), so lean
on crisp items: the hard questions should be hard because of the *idea*, not
because of arithmetic volume.

Domain mix (35 / 35 / 15 / 15, rounded to the question count):

| Domain | 27-Q module | 22-Q (official pacing) |
|---|---|---|
| Algebra | 9–10 | 7–8 |
| Advanced Math | 9–10 | 7–8 |
| Problem-Solving & Data Analysis | 4 | 3–4 |
| Geometry & Trigonometry | 4 | 3–4 |

Format mix: **~75 % multiple choice (A–D), ~25 % student-produced response**
→ 20 MC / 7 SPR for 27 questions. Spread SPRs through the module (e.g.
positions 4, 8, 13, 17, 22, 25, 27), not bunched at the end.

Difficulty rises through the module: roughly the first third easy, middle
third medium, last third hard. Within that, interleave domains — never three
items from the same domain in a row.

Coverage: across 27 items hit **every skill** in section 3 at least once
where feasible (there are ~20), and no skill more than three times.

Visuals: at least one table item and at least one graph item per module;
a typical module has 3–5 items with a table or graph.

Context: about half of official items are set in a situation (a rate, a
cost, a population, a survey). Plan ≥ 11 of 27 in context, including
Algebra and Advanced Math items (an interpretation item, a system from two
prices, an exponential model), not only the PSDA ones. Testing showed that
without an explicit target the mix drifts to ~30 % context.

---

## 2. House voice

Read these before writing. The register is neutral, precise, and slightly
formal. Contexts are stated in one or two sentences; the math question is a
single clean sentence at the end.

Canonical openers (mix them, don't lean on one):

- "The function $f$ is defined by $f(x) = \ldots$."
- "In the $xy$-plane, the graph of $\ldots$"
- "The equation $\ldots$ represents this situation, where $x$ is $\ldots$ and $y$ is $\ldots$."
- "The table gives / The table shows / The scatterplot shows the relationship between…"
- "For the function $g$, $g(0) = \ldots$, and for each increase in $x$ by $1$, the value of $g(x)$ increases by $\ldots\%$."
- "…where $k$ is a constant."  /  "…where $a$ and $b$ are positive constants."

Canonical question stems:

- "What is the value of $x$?" / "What is the value of $x + y$?" / "What is the value of $ab$?"
- "Which equation / expression / function / system represents …?"
- "Which of the following is the best interpretation of the number $3$ in this context?"
- "Which point $(x, y)$ is a solution to the given inequality in the $xy$-plane?"
- "Which expression is equivalent to $\ldots$, where $x \ne \ldots$?"
- "How many distinct real solutions does the given equation have?"
- "For what value of $k$ does the system have no solution / infinitely many solutions?"
- "Which of the following is closest to …?"  (for graph reads and rounding)
- "To the nearest tenth, what is …?"
- "If … is selected at random, what is the probability that …?"

Conventions:

- Variables in math mode always: `$x$`, `$f(x)$`, `$xy$-plane`, `$\overline{AB}$`.
- State units in the stem and ask for the answer "in <units>": "What is the volume, in cubic centimeters, of the cylinder?" Never put units in an SPR answer.
- Numbers ≥ 1,000 use `$2{,}400$` in math or `2,400` in prose.
- Percent: `$25\%$` in math; "25%" in prose is fine (auto-escaped).
- Say "distinct real solutions", "positive constant", "not shown", "not necessarily drawn to scale" where the real test would.
- MC options are ordered ascending numerically when they are numbers; for expressions, order by structural similarity. No "None of the above" / "Cannot be determined" except as a deliberate distractor on a data item.

---

## 3. Skill catalog

Each entry: **stem pattern → correct-answer construction → distractor recipe**. Distractors on the real test are *diagnostic*: each wrong answer is what a student gets from one specific misconception. Build them that way, and note the misconception in your scratch notes so the key can mention it when useful.

### Algebra

**A1. Linear equations in one variable.**
Stem: "$5(x-2) = 3x + 8$. What is the value of $x$?" Harder: fractions, variables both sides, a parameter ("for what value of $c$ does the equation have no solution").
Distractors: sign slip on distribution, solving for the wrong quantity ($2x$ instead of $x$), forgetting to distribute to the second term.

**A2. Linear functions.**
Stem types: evaluate ($f(5) - g(2)$); write from a table ("The table gives three values…linear function $f$…What is $f(8)$?"); interpret slope/intercept in context ("best interpretation of the number 3").
Distractors for interpretation: swap slope and intercept meanings; flip the sign of the rate; confuse "value at time 0" with "time to reach 0".

**A3. Linear equations in two variables.**
Stem types: intercepts of $ax + by = c$; equation of a line parallel/perpendicular through a point; interpreting a coefficient in $3x + 6y = 63$-style equations.
Distractors: negative-reciprocal slope when parallel was asked (and vice versa); swapping $x$- and $y$-intercept; using the given point's coordinate as the intercept.

**A4. Systems of two linear equations.**
Stem types: solve and report a combination ($x+y$, $2x - y$); word problem with two prices at two stores; the parameter question ("$3x - ky = 7$ and $6x - 10y = 15$ has no solution. What is $k$?").
Construction: for no solution, match slope ratios and mismatch the constant ratio; for infinitely many, match all three; for exactly one, mismatch slopes.
Distractors: the other variable; the negated value; the value that gives infinitely many solutions; reading the ratio the wrong way.

**A5. Linear inequalities in one or two variables.**
Stem types: which point satisfies the system; which system represents a constraint word problem (hours ≤, earnings ≥); solve $-3x + 7 > 22$ and pick the correct solution set.
Distractors: direction flipped ("at most" → ≥); a point that satisfies only one of the two inequalities (make each distractor fail exactly one); dividing by a negative without flipping.

### Advanced Math

**M1. Nonlinear functions** (quadratic, exponential, polynomial, absolute value).
Stem types: minimum/maximum value of $x^2 - 10x + 31$; exponential from a percent change ("increases by 6% each year — which function?"); exponential from two values ($g(1)=12$, $g(3)=108$ → $g(0)$); read a vertex/intercept from a form; "for what value of $x$ is $f(x)$ at its minimum".
Distractors (exponential): $a(0.06)^t$, $a(1.6)^t$, linear $a + 1.06t$. Distractors (vertex): the $x$-coordinate of the vertex instead of the value; $k$ with wrong sign; $c$ (the $y$-intercept).

**M2. Nonlinear equations in one variable.**
Stem types: $3x^2 = 75$, $x > 0$; discriminant question ("exactly one real solution, $k < 0$ — what is $k$?"); "how many distinct real solutions" for $(x - 1)^2 = -4$; radical equation with an extraneous root; $x^3 - 4x = 0$.
Distractors: the positive value when negative was required; $k^2$ instead of $k$; the extraneous root; counting a repeated root twice.

**M3. Systems of equations in two variables (one nonlinear).**
Stem: line meets parabola/circle; "which is one of the intersection points"; "sum of the $x$-coordinates"; tangency via discriminant = 0.
Distractors: an $x$-value paired with the wrong $y$; a point on only one curve; the sum when the product was asked.

**M4. Equivalent expressions.**
Stem types: expand and identify a coefficient ("$(2x+3)(x-4) = 2x^2 + bx - 12$, what is $b$?"); factor/simplify a rational expression with stated exclusions; combine $\frac{a}{x+1} - \frac{b}{x-2}$ over a common denominator; exponent rules ($x^{3/2}$ as a radical); rewrite $ax^2 + bx + c$ in vertex form and read $h$.
Distractors: sign error in the middle term; cancelling terms instead of factors; the reciprocal; forgetting to distribute a negative across the second numerator.

### Problem-Solving & Data Analysis

**D1. Ratios, rates, proportional relationships, and units.**
Stem: unit-rate then scale ("45 pages in 3 minutes — how many in 8 minutes?"); density $= \frac{m}{V}$ with a cube; unit conversion with two steps (cm → m, then area).
Distractors: inverted rate; forgetting to square/cube a conversion factor; adding instead of multiplying.

**D2. Percentages.**
Stem: successive percent changes (discount then tax); "what percent of the items are red and striped" (multiply 40% × 30%); percent increase from $a$ to $b$; reverse percent ("after a 20% increase the price is \$96; what was the original?").
Distractors: adding percents (40+30); applying the second percent to the original base; dividing by the wrong base for percent change; $0.8 \times 96$ for the reverse-percent item.

**D3. One-variable data: distributions, center, spread.**
Stem: frequency table → median vs mean comparison; effect of removing an outlier on mean/median/range; which data set has the larger standard deviation (describe two lists).
Renderer note: no dot plots/histograms/box plots. Present the data as a **frequency table** (`| Value | Frequency |`) or a short list in prose.
Distractors: the mode; the midrange; "cannot be determined"; reversing the mean/median inequality.

**D4. Two-variable data: models and scatterplots.**
Stem: scatterplot with a line of best fit; "closest to the predicted $y$ at $x = 14$"; "how many data points lie above the line"; interpret the slope of the fit line; which model (linear vs exponential) fits a table.
Renderer: `[graph]` with `points (…)` and the fit line `y = mx + b for lo <= x <= hi`. Use `size: large`, real-world axis labels, and steps that make values readable. Keep 8–12 points, scattered ±10 % around the line.
Distractors: the actual data value at that $x$ (very SAT); the predicted value at $x \pm 1$ step; the residual.

**D5. Probability and conditional probability.**
Stem: two-way table (`| | Yes | No | Total |`) → "if a student who prefers morning is selected at random, what is the probability the student is in grade 12?"; simple probability from a frequency table.
Construction: make the conditional total a number whose fraction reduces nicely and fits the SPR limit (e.g. $54/102 = 9/17$).
Distractors: the joint probability ($54/200$); the reverse conditional ($54/90$); the complement.

**D6. Inference from sample statistics and margin of error.**
Stem: "A random sample of 400 residents… 58% support… margin of error 4 percentage points. Which is the most appropriate conclusion?" Answer is a *plausible range for the population* ("it is plausible that between 54% and 62% of all residents…"). Also: which sample design is a valid random sample; what a larger sample does to the margin of error.
Distractors: claims about the sample rather than the population; certainty language ("exactly 58%"); claims about causation.

**D7. Evaluating statistical claims: observational studies and experiments.**
Stem: describe a study; "which conclusion is appropriate?" Random *selection* → generalize to the population; random *assignment* → cause and effect; neither → neither.
Distractors: the other three combinations of (generalize / cause) claims.

### Geometry & Trigonometry

**G1. Area and volume.**
Stem: cylinder/cone/sphere/prism volume (reference-sheet formulas), often with one dimension to be found from another ("volume is $90\pi$, radius 3 — height?"); area of a composite figure described in words; scaling ("if every edge is doubled, the volume is multiplied by …").
Distractors: surface area instead of volume; forgetting the $\frac{1}{3}$ for cones/pyramids; radius/diameter swap; forgetting to square/cube a scale factor.

**G2. Lines, angles, and triangles.**
Stem: similar triangles with a scale factor ("$ABC \sim DEF$, $AB = 6$, $BC = 9$, $DE = 10$ — what is $EF$?"); angle chasing with parallel lines and a transversal; triangle angle sum with algebraic expressions; isosceles base angles.
Distractors: additive instead of multiplicative scaling ($9 + 4 = 13$); matching the wrong corresponding side; the supplement instead of the angle.

**G3. Right triangles and trigonometry.**
Stem: Pythagorean theorem (diagonal of a rectangle, often with a radical like $5\sqrt{17}$); given $\sin A = \frac{5}{13}$, find $\tan B$ or $\cos A$; special right triangles (30-60-90, 45-45-90); complementary-angle identity $\sin A = \cos(90° - A)$; radians ↔ degrees.
Distractors: the reciprocal ratio; the ratio for the other acute angle; using the hypotenuse as a leg; $\sqrt{a^2 - b^2}$ vs $\sqrt{a^2 + b^2}$.

**G4. Circles.**
Stem: arc length ↔ central angle ↔ circumference (45° arc of length 3 → circumference 24); sector area; equation of a circle in general form → complete the square for center/radius; a point on the circle given center and radius.
Distractors: $r^2$ instead of $r$; the center's coordinates with signs flipped; arc measure vs arc length confusion; diameter for radius.

---

## 4. Number hygiene and the SPR rules

- Choose numbers **backward from the answer**: decide the clean answer first, then build the problem. Slopes, roots, intercepts, and totals should be integers or one-decimal values unless the skill is specifically about rounding ("to the nearest tenth").
- Two-step percent items: pick a base so every intermediate is exact (\$80 → 25% off → \$60 → 8% tax → \$64.80).
- Two-way tables: rows and columns must sum to the totals shown. Verify.
- Frequency tables: total frequency must equal the stated class size.
- Scatterplots: the fit line must pass visibly through the cloud; predicted values at the asked $x$ must be clearly closer to one option than the others.
- Similar triangles: choose the scale factor first (e.g. $\frac{5}{3}$) and multiply.
- Systems of inequalities in context ("at most 40 hours", "at least \$1,000"): confirm the system has a feasible **integer** solution before finalizing — a cap and a floor that can't both be met is a flaw a real item never has.
- MC options that are numbers are listed in ascending order, including radicals and fractions ($5$, $\sqrt{75}$, $15$, $25$).

**Student-produced response (SPR) rules** — the correct answer must be enterable on the real test:

- Positive answers: at most **5 characters**; negative: at most **6** (the minus sign counts).
- Allowed characters: digits, `.`, `/`, and a leading `-`. **No** symbols (%, \$, °, π), **no** units, **no** commas, **no** mixed numbers ($3\frac{1}{2}$ must be `7/2` or `3.5`).
- Fractions need not be reduced but must fit: `54/102` is 6 characters (too long for a positive answer) → the key must offer `9/17` (or `.5294`). Decimals that don't terminate are entered truncated or rounded to fit 5 characters (`.5294` or `.5295`) — say "either form" in the key.
- If a question has more than one correct answer (e.g. "one solution"), the stem must say "What is **one** possible value" — and the key lists all acceptable answers.
- Never pose an SPR whose answer is irrational, negative-and-long, or ambiguous in form.

---

## 5. Contexts that read as authentic

College Board contexts are concrete, low-drama, and international. Rotate among: a greenhouse temperature, a town's population, a printer's output, a landscaper's schedule, a jacket on sale, a survey of students, quiz scores vs hours studied, a cube of wood and its density, two trees and their shadows, a shipping container's volume, a bicycle rental fee, a plant's height over weeks, a bakery's flour usage, a car's fuel economy, a museum's admission revenue.

Keep names to initials or none; avoid brands, celebrities, and anything political. Don't write jokes into the stems.

---

## 6. Difficulty ladder

| Level | What makes it that level | Example |
|---|---|---|
| Easy (Q1–9) | One step, one concept, numbers given directly, the question is the definition | Solve a two-step linear equation; evaluate $f(5)$; volume from given $r$ and $h$ |
| Medium (Q10–19) | Two steps or a translation step (words → equation, table → function), a mild trap | Line parallel through a point from a graph; percent then tax; mean vs median from a frequency table |
| Hard (Q20–27) | A parameter or unknown coefficient, a structural insight (discriminant, ratio of slopes, Vieta), or a conditional reading of data | $k$ for no solution; $k$ for one real root; conditional probability; $c$ from root spacing $q - p = 4$ |

Hard items on the SAT are rarely long. They are short stems that require the student to *know which idea applies*. Prefer that to arithmetic-heavy items.

---

## 7. Shape of a module by position

Testing four generated modules against each other showed a stable
position-by-position shape. Part of it is the real test's shape and should
be kept; part of it is lock-in that must be rotated.

### The archetype (keep this)

| Slots | Complexity | What typically lives here | Stem length |
|---|---|---|---|
| **1–3** | One step, one concept, numbers given directly | A two-step linear equation; evaluate a function; a unit rate; one interpretation item ("best interpretation of the number 30") | 12–25 words |
| **4–9** | Still one idea, but with a translation step | Intercept of $ax + by = c$; expand a product and read a coefficient; a simple percent; solve a small system; a volume from given dimensions; first SPRs appear here | 10–30 words |
| **10–13** | First "medium" items; two steps or a representation switch | A linear inequality (solve or pick the point); a table or scatterplot to read; an exponential model from a percent change; a linear function from a table or two points | 20–45 words |
| **14–19** | Medium; a mild trap or an extra step | Similar triangles / angle relationships; a line meeting a parabola; a radical or absolute-value equation with a check; mean vs. median from a frequency table; circle from center/radius or completing the square; vertex or extreme value | 15–40 words |
| **20–24** | Hard; a parameter or a structural idea | System with $k$ (no / infinitely many solutions); a rational or exponent-rule simplification; a two-way table or margin-of-error conclusion; a trig identity or ratio; a constraint system in context | 20–45 words |
| **25–27** | Hardest; short stems that hinge on one insight, usually SPRs | Exponential from two values; Vieta or discriminant with a constant; vertex form / root spacing / coefficient matching to find a constant | 25–40 words |

Stem length should rise gently through the module (≈24 → 28 → 31 words per
third); "hard" comes from the idea, not from a longer paragraph.

### The lock-in to avoid (rotate this)

Across four modules written from the same plan, these slots repeated the
same skill every time: Q1 linear warm-up, Q10 linear inequality, Q13
linear-function SPR, Q23 trigonometry, Q25 nonlinear-function SPR, Q27
find-the-constant SPR — and the SPR positions were identical (4, 8, 13, 17,
22, 25, 27). A student who takes three such modules learns the layout, not
the math. Rules:

- **Vary SPR positions** between modules. Pick one of these patterns, or
  any spread that keeps the first SPR ≤ 9 and never three in a row:
  `4 8 13 17 22 25 27` · `3 7 11 16 20 24 27` · `5 9 12 18 21 24 26` ·
  `2 6 10 15 19 23 27` · `4 7 12 16 21 25 26`.
- **Rotate which skill sits in each band.** Within a band, any of the
  listed skills is fine; across modules, don't put the same skill in the
  same slot twice. Slot 23 can be trig once, circles next time, similar
  triangles after that; slot 27 can be a discriminant, then root spacing,
  then a system with $k$.
- **Vary the closer.** Slots 25–27 have drifted to "quadratic + constant"
  three times over. Alternatives with the same bite: a conditional
  probability, an exponential from two points, an arc/sector with an
  unknown radius, a linear system in context that resolves to one number.
- **Move the visuals.** The scatterplot sat at Q12 three times; tables can
  appear at 5, 12, 18, or 22 and a graph anywhere from 3 to 21.
- When the user asks for "another module", or when earlier modules exist in
  the conversation or memory, run `lint_module.py new.txt --previous
  old1.txt old2.txt` and act on its collision warnings (see SKILL.md).
