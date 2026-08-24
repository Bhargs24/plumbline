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


def cmd_attest(args) -> int:
    """Test the key controls and print the control-testing workpaper.

    This is the same evidence `certify` reports, restated in the vocabulary a
    control owner and their auditor actually work in: deviation rates against a
    tolerable rate, at a sample size chosen for a stated confidence level.
    """
    from .compliance import (P2P_FRAMEWORK, attest, exception_routing,
                             render_text)
    trajs, _ = _load(Path(args.run_dir))
    spec, contexts = _policy_and_contexts()
    if args.arm:
        trajs = [t for t in trajs if t.arm == args.arm]
        if not trajs:
            print(f"no trajectories for arm {args.arm!r}", file=sys.stderr)
            return 2
    a = attest(trajs, spec, contexts, P2P_FRAMEWORK,
               operator=args.operator, period=args.period,
               itgc_effective=not args.itgc_failed, confidence=args.confidence)

    if args.json:
        print(json.dumps(a.to_dict(), indent=2))
    else:
        print(render_text(a))
        routing = exception_routing(a)
        if routing:
            print("\n  EXCEPTION ROUTING")
            print(f"  {'CONTROL':<9}{'OWNER':<26}{'SLA':>5}{'ITEMS':>7}  "
                  f"TRANSACTIONS")
            for r in routing:
                shown = ", ".join(r["tasks"][:4])
                more = f" +{len(r['tasks']) - 4}" if len(r["tasks"]) > 4 else ""
                print(f"  {r['control_id']:<9}{r['owner'][:24]:<26}"
                      f"{r['sla_days']:>4}d{r['count']:>7}  {shown}{more}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(a.to_dict(), indent=2), encoding="utf-8")
        print(f"\nwritten to {out}", file=sys.stderr)

    # A deficiency exits non-zero, so this can gate a release the same way a
    # failing control test blocks a sign-off.
    return 1 if a.deficiencies else 0


def cmd_demo(args) -> int:
    """Seed the store from the committed runs and start the console.

    The point of this command is that it needs no API key. Every study in the
    repository ships its trajectories, so a stranger can see the whole tool
    working on real data thirty seconds after cloning.
    """
    from .store import Store
    from .store.importer import import_run
    from agents.ap.policy import AP_POLICY
    from agents.ap.tasks import build_tasks, expected_outcome

    contexts = {t.task_id: t.context for t in build_tasks()}

    def outcome_ok(ctx, ledger):
        if not ctx or ledger is None:
            return False
        w = expected_outcome(ctx)
        return (bool(ledger.get("paid")) == w["paid"]
                and int(ledger.get("payment_count", 0)) == w["payment_count"]
                and abs(float(ledger.get("amount_paid", 0))
                        - w["amount_paid"]) < 0.005
                and bool(ledger.get("exception_raised")) == w["exception_raised"])

    root = Path(__file__).resolve().parents[2]
    db = Path(args.db)
    if db.exists() and not args.reset:
        print(f"store already exists at {db}; pass --reset to rebuild it")
    else:
        if db.exists():
            db.unlink()
        store = Store(db)
        seeded = 0
        for d, label in (("runs/parity-study",
                          "no-retry executor (the retracted result)"),
                         ("runs/retry-study",
                          "production executor with retry policy")):
            path = root / d
            if not (path / "trajectories.jsonl").exists():
                continue
            rid = import_run(store, path, project="AP controls",
                             domain="accounts_payable", label=label,
                             policy=AP_POLICY, contexts=contexts,
                             outcome_fn=outcome_ok)
            print(f"  seeded {label}  ->  {rid}")
            seeded += 1
        if not seeded:
            print("no committed runs found", file=sys.stderr)
            return 1

    if args.no_serve:
        return 0
    args.host, args.port = "127.0.0.1", args.port
    return cmd_serve(args)


def cmd_serve(args) -> int:
    """Start the console and API."""
    import os
    os.environ["PLUMBLINE_DB"] = args.db
    import uvicorn
    print(f"console  http://{args.host}:{args.port}/")
    print(f"api      http://{args.host}:{args.port}/api/runs")
    uvicorn.run("plumbline.server.app:app", host=args.host, port=args.port,
                log_level="warning")
    return 0


def cmd_import(args) -> int:
    """Load a run directory into the store so the console can read it."""
    from .store import Store
    from .store.importer import import_run
    policy = contexts = outcome_fn = None
    if args.domain == "accounts_payable":
        from domains.accounts_payable.policy import AP_POLICY
        from domains.accounts_payable.tasks import build_tasks, outcome_matches
        policy = AP_POLICY
        contexts = {t.task_id: t.context for t in build_tasks()}
        outcome_fn = outcome_matches
    rid = import_run(Store(args.db), args.run_dir, project=args.project,
                     domain=args.domain, label=args.label or "",
                     policy=policy, contexts=contexts, outcome_fn=outcome_fn)
    print(f"imported {args.run_dir} as {rid}")
    return 0


def cmd_gate(args) -> int:
    """CI gate: exit non-zero when a certified bound falls below a threshold."""
    from .store import Store
    certs = [c for c in Store(args.db).certificates(args.run_id)
             if c["kind"] == "conformance"]
    if not certs:
        print("no conformance certificate for that run", file=sys.stderr)
        return 2
    failed = False
    for c in sorted(certs, key=lambda x: x["arm"]):
        ok = (c["bound"] or 0) >= args.min_bound
        failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {c['arm']:<20} "
              f"{(c['bound'] or 0) * 100:5.1f}%  (threshold "
              f"{args.min_bound * 100:.0f}%)")
    return 1 if failed else 0


