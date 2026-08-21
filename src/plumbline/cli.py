"""
Command line interface.

    plumbline certify runs/my-run --arm react
    plumbline compare runs/my-run react plan_execute
    plumbline show    runs/my-run --trial react/INV-7007/paraphrase/1/0

`certify` re-derives a certificate from stored trajectories without calling any
model. That is the property that makes a published certificate checkable: ship
the trajectories file and anyone can rebuild the number.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(run_dir: Path):
    from .core.trajectory import TrajectoryStore
    trajs = TrajectoryStore(run_dir / "trajectories.jsonl").load()
    if not trajs:
        raise SystemExit(f"no trajectories found in {run_dir}")
    ledger_path = run_dir / "ledger_states.json"
    ledgers = json.loads(ledger_path.read_text(encoding="utf-8")) \
        if ledger_path.exists() else {}
    return trajs, ledgers


def _policy_and_contexts():
    """The AP policy ships as the worked example. A different agent supplies its
    own PolicySpec; this is the default so the CLI is useful out of the box."""
    from agents.ap.policy import AP_POLICY
    from agents.ap.tasks import build_tasks
    return AP_POLICY, {t.task_id: t.context for t in build_tasks()}


def cmd_certify(args) -> int:
    from .certify import certify
    run_dir = Path(args.run_dir)
    trajs, ledgers = _load(run_dir)
    spec, contexts = _policy_and_contexts()
    arms = sorted({t.arm for t in trajs}) if args.arm is None else [args.arm]
    for arm in arms:
        subset = [t for t in trajs if t.arm == arm]
        if not subset:
            print(f"no runs for arm {arm!r}", file=sys.stderr)
            continue
        cert = certify(subset, spec, contexts, ledgers, subject=f"{arm} arm",
                       provenance={"model": subset[0].model, "arm": arm,
                                   "source": str(run_dir)})
        if args.json:
            print(json.dumps(cert.to_dict(), indent=2))
        else:
            print(cert.render())
        if args.out:
            p = cert.save(Path(args.out) / f"certificate-{arm}.json")
            print(f"\nwritten to {p}", file=sys.stderr)
    return 0


def cmd_compare(args) -> int:
    from .certify import compare_arms
    trajs, _ = _load(Path(args.run_dir))
    spec, contexts = _policy_and_contexts()
    overall = compare_arms(trajs, spec, contexts, args.arm_a, args.arm_b)
    print(f"overall   {overall.describe()}")
    for pert in sorted({t.perturbation for t in trajs}):
        c = compare_arms(trajs, spec, contexts, args.arm_a, args.arm_b,
                         perturbation=pert)
        if c.a.total and c.b.total:
            print(f"  {pert:<14} {c.describe()}")
    return 0


def cmd_parity(args) -> int:
    """Prove that a replacement behaves like the incumbent it would retire."""
    from .certify import prove_parity
    run_dir = Path(args.run_dir)
    trajs, ledgers = _load(run_dir)
    spec, _ = _policy_and_contexts()

    if args.exclude_errors:
        before = len(trajs)
        trajs = [t for t in trajs if not t.error]
        dropped = before - len(trajs)
        if dropped:
            print(f"excluded {dropped} run(s) that did not complete\n",
                  file=sys.stderr)

    report = prove_parity(trajs, incumbent=args.incumbent,
                          replacement=args.replacement,
                          ledger_states=ledgers, spec=spec)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    if args.out:
        p = Path(args.out) / f"parity-{args.incumbent}-vs-{args.replacement}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"\nwritten to {p}", file=sys.stderr)
    return 0


def cmd_show(args) -> int:
    trajs, ledgers = _load(Path(args.run_dir))
    matches = [t for t in trajs if args.trial in t.trial_id]
    if not matches:
        print(f"no trial matching {args.trial!r}", file=sys.stderr)
        return 1
    for t in matches[:args.limit]:
        print("=" * 74)
        print(f"{t.trial_id}   arm={t.arm} task={t.task_id} "
              f"perturbation={t.perturbation}")
        print(f"input: {t.task_input}")
        print("-" * 74)
        for i, s in enumerate(t.steps):
            mark = "!" if s.error else " "
            args_txt = json.dumps(s.args, default=str) if s.args else ""
            print(f" {mark}{i:>3} {s.kind:<10} {s.name:<24} {args_txt}")
            if s.error:
                print(f"       error: {s.error}")
        print("-" * 74)
        print(f"final: {t.final_output}")
        print(f"ledger: {json.dumps(ledgers.get(t.trial_id, {}))}")
    return 0


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))

    ap = argparse.ArgumentParser(prog="plumbline",
                                 description="Conformance-under-perturbation "
                                             "testing for LLM agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("certify", help="rebuild a certificate from stored traces")
    c.add_argument("run_dir")
    c.add_argument("--arm", default=None)
    c.add_argument("--json", action="store_true")
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_certify)

    p = sub.add_parser("compare", help="test two arms against each other")
    p.add_argument("run_dir")
    p.add_argument("arm_a")
    p.add_argument("arm_b")
    p.set_defaults(fn=cmd_compare)

    q = sub.add_parser("parity",
                       help="prove a replacement matches the incumbent it "
                            "would retire, including under perturbation")
    q.add_argument("run_dir")
    q.add_argument("incumbent", help="the system being replaced")
    q.add_argument("replacement", help="the system replacing it")
    q.add_argument("--exclude-errors", action="store_true",
                   help="drop runs that did not complete, e.g. after an API "
                        "outage, rather than scoring them as divergences")
    q.add_argument("--json", action="store_true")
    q.add_argument("--out", default=None)
    q.set_defaults(fn=cmd_parity)

    s = sub.add_parser("show", help="print a stored trajectory step by step")
    s.add_argument("run_dir")
    s.add_argument("--trial", required=True, help="substring of the trial id")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(fn=cmd_show)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
