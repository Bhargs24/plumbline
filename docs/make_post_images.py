"""
Render standalone images for a LinkedIn image post.

Different constraints from the carousel. A document post is read in order, so
a slide can assume the one before it. An image post is not: LinkedIn may show
one image in the feed, a reader may open the third one first, and each has to
carry its own context. So every image here repeats who made it and what it is,
states its own claim, and does not refer to any other image.

Figures are imported from make_social so there is exactly one place a number
can be wrong. 1080x1350 is the 4:5 portrait LinkedIn serves largest.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("ms", ROOT / "docs" / "make_social.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

W, H = 1080, 1350
BG, PANE, RULE = "#ffffff", "#f4f5f7", "#d5dae0"
INK, DIM, FAINT = "#11151a", "#4d565f", "#7d868f"
GRN, RED, CYN = "#116b43", "#b0202c", "#12508f"

CSS = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{BG}}}
.s{{width:{W}px;height:{H}px;background:{BG};color:{INK};padding:56px 54px 44px;
   display:flex;flex-direction:column;gap:26px;overflow:hidden;
   font-family:'IBM Plex Sans','Inter',system-ui,sans-serif;
   -webkit-font-smoothing:antialiased}}
.kick{{flex:none;font-family:'JetBrains Mono',monospace;font-size:19px;
   letter-spacing:.13em;text-transform:uppercase;color:{CYN};font-weight:500}}
h1{{flex:none;font-size:60px;line-height:1.07;font-weight:700;
   letter-spacing:-.026em;text-wrap:balance}}
h1 em{{font-style:normal;color:{RED}}}
.sub{{flex:none;font-size:29px;line-height:1.36;color:{DIM}}}
.sub b{{color:{INK};font-weight:600}}
.box{{flex:none;background:{PANE};border-left:7px solid {INK};padding:28px 32px}}
.box.bad{{border-left-color:{RED}}}
.box.ok{{border-left-color:{GRN}}}
.lbl{{font-family:'JetBrains Mono',monospace;font-size:19px;letter-spacing:.11em;
   text-transform:uppercase;color:{FAINT};margin-bottom:16px}}
.lbl .sym{{text-transform:none}}
pre{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:26px;
   line-height:1.6;color:{DIM};white-space:pre;overflow:hidden}}
pre b{{color:{INK};font-weight:600}}
.g{{color:{GRN}}} .r{{color:{RED}}} .f{{color:{FAINT}}}
.duo{{flex:none;display:flex;gap:24px}}
.duo>div{{flex:1;background:{PANE};border-left:7px solid {GRN};padding:28px 30px}}
.duo>div.bad{{border-left-color:{RED}}}
.duo .n{{font-family:'JetBrains Mono',monospace;font-size:96px;font-weight:600;
   line-height:1;letter-spacing:-.035em;color:{GRN}}}
.duo .bad .n,.duo>div.bad .n{{color:{RED}}}
.duo .c{{font-size:24px;color:{DIM};margin-top:14px;line-height:1.34}}
.duo .c b{{color:{INK};font-weight:600}}
.hero{{flex:none;font-family:'JetBrains Mono',monospace;font-size:132px;
   font-weight:600;letter-spacing:-.04em;line-height:1;color:{RED}}}
.hero small{{display:block;font-family:'IBM Plex Sans',sans-serif;font-size:27px;
   font-weight:400;color:{DIM};letter-spacing:0;margin-top:16px;line-height:1.35}}
.fml{{flex:none;background:{PANE};padding:30px;text-align:center;
   font-family:'JetBrains Mono',monospace;font-size:36px;color:{INK}}}
.fml small{{display:block;font-size:21px;color:{FAINT};margin-top:14px}}
.sp{{flex:1}}
.ft{{flex:none;display:flex;justify-content:space-between;align-items:baseline;
   border-top:2px solid {INK};padding-top:14px;
   font-family:'JetBrains Mono',monospace;font-size:19px;color:{FAINT}}}
.ft b{{color:{INK};font-weight:600;letter-spacing:.06em}}
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=IBM+Plex+Sans:wght@400;600;700&family=JetBrains+Mono:'
         'wght@400;500;600&display=swap">')


def foot(tag: str) -> str:
    """Every image is self-contained, so every image is signed."""
    return (f'<div class="ft"><span><b>PLUMBLINE</b> &nbsp; {tag}</span>'
            f'<span>github.com/Bhargs24/plumbline</span></div>')


IMAGES = [
    # 1 ------------------------------------------------ the finding, standalone
    f"""<div class="kick">AI agents in accounts payable</div>
<h1>It got every invoice right.<br>A mandatory control<br><em>never ran</em>.</h1>
<div class="duo">
  <div><div class="lbl">output eval</div>
    <div class="n">{ms.REACT_OUT.pct:.1f}%</div>
    <div class="c"><b>PASS.</b> Right amount, right vendor, duplicates held.</div></div>
  <div class="bad"><div class="lbl">test of controls</div>
    <div class="n">FAIL</div>
    <div class="c"><b>DEFICIENT.</b> Supplier validation did not execute.</div></div>
</div>
<div class="box bad"><pre>on its worst invoice, the agent skipped
<b>check_vendor_status</b> in <span class="r">{ms.SKIP_N} of {ms.SKIP_RUNS} runs</span>
across all {ms.P2P03.assessment.tested} runs, <span class="r">{ms.P2P03.assessment.deviation_rate * 100:.1f}%</span> <span class="f">&mdash; tolerable rate</span> <b>0%</b>

