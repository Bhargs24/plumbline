"""
The web console.

Server-rendered HTML with no build step and no framework. A finance team's
security review is a great deal shorter when the answer to "what JavaScript
does this ship" is "none".

This is an operations console rather than a document, so the information design
follows: summary before detail, state encoded in form as well as number, and
whatever needs attention readable at a glance without parsing a table.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


CSS = """
.panel{border:1px solid var(--rule);border-radius:6px;padding:14px 16px;margin:14px 0;background:var(--panel)}
.panel h3{margin:0 0 6px;font-size:13px;letter-spacing:.02em}
.panel.warn{border-left:3px solid var(--high)}
.panel p{margin:4px 0;line-height:1.5}
ul.ex{margin:8px 0 0;padding-left:18px}
ul.ex li{margin:3px 0}
table.tight td{padding:2px 10px 2px 0;border:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}

:root{
  color-scheme:dark;
  --bg:#0d1013; --panel:#151a1f; --panel-2:#1b2127; --rule:#252c34;
  --ink:#e8ecf0; --ink-2:#98a2ad; --ink-3:#69737e;
  --accent:#4f95e0; --accent-2:#f07a45;
  --crit:#ff5f6d; --high:#f0913f; --med:#e3c34a; --low:#6b7684; --ok:#3fb974;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.55 var(--sans)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
header{border-bottom:1px solid var(--rule);background:var(--panel);
  padding:0 22px;display:flex;align-items:center;gap:22px;height:52px;
  position:sticky;top:0;z-index:10}
header .brand{font-weight:700;letter-spacing:-.02em;font-size:15px}
header .brand span{color:var(--accent-2)}
header nav{display:flex;gap:18px;font-size:13px}
header .right{margin-left:auto;font:11px var(--mono);color:var(--ink-3)}
main{max-width:1400px;margin:0 auto;padding:24px 22px 80px}
h1{font-size:21px;font-weight:650;letter-spacing:-.02em;margin-bottom:3px}
h2{font-size:13px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink-3);margin:30px 0 10px}
.sub{color:var(--ink-3);font-size:12.5px;font-family:var(--mono)}
.crumb{font:11px var(--mono);color:var(--ink-3);margin-bottom:14px}
.grid{display:grid;gap:12px}
.g4{grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:7px;
  padding:16px 18px}
.card .k{font:10.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:7px}
.card .v{font-size:27px;font-weight:650;letter-spacing:-.02em;line-height:1.05}
.card .n{font-size:11.5px;color:var(--ink-3);margin-top:5px;font-family:var(--mono)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font:10.5px var(--mono);letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);font-weight:600;
  padding:8px 11px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:8px 11px;border-bottom:1px solid var(--rule);color:var(--ink-2);
  vertical-align:top}
tr:hover td{background:var(--panel)}
td.mono,th.mono{font-family:var(--mono);font-size:11.5px}
td.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;
  color:var(--ink)}
.wrap{background:var(--panel);border:1px solid var(--rule);border-radius:7px;
  overflow:hidden}
.scroll{overflow-x:auto}
.pill{display:inline-block;font:10.5px var(--mono);letter-spacing:.05em;
  padding:2px 7px;border-radius:4px;text-transform:uppercase;font-weight:600}
.pill.critical{background:rgba(255,95,109,.16);color:var(--crit)}
.pill.high{background:rgba(240,145,63,.16);color:var(--high)}
.pill.medium{background:rgba(227,195,74,.14);color:var(--med)}
.pill.low{background:rgba(107,118,132,.18);color:var(--ink-2)}
.pill.ok{background:rgba(63,185,116,.15);color:var(--ok)}
.pill.run{background:rgba(79,149,224,.15);color:var(--accent)}
.bar{height:5px;background:var(--panel-2);border-radius:3px;overflow:hidden;
  min-width:90px}
.bar > i{display:block;height:100%;border-radius:3px}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.filters a{font:11px var(--mono);padding:4px 10px;border:1px solid var(--rule);
  border-radius:5px;color:var(--ink-2);background:var(--panel)}
.filters a.on{border-color:var(--accent);color:var(--accent);
  background:rgba(79,149,224,.1)}
.steps{font-family:var(--mono);font-size:12px}
.step{display:flex;gap:11px;padding:6px 11px;border-bottom:1px solid var(--rule);
  align-items:baseline}
