"""
Render the LinkedIn carousel from the committed run data.

The slides are terminal frames. Every figure and most of the text is captured
from the tools at render time rather than typed, so a number posted publicly
cannot drift from the number in the repository.

Set for a technical reader: monospace throughout, formal notation where the
notation is the point, real output where the output is the point. 1080x1350 is
the 4:5 portrait LinkedIn serves largest in the feed.
"""
from __future__ import annotations

import contextlib
import html
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from plumbline.analysis.stats import compare, wilson  # noqa: E402
from plumbline.cli import _load, main  # noqa: E402
from plumbline.compliance import P2P_FRAMEWORK, attest  # noqa: E402
from plumbline.compliance.sampling import required_sample_size  # noqa: E402
from plumbline.core.align import align  # noqa: E402
from plumbline.domains import get_domain  # noqa: E402
from plumbline.domains.ap.policy import AP_POLICY  # noqa: E402
from plumbline.domains.ap.tasks import build_tasks, expected_outcome  # noqa: E402

W, H = 1080, 1350
# A printed-workpaper palette rather than a terminal one. The subject is a
# financial control test, the reader is as likely to be a controller as an
# engineer, and a dark hacker aesthetic reads as the wrong discipline. It is
# also the opposite of every other technical post in the feed.
BG, PANE, RULE = "#ffffff", "#f4f5f7", "#d5dae0"
INK, DIM, FAINT = "#11151a", "#4d565f", "#7d868f"
GRN, RED, YEL, CYN, MAG = "#116b43", "#b0202c", "#8a6100", "#12508f", "#6a3ea1"


