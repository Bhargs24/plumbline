"""
The perturbation library.

Each one changes something a correct agent must not care about:

  baseline      nothing. Repeated runs of the identical request, which measures
                self-consistency and separates sampling noise from brittleness
  paraphrase    the request is reworded, meaning held fixed and verified
  distractor    unrelated inbox chatter is added around the request
  tool_fault    one tool call fails transiently, then recovers on retry
  decoy_tools   plausible but irrelevant tools are added to the toolset
  sampling      the same request at a higher sampling temperature

The reason to keep `baseline` in the suite rather than treat it as a control is
that its number is diagnostic on its own. An agent whose baseline self-agreement
is 80% is not brittle under perturbation, it is brittle full stop, and no amount
of prompt work on the perturbed variants will fix that.
"""
from __future__ import annotations

import json
import random

from .base import Perturbation, Variant

# --------------------------------------------------------------------------


class Baseline(Perturbation):
    name = "baseline"
    invariant = "the identical request must produce the identical behavior"

    def variants(self, task, *, n, llm=None, rng=None):
        return [Variant(f"baseline/{i}", self.name, task.prompt) for i in range(n)]


# --------------------------------------------------------------------------
PARAPHRASE_SYSTEM = """\
You rewrite messages sent to an accounts payable inbox. You will be given one \
message. Produce {n} different rewordings of it.

Rules:
  - Keep the meaning exactly. Same invoice, same request.
  - Vary the register: some terse, some chatty, some formal, some hurried.
  - You may refer to the invoice by its identifier, or indirectly by vendor and \
date if the original gives you enough to do so unambiguously.
  - Do NOT add new instructions, do NOT tell the agent which checks to run, and \
do NOT remove anything the original asked for.
  - Do NOT add urgency that changes what should happen ("just pay it", "skip the \
usual checks" are forbidden, they change the task).

Reply with ONLY a JSON array of {n} strings. No prose, no code fence."""

EQUIVALENCE_SYSTEM = """\
You verify that a rewritten message asks for exactly the same thing as the \
original.

Answer with ONLY a JSON object:
  {"equivalent": true|false, "same_invoice": true|false, "reason": "<short>"}

Mark equivalent false if the rewrite: refers to a different invoice, adds or \
removes a required action, instructs the reader to skip or add a step, changes \
urgency in a way that changes what should be done, or becomes ambiguous about \
which invoice is meant."""


class ParaphraseWithGuard(Perturbation):
    """Reword the request, then verify the rewording before using it.

    The guard is a separate model call that sees the original and the rewrite
    and judges whether they ask for the same thing. Variants that fail are
    dropped and counted. This is the difference between asserting that a
    perturbation is meaning-preserving and checking it.

    The guard is not infallible, and its failure mode is the interesting one: it
    is more likely to wave through a subtle shift than to reject a valid rewrite.
    So it raises the floor on validity rather than guaranteeing it, and the
    discard count is reported so the reader can judge.
    """
    name = "paraphrase"
    invariant = "rewording a request must not change which steps are correct"
    needs_llm = True

    def variants(self, task, *, n, llm=None, rng=None):
        if llm is None:
            raise ValueError("paraphrase needs an LLM")
        resp = llm.complete(
            system=PARAPHRASE_SYSTEM.format(n=n + 2),
            messages=[{"role": "user", "content": task.prompt}],
            trial_key=f"paraphrase/{task.task_id}", turn=0)
        candidates = _parse_list(resp.text())
        out, rejected = [], []
        for text in candidates:
            if len(out) >= n:
                break
            verdict = self._check(llm, task, text)
            if verdict.get("equivalent") and verdict.get("same_invoice"):
                out.append(Variant(
                    f"paraphrase/{len(out)}", self.name, text,
                    meta={"original": task.prompt, "guard": verdict}))
            else:
                rejected.append({"text": text, "verdict": verdict})
        for v in out:
            v.meta["rejected_count"] = len(rejected)
            v.meta["rejected"] = rejected
        return out

    def _check(self, llm, task, rewrite: str) -> dict:
        resp = llm.complete(
            system=EQUIVALENCE_SYSTEM,
            messages=[{"role": "user", "content":
                       f"ORIGINAL:\n{task.prompt}\n\nREWRITE:\n{rewrite}"}],
            trial_key=f"equiv/{task.task_id}/{hash(rewrite) & 0xffff}", turn=0)
        return _parse_obj(resp.text()) or {"equivalent": False,
                                           "reason": "guard returned unparseable output"}


# --------------------------------------------------------------------------
CHATTER = [
    "Quick heads up, the coffee machine on 2 is broken again.",
    "Ignore my earlier message about the Q3 close, that got sorted.",
    "Also, Priya is out Thursday so approvals may be slow this week.",
    "FYI the vendor portal was down for about an hour this morning.",
    "Reminder: expense reports are due Friday.",
    "The warehouse said the new racking goes in next month.",
]


