"""
The study runner.

Generates perturbation variants once per task, then runs every (arm, task,
variant) combination, capturing a trajectory and the resulting ledger state for
each. Variants are generated once and shared across arms, which is what makes
the arms comparable: all three see the identical set of reworded requests, the
identical injected faults, and the identical decoy tools.

Concurrency is thread-based because the work is network-bound. Each trial gets
its own toolbox and therefore its own database, so trials cannot contaminate one
another and can run in any order.

Failures of a single trial are recorded on the trajectory rather than raised. A
study that aborts on trial 400 of 700 wastes the 399 that worked, and an agent
that crashes is itself a result worth keeping.
"""
from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..core.trajectory import Trajectory, TrajectoryStore
from .budget import Budget, BudgetExceeded


@dataclass
class RunConfig:
    arms: list                       # list[Arm]
    tasks: list                      # list[Task]
    perturbations: list              # list[Perturbation]
    trials_per_variant: int = 1
    variants_per_perturbation: int = 5
    max_workers: int = 8
    out_dir: str = "runs/latest"
    seed: int = 7


@dataclass
class RunResult:
    trajectories: list = field(default_factory=list)
    ledger_states: dict = field(default_factory=dict)
    variants: dict = field(default_factory=dict)     # task_id -> list[Variant]
    budget: Budget | None = None
    wall_seconds: float = 0.0
    discarded_variants: int = 0
    errors: list = field(default_factory=list)


def load_variants(run_dir) -> dict:
    """Rebuild the exact variant set a previous study used.

    Two reasons. Comparability: an arm added later must see the identical
    reworded requests, injected faults and decoy tools, or the comparison is
    confounded by the inputs rather than isolating the architecture. And cost:
    regenerating paraphrases re-pays for work already done.

    Fault hooks and decoy tool lists are reconstructed from the recorded
    metadata, since a callable cannot be serialised.
    """
    from ..perturb.base import Variant
    from ..perturb.library import DECOYS, _fail_once
    from pathlib import Path as _P
    raw = json.loads((_P(run_dir) / "variants.json").read_text(encoding="utf-8"))
    out: dict[str, list] = {}
    for task_id, entries in raw.items():
        rebuilt = []
        for e in entries:
            meta = e.get("meta") or {}
            hook = None
            if e["perturbation"] == "tool_fault" and meta.get("target"):
                hook = _fail_once(meta["target"], meta.get("error", "injected fault"))
            extra = []
            if e["perturbation"] == "decoy_tools" and meta.get("decoys"):
                names = set(meta["decoys"])
                extra = [d for d in DECOYS if d["name"] in names]
            rebuilt.append(Variant(e["variant_id"], e["perturbation"], e["prompt"],
                                   fault_hook=hook, extra_tools=extra,
                                   temperature=meta.get("temperature"), meta=meta))
        out[task_id] = rebuilt
    return out


def generate_variants(cfg: RunConfig, perturb_llm) -> tuple[dict, int]:
    """Build the variant set once, shared by every arm."""
    import random
    out: dict[str, list] = {}
    discarded = 0
    for task in cfg.tasks:
        rng = random.Random(cfg.seed + hash(task.task_id) % 10_000)
        variants = []
        for p in cfg.perturbations:
            got = p.variants(task, n=cfg.variants_per_perturbation,
                             llm=perturb_llm if p.needs_llm else None, rng=rng)
            if p.needs_llm and got:
                discarded += got[0].meta.get("rejected_count", 0)
            if p.needs_llm and len(got) < cfg.variants_per_perturbation:
                # Not enough variants survived the equivalence guard. Record it
                # rather than padding with unverified text.
                pass
            variants.extend(got)
        out[task.task_id] = variants
    return out, discarded


def run_study(cfg: RunConfig, agent_llm, perturb_llm, *,
              on_progress=None, reuse_variants=None) -> RunResult:
    from agents.ap.tools import APToolbox

    started = time.perf_counter()
    result = RunResult(budget=agent_llm.budget)
    if reuse_variants:
        variants, discarded = load_variants(reuse_variants), 0
    else:
        variants, discarded = generate_variants(cfg, perturb_llm)
    result.variants = variants
    result.discarded_variants = discarded

    jobs = []
    for arm in cfg.arms:
        for task in cfg.tasks:
            for variant in variants[task.task_id]:
                for k in range(cfg.trials_per_variant):
                    jobs.append((arm, task, variant, k))

    total = len(jobs)
    done = 0

    def one(arm, task, variant, k):
        trial_key = f"{arm.name}/{task.task_id}/{variant.variant_id}/{k}"
        toolbox = APToolbox(fault_hook=variant.fault_hook)
        try:
            traj = arm.run(prompt=variant.prompt, toolbox=toolbox, llm=agent_llm,
                           trial_key=trial_key, temperature=variant.temperature,
                           extra_tools=variant.extra_tools, task_id=task.task_id,
                           perturbation=variant.perturbation,
                           variant_id=variant.variant_id)
        except BudgetExceeded:
            raise
        except Exception as exc:  # a crashed agent is a result, not an abort
            traj = Trajectory(trial_id=trial_key, perturbation=variant.perturbation,
                              variant_id=variant.variant_id, arm=arm.name,
                              task_id=task.task_id, task_input=variant.prompt,
                              model=agent_llm.model,
                              error=f"{type(exc).__name__}: {exc}")
            result.errors.append({"trial": trial_key, "error": str(exc),
                                  "trace": traceback.format_exc(limit=3)})
        return traj, toolbox.ledger_state(task.invoice_id)

    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = {pool.submit(one, *j): j for j in jobs}
        try:
            for fut in as_completed(futures):
                traj, ledger = fut.result()
                result.trajectories.append(traj)
                result.ledger_states[traj.trial_id] = ledger
                done += 1
                if on_progress:
                    on_progress(done, total, traj)
        except BudgetExceeded as exc:
            result.errors.append({"trial": "-", "error": f"budget stop: {exc}"})
            for f in futures:
                f.cancel()

    result.wall_seconds = time.perf_counter() - started

    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    TrajectoryStore(out / "trajectories.jsonl").write_all(result.trajectories)
    import json
    (out / "ledger_states.json").write_text(
        json.dumps(result.ledger_states, indent=2), encoding="utf-8")
    (out / "variants.json").write_text(json.dumps(
        {tid: [{"variant_id": v.variant_id, "perturbation": v.perturbation,
                "prompt": v.prompt, "meta": {k: x for k, x in v.meta.items()
                                             if k != "rejected"}}
               for v in vs] for tid, vs in variants.items()},
        indent=2, default=str), encoding="utf-8")
    return result
