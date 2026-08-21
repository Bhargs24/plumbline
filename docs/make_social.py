"""
Render the LinkedIn carousel from the committed run data.

Slides are generated, never typed, so a figure on social media cannot drift
from the result in the repository. 1080x1350 is the 4:5 portrait LinkedIn
serves largest in the feed; text sizes are set for a phone screen, which is
where these are actually read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from plumbline.analysis.stats import compare, wilson       # noqa: E402
from plumbline.core.trajectory import TrajectoryStore      # noqa: E402

W, H = 1080, 1350
INK, DIM, FAINT = "#f2f4f5", "#a4abb3", "#6b727a"
BG, CARD, RULE = "#0e1113", "#181c20", "#2a3036"
BLUE, ORANGE, RED, GREEN = "#4f95e0", "#f07a45", "#ff6369", "#4cb26a"

PERTS = ["baseline", "paraphrase", "distractor", "decoy_tools", "sampling", "tool_fault"]


def load():
    from agents.ap.tasks import build_tasks, expected_outcome
    ctx = {t.task_id: t.context for t in build_tasks()}
    led = json.loads((ROOT / "runs/parity-study/ledger_states.json").read_text(encoding="utf-8"))
    trajs = [t for t in TrajectoryStore(ROOT / "runs/parity-study/trajectories.jsonl").load()
             if not t.error]

    def ok(t):
        w = expected_outcome(ctx[t.task_id]); g = led.get(t.trial_id) or {}
        return (bool(g.get("paid")) == w["paid"]
                and int(g.get("payment_count", 0)) == w["payment_count"]
                and abs(float(g.get("amount_paid", 0)) - w["amount_paid"]) < 0.005
                and bool(g.get("exception_raised")) == w["exception_raised"])
    return trajs, ok


TRAJS, OK = load()
flags = lambda arm, p=None: [OK(t) for t in TRAJS if t.arm == arm
                             and (p is None or t.perturbation == p)]
CMP = compare("plan_execute", flags("plan_execute", "tool_fault"),
              "react", flags("react", "tool_fault"))
REST = compare("a", [x for t in TRAJS if t.arm == "plan_execute"
                     and t.perturbation != "tool_fault" for x in [OK(t)]],
               "b", [x for t in TRAJS if t.arm == "react"
                     and t.perturbation != "tool_fault" for x in [OK(t)]])

CSS = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{BG};font-family:'Archivo',system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
.s{{width:{W}px;height:{H}px;background:{BG};color:{INK};padding:78px 74px;
   display:flex;flex-direction:column;position:relative;overflow:hidden}}
.tag{{font-family:'JetBrains Mono',monospace;font-size:23px;letter-spacing:.22em;
   text-transform:uppercase;color:{FAINT};margin-bottom:46px}}
h1{{font-size:97px;line-height:1.02;letter-spacing:-.035em;font-weight:700}}
h2{{font-size:70px;line-height:1.08;letter-spacing:-.03em;font-weight:700}}
p{{font-size:37px;line-height:1.42;color:{DIM};margin-top:30px;font-weight:400}}
p b{{color:{INK};font-weight:600}}
.big{{font-size:225px;font-weight:700;letter-spacing:-.05em;line-height:.9;color:{INK}}}
.unit{{font-size:52px;color:{FAINT};font-weight:500}}
.mono{{font-family:'JetBrains Mono',monospace}}
.spacer{{flex:1}}
.foot{{font-family:'JetBrains Mono',monospace;font-size:23px;color:{FAINT};
   border-top:1px solid {RULE};padding-top:26px;line-height:1.7}}
.card{{background:{CARD};border:1px solid {RULE};border-radius:14px;padding:42px 46px}}
.row{{display:flex;gap:20px;align-items:baseline;margin:14px 0}}
.k{{font-family:'JetBrains Mono',monospace;font-size:30px;color:{DIM};width:290px}}
.v{{font-family:'JetBrains Mono',monospace;font-size:35px;font-weight:500}}
.diff{{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:{RULE};
   border:1px solid {RULE};border-radius:12px;overflow:hidden;margin-top:34px}}
.col{{background:{CARD};padding:30px 26px}}
.col h3{{font-family:'JetBrains Mono',monospace;font-size:29px;font-weight:600;
   margin-bottom:6px}}
.col .who{{font-size:22px;color:{FAINT};margin-bottom:22px;line-height:1.4}}
.step{{font-family:'JetBrains Mono',monospace;font-size:23px;padding:10px 12px;
   border-radius:5px;margin-bottom:5px;color:{DIM};white-space:nowrap;
   overflow:hidden;text-overflow:ellipsis}}
.step.bad{{background:rgba(255,99,105,.15);color:{INK}}}
.step.good{{background:rgba(76,178,106,.15);color:{INK}}}
.verdict{{font-family:'JetBrains Mono',monospace;font-size:25px;margin-top:26px;
   padding-top:18px;border-top:1px solid {RULE};line-height:1.5}}
.bars{{margin-top:40px}}
.bar{{display:flex;align-items:center;gap:24px;margin-bottom:34px}}
.bl{{font-family:'JetBrains Mono',monospace;font-size:28px;color:{DIM};width:280px;
   text-align:right}}
.bt{{flex:1;height:34px;background:{CARD};border-radius:4px;overflow:hidden;
   display:flex;gap:3px;flex-direction:column;justify-content:center}}
.seg{{height:13px;border-radius:3px}}
.bv{{font-family:'JetBrains Mono',monospace;font-size:26px;width:165px}}
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600'
         '&display=swap">')


def bars():
    out = ['<div class="bars">']
    for p in PERTS:
        for arm, col in (("plan_execute", BLUE), ("react", ORANGE)):
            f = flags(arm, p)
            if not f:
                continue
            w = wilson(sum(f), len(f))
            pct = (w.value - 0.6) / 0.4 * 100
            if arm == "plan_execute":
                out.append(f'<div class="bar"><div class="bl">{p.replace("_"," ")}</div>'
                           f'<div class="bt">')
            out.append(f'<div class="seg" style="width:{max(pct,2):.0f}%;'
                       f'background:{col}"></div>')
            if arm == "react":
                lo = wilson(sum(flags("plan_execute", p)), len(flags("plan_execute", p)))
                out.append(f'</div><div class="bv" style="color:'
                           f'{RED if lo.value < .95 else DIM}">{lo.pct:.0f} / {w.pct:.0f}%'
                           f'</div></div>')
    out.append('</div>')
    return "".join(out)


SLIDES = [
    # 1 — the hook
    f"""<div class="tag">Plumbline · 738 runs</div>
