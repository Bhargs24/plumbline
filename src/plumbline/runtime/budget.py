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

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

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


DEFAULT_LEDGER = Path(".plumbline-spend.json")


@dataclass
class Budget:
    """Spend accounting with a cap that survives process restarts.

    Two properties this needs that the first version did not have, both learned
    the expensive way.

    CUMULATIVE. A cap held only in memory is not a cap. Every invocation used to
    start a fresh counter at zero, so running the same study five times meant
    five times the ceiling. Spend is now journalled to `ledger_path` and loaded
    on startup, so `max_usd` bounds total spend across every run that shares the
    ledger, not one process.

    HONEST WHEN RESUMED. A study rerun against a warm cache reports a tiny
    number, because it only pays for what was not already cached. Reporting that
    figure as the cost of the study understates it by however much the earlier,
    abandoned run spent. `session_usd` is what this process paid; `total_usd`
    is what the work has cost overall. Reports should quote the second.

    Thread-safe, because the runner records from a worker pool and unsynchronised
    accumulation across threads can silently lose calls.
    """
    max_usd: float = 5.00
    max_calls: int = 20_000
    spent_usd: float = 0.0          # this process only
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    by_model: dict = field(default_factory=dict)
    ledger_path: Path | str | None = DEFAULT_LEDGER
    prior_usd: float = 0.0          # spent by earlier runs sharing this ledger
    prior_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if self.ledger_path is None:
            return
        p = Path(self.ledger_path)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                self.prior_usd = float(d.get("total_usd", 0.0))
                self.prior_calls = int(d.get("total_calls", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                pass

    @property
    def total_usd(self) -> float:
        """Everything this work has cost, including earlier runs."""
        return self.prior_usd + self.spent_usd

    @property
    def total_calls(self) -> int:
        return self.prior_calls + self.calls

    def check(self) -> None:
        if self.total_usd >= self.max_usd:
            raise BudgetExceeded(
                f"spend cap reached: ${self.total_usd:.2f} of ${self.max_usd:.2f} "
                f"(${self.prior_usd:.2f} from earlier runs in "
                f"{self.ledger_path}, ${self.spent_usd:.2f} this run)")
        if self.total_calls >= self.max_calls:
            raise BudgetExceeded(f"call cap reached: {self.total_calls}")

    def record(self, model: str, tin: int, tout: int,
               cache_read: int = 0, cache_write: int = 0) -> float:
        pin, pout = resolve_price(model)
        cost = (tin * pin + tout * pout) / 1e6
        cost += (cache_read * pin * CACHE_READ_MULT) / 1e6
        cost += (cache_write * pin * CACHE_WRITE_MULT) / 1e6
        with self._lock:
            self.spent_usd += cost
            self.calls += 1
            self.tokens_in += tin
            self.tokens_out += tout
            self.cache_read += cache_read
            self.cache_write += cache_write
            m = self.by_model.setdefault(model, {"calls": 0, "usd": 0.0})
            m["calls"] += 1
            m["usd"] += cost
            self._flush()
        return cost

    def _flush(self) -> None:
        """Journal after every call. A process killed mid-run must not take its
        spend record with it, which is exactly how the first overrun happened."""
        if self.ledger_path is None:
            return
        p = Path(self.ledger_path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "total_usd": round(self.total_usd, 6),
                "total_calls": self.total_calls,
                "note": "cumulative across runs; delete to reset the cap",
            }), encoding="utf-8")
            import os
            os.replace(tmp, p)
        except OSError:
            pass

    def summary(self) -> dict:
        return {
            "session_usd": round(self.spent_usd, 4),
            "prior_usd": round(self.prior_usd, 4),
            "total_usd": round(self.total_usd, 4),
            "session_calls": self.calls,
            "total_calls": self.total_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "prompt_cache_engaged": bool(self.cache_read or self.cache_write),
            "by_model": {k: {"calls": v["calls"], "usd": round(v["usd"], 4)}
                         for k, v in self.by_model.items()},
        }
