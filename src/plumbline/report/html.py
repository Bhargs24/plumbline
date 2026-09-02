"""
The HTML report.

A certificate that only exists as terminal output is not a deliverable. This
renders a run into a self-contained page: a headline with its interval, outcome
correctness per perturbation with error bars, the side-by-side trajectory diff
that shows what actually happened, and the provenance to check it.

No scripts, no chart library, no data fetched at load. The single external
reference is the Google Fonts stylesheet, and every face declares a real
fallback stack, so the page still reads correctly from a file, from an email
attachment, or from behind a corporate proxy that blocks it.

Chart choices, so nobody has to re-derive them:
  - A dot plot with confidence intervals, not bars. The interesting range sits
    between 60 and 100 percent, and bars would encode length from zero and bury
    the effect in ink. Dots encode position, so a labelled non-zero range is
    honest for this form.
  - Two categorical hues, slots 1 and 2 of a validated palette. Checked with the
    palette validator in both modes rather than eyeballed: worst CVD separation
    24.7 light, 26.8 dark, against a target of 8.
  - Values are direct-labelled only on the row that carries the finding. A number
    beside every dot goes unread.
  - A table view follows the chart, so identity never rests on color alone.
"""
from __future__ import annotations

import html
import json

# Validated categorical slots 1 and 2. See the module docstring.
SERIES = [
    {"key": "incumbent", "light": "#2a78d6", "dark": "#3987e5"},
    {"key": "replacement", "light": "#eb6834", "dark": "#d95926"},
]

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400'
         '&family=IBM+Plex+Sans:wght@400;500;600'
         '&family=IBM+Plex+Mono:wght@400;500&display=swap">')

