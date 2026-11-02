"""Per-request official-provider budget wrapper for the frozen return journey audit."""

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from evaluation.answer_trust_budget import BudgetLedger, cost
from evaluation.answer_trust_data import write_json

PHASE = "return-journey-audit-v1-20261101"


class BatchStop(RuntimeError):
    pass


class AuditBudget:
    def __init__(self, path, output, *, allowed_cases=None, allowed_arms=None):
        if not Path(path).is_file():
            raise ValueError("Existing cumulative ledger required")
        self.ledger = BudgetLedger(path)
        self.output = Path(output)
        self.current = {}
        self.calls = []
        self.stopped = None
        self.allowed_cases = allowed_cases
        if allowed_arms is not None and (
            not allowed_arms or not set(allowed_arms) <= {"baseline_frozen", "candidate_v2"}
        ):
            raise ValueError("Unknown preregistered comparison arm")
        self.allowed_arms = set(allowed_arms) if allowed_arms is not None else None
        if any(
            e.get("metadata", {}).get("phase") == PHASE
            and (allowed_cases is None or e.get("metadata", {}).get("case_id") in allowed_cases)
            and (self.allowed_arms is None or e.get("metadata", {}).get("arm") in self.allowed_arms)
            for e in self.ledger.snapshot()["entries"]
        ):
            raise ValueError(
                "This authorization phase already has calls; refusing automatic replay"
            )

    def call(self, stage, messages, output_tokens, callback):
        if self.allowed_arms is not None and self.current.get("arm") not in self.allowed_arms:
            raise BatchStop("Arm is outside the preregistered comparison")
        if self.allowed_cases is not None and self.current.get("case_id") not in self.allowed_cases:
            raise BatchStop("Case is outside the frozen allowed set")
        # The caller performs the production preflight before reserving.
        reserve = cost(6000, output_tokens)
        if self.stopped:
            raise BatchStop(self.stopped)
        token = uuid4().hex
        with self.ledger.transaction() as state:
            prior = [e for e in state["entries"] if e.get("metadata", {}).get("phase") == PHASE]
            if (
                len(prior) >= 128
                or sum(e["charged_cny"] for e in prior) + reserve > 1
                or sum(e["charged_cny"] for e in state["entries"]) + reserve > state["limit_cny"]
            ):
                self.stopped = "authorization_or_cumulative_budget_exhausted"
                raise BatchStop(self.stopped)
            entry = {
                "id": token,
                "status": "reserved",
                "reserved_cny": reserve,
                "charged_cny": reserve,
                "started_at": datetime.now(UTC).isoformat(),
                "usage": None,
                "metadata": {
                    "phase": PHASE,
                    "stage": stage,
                    **self.current,
                    "authorization_max_calls": 128,
                    "authorization_max_cny": 1,
                },
            }
            state["entries"].append(entry)
            write_json(self.ledger.path, state)
        started = perf_counter()
        result = None
        error = None
        try:
            result = callback()
        except Exception as exc:
            error = type(exc).__name__
        usage = result.get("usage") if result else None
        known = isinstance(usage, dict) and all(
            type(usage.get(k)) is int and usage[k] >= 0
            for k in ("prompt_tokens", "completion_tokens")
        )
        exceeded = known and (
            usage["prompt_tokens"] > 6000 or usage["completion_tokens"] > output_tokens
        )
        with self.ledger.transaction() as state:
            entry = next(e for e in state["entries"] if e["id"] == token)
            entry.update(
                status="provider_limit_violation"
                if exceeded
                else "settled"
                if known
                else "unknown_usage",
                usage=usage,
                charged_cny=cost(usage["prompt_tokens"], usage["completion_tokens"])
                if known
                else reserve,
                elapsed_ms=(perf_counter() - started) * 1000,
                error=error,
                response_model=(result.get("response_model") or result.get("model"))
                if result
                else None,
            )
            write_json(self.ledger.path, state)
            self.calls.append(
                {
                    **{k: v for k, v in entry.items() if k != "id"},
                    "messages": messages,
                    "response": result,
                }
            )
        write_json(self.output / "model-calls.json", self.calls)
        if exceeded:
            self.stopped = "provider_exceeded_reservation"
            raise BatchStop(self.stopped)
        if error:
            self.stopped = "provider_request_failed"
            raise RuntimeError("Provider request failed: " + error)
        return result

    def summary(self):
        state = self.ledger.snapshot()
        entries = [e for e in state["entries"] if e.get("metadata", {}).get("phase") == PHASE]
        return {
            "requests": len(entries),
            "charged_cny": sum(e["charged_cny"] for e in entries),
            "unknown_calls": sum(e["status"] != "settled" for e in entries),
            "cumulative_cny": state["committed_cny"],
            "stopped": self.stopped,
        }
