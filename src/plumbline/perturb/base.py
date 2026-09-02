"""
Perturbations: input changes that must not change correct behavior.

A perturbation is only meaningful if it genuinely preserves the task. If a
reworded request quietly asks for something else, then a behavior change is
correct and counting it as a defect is a measurement error. The closest prior
art names this as its main threat to validity and handles it by careful manual
design. Here it is handled by construction plus a check: every perturbation
declares the invariant it preserves, and the ones that rewrite text are verified
by an independent model call before they are used (see `library.ParaphraseWithGuard`).

A variant that fails its equivalence check is discarded and recorded, not
quietly repaired. How many were discarded is part of the certificate, because a
perturbation engine that silently drops a third of its variants is telling you
something about itself.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Variant:
    """One perturbed version of one task, ready to run."""
    variant_id: str
    perturbation: str
    prompt: str
    fault_hook: Callable | None = None
    extra_tools: list = field(default_factory=list)
    temperature: float | None = None
    meta: dict = field(default_factory=dict)


class Perturbation:
    name: str = "perturbation"
    #: what a correct agent's behavior must be invariant to
    invariant: str = ""
    #: does generating variants require model calls
    needs_llm: bool = False

    def variants(self, task, *, n: int, llm=None, rng=None) -> list[Variant]:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "invariant": self.invariant}


class EquivalenceCheckFailed(Exception):
    pass


class InjectedFault(Exception):
    """A fault deliberately injected at the tool boundary.

    Generic on purpose. The perturbation engine must not import a domain's
    exception type, so a toolbox converts this into whatever error its own
    tools raise. The agent then observes a genuine error from a genuine call
    and cannot distinguish an injected fault from a real one, which is the
    whole point.
    """