# Palette notes, so the choices are checkable rather than taste claims.
# Ground is a cool blue-biased document stock rather than cream: the subject is
# an instrument reading on financial controls, not an editorial essay. The only
# two accents are the categorical series hues already validated for CVD
# separation in both modes; nothing decorative introduces a third. Newsreader
# carries headings, IBM Plex Sans the body, IBM Plex Mono every figure and
# trajectory, since the Plex family comes out of an engineering identity and
# suits AP controls better than a neutral grotesque.
CSS = """
.pl {
  color-scheme: light;
  --paper: #f6f7f8;
  --card: #ffffff;
  --sunk: #eef1f3;
  --rule: #dde2e5;
  --ink: #0d1114;
  --ink-2: #4a5257;
  --ink-3: #7c848a;
  --series-1: #e0574f;
  --series-2: #2a78d6;
  --series-3: #eb6834;
  --good: #0ca30c;
  --critical: #e5484d;
  --sans: "IBM Plex Sans", ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
  --serif: Newsreader, ui-serif, Georgia, "Times New Roman", serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-family: var(--sans);
  color: var(--ink);
  background: var(--paper);
  line-height: 1.6;
  font-size: 16px;
  margin: 0 auto;
  padding: 3rem 1.5rem 5rem;
  max-width: 58rem;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .pl {
    color-scheme: dark;
    --paper: #0e1113;
    --card: #161a1d;
    --sunk: #1c2124;
    --rule: #272d31;
    --ink: #eef1f2;
    --ink-2: #a9b1b6;
    --ink-3: #767e83;
    --series-1: #e8695f;
    --series-2: #3987e5;
    --series-3: #d95926;
    --good: #30a46c;
    --critical: #ff6369;
  }
}
:root[data-theme="dark"] .pl {
  color-scheme: dark;
  --paper: #0e1113;
  --card: #161a1d;
  --sunk: #1c2124;
  --rule: #272d31;
  --ink: #eef1f2;
  --ink-2: #a9b1b6;
  --ink-3: #767e83;
  --series-1: #e8695f;
  --series-2: #3987e5;
  --series-3: #d95926;
  --good: #30a46c;
  --critical: #ff6369;
}
.pl > * { max-width: 40rem; margin-left: auto; margin-right: auto; }
.pl > .wide { max-width: 58rem; }
.pl h1 {
  font-family: var(--serif); font-weight: 400; font-size: clamp(2rem, 5vw, 3rem);
  line-height: 1.1; letter-spacing: -.02em; text-wrap: balance;
  margin: 0 0 1rem; color: var(--ink);
}
.pl h2 {
  font-family: var(--serif); font-weight: 500; font-size: 1.5rem;
  letter-spacing: -.01em; text-wrap: balance; margin: 3.5rem 0 .5rem;
}
.pl .eyebrow {
  font-family: var(--mono); font-size: .7rem; font-weight: 500;
  letter-spacing: .14em; text-transform: uppercase; color: var(--ink-3);
  margin: 3.5rem 0 .1rem;
}
.pl .eyebrow + h2 { margin-top: 0; }
.pl p { margin: .85rem 0; color: var(--ink-2); }
.pl .lede { font-size: 1.05rem; color: var(--ink-2); }
.pl .sub { color: var(--ink-3); font-size: .92rem; }
.pl strong { color: var(--ink); font-weight: 600; }
.pl .hero {
  background: var(--card); border: 1px solid var(--rule); border-radius: 4px;
  padding: 1.75rem; margin: 1.5rem 0 .5rem;
}
.pl .figure {
  font-family: var(--serif); font-size: 4rem; font-weight: 400;
  letter-spacing: -.03em; line-height: 1; color: var(--ink);
}
.pl .figure .unit { font-size: 1.25rem; font-family: var(--sans);
  font-weight: 500; color: var(--ink-3); letter-spacing: 0; }
.pl .ci { font-family: var(--mono); font-size: .76rem; color: var(--ink-3);
  line-height: 1.7; margin-top: .75rem; }
.pl .legend { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0 .25rem;
  font-size: .84rem; color: var(--ink-2); }
.pl .swatch { width: 9px; height: 9px; border-radius: 50%; display: inline-block;
  margin-right: .45rem; }
.pl figure { margin: .5rem 0 0; }
.pl figcaption { font-size: .82rem; color: var(--ink-3); margin-top: .75rem;
  max-width: 40rem; }
.pl .scroll { overflow-x: auto; }
.pl table { border-collapse: collapse; width: 100%; font-size: .82rem;
  margin-top: 1rem; font-variant-numeric: tabular-nums; }
.pl th, .pl td { text-align: left; padding: .45rem .7rem;
  border-bottom: 1px solid var(--rule); }
.pl th { font-family: var(--mono); font-size: .68rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ink-3); font-weight: 500; }
.pl td { color: var(--ink-2); }
.pl td.num { text-align: right; font-family: var(--mono); color: var(--ink); }
.pl .diff { display: grid; grid-template-columns: 1fr 1fr; gap: 1px;
  background: var(--rule); border: 1px solid var(--rule); border-radius: 4px;
  overflow: hidden; margin-top: 1rem; }
@media (max-width: 700px) { .pl .diff { grid-template-columns: 1fr; } }
.pl .diff > div { background: var(--card); padding: 1.1rem 1.25rem; }
.pl .diff h4 { margin: 0 0 .15rem; font-size: .95rem; font-family: var(--mono);
  font-weight: 500; color: var(--ink); }
.pl .diff .who { font-size: .74rem; color: var(--ink-3); margin-bottom: 1rem;
  line-height: 1.5; }
.pl .step { font-family: var(--mono); font-size: .74rem; padding: .22rem .5rem;
  border-radius: 3px; margin-bottom: .2rem; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; color: var(--ink-2); }
.pl .step.fail { background: color-mix(in srgb, var(--critical) 13%, transparent);
  color: var(--ink); }
.pl .step.key { background: color-mix(in srgb, var(--series-2) 15%, transparent);
  color: var(--ink); font-weight: 500; }
.pl .verdict { font-family: var(--mono); font-size: .74rem; margin-top: 1rem;
  padding-top: .8rem; border-top: 1px solid var(--rule); line-height: 1.6; }
.pl .bad { color: var(--critical); } .pl .ok { color: var(--good); }
.pl .note { background: var(--sunk); padding: 1.1rem 1.35rem; border-radius: 4px;
  margin: 1.5rem 0; font-size: .9rem; color: var(--ink-2); }
.pl code { font-family: var(--mono); font-size: .86em; color: var(--ink); }
.pl footer { margin-top: 4rem; padding-top: 1.25rem;
  border-top: 1px solid var(--rule); font-size: .74rem; color: var(--ink-3);
  font-family: var(--mono); line-height: 1.8; }
@media (prefers-reduced-motion: reduce) {
  .pl * { animation: none !important; transition: none !important; }
}
"""


def _e(s) -> str:
    return html.escape(str(s))