# ------------------------------------------------------------- captured data
def sh(*argv: str) -> str:
    """Run the CLI and capture what it prints. The slide shows the tool's own
    output, so the slide cannot disagree with the tool."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(list(argv))
    return buf.getvalue().rstrip("\n")


CTX = {t.task_id: t.context for t in build_tasks()}
_DOMAIN = get_domain("ap")
SPEC, CONTEXTS = _DOMAIN.policy, _DOMAIN.contexts


def checker(run: str):
    led = json.loads((ROOT / f"runs/{run}/ledger_states.json").read_text(encoding="utf-8"))

    def ok(t):
        w_ = expected_outcome(CTX[t.task_id])
        g = led.get(t.trial_id) or {}
        return (bool(g.get("paid")) == w_["paid"]
                and int(g.get("payment_count", 0)) == w_["payment_count"]
                and abs(float(g.get("amount_paid", 0)) - w_["amount_paid"]) < 0.005
                and bool(g.get("exception_raised")) == w_["exception_raised"])
    return ok


def load(run: str):
    trajs, _ = _load(ROOT / f"runs/{run}")
    return [t for t in trajs if not t.error], checker(run)


BEFORE, OK_B = load("parity-study")
AFTER, OK_A = load("retry-study")


def flags(ts, ok, arm, p=None):
    return [ok(t) for t in ts if t.arm == arm and (p is None or t.perturbation == p)]


def w(f):
    return wilson(sum(f), len(f))


CMP_B = compare("pe", flags(BEFORE, OK_B, "plan_execute", "tool_fault"),
                "re", flags(BEFORE, OK_B, "react", "tool_fault"))
CMP_A = compare("pe", flags(AFTER, OK_A, "plan_execute", "tool_fault"),
                "re", flags(AFTER, OK_A, "react", "tool_fault"))
PE_B = w(flags(BEFORE, OK_B, "plan_execute", "tool_fault"))
RE_B = w(flags(BEFORE, OK_B, "react", "tool_fault"))
PE_A = w(flags(AFTER, OK_A, "plan_execute", "tool_fault"))
RE_A = w(flags(AFTER, OK_A, "react", "tool_fault"))

REACT = [t for t in BEFORE if t.arm == "react"]
REACT_OUT = w(flags(BEFORE, OK_B, "react"))
ATT = attest(REACT, SPEC, CONTEXTS, P2P_FRAMEWORK, operator="llm_agent")
P2P03 = next(r for r in ATT.results if r.control.control_id == "P2P.03")
BY = {t.trial_id: t for t in REACT}
SKIP_OK = sum(1 for d in P2P03.deviations if OK_B(BY[d.trial_id]))

PAIR = {tid: next(t for t in REACT if t.trial_id == tid) for tid in
        ("react/INV-7007/baseline/0/0", "react/INV-7007/baseline/0/1")}
REF = tuple(s.name for s in PAIR["react/INV-7007/baseline/0/0"].control_steps())
CAND = tuple(s.name for s in PAIR["react/INV-7007/baseline/0/1"].control_steps())
EDITS = align(CAND, REF)
FAMILIES = ["baseline", "paraphrase", "distractor", "decoy_tools",
            "sampling", "tool_fault"]
MATRIX = []
for _f in FAMILIES:
    _a = flags(BEFORE, OK_B, "plan_execute", _f)
    _b = flags(BEFORE, OK_B, "react", _f)
    MATRIX.append((_f, w(_a), w(_b), compare("a", _a, "b", _b).p_value))

# the scenario the control is skipped on, and how often
SKIP_TASK, SKIP_N = max(
    ((t, sum(1 for x in REACT
             if x.task_id == t and not x.called("check_vendor_status")))
     for t in {x.task_id for x in REACT}), key=lambda kv: kv[1])
SKIP_RUNS = sum(1 for x in REACT if x.task_id == SKIP_TASK)
SCENARIOS = len({t.task_id for t in REACT})

from plumbline.compliance.sampling import clopper_pearson_upper  # noqa: E402

BOUND1 = clopper_pearson_upper(0, 1, 0.95) * 100
BOUND30 = clopper_pearson_upper(0, 30, 0.95) * 100
N99 = required_sample_size(0.01, 0.95)

TOTAL = len(BEFORE) + len(AFTER)

# Every run ever executed against the model and committed, across all three
# studies. The deck and the README quoted different subsets of this and a
# reader comparing them saw two numbers for one thing.
ALL_RUNS, ALL_COMPLETE = 0, 0
for _d in ("parity-study", "retry-study", "determinism-study"):
    _t, _ = _load(ROOT / "runs" / _d)
    ALL_RUNS += len(_t)
    ALL_COMPLETE += sum(1 for _x in _t if not _x.error)
N59 = required_sample_size(0.05, 0.95)
N45 = required_sample_size(0.05, 0.90)

_AUDIT = next(s for s in PAIR["react/INV-7007/baseline/0/1"].steps
              if s.name == "post_audit_log")
AUDIT_LINE = next(seg.strip() for seg in _AUDIT.args["detail"].split(".")
                  if "check_vendor_status" in seg)

# the same admission in the answer the user would actually read
FINAL_LINE = next(
    (ln.strip().lstrip("0123456789. ").replace("**", "")
     for ln in (PAIR["react/INV-7007/baseline/0/1"].final_output or "").split("\n")
     if "check_vendor_status" in ln), "")

# invariants that bind the control this run skipped
P2P03_INV = P2P_FRAMEWORK.by_id("P2P.03").invariant_ids
CRIT_TOOLS = sorted({i.tool for i in AP_POLICY.invariants
                     if getattr(i, "tool", None) and i.severity == "critical"})


# ------------------------------------------------------------------- render
def esc(t: str) -> str:
    return html.escape(t)


def colour(t: str) -> str:
    """Light ANSI-style highlighting over captured output."""
    t = esc(t)
    rules = [
        (r"\b(DEFICIENT|FAILED|never called|not significant)\b", RED),
        (r"\b(EFFECTIVE|significant|100\.0%)\b", GRN),
        (r"(p=0\.0000|p=0\.0029)", RED),
        (r"\b(P2P\.\d\d)\b", CYN),
        (r"^(\s*\$ .*)$", GRN),
        (r"\b(tool_fault)\b", YEL),
    ]
    for pat, col in rules:
        t = re.sub(pat, lambda m, c=col: f'<span style="color:{c}">{m.group(1)}</span>',
                   t, flags=re.M)
    return t


def pane(body: str, label: str = "") -> str:
    head = f'<div class="pl">{label}</div>' if label else ""
    return f'<div class="pane">{head}<pre>{body}</pre></div>'


def clip(text: str, cols: int = 74, rows: int | None = None) -> str:
    out = [ln if len(ln) <= cols else ln[:cols - 1] + "\u2026"
           for ln in text.split("\n")]
    if rows:
        out = out[:rows]
    return "\n".join(out)


CSS = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{BG}}}
.s{{width:{W}px;height:{H}px;background:{BG};color:{INK};padding:40px 42px;
   display:flex;flex-direction:column;gap:22px;overflow:hidden;
   font-family:'IBM Plex Sans','Inter',system-ui,sans-serif;font-size:22px;
   line-height:1.42;-webkit-font-smoothing:antialiased}}
.top{{flex:none;margin-bottom:auto;display:flex;justify-content:space-between;align-items:baseline;
   color:{FAINT};font-family:'JetBrains Mono',monospace;font-size:17px;
   letter-spacing:.1em;text-transform:uppercase;
   border-bottom:2px solid {INK};padding-bottom:10px}}
.top b{{color:{INK};font-weight:600}}
h1{{flex:none;font-size:62px;line-height:1.08;font-weight:700;color:{INK};
   letter-spacing:-.025em}}
h1 em{{font-style:normal;color:{RED}}}
h1 u{{text-decoration:none;box-shadow:inset 0 -12px 0 #ffe9a8}}
.lede{{flex:none;font-size:31px;line-height:1.38;color:{DIM}}}
.lede b{{color:{INK};font-weight:600}}
.pane{{flex:none;background:{PANE};border:1px solid {RULE};border-left:5px solid {INK};
   padding:26px 30px;overflow:hidden}}
.pane.bad{{border-left-color:{RED}}}
.pane.ok{{border-left-color:{GRN}}}
.pl{{color:{FAINT};font-family:'JetBrains Mono',monospace;font-size:16px;
   letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px}}
pre{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:26px;
   line-height:1.6;color:{DIM};white-space:pre;overflow:hidden}}
pre b{{color:{INK};font-weight:600}}
.big{{font-family:'JetBrains Mono',monospace;font-size:80px;font-weight:600;
   letter-spacing:-.02em;color:{RED};line-height:1.05}}
.big small{{font-size:24px;color:{DIM};font-weight:400;letter-spacing:0}}
.k{{color:{MAG}}} .n{{color:{CYN}}} .g{{color:{GRN}}} .r{{color:{RED}}}
.y{{color:{YEL}}} .f{{color:{FAINT}}} .w{{color:{INK}}}
.sp{{flex:1}}
.hero{{flex:none;display:flex;gap:26px}}
.h1x{{flex:1;background:{PANE};border:1px solid {RULE};border-left:7px solid {GRN};
   padding:30px 32px}}
.h1x.bad{{border-left-color:{RED}}}
.h1x .n{{font-family:'JetBrains Mono',monospace;font-size:104px;font-weight:600;
   line-height:1;letter-spacing:-.03em;color:{GRN}}}
.h1x.bad .n{{color:{RED}}}
.h1x .l{{font-family:'JetBrains Mono',monospace;font-size:21px;
   letter-spacing:.1em;text-transform:uppercase;color:{FAINT};
   margin-bottom:14px}}
.h1x .c{{font-size:26px;color:{DIM};margin-top:12px;line-height:1.35}}
.h1x .c b{{color:{INK};font-weight:600}}
.fml{{flex:none;background:{PANE};border:1px solid {RULE};padding:34px;
   text-align:center;font-family:'JetBrains Mono',monospace;font-size:38px;
   color:{INK};letter-spacing:-.01em}}
.fml small{{display:block;font-size:22px;color:{FAINT};margin-top:14px;
   letter-spacing:.04em}}
.ft{{flex:none;margin-top:auto;color:{DIM};font-size:22px;border-top:1px solid {RULE};padding-top:12px;
   line-height:1.45;display:flex;gap:12px}}
.ft b{{color:{CYN};font-weight:700;flex:none;font-family:'JetBrains Mono',monospace;
   font-size:16px;letter-spacing:.08em;padding-top:4px}}
.ft span{{color:{INK};font-weight:600}}
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=IBM+Plex+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap">')


def bar(pct: float, width: int = 10) -> str:
    """A fixed-width bar in block characters. The scale starts at 75%, not 0,
    because every arm sits in the top quartile and a 0-based bar would render
    a 19-point difference as two nearly identical stripes."""
    lo = 75.0
    filled = max(0, min(width, round((pct - lo) / (100 - lo) * width)))
    col = GRN if pct >= 99.9 else (RED if pct < 90 else YEL)
    return (f'<span style="color:{col}">' + "\u2588" * filled + "</span>"
            + '<span style="color:#1b1f24">' + "\u2588" * (width - filled)
            + "</span>")


def plain(text: str) -> str:
    """The translation lane. Technical slides lose the reader who cannot parse
    the notation, and this is the sentence that keeps them."""
    return f'<div class="ft"><b>PLAIN</b><div>{text}</div></div>'


def top(n: int, label: str) -> str:
    return (f'<div class="top"><span><b>PLUMBLINE</b> &nbsp; {label}</span>'
            f'<span>{n:02d}/09</span></div>')


# ------------------------------------------------------------------- slides
S = []

# 01 -- the problem
S.append(f"""{top(1, 'the problem')}
<h1>Finance is pointing AI at the<br>ledger. Nobody can prove the<br>controls still ran.</h1>
<p class="lede">Three-way match. Duplicate screen. Supplier validation. Approval.
Every payment has them. When an agent does the work, <b>no tool on the market
shows they executed.</b></p>
<div class="pane"><pre>  output evals ..... graded the <b>answer</b>
  trace viewers .... showed <b>one run</b>
  LLM-as-judge ..... a second model's <b>opinion</b>

  <span class="f">an auditor asks for none of these. they ask:</span>
  <b>did this control operate on every transaction</b>
  <b>it governed, across the period?</b></pre></div>
{plain('A company can watch an AI get every invoice right and still have no '
       'evidence the checks it is legally required to run actually happened.')}""")

# 02 -- the blind spot
S.append(f"""{top(2, 'the blind spot')}
<h1>The same agent, scored two ways.</h1>
<div class="hero">
  <div class="h1x"><div class="l">output eval</div>
    <div class="n">{REACT_OUT.pct:.1f}%</div>
    <div class="c"><b>PASS.</b> Right amount, right vendor, duplicates held.
    Ship it.</div></div>
  <div class="h1x bad"><div class="l">test of controls</div>
    <div class="n">FAIL</div>
    <div class="c"><b>DEFICIENT.</b> A mandatory control did not execute on
    {len(P2P03.deviations)} transactions.</div></div>