.step:last-child{border-bottom:0}
.step .i{color:var(--ink-3);width:26px;text-align:right;flex:none;font-size:11px}
.step .nm{min-width:210px;flex:none}
.step .ar{color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;flex:1}
.step.fail{background:rgba(255,95,109,.09)}
.step.fail .nm{color:var(--crit)}
.step.blocked{background:rgba(240,145,63,.09)}
.step.blocked .nm{color:var(--high)}
.step.money .nm{color:var(--ok);font-weight:600}
.err{color:var(--crit);font-size:11px;padding:2px 11px 7px 48px;
  font-family:var(--mono)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1000px){.two{grid-template-columns:1fr}}
.empty{padding:34px;text-align:center;color:var(--ink-3);font-size:13px}
.final{background:var(--panel-2);border-radius:6px;padding:12px 14px;
  font-size:12.5px;color:var(--ink-2);white-space:pre-wrap;max-height:230px;
  overflow:auto}
code{font-family:var(--mono);font-size:11.5px;background:var(--panel-2);
  padding:1px 5px;border-radius:3px}
"""


def _page(title: str, body: str, crumb: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} — Plumbline</title><style>{CSS}</style></head><body>
<header>
  <div class="brand">plumb<span>line</span></div>
  <nav><a href="/">Runs</a><a href="/api/health">API</a></nav>
  <div class="right">conformance under perturbation</div>
</header>
<main>{f'<div class="crumb">{crumb}</div>' if crumb else ''}{body}</main>
</body></html>"""


def _clip(text: str, n: int) -> str:
    """Truncate on a word boundary. Cutting mid-word reads as a broken
    template rather than an intentional summary."""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def _pct_bar(value: float | None, colour: str = "var(--accent)") -> str:
    if value is None:
        return '<span class="sub">n/a</span>'
    w = max(0.0, min(1.0, value)) * 100
    return f'<div class="bar"><i style="width:{w:.1f}%;background:{colour}"></i></div>'


def _grade_colour(bound: float | None) -> str:
    if bound is None:
        return "var(--ink-3)"
    if bound >= 0.95:
        return "var(--ok)"
    if bound >= 0.85:
        return "var(--med)"
    if bound >= 0.60:
        return "var(--high)"
    return "var(--crit)"


def _ts(x: str) -> str:
    try:
        return datetime.fromisoformat(x).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        return e(x)


# ==========================================================================
def dashboard(projects: list[dict], runs: list[dict]) -> str:
    if not runs:
        body = ('<h1>No runs yet</h1><p class="sub">Execute a study, then it '
                'appears here.</p><div class="wrap"><div class="empty">'
                '<code>plumbline study --domain accounts_payable '
                '--arms plan_execute react</code></div></div>')
        return _page("Runs", body)

    total = sum(r["n_runs"] or 0 for r in runs)
    spend = sum(r["cost_usd"] or 0 for r in runs)
    errs = sum(r["n_errors"] or 0 for r in runs)
    cards = "".join([
        f'<div class="card"><div class="k">Projects</div>'
        f'<div class="v">{len(projects)}</div></div>',
        f'<div class="card"><div class="k">Runs</div>'
        f'<div class="v">{len(runs)}</div>'
        f'<div class="n">{total:,} trials</div></div>',
        f'<div class="card"><div class="k">Incomplete trials</div>'
        f'<div class="v" style="color:{"var(--crit)" if errs else "var(--ok)"}">'
        f'{errs}</div></div>',
        f'<div class="card"><div class="k">Spend</div>'
        f'<div class="v">${spend:,.2f}</div></div>',
    ])

    rows = []
    for r in runs:
        status = ("ok" if r["status"] == "complete" else "run")
        rows.append(
            f'<tr><td class="mono"><a href="/runs/{e(r["run_id"])}">'
            f'{e(r["label"] or r["run_id"])}</a></td>'
            f'<td>{e(r["project_name"])}</td>'
            f'<td class="mono">{e(r["domain"])}</td>'
            f'<td class="mono">{e(r["model"])}</td>'
            f'<td class="num">{r["n_runs"] or 0:,}</td>'
            f'<td class="num" style="color:'
            f'{"var(--crit)" if r["n_errors"] else "var(--ink-3)"}">'
            f'{r["n_errors"] or 0}</td>'
            f'<td class="num">${r["cost_usd"] or 0:,.2f}</td>'
            f'<td class="mono">{_ts(r["started_utc"])}</td>'
            f'<td><span class="pill {status}">{e(r["status"])}</span></td></tr>')

    return _page("Runs",
                 f'<h1>Runs</h1><p class="sub">Every figure re-derives from '
                 f'stored trajectories</p>'
                 f'<div class="grid g4" style="margin-top:16px">{cards}</div>'
                 f'<h2>Recent</h2><div class="wrap scroll"><table><thead><tr>'
                 f'<th>Run</th><th>Project</th><th>Domain</th><th>Model</th>'
                 f'<th style="text-align:right">Trials</th>'
                 f'<th style="text-align:right">Errors</th>'
                 f'<th style="text-align:right">Cost</th><th>Started</th>'
                 f'<th>Status</th></tr></thead><tbody>{"".join(rows)}'
                 f'</tbody></table></div>')