<span class="f">every one of those runs still produced</span>
<span class="f">the correct answer, so every eval passed</span></pre></div>
<div class="box"><div class="lbl">it logged the skip itself &mdash; verbatim</div>
<pre><b>"Check 4 (check_vendor_status):</b>
<b> Not run due to prior failure."</b></pre></div>
<div class="sp"></div>
{foot('the finding')}""",

    # 2 ------------------------------------------- it was told not to, verbatim
    f"""<div class="kick">The instruction it was given</div>
<h1>Told not to skip it, in<br>the exact words.</h1>
<div class="box"><div class="lbl">operating policy, handed to the agent</div>
<pre><span class="f">"For EVERY invoice, without exception, run</span>
<span class="f">all four checks before deciding anything...</span>
<span class="f">even when an earlier check has already told</span>
<span class="f">you what the disposition will be.</span>

<b>A control you skip because you predicted</b>
<b>its result is a control that did not run."</b></pre></div>
<div class="box bad"><div class="lbl">what it did on {ms.SKIP_TASK}</div>
<div class="hero">{100 * ms.SKIP_N / ms.SKIP_RUNS:.0f}%<small>of runs skipped the supplier check anyway.
The other {ms.SCENARIOS - 2} invoices: zero skips.</small></div></div>
<div class="sp"></div>
{foot('not a prompting gap')}""",

    # 3 ------------------------------------------------- the sampling arithmetic
    f"""<div class="kick">Why one test is not enough</div>
<h1>Auditors test automation<br>once. Once bounds<br>almost <em>nothing</em>.</h1>
<div class="fml">n &nbsp;&ge;&nbsp; ln(1 &minus; &alpha;) / ln(1 &minus; p<sub>tol</sub>)
<small>clean runs needed to bound the failure rate, at confidence &alpha;</small></div>
<div class="box bad"><div class="lbl">what a clean test proves &nbsp;&middot;&nbsp; <span class="sym">&alpha;</span> = 95%</div>
<pre><span class="f">failure rate could still be as high as</span>

<b>  1</b> clean run  .....  <span class="r">{ms.BOUND1:.0f}%</span>
<b> 30</b> clean runs ....  <span class="r">{ms.BOUND30:.1f}%</span>
<b>{ms.N99}</b> clean runs ....  <span class="g">1.0%</span></pre></div>
<div class="sub">PCAOB allows test-of-one because conventional automation is
deterministic, so one run generalises. <b>A language model does not.</b></div>
<div class="box"><pre><b>Why this is a cost problem, not a purity problem.</b>
<span class="f">a control an auditor cannot rely on is one you</span>
<span class="f">must staff a human against, which removes the</span>
<span class="f">reason to automate it in the first place.</span></pre></div>
<div class="sp"></div>
{foot('the evidence gap')}""",

    # 4 ------------------------------------------------------ what comes out
    f"""<div class="kick">What the harness produces</div>
<h1>A control test, not<br>a dashboard.</h1>
<div class="box"><div class="lbl"><span class="sym">$ plumbline attest --arm react</span></div>
<pre><span class="f">CONTROL  NAME                 DEV  CONCL</span>
P2P.01   Three-way match        0  <span class="g">EFFECTIVE</span>
P2P.02   Duplicate detection    0  <span class="g">EFFECTIVE</span>
P2P.03   Supplier validation   <b>18</b>  <span class="r">DEFICIENT</span>
P2P.05   Disbursement accuracy  0  <span class="g">EFFECTIVE</span>
P2P.06   Exception disposition  2  <span class="r">DEFICIENT</span></pre></div>
<div class="box"><pre>every deviation names the <b>run</b>, the
<b>transaction</b> and the <b>condition</b>, and routes
to an owner with an SLA.

exit code <span class="r">1</span> on a deficiency, so CI blocks
the release.</pre></div>
<div class="sub"><b>{ms.ALL_RUNS:,} live runs</b> against Claude Haiku 4.5, every
trajectory committed and independently checkable.</div>
<div class="sp"></div>
{foot('the deliverable')}""",
]


def build_html() -> str:
    body = "\n".join(f'<div class="s" id="i{i+1}">{s}</div>'
                     for i, s in enumerate(IMAGES))
    return (f'<!doctype html><html><head><meta charset="utf-8">{FONTS}'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


if __name__ == "__main__":
    out = ROOT / "docs" / "social" / "post"
    out.mkdir(parents=True, exist_ok=True)
    page = out / "_images.html"
    page.write_text(build_html(), encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg.goto(page.resolve().as_uri(), wait_until="networkidle")
        pg.wait_for_timeout(2500)
        for i in range(len(IMAGES)):
            f = out / f"post-{i+1}.png"
            pg.locator(f"#i{i+1}").screenshot(path=str(f))
            m = pg.evaluate(
                "((id) => {const e=document.getElementById(id);"
                "const over=e.scrollHeight-e.clientHeight;const wide=[];"
                "e.querySelectorAll('pre').forEach(el=>{"
                " if(el.scrollWidth>el.clientWidth+1)"
                "   wide.push(el.scrollWidth-el.clientWidth);});"
                "return {over:over, wide:wide.length?Math.max(...wide):0};})('i%d')"
                % (i + 1))
            flags = ""
            if m["over"] > 2:
                flags += f"  OVERFLOW +{m['over']:.0f}px"
            if m["wide"] > 0:
                flags += f"  CLIPPED +{m['wide']:.0f}px WIDE"
            print(f"wrote {f.name}  {f.stat().st_size // 1024}KB{flags or '  ok'}")
        b.close()
