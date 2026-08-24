# LinkedIn post kit

Everything below is ready to paste. Images are in this folder.

Written to be understood by someone who has never seen the repository. The
first line says what was built, the second says what it caught. The regulatory
argument comes after the finding, not before it, because a reader who does not
already know the project will not stay for a framing they cannot place.

Design is a printed workpaper rather than a terminal: light ground, IBM Plex
Sans headings, monospace for anything quoted from the tool. The subject is a
financial control test and the audience is as likely to be a controller as an
engineer. It is also the opposite of every other technical carousel in the
feed, which does not hurt.

The retraction is in the post as a demonstration of method rather than as the
finding. Note that the corrected result is *favourable* to deterministic
automation: once the coded arm had the retry any real system ships, it scored
100.0%. Do not let the deck read as an argument against determinism, because
the data is not one.

---

## How to post it

**Format: native image post, not a document.** Attach the four PNGs in
`post/` in order. LinkedIn renders two to four images as a tappable grid in
feed and keeps the whole post native, which the feed treats better than a
document upload and far better than an outbound link.

Each image is built to stand alone. A reader who only ever sees `post-1.png`
in the feed, or who opens `post-3.png` first, still gets a complete claim,
because every image repeats the context and carries the repo handle. That is
the difference between these and the carousel slides, which assume order.

**Order matters even in a grid.** `post-1` is the finding and does the most
work; it is the one most likely to be shown alone. `post-2` closes the "your
prompt was vague" objection. `post-3` is the arithmetic. `post-4` is the
deliverable.

**No link in the post body.** LinkedIn suppresses reach on posts carrying an
outbound link. Post the text, then put the repo link in your own first
comment, within a minute or two.

**Timing:** Tuesday to Thursday, 8 to 10am in the timezone where most of your
audience works. Be at your desk for the two hours after: reply to every
comment, because early engagement decides how far it travels.

**The hook is the first ~200 characters.** That is all that shows before
"see more". Everything else only gets read if that earns it.

---

## Post text

```
An AI agent that reached the correct outcome on 99.4% of its runs skipped a mandatory control on 16 of 48 runs of the same invoice.

Same invoice, same instructions, correct answer every time. A third of those runs never ran the supplier validation that gates the payment. Across all 354 runs it was 5.1%, against a tolerable deviation rate of zero.

Output evals grade the answer, and the answer was right. Nothing in an eval, or a trace viewer showing one run at a time, detects a check that did not happen.

The instruction was not vague. The policy names the exact excuse and forbids it: run all four checks "even when an earlier check has already told you what the disposition will be. A control you skip because you predicted its result is a control that did not run."

It skipped anyway, logged that it had, and closed the case.

Auditors may test a conventional automated control once and rely on it for the period, because a deterministic system generalises from one run. A language model has no stable baseline, so that concession is not available. And one clean test bounds almost nothing: at 95% confidence, one clean run leaves the true failure rate as high as 95%. Getting under 1% takes 299 consecutive clean runs.

Nobody is running 299.

A control an auditor cannot rely on is one you have to staff a human against, which removes the reason to automate it. So I built the harness that runs them: hold the transaction fixed, vary only what cannot change the decision, and report per control whether it executed and where it did not.

On rigour: my first headline said the deterministic baseline lost by 17 points. Before publishing I checked whether that baseline was fair. It was not, so I fixed it and re-ran, and the deterministic arm went to 100.0%. I had been measuring my own missing error handling.

2,082 live runs, all committed and independently checkable.

I want to build agents that have to survive an audit. If that is your problem, I would like to talk.

#AIAgents #AIGovernance #InternalAudit #LLMOps #SOX #FinanceAutomation
```

About 2,050 characters against LinkedIn's 3,000 limit. Long enough to carry the argument, short enough not to exhaust it:
the carousel carries the detail, and a long body competes with it rather
than selling the swipe. `python docs/check_post.py` recounts it and checks
the figures against the run data.

---

## First comment (post immediately after)

```
Repo, the full write-up, and the control attestation behind these numbers:

github.com/Bhargs24/plumbline

Method is metamorphic testing (Chen, 1998): hold the transaction fixed, apply transforms that must not change the correct decision, and check whether the controls still executed. Claude Haiku 4.5, one procure-to-pay domain, 2,082 runs across three studies, every trajectory committed and re-derivable.

The harness is model-agnostic and ingests OpenTelemetry GenAI or OpenInference traces directly, so it runs against whatever instrumentation you already have. Scope and method are in section 9.
```

---

## Shorter version, for a single-image post

Pair with `slide-2.png`, the 99.4% PASS beside the FAIL. It is the strongest
image in the set and needs no context at all.

```
An AI agent that reached the correct outcome on 99.4% of its runs skipped a mandatory control on 16 of 48 runs of the same invoice.

Same invoice, same instructions, correct outcome every time. A third of those runs never ran the supplier validation that gates the payment. Across all 354 runs it was 5.1%, against a tolerable deviation rate of zero.

Output evals grade the answer, and the answer was right. Nothing in an eval detects a check that did not happen.

Auditors may test a conventional automated control once and rely on it for the period, because deterministic systems generalise from one run. A language model does not, and one clean test bounds almost nothing: at 95% confidence, one clean run leaves the true failure rate as high as 95%. Getting under 1% takes 299 consecutive clean runs.

Nobody is running 299.

#AIAgents #AIGovernance #InternalAudit #LLMOps #SOX #FinanceAutomation
```