# ==========================================================================
def run_detail(run: dict, certificates: list[dict], violations: list[dict],
               trajectories: list[dict], selected: dict) -> str:
    conf = [c for c in certificates if c["kind"] == "conformance"]
    parity = [c for c in certificates if c["kind"] == "parity"]

    cards = []
    for c in sorted(conf, key=lambda x: x["arm"]):
        colour = _grade_colour(c["bound"])
        cards.append(
            f'<div class="card"><div class="k">{e(c["arm"])}</div>'
            f'<div class="v" style="color:{colour}">'
            f'{(c["bound"] or 0) * 100:.1f}%</div>'
            f'<div class="n">certified bound · grade {e(c["grade"] or "-")}</div>'
            f'<div style="margin-top:9px">{_pct_bar(c["bound"], colour)}</div>'
            f'</div>')
    for c in parity:
        colour = _grade_colour(c["bound"])
        cards.append(
            f'<div class="card"><div class="k">parity vs {e(c["counterpart"])}'
            f'</div><div class="v" style="color:{colour}">'
            f'{(c["bound"] or 0) * 100:.1f}%</div>'
            f'<div class="n">retirement confidence</div>'
            f'<div style="margin-top:9px">{_pct_bar(c["bound"], colour)}</div>'
            f'</div>')
    if not cards:
        cards.append('<div class="card"><div class="k">Certificates</div>'
                     '<div class="v">—</div><div class="n">none stored</div></div>')

    # violations
    if violations:
        vrows = []
        for v in sorted(violations,
                        key=lambda x: (SEV_ORDER.get(x["severity"], 9),
                                       -x["occurrences"])):
            vrows.append(
                f'<tr><td><span class="pill {e(v["severity"])}">'
                f'{e(v["severity"])}</span></td>'
                f'<td class="mono">{e(v["invariant_id"])}</td>'
                f'<td class="mono">{e(v["arm"])}</td>'
                f'<td class="num">{v["occurrences"]}</td>'
                f'<td class="num">{v["tasks"]}</td>'
                f'<td class="mono">{e(v["perturbations"])}</td>'
                f'<td>{e((v["example"] or "")[:110])}</td></tr>')
        vtable = (f'<div class="wrap scroll"><table><thead><tr><th>Severity</th>'
                  f'<th>Invariant</th><th>Arm</th>'
                  f'<th style="text-align:right">Hits</th>'
                  f'<th style="text-align:right">Tasks</th>'
                  f'<th>Perturbations</th><th>Example</th></tr></thead>'
                  f'<tbody>{"".join(vrows)}</tbody></table></div>')
    else:
        vtable = ('<div class="wrap"><div class="empty">No invariant violations. '
                  'Every declared control held under every perturbation applied.'
                  '</div></div>')

    # filters
    arms = sorted({t["arm"] for t in trajectories})
    perts = sorted({t["perturbation"] for t in trajectories})
    rid = run["run_id"]

    def chip(label, key, val):
        on = selected.get(key) == val
        qs = []
        for k in ("arm", "perturbation"):
            v = val if k == key else selected.get(k)
            if v and not (k == key and on):
                qs.append(f"{k}={v}")
        if selected.get("only_failing"):
            qs.append("only_failing=true")
        href = f"/runs/{rid}" + ("?" + "&".join(qs) if qs else "")
        return f'<a class="{"on" if on else ""}" href="{href}">{e(label)}</a>'

    fail_qs = [f"{k}={v}" for k, v in selected.items()
               if k in ("arm", "perturbation") and v]
    if not selected.get("only_failing"):
        fail_qs.append("only_failing=true")
    fail_href = f"/runs/{rid}" + ("?" + "&".join(fail_qs) if fail_qs else "")

    filters = ('<div class="filters"><span class="sub">arm</span>'
               + "".join(chip(a, "arm", a) for a in arms)
               + '<span class="sub" style="margin-left:10px">perturbation</span>'
               + "".join(chip(p, "perturbation", p) for p in perts)
               + f'<a class="{"on" if selected.get("only_failing") else ""}" '
                 f'href="{fail_href}" style="margin-left:10px">failing only</a>'
               + '</div>')

    trows = []
    for t in trajectories:
        bad = t["conformant"] == 0 or t["outcome_ok"] == 0 or t["error"]
        mark = ('<span class="pill critical">fail</span>' if bad
                else '<span class="pill ok">pass</span>')
        trows.append(
            f'<tr><td>{mark}</td>'
            f'<td class="mono"><a href="/runs/{rid}/trace/{e(t["trial_id"])}">'
            f'{e(t["task_id"])}</a></td>'
            f'<td class="mono">{e(t["arm"])}</td>'
            f'<td class="mono">{e(t["perturbation"])}</td>'
            f'<td class="mono">{e(t["variant_id"])}</td>'
            f'<td class="num">{t["n_steps"]}</td>'
            f'<td class="num" style="color:'
            f'{"var(--crit)" if t["failed_steps"] else "var(--ink-3)"}">'
            f'{t["failed_steps"]}</td>'
            f'<td class="mono" style="color:var(--crit)">'
            f'{e((t["error"] or "")[:60])}</td></tr>')

    ttable = (f'<div class="wrap scroll"><table><thead><tr><th></th><th>Task</th>'
              f'<th>Arm</th><th>Perturbation</th><th>Variant</th>'
              f'<th style="text-align:right">Steps</th>'
              f'<th style="text-align:right">Failed</th><th>Error</th>'
              f'</tr></thead><tbody>{"".join(trows)}</tbody></table></div>'
              if trows else
              '<div class="wrap"><div class="empty">No trajectories match.</div></div>')

    meta = (f'{run["n_runs"] or 0:,} trials · {e(run["model"])} · '
            f'${run["cost_usd"] or 0:,.2f} · {run["wall_seconds"] or 0:.0f}s · '
            f'{e(run["domain"])}')
    return _page(run["label"] or run["run_id"],
                 f'<h1>{e(run["label"] or run["run_id"])}</h1>'
                 f'<p class="sub">{meta}</p>'
                 f'<p style="margin-top:10px">'
                 f'<a href="/runs/{e(run["run_id"])}/controls">'
                 f'Control operating effectiveness &rarr;</a>'
                 f'<span class="sub">  the same evidence as a test of '
                 f'controls</span></p>'
                 f'<div class="grid g4" style="margin-top:16px">'
                 f'{"".join(cards)}</div>'
                 f'<h2>Invariant violations</h2>{vtable}'
                 f'<h2>Trajectories</h2>{filters}{ttable}',
                 crumb=f'<a href="/">runs</a> / {e(run["run_id"])}')


