#!/usr/bin/env python3
"""Lint a Worksheet-Studio .txt SAT Math module before compiling it.

    python3 lint_module.py module.txt [--questions 27] [--mc 20] [--spr 7]

Checks (errors stop the build; warnings are worth a look):
  * title + subtitle lines exist; subtitle names the question count & minutes
  * exactly N problems, numbered 1..N, one marker style
  * every multiple-choice item has exactly A) B) C) D), each on its own
    paragraph (blank line between options)
  * MC / SPR split matches the target (default 20 / 7)
  * no bare `$` outside math on any line (use \\$ for currency); every
    `$...$` span closes on the same line
  * no line inside a problem starts with a number followed by . ) or :
    (e.g. "3.5 kg" would be parsed as "Problem 3")
  * [graph] / [/graph] balanced; no unsupported chart words used as if the
    renderer drew them (bar graph, histogram, box plot, dot plot)
  * answer key covers every problem exactly once; MC keys are a letter A-D,
    SPR keys obey the SAT student-produced-response rules (<=5 chars if
    positive, <=6 if negative, digits . / - only, no mixed numbers)
  * every key line carries a domain tag like [Algebra: Linear functions] and
    the domain counts match the SAT blueprint (35/35/15/15) within +-1
  * difficulty ladder sanity: SPRs not all clustered at the end
Exit code 1 if any error.
"""
import argparse
import re
import sys

PROBLEM_RE = re.compile(
    r"^\s*([*\u2605\u22C6]\s*)?(?:Problem|Question|Q|#)?\s*(\d+)\s*[.):]\s*(.*)$",
    re.IGNORECASE,
)
ANSWER_HEADER_RE = re.compile(r"^\s*(answer\s*key|answers|solutions?)\s*:?\s*$", re.IGNORECASE)
OPTION_RE = re.compile(r"^\s*([A-D])\)\s+\S")
GRAPH_OPEN = re.compile(r"^\s*\[graph\]\s*$", re.I)
GRAPH_CLOSE = re.compile(r"^\s*\[/graph\]\s*$", re.I)
DECIMAL_LINE_RE = re.compile(r"^\s*\d+[.):]\s*\S")

DOMAINS = {
    "algebra": "Algebra",
    "advanced math": "Advanced Math",
    "psda": "PSDA",
    "problem-solving and data analysis": "PSDA",
    "problem solving and data analysis": "PSDA",
    "geometry/trig": "Geometry/Trig",
    "geometry and trigonometry": "Geometry/Trig",
    "geometry & trigonometry": "Geometry/Trig",
    "geometry": "Geometry/Trig",
}
TAG_RE = re.compile(r"\[([^\]:]+?)\s*:\s*([^\]]+)\]")
SPR_RE = re.compile(r"^-?(\d+(\.\d*)?|\.\d+|\d+/\d+)$")
UNSUPPORTED = ["bar graph", "bar chart", "histogram", "box plot", "boxplot", "dot plot", "pie chart", "circle graph"]


def _numeric(line):
    """Value of a simple numeric option line like 'B) $\\sqrt{75}$' or 'C) \\$64.80', else None."""
    t = OPTION_RE.match(line)
    if not t:
        return None
    v = line.split(")", 1)[1].strip().strip("$").replace("\\$", "").replace("{,}", "").replace(",", "")
    v = v.replace("\\pi", "*3.14159").replace("\\sqrt{2}", "*1.41421").replace("\\sqrt{3}", "*1.73205")
    v = re.sub(r"\\sqrt\{(\d+)\}", lambda mm: str(float(mm.group(1)) ** 0.5), v)
    v = re.sub(r"\\d?frac\{(-?\d+)\}\{(\d+)\}", lambda mm: f"({mm.group(1)}/{mm.group(2)})", v)
    v = v.replace("\\%", "")
    if re.fullmatch(r"-?[\d.()/*+\- ]+", v):
        try:
            return eval(v)
        except Exception:
            return None
    return None