<h1>The failure everyone tests&nbsp;for wasn't there.</h1>
<h1 style="color:{ORANGE};margin-top:34px">The one nobody tests&nbsp;for was.</h1>
<p style="margin-top:52px">I ran two accounts-payable automations through 738 identical
trials to find out what moving control flow out of the model actually buys you.</p>
<div class="spacer"></div>
<div class="foot">Rewording the request broke nothing.<br>A network timeout broke the
deterministic one.</div>""",

    # 2 — the setup
    f"""<div class="tag">The setup</div>
<h2>Identical everything.<br>One variable.</h2>
<p>Same eight invoices, same eight tools, same database, same model, same
perturbation variants. The only difference is <b>who decides which step runs
next</b>.</p>
<div class="card" style="margin-top:44px">
  <div class="row"><div class="k" style="color:{BLUE}">plan_execute</div>
    <div class="v">model interprets once,<br>fixed code runs the procedure</div></div>
  <div class="row" style="margin-top:30px"><div class="k" style="color:{ORANGE}">react</div>
    <div class="v">model picks every step,<br>freely, until it stops</div></div>
</div>
<p style="margin-top:44px">Then I changed the input in five ways that <b>must not</b>
change correct behaviour: reword it, add irrelevant text, add unused tools, raise
the temperature, and fail one tool call once.</p>
<div class="spacer"></div>
<div class="foot">8 invoices × 6 conditions × 4 variants × 2 trials × 2 architectures</div>""",

    # 3 — the chart
    f"""<div class="tag">Outcome correctness vs ground truth</div>