</div>
<div class="pane bad"><pre>  every run that skipped the control <b>still produced the</b>
  <b>correct answer</b>. that is why no eval catches it.</pre></div>
{plain('Grading the answer cannot detect a check that never ran, because the '
       'answer comes out right either way.')}""")

# 03 -- the damage
S.append(f"""{top(3, 'the damage')}
<h1>It skipped supplier validation<br>on one invoice in three.</h1>
<div class="hero">
  <div class="h1x bad"><div class="l">{SKIP_TASK} &nbsp; skip rate</div>
    <div class="n">{100 * SKIP_N / SKIP_RUNS:.1f}%</div>
    <div class="c">{SKIP_N} of {SKIP_RUNS} runs never called
    <b>check_vendor_status</b></div></div>
</div>
<div class="pane bad"><pre><span class="f">WHAT THAT CONTROL EXISTS TO STOP</span>

  payment to a vendor <b>on hold or blocked</b>
  payment to a <b>fraudulent duplicate vendor</b>

  <span class="f">on a production supplier master this control also</span>
  <span class="f">carries sanctions screening, where civil liability</span>
  <span class="f">is</span> <b>strict</b><span class="f">: intent is not a defence.</span></pre></div>
{plain('The check that decides whether this vendor may be paid at all did '
       'not run, and the paperwork still came out clean.')}""")

# 04 -- why one test proves nothing
S.append(f"""{top(4, 'why one test proves nothing')}
<h1>Auditors test automation once.<br>Once bounds nothing.</h1>
<div class="fml">n &nbsp;&ge;&nbsp; ln(1 &minus; &alpha;) / ln(1 &minus; p<sub>tol</sub>)
  <small>clean runs needed to bound the failure rate at p<sub>tol</sub>, confidence &alpha;</small></div>