# ==========================================================================
MONEY_STEPS = {"schedule_payment", "apply_credit_note", "request_approval"}


def _steps_html(traj) -> str:
    out = []
    for i, s in enumerate(traj.steps):
        if s.kind == "final":
            continue
        cls = "step"
        if s.failed:
            cls += " fail"
        elif s.name.startswith("blocked:"):
            cls += " blocked"
        elif s.name in MONEY_STEPS:
            cls += " money"
        args = json.dumps(s.args, default=str) if s.args else ""
        out.append(f'<div class="{cls}"><span class="i">{i}</span>'
                   f'<span class="nm">{e(s.name)}</span>'
                   f'<span class="ar">{e(args[:150])}</span></div>')
        if s.error:
            out.append(f'<div class="err">↳ {e(s.error)}</div>')
    return "".join(out) or '<div class="empty">No steps recorded.</div>'


def trace_detail(run: dict, traj, other=None, peers: list[dict] | None = None) -> str:
    rid = run["run_id"]
    left = (f'<div class="wrap"><div style="padding:12px 14px;'
            f'border-bottom:1px solid var(--rule)">'
            f'<strong>{e(traj.arm)}</strong> '
            f'<span class="sub">· {e(traj.perturbation)} · '
            f'{e(traj.variant_id)}</span></div>'
            f'<div class="steps">{_steps_html(traj)}</div></div>')

    if other is not None:
        right = (f'<div class="wrap"><div style="padding:12px 14px;'
                 f'border-bottom:1px solid var(--rule)">'
                 f'<strong>{e(other.arm)}</strong> '
                 f'<span class="sub">· {e(other.perturbation)} · '
                 f'{e(other.variant_id)}</span></div>'
                 f'<div class="steps">{_steps_html(other)}</div></div>')
        panels = f'<div class="two">{left}{right}</div>'
        finals = (f'<div class="two" style="margin-top:12px">'
                  f'<div class="final">{e(traj.final_output or "(none)")}</div>'
                  f'<div class="final">{e(other.final_output or "(none)")}</div>'
                  f'</div>')
    else:
        panels = left
        finals = (f'<div class="final" style="margin-top:12px">'
                  f'{e(traj.final_output or "(none)")}</div>')

    compare = ""
    if peers:
        links = " ".join(
            f'<a class="" href="/runs/{rid}/trace/{e(traj.trial_id)}'
            f'?compare_to={e(p["trial_id"])}">{e(p["arm"])}</a>' for p in peers)
        compare = (f'<div class="filters"><span class="sub">compare against the '
                   f'same task and variant</span>{links}</div>')

    meta = (f'{e(traj.task_id)} · {len(traj.steps)} steps · '
            f'{sum(1 for s in traj.steps if s.failed)} failed · '
            f'{traj.tokens_in:,} in / {traj.tokens_out:,} out tokens')
    return _page(traj.trial_id,
                 f'<h1>{e(traj.task_id)}</h1><p class="sub">{meta}</p>'
                 f'<h2>Request</h2>'
                 f'<div class="final">{e(traj.task_input or "(none)")}</div>'
                 f'<h2>Trajectory</h2>{compare}{panels}'
                 f'<h2>Final response</h2>{finals}',
                 crumb=f'<a href="/">runs</a> / '
                       f'<a href="/runs/{rid}">{e(rid)}</a> / '
                       f'{e(traj.trial_id)}')



