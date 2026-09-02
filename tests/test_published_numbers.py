"""The published numbers, pinned to the committed evidence.

The README and the site quote specific figures. This test recomputes every one
of them from the committed trajectories on every push, so the claim "if the
evidence stops reproducing the published numbers, the build goes red" is a
property of CI, not a hope. If an analysis change legitimately moves a number,
this test forces the publication to move with it in the same commit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plumbline.analysis.stats import wilson
from plumbline.certify import certify
from plumbline.core.trajectory import TrajectoryStore
from plumbline.domains import get_domain

ROOT = Path(__file__).resolve().parents[1]
_HAVE_RUNS = (ROOT / "runs" / "parity-study" / "trajectories.jsonl").exists()

pytestmark = pytest.mark.skipif(
    not _HAVE_RUNS, reason="committed runs not present (installed package?)")


def _run(name: str):
    root = ROOT / "runs" / name
    trajs = TrajectoryStore(root / "trajectories.jsonl").load()
    ledgers = json.loads((root / "ledger_states.json").read_text(encoding="utf-8"))
    return trajs, ledgers


def _outcome_pct(run: str, arm: str, pert: str) -> tuple[float, float, float, int]:
    d = get_domain("ap")
    trajs, ledgers = _run(run)
    subset = [t for t in trajs
              if t.arm == arm and t.perturbation == pert and not t.error]
    ok = sum(1 for t in subset
             if d.outcome_matches(d.contexts.get(t.task_id),
                                  ledgers.get(t.trial_id)))
    w = wilson(ok, len(subset))
    return 100 * ok / len(subset), 100 * w.lo, 100 * w.hi, len(subset)


def _bound(run: str, arm: str) -> tuple[float, str]:
    d = get_domain("ap")
    trajs, ledgers = _run(run)
    subset = [t for t in trajs if t.arm == arm and not t.error]
    cert = certify(subset, d.policy, d.contexts, ledgers, subject=arm,
                   provenance={"source": str(ROOT / "runs" / run)})
    return cert.certified_bound, cert.grade


def test_the_headline_tool_fault_row() -> None:
    """README section 1's table, tool_fault row -- the whole finding."""
    pct, lo, hi, n = _outcome_pct("parity-study", "plan_execute", "tool_fault")
    assert (round(pct, 1), round(lo, 1), round(hi, 1)) == (81.2, 70.0, 88.9)
    assert n == 64

    pct, lo, _hi, n = _outcome_pct("retry-study", "plan_execute", "tool_fault")
    assert (round(pct, 1), round(lo, 1)) == (100.0, 94.3)
    assert n == 64

    pct, lo, hi, n = _outcome_pct("parity-study", "react", "tool_fault")
    assert (round(pct, 1), round(lo, 1), round(hi, 1)) == (98.2, 90.6, 99.7)
    assert n == 56


def test_the_retry_study_replay_loses_exactly_52_react_runs() -> None:
    """The README discloses 52 unavailable replays; hold it to that."""
    trajs, _ = _run("retry-study")
    react = [t for t in trajs if t.arm == "react"]
    assert len(react) == 768 // 2
    assert sum(1 for t in react if t.error) == 52


def test_certified_bounds_agree_across_every_surface() -> None:
    """One scoring rule everywhere: the CLI, the store importer, and this test
    must produce the same certified bound for the same run and arm. The
    retry-study react arm once graded F from one surface and B from another."""
    assert _bound("parity-study", "react") == (pytest.approx(0.9434, abs=1e-4), "B")
    assert _bound("retry-study", "react") == (pytest.approx(0.9434, abs=1e-4), "B")
    assert _bound("retry-study", "plan_execute") == (pytest.approx(0.9434, abs=1e-4), "B")
    # The retracted no-retry arm genuinely fails its controls; that stays F.
    bound, grade = _bound("parity-study", "plan_execute")
    assert grade == "F" and bound == pytest.approx(0.4869, abs=1e-4)


def test_trajectory_totals_match_the_evidence_badge() -> None:
    """'2,082 trajectories committed' -- counted, not asserted."""
    total = sum(len(_run(name)[0]) for name in
                ("determinism-study", "parity-study", "retry-study"))
    assert total == 2082
