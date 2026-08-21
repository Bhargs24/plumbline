# Plumbline

**Conformance-under-perturbation testing for LLM agents.**

*A plumb line does not measure a wall. It gives you a reference that is true, so
the wall's deviation from it becomes visible and measurable.*

Your agent passed its evals. That tells you it produced acceptable answers on a
fixed test set. It does not tell you whether it will still run your controls when
someone rewords the request.

Plumbline asks you to declare what must always be true of a run, then tries to
break those claims using input changes that preserve meaning: reword the request,
inject a transient tool failure, add irrelevant context, add unrelated tools,
raise the sampling temperature. It reports which invariant broke, under which
perturbation, at which named step, how often, and with what confidence interval.

```
MustCall("check_duplicate")
Ordering("match_purchase_order", then="schedule_payment")
CallAtMost("schedule_payment", 1)
ArgEquals("schedule_payment", "amount", from_context="expected_amount")
```

Those are ordinary accounts payable controls. Each one exists because skipping it
has cost someone money.

## Why arguments, not just paths

Trajectory evaluation compares *which tools ran*. A payment of $48,200 instead of
$4,820 runs the identical tools in the identical order and produces an identical
confirmation message. It is invisible to path comparison and invisible to a
final-output eval.

Plumbline compares tool arguments field by field with typed policies, against
task ground truth rather than against other runs, so a drift that every run
shares still fires. The closest prior art names this resolution as open work.

## Status

Working: the measurement core, the invariant layer, the perturbation engine with
a semantic-equivalence guard, three real agent architectures over a real
database, statistics with confidence intervals, the certificate, and the CLI.
33 tests pass.

The determinism study needs an API key to produce real numbers. See below.

## Install

```bash
pip install -e ".[dev]"
cp .env.example .env    # add your ANTHROPIC_API_KEY
```

## Run the study

```bash
python experiments/determinism_study/run.py --trials 1 --variants 4
```

Three architectures handle the same invoices with the same tools, the same model,
and the same perturbation variants. The only thing that differs is who decides
which step runs next:

| arm | control flow | what it can still get wrong |
|---|---|---|
| `react` | the model decides every step | skip a control, pay a blocked invoice, pay twice, pay a wrong amount |
| `plan_execute` | the model interprets, a fixed executor runs the procedure | identify the wrong invoice; structural conformance holds by construction |
| `guarded` | the model decides, a policy layer can refuse a step | loop, give up, or reach a wrong final disposition after being refused |

## Inspect the results

```bash
plumbline certify runs/determinism-study
```

```bash
plumbline compare runs/determinism-study react plan_execute
```

```bash
plumbline show runs/determinism-study --trial paraphrase
```

`certify` rebuilds the certificate from stored trajectories with no model calls.
That is deliberate: ship the trajectories file and anyone can check the number.

## The headline number

The certificate reports the **95% lower confidence bound on critical-invariant
conformance under the worst perturbation**, not the observed average.

Worst case, because averaging is how a dangerous perturbation gets buried.
Critical only, because a missing log entry and a duplicate payment are not
commensurable. A lower bound rather than a point estimate, because a certificate
should state what you can defend, not what you saw on a good day. This makes a
small study certify lower than a large one, which is correct.

## Layout

```
src/plumbline/
  core/       trajectory model, typed argument comparators, sequence alignment
  spec/       the invariant DSL
  adapters/   Claude client with trial-keyed caching and budget accounting
  perturb/    the perturbation library and the equivalence guard
  runtime/    runner, cache, hard spend cap
  analysis/   conformance, consistency, Wilson intervals, permutation tests
  report/     the certificate
agents/ap/    the system under test: tools over sqlite, policy, three arms
experiments/  the determinism study
```

## Prior art

The idea that trajectory consistency under perturbation matters is not new, and
`DESIGN.md` says exactly what is and is not novel here. Briefly: metamorphic
reliability testing of agents is active 2026 research, the closest work does it
at the trajectory level with statistical methods and no released tool, and this
is an operationalization plus two extensions those papers name as open. It is a
product and tooling contribution, not a new-science claim.

Built by Bhargav Raghavendra. Apache-2.0.
