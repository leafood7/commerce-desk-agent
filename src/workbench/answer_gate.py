"""Private, non-streaming, once-per-phase generation and semantic publication gate."""

from time import perf_counter

import httpx
from sqlalchemy import select

from workbench.answer_contract import (
    SAFE_ANSWER,
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


def produce_answer(database, run_id, settings, context, limits, guard, progress=None):
    mode = settings.policy_answer_mode
    if mode == "excerpts":
        return None

    def fallback(status, codes=None):
        return {
            "answer": SAFE_ANSWER
            + (
                "\n" + "\n".join(context["unknown_facts"].values())
                if context["unknown_facts"]
                else ""
            ),
            "answer_gate": {
                "mode": mode,
                "status": status,
                "codes": codes or [],
                "semantic_correctness_guaranteed": False,
            },
        }

    if not context["allowed_evidence"]:
        return fallback("no_evidence")
    try:
        if progress:
            progress("generating")
        generated = private_call(
            database, run_id, settings, "generate", context, None, limits, guard
        )
        draft = validate_draft(generated["raw"], context)
        if mode == "reviewed":
            if progress:
                progress("reviewing")
            reviewed = private_call(
                database, run_id, settings, "review", context, draft.model_dump(), limits, guard
            )
            decision = validate_review(reviewed["raw"])
            if not decision.approved:
                return fallback("blocked", decision.codes)
        return {
            "answer": render_draft(draft, context),
            "answer_gate": {
                "mode": mode,
                "status": "approved" if mode == "reviewed" else "contract_valid",
                "codes": [],
                "semantic_correctness_guaranteed": False,
            },
        }
    except ToolFailure as exc:
        if exc.code in {"cancelled", "worker_lease_lost", "forbidden", "unauthorized"}:
            raise
        return fallback("unavailable")
    except Exception:
        return fallback("invalid_contract_or_review")