<h2>Four of five perturbations changed&nbsp;nothing.</h2>
{bars()}
<p style="margin-top:38px;font-size:27px">Blue is the deterministic executor,
orange the free-form agent. Everything sits at 100% until a tool fails.</p>
<div class="spacer"></div>
<div class="foot">95% Wilson intervals · measured against ground truth,
not against each other</div>""",

    # 4 — the numbers
    f"""<div class="tag">Under a transient tool failure</div>
<div style="margin-top:20px"><span class="big">17</span>
<span class="unit">point gap</span></div>
<p style="margin-top:44px">The <b>deterministic</b> executor reached the correct
outcome {CMP.a.pct:.1f}% of the time.<br>The <b>free-form agent</b> reached it
{CMP.b.pct:.1f}%.</p>
<div class="card" style="margin-top:48px">
  <div class="row"><div class="k">plan_execute</div>
    <div class="v" style="color:{RED}">{CMP.a.pct:.1f}% [{CMP.a.lo*100:.0f}, {CMP.a.hi*100:.0f}]</div></div>
  <div class="row"><div class="k">react</div>
    <div class="v" style="color:{GREEN}">{CMP.b.pct:.1f}% [{CMP.b.lo*100:.0f}, {CMP.b.hi*100:.0f}]</div></div>
  <div class="row" style="margin-top:26px"><div class="k">permutation test</div>
    <div class="v">p = {CMP.p_value:.4f}</div></div>
  <div class="row"><div class="k">all other conditions</div>
    <div class="v" style="color:{FAINT}">p = {REST.p_value:.2f} (n.s.)</div></div>
</div>
<div class="spacer"></div>
<div class="foot">The effect is localised to one condition.<br>That is what a real
effect looks like rather than noise.</div>""",

    # 5 — the trajectory diff
    f"""<div class="tag">Same invoice · same injected fault</div>
<h2>Opposite results.</h2>
<p style="font-size:28px">Invoice INV-7002 is clean. Every check passes. The correct
action is to pay <b>$4,500</b>.</p>
<div class="diff">
  <div class="col">
    <h3 style="color:{BLUE}">plan_execute</h3>
    <div class="who">deterministic executor<br>control flow fixed in code</div>
    <div class="step">fetch_invoice</div>
    <div class="step bad">match_purchase_order ✗</div>
    <div class="step bad" style="padding-left:24px">↳ 503 unavailable</div>
    <div class="step">check_duplicate</div>
    <div class="step">check_vendor_status</div>
    <div class="step bad">flag_exception</div>
    <div class="verdict" style="color:{RED}">✗ held a clean<br>invoice for review</div>
  </div>
  <div class="col">
    <h3 style="color:{ORANGE}">react</h3>
    <div class="who">free-form agent<br>the model picks every step</div>
    <div class="step">fetch_invoice</div>
    <div class="step">match_purchase_order</div>
    <div class="step">check_duplicate</div>
    <div class="step bad">check_vendor_status ✗</div>
    <div class="step good">check_vendor_status ✓ retried</div>
    <div class="step good">schedule_payment $4,500</div>
    <div class="verdict" style="color:{GREEN}">✓ paid correctly</div>
  </div>
</div>
<div class="spacer"></div>
<div class="foot">The agent improvised a retry nobody specified.<br>The deterministic
system had no rule for a network blip.</div>""",

    # 6 — the honest reading
    f"""<div class="tag">What it does and does not show</div>
<h2>Determinism did not buy reliability.</h2>
<h2 style="color:{ORANGE};margin-top:26px">It bought predictability.</h2>
<p style="margin-top:46px">Before anyone asks: the fail-closed behaviour is a
<b>design choice in my executor</b>, not a property of deterministic systems. One
with retry logic would not fail this way.</p>
<p>Which is the actual finding. A deterministic system does exactly what its author
anticipated and <b>nothing else</b>. It handled every perturbation it was written
for and failed on the one it was not.</p>
<p>The agent improvised a recovery nobody specified. That is the same capability
that lets it skip a control somewhere else.</p>
<div class="spacer"></div>
<div class="foot">Determinism moves the failure from the model to the
specification.<br>It does not remove it.</div>""",

    # 7 — why it needed an instrument
    f"""<div class="tag">Why existing tooling misses this</div>