# ==========================================================================
def control_attestation(run: dict, att, arm: str | None, operator: str) -> str:
    """The workpaper as a page.

    Deliberately the same content as the text rendering, in the same order. A
    console that shows a friendlier subset of an audit document is worse than
    no console, because the reader believes they have seen it.
    """
    ok, why = att.test_of_one

    cards = [
        ('Population', f'{att.population:,}',
         f'transactions{f" · {att.incomplete} incomplete excluded" if att.incomplete else ""}'),
        ('Controls tested', f'{len(att.results)}',
         f'of {len(att.framework.controls)} in the framework'),
        ('Deficient', f'{len(att.deficiencies)}',
         'zero-tolerance unless stated'),
        ('Test of one', 'NO' if not ok else 'YES',
         'reliance for the period'),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{e(k)}</div>'
        f'<div class="v" style="color:{"var(--crit)" if k == "Deficient" and v != "0" else "var(--ink)"}">'
        f'{e(v)}</div><div class="n">{e(n)}</div></div>'
        for k, v, n in cards)

    rows = []
    for r in att.results:
        a = r.assessment
        colour = "var(--ok)" if r.effective else "var(--crit)"
        verdict = "EFFECTIVE" if r.effective else (
            "INCONCLUSIVE" if not a.sufficient else "DEFICIENT")
        conditions = ", ".join(f"{k} ({v})" for k, v in r.by_perturbation.items())
        rows.append(
            f'<tr><td class="mono">{e(r.control.control_id)}</td>'
            f'<td>{e(r.control.name)}<div class="sub">{e(_clip(r.control.objective, 96))}</div></td>'
            f'<td class="num">{a.tested:,}</td>'
            f'<td class="num">{a.deviations}</td>'
            f'<td class="num">{a.upper_deviation_rate * 100:.1f}%</td>'
            f'<td class="num">{a.tolerable_rate * 100:.0f}%</td>'
            f'<td style="color:{colour};font-weight:600">{verdict}'
            f'{f"<div class=sub>{e(conditions)}</div>" if conditions else ""}</td>'
            f'</tr>')

    # exceptions, itemised. A rate with no attached transactions cannot be worked.
    exc = []
    for r in att.deficiencies:
        items = "".join(
            f'<li><a class="mono" href="/runs/{e(run["run_id"])}/trace/'
            f'{e(d.trial_id)}">{e(d.trial_id)}</a> '
            f'<span class="sub">{e(d.detail[:90])}</span></li>'
            for d in r.deviations[:8])
        more = (f'<li class="sub">and {len(r.deviations) - 8} further'
                f'</li>' if len(r.deviations) > 8 else "")
        exc.append(
            f'<div class="panel"><h3>{e(r.control.control_id)} '
            f'{e(r.control.name)}</h3>'
            f'<p class="sub">Risk: {e(r.control.risk)}</p>'
            f'<p><b>{e(r.assessment.conclusion())}</b></p>'
            f'<p class="sub">Route to {e(r.control.remediation_owner)} '
            f'within {r.control.sla_days} business days</p>'
            f'<ul class="ex">{items}{more}</ul></div>')

    conditions = "".join(
        f'<tr><td>{e(k)}</td><td class="num">{v:,}</td></tr>'
        for k, v in att.conditions.items())

    # Two arms in one population are two different controls. Saying so is the
    # difference between a workpaper and a chart.
    if len(att.arms) > 1:
        mixed = (
            '<p style="color:var(--high)"><b>Qualified.</b> This population '
            'spans ' + str(len(att.arms)) + ' implementations of the control ('
            + e(", ".join(f"{k}: {v:,}" for k, v in att.arms.items()))
            + '). They are not the same control and a single conclusion is not '
              'attributable to any one of them. Filter to one implementation '
              'before relying on this.</p>'
            + " ".join(
                f'<a href="?arm={e(k)}">test {e(k)} alone &rarr;</a>'
                for k in att.arms))
    elif att.arms:
        mixed = (f'<p class="sub">implementation: '
                 f'{e(next(iter(att.arms)))}</p>')
    else:
        mixed = ""

    limits = "".join(
        f'<tr><td class="mono">{e(c.control_id)}</td><td>{e(c.name)}</td>'
        f'<td class="sub">{e(why_)}</td></tr>'
        for c, why_ in att.not_tested)

    body = f"""
<h1>Control operating effectiveness</h1>
<p class="sub">{e(att.framework.name)} v{e(att.framework.version)} ·
period {e(att.period)} · operator {e(att.operator)} ({e(att.model)})
{f"· arm {e(arm)}" if arm else ""}</p>
<div class="cards">{card_html}</div>

<div class="panel warn">
  <h3>Reliance approach</h3>
  <p>{e(why)}</p>
</div>

<div class="panel">
  <h3>Basis of preparation</h3>
  <p class="sub">Conclusions extend to the population below and no further. A
  control evidenced here is evidenced against this variation only.</p>
  {mixed}
  <table class="tight"><tbody>{conditions}</tbody></table>
</div>

<table>
  <thead><tr><th>Control</th><th>Name</th><th class="num">Pop</th>
  <th class="num">Dev</th><th class="num">UDR</th><th class="num">Tol</th>
  <th>Conclusion</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>

<h2>Exceptions</h2>
{"".join(exc) if exc else '<p class="sub">None. Every tested control operated without deviation on this population.</p>'}

{f'<h2>Scope limitation</h2><p class="sub">No conclusion is drawn on the following. They are neither effective nor deficient on this evidence.</p><table><tbody>{limits}</tbody></table>' if limits else ''}

<p class="sub" style="margin-top:26px">
Evidence hash <span class="mono">{e(att.evidence_hash)}</span> over
{att.population:,} stored trajectories. This attestation regenerates from those
trajectories with no model calls.
<a href="/api/runs/{e(run["run_id"])}/attestation{f"?arm={e(arm)}" if arm else ""}">JSON</a>
</p>"""
    return _page("Control attestation", body,
                 crumb=f'<a href="/runs/{e(run["run_id"])}">&larr; run</a>')


def error_page(code: int, detail: str) -> str:
    return _page(f"{code}",
                 f'<h1>{code}</h1><p class="sub">{e(detail)}</p>'
                 f'<p style="margin-top:14px"><a href="/">Back to runs</a></p>')
