"""SQL-backed admission and cumulative reservations. Unknown model usage stays reserved."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from workbench.flow_models import FlowAction
from workbench.mcp_gateway import ToolFailure
from workbench.models import Run, RunEvent
from workbench.quota_models import AdmissionGate, RunUsage


def acquire_admission(session):
    # Authentication may already have established a MySQL RR snapshot.
    # End that read transaction before taking the mutex; first consistent read is now fresh.
    # Call before ANY run/action mutation or row lock.
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Admission requires a read-only request session")
    session.commit()
    if session.bind.dialect.name == "mysql":
        statement = mysql_insert(AdmissionGate).values(key="runs")
        statement = statement.on_duplicate_key_update(key=statement.inserted.key)
    else:
        statement = sqlite_insert(AdmissionGate).values(key="runs").on_conflict_do_nothing()
    session.execute(statement)
    session.scalar(select(AdmissionGate).where(AdmissionGate.key == "runs").with_for_update())


def check_admission(session, actor, settings):
    # No new admission can increase these counts while this transaction owns the gate.
    # Workers/cancellation can only reduce committed active counts (safe overestimation).
    rows = session.execute(
        select(Run.organization_id, Run.owner_id)
        .where(Run.status.in_(["queued", "running"]))
        .limit(settings.queue_global_limit + 1)
    ).all()
    owner = sum(org == actor.organization_id and owner == actor.id for org, owner in rows)
    if len(rows) >= settings.queue_global_limit or owner >= settings.queue_owner_limit:
        raise HTTPException(
            429, "Execution queue is full; retry later", headers={"Retry-After": "2"}
        )


def observed_tokens(usage):
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    parts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    if total is None and all(type(x) is int and x >= 0 for x in parts):
        total = sum(parts)
    if type(total) is not int or not 0 < total <= 2147483647:
        return None
    # Do not accept contradictory usage as a zero/cheap successful settlement.
    if all(type(x) is int and x >= 0 for x in parts) and total < sum(parts):
        return None
    return total


def ensure_usage(session, run, settings, *, legacy=True):
    row = session.get(RunUsage, run.id)
    if row:
        return row
    tools = 0
    model = None
    if legacy:
        tools = session.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.type.in_(["tool.started", "tool.retry"]))
        )
        model = session.get(FlowAction, (run.id, "__intent_router__"))
    usage = (model.body.get("result") or {}).get("usage") if model else None
    known = observed_tokens(usage)
    row = RunUsage(
        run_id=run.id,
        tool_attempts=tools,
        model_attempts=int(model is not None),
        model_reserved_tokens=settings.run_model_reserve_tokens if model and known is None else 0,
        model_observed_tokens=known or 0,
        unknown_model_attempts=int(model is not None and known is None),
        max_tools=settings.run_tool_limit,
        max_model_attempts=1,
        max_model_tokens=settings.run_model_token_limit,
        legacy_estimate=legacy,
    )
    session.add(row)
    session.flush()
    return row


def reserve_tool(database, run_id, guard=None):
    with database.sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if guard and not guard(session):
            raise ToolFailure("worker_lease_lost")
        if run.status != "running":
            raise ToolFailure("cancelled")
        usage = ensure_usage(session, run, database.settings)
        if usage.tool_attempts >= usage.max_tools:
            raise ToolFailure("run_tool_quota")
        usage.tool_attempts += 1
        session.commit()


def reserve_model(session, run, settings):
    usage = ensure_usage(session, run, settings)
    amount = settings.run_model_reserve_tokens
    if usage.model_attempts >= usage.max_model_attempts or (
        usage.model_reserved_tokens + usage.model_observed_tokens + amount > usage.max_model_tokens
    ):
        raise ToolFailure("run_model_quota")
    usage.model_attempts += 1
    usage.model_reserved_tokens += amount
    usage.unknown_model_attempts += 1
    return amount


def settle_model(session, run, settings, amount, counts, *, no_dispatch=False):
    usage = ensure_usage(session, run, settings)
    total = 0 if no_dispatch else observed_tokens(counts)
    if total is not None:
        usage.model_reserved_tokens = max(0, usage.model_reserved_tokens - amount)
        usage.unknown_model_attempts = max(0, usage.unknown_model_attempts - 1)
        usage.model_observed_tokens += total
    return usage.model_observed_tokens + usage.model_reserved_tokens <= usage.max_model_tokens


def usage_view(row):
    return {
        "tool_attempts": row.tool_attempts,
        "max_tools": row.max_tools,
        "remaining_tools": max(0, row.max_tools - row.tool_attempts),
        "model_attempts": row.model_attempts,
        "max_model_attempts": row.max_model_attempts,
        "model_reserved_tokens": row.model_reserved_tokens,
        "model_observed_tokens": row.model_observed_tokens,
        "unknown_model_attempts": row.unknown_model_attempts,
        "max_model_tokens": row.max_model_tokens,
        "remaining_model_tokens": max(
            0, row.max_model_tokens - row.model_observed_tokens - row.model_reserved_tokens
        ),
        "legacy_estimate": row.legacy_estimate,
        "cost_cny": None,
    }