---

## Tags

**Hashtags.** LinkedIn weights the first three most, so those three should
reach the audience you actually want rather than the biggest one.

| Tag | Why |
|---|---|
| `#AIAgents` | high volume, and the crowd most likely to argue, which is engagement |
| `#AIGovernance` | the buyers for this, and growing fast |
| `#InternalAudit` | small room, exactly the right room, almost no AI content in it |
| `#LLMOps` | the people who run evals and will push back on the method |
| `#SOX` | narrow and precise |
| `#FinanceAutomation` | reach into finance operations |

Six is the right number. More reads as reach-farming.

**On tagging companies.** The post is now safe to put in front of a vendor
whose pitch is deterministic automation, because the retraction paragraph ends
on the deterministic arm scoring 100.0% rather than on the effect vanishing.
That distinction matters: the earlier draft could be read as "his data shows
determinism buys nothing", which is both wrong and the opposite of what you
want a deterministic-automation company to take away.

If you tag a company, tag the company and not individuals, and only if the
post stands without it. Do not tag auditors, Big Four firms, or the PCAOB:
the post cites public guidance and does not need their endorsement, and
tagging a regulator reads as claiming an approval you do not have.

**The closing line is the ask.** "I want to build agents that have to survive
an audit. If that is the problem you are working on, I would like to talk."
Without it the post reads as a product launch, and a reader who might hire you
spends their attention wondering whether you are selling or raising instead.

---|---|
| `#AIEngineering` | broadest technical reach |
| `#LLMOps` | the people who run evals and will argue with you |
| `#AIAgents` | high volume, brings the sceptics |
| `#AIGovernance` | the buyers, growing fast |
| `#SOX` | small but exactly the right room |
| `#InternalAudit` | ditto, and almost nobody posts AI content there |
| `#FinTech` | reach into finance operations |
| `#AIReliability` | niche but on-topic |

Use six to eight. More than that reads as reach-farming.

**Accounts worth tagging in the post body**, only where it is genuinely
relevant, because an unearned tag costs more than it gains:

- **Kognitos** if you are targeting them, but tag the company, not individuals,
  and only if the post stands on its own without it.
- The **maintainers of any eval tool you name**, if you name one. Do not name
  competitors to criticise them. The post already argues a different axis
  without needing a foil.

**Do not tag** auditors, Big Four firms, or the PCAOB. The post cites public
guidance and does not need their endorsement, and tagging a regulator reads as
a claim of approval you do not have.

---

## Expected pushback, and how to answer

**"So your finding was wrong?"**
The first one was, and the post leads with that. The retraction is what let me
find the real result. Catching a strawman baseline in your own work before
anyone else does is the whole job.

**"Doesn't Patronus / Braintrust / LangSmith already do this?"**
They generate scenarios and score traces on inputs fixed in advance. This holds
the scenario fixed and varies only the surface form, then reports the result as
a control test. Different axis, and complementary rather than competing.

**"18 out of 354 is 5%. Why does that matter if the outcome was right?"**
Because the tolerable deviation rate on a cash-disbursement control is zero,
and because a control that did not execute is an audit finding independent of
the payment. That is not my opinion, it is how operating effectiveness is
tested.

**"n is too small / one model / one domain."**
Correct, and stated in the limits on the last slide. The harness runs against
any model. The claim is architectural.

**"Your agent is just badly prompted."**
Possibly. That is a fixable instance of a general problem, and the general
problem is that you cannot tell whether it is fixed by looking at outcomes.
Show me the evidence that the control operates on every transaction.

**"How do you know your perturbations preserve meaning?"**
Every reworded request is verified by an independent model call that sees the
original and the rewrite. Failures are discarded and counted, and the count is
in the report. The guard is not infallible and its failure mode is asymmetric.

---

## Images

Nine slides. Each pairs a technical artifact (formal notation, real terminal
output, actual code) with a `PLAIN` lane underneath that says the same thing in
ordinary words, so the deck reads for an engineer and for a finance buyer
without being written twice.

| File | Use |
|---|---|
| `plumbline-carousel.pdf` | The document post. All 9 slides. |
| `slide-1.png` | The problem. What auditors ask for and nothing provides. |
| `slide-2.png` | **99.4% PASS beside FAIL. Strongest single image.** |
| `slide-3.png` | The damage: 33.3% skip rate, OFAC strict liability. |
| `slide-4.png` | **The sampling formula. One test bounds nothing.** |
| `slide-6.png` | The results matrix. |
| `slide-7.png` | The retraction, as two numbers. |
| `slide-8.png` | The control test as a deliverable. |

---

## Regenerating

```bash
python docs/make_social.py
```

Every figure on every slide is computed from the committed trajectories at
render time. A number posted publicly cannot drift from the number in the
repository, and the script prints the three headline figures so you can check
them against the slides before posting.
