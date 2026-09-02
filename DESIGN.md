# Plumbline — Design

*A plumb line does not measure a wall. It gives you a reference that is true, so
the wall's deviation from it becomes visible and measurable.*

---

## 1. The problem

An agent evaluation tells you the agent produced acceptable output on a fixed
test set. It does not tell you the agent will still run your controls when the
same request arrives worded differently.

That gap is not hypothetical. Published 2026 work finds that meaning-preserving
input changes degrade agent success rates measurably, and that trajectory-level
metrics detect problems that end-state success metrics miss entirely. The failure
mode is specific and nasty: the agent produces a confident, plausible, correct
looking answer while having skipped a step on the way.

In accounts payable, which is the worked example here, that looks like an agent
that pays an invoice without running the duplicate check. The payment succeeds.
The confirmation message is identical to a correct run. The ledger is short by
the value of the invoice, and nothing in an output-level evaluation will ever
show it.

## 2. What Plumbline does

You declare what must always be true of a run:

```python
MustCall("check_duplicate")
Ordering("match_purchase_order", then="schedule_payment")
CallAtMost("schedule_payment", 1)
ArgEquals("schedule_payment", "amount", from_context="expected_amount")
MustNotCall("schedule_payment", when=lambda ctx: not ctx["should_pay"])
```

Plumbline then tries to break those claims using input changes that a correct
agent must be indifferent to, and reports which invariant broke, under which
perturbation, at which named step, how often, and with what confidence interval.

The output is a bug report with a statistical bound attached, not a score.

## 3. Position relative to prior art

The idea that trajectory consistency under perturbation matters is not new, and
this document states that plainly rather than leaving it to be discovered.

