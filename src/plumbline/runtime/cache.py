"""
Content-addressed response cache.

Two reasons this exists, and the second one is a correctness issue rather than a
cost one.

COST. A study re-run should not re-pay for calls whose inputs did not change.

CORRECTNESS. A naive cache keyed only on the request would destroy the very
thing being measured. Self-consistency is measured by issuing the SAME request
several times and seeing whether the agent does the same thing. If identical
requests are served from one cached response, self-consistency comes back as a
perfect 100% every time, and the number is an artifact of the cache rather than
a property of the agent.

So the key includes the trial identity. Repeating trial 3 replays trial 3's
recorded calls exactly, which makes a published result reproducible from the
committed cache. Running a NEW trial 4 goes to the API and samples afresh. You
get replayability without manufacturing agreement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _stable(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class ResponseCache:
    def __init__(self, root: str | Path = ".cache/llm", enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def key(self, *, model: str, system: Any, messages: Any, tools: Any,
            temperature: float | None, trial_key: str, turn: int) -> str:
        payload = _stable({
            "model": model, "system": system, "messages": messages,
            "tools": tools, "temperature": temperature,
            # trial identity: replay is exact, a new trial is a real sample
            "trial_key": trial_key, "turn": turn,
        })
        return hashlib.sha256(payload.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        p = self._path(key)
        if not p.exists():
            self.misses += 1
            return None
        self.hits += 1
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
        os.replace(tmp, p)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0}
