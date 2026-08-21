"""Plumbline: conformance-under-perturbation testing for LLM agents.

You declare the invariants your agent must never violate. Plumbline tries to
break them using input changes that preserve meaning, then reports which
invariant broke, under which perturbation, at which named step, how often, and
with what confidence interval.
"""
__version__ = "0.2.0"

from .core.trajectory import Step, Trajectory, TrajectoryStore

__all__ = ["Step", "Trajectory", "TrajectoryStore", "__version__"]
