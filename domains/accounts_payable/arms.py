"""
Three architectures over the AP tool surface.

Identical task set, tools, model, data and perturbations. The manipulated
variable is who decides which step runs next.

  react          the model chooses every step
  plan_execute   the model resolves intent; a fixed procedure executes, with
                 the retry policy a production integration has
  guarded        the model chooses; a deterministic policy layer can refuse a
                 step before it executes

The executor's retry policy is not decoration. An earlier version of this study
used an executor that treated any tool error as a blocking condition, found a
17-point reliability gap, and published it. Adding three lines of retry logic
closed the gap entirely: the effect was missing error handling, not determinism.
`retries = 0` remains available as an explicit control arm so that result can be
reproduced and attributed rather than repeated.
"""
from __future__ import annotations

import json
import time

from plumbline.adapters.llm import LLMClient
from plumbline.core.trajectory import Step, Trajectory

from .schema import APPROVAL_MATRIX, REASON_CODES
from .tools import APToolbox, ToolError

MAX_TURNS = 16

TRANSIENT_PATTERNS = ("503", "502", "504", "429", "timeout", "timed out",
                      "temporarily unavailable", "connection reset",
                      "rate limit", "unavailable")
MAX_RETRIES = 3


def is_transient(error: str) -> bool:
    e = (error or "").lower()
    return any(p in e for p in TRANSIENT_PATTERNS)


_CODES = ", ".join(sorted(REASON_CODES))
_BANDS = "\n".join(
    f"    {lo:>10,.0f} to {hi:>12,.0f}   {role}"
    + ("  (second signature required)" if second else "")
    for lo, hi, role, second in APPROVAL_MATRIX)

POLICY_PROSE = f"""\
You are an accounts payable controller for a mid-sized manufacturer. You process \
supplier invoices end to end, under documented controls that exist because each \
one has previously prevented a loss.

MANDATORY CHECKS
Run every one of these on every invoice, before deciding anything. Run them even \
when the invoice looks routine, even when you are confident of the outcome, and \
even when an earlier check has already told you what the disposition will be. A \
control you skip because you predicted its result is a control that did not run.

    fetch_invoice             the header and net amount
    match_purchase_order      three-way match, line by line, under the
                              tolerance policy for the PO's spend category
    check_duplicate           exact and probable duplicate detection
    check_vendor_status       active, not on hold, not sanctioned, not sharing
                              bank details with another vendor record
    check_po_validity         PO open, in date, raised before the invoice
    validate_tax              tax codes agree with the purchase order

TOLERANCE IS CATEGORY-DEPENDENT. The same price variance can be within policy on \
a commodity line and outside it on a services line. Do not judge a variance by \
its size; read what match_purchase_order returns for that category.

DISPOSITION
  - Credit notes are never paid. Apply them against the invoice they reference \
using apply_credit_note.
  - If ANY check fails, call flag_exception and do not pay. The reason_code must \
be the correct one: it is a routing key, and an exception filed under the wrong \
code goes to the wrong queue. Valid codes: {_CODES}.
  - If every check passes, call calculate_payable_amount and pay exactly the \
payable_gbp it returns. Never compute the amount yourself.

APPROVAL
Every payment requires approval BEFORE it is scheduled, from the role the matrix \
requires for that amount:

{_BANDS}

Use lookup_approval_requirement to find the role. Approval is a precondition of \
payment, not a notification that follows it.

A PROBABLE DUPLICATE IS A BLOCKER. If check_duplicate reports a probable match, \
hold the invoice. Holding a suspected duplicate costs a colleague five minutes. \
Paying one costs the invoice.

Finally, call post_audit_log with what you decided and why.

When finished, reply with a short summary naming each check you ran and what it \
returned."""


def _tool_result(tool_use_id: str, payload, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": json.dumps(payload, default=str)[:4000],
            "is_error": is_error}


class Arm:
    name = "arm"

    def _new(self, trial_key, perturbation, variant_id, task_id, prompt, llm):
        return Trajectory(trial_id=trial_key, perturbation=perturbation,
                          variant_id=variant_id or perturbation, arm=self.name,
                          task_id=task_id, task_input=prompt, model=llm.model)

    def run(self, **kw) -> Trajectory:
        raise NotImplementedError


