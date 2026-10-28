"""One durable budget across runs; every request reserves before network I/O."""
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from evaluation.answer_trust_data import read_json, write_json

TOTAL_CNY = 100.0
INPUT_CNY_PER_MILLION = 2.0
OUTPUT_CNY_PER_MILLION = 8.0
INPUT_LIMIT = 1_048_576
OUTPUT_LIMIT = 2048

class BudgetStop(RuntimeError):
    answer_guard_passthrough = True

def cost(input_tokens, output_tokens):
    return (input_tokens * INPUT_CNY_PER_MILLION + output_tokens * OUTPUT_CNY_PER_MILLION) / 1_000_000

class BudgetLedger:
    def __init__(self, path, *, limit=TOTAL_CNY):
        if not 0 < limit <= TOTAL_CNY:
            raise ValueError("Budget must be positive and no greater than 100 CNY")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".lock")
        self.anchor = self.path.with_suffix(self.path.suffix + ".initialized")
        with self.transaction(create=True) as state:
            if state is None:
                if self.anchor.exists():
                    raise BudgetStop("Budget ledger is missing; refusing to reset cumulative spend")
                self.anchor.write_text(str(self.path.resolve()), encoding="utf-8")
                write_json(self.path, {"version": 1, "limit_cny": limit, "input_cny_per_million": INPUT_CNY_PER_MILLION,
                    "output_cny_per_million": OUTPUT_CNY_PER_MILLION, "pricing_basis": "peak uncached estimate, not invoice",
                    "entries": []}, exclusive=True)
            elif state["limit_cny"] != limit:
                raise BudgetStop("Existing budget limit cannot change")

    @contextmanager
    def transaction(self, create=False):
        try:
            descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise BudgetStop("Budget is locked; do not run concurrent evaluations or reset a stale lock blindly") from exc
        try:
            os.close(descriptor)
            if not self.path.exists() and not create:
                raise BudgetStop("Budget ledger disappeared")
            yield read_json(self.path) if self.path.exists() else None
        finally:
            self.lock.unlink(missing_ok=True)

    def reserve(self, metadata=None):
        maximum = cost(INPUT_LIMIT, OUTPUT_LIMIT)
        with self.transaction() as state:
            committed = sum(e["charged_cny"] for e in state["entries"])
            if committed + maximum > state["limit_cny"] + 1e-9:
                raise BudgetStop("Insufficient remaining budget for worst-case request reservation")
            token = uuid4().hex
            state["entries"].append({"id": token, "status": "reserved", "reserved_cny": maximum,
                "charged_cny": maximum, "started_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {}, "usage": None})
            write_json(self.path, state)
            return token

    def settle(self, token, usage, *, error_type=None, elapsed_ms=None, model=None):
        with self.transaction() as state:
            entry = next((e for e in state["entries"] if e["id"] == token), None)
            if entry is None or entry["status"] != "reserved":
                raise BudgetStop("Unknown or already settled reservation")
            entry.update({"error_type": error_type, "elapsed_ms": elapsed_ms, "response_model": model})
            valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens"))
            if valid:
                charge = cost(usage["input_tokens"], usage["output_tokens"])
                entry.update({"status": "settled", "usage": usage, "charged_cny": charge})
                if usage["input_tokens"] > INPUT_LIMIT or usage["output_tokens"] > OUTPUT_LIMIT:
                    entry["status"] = "provider_limit_violation"
                    write_json(self.path, state)
                    raise BudgetStop("Provider exceeded the reserved token limit; stop and audit")
            else:
                entry["status"] = "unknown_usage"
            write_json(self.path, state)
            return dict(entry)

    def snapshot(self):
        with self.transaction() as state:
            total = sum(e["charged_cny"] for e in state["entries"])
            return {**state, "committed_cny": total, "remaining_cny": state["limit_cny"] - total,
                "unknown_or_pending_calls": sum(e["status"] != "settled" for e in state["entries"])}

class BudgetedModel:
    """Preserve accounting after bind/bind_tools; no async streaming bypass."""
    def __init__(self, model, ledger, records, metadata=None):
        self.model, self.ledger, self.records = model, ledger, records
        self.metadata = metadata or {}

    def bind(self, **kwargs):
        for key in ("max_tokens", "max_completion_tokens"):
            if key in kwargs and (type(kwargs[key]) is not int or not 1 <= kwargs[key] <= OUTPUT_LIMIT):
                raise BudgetStop("Output limit exceeds reservation")
        if kwargs.get("stream") is True or kwargs.get("streaming") is True:
            raise BudgetStop("Evaluation requires non-streaming response usage")
        return BudgetedModel(self.model.bind(**kwargs), self.ledger, self.records, self.metadata)

    def bind_tools(self, tools, **kwargs):
        return BudgetedModel(self.model.bind_tools(tools, **kwargs), self.ledger, self.records, self.metadata)

    async def ainvoke(self, messages, config=None, **kwargs):
        token = self.ledger.reserve(self.metadata)
        started = perf_counter()
        try:
            response = await self.model.ainvoke(messages, config=config, **kwargs)
        except BaseException as exc:
            record = self.ledger.settle(token, None, error_type=type(exc).__name__, elapsed_ms=(perf_counter()-started)*1000)
            self.records.append(record)
            raise
        record = self.ledger.settle(token, getattr(response, "usage_metadata", None),
            elapsed_ms=(perf_counter()-started)*1000,
            model=getattr(response, "response_metadata", {}).get("model_name"))
        self.records.append(record)
        return response