| Work | What it does | What it leaves open |
|---|---|---|
| [Consistency as a Testable Property](https://arxiv.org/abs/2605.10516) (2605.10516) | Trajectory-level consistency under perturbation. JSD kernels, global alignment kernels, MMD. Tested on SWE-bench, Spider2, BFCL | No released tool. Runs on benchmarks, not on your agent. Compares action *type* sequences; its own future work asks for "granular trajectory similarity metrics capturing command content details beyond action type" |
| [ReliabilityBench](https://arxiv.org/abs/2601.06112) (2601.06112) | Perturbation, repeated runs, fault injection, 1,280 episodes | End-state equivalence only. A benchmark over foundation models |
| [Semantic Invariance in Agentic AI](https://arxiv.org/abs/2603.13173) (2603.13173) | Eight meaning-preserving transforms across seven models | Response-level invariance, not trajectory or policy conformance |
| [MAESTRO](https://arxiv.org/abs/2601.00481) (2601.00481) | Framework-agnostic trace export, run-to-run variance across 12 systems | No metamorphic perturbation. Finds architecture dominates reproducibility, which motivates the study here |
| LangSmith, Braintrust, Langfuse, Arize | Trace scoring, LLM-as-judge, failure clustering on fixed inputs | No native perturbation or invariance testing |

**The contribution is operationalization plus two extensions the prior art names
as open.** Not a new-science claim:

1. **Argument-level comparison against ground truth.** Prior methods compare
   which tools ran. A payment of $48,200 instead of $4,820 runs the identical
   tools in the identical order. It is invisible at action-type resolution, and
   because the drift can be shared by every run, it is also invisible to any
   method that compares runs only against each other. Plumbline compares tool
   arguments field by field with typed policies against task ground truth.
   The closest paper lists this resolution as future work.
2. **Policy conformance rather than distributional distance.** MMD between
   trajectory distributions is the right tool for a research question and the
   wrong output for an engineer. A named invariant, a named step, a count, and
   an interval is actionable.
3. **A tool you point at your own agent**, with a portable certificate that
   rebuilds from stored traces.

## 4. Design decisions worth defending

**The headline number is a lower bound, not an average.** The certificate reports
the 95% lower confidence bound on critical-invariant conformance under the
*worst* perturbation. Averaging across perturbations is how a dangerous one gets
buried. Averaging across severities makes a missing log entry commensurable with
a duplicate payment, which it is not. And a point estimate from 20 runs overstates
what you know. A lower bound makes a small study certify lower than a large one,
which is correct and removes the incentive to certify on a thin sample.

**Severity is declared, not inferred.** The tool cannot know that
`schedule_payment` is denominated in money and `post_audit_log` is not. You tell
it. Every aggregate is reported both overall and restricted to critical.

**Divergences are located by sequence alignment, not by position.** If the
reference path is `fetch, match, dup, pay` and a run omits `dup`, positional
comparison reports "at step 2, expected dup, got pay", which reads as a
substitution. It was a skip. Needleman-Wunsch alignment recovers the actual edit
script so a skipped control reports as `SKIPPED`, an extra call as `EXTRA`, and a
genuine swap as `SUBSTITUTE`. These get fixed differently, so they must be named
differently.

**The reference is the agent's own baseline behavior, per task.** Not the mode
across all runs. Taking the mode over everything lets a perturbation that affects
most runs redefine what normal means, and the divergence then disappears into the
reference.

**Numeric arguments default to zero tolerance.** On a payment instruction, "close
enough" is the defect. Widen it deliberately, per field, when a field genuinely is
approximate.

**Conformance and consistency are reported separately.** They answer different
questions and neither subsumes the other. An agent can be *consistently wrong*,
which conformance catches and consistency does not. An agent can be *erratically
right*, which consistency catches and conformance does not. Erratically right is
the state that passes evaluation and fails in production.

**Perturbations are verified, not assumed.** A reworded request that quietly asks
for something else makes a behavior change correct, and counting it as a defect is
a measurement error. The closest prior art names this as its main threat to
validity and handles it with careful manual design. Here, text-rewriting
perturbations pass through an independent model call that checks the rewrite asks
for the same thing about the same invoice. Failures are discarded and *counted*,
and the discard count appears in the certificate, because an engine that silently
drops a third of its variants is telling you something about itself.

**Caching is keyed on trial identity.** A cache keyed only on the request would
destroy the measurement: self-consistency is measured by issuing the same request
repeatedly, so serving those from one cached response returns a perfect 100% that
is an artifact of the cache. Replaying trial 3 replays trial 3 exactly; running a
new trial 4 samples afresh. Reproducible without manufacturing agreement.

## 5. The determinism study

One question: when you move an agent's control flow out of the model, how much
reliability do you buy, and where does the remaining risk go?

Held fixed: task set, tools, database, model, perturbation variants (generated
once and shared across arms), declared policy. Changed: who decides which step
runs next.

| arm | control flow | residual failure modes |
|---|---|---|
| `react` | the model decides every step | skip a control, pay a blocked invoice, pay twice, drift an amount |
| `plan_execute` | the model interprets the request; a fixed executor runs the procedure | identify the wrong invoice. Structural conformance holds *by construction* |
| `guarded` | the model decides; a deterministic policy layer can refuse a step before it executes | loop, give up, or reach a wrong disposition after being refused |

**On `plan_execute` conforming by construction.** This is stated openly because
it is the point rather than a flaw in the experiment. Putting control flow in the
runtime converts a probabilistic guarantee into a structural one. What the study
measures is where the residual risk *goes*: if interpretation misidentifies the
invoice under paraphrase, the executor will faithfully, auditably, and
irreversibly process the wrong invoice. Whether that happens, and how often, is
an empirical question this design refuses to assume the answer to.

An earlier iteration of this project attempted the same comparison with a
simulated agent whose steps were flipped between "stochastic" and "always
correct". That is circular: it proves that a step defined as always correct is
always correct. It was discarded. Every number here comes from a real model
calling real tools against a real database.

## 6. Honest limitations

- **Outcome equivalence is judged on ledger state**, which works because the
  worked example writes to a database. For agents whose output is prose, this
  needs a judge or embedding comparison, and that is not built.
- **The equivalence guard is a model call**, and its failure mode is asymmetric:
  more likely to wave through a subtle shift than to reject a valid rewrite. It
  raises the floor on perturbation validity, it does not guarantee it.
- **One domain, one model family.** The architectural claim would be stronger
  confirmed across a second domain and a second model provider. The harness is
  built to allow that; the study has not yet run it.
- **The invariant DSL covers ordering, presence, cardinality, and argument
  constraints.** It does not cover temporal windows, cross-run state, or
  multi-agent handoffs.
- **Perturbations are a chosen finite set.** Passing them is evidence, not proof.
  An agent is only as certified as the perturbations you thought to apply.

## 7. Regulatory context

[EU AI Act Article 15](https://artificialintelligenceact.eu/article/15/) requires
high-risk systems to achieve and document an appropriate level of accuracy and
robustness, with metrics declared in technical documentation. Those obligations
became enforceable on 2 August 2026, and the harmonised standards addressing
Article 15 are still being finalised. A certificate that names its perturbations,
reports bounds rather than point estimates, and rebuilds from stored evidence is
the shape of artifact that obligation wants. This is context for why the output
format is what it is, not a claim of legal compliance.

## 8. Build order

1. Measurement core: trajectory model, typed argument comparison, alignment. **done**
2. Invariant DSL and conformance analysis. **done**
3. System under test: AP exception agent over sqlite, three architectures. **done**
4. Perturbation engine with equivalence guard. **done**
5. Statistics: Wilson intervals, permutation tests. **done**
6. Certificate, provenance, CLI, tests. **done**
7. The determinism study against a live model. **done** — three studies,
   2,082 committed trajectories, and a retraction (see the README)
8. OpenTelemetry / OpenInference span ingestion, so the harness attaches to an
   existing agent stack with no integration work. **done** — `adapters/otel.py`,
   `POST /ingest/traces`, with coverage honestly reported
9. Second domain **built** (`plumbline.domains.accounts_payable`, not yet run
   against a model); second model provider **not started**

## 9. Origin

The approach generalises two things. An evaluation project where a question bank
passed every surface metric while being substantially gameable, which taught that
a metric checking the *shape* of an output cannot check its truth, and only
re-deriving the result under stress can. And a published patent on deterministic
orchestration of LLM control flow (IN202641086881), whose claim is that
reproducibility and auditability come from moving control flow off the model.
Plumbline is the measuring instrument for that claim, built so the claim can come
back false.
