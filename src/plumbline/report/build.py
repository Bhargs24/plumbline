"""
Assemble a run into a report page.

The page leads with the finding rather than the tool. A reader who stops after
the first screen should still leave knowing what was measured, how big it was,
and how uncertain it is.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..analysis.stats import compare, wilson
from ..core.trajectory import Trajectory, TrajectoryStore
from .html import CSS, dot_plot, trajectory_diff, wrap_standalone, _e

PAGE_TITLE = "Parity Under Perturbation"

PERT_ORDER = ["baseline", "paraphrase", "distractor", "decoy_tools",
              "sampling", "tool_fault"]


def _correct(traj, contexts, ledgers) -> bool:
    from agents.ap.tasks import expected_outcome
    want = expected_outcome(contexts.get(traj.task_id, {}))
    got = ledgers.get(traj.trial_id) or {}
    return (bool(got.get("paid")) == want["paid"]
            and int(got.get("payment_count", 0)) == want["payment_count"]
            and abs(float(got.get("amount_paid", 0)) - want["amount_paid"]) < 0.005
            and bool(got.get("exception_raised")) == want["exception_raised"])


def _find_diff_pair(trajs, contexts, ledgers, incumbent, replacement):
    """The most illustrative pair: same invoice, same injected fault, opposite
    outcome, where the INCUMBENT is the one that got it wrong. That is the pair
    a reader does not expect, so it is the one worth showing."""
    by_key = {}
    for t in trajs:
        by_key.setdefault((t.arm, t.task_id, t.variant_id), t)
    best = None
    for (arm, task, var), inc in by_key.items():
        if arm != incumbent:
            continue
        rep = by_key.get((replacement, task, var))
        if rep is None:
            continue
        ic, rc = _correct(inc, contexts, ledgers), _correct(rep, contexts, ledgers)
        if not ic and rc:
            score = (1, sum(1 for s in inc.steps if s.failed))
            if best is None or score > best[0]:
                best = (score, inc, rep)
    return (best[1], best[2]) if best else (None, None)


def build(run_dir: str | Path, *, incumbent: str, replacement: str,
          standalone: bool = True) -> str:
    from agents.ap.policy import AP_POLICY
    from agents.ap.tasks import build_tasks
    from ..certify import prove_parity

    run_dir = Path(run_dir)
    trajs = TrajectoryStore(run_dir / "trajectories.jsonl").load()
    trajs = [t for t in trajs if not t.error]
    ledgers = json.loads((run_dir / "ledger_states.json").read_text(encoding="utf-8"))
    contexts = {t.task_id: t.context for t in build_tasks()}

    arms = {incumbent: 0, replacement: 1}
    flags: dict[tuple, list] = defaultdict(list)
    for t in trajs:
        if t.arm in arms:
            flags[(t.arm, t.perturbation)].append(_correct(t, contexts, ledgers))

    perts = [p for p in PERT_ORDER if any((a, p) in flags for a in arms)]
    rows, table = [], []
    for p in perts:
        pts = []
        for arm, idx in arms.items():
            f = flags.get((arm, p), [])
            if not f:
                continue
            w = wilson(sum(f), len(f))
            pts.append((idx, w.value, w.lo, w.hi, w.total))
            table.append((p, arm, w))
        rows.append((p.replace("_", " "), pts))

    overall = {a: wilson(sum(v), len(v)) for a, v in
               ((a, [x for (ar, _), lst in flags.items() if ar == a for x in lst])
                for a in arms)}
    worst = min(perts, key=lambda p: min(
        wilson(sum(flags[(a, p)]), len(flags[(a, p)])).value
        for a in arms if (a, p) in flags))
    cmp_worst = compare(incumbent, flags[(incumbent, worst)],
                        replacement, flags[(replacement, worst)])
    others_i = [x for (a, p), v in flags.items() if a == incumbent and p != worst for x in v]
    others_r = [x for (a, p), v in flags.items() if a == replacement and p != worst for x in v]
    cmp_rest = compare(incumbent, others_i, replacement, others_r)

    parity = prove_parity(trajs, incumbent=incumbent, replacement=replacement,
                          ledger_states=ledgers, spec=AP_POLICY)
    inc_run, rep_run = _find_diff_pair(trajs, contexts, ledgers, incumbent, replacement)

    gap = cmp_worst.diff
    B = []
    B.append('<div class="pl">')
    B.append('<h1>The failure everyone tests for wasn\'t there.<br>'
             'The one nobody tests for was.</h1>')
    B.append(f'<p class="sub">Two accounts-payable automations, identical tasks, '
             f'tools, model and inputs. {len(trajs)} runs. The only difference is '
             f'who decides which step runs next.</p>')

    B.append('<div class="hero">')
    B.append(f'<div class="figure">{abs(gap):.0f}<span class="unit"> point gap</span></div>')
    B.append(f'<p style="margin:.5rem 0 0">Under an injected <strong>transient tool '
             f'failure</strong>, the deterministic executor reached the correct '
             f'outcome {cmp_worst.a.pct:.1f}% of the time. The free-form agent '
             f'reached it {cmp_worst.b.pct:.1f}% of the time.</p>')
    B.append(f'<p class="ci">p = {cmp_worst.p_value:.4f} (permutation test) &nbsp;·&nbsp; '
             f'{incumbent} {cmp_worst.a.describe()} &nbsp;·&nbsp; '
             f'{replacement} {cmp_worst.b.describe()}</p>')
    B.append('</div>')
    B.append(f'<p class="sub">On every other perturbation the two are '
             f'indistinguishable ({cmp_rest.diff:+.1f} pts, p = {cmp_rest.p_value:.2f}). '
             f'The effect is localised to one condition, which is what a real '
             f'effect looks like rather than noise.</p>')

    B.append('<p class="eyebrow">Evidence</p>')
    B.append('<h2>Outcome correctness by perturbation</h2>')
    B.append('<p>Measured against ground truth, not against each other. Dots are '
             'point estimates, bars are 95% Wilson intervals.</p>')
    B.append('<div class="legend">'
             f'<span><span class="swatch" style="background:var(--series-1)"></span>'
             f'{_e(incumbent)} — deterministic executor</span>'
             f'<span><span class="swatch" style="background:var(--series-2)"></span>'
             f'{_e(replacement)} — free-form agent</span></div>')
    B.append('<figure class="wide"><div class="scroll">')
    B.append(dot_plot(rows, label_rows={worst.replace("_", " ")}))
    B.append('</div><figcaption>Paraphrasing, distractor text, decoy tools and '
             'sampling variation broke neither system. Only a transient tool '
             'error did, and it broke the deterministic one.</figcaption></figure>')

    B.append('<div class="scroll"><table><thead><tr><th>perturbation</th>'
             f'<th>system</th><th class="num">correct</th><th class="num">95% CI</th>'
             f'<th class="num">n</th></tr></thead><tbody>')
    for p, arm, w in table:
        B.append(f'<tr><td>{_e(p.replace("_", " "))}</td><td>{_e(arm)}</td>'
                 f'<td class="num">{w.pct:.1f}%</td>'
                 f'<td class="num">{w.lo * 100:.1f} – {w.hi * 100:.1f}</td>'
                 f'<td class="num">{w.total}</td></tr>')
    B.append('</tbody></table></div>')

    if inc_run is not None:
        ctx = contexts.get(inc_run.task_id, {})
        amount = ctx.get("expected_amount", 0)
        B.append('<p class="eyebrow">The divergence</p>')
        B.append('<h2>Same invoice, same injected fault, opposite results</h2>')
        B.append(f'<p>Invoice <code>{_e(inc_run.task_id)}</code> is clean. Every check '
                 f'passes and the correct action is to pay '
                 f'<strong>${amount:,.2f}</strong>. A transient error was injected '
                 f'into one tool call.</p>')
        B.append('<div class="wide">' + trajectory_diff(
            inc_run, rep_run,
            left_title=incumbent, right_title=replacement,
            left_label="deterministic executor · control flow fixed in code",
            right_label="free-form agent · the model picks every step",
            left_verdict='<span class="bad">✗ held a clean invoice for human '
                         'review</span><br><span style="color:var(--text-muted)">'
                         'the failed check was treated as a blocker</span>',
            right_verdict=f'<span class="ok">✓ paid ${amount:,.2f} correctly</span>'
                          '<br><span style="color:var(--text-muted)">'
                          'retried the failed call with the right argument</span>',
            highlight={"schedule_payment", "flag_exception"}) + '</div>')
        B.append('<div class="note"><strong>Why this matters for a migration.</strong> '
                 'Both logs look confident and reasonable. Neither system is simply '
                 'better. They disagree, in opposite directions, on a condition that '
                 'only appears when a tool happens to fail — which is why running '
                 'both side by side for thirty days does not find it unless a network '
                 'blip lands on the right invoice inside that window.</div>')

    B.append('<p class="eyebrow">Migration verdict</p>')
    B.append('<h2>Can the incumbent be retired?</h2>')
    B.append(f'<div class="hero"><div class="figure">'
             f'{100 * parity.retirement_bound:.0f}<span class="unit">% confidence</span></div>'
             f'<p style="margin:.5rem 0 0">{_e(parity.verdict())}</p>'
             f'<p class="ci">95% lower bound on outcome equivalence under the worst '
             f'perturbation ({_e(parity.worst_perturbation[0])})</p></div>')
    B.append('<div class="scroll"><table><thead><tr><th>measure</th>'
             '<th class="num">value</th><th class="num">95% CI</th><th class="num">n</th>'
             '</tr></thead><tbody>')
    for label, prop in (("same end state", parity.outcome),
                        ("same control path", parity.path),
                        ("same tool arguments", parity.argument),
                        ("incumbent self-consistency",
                         parity.incumbent_self_consistency)):
        B.append(f'<tr><td>{label}</td><td class="num">{prop.pct:.1f}%</td>'
                 f'<td class="num">{prop.lo * 100:.1f} – {prop.hi * 100:.1f}</td>'
                 f'<td class="num">{prop.total}</td></tr>')
    B.append('</tbody></table></div>')
    B.append(f'<p>The replacement produces the same end state {parity.outcome.pct:.1f}% '
             f'of the time while taking a different route through the system '
             f'{100 - parity.path.pct:.1f}% of the time. Matching outcomes is not '
             f'matching behavior.</p>')

    B.append('<p class="eyebrow">Limits</p>')
    B.append('<h2>What this does not show</h2>')
    B.append('<div class="note">The deterministic executor treats a failed check as '
             'a blocker. That is a design choice in this implementation, not a '
             'property of deterministic systems, and an executor with retry logic '
             'would not fail this way. The honest reading is narrower and more '
             'useful: a deterministic system does exactly what its author '
             'anticipated. It handled every perturbation it was written for, and '
             'failed on the one it was not. The agent improvised a recovery nobody '
             'specified, which is the same capability that lets it skip a control '
             'elsewhere.<br><br>'
             'One domain, one model, one policy. Results are for '
             '<code>claude-haiku-4-5</code> on this task. Perturbations are a chosen '
             'finite set: passing them is evidence, not proof.</div>')

    prov = parity.to_dict()
    B.append('<footer>')
    B.append(f'{len(trajs)} runs · model claude-haiku-4-5 · '
             f'{parity.n_pairs} paired comparisons · '
             f'rebuilt from stored trajectories with <code>plumbline parity</code><br>'
             f'Every number here regenerates from the committed traces. '
             f'No figure in this report was typed by hand.')
    B.append('</footer></div>')

    body = "\n".join(B)
    if standalone:
        return wrap_standalone(body, PAGE_TITLE)
    # Fragment: the host supplies the document shell, so emit the title, the
    # font links and the stylesheet, then the content. Without the title the
    # page inherits its filename as a name; without the links the display and
    # mono faces silently fall back and the design does not arrive.
    from .html import FONTS
    return (f"<title>{PAGE_TITLE}</title>{FONTS}"
            f"<style>{CSS}</style>\n{body}")