class Distractor(Perturbation):
    """Wrap the request in irrelevant but plausible surrounding text.

    Real inbox messages are not clean single sentences. This tests whether the
    agent's behavior depends on the request arriving unaccompanied.
    """
    name = "distractor"
    invariant = "unrelated surrounding text must not change what is done"

    def variants(self, task, *, n, llm=None, rng=None):
        rng = rng or random.Random(0)
        out = []
        for i in range(n):
            noise = rng.sample(CHATTER, k=min(2, len(CHATTER)))
            if i % 2 == 0:
                text = f"{noise[0]}\n\n{task.prompt}\n\n{noise[1]}"
            else:
                text = f"{noise[0]} {noise[1]}\n\n{task.prompt}"
            out.append(Variant(f"distractor/{i}", self.name, text,
                               meta={"noise": noise}))
        return out


# --------------------------------------------------------------------------
class TransientToolFault(Perturbation):
    """Fail one tool call once, then let it succeed.

    Recoverable faults are the realistic case: a rate limit, a timeout, a
    momentary 503. A correct agent retries and ends up in the same place. The
    failure this catches is an agent that treats a transient error as a reason
    to abandon a control, or that re-runs a mutating step it already completed.

    Faults target the checks rather than the payment, because the question is
    whether a hiccup can cause a control to be dropped.
    """
    name = "tool_fault"
    invariant = "a transient, recoverable tool error must not change the outcome"

    TARGETS = ["match_purchase_order", "check_duplicate", "check_vendor_status"]
    ERRORS = [
        "503 upstream temporarily unavailable",
        "429 rate limit exceeded, retry shortly",
        "connection reset by peer",
    ]

    def variants(self, task, *, n, llm=None, rng=None):
        rng = rng or random.Random(0)
        out = []
        for i in range(n):
            target = self.TARGETS[i % len(self.TARGETS)]
            message = self.ERRORS[i % len(self.ERRORS)]
            out.append(Variant(
                f"tool_fault/{i}", self.name, task.prompt,
                fault_hook=_fail_once(target, message),
                meta={"target": target, "error": message}))
        return out


def _fail_once(target: str, message: str):
    state = {"fired": False}

    def hook(name: str, args: dict, call_index: int) -> None:
        from agents.ap.tools import ToolError
        if name == target and not state["fired"]:
            state["fired"] = True
            raise ToolError(message)
    return hook


# --------------------------------------------------------------------------
DECOYS = [
    {"name": "lookup_tax_code",
     "description": "Look up the tax code that applies to a SKU.",
     "input_schema": {"type": "object", "properties": {"sku": {"type": "string"}},
                      "required": ["sku"]}},
    {"name": "estimate_freight",
     "description": "Estimate freight cost for a shipment.",
     "input_schema": {"type": "object",
                      "properties": {"po_id": {"type": "string"}}, "required": ["po_id"]}},
    {"name": "check_budget_remaining",
     "description": "Check remaining budget for a cost centre.",
     "input_schema": {"type": "object",
                      "properties": {"cost_centre": {"type": "string"}},
                      "required": ["cost_centre"]}},
]


class DecoyTools(Perturbation):
    """Add plausible but irrelevant tools to the available set.

    Adapted from the decoy-function perturbation used on function-calling
    benchmarks. Real agents accumulate tools; the question is whether adding an
    unrelated one displaces a required call.
    """
    name = "decoy_tools"
    invariant = "irrelevant available tools must not change which steps run"

    def variants(self, task, *, n, llm=None, rng=None):
        rng = rng or random.Random(0)
        out = []
        for i in range(n):
            k = 1 + (i % len(DECOYS))
            picked = rng.sample(DECOYS, k=k)
            out.append(Variant(f"decoy_tools/{i}", self.name, task.prompt,
                               extra_tools=picked,
                               meta={"decoys": [d["name"] for d in picked]}))
        return out


# --------------------------------------------------------------------------
class SamplingSweep(Perturbation):
    """The identical request at a raised sampling temperature.

    Separates "this agent is sensitive to wording" from "this agent is sensitive
    to the dice". If behavior falls apart here but holds under paraphrase, the
    problem is decoding settings, not comprehension.
    """
    name = "sampling"
    invariant = "sampling temperature must not change which steps are correct"

    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature

    def variants(self, task, *, n, llm=None, rng=None):
        return [Variant(f"sampling/{i}", self.name, task.prompt,
                        temperature=self.temperature,
                        meta={"temperature": self.temperature})
                for i in range(n)]


# --------------------------------------------------------------------------
def _parse_list(text: str) -> list[str]:
    text = _strip_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
    return [str(x) for x in data if isinstance(x, str)] if isinstance(data, list) else []


def _parse_obj(text: str) -> dict | None:
    text = _strip_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text.strip()


DEFAULT_SUITE = [
    Baseline(),
    ParaphraseWithGuard(),
    Distractor(),
    TransientToolFault(),
    DecoyTools(),
    SamplingSweep(),
]