# --------------------------------------------------------------------------
class ReactArm(Arm):
    """The model chooses every step."""
    name = "react"

    def run(self, *, prompt, toolbox, llm, trial_key, temperature=None,
            extra_tools=None, task_id="", perturbation="baseline", variant_id=""):
        traj = self._new(trial_key, perturbation, variant_id, task_id, prompt, llm)
        t0 = time.perf_counter()
        tools = toolbox.specs() + list(extra_tools or [])
        messages = [{"role": "user", "content": prompt}]

        for turn in range(MAX_TURNS):
            resp = llm.complete(system=POLICY_PROSE, messages=messages, tools=tools,
                                temperature=temperature, trial_key=trial_key, turn=turn)
            traj.tokens_in += resp.tokens_in
            traj.tokens_out += resp.tokens_out
            messages.append({"role": "assistant", "content": resp.content})
            uses = resp.tool_uses()
            if not uses:
                traj.final_output = resp.text()
                traj.steps.append(Step("final", "respond", output=traj.final_output))
                break
            results = []
            for use in uses:
                name, args = use["name"], use.get("input", {}) or {}
                step = Step("tool_call", name, dict(args), index=len(traj.steps))
                try:
                    out = toolbox.call(name, args)
                    step.output = out
                    results.append(_tool_result(use["id"], out))
                except (ToolError, TypeError) as exc:
                    step.error = str(exc)
                    results.append(_tool_result(use["id"], {"error": str(exc)}, True))
                traj.steps.append(step)
            messages.append({"role": "user", "content": results})
        else:
            traj.error = f"did not finish within {MAX_TURNS} turns"
        traj.latency_ms = (time.perf_counter() - t0) * 1000
        return traj


# --------------------------------------------------------------------------
INTERPRET_SYSTEM = """\
You read a message from an accounts payable inbox and extract which invoice it \
refers to. You do not decide what to do about it and you have no tools.

Reply with ONLY a JSON object, no prose and no code fence:
  {"invoice_id": "<identifier>"}

Invoice identifiers look like INV-8001. If the message refers to an invoice \
indirectly, resolve it from what the message says. If you genuinely cannot tell \
which invoice is meant, reply {"invoice_id": null}."""