def blueprint(n):
    """Target counts per domain for n questions (SAT: 35/35/15/15)."""
    alg = round(0.35 * n)
    adv = round(0.35 * n)
    psda = round(0.15 * n)
    geo = n - alg - adv - psda
    return {"Algebra": alg, "Advanced Math": adv, "PSDA": psda, "Geometry/Trig": geo}


def dollars_ok(line):
    """True if `$` usage on this line is well-formed."""
    s = line.replace("\\$", "")
    return s.count("$") % 2 == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--questions", type=int, default=27)
    ap.add_argument("--mc", type=int, default=None, help="target MC count (default ~75%)")
    ap.add_argument("--spr", type=int, default=None, help="target SPR count (default ~25%)")
    ap.add_argument("--previous", nargs="*", default=[], help="earlier modules to compare slot-by-slot")
    a = ap.parse_args()
    n = a.questions
    target_mc = a.mc if a.mc is not None else round(0.75 * n)
    target_spr = a.spr if a.spr is not None else n - target_mc

    errors, warns = [], []
    lines = open(a.path, encoding="utf-8").read().splitlines()

    # ---- head ---------------------------------------------------------
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        print("empty file"); sys.exit(1)
    title = nonblank[0].strip()
    subtitle = nonblank[1].strip() if len(nonblank) > 1 else ""
    if PROBLEM_RE.match(title):
        errors.append("line 1 looks like a problem marker; the first line must be the title")
    if PROBLEM_RE.match(subtitle):
        errors.append("line 2 must be a subtitle (e.g. '27 Questions · 35 Minutes · ...'); problem 1 is being eaten as the subtitle")
    if str(n) not in subtitle or "minute" not in subtitle.lower():
        warns.append(f"subtitle should state '{n} Questions' and the time limit in minutes")

    # ---- split into problems / answers --------------------------------
    mode = "head"
    problems, answers = {}, {}
    cur = None
    in_graph = False
    order = []
    for i, raw in enumerate(lines, 1):
        if in_graph:
            if cur is not None:
                cur["lines"].append((i, raw))
            if GRAPH_CLOSE.match(raw):
                in_graph = False
            continue
        if ANSWER_HEADER_RE.match(raw):
            mode = "answers"; cur = None; continue
        m = PROBLEM_RE.match(raw)
        if mode == "head":
            if m and raw.strip() not in (title, subtitle):
                mode = "problems"
            else:
                continue
        if m:
            num = int(m.group(2))
            body = m.group(3)
            cur = {"num": num, "lines": [(i, body)] if body.strip() else [], "start": i}
            store = answers if mode == "answers" else problems
            if num in store:
                errors.append(f"line {i}: duplicate {'answer' if mode=='answers' else 'problem'} number {num}")
            store[num] = cur
            if mode == "problems":
                order.append(num)
        elif cur is not None:
            cur["lines"].append((i, raw))
            if GRAPH_OPEN.match(raw):
                in_graph = True
    if in_graph:
        errors.append("a [graph] block is never closed with [/graph]")

    # ---- problem count / numbering ------------------------------------
    if order != list(range(1, n + 1)):
        errors.append(f"expected problems 1..{n} in order; got {order}. "
                      "A line starting with a number + '.' inside a problem (e.g. '3.5 kg') splits it.")

    # ---- per-problem checks -------------------------------------------
    mc_nums, spr_nums = [], []
    for num, p in sorted(problems.items()):
        plines = p["lines"]
        opts = []
        graph_opts = []
        gdepth = 0
        for idx, (ln, txt) in enumerate(plines):
            if GRAPH_OPEN.match(txt): gdepth += 1; continue
            if GRAPH_CLOSE.match(txt): gdepth -= 1; continue
            if gdepth:  # inside graph: check $ pairing; collect option-graph titles
                if not dollars_ok(txt):
                    errors.append(f"line {ln} (problem {num}, graph): unbalanced $")
                gt = re.match(r"^\s*title\s*:\s*([A-D])\)\s*$", txt, re.I)
                if gt:
                    graph_opts.append((gt.group(1).upper(), idx, ln))
                continue
            if not dollars_ok(txt):
                errors.append(f"line {ln} (problem {num}): unbalanced or bare $ — use \\$ for currency")
            if idx > 0 and DECIMAL_LINE_RE.match(txt):
                errors.append(f"line {ln} (problem {num}): line starts with a number + punctuation; the parser will read it as a new problem")
            om = OPTION_RE.match(txt)
            if om:
                opts.append((om.group(1), idx, ln))
            low = txt.lower()
            for w in UNSUPPORTED:
                if w in low:
                    warns.append(f"line {ln} (problem {num}): mentions '{w}' — the renderer can't draw it; use a table or a `points` scatterplot")
        if opts and graph_opts:
            errors.append(f"problem {num}: mixes text options and graph-title options")
        if opts:
            letters = [o[0] for o in opts]
            if letters != ["A", "B", "C", "D"]:
                errors.append(f"problem {num}: options must be exactly A) B) C) D) in order; found {letters}")
            vals = [_numeric(plines[idx][1]) for _, idx, _ in opts]
            if None not in vals and vals != sorted(vals):
                warns.append(f"problem {num}: numeric options are not in ascending order (SAT convention)")
            for _, idx, ln in opts:
                if idx > 0 and plines[idx - 1][1].strip() != "":
                    errors.append(f"line {ln} (problem {num}): put a blank line before each answer choice")
            mc_nums.append(num)
        elif graph_opts:
            letters = [o[0] for o in graph_opts]
            if letters != ["A", "B", "C", "D"]:
                errors.append(f"problem {num}: graph options must be titled A) B) C) D) in order; found {letters}")
            mc_nums.append(num)
        else:
            spr_nums.append(num)

    if abs(len(mc_nums) - target_mc) > 1:
        errors.append(f"{len(mc_nums)} multiple-choice items; target is {target_mc}")
    if abs(len(spr_nums) - target_spr) > 1:
        errors.append(f"{len(spr_nums)} student-produced-response items; target is {target_spr}")
    if spr_nums and spr_nums[0] > n // 2:
        warns.append(f"first SPR is #{spr_nums[0]}; the real test spreads SPRs through the module (aim for one in the first third)")
    for a_, b_, c_ in zip(spr_nums, spr_nums[1:], spr_nums[2:]):
        if c_ - a_ == 2:
            warns.append(f"three consecutive SPRs ({a_}-{c_}); the real test rarely does this")

    # ---- answer key -----------------------------------------------------
    if not answers:
        errors.append("no Answer Key section found")
    missing = [k for k in range(1, n + 1) if k not in answers]
    extra = [k for k in answers if k > n or k < 1]
    if missing:
        errors.append(f"answer key missing problems {missing}")
    if extra:
        errors.append(f"answer key has entries for nonexistent problems {extra}")

    counts = {d: 0 for d in ("Algebra", "Advanced Math", "PSDA", "Geometry/Trig")}
    for num, ans in sorted(answers.items()):
        if not ans["lines"]:
            errors.append(f"answer {num}: empty"); continue
        first = ans["lines"][0][1].strip()
        # answer token = text before the first ' — ' or ' - ' or '['
        tok = re.split(r"\s+[—–-]\s+|\s*\[", first, 1)[0].strip()
        tok_plain = tok.strip("$ ").replace("\\,", "")
        if num in mc_nums:
            if not re.fullmatch(r"[A-D]", tok_plain):
                errors.append(f"answer {num}: multiple-choice key must start with a letter A-D, got '{tok}'")
        elif num in spr_nums:
            core = tok_plain.replace(",", "")
            if not SPR_RE.match(core):
                errors.append(f"answer {num}: SPR key '{tok}' must be a number: digits, '.', '/', leading '-' only (no mixed numbers, no symbols, no units)")
            else:
                limit = 6 if core.startswith("-") else 5
                if len(core) > limit:
                    errors.append(f"answer {num}: SPR key '{core}' is {len(core)} characters; the SAT limit is {limit}")
                if "/" in core:
                    p_, q_ = core.lstrip("-").split("/")
                    if int(q_) == 0:
                        errors.append(f"answer {num}: zero denominator")
                    elif int(p_) % int(q_) == 0:
                        warns.append(f"answer {num}: fraction '{core}' reduces to an integer; give the integer")
        whole = " ".join(t for _, t in ans["lines"])
        tm = TAG_RE.search(whole)
        if not tm:
            errors.append(f"answer {num}: missing domain tag like [Algebra: Linear functions]")
        else:
            dkey = tm.group(1).strip().lower()
            if dkey not in DOMAINS:
                errors.append(f"answer {num}: unknown domain '{tm.group(1)}' (use Algebra, Advanced Math, PSDA, Geometry/Trig)")
            else:
                counts[DOMAINS[dkey]] += 1
        for ln, txt in ans["lines"]:
            if not dollars_ok(txt):
                errors.append(f"line {ln} (answer {num}): unbalanced or bare $")

    # domain runs: the blueprint forbids three consecutive items from one domain
    seq = []
    for num in range(1, n + 1):
        ans = answers.get(num)
        tm = TAG_RE.search(" ".join(t for _, t in ans["lines"])) if ans else None
        seq.append(DOMAINS.get(tm.group(1).strip().lower()) if tm else None)
    for i in range(len(seq) - 2):
        if seq[i] and seq[i] == seq[i + 1] == seq[i + 2]:
            errors.append(f"problems {i+1}-{i+3} are all {seq[i]}; interleave domains (no three in a row)")
            break

    if answers and len(answers) == n:
        bp = blueprint(n)
        for d, want in bp.items():
            got = counts[d]
            if abs(got - want) > 1:
                errors.append(f"domain {d}: {got} questions, blueprint wants {want} (±1)")
        print("domain counts:", ", ".join(f"{d} {counts[d]}/{bp[d]}" for d in bp))
    print(f"{len(mc_nums)} MC / {len(spr_nums)} SPR (targets {target_mc}/{target_spr}); SPRs at {spr_nums}")

    # cross-module lock-in: same skill in the same slot, same SPR positions
    def slot_map(path):
        m = {}
        sprs = []
        cur = None
        mode = "head"
        for raw in open(path, encoding="utf-8").read().splitlines():
            if ANSWER_HEADER_RE.match(raw):
                mode = "answers"; continue
            pm = re.match(r"^\s*(\d+)\.\s+(.*)$", raw) if mode == "answers" else None
            if pm:
                tm = TAG_RE.search(pm.group(2))
                m[int(pm.group(1))] = tm.group(2).strip().lower() if tm else None
                tok = re.split(r"\s+[—–-]\s+|\s*\[", pm.group(2), 1)[0].strip().strip("$ ")
                if not re.fullmatch(r"[A-D]", tok):
                    sprs.append(int(pm.group(1)))
        return m, sprs
    this_map, this_sprs = slot_map(a.path)
    for prev in a.previous:
        pmap, psprs = slot_map(prev)
        same = [k for k in this_map if this_map[k] and this_map.get(k) == pmap.get(k)]
        if len(same) >= 6:
            warns.append(f"vs {prev}: {len(same)} slots repeat the same skill in the same position "
                         f"({same}); rotate skills across bands (guide §7)")
        if this_sprs == psprs:
            warns.append(f"vs {prev}: identical SPR positions {this_sprs}; pick a different spread (guide §7)")
    for w in warns:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        print(f"\n{len(errors)} error(s) — fix the .txt and rerun.")
        sys.exit(1)
    print("\nLint passed." + (f" ({len(warns)} warning(s))" if warns else ""))


if __name__ == "__main__":
    main()