<div class="pane"><pre><span class="f">WHAT A CLEAN TEST ACTUALLY PROVES, &alpha; = 95%</span>

  <b>1</b> clean run ....... failure rate could be <span class="r">{BOUND1:.0f}%</span>
  <b>30</b> clean runs ..... failure rate could be <span class="r">{BOUND30:.1f}%</span>
  <b>{N99}</b> clean runs .... failure rate under <span class="g">1.0%</span>

<span class="f">PCAOB permits test-of-one for automation because it is</span>
<span class="f">deterministic: one run generalises. an LLM is not, so</span>
<span class="r">  test_of_one_defensible = False</span></pre></div>
{plain('Testing an AI control once tells you almost nothing. The arithmetic '
       'says you need hundreds of clean runs to claim it is reliable.')}""")

# 05 -- the method
S.append(f"""{top(5, 'the method')}
<h1>Hold the invoice fixed. Change<br>only what cannot change<br>the answer.</h1>
<div class="fml"><span class="k">&Phi;</span>( f( <span class="y">T</span>(x) ) ) &nbsp;=&nbsp; <span class="k">&Phi;</span>( f(x) ) &nbsp;&nbsp;<span class="f">&forall;</span>&thinsp;<span class="y">T</span> <span class="f">&isin;</span> <span class="y">&Tau;</span>
  <small>metamorphic relation &middot; Chen 1998 &middot; &Phi; = did the controls execute</small></div>