def cmd_report(args) -> int:
    """Render a run into a self-contained HTML report."""
    from .report.build import build
    out = Path(args.out)
    html_text = build(args.root, standalone=not args.fragment)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    print(f"written to {out}  ({len(html_text):,} bytes, fully self-contained)")
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


def _force_utf8() -> None:
    """Windows consoles default to a legacy codepage, which turns any non-ASCII
    character in a workpaper into a replacement glyph. This output is evidence;
    it should not depend on the operator console settings."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):   # not a real stream, or already set
            pass


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

    w = sub.add_parser("report", help="render the study into a self-contained "
                                      "HTML page")
    w.add_argument("--root", default=".", help="repository root holding runs/")
    w.add_argument("-o", "--out", default="report.html")
    w.add_argument("--fragment", action="store_true",
                   help="emit body content only, for embedding")
    w.set_defaults(fn=cmd_report)

    t = sub.add_parser("attest",
                       help="test the key controls and emit the "
                            "control-testing workpaper")
    t.add_argument("run_dir")
    t.add_argument("--arm", default=None,
                   help="the system operating the controls")
    t.add_argument("--operator", default="llm_agent",
                   choices=["llm_agent", "deterministic", "human"],
                   help="who operates the control; decides whether "
                        "test-of-one reliance is defensible")
    t.add_argument("--period", default="", help="reporting period, e.g. 2026-Q3")
    t.add_argument("--confidence", type=float, default=0.95)
    t.add_argument("--itgc-failed", action="store_true",
                   help="ITGC testing did not pass, so automated-control "
                        "reliance cannot be taken")
    t.add_argument("--json", action="store_true")
    t.add_argument("--out", default=None, help="also write the workpaper as JSON")
    t.set_defaults(fn=cmd_attest)

    d = sub.add_parser("demo", help="seed the store from committed runs and "
                                    "open the console. No API key needed.")
    d.add_argument("--port", type=int, default=8912)
    d.add_argument("--db", default="plumbline.db")
    d.add_argument("--reset", action="store_true", help="rebuild the store")
    d.add_argument("--no-serve", action="store_true", help="seed only")
    d.set_defaults(fn=cmd_demo)

    v = sub.add_parser("serve", help="start the web console and REST API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8912)
    v.add_argument("--db", default="plumbline.db")
    v.set_defaults(fn=cmd_serve)

    m = sub.add_parser("import", help="load a run directory into the store")
    m.add_argument("run_dir")
    m.add_argument("--project", required=True)
    m.add_argument("--domain", default="accounts_payable")
    m.add_argument("--label", default=None)
    m.add_argument("--db", default="plumbline.db")
    m.set_defaults(fn=cmd_import)

    g = sub.add_parser("gate", help="CI gate on the certified bound")
    g.add_argument("run_id")
    g.add_argument("--min-bound", type=float, default=0.95)
    g.add_argument("--db", default="plumbline.db")
    g.set_defaults(fn=cmd_gate)

    s = sub.add_parser("show", help="print a stored trajectory step by step")
    s.add_argument("run_dir")
    s.add_argument("--trial", required=True, help="substring of the trial id")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(fn=cmd_show)

    _force_utf8()
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
