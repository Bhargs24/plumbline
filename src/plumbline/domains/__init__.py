"""Built-in domains: the policy, task contexts, and outcome grader for each
worked example the CLI and console can score a run against.

Why a registry instead of an import: pointing the tool at a run with the
wrong domain's policy produces confidently wrong findings. Every command
that needs a policy resolves it here by name and says which one it used;
an unknown name fails loudly with the list of known ones -- it is never
guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    name: str
    summary: str
    policy: object                      # a PolicySpec
    contexts: dict                      # task_id -> context
    outcome_matches: Callable | None    # (context, ledger) -> bool


def _ap() -> Domain:
    from .ap.policy import AP_POLICY
    from .ap.tasks import build_tasks, outcome_matches
    return Domain(
        name="ap",
        summary="accounts payable, 8 invoices -- the studied domain",
        policy=AP_POLICY,
        contexts={t.task_id: t.context for t in build_tasks()},
        outcome_matches=outcome_matches,
    )


def _ap_full() -> Domain:
    from .accounts_payable.policy import AP_POLICY
    from .accounts_payable.tasks import build_tasks, outcome_matches
    return Domain(
        name="ap-full",
        summary="accounts payable, 20 invoices, 11 tables -- the production-shaped domain",
        policy=AP_POLICY,
        contexts={t.task_id: t.context for t in build_tasks()},
        outcome_matches=outcome_matches,
    )


_BUILDERS: dict[str, Callable[[], Domain]] = {
    "ap": _ap,
    # Historical alias: committed runs and older stores label the studied
    # 8-invoice domain "accounts_payable".
    "accounts_payable": _ap,
    "ap-full": _ap_full,
}

KNOWN = ("ap", "ap-full")


def get_domain(name: str) -> Domain:
    builder = _BUILDERS.get(name)
    if builder is None:
        raise SystemExit(
            f"unknown domain {name!r}. Known domains: {', '.join(KNOWN)}. "
            "A run must be scored against the policy it was recorded under; "
            "refusing to guess."
        )
    return builder()