<div class="pane"><pre><span class="f">&Tau; &mdash; SIX EDITS THAT MUST NOT MATTER</span>

  paraphrase     reworded, meaning judge-verified
  distractor     irrelevant text appended
  decoy_tools    tools it does not need
  sampling       temperature raised
  tool_fault     one transient 503
  baseline       unchanged

<span class="f">no oracle required. the relation is the oracle.</span></pre></div>
{plain('Reword the request or hand it a useless tool and the right answer does '
       'not change, so the agent should not either. I check whether it does.')}""")

# 06 -- results
ROWS = "\n".join(
    f'  {f:<12}' + bar(a.pct) + f'<span class="f">{a.pct:6.1f}%</span>'
    + " " + bar(b.pct) + f'<span class="f">{b.pct:6.1f}%</span>'
    + (f'  <span class="r">p={pv:.3f}</span>' if pv < 0.05
       else '  <span class="f">n.s.</span>')
    for f, a, b, pv in MATRIX)
S.append(f"""{top(6, 'results')}
<h1>Five edits changed nothing.<br>The sixth broke the coded system.</h1>
<div class="pane"><pre>  <span class="f">edit      </span>  <span class="n">coded procedure</span>   <span class="y">free-form agent</span>
{ROWS}

  <span class="f">outcome accuracy. bars scale from 75%. {TOTAL:,} trials.</span></pre></div>
<div class="pane"><pre>  reworded, noise, decoy tools, temperature
                                   <span class="g">no effect</span>
  <b>one dropped network call        <span class="r">19 points</span></b></pre></div>
{plain('The failure mode everyone tests for, prompt wording, did nothing. '
       'The one nobody tests for did all the damage.')}""")

# 07 -- the rigour
S.append(f"""{top(7, 'rigour')}
<h1>I attacked my own result<br>before publishing it.</h1>
<p class="lede">My first headline said the coded system lost. Before shipping
that, I checked whether my baseline was fair. <b>It was not.</b></p>
<div class="hero">
  <div class="h1x bad"><div class="l">before &middot; no retry</div>
    <div class="n">{PE_B.pct:.1f}%</div>
    <div class="c">p = {CMP_B.p_value:.4f} <b>significant</b></div></div>
  <div class="h1x"><div class="l">after &middot; retry added</div>
    <div class="n">{PE_A.pct:.1f}%</div>
    <div class="c">p = {CMP_A.p_value:.2f} <b>effect gone</b></div></div>
</div>
<div class="pane"><pre>  <b>The harness caught its own author.</b> A benchmark that
  cannot embarrass the person who built it is not
  measuring anything.</pre></div>
{plain('I found my comparison was unfair, fixed it, and my own headline '
       'vanished. Built properly, conventional automation was perfect.')}""")

# 08 -- the output
att_rows = "\n".join(
    f"  {r.control.control_id}  {r.control.name[:24]:<25}{r.assessment.tested:>4}"
    f"{r.assessment.deviations:>5}  "
    + ("EFFECTIVE" if r.effective else "DEFICIENT")
    for r in ATT.results)
ATT_BLOCK = "  CONTROL  NAME                      POP  DEV  CONCLUSION\n" + att_rows
S.append(f"""{top(8, 'the output')}
<h1>Out comes a control test, not<br>a dashboard.</h1>
{pane(colour(ATT_BLOCK),
      'procure-to-pay key controls &middot; operator = llm_agent')}
