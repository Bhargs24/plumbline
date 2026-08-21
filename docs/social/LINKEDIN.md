# LinkedIn post kit

Everything below is ready to paste. Images are in this folder.

---

## How to post it

**Format: document (carousel) post.** Upload `plumbline-carousel.pdf` as a document.
LinkedIn renders it as a swipeable carousel, which gets materially more dwell time
than a single image, and dwell time is what the feed rewards.

**Put no link in the post body.** LinkedIn suppresses reach on posts with outbound
links. Post the text, then immediately add the links as your own first comment.

**Post timing:** Tuesday to Thursday, 8–10am in the timezone where most of your
audience works. Reply to every comment in the first two hours; early engagement
decides how far it travels.

---

## Post text — primary version

> Copy from the line below. The first two lines are what shows before "see more",
> so they carry the whole click decision.

```
Everyone testing AI agents is testing the wrong failure.

I ran 738 trials to check, and the perturbation everyone worries about did nothing at all.

The setup: two accounts-payable automations. Same invoices, same tools, same database, same model, same inputs. One variable changed between them — who decides which step runs next.

Arm A: the model picks every step, freely. Classic ReAct.
Arm B: the model reads the request once, then fixed code runs the procedure. Control flow out of the model entirely.

Then I changed the input in five ways that must NOT change correct behaviour. Reworded the request. Added irrelevant text. Added unused tools. Raised the temperature. And failed one tool call once, transiently.

Rewording broke nothing. Distractors broke nothing. Decoy tools broke nothing. Temperature broke nothing.

A single transient network error broke the deterministic one.

Under an injected 503, the deterministic executor reached the correct outcome 81.2% of the time. The free-form agent reached it 98.2%. A 17-point gap, p = 0.0029. On every other condition the two were statistically indistinguishable (p = 0.48).

Here is the run that explains it.

A clean invoice. Every check passes. Correct action: pay $4,500.

The deterministic executor's three-way match hit a 503. It had no rule for that, so it treated the error as a blocker and held a clean invoice for human review. Safe. Wrong. And it silently converts clean invoices into somebody's Monday.

The agent's vendor lookup also failed — it used the vendor name instead of the ID. Nobody told it to retry. It retried anyway, with the right argument, and paid correctly.

The caveat, before anyone asks: fail-closed is a design choice in MY executor, not a property of deterministic systems. One with retry logic would not fail this way.

Which is exactly the finding.

A deterministic system does precisely what its author anticipated, and nothing else. It handled every perturbation it was written for, perfectly. It failed on the one condition its author didn't think of. The agent improvised a recovery nobody specified — and that is the same capability that lets it skip a control somewhere else.

Determinism doesn't buy reliability. It buys predictability. It moves the failure from the model to the specification.

The uncomfortable part for anyone shipping agents: both runs produce confident, well-structured logs. Both look fine. Your output evals pass both, because output evals grade the answer and the answer was right. Trace tools grade trajectories on inputs you fixed in advance, so the wording never changes and they can't tell you the agent behaves differently when it does.

In accounts payable, a control that didn't execute is an audit finding whether or not the money was right.

All 738 trajectories are committed. Every number re-derives from stored traces with no API key. CI runs that check on every push, so if the evidence stops reproducing the published numbers the build goes red.

Genuine question for anyone running agents in production: which perturbation would break yours? I have five. I'm fairly sure I'm missing the important one.

#AIEngineering #LLMOps #AIAgents #MLOps #AIReliability
```

---

## First comment (post immediately after)

```
Repo, full report and a 55-page write-up covering everything from the method to the nine bugs I found building it:

github.com/Bhargs24/plumbline

The bugs chapter is the honest part. A spend cap that failed open. A harness that certified a control which had errored. Crashed runs being reported as a dramatic finding. Every one produced plausible output and none threw an obvious error — which is exactly the failure mode the project exists to detect.
```

---

## Shorter version, if you want a single-image post

```
I ran 738 trials on two AI agents to find out what determinism actually buys you.

Same invoices. Same tools. Same model. One variable: who decides which step runs next.

Rewording the request broke nothing. Distractors, decoy tools, temperature — nothing.

A single transient network error broke the DETERMINISTIC one. 81.2% vs 98.2%, p = 0.0029.

The deterministic executor hit a 503 on a routine check, had no rule for it, and held a clean $4,500 invoice for human review. The agent hit its own error, improvised a retry nobody specified, and paid correctly.

Caveat before anyone asks: fail-closed is a design choice in my executor, not a property of determinism.

Which is the finding. A deterministic system does exactly what its author anticipated and nothing else. Determinism doesn't buy reliability — it buys predictability, and moves the failure from the model to the specification.

Which perturbation would break your agent?

#AIEngineering #LLMOps #AIAgents #AIReliability
```

Pair it with `slide-5.png`, the trajectory diff. It is the single most legible image
of the set and needs no context to land.

---

## Images

| File | Use |
|---|---|
| `plumbline-carousel.pdf` | The document post. All 8 slides. |
| `slide-1.png` | Hook. Good as a standalone if you post text-plus-image. |
| `slide-3.png` | The chart. Best single image for a technical audience. |
| `slide-4.png` | The headline number and statistics. |
| `slide-5.png` | **The trajectory diff. The strongest single image.** |
| `slide-6.png` | The caveat and the honest reading. |

---

## Expected pushback, and how to answer

**"Doesn't Patronus / Braintrust / LangSmith already do this?"**
They do scenario generation and trace scoring on fixed inputs. This is a different
axis: hold the scenario fixed, vary only the surface form. And this isn't a product
pitch, it's a result. Nobody has published this measurement.

**"Your deterministic arm was just badly written."**
Yes, in a specific way, and the post says so before anyone asks. That IS the finding:
a deterministic system's behaviour is bounded entirely by what its author thought of.

**"n is too small / one model / one domain."**
Correct, and it's stated in the limits. Haiku 4.5, one AP domain, eight invoices.
The harness runs against any model. The claim is architectural, not a model ranking.

**"How do you know your perturbations preserve meaning?"**
Every reworded request is verified by an independent model call that sees the original
and the rewrite. Failures are discarded and counted, and the count is in the report.
The guard is not infallible and its failure mode is asymmetric.

---

## Regenerating

```bash
python docs/make_social.py
```

Slides render from the committed run data, so a figure posted publicly cannot drift
from the result in the repository.
