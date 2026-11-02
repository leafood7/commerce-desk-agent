"""Private, non-streaming, once-per-phase generation and semantic publication gate."""

from time import perf_counter

import httpx
from sqlalchemy import select

from workbench.answer_contract import (
    SAFE_ANSWER,
    AnswerDraft,
    DraftContractIssue,
    messages,
    prompt_hash,
    render_draft,
    validate_draft,
    validate_review,
)
from workbench.context_management import ContextOverflow, pack_messages
from workbench.domain import fingerprint
from workbench.flow_models import FlowAction
from workbench.mcp_gateway import ToolFailure
from workbench.models import Run
from workbench.quotas import reserve_model, settle_model

PREFIX = "__answer_gate__:"


class GateNotSent(ValueError):
    pass


def model_call(settings, phase, context, draft=None, *, timeout=12):
    try:
        packed, _ = pack_messages(
            settings,
            messages(phase, context, draft),
            output_tokens=settings.policy_answer_max_tokens,
        )
    except ContextOverflow as exc:
        raise GateNotSent("Required input exceeds allowance") from exc
    body = {
        "model": settings.agent_router_model,
        "temperature": 0,
        "max_tokens": settings.policy_answer_max_tokens,
        "response_format": {"type": "json_object"},
        "messages": packed,
    }
    if settings.agent_router_model.startswith("deepseek-"):
        body["thinking"] = {"type": "disabled"}
    response = httpx.post(
        settings.agent_router_url,
        headers={"Authorization": "Bearer " + settings.agent_router_key.get_secret_value()},
        json=body,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    response.raise_for_status()
    if len(response.content) > 65536:
        raise ValueError("Response size limit")
    payload = response.json()
    usage = payload.get("usage")
    if not isinstance(usage, dict) or any(
        v is not None and (type(v) is not int or v < 0)
        for k, v in usage.items()
        if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
    ):
        usage = None
    raw = payload["choices"][0]["message"]["content"]
    if not isinstance(raw, str):
        raise ValueError("Invalid model content")
    return {"raw": raw, "usage": usage, "response_model": str(payload.get("model", ""))[:128]}


def private_call(database, run_id, settings, phase, context, draft, limits, guard):
    digest = fingerprint(
        {
            "phase": phase,
            "context": context,
            "draft": draft,
            "prompt": prompt_hash(),
            "model": settings.agent_router_model,
            "url": settings.agent_router_url,
            "output_cap": settings.policy_answer_max_tokens,
        }
    )
    key = PREFIX + digest
    guard()
    limits.check()
    with database.sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run.status != "running":
            raise ToolFailure("cancelled")
        cached = session.get(FlowAction, (run_id, key))
        if cached:
            if cached.body.get("status") != "finished":
                raise ToolFailure("answer_unknown_cached")
            return cached.body["result"]
        amount = reserve_model(session, run, settings)
        session.add(
            FlowAction(
                run_id=run_id,
                key=key,
                input_hash=digest,
                body={"status": "reserved", "phase": phase},
            )
        )
        session.commit()
    result, counts, no_dispatch, error = None, None, False, None
    start = perf_counter()
    try:
        result = model_call(
            settings,
            phase,
            context,
            draft,
            timeout=min(settings.policy_answer_timeout_seconds, limits.remaining()),
        )
        counts = result.get("usage")
    except GateNotSent:
        no_dispatch = True
        error = "preflight"
    except Exception:
        error = "invalid_or_unavailable"
    with database.sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        within = settle_model(session, run, settings, amount, counts, no_dispatch=no_dispatch)
        row = session.get(FlowAction, (run_id, key))
        row.body = {
            "status": "finished" if result and within and not error else "failed",
            "phase": phase,
            "result": result,
            "error": error or (None if within else "quota"),
            "elapsed_ms": (perf_counter() - start) * 1000,
        }
        session.commit()
    guard()
    limits.check()
    if error or not within:
        raise ToolFailure("answer_model_failed")
    return result


def reserve_repair_once(database, run_id, context, draft, codes, guard):
    """Consume the run's single repair allowance before I/O; never restore on replay."""
    digest = fingerprint(
        {"context": context, "draft": draft, "codes": codes, "prompt": prompt_hash()}
    )
    guard()
    with database.sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run.status != "running":
            raise ToolFailure("cancelled")
        key = "__answer_repair_once__"
        row = session.get(FlowAction, (run_id, key))
        if row:
            if row.input_hash != digest:
                raise ToolFailure("repair_already_consumed")
        else:
            session.add(
                FlowAction(
                    run_id=run_id,
                    key=key,
                    input_hash=digest,
                    body={"status": "consumed", "phase": "repair", "codes": codes},
                )
            )
        session.commit()


class AnswerSession:
    """Shared bounded role operations for the fixed workflow and collaborative graph."""

    def __init__(self, database, run_id, settings, context, limits, guard, progress=None):
        self.database, self.run_id, self.settings = database, run_id, settings
        self.context, self.limits, self.guard, self.progress = context, limits, guard, progress
        self.mode = settings.policy_answer_mode
        self.repair_attempted = False

    def call(self, phase, payload=None):
        phases = {
            "generate": "generating",
            "review": "reviewing",
            "repair": "repairing",
            "review_repair": "reviewing_repair",
        }
        if self.progress:
            self.progress(phases[phase])
        return private_call(
            self.database,
            self.run_id,
            self.settings,
            phase,
            self.context,
            payload,
            self.limits,
            self.guard,
        )

    def fallback(self, status, codes=None):
        return {
            "answer": SAFE_ANSWER
            + (
                "\n" + "\n".join(self.context["unknown_facts"].values())
                if self.context["unknown_facts"]
                else ""
            ),
            "answer_gate": {
                "mode": self.mode,
                "status": status,
                "codes": codes or [],
                "repair": {"attempted": self.repair_attempted},
                "semantic_correctness_guaranteed": False,
            },
        }

    def published(self, draft, repaired=False):
        return {
            "answer": render_draft(draft, self.context),
            "answer_gate": {
                "mode": self.mode,
                "status": "approved"
                if self.mode in {"reviewed", "reviewed_repair"}
                else "contract_valid",
                "codes": [],
                "repair": {
                    "attempted": repaired,
                    "outcome": "approved" if repaired else "not_needed",
                },
                "semantic_correctness_guaranteed": False,
                "evidence_bindings": [
                    {"label": i + 1, "id": row["id"], "version_id": row["version_id"]}
                    for i, row in enumerate(self.context["allowed_evidence"])
                ],
            },
        }

    def repair_usable(self):
        return not self.context.get("requires_partial_boundary") and not any(
            (self.context.get("assembly_gaps") or {}).values()
        )

    def repair_generation(self, draft, codes, check_kind):
        if not self.repair_usable():
            raise ToolFailure("answer_evidence_gap")
        reserve_repair_once(
            self.database, self.run_id, self.context, draft.model_dump(), codes, self.guard
        )
        self.repair_attempted = True
        payload = {
            "previous_draft": draft.model_dump(),
            "check_codes": codes,
            "check_kind": check_kind,
        }
        return validate_draft(self.call("repair", payload)["raw"], self.context)

    def check(self, draft, repaired=False):
        return validate_review(
            self.call("review_repair" if repaired else "review", draft.model_dump())["raw"]
        )

    def repair_and_check(self, draft, codes, check_kind):
        if not self.repair_usable():
            return self.fallback("blocked_evidence_gap", codes)
        corrected = self.repair_generation(draft, codes, check_kind)
        decision = self.check(corrected, repaired=True)
        if not decision.approved:
            return self.fallback("repair_rejected", decision.codes)
        return self.published(corrected, repaired=True)


def produce_answer(database, run_id, settings, context, limits, guard, progress=None):
    engine = AnswerSession(database, run_id, settings, context, limits, guard, progress)
    if engine.mode == "excerpts":
        return None
    if not context["allowed_evidence"]:
        return engine.fallback("no_evidence")
    try:
        generated = engine.call("generate")
        try:
            draft = validate_draft(generated["raw"], context)
        except DraftContractIssue as issue:
            if engine.mode != "reviewed_repair":
                return engine.fallback("invalid_contract_or_review")
            return engine.repair_and_check(
                AnswerDraft.model_validate_json(generated["raw"]), [issue.code], "program"
            )
        if engine.mode in {"reviewed", "reviewed_repair"}:
            decision = engine.check(draft)
            if not decision.approved:
                if engine.mode == "reviewed_repair":
                    return engine.repair_and_check(draft, decision.codes, "semantic")
                return engine.fallback("blocked", decision.codes)
        return engine.published(draft)
    except ToolFailure as exc:
        if exc.code in {"cancelled", "worker_lease_lost", "forbidden", "unauthorized"}:
            raise
        return engine.fallback("repair_unavailable" if engine.repair_attempted else "unavailable")
    except Exception:
        return engine.fallback(
            "repair_invalid" if engine.repair_attempted else "invalid_contract_or_review"
        )