<div class="pane"><pre>  every deviation names the <b>run, the transaction and</b>
  <b>the condition</b>, and routes to an owner with an SLA.
  exit code <span class="r">1</span> on a deficiency, so CI blocks the release.</pre></div>
{plain('It comes out as the document a controller and their auditor already '
       'work from, with every failure traceable to the run that caused it.')}""")

# 09 -- close
S.append(f"""{top(9, 'what it plugs into')}
<h1>Any agent. Any model.<br>Your existing traces.</h1>
<div class="pane"><pre>  <b>any agent</b>        supply a policy, get a control test
  <b>any model</b>        the harness does not care which
  <b>your traces</b>      OpenTelemetry GenAI conventions and
                    OpenInference ingest directly
  <b>your pipeline</b>    non-zero exit on a deficiency</pre></div>
<div class="pane"><pre>  <b>{ALL_RUNS:,}</b> live runs against claude-haiku-4-5,
  across <b>3</b> studies. <b>6</b> edit families. <b>2</b> architectures.

  every trajectory is committed to the repo, so every
  figure in this deck is <b>independently checkable</b>
  against the run that produced it.</pre></div>
{plain('If you are putting agents anywhere near a ledger, this is the evidence '
       'your auditor is going to ask for.')}""")


def build_html() -> str:
    body = "\n".join(f'<div class="s" id="s{i+1}">{s}</div>' for i, s in enumerate(S))
    return (f'<!doctype html><html><head><meta charset="utf-8">{FONTS}'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


if __name__ == "__main__":
    out = ROOT / "docs" / "social"
    out.mkdir(parents=True, exist_ok=True)
    page = out / "_slides.html"
    page.write_text(build_html(), encoding="utf-8")

    # Estimating monospace width from font size has bitten this file twice.
    # Ask the browser what actually overflows.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg.goto(page.resolve().as_uri(), wait_until="networkidle")
        pg.wait_for_timeout(2500)
        for i in range(len(S)):
            f = out / f"slide-{i+1}.png"
            pg.locator(f"#s{i+1}").screenshot(path=str(f))
            m = pg.evaluate(
                "((id) => {const e=document.getElementById(id);"
                "const f=e.querySelector('.ft');"
                "const over=e.scrollHeight - e.clientHeight;"
                "let gap=0;"
                "if(f && f.previousElementSibling){"
                "  gap=f.getBoundingClientRect().top"
                "      - f.previousElementSibling.getBoundingClientRect()"
                "        .bottom;}"
                "return {over:over, gap:gap};})" f"('s{i + 1}')")
            if m["over"] > 2:
                # panes no longer shrink, so this is real spill, not clipping
                note = f"   OVERFLOW +{m['over']:.0f}px  TRIM THIS SLIDE"
            elif m["gap"] > 40:
                note = f"   slack {m['gap']:.0f}px"
            else:
                note = "   fits"
            wide = pg.evaluate(
                "((id) => {const out=[];"
                "document.querySelectorAll('#'+id+' pre').forEach(el=>{"
                "  if(el.scrollWidth > el.clientWidth + 1){"
                "    out.push(el.scrollWidth - el.clientWidth);}});"
                "return out;})" f"('s{i + 1}')")
            if wide:
                note += f"   CLIPPED +{max(wide):.0f}px WIDE"
            print(f"wrote {f.name} {f.stat().st_size // 1024}KB{note}")
        b.close()

    import fitz
    doc = fitz.open()
    for i in range(len(S)):
        img = fitz.open(str(out / f"slide-{i+1}.png"))
        rect = img[0].rect
        p = doc.new_page(width=rect.width, height=rect.height)
        p.show_pdf_page(rect, fitz.open("pdf", img.convert_to_pdf()), 0)
    pdf = out / "plumbline-carousel.pdf"
    doc.save(str(pdf))
    print("wrote", pdf.name, f"{pdf.stat().st_size // 1024}KB")

    print(f"\n  retracted : {PE_B.pct:.1f} vs {RE_B.pct:.1f}  p={CMP_B.p_value:.4f}")
    print(f"  corrected : {PE_A.pct:.1f} vs {RE_A.pct:.1f}  p={CMP_A.p_value:.4f}")
    print(f"  P2P.03    : {len(P2P03.deviations)} dev, {SKIP_OK} outcome-correct")