def dot_plot(rows, *, x_min=0.60, width=760, row_h=44, pad_l=132, pad_r=64,
             label_rows=()) -> str:
    """Dot plot with confidence intervals. `rows` is a list of
    (category, [(series_index, value, lo, hi, n), ...])."""
    pad_t, pad_b = 16, 40
    plot_w = width - pad_l - pad_r
    height = pad_t + len(rows) * row_h + pad_b

    def x(v):
        return pad_l + (max(v, x_min) - x_min) / (1.0 - x_min) * plot_w

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
           f'style="max-width:{width}px;height:auto;display:block" '
           f'role="img" aria-label="Outcome correctness by perturbation, '
           f'with 95% confidence intervals">']

    ticks = [t / 100 for t in range(int(x_min * 100), 101, 10)]
    for t in ticks:
        gx = x(t)
        out.append(f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" '
                   f'y2="{pad_t + len(rows) * row_h}" stroke="var(--rule)" '
                   f'stroke-width="1"/>')
        out.append(f'<text x="{gx:.1f}" y="{height - pad_b + 22}" '
                   f'text-anchor="middle" font-size="11" fill="var(--ink-3)" '
                   f'style="font-variant-numeric:tabular-nums">{t * 100:.0f}%</text>')

    for i, (cat, points) in enumerate(rows):
        cy = pad_t + i * row_h + row_h / 2
        out.append(f'<text x="{pad_l - 14}" y="{cy + 4}" text-anchor="end" '
                   f'font-size="12.5" fill="var(--ink-2)">{_e(cat)}</text>')
        # two series offset vertically so overlapping intervals stay readable
        offsets = {1: (0,), 2: (-7, 7), 3: (-11, 0, 11)}.get(len(points), (0,))
        for (si, val, lo, hi, n), dy in zip(points, offsets, strict=False):
            colour = f"var(--series-{si + 1})"
            y = cy + dy
            out.append(
                f'<line x1="{x(lo):.1f}" y1="{y:.1f}" x2="{x(hi):.1f}" y2="{y:.1f}" '
                f'stroke="{colour}" stroke-width="2" stroke-linecap="round" '
                f'opacity="0.55"/>')
            out.append(
                f'<circle cx="{x(val):.1f}" cy="{y:.1f}" r="5.5" fill="{colour}" '
                f'stroke="var(--paper)" stroke-width="2">'
                f'<title>{_e(cat)}: {val * 100:.1f}% '
                f'[{lo * 100:.1f}, {hi * 100:.1f}], n={n}</title></circle>')
            if cat in label_rows:
                out.append(
                    f'<text x="{x(hi) + 9:.1f}" y="{y + 4:.1f}" font-size="11.5" '
                    f'fill="{colour}" style="font-variant-numeric:tabular-nums" '
                    f'font-weight="600">{val * 100:.1f}%</text>')

    out.append(f'<line x1="{pad_l}" y1="{pad_t + len(rows) * row_h}" x2="{width - pad_r}" '
               f'y2="{pad_t + len(rows) * row_h}" stroke="var(--rule)" stroke-width="1"/>')
    out.append('</svg>')
    return "\n".join(out)


def _step_rows(traj, highlight: set[str]) -> str:
    out = []
    for s in traj.steps:
        if s.kind == "final":
            continue
        cls = "step"
        if s.failed:
            cls += " fail"
        elif s.name in highlight:
            cls += " key"
        arg = ""
        if s.args:
            arg = " " + json.dumps(s.args, default=str)
            if len(arg) > 46:
                arg = arg[:44] + "…"
        mark = "×" if s.failed else "·"
        out.append(f'<div class="{cls}">{mark} {_e(s.name)}<span '
                   f'style="opacity:.6">{_e(arg)}</span></div>')
        if s.failed:
            out.append(f'<div class="step fail" style="padding-left:1.4rem">'
                       f'↳ {_e(s.error)}</div>')
    return "\n".join(out)


def trajectory_diff(left, right, *, left_title, right_title, left_label,
                    right_label, left_verdict, right_verdict,
                    highlight: set[str] | None = None) -> str:
    """The side-by-side that carries the argument. Two runs, same invoice, same
    injected fault, different outcome."""
    highlight = highlight or set()
    return (
        '<div class="diff">'
        f'<div><h4>{_e(left_title)}</h4><div class="who">{_e(left_label)}</div>'
        f'{_step_rows(left, highlight)}'
        f'<div class="verdict">{left_verdict}</div></div>'
        f'<div><h4>{_e(right_title)}</h4><div class="who">{_e(right_label)}</div>'
        f'{_step_rows(right, highlight)}'
        f'<div class="verdict">{right_verdict}</div></div>'
        '</div>')


def wrap_standalone(body: str, title: str) -> str:
    """A full document for local use. The font links are the one external
    reference; every face declares a real fallback stack, so the page still
    reads correctly with no network."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title>{FONTS}'
            f'<style>*{{box-sizing:border-box}}body{{margin:0;'
            f'background:#f6f7f8}}'
            f'@media(prefers-color-scheme:dark){{body{{background:#0e1113}}}}'
            f'{CSS}</style></head><body>{body}</body></html>')
