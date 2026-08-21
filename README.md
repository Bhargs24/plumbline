# Plumbline

**Metamorphic conformance testing for LLM agents.** Declare the invariants an agent must never violate; break them with semantics-preserving input transformations; localise every violation to a named step with a confidence interval.

[![tests](https://github.com/Bhargs24/plumbline/actions/workflows/tests.yml/badge.svg)](https://github.com/Bhargs24/plumbline/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue)
![tests](https://img.shields.io/badge/tests-70-brightgreen)
![deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![evidence](https://img.shields.io/badge/evidence-738%20trajectories%20committed-informational)
![licence](https://img.shields.io/badge/licence-Apache--2.0-lightgrey)

---

## 1 · Result

Controlled comparison, *n* = 738. Two agent architectures over an identical task set, tool surface, backing store, model (`claude-haiku-4-5`) and perturbation variant set. Single manipulated variable: **locus of control-flow decision**.

<img src="docs/assets/results.svg" alt="Outcome correctness by perturbation for both architectures with 95% Wilson intervals. All conditions at 100% except tool_fault, where plan_execute falls to 81.2% and react holds at 98.2%." width="100%">

| Condition | `plan_execute` (deterministic executor) | `react` (free-form ReAct) |
|---|---|---|
| `baseline` | 100.0% [94.3, 100.0] *n*=64 | 100.0% [94.3, 100.0] *n*=64 |
| `paraphrase` | 100.0% [94.3, 100.0] *n*=64 | 100.0% [94.3, 100.0] *n*=64 |
| `distractor` | 100.0% [94.3, 100.0] *n*=64 | 98.3% [90.9, 99.7] *n*=58 |
| `decoy_tools` | 100.0% [94.3, 100.0] *n*=64 | 100.0% [93.6, 100.0] *n*=56 |
| `sampling` | 100.0% [94.3, 100.0] *n*=64 | 100.0% [93.6, 100.0] *n*=56 |
| **`tool_fault`** | **81.2% [70.0, 88.9]** *n*=64 | **98.2% [90.6, 99.7]** *n*=56 |

```
H₀: outcome correctness is independent of architecture, under tool_fault
    Δ = +17.0 pp    p = 0.0029    two-sided permutation test, 20 000 relabellings

control (all conditions except tool_fault)
    Δ = −0.3 pp     p = 0.48      not significant
```

Effect is localised to a single condition with the control flat, which is the signature of a real effect rather than an analysis artefact. `plan_execute` failure mode is systematic and unidirectional: 12/12 errors under `tool_fault`, all `should_pay → held`, on exactly the three payable invoices, four occurrences each.

<details>
<summary><b>Equivalence view of the same runs</b></summary>

| Measure | Value | 95% CI | *n* |
|---|---|---|---|
| same terminal ledger state | 94.9% | [92.1, 96.8] | 354 |
| same effect path | 21.5% | [17.5, 26.0] | 354 |
| same tool arguments, given shared path | 85.5% | [75.9, 91.7] | 76 |
| incumbent self-consistency | 96.9% | [94.6, 98.2] | 384 |

The replacement reaches the same terminal state 94.9% of the time while traversing a different path 78.5% of the time. **Outcome equivalence is not behavioural equivalence**, and a 30-day parallel run measures only the former.
</details>

### The divergence, at trace level

`INV-7002`: three-way match reconciles, not a duplicate, vendor active. Ground truth `should_pay = True`, `expected_amount = 4500.00`. One transient fault injected at the toolbox boundary.

```
plan_execute                            react
────────────────────────────────────    ────────────────────────────────────
decision  interpret_request             tool_call fetch_invoice
tool_call fetch_invoice                 tool_call match_purchase_order
tool_call match_purchase_order  ✗ 503   tool_call check_duplicate
tool_call check_duplicate               tool_call check_vendor_status  ✗ 404
tool_call check_vendor_status             {"vendor_id": "ASHFIELD_LOGISTICS"}
tool_call flag_exception        ← ✗     tool_call check_vendor_status  ✓ retry
tool_call post_audit_log                  {"vendor_id": "V-101"}
                                        tool_call schedule_payment 4500.00 ✓
ledger: {paid: false,                   tool_call post_audit_log
         exception_raised: true}
                                        ledger: {paid: true, amount: 4500.00,
                                                 payment_count: 1}
```

The executor treats a non-null `error` on a control as a blocking condition. It has no retry branch, because retry was not in the specification. The agent emitted a malformed `vendor_id`, observed the error, and re-emitted with a valid identifier — recovery behaviour that appears nowhere in its system prompt.

> **Threat to validity, stated first.** Fail-closed-on-tool-error is a property of *this executor*, not of deterministic orchestration. An executor with a retry policy does not exhibit it. The defensible claim is narrower: **a deterministic system's behaviour is bounded by its author's anticipation set.** It handled every perturbation inside that set at 100% and failed on the one outside it. The agent's out-of-distribution recovery is the same mechanism that permits control skipping elsewhere. Determinism trades stochastic failure for specification failure; it does not eliminate failure.

📄 **[Full report](https://bhargs24.github.io/plumbline/report.html)** · 📘 **[Companion, 55pp](https://bhargs24.github.io/plumbline/companion.html)** · 📐 **[Method &amp; prior art](DESIGN.md)**

---

## 2 · Why an instrument was needed

Let *A* be an agent, *x* a task input, *τ(A, x)* the trajectory it produces, and *o(τ)* the terminal state.

Existing evaluation computes either **`score(o(τ(A, x)))`** for *x* in a fixed set, or **`score(τ(A, x))`** against a reference, for *x* in a fixed set. Both quantify over a **fixed** input set. Neither can express a claim about behaviour under *x′* ≈ *x*.

Metamorphic testing supplies the missing quantifier. Given a transformation *T* with a declared metamorphic relation, the assertion is over the *relation between* runs:

```
∀ x ∈ X, ∀ T ∈ 𝒯 :   conforms(τ(A, T(x))) = conforms(τ(A, x))
```

This holds without an oracle for the correct trajectory, which is the property that makes it applicable where reference trajectories do not exist. See Chen et al., 1998, for the origin.

---

## 3 · Measurement model

### 3.1 Trajectory algebra

A trajectory is a finite sequence of steps `s = (kind, name, args, output, error)`. Three projections, deliberately distinct:

| Projection | Definition | Used for |
|---|---|---|
| `path()` | `⟨(kind, name)⟩` over `kind ∈ {tool_call, decision}` | intra-architecture comparison |
| `effect_path()` | `⟨(kind, name)⟩` over `kind = tool_call` | **cross-architecture** comparison |
| `canonical_args()` | per-step `args` under a typed normalisation | argument-level divergence |

`effect_path()` exists because deliberation is not behaviour. `plan_execute` emits an `interpret_request` decision with no analogue in `react`; `guarded` emits `blocked:*` entries for refused actions. Including them scored two architectures that performed identical actions at **0% path agreement**. Cross-architecture comparison is defined over world-touching steps only.

### 3.2 Effect semantics for failed calls

A step with non-null `error` **did not occur**. Formally, `called(n) ≜ ∃ s ∈ τ : s.name = n ∧ s.error = ∅`.

This propagates through every invariant: a failed call cannot satisfy `MustCall`, cannot discharge the precondition of `Ordering`, does not increment `CallAtMost`, and is excluded from `ArgEquals`. A harness that treats an errored control as executed certifies a control that never ran, which is strictly worse than no harness. Regression test: `test_failed_control_does_not_satisfy_must_call`.

### 3.3 Three analyses

| Analysis | Predicate | Detects | Blind to |
|---|---|---|---|
| **Conformance** | `τ ⊨ Σ` for policy Σ | consistently-wrong agents | agents that are wrong in a way Σ does not encode |
| **Consistency** | `τ(A, T(x)) ≡ τ(A, x)` | erratically-right agents | systematic error shared by all runs |
| **Equivalence** | `τ(B, T(x)) ≡ τ(A, T(x))` | silent behavioural change during migration | both systems being wrong identically |

Conformance and consistency are independent: an agent can be consistently wrong (100% consistency, 0% conformance) or erratically right. Both cases have dedicated tests.

---

## 4 · Algorithms

### 4.1 Divergence localisation — Needleman-Wunsch

Positional comparison misattributes omissions. Reference `⟨fetch, match, dup, pay⟩` against candidate `⟨fetch, match, pay⟩` yields "index 2: expected `dup`, got `pay`" — a substitution report for what is an omission, with every subsequent index also misaligned.

Global alignment with affine-free scoring recovers the edit script:

```
match = +2      mismatch = −1      gap = −2
```

Chosen so a single omission resolves as one gap rather than cascading into *k* mismatches. `align()` returns ops in `{MATCH, SUBSTITUTE, SKIPPED, EXTRA}`, each carrying reference and candidate indices. Complexity **O(nm)** time and space over control-step counts, which are single- or low-double-digit; the quadratic term is irrelevant at this scale.

Regression test: `test_alignment_one_skip_does_not_cascade` asserts exactly one divergence.

### 4.2 Typed argument comparison

Single-operator equality both over- and under-reports. `"INV-1029"` vs `"inv-1029 "` is not divergence; `amount=49.0` vs `amount=490.0` is the most expensive divergence in the system. Comparison is typed per field:

| Policy | Semantics | Default severity |
|---|---|---|
| `EXACT` | normalise whitespace + case, then equality | high |
| `NUMERIC` | `abs_tol`, `rel_tol`, **both defaulting to 0** | critical |
| `TEXT` | normalised equality, excluded from consistency aggregates | low |
| `IGNORE` | dropped before comparison | — |
| `SET` | order-insensitive multiset equality | medium |

Unclassified fields fall back to zero-tolerance `NUMERIC` for numbers and `EXACT` otherwise — biased toward surfacing for triage rather than silent forgiveness.

**Argument checks compare against task ground truth, not against sibling runs.** If every run emits `amount=48200.0` against a true `4820.00`, inter-run comparison reports perfect agreement. Only a ground-truth predicate fires. Test: `test_amount_drift_survives_a_matching_path` asserts 100% consistency with 0% conformance on exactly this case.

### 4.3 Statistics

**Wilson score interval** ([Wilson, 1927](https://doi.org/10.1080/01621459.1927.10502953)) rather than the normal approximation, which produces intervals exceeding [0,1] and understates uncertainty near the boundaries — precisely where agent results cluster.

```
        p̂ + z²/2n  ±  z·√( p̂(1−p̂)/n + z²/4n² )
lo,hi = ──────────────────────────────────────────
                    1 + z²/n
```

`wilson(40, 40) → 100.0% [91.2, 100.0]`. Forty clean runs is consistent with a system failing 1-in-12.

**Two-sided permutation test**, 20 000 relabellings, add-one smoothed so `p > 0` always. Distribution-free, appropriate for small correlated binary samples where a normal approximation is unjustified.

**Certified bound.** The headline is not the point estimate:

```
certified ≜ min  lower₉₅( conformance_critical | perturbation = P )
                P ∈ 𝒯
```

Worst-case over perturbations (averaging conceals the dangerous one), critical severity only (a missing audit log and a duplicate payment are not commensurable), and a lower bound (states what is defensible, and self-penalises small *n* — a 0.95 bound requires ≈73 clean runs per condition).

---

## 5 · Perturbation suite

Each transformation declares the metamorphic relation it preserves.

| `T` | Transformation | Declared invariant |
|---|---|---|
| `baseline` | identity, repeated | self-consistency; separates sampling noise from brittleness |
| `paraphrase` | LLM rewrite + independent equivalence verification | correct step set is wording-invariant |
| `distractor` | irrelevant text pre/post-pended | correct behaviour is context-noise-invariant |
| `tool_fault` | one call fails once (503 / 429 / RST), then recovers | outcome survives recoverable faults |
| `decoy_tools` | plausible unused tools added to schema list | step set is invariant to available-tool set |
| `sampling` | identical input, elevated temperature | correct behaviour is decode-invariant |

**Semantic-equivalence guard.** The dominant threat to metamorphic validity is a transformation that silently alters the task, converting a correct behaviour change into a false positive. `ParaphraseWithGuard` routes every candidate through an independent model call receiving both original and rewrite, judging same-referent and same-required-action. Rejects are **discarded and counted**, and the discard count is carried into the certificate. The guard's failure mode is asymmetric — likelier to admit a subtle shift than reject a valid rewrite — so it raises the validity floor without guaranteeing it.

Faults inject at the toolbox boundary via a pre-call hook, so neither agent nor tool implementation is aware. The agent observes a genuine error from a genuine call.

---

## 6 · Architecture

<img src="docs/assets/architecture.svg" alt="Pipeline: an agent under test, or ingested OpenTelemetry spans, feeds the perturbation engine, producing trajectories, feeding conformance, consistency and equivalence analyses." width="100%">

```
src/plumbline/
├── core/
│   ├── trajectory.py    Step · Trajectory · TrajectoryStore (JSONL) · projections
│   ├── compare.py       FieldPolicy · ArgSchema · typed diff → ArgDiff
│   └── align.py         Needleman-Wunsch → EditOp{MATCH,SUBSTITUTE,SKIPPED,EXTRA}
├── spec/invariants.py   MustCall · MustNotCall · Ordering · CallAtMost
│                        ArgEquals · ArgSatisfies · PolicySpec · severity lattice
├── adapters/
│   ├── llm.py           Anthropic client · trial-keyed cache · budget gate
│   └── otel.py          OTel GenAI + OpenInference span ingest · coverage report
├── perturb/library.py   6 transformations + semantic-equivalence guard
├── runtime/
│   ├── runner.py        thread-pool executor · per-trial isolated toolbox
│   ├── cache.py         content-addressed, keyed on (request, trial, turn)
│   └── budget.py        journalled cumulative spend cap · thread-safe
├── analysis/
│   ├── conformance.py   violation grouping · per-perturbation · per-invariant
│   ├── consistency.py   modal-baseline reference · divergence localisation
│   ├── equivalence.py   exact variant pairing · retirement bound
│   └── stats.py         Wilson · permutation · bootstrap
└── report/
    ├── certificate.py   JSON schema v1 · provenance · evidence hash
    └── html.py/build.py self-contained report generator
```

**Zero runtime dependencies.** `anthropic` is an extra, required only for live capture. Analysis, certification and reporting run on the standard library, so the measurement core is auditable without a dependency tree.

### 6.1 Cache keying — a correctness constraint, not an optimisation

Self-consistency is measured by issuing an identical request *k* times. A cache keyed on request content alone serves one recorded response to all *k*, returning self-consistency ≡ 100% as a cache artefact.

The key is therefore `H(model, system, messages, tools, temperature, trial_id, turn)`. Replaying trial *i* is exact and free; running trial *i+1* samples afresh. Reproducibility without manufactured agreement.

### 6.2 Budget

Journalled to disk after **every** call and reloaded at startup, so `max_usd` bounds spend across processes rather than per-process. Both properties exist because their absence caused a documented 6× cost under-report; see the Companion, Chapter 50.

---

## 7 · Ingesting external traces

Agent frameworks already emit spans containing what a trajectory requires. Both conventions are supported and detected **per span**, since production traces carry spans from multiple instrumentation libraries.

| Field | OTel GenAI | OpenInference |
|---|---|---|
| tool name | `gen_ai.tool.name` | `tool.name` |
| arguments | `gen_ai.tool.call.arguments` | `tool.parameters` |
| span kind | `gen_ai.operation.name` | `openinference.span.kind` |
| tokens | `gen_ai.usage.{input,output}_tokens` | `llm.token_count.*` |

```python
from plumbline.adapters.otel import load_trace_file, describe_coverage

trajs = load_trace_file(
    "traces.json",
    task_of=lambda spans: spans[0]["attributes"]["invoice.id"],
    perturbation_of=lambda spans: "baseline",
)
describe_coverage(trajs)
# {'argument_comparison_available': False,
#  'with_structured_arguments': 28, 'tool_calls': 40,
#  'notes': ['12 of 40 tool calls carry no structured arguments, so
#            argument-level comparison is unavailable for them.']}
```

`describe_coverage()` reports what the traces **cannot** support. Many instrumentations record tool identity but not arguments; comparing absent argument dicts and reporting perfect agreement would be a lie of omission. Error status is read from OTLP `status.code = 2` and from `exception` span events, since exporters differ.

---

## 8 · Position relative to prior art

| Work | Resolution | Perturbation | Released tool | Runs on your agent |
|---|---|---|---|---|
| [Consistency as a Testable Property](https://arxiv.org/abs/2605.10516) | action type | ✅ | ❌ | ❌ benchmarks |
| [ReliabilityBench](https://arxiv.org/abs/2601.06112) | terminal state | ✅ | ❌ | ❌ benchmark |
| [Semantic Invariance in Agentic AI](https://arxiv.org/abs/2603.13173) | response | ✅ | ❌ | ❌ |
| [MAESTRO](https://arxiv.org/abs/2601.00481) | trace export | ❌ | ✅ | ✅ |
| LangSmith · Braintrust · Langfuse · Arize | trace, fixed inputs | ❌ | ✅ | ✅ |
| **Plumbline** | **action type + arguments** | ✅ | ✅ | ✅ |

**The delta is resolution.** Published trajectory methods compare action-type sequences. A payment of `48200.00` against a true `4820.00` traverses an identical action-type sequence and emits an identical-looking confirmation; it is unobservable at that resolution and unobservable to inter-run comparison when the drift is systematic. The closest work names this exact extension as open: *"granular trajectory similarity metrics capturing command content details beyond action type."*

`DESIGN.md` states what is and is not novel here in full. This is an operationalisation plus two extensions the literature names as open, not a new-science claim.

---

## 9 · Reproduce

All 738 trajectories are committed. Every command below reads stored traces; **none makes a model call**.

```bash
git clone https://github.com/Bhargs24/plumbline && cd plumbline
pip install -e ".[dev]" && pytest -q                     # 70 tests, offline
```

```bash
plumbline parity  runs/parity-study plan_execute react   # equivalence proof
plumbline certify runs/parity-study --arm react          # conformance certificate
plumbline show    runs/parity-study --trial tool_fault   # step-level trace
plumbline report  runs/parity-study plan_execute react -o report.html
```

CI executes `certify` and `parity` against the committed evidence on every push. Divergence between the stored traces and the published numbers turns the build red.

Live capture:

```bash
cp .env.example .env   # ANTHROPIC_API_KEY
python experiments/determinism_study/run.py \
    --arms plan_execute react --variants 4 --trials 2 --budget 11.00
```

≈768 trials, ≈$5, hard-capped cumulatively.

---

## 10 · Limits

**External validity.** One domain, one model (`claude-haiku-4-5`), one policy, eight tasks. The claim is architectural; generalisation across model families and domains is untested.

**Perturbation coverage.** 𝒯 is a chosen finite set. Conformance under 𝒯 is evidence, not proof. An agent is certified only against the transformations someone thought to apply.

**Outcome equivalence** is computed on ledger state, exact and objective because the specimen writes to a database. Prose-output agents require semantic comparison, which is deliberately not implemented rather than implemented badly.

**Guard fallibility.** The semantic-equivalence guard is a model call with asymmetric error.

**Nine defects were found during construction**, each documented in the Companion, Chapter 50 — a spend cap that failed open, a harness certifying an errored control, crashed runs surfacing as a dramatic finding, a billing artefact that inverted a headline. Every one produced plausible output; none raised at top level. That is precisely the failure class this instrument targets, and the project committed it repeatedly.

---

Built by Bhargav Raghavendra · Apache-2.0
