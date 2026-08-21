"""
Token and cost accounting, with a hard stop.

A perturbation study multiplies quickly: arms x tasks x perturbations x trials x
turns. It is easy to write a config that looks modest and issues fifty thousand
calls. The budget is checked before every request and raises rather than
overspending, because the failure mode of "it kept going" is a bill.

Prices are per million tokens, first-party Anthropic API rates. They are
recorded in the certificate alongside the result so a reported cost can be
audited later even after prices change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# USD per million tokens: (input, output)
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# cached reads are ~0.1x input, cache writes ~1.25x input
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 1.25


class BudgetExceeded(RuntimeError):
    pass


class UnknownModelPrice(RuntimeError):
    """Raised when a model has no price entry.

    This is deliberately fatal rather than a warning. If an unpriced model
    silently costs 0.00, the spend cap never fires and a misconfigured study
    runs until the API stops it. A budget that fails open is worse than no
    budget, because it is trusted.
    """


def resolve_price(model: str) -> tuple[float, float]:
    """Price for a model, tolerating dated snapshot ids.

    `claude-haiku-4-5-20251001` prices as `claude-haiku-4-5`. Anything with no
    match at all raises, so the failure is visible at startup instead of showing
    up as a study that reports it spent nothing.
    """
    if model in PRICES:
        return PRICES[model]
    candidates = [k for k in PRICES if model.startswith(k)]
    if candidates:
        return PRICES[max(candidates, key=len)]
    raise UnknownModelPrice(
        f"no price entry for model {model!r}. Add it to PRICES in "
        f"runtime/budget.py, or use one of: {', '.join(sorted(PRICES))}")


@dataclass
class Budget:
    max_usd: float = 5.00
    max_calls: int = 20_000
    spent_usd: float = 0.0
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    by_model: dict = field(default_factory=dict)

    def check(self) -> None:
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(
                f"spend cap reached: ${self.spent_usd:.2f} of ${self.max_usd:.2f}")
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"call cap reached: {self.calls}")

    def record(self, model: str, tin: int, tout: int,
               cache_read: int = 0, cache_write: int = 0) -> float:
        pin, pout = resolve_price(model)
        cost = (tin * pin + tout * pout) / 1e6
        cost += (cache_read * pin * CACHE_READ_MULT) / 1e6
        cost += (cache_write * pin * CACHE_WRITE_MULT) / 1e6
        self.spent_usd += cost
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.cache_read += cache_read
        self.cache_write += cache_write
        m = self.by_model.setdefault(model, {"calls": 0, "usd": 0.0})
        m["calls"] += 1
        m["usd"] += cost
        return cost

    def summary(self) -> dict:
        return {
            "spent_usd": round(self.spent_usd, 4),
            "calls": self.calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "by_model": {k: {"calls": v["calls"], "usd": round(v["usd"], 4)}
                         for k, v in self.by_model.items()},
        }