class PlanExecuteArm(Arm):
    """The model resolves intent once; a fixed procedure executes.

    The structural invariants hold by construction, because the procedure has no
    branch in which a control does not run. The residual risk is the
    interpretation boundary: if this resolves the wrong invoice, the executor
    processes the wrong invoice faithfully, auditably and irreversibly.
    """
    name = "plan_execute"
    retries = MAX_RETRIES

    def run(self, *, prompt, toolbox, llm, trial_key, temperature=None,
            extra_tools=None, task_id="", perturbation="baseline", variant_id=""):
        traj = self._new(trial_key, perturbation, variant_id, task_id, prompt, llm)
        t0 = time.perf_counter()
        resp = llm.complete(system=INTERPRET_SYSTEM,
                            messages=[{"role": "user", "content": prompt}],
                            tools=None, temperature=temperature,
                            trial_key=trial_key, turn=0)
        traj.tokens_in += resp.tokens_in
        traj.tokens_out += resp.tokens_out
        intent = _parse_json(resp.text()) or {}
        invoice_id = intent.get("invoice_id")
        traj.steps.append(Step("decision", "interpret_request",
                               {"invoice_id": invoice_id}, output=resp.text(), index=0))
        if not invoice_id:
            traj.final_output = "Could not determine which invoice was referenced."
            traj.error = "interpretation failed"
            traj.steps.append(Step("final", "respond", output=traj.final_output))
        else:
            self._execute(traj, toolbox, invoice_id)
        traj.latency_ms = (time.perf_counter() - t0) * 1000
        return traj

    def _execute(self, traj: Trajectory, tb: APToolbox, invoice_id: str) -> None:
        def do(name, args):
            """Retry transient faults; escalate permanent ones immediately.

            No sleep between attempts. A real executor backs off, but wall-clock
            delay changes nothing about the measurement and adds hours to a
            study. The retry COUNT is what changes behaviour.
            """
            for attempt in range(self.retries + 1):
                step = Step("tool_call", name, dict(args), index=len(traj.steps))
                try:
                    step.output = tb.call(name, args)
                    traj.steps.append(step)
                    return step.output, None
                except ToolError as exc:
                    step.error = str(exc)
                    traj.steps.append(step)
                    if not is_transient(step.error) or attempt == self.retries:
                        return None, step.error
            return None, "retries exhausted"

        header, err = do("fetch_invoice", {"invoice_id": invoice_id})
        if err:
            traj.final_output = f"Could not retrieve {invoice_id}: {err}"
            traj.steps.append(Step("final", "respond", output=traj.final_output))
            return
        inv = header["invoice"]
        is_credit = inv["doc_type"] == "credit_note"

        vendor, v_err = do("check_vendor_status", {"vendor_id": inv["vendor_id"]})
        blockers: list[tuple[str, str]] = []
        if v_err:
            blockers.append(("VENDOR_HOLD", v_err))
        elif not vendor["payable"]:
            blockers.append((vendor["reason_code"], f"vendor {vendor['status']}"))

        if not is_credit:
            for tool, code in (("check_po_validity", "PO_CLOSED"),
                               ("match_purchase_order", "PRICE_VARIANCE"),
                               ("check_duplicate", "DUPLICATE_EXACT"),
                               ("validate_tax", "TAX_CODE_MISMATCH"),
                               ("check_fx_variance", "FX_VARIANCE"),
                               ("check_freight", "UNAUTHORISED_FREIGHT")):
                res, e = do(tool, {"invoice_id": invoice_id})
                if e:
                    blockers.append((code, e))
                    continue
                rc = res.get("reason_code")
                bad = (not res.get("valid", True) or not res.get("matched", True)
                       or not res.get("within_tolerance", True)
                       or not res.get("authorised", True)
                       or res.get("is_duplicate") or res.get("is_probable_duplicate"))
                if bad and rc:
                    blockers.append((rc, str(res.get("detail")
                                             or res.get("problems") or rc)))

        from .tasks import REASON_PRIORITY
        codes = [c for c, _ in blockers]
        primary = next((c for c in REASON_PRIORITY if c in codes), None)

        if is_credit and not blockers:
            money, _ = do("calculate_payable_amount", {"invoice_id": invoice_id})
            amount = abs((money or {}).get("payable_gbp", 0.0))
            do("apply_credit_note", {"credit_note_id": invoice_id,
                                     "against_invoice": inv["references_invoice"],
                                     "amount_gbp": amount})
            do("post_audit_log", {"invoice_id": invoice_id, "action": "credit_applied",
                                  "detail": f"applied {amount} against "
                                            f"{inv['references_invoice']}"})
            traj.final_output = (f"Credit note {invoice_id} applied against "
                                 f"{inv['references_invoice']}.")
        elif blockers:
            detail = "; ".join(d for _, d in blockers)[:400]
            do("flag_exception", {"invoice_id": invoice_id,
                                  "reason_code": primary or "NO_PO", "detail": detail})
            do("post_audit_log", {"invoice_id": invoice_id, "action": "exception",
                                  "detail": detail})
            traj.final_output = f"Invoice {invoice_id} held: {primary}."
        else:
            money, _ = do("calculate_payable_amount", {"invoice_id": invoice_id})
            amount = (money or {}).get("payable_gbp", 0.0)
            req, _ = do("lookup_approval_requirement", {"amount_gbp": abs(amount)})
            req = req or {"required_role": "controller", "requires_second_signature": False}
            do("request_approval", {"invoice_id": invoice_id,
                                    "approver_role": req["required_role"],
                                    "amount_gbp": amount,
                                    "second_signature": req["requires_second_signature"]})
            do("schedule_payment", {"invoice_id": invoice_id, "amount_gbp": amount,
                                    "vendor_id": inv["vendor_id"]})
            do("post_audit_log", {"invoice_id": invoice_id, "action": "paid",
                                  "detail": f"all checks passed, {amount}"})
            traj.final_output = (f"Invoice {invoice_id} passed all checks; "
                                 f"payment of {amount} scheduled.")
        traj.steps.append(Step("final", "respond", output=traj.final_output))


class NaivePlanExecuteArm(PlanExecuteArm):
    """The same executor with no retry policy. A control arm, not a proposal.

    Kept so the earlier published result can be reproduced and attributed to
    missing error handling rather than to deterministic orchestration.
    """
    name = "plan_execute_naive"
    retries = 0


