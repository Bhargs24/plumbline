"""
Generate the README figures from the committed run data.

These are regenerated rather than drawn, so a figure can never drift from the
result it depicts. Colours are literal rather than CSS variables because GitHub
strips custom properties from inline SVG in Markdown; the palette is the same
one the HTML report uses, chosen to read on both light and dark backgrounds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from plumbline.analysis.stats import wilson  # noqa: E402
from plumbline.core.trajectory import TrajectoryStore  # noqa: E402

BLUE, ORANGE = "#2a78d6", "#eb6834"
GREY, MUTED, RULE = "#6b7280", "#9aa1a9", "#c9ced4"

PERTS = ["baseline", "paraphrase", "distractor", "decoy_tools", "sampling", "tool_fault"]

#: Three series, because the third is the one that matters. `plan_execute_naive`
#: is the executor with no retry policy, which produced the effect this project
#: originally published. `plan_execute` is the same executor with the error
#: handling a production integration has. The gap between them is the finding.
SERIES = [
    ("naive",  "runs/parity-study", "plan_execute",  "#e0574f", "deterministic, no retry"),
    ("retry",  "runs/retry-study",  "plan_execute",  "#2a78d6", "deterministic, 3 retries"),
    ("react",  "runs/retry-study",  "react",         "#eb6834", "free-form agent"),
]


def _series_flags():
    """Outcome correctness per perturbation for each of the three series."""
    from plumbline.domains.ap.tasks import build_tasks, expected_outcome
    ctx = {t.task_id: t.context for t in build_tasks()}
    out = {}
    for key, run, arm, _colour, _label in SERIES:
        led = json.loads((ROOT / run / "ledger_states.json").read_text(encoding="utf-8"))
        trajs = [t for t in TrajectoryStore(ROOT / run / "trajectories.jsonl").load()
                 if not t.error and t.arm == arm]

        def ok(t, led=led):
            w = expected_outcome(ctx[t.task_id])
            g = led.get(t.trial_id) or {}
            return (bool(g.get("paid")) == w["paid"]
                    and int(g.get("payment_count", 0)) == w["payment_count"]
                    and abs(float(g.get("amount_paid", 0)) - w["amount_paid"]) < 0.005
                    and bool(g.get("exception_raised")) == w["exception_raised"])
        out[key] = {p: [ok(t) for t in trajs if t.perturbation == p] for p in PERTS}
    return out


def results_svg() -> str:
    flags = _series_flags()

    W, H, PAD_L, PAD_R, ROW, TOP = 900, 400, 160, 90, 54, 40
    plot = W - PAD_L - PAD_R
    xmin = 0.60
    def x(v):
        return PAD_L + (max(v, xmin) - xmin) / (1 - xmin) * plot

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" font-family="system-ui,-apple-system,Segoe UI,sans-serif">']
    for t in [x / 100 for x in range(60, 101, 10)]:
        o.append(f'<line x1="{x(t):.0f}" y1="{TOP}" x2="{x(t):.0f}" y2="{TOP+len(PERTS)*ROW}" '
                 f'stroke="{RULE}" stroke-width="1"/>')
        o.append(f'<text x="{x(t):.0f}" y="{TOP+len(PERTS)*ROW+20}" text-anchor="middle" '
                 f'font-size="12" fill="{MUTED}">{t*100:.0f}%</text>')
    for i, p in enumerate(PERTS):
        cy = TOP + i * ROW + ROW / 2
        o.append(f'<text x="{PAD_L-14}" y="{cy+4:.0f}" text-anchor="end" font-size="13" '
                 f'fill="{GREY}">{p.replace("_", " ")}</text>')
        for (key, _run, _arm, colour, _lbl), dy in zip(SERIES, (-13, 0, 13), strict=False):
            f = flags[key][p]
            if not f:
                continue
            w = wilson(sum(f), len(f))
            y = cy + dy
            o.append(f'<line x1="{x(w.lo):.1f}" y1="{y:.0f}" x2="{x(w.hi):.1f}" '
                     f'y2="{y:.0f}" stroke="{colour}" stroke-width="2.5" '
                     f'stroke-linecap="round" opacity="0.5"/>')
            o.append(f'<circle cx="{x(w.value):.1f}" cy="{y:.0f}" r="5.5" '
                     f'fill="{colour}"/>')
            if p == "tool_fault":
                o.append(f'<text x="{x(w.hi)+10:.0f}" y="{y+4:.0f}" font-size="12.5" '
                         f'font-weight="600" fill="{colour}">{w.pct:.1f}%</text>')
    o.append(f'<line x1="{PAD_L}" y1="{TOP+len(PERTS)*ROW}" x2="{W-PAD_R}" '
             f'y2="{TOP+len(PERTS)*ROW}" stroke="{RULE}" stroke-width="1"/>')
    lx = PAD_L
    for _key, _run, _arm, colour, label in SERIES:
        o.append(f'<circle cx="{lx}" cy="16" r="5" fill="{colour}"/>'
                 f'<text x="{lx+12}" y="20" font-size="12" fill="{GREY}">'
                 f'{label}</text>')
        lx += 22 + len(label) * 6.6
    o.append('</svg>')
    return "\n".join(o)


def architecture_svg() -> str:
    W, H = 880, 380
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" font-family="system-ui,-apple-system,Segoe UI,sans-serif">',
         f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
         f'markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/>'
         f'</marker></defs>']

    def box(bx, by, bw, bh, title, sub, accent=GREY, dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        o.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5" fill="none" '
                 f'stroke="{accent}" stroke-width="1.5"{dash}/>')
        o.append(f'<text x="{bx+bw/2}" y="{by+23}" text-anchor="middle" font-size="13.5" '
                 f'font-weight="600" fill="{accent}">{title}</text>')
        for k, line in enumerate(sub):
            o.append(f'<text x="{bx+bw/2}" y="{by+42+k*15}" text-anchor="middle" '
                     f'font-size="11.5" fill="{MUTED}">{line}</text>')

    def arrow(x1, y1, x2, y2):
        o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{MUTED}" '
                 f'stroke-width="1.5" marker-end="url(#a)"/>')

    def label(lx, ly, text):
        o.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="10.5" '
                 f'fill="{MUTED}" font-style="italic">{text}</text>')

    box(20, 40, 175, 92, "Your agent", ["any architecture,", "any framework"], BLUE)
    box(20, 168, 175, 92, "OpenTelemetry", ["GenAI or OpenInference", "spans, already emitted"],
        BLUE, dashed=True)
    box(245, 100, 175, 100, "Perturbation", ["reword · inject fault", "distract · decoy tools",
                                             "sample"], ORANGE)
    box(470, 100, 165, 100, "Trajectories", ["every step, every", "argument, every", "error"], GREY)
    box(685, 22, 175, 96, "Conformance", ["did it obey its", "declared invariants"], GREY)
    box(685, 140, 175, 96, "Consistency", ["same behaviour when", "reworded"], GREY)
    box(685, 258, 175, 96, "Equivalence", ["does it match the", "system it replaces"], ORANGE)

    arrow(195, 86, 240, 130)
    arrow(195, 214, 240, 170)
    arrow(420, 150, 465, 150)
    arrow(635, 145, 680, 90)
    arrow(635, 155, 680, 178)
    arrow(635, 165, 680, 295)
    label(217, 78, "under test")
    label(217, 240, "or ingested")
    label(443, 140, "captured")

    o.append(f'<text x="{W/2}" y="{H-8}" text-anchor="middle" font-size="11" fill="{MUTED}">'
             f'Every output re-derives from stored trajectories with no model calls</text>')
    o.append('</svg>')
    return "\n".join(o)


if __name__ == "__main__":
    out = ROOT / "docs" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.svg").write_text(results_svg(), encoding="utf-8")
    (out / "architecture.svg").write_text(architecture_svg(), encoding="utf-8")
    print("wrote", out / "results.svg")
    print("wrote", out / "architecture.svg")
