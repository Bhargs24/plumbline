"""
The Claude client used by every arm and by the perturbation engine.

Kept deliberately thin. It does four things the study depends on: it caches on
trial identity (see runtime/cache.py for why that distinction matters), it
accounts for spend before every call, it records token usage onto the trajectory
so cost is part of the result rather than a footnote, and it returns plain dicts
so a recorded response replays identically without the SDK in the loop.

Model choice for the system under test is Claude Haiku 4.5, for two reasons
worth stating rather than hiding. First, the study needs hundreds of runs and a
sampling perturbation, and `temperature` is rejected on the current Opus and
Sonnet 5 models. Second, a small fast model is the realistic choice for
high-volume invoice processing, so measuring one is measuring the deployment
people actually ship. The claim under test is architectural, not a ranking of
models, and the same harness runs against any model you point it at.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..runtime.budget import Budget
from ..runtime.cache import ResponseCache

DEFAULT_AGENT_MODEL = "claude-haiku-4-5"
DEFAULT_PERTURB_MODEL = "claude-sonnet-5"


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader so a key can live in a gitignored file rather than
    in the shell history."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def have_credentials() -> bool:
    load_dotenv()
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@dataclass
class LLMResponse:
    content: list          # list of content-block dicts
    stop_reason: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    model: str = ""

    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content
                         if b.get("type") == "text").strip()

    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class OfflineError(RuntimeError):
    """Raised when a live call is needed but no credentials are available and
    the cache has no recorded response for this exact trial."""


@dataclass
class LLMClient:
    model: str = DEFAULT_AGENT_MODEL
    cache: ResponseCache = field(default_factory=ResponseCache)
    budget: Budget = field(default_factory=Budget)
    max_tokens: int = 2048
    offline: bool = False       # replay only; never hit the network
    _client: object = None

    def __post_init__(self):
        load_dotenv()
        if not self.offline and have_credentials():
            import anthropic
            self._client = anthropic.Anthropic()
        else:
            self.offline = True

    def complete(self, *, system, messages: list, tools: list | None = None,
                 temperature: float | None = None,
                 trial_key: str = "", turn: int = 0) -> LLMResponse:
        key = self.cache.key(model=self.model, system=system, messages=messages,
                             tools=tools, temperature=temperature,
                             trial_key=trial_key, turn=turn)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(cached=True, model=self.model, **hit)

        if self.offline:
            raise OfflineError(
                f"no recorded response for trial {trial_key!r} turn {turn} and no "
                f"credentials available. Set ANTHROPIC_API_KEY (see .env.example) "
                f"to run live, or point at a run directory that has the cache.")

        self.budget.check()
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            # The system prompt and tool definitions are identical across every
            # trial in a study, so caching that prefix is most of the savings.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature

        started = time.perf_counter()
        resp = self._call_with_retries(kwargs)
        latency = (time.perf_counter() - started) * 1000

        usage = resp.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.budget.record(self.model, usage.input_tokens, usage.output_tokens,
                           cache_read, cache_write)

        payload = {
            "content": [b.to_dict() if hasattr(b, "to_dict") else dict(b)
                        for b in resp.content],
            "stop_reason": resp.stop_reason,
            "tokens_in": usage.input_tokens,
            "tokens_out": usage.output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "latency_ms": latency,
        }
        self.cache.put(key, payload)
        return LLMResponse(cached=False, model=self.model, **payload)

    def _call_with_retries(self, kwargs: dict, attempts: int = 4):
        """The SDK retries 429 and 5xx already. This adds a bounded outer retry
        so one flaky call does not abort a study that is otherwise an hour in."""
        import anthropic
        delay = 2.0
        last = None
        for i in range(attempts):
            try:
                return self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                last = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code < 500:
                    raise
                last = exc
            except anthropic.APIConnectionError as exc:
                last = exc
            time.sleep(delay)
            delay *= 2
        raise last