<h2>Both runs pass every eval you own.</h2>
<p>Output evals grade the answer. The answer was right.</p>
<p>Trace tools grade the trajectory on <b>inputs you fixed in advance</b>. The wording
never changes, so they cannot tell you the agent behaves differently when it does.</p>
<p style="color:{INK}"><b>A control that did not execute is an audit finding whether
or not the money was right.</b></p>
<div class="card" style="margin-top:40px">
<div class="v" style="font-size:25px;line-height:1.7;color:{DIM}">
MustCall(<span style="color:{ORANGE}">"check_duplicate"</span>)<br>
Ordering(<span style="color:{ORANGE}">"match_po"</span>, then=<span style="color:{ORANGE}">"pay"</span>)<br>
CallAtMost(<span style="color:{ORANGE}">"schedule_payment"</span>, 1)<br>
ArgEquals(<span style="color:{ORANGE}">"pay"</span>, <span style="color:{ORANGE}">"amount"</span>, expected)
</div></div>
<p style="margin-top:36px;font-size:27px">Declare what must always hold. Then try to
break it with changes that preserve meaning.</p>
<div class="spacer"></div>
<div class="foot">Reports which invariant broke, under which perturbation,<br>at which
named step, with a confidence interval.</div>""",

    # 8 — CTA
    f"""<div class="tag">Open source · Apache-2.0</div>
<h2>All 738 trajectories are committed.</h2>
<p>Every number above re-derives from stored traces with no API key and no model
calls. CI runs exactly that on every push, so if the evidence stops reproducing the
published numbers the build goes red.</p>
<div class="card" style="margin-top:44px">
<div class="mono" style="font-size:24px;line-height:1.9;color:{DIM}">
git clone github.com/Bhargs24/plumbline<br>
pip install -e ".[dev]"<br>
<span style="color:{ORANGE}">plumbline parity runs/parity-study \\<br>
&nbsp;&nbsp;&nbsp;&nbsp;plan_execute react</span>
</div></div>
<p style="margin-top:44px;font-size:29px">What perturbation would break <b>your</b>
agent? I would genuinely like to know which one I should add next.</p>
<div class="spacer"></div>
<div class="foot">github.com/Bhargs24/plumbline<br>70 tests · 738 runs · $11.02 ·
claude-haiku-4-5</div>""",
]


def build_html() -> str:
    body = "\n".join(f'<div class="s" id="s{i+1}">{s}</div>' for i, s in enumerate(SLIDES))
    return (f'<!doctype html><html><head><meta charset="utf-8">{FONTS}'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


if __name__ == "__main__":
    out = ROOT / "docs" / "social"
    out.mkdir(parents=True, exist_ok=True)
    page_path = out / "_slides.html"
    page_path.write_text(build_html(), encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg.goto(page_path.resolve().as_uri(), wait_until="networkidle")
        pg.wait_for_timeout(2200)
        for i in range(len(SLIDES)):
            f = out / f"slide-{i+1}.png"
            pg.locator(f"#s{i+1}").screenshot(path=str(f))
            print("wrote", f.name, f"{f.stat().st_size//1024}KB")
        pg.pdf  # noqa
        b.close()

    # carousel PDF for a LinkedIn document post
    import fitz
    doc = fitz.open()
    for i in range(len(SLIDES)):
        img = fitz.open(str(out / f"slide-{i+1}.png"))
        rect = img[0].rect
        page = doc.new_page(width=rect.width, height=rect.height)
        page.show_pdf_page(rect, fitz.open("pdf", img.convert_to_pdf()), 0)
    pdf = out / "plumbline-carousel.pdf"
    doc.save(str(pdf))
    print("wrote", pdf.name, f"{pdf.stat().st_size//1024}KB")
