# Plumbline

**Conformance-under-perturbation testing for LLM agents.**

[![tests](https://github.com/Bhargs24/plumbline/actions/workflows/tests.yml/badge.svg)](https://github.com/Bhargs24/plumbline/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![tests](https://img.shields.io/badge/tests-70-brightgreen)
![licence](https://img.shields.io/badge/licence-Apache--2.0-lightgrey)
![evidence](https://img.shields.io/badge/evidence-738%20runs%20committed-informational)

*A plumb line does not measure a wall. It gives you a reference that is true, so the wall's deviation from it becomes visible and measurable.*

---

## The finding

Two accounts-payable automations. Identical tasks, tools, model, inputs and perturbation variants. The only difference is **who decides which step runs next**. 738 runs.

<img src="docs/assets/results.svg" alt="Outcome correctness by perturbation for both architectures, with 95% Wilson confidence intervals. All conditions sit at 100% except tool_fault, where the deterministic executor drops to 81.2% and the free-form agent holds at 98.2%." width="100%">

**The perturbations everyone tests for did nothing.** Paraphrasing, distractor text, decoy tools and sampling variation broke neither system.

**The one nobody tests for broke the deterministic one.** Under an injected transient tool failure the deterministic executor reached the correct outcome 81.2% of the time; the free-form agent reached it 98.2%.

```
plan_execute 81.2% [70.0, 88.9]  →  react 98.2% [90.6, 99.7]
+17.0 points, p = 0.0029 (permutation test)

control, all other perturbations:  −0.3 points, p = 0.48 (not significant)
```

The effect is localised to one condition, which is what a real effect looks like rather than noise.

### The pair that shows why

Invoice `INV-7002` is clean. Every check passes. The correct action is to pay $4,500.

```
plan_execute  (deterministic)          react  (free-form agent)
────────────────────────────────       ────────────────────────────────
interpret_request                      fetch_invoice
fetch_invoice                          match_purchase_order
match_purchase_order   ✗ 503           check_duplicate
check_duplicate                        check_vendor_status ✗ not found
check_vendor_status                    check_vendor_status ✓ (retried)
flag_exception         ← WRONG         schedule_payment $4,500 ✓
post_audit_log                         post_audit_log

held a clean invoice for review        paid correctly
```

The deterministic executor hit a transient network error and stopped. The agent hit its own error, improvised a retry nobody specified, and got it right.

> **The caveat, stated before you ask.** The fail-closed behaviour is a design choice in this executor, not a property of deterministic systems. An executor with retry logic would not fail this way. The honest reading is narrower and more useful: *a deterministic system does exactly what its author anticipated and nothing else.* It handled every perturbation it was written for and failed on the one it was not. The agent improvised a recovery nobody specified, which is the same capability that lets it skip a control elsewhere. **Determinism does not buy reliability. It buys predictability, and it moves the failure from the model to the specification.**

📄 **[Full report with every number and interval](https://bhargs24.github.io/plumbline/report.html)** · 📘 **[The Companion: a 55-page guide from ML basics to this result](https://bhargs24.github.io/plumbline/companion.html)**

---

## Why this needed a new instrument

Existing evaluation grades the **output**, or grades the trajectory **on inputs you fixed in advance**. Neither tells you whether the agent behaves the same way when the input changes in a way that should not matter.

Consider two runs. One runs all four required checks. The other has a check time out, never retries it, reaches the same conclusion by other means, and writes a confident summary. The ledger is identical. The closing message is arguably better written. **Every existing tool passes both.** In accounts payable, a control that did not execute is an audit finding regardless of outcome.

You declare what must always be true:

```python
MustCall("check_duplicate")                     # on every invoice, not just suspicious ones
Ordering("match_purchase_order", then="schedule_payment")
CallAtMost("schedule_payment", 1)               # never pay twice
ArgEquals("schedule_payment", "amount", from_context="expected_amount")
MustNotCall("schedule_payment", when=lambda c: not c["should_pay"])
```

Then it tries to break those claims with input changes that preserve meaning, and reports which invariant broke, under which perturbation, **at which named step**, how often, and with what confidence interval.

---

## Architecture

<img src="docs/assets/architecture.svg" alt="Pipeline: your agent, or ingested OpenTelemetry spans, feeds a perturbation engine, which produces trajectories, which feed three analyses: conformance, consistency and equivalence." width="100%">

Three analyses, answering questions that are easy to conflate and must not be:

| | Question | Catches |
|---|---|---|
| **Conformance** | Did this agent obey its own declared rules? | An agent that is *consistently wrong* |
| **Consistency** | Does it behave the same when the input is reworded? | An agent that is *erratically right* — the state that passes evals and fails in production |
| **Equivalence** | Does the replacement do what the incumbent did? | A migration that looks safe because outcomes match while behaviour silently changed |

---

## Three things that are actually different here

**1. It compares arguments, not just which tools ran.** Every published trajectory method compares action-type sequences. A payment of $48,200 instead of $4,820 calls the identical tools in the identical order and produces an identical confirmation message. It is invisible at that resolution. Comparison is typed per field — zero tolerance on money, ignore-list for timestamps, low severity for free text — and checked against **task ground truth**, so a drift that every run shares still fires. The closest prior work ([arXiv 2605.10516](https://arxiv.org/abs/2605.10516)) lists exactly this resolution as its own future work.

**2. A skipped step reports as a skip.** Naive position-by-position comparison of `fetch → match → dup → pay` against a run missing `dup` reports "at step 2, expected dup, got pay", which reads as a substitution. It was a skip, and skips and substitutions get fixed differently. Divergences are localised with Needleman-Wunsch alignment so the edit script is recovered correctly.

**3. The headline is a lower bound, not an average.** The certificate reports the 95% lower confidence bound on critical-invariant conformance under the **worst** perturbation. Worst case because averaging is how a dangerous perturbation gets buried. Critical-only because a missing log entry and a duplicate payment are not commensurable. A lower bound because a certificate should state what you can defend, not what you saw on a good day — which makes a small study certify lower than a large one, as it should.

---

## Try it in thirty seconds, no API key

The published study is committed. Every command below reads stored trajectories and makes **no model calls**.

```bash
git clone https://github.com/Bhargs24/plumbline && cd plumbline
pip install -e ".[dev]"
```

```bash
plumbline parity runs/parity-study plan_execute react
```

```bash
plumbline certify runs/parity-study --arm react
```

```bash
plumbline show runs/parity-study --trial tool_fault
```

```bash
plumbline report runs/parity-study plan_execute react -o report.html
```

That is the property that makes a published certificate checkable: take the trajectories file and rebuild the number yourself. CI does exactly this on every push, so if the committed evidence ever stops reproducing the published numbers, the build goes red.

---

## Point it at your own agent

Agent frameworks already emit OpenTelemetry spans containing what a trajectory needs. Both conventions are supported — OpenTelemetry GenAI (`gen_ai.*`) and OpenInference (`openinference.span.kind`, `tool.*`) — detected per span, because a real trace can carry spans from more than one instrumentation library.

```python
from plumbline.adapters.otel import load_trace_file, describe_coverage

trajs = load_trace_file("traces.json",
                        task_of=lambda spans: spans[0]["attributes"]["invoice.id"],
                        perturbation_of=lambda spans: "baseline")

print(describe_coverage(trajs))
# {'argument_comparison_available': False,
#  'notes': ['12 of 40 tool calls carry no structured arguments, so
#             argument-level comparison is unavailable for them.'], ...}
```

`describe_coverage` exists because ingested traces are frequently incomplete. Many instrumentations record which tool ran but not what it was called with. Reporting that as perfect argument agreement would be a lie of omission, so it says what it cannot measure instead.

---

## Running the study yourself

```bash
cp .env.example .env    # add ANTHROPIC_API_KEY
python experiments/determinism_study/run.py --arms plan_execute react --variants 4 --trials 2 --budget 11.00
```

About 768 runs and roughly $5. The spend cap is journalled to disk after every call and applies **across runs**, not per process, so re-running does not silently double your ceiling.

---

## Layout

```
src/plumbline/
  core/       trajectory model · typed argument comparison · sequence alignment
  spec/       the invariant DSL, with declared severity
  adapters/   Claude client (trial-keyed cache) · OpenTelemetry ingest
  perturb/    the perturbation library and the semantic-equivalence guard
  runtime/    parallel runner · response cache · cumulative spend cap
  analysis/   conformance · consistency · equivalence · Wilson · permutation tests
  report/     certificate and self-contained HTML report
agents/ap/    the system under test: sqlite tools, policy, three architectures
experiments/  the determinism study
runs/         738 committed trajectories — the evidence behind every number above
docs/         the Companion, the report, and the figures above (all regenerated)
```

---

## Honest scope

**One domain, one model, one policy.** Results are for `claude-haiku-4-5` on this AP task. The harness runs against any model you point it at; the claim is architectural, not a model ranking.

**Perturbations are a chosen finite set.** Passing them is evidence, not proof. An agent is only as certified as the perturbations somebody thought to apply.

**The equivalence guard is a model call** with an asymmetric failure mode: likelier to wave through a subtle meaning shift than reject a valid rewrite. It raises the floor on perturbation validity; it does not guarantee it.

**On prior art.** The idea that trajectory consistency under perturbation matters is not new, and [`DESIGN.md`](DESIGN.md) states exactly what is and is not novel here, with the landscape audited against [ReliabilityBench](https://arxiv.org/abs/2601.06112), [Consistency as a Testable Property](https://arxiv.org/abs/2605.10516), [Semantic Invariance in Agentic AI](https://arxiv.org/abs/2603.13173) and [MAESTRO](https://arxiv.org/abs/2601.00481). This is an operationalisation plus two extensions those papers name as open, not a new-science claim.

**Nine bugs were found building this**, and [the Companion](https://bhargs24.github.io/plumbline/companion.html) documents every one — including a spend cap that failed open, a harness that certified a control which had errored, and crashed runs that were being reported as a dramatic finding. Every one produced plausible output and none threw an obvious error, which is exactly the failure mode this project exists to detect.

---

Built by Bhargav Raghavendra · Apache-2.0