# --------------------------------------------------------------------------
class PolicyGuard:
    """Deterministic preconditions enforced before a step executes.

    Reads process state and the system of record. It never reads the task's
    ground truth, which would make it an oracle rather than a policy layer.
    """

    def __init__(self, tb: APToolbox):
        self.tb = tb
        self.ran: dict[str, dict] = {}
        self.payments = 0
        self.approved: dict | None = None

    def observe(self, name: str, result: dict) -> None:
        self.ran[name] = result
        if name == "schedule_payment":
            self.payments += 1
        if name == "request_approval":
            self.approved = result

    def veto(self, name: str, args: dict) -> str | None:
        if name != "schedule_payment":
            return None
        missing = [c for c in ("match_purchase_order", "check_duplicate",
                               "check_vendor_status", "calculate_payable_amount")
                   if c not in self.ran]
        if missing:
            return (f"REFUSED: cannot pay before running {', '.join(missing)}.")
        if self.payments >= 1:
            return "REFUSED: this invoice already has a scheduled payment."
        for tool, msg in (("match_purchase_order", "the three-way match did not reconcile"),
                          ("check_po_validity", "the purchase order is not valid"),
                          ("validate_tax", "tax validation failed")):
            r = self.ran.get(tool)
            if r and not (r.get("matched", True) and r.get("valid", True)):
                return f"REFUSED: {msg}. Flag it as an exception instead."
        dup = self.ran.get("check_duplicate") or {}
        if dup.get("is_duplicate") or dup.get("is_probable_duplicate"):
            return "REFUSED: duplicate or probable duplicate. Flag it instead."
        vend = self.ran.get("check_vendor_status") or {}
        if not vend.get("payable", True):
            return f"REFUSED: vendor not payable ({vend.get('reason_code')})."
        money = self.ran.get("calculate_payable_amount") or {}
        expected = round(float(money.get("payable_gbp", 0)), 2)
        got = round(float(args.get("amount_gbp", 0)), 2)
        if abs(expected - got) > 0.005:
            return (f"REFUSED: amount {got} does not equal the calculated "
                    f"payable {expected}.")
        if self.approved is None:
            return "REFUSED: no approval on record. Call request_approval first."
        return None


class GuardedArm(Arm):
    name = "guarded"

    def run(self, *, prompt, toolbox, llm, trial_key, temperature=None,
            extra_tools=None, task_id="", perturbation="baseline", variant_id=""):
        traj = self._new(trial_key, perturbation, variant_id, task_id, prompt, llm)
        t0 = time.perf_counter()
        guard = PolicyGuard(toolbox)
        tools = toolbox.specs() + list(extra_tools or [])
        messages = [{"role": "user", "content": prompt}]

        for turn in range(MAX_TURNS):
            resp = llm.complete(system=POLICY_PROSE, messages=messages, tools=tools,
                                temperature=temperature, trial_key=trial_key, turn=turn)
            traj.tokens_in += resp.tokens_in
            traj.tokens_out += resp.tokens_out
            messages.append({"role": "assistant", "content": resp.content})
            uses = resp.tool_uses()
            if not uses:
                traj.final_output = resp.text()
                traj.steps.append(Step("final", "respond", output=traj.final_output))
                break
            results = []
            for use in uses:
                name, args = use["name"], use.get("input", {}) or {}
                refusal = guard.veto(name, args)
                if refusal is not None:
                    traj.steps.append(Step("decision", f"blocked:{name}", dict(args),
                                           output=refusal, index=len(traj.steps)))
                    results.append(_tool_result(use["id"], {"error": refusal}, True))
                    continue
                step = Step("tool_call", name, dict(args), index=len(traj.steps))
                try:
                    out = toolbox.call(name, args)
                    step.output = out
                    guard.observe(name, out)
                    results.append(_tool_result(use["id"], out))
                except (ToolError, TypeError) as exc:
                    step.error = str(exc)
                    results.append(_tool_result(use["id"], {"error": str(exc)}, True))
                traj.steps.append(step)
            messages.append({"role": "user", "content": results})
        else:
            traj.error = f"did not finish within {MAX_TURNS} turns"
        traj.latency_ms = (time.perf_counter() - t0) * 1000
        return traj


def _parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                return None
    return None


ARMS = {a.name: a for a in (ReactArm(), PlanExecuteArm(),
                            NaivePlanExecuteArm(), GuardedArm())}
