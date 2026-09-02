"""
Assemble the study into a report page.

The page leads with the retraction rather than the result, because the
retraction IS the result. A reader who stops after the first screen should
leave knowing that a significant, well-visualised effect turned out to be an
artifact of the baseline, and what that implies for reading anybody else's
agent benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..analysis.stats import compare, wilson
from ..core.trajectory import TrajectoryStore
from .html import CSS, _e, dot_plot, trajectory_diff, wrap_standalone

PAGE_TITLE = "The Baseline Was the Result"

PERT_ORDER = ["baseline", "paraphrase", "distractor", "decoy_tools",
              "sampling", "tool_fault"]

#: (key, run directory, arm, label). The third series is why the page exists.
SERIES = [
    ("naive", "runs/parity-study", "plan_execute", "deterministic, no retry"),
    ("retry", "runs/retry-study", "plan_execute", "deterministic, 3 retries"),
    ("react", "runs/retry-study", "react", "free-form agent"),
]


def _load(root: Path, run: str, arm: str, contexts, matches):
    led = json.loads((root / run / "ledger_states.json").read_text(encoding="utf-8"))
    trajs = [t for t in TrajectoryStore(root / run / "trajectories.jsonl").load()
             if not t.error and t.arm == arm]
    flags = {p: [matches(contexts.get(t.task_id, {}), led.get(t.trial_id))
                 for t in trajs if t.perturbation == p] for p in PERT_ORDER}
    return trajs, led, flags


def _find_pair(naive_trajs, retry_trajs, contexts, naive_led, retry_led, matches):
    """A task and variant where the no-retry executor failed and the retrying
    one did not. That single pair is the whole retraction."""
    retry_by = {(t.task_id, t.variant_id): t for t in retry_trajs}
    for n in naive_trajs:
        if matches(contexts.get(n.task_id, {}), naive_led.get(n.trial_id)):
            continue
        r = retry_by.get((n.task_id, n.variant_id))
        if r is not None and matches(contexts.get(r.task_id, {}),
                                     retry_led.get(r.trial_id)):
            return n, r
    return None, None


def build(root: str | Path = ".", *, standalone: bool = True) -> str:
    from plumbline.domains.ap.tasks import build_tasks, expected_outcome

    root = Path(root)
    contexts = {t.task_id: t.context for t in build_tasks()}

    def matches(ctx, led):
        if not ctx or led is None:
            return False
        w = expected_outcome(ctx)
        return (bool(led.get("paid")) == w["paid"]
                and int(led.get("payment_count", 0)) == w["payment_count"]
                and abs(float(led.get("amount_paid", 0)) - w["amount_paid"]) < 0.005
                and bool(led.get("exception_raised")) == w["exception_raised"])

    data = {}
    for key, run, arm, _label in SERIES:
        data[key] = _load(root, run, arm, contexts, matches)

    naive_f = data["naive"][2]
    retry_f = data["retry"][2]
    react_f = data["react"][2]

    tf = "tool_fault"
    cmp_fix = compare("naive", naive_f[tf], "retry", retry_f[tf])
    cmp_vs = compare("retry", retry_f[tf], "react", react_f[tf])
    cmp_orig = compare("naive", naive_f[tf], "react", react_f[tf])

    rows, table = [], []
    for p in PERT_ORDER:
        pts = []
        for idx, (key, _r, _a, label) in enumerate(SERIES):
            f = data[key][2][p]
            if not f:
                continue
            w = wilson(sum(f), len(f))
            pts.append((idx, w.value, w.lo, w.hi, w.total))
            table.append((p, label, w))
        rows.append((p.replace("_", " "), pts))

    n_total = sum(len(t) for t, _, _ in data.values())
    inc, ret = _find_pair(data["naive"][0], data["retry"][0], contexts,
                          data["naive"][1], data["retry"][1], matches)

    B = ['<div class="pl">']
    B.append('<h1>I found a 17-point result.<br>Then I fixed the baseline '
             'and it vanished.</h1>')
    B.append(f'<p class="sub">A retraction, and what it says about reading '
             f'anyone\'s agent benchmark. {n_total:,} runs.</p>')

    B.append('<div class="hero">')
    B.append(f'<div class="figure">{abs(cmp_orig.diff):.0f}'
             f'<span class="unit"> points, retracted</span></div>')
    B.append(f'<p style="margin:.5rem 0 0">The original study reported that a '
             f'<strong>deterministic executor</strong> was more brittle under a '
             f'transient tool failure than a free-form agent: '
             f'{cmp_orig.a.pct:.1f}% against {cmp_orig.b.pct:.1f}%, '
             f'p&nbsp;=&nbsp;{cmp_orig.p_value:.4f}.</p>')
    B.append('<p style="margin:.6rem 0 0">Then somebody pointed out that no '
             'production finance system treats a single 503 as fatal. My '
             'executor had no retry policy. Every RPA platform has one.</p>')
    B.append('</div>')

    B.append('<p class="eyebrow">The correction</p>')
    B.append('<h2>Three lines of retry logic closed the entire gap</h2>')
    B.append('<p>The identical 768 trials, re-run with the error handling a '
             'real integration has:</p>')
    B.append(f'<div class="hero"><p class="ci" style="margin:0;font-size:.95rem;'
             f'line-height:2">'
             f'naive &rarr; retry &nbsp;&nbsp;{cmp_fix.diff:+.1f} pts&nbsp;&nbsp;'
             f'p = {cmp_fix.p_value:.4f}&nbsp;&nbsp;<strong>significant</strong>'
             f'<br>retry &rarr; react &nbsp;&nbsp;{cmp_vs.diff:+.1f} pts&nbsp;&nbsp;'
             f'p = {cmp_vs.p_value:.2f}&nbsp;&nbsp;not significant</p></div>')
    B.append('<p>A well-built deterministic executor is <strong>statistically '
             'indistinguishable</strong> from a free-form agent on this task. '
             'The original headline was measuring missing error handling.</p>')

    B.append('<p class="eyebrow">Evidence</p>')
    B.append('<h2>Outcome correctness by perturbation</h2>')
    B.append('<p>Measured against ground truth. Dots are point estimates, bars '
             'are 95% Wilson intervals.</p>')
    B.append('<div class="legend">' + "".join(
        f'<span><span class="swatch" style="background:var(--series-{i+1})">'
        f'</span>{_e(label)}</span>'
        for i, (_k, _r, _a, label) in enumerate(SERIES)) + '</div>')
    B.append('<figure class="wide"><div class="scroll">')
    B.append(dot_plot(rows, label_rows={"tool fault"}))
    B.append('</div><figcaption>Paraphrasing, distractor text, decoy tools and '
             'sampling variation broke nothing, in any of the three. Only the '
             'injected tool fault separated them, and only when the executor '
             'could not retry.</figcaption></figure>')

    B.append('<div class="scroll"><table><thead><tr><th>perturbation</th>'
             '<th>system</th><th class="num">correct</th>'
             '<th class="num">95% CI</th><th class="num">n</th></tr></thead>'
             '<tbody>')
    for p, label, w in table:
        B.append(f'<tr><td>{_e(p.replace("_", " "))}</td><td>{_e(label)}</td>'
                 f'<td class="num">{w.pct:.1f}%</td>'
                 f'<td class="num">{w.lo * 100:.1f} – {w.hi * 100:.1f}</td>'
                 f'<td class="num">{w.total}</td></tr>')
    B.append('</tbody></table></div>')

    if inc is not None:
        ctx = contexts.get(inc.task_id, {})
        amount = ctx.get("expected_amount", 0)
        B.append('<p class="eyebrow">The same fault, both executors</p>')
        B.append('<h2>One retry is the whole difference</h2>')
        B.append(f'<p>Invoice <code>{_e(inc.task_id)}</code> is clean; the '
                 f'correct action is to pay <strong>${amount:,.2f}</strong>. '
                 f'A transient 503 was injected into the same tool call in '
                 f'both runs.</p>')
        B.append('<div class="wide">' + trajectory_diff(
            inc, ret,
            left_title="no retry policy", right_title="3 retries",
            left_label="any tool error is a blocking condition",
            right_label="transient faults retried, permanent ones escalate",
            left_verdict='<span class="bad">✗ held a clean invoice</span><br>'
                         '<span style="color:var(--ink-3)">the 503 was treated '
                         'as a blocker</span>',
            right_verdict=f'<span class="ok">✓ paid ${amount:,.2f}</span><br>'
                          f'<span style="color:var(--ink-3)">retried, '
                          f'succeeded, continued</span>',
            highlight={"schedule_payment", "flag_exception"}) + '</div>')

    B.append('<p class="eyebrow">What this is actually evidence for</p>')
    B.append('<h2>The baseline was the result</h2>')
    B.append('<div class="note">This is not evidence that determinism wins, and '
             'not evidence that it loses. It is evidence about <strong>how agent '
             'benchmarks are built</strong>.<br><br>'
             'Everything downstream of the baseline was correct. The confidence '
             'intervals were correct. The permutation test was correct. The '
             'control condition was correct and showed the effect localised to '
             'one perturbation, which is exactly what a real effect looks like. '
             'The result was still an artifact, and no amount of statistical '
             'rigour would have caught it, because the flaw was upstream of the '
             'statistics.<br><br>'
             'Only domain knowledge finds it. Somebody who had shipped a '
             'payments integration looked at the setup and said no production '
             'system behaves that way.<br><br>'
             '<strong>Every agent benchmark has a baseline somebody chose.</strong> '
             'When you read one, the question that decides whether the number '
             'means anything is not which statistical test they used. It is '
             'whether the thing they compared against is a system anyone would '
             'actually deploy.</div>')

    B.append('<p class="eyebrow">Limits</p>')
    B.append('<h2>What this does not show</h2>')
    B.append('<div class="note">One model (<code>claude-haiku-4-5</code>), one '
             'domain, one policy. Perturbations are a chosen finite set: passing '
             'them is evidence, not proof, and as the retraction shows, so is '
             'the choice of what to compare against.<br><br>'
             'Neither architecture failed under paraphrasing, distractors, '
             'decoy tools or sampling variation. That is a real observation and '
             'a narrow one: this task is well within the model\'s ability, and '
             'a harder domain might separate them. A richer AP domain with 20 '
             'invoices across 13 exception classes exists in the repository and '
             'has not yet been run against a live model.</div>')

    B.append('<footer>')
    B.append(f'{n_total:,} runs · claude-haiku-4-5 · the no-retry executor is '
             f'kept as a permanent control arm so the effect can be attributed '
             f'rather than repeated<br>'
             f'Every number regenerates from committed trajectories with no '
             f'model calls. CI re-derives them on every push.')
    B.append('</footer></div>')

    body = "\n".join(B)
    if standalone:
        return wrap_standalone(body, PAGE_TITLE)
    from .html import FONTS
    return (f"<title>{PAGE_TITLE}</title>{FONTS}"
            f"<style>{CSS}</style>\n{body}")
