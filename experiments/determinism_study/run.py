"""
The determinism study.

One question: when you move an agent's control flow out of the model, how much
reliability do you actually buy, and where does the remaining risk go?

Held fixed across all three arms: the task set, the tools, the database, the
model, the perturbation variants (generated once and shared), and the declared
policy. Changed: who decides which step runs next.

Run it:
    python experiments/determinism_study/run.py --trials 3
    python experiments/determinism_study/run.py --arms react --budget 1.00

Output lands in runs/<name>/: trajectories.jsonl, ledger_states.json,
variants.json, one certificate per arm, and comparison.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from plumbline.adapters.llm import (DEFAULT_AGENT_MODEL, DEFAULT_PERTURB_MODEL,
                                     LLMClient, have_credentials)
from plumbline.certify import certify, compare_arms
from plumbline.perturb.library import (Baseline, DecoyTools, Distractor,
                                        ParaphraseWithGuard, SamplingSweep,
                                        TransientToolFault)
from plumbline.runtime.budget import Budget
from plumbline.runtime.cache import ResponseCache
from plumbline.runtime.runner import RunConfig, run_study

from agents.ap.arms import ARMS
from agents.ap.policy import AP_POLICY
from agents.ap.tasks import build_tasks


def build_suite(temperature: float):
    return [Baseline(), ParaphraseWithGuard(), Distractor(),
            TransientToolFault(), DecoyTools(), SamplingSweep(temperature)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Plumbline determinism study")
    ap.add_argument("--arms", nargs="+", default=list(ARMS),
                    choices=list(ARMS), help="which architectures to run")
    ap.add_argument("--tasks", nargs="+", default=None, help="invoice ids")
    ap.add_argument("--variants", type=int, default=4,
                    help="variants per perturbation per task")
    ap.add_argument("--trials", type=int, default=1, help="repeats per variant")
    ap.add_argument("--model", default=DEFAULT_AGENT_MODEL)
    ap.add_argument("--perturb-model", default=DEFAULT_PERTURB_MODEL)
    ap.add_argument("--temperature", type=float, default=1.0,
                    help="temperature for the sampling perturbation")
    ap.add_argument("--budget", type=float, default=8.00, help="hard USD cap")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--name", default="determinism-study")
    ap.add_argument("--cache", default=".cache/llm")
    ap.add_argument("--ledger", default=".plumbline-spend.json",
                    help="cumulative spend journal; the cap applies across every "
                         "run sharing it. Delete it to reset.")
    ap.add_argument("--reuse-variants", default=None, metavar="RUN_DIR",
                    help="reuse another run's exact variant set, so a new arm "
                         "sees identical inputs and no paraphrases are re-paid for")
    ap.add_argument("--offline", action="store_true",
                    help="replay from cache only, never call the API")
    args = ap.parse_args()

    if not args.offline and not have_credentials():
        print("No ANTHROPIC_API_KEY found. Copy .env.example to .env and add a key,\n"
              "or pass --offline to replay a cached run.", file=sys.stderr)
        return 2

    tasks = build_tasks(args.tasks)
    arms = [ARMS[a] for a in args.arms]
    suite = build_suite(args.temperature)
    out_dir = ROOT / "runs" / args.name
    budget = Budget(max_usd=args.budget, ledger_path=args.ledger)
    cache = ResponseCache(args.cache)

    agent_llm = LLMClient(model=args.model, cache=cache, budget=budget,
                          offline=args.offline)
    perturb_llm = LLMClient(model=args.perturb_model, cache=cache, budget=budget,
                            max_tokens=2048, offline=args.offline)

    from agents.ap.tools import APToolbox
    cfg = RunConfig(toolbox_factory=APToolbox,
                    ledger_of=lambda tb, task: tb.ledger_state(task.invoice_id),
                    arms=arms, tasks=tasks, perturbations=suite,
                    trials_per_variant=args.trials,
                    variants_per_perturbation=args.variants,
                    max_workers=args.workers, out_dir=str(out_dir))

    planned = (len(arms) * len(tasks) * len(suite)
               * args.variants * args.trials)
    print(f"arms={len(arms)} tasks={len(tasks)} perturbations={len(suite)} "
          f"variants={args.variants} trials={args.trials}")
    print(f"planned runs: {planned}   model: {args.model}   "
          f"budget cap: ${args.budget:.2f}\n")

    def progress(done, total, traj):
        if done % 10 == 0 or done == total:
            print(f"  [{done:>4}/{total}] ${budget.spent_usd:6.3f}  "
                  f"last: {traj.arm}/{traj.task_id}/{traj.perturbation}",
                  flush=True)

    result = run_study(cfg, agent_llm, perturb_llm, on_progress=progress,
                       reuse_variants=args.reuse_variants)

    contexts = {t.task_id: t.context for t in tasks}
    bs = budget.summary()
    print(f"\ncompleted {len(result.trajectories)} runs in "
          f"{result.wall_seconds:.0f}s, local replay cache "
          f"{cache.stats()['hit_rate']:.0%} hit")
    print(f"  this run  ${bs['session_usd']:.3f} over {bs['session_calls']} calls")
    print(f"  earlier   ${bs['prior_usd']:.3f} over "
          f"{bs['total_calls'] - bs['session_calls']} calls")
    print(f"  TOTAL     ${bs['total_usd']:.3f}   (cap ${args.budget:.2f}, "
          f"ledger {args.ledger})")
    if not bs["prompt_cache_engaged"] and bs["session_calls"] > 25:
        print("  note: prompt caching never engaged, so every call paid full "
              "price for the system prompt and tool definitions")
    if result.discarded_variants:
        print(f"equivalence guard discarded {result.discarded_variants} paraphrases")
    if result.errors:
        print(f"{len(result.errors)} trial error(s); see errors.json")
        (out_dir / "errors.json").write_text(
            json.dumps(result.errors, indent=2), encoding="utf-8")

    certs = {}
    for arm in arms:
        trajs = [t for t in result.trajectories if t.arm == arm.name]
        if not trajs:
            continue
        cert = certify(
            trajs, AP_POLICY, contexts, result.ledger_states,
            subject=f"{arm.name} arm",
            provenance={
                "model": args.model, "perturb_model": args.perturb_model,
                "arm": arm.name, "seed": cfg.seed,
                "variants_per_perturbation": args.variants,
                "trials_per_variant": args.trials,
                "perturbations": [p.describe() for p in suite],
                "invariants": AP_POLICY.ids(),
                "tasks": [t.task_id for t in tasks],
                "cost_usd_this_run": budget.spent_usd,
                "cost_usd_total": budget.total_usd,
                "llm_calls": budget.calls,
                "wall_seconds": result.wall_seconds,
                "discarded_paraphrases": result.discarded_variants,
            })
        cert.save(out_dir / f"certificate-{arm.name}.json")
        certs[arm.name] = cert
        print("\n" + cert.render())

    if len(certs) > 1:
        print("\n" + "=" * 74)
        print("  ARCHITECTURE COMPARISON  (critical-invariant conformance)")
        print("=" * 74)
        comparisons = []
        names = [a.name for a in arms if a.name in certs]
        base = names[0]
        for other in names[1:]:
            overall = compare_arms(result.trajectories, AP_POLICY, contexts,
                                   base, other)
            print(f"  overall      {overall.describe()}")
            comparisons.append({"scope": "overall", **overall.to_dict()})
            for p in suite:
                c = compare_arms(result.trajectories, AP_POLICY, contexts,
                                 base, other, perturbation=p.name)
                if c.a.total and c.b.total:
                    print(f"    {p.name:<13} {c.describe()}")
                    comparisons.append({"scope": p.name, **c.to_dict()})
            print()
        (out_dir / "comparison.json").write_text(
            json.dumps(comparisons, indent=2), encoding="utf-8")

        # The migration view. Treat the first arm as the incumbent being
        # replaced and every other arm as a candidate replacement. This asks a
        # different question from the certificates above: not "did each system
        # obey its own rules" but "does the replacement do what the incumbent
        # did", which is what actually gates retiring the incumbent.
        from plumbline.certify import prove_parity
        completed = [t for t in result.trajectories if not t.error]
        for other in names[1:]:
            try:
                parity = prove_parity(completed, incumbent=base,
                                      replacement=other,
                                      ledger_states=result.ledger_states,
                                      spec=AP_POLICY)
            except ValueError as exc:
                print(f"  parity {base} vs {other}: {exc}")
                continue
            print("\n" + parity.render())
            (out_dir / f"parity-{base}-vs-{other}.json").write_text(
                json.dumps(parity.to_dict(), indent=2), encoding="utf-8")

    summary = {
        "certified_bound": {k: v.certified_bound for k, v in certs.items()},
        "grade": {k: v.grade for k, v in certs.items()},
        "critical_conformance": {k: v.conformance.critical.to_dict()
                                 for k, v in certs.items()},
        "outcome_correctness": {k: v.outcome_correctness.to_dict()
                                for k, v in certs.items()},
        "cost_usd_this_run": round(budget.spent_usd, 4),
        "cost_usd_total": round(budget.total_usd, 4),
        "budget": budget.summary(),
        "runs": len(result.trajectories),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2),
                                          encoding="utf-8")
    print(f"\nwritten to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
