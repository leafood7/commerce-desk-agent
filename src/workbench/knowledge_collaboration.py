"""Private LangGraph policy roles. Retrieval can only search/read current sources."""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from workbench.answer_contract import (
    DraftContractIssue,
    parse_draft,
    question_context,
    validate_draft,
)
from workbench.answer_gate import AnswerSession
from workbench.checkpoints import SQLCheckpointSaver
from workbench.domain import fingerprint
from workbench.execution_limits import call_bounded
from workbench.flow_models import FlowAction
from workbench.knowledge_schemas import ReadInput
from workbench.knowledge_service import validated_evidence
from workbench.mcp_gateway import ToolFailure
from workbench.models import Run
from workbench.quota_models import RunUsage
from workbench.return_decisions import choose_return_action
from workbench.source_reading import (
    available_targets,
    next_targets,
    reading_evidence,
    reference_gaps,
    reserve_source_io,
    search_targets,
)
from workbench.tool_contracts import verify_grant


class EvidenceHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ids: list[str]
    version_ids: list[str]
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reads: int = Field(ge=0, le=2)
    supplements: int = Field(ge=0, le=1)
    stop_reason: str


class CrewState(TypedDict, total=False):
    context: dict
    matches: list
    trace: dict
    handoff: dict
    input_digest: str
    draft: dict
    codes: list
    check_kind: str
    next_step: str
    repair_attempted: bool
    result: dict
    tasks: list
    task_board: dict
    retrieval_passes: int
    suggested_targets: list
    annotation_digest: str


class RetrievalRole:
    allowed_tools = frozenset({"search_knowledge_base", "read_policy_source"})

    def __init__(
        self, database, run_id, settings, gateway, token, limits, guard, execution_guard, announce
    ):
        self.database, self.run_id, self.settings = database, run_id, settings
        self.gateway, self.token, self.limits, self.guard = gateway, token, limits, guard
        self.execution_guard, self.announce = execution_guard, announce

    def locked_guard(self, session, run):
        if self.execution_guard and not self.execution_guard(session):
            raise ToolFailure("worker_lease_lost")
        actor, scopes = verify_grant(session, self.token, self.settings)
        if "knowledge:read" not in scopes:
            raise ToolFailure("forbidden")
        if actor.id != run.owner_id or actor.organization_id != run.organization_id:
            raise ToolFailure("forbidden")
        if run.status != "running":
            raise ToolFailure("cancelled")

    def current(self, matches, audience):
        self.guard()
        with self.database.sessions() as session:
            actor, scopes = verify_grant(session, self.token, self.settings)
            if "knowledge:read" not in scopes:
                raise ToolFailure("forbidden")
            return validated_evidence(session, actor, matches, audience=audience)

    def spent(self):
        with self.database.sessions() as session:
            row = session.get(FlowAction, (self.run_id, "__source_budget__"))
            return dict(row.body) if row else {"reads": 0, "supplements": 0}

    def cached(self, name, body):
        key = "__knowledge_io__:" + fingerprint({"name": name, "arguments": body})
        with self.database.sessions() as session:
            row = session.get(FlowAction, (self.run_id, key))
            return row.body if row else None

    def tool(self, name, body):
        if name not in self.allowed_tools:
            raise ToolFailure("role_tool_forbidden")
        if not self.settings.return_source_reading_enabled:
            raise ToolFailure("source_reading_disabled")
        key = "__knowledge_io__:" + fingerprint({"name": name, "arguments": body})
        self.guard()
        with self.database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            self.locked_guard(session, run)
            row = session.get(FlowAction, (self.run_id, key))
            if row:
                if row.body.get("status") != "finished":
                    raise ToolFailure("role_tool_outcome_unknown")
                return row.body["payload"]
            session.add(
                FlowAction(
                    run_id=self.run_id,
                    key=key,
                    input_hash=key.split(":")[-1],
                    body={"status": "reserved", "name": name},
                )
            )
            session.commit()
        try:
            reserve_source_io(
                self.database,
                self.run_id,
                "reads" if name == "read_policy_source" else "supplements",
                self.locked_guard,
            )
            self.announce("retrieval_agent", "tool.started", name=name)
            payload = asyncio.run(
                call_bounded(
                    self.gateway,
                    name,
                    body,
                    self.token,
                    self.limits,
                    self.settings.model_copy(update={"mcp_read_retries": 0}),
                    lambda *a, **k: None,
                    self.guard,
                )
            )
            self.guard()
            with self.database.sessions() as session:
                row = session.get(FlowAction, (self.run_id, key))
                row.body = {"status": "finished", "name": name, "payload": payload}
                session.commit()
            self.announce("retrieval_agent", "tool.finished", name=name)
            return payload
        except Exception:
            with self.database.sessions() as session:
                row = session.get(FlowAction, (self.run_id, key))
                row.body = {"status": "failed", "name": name}
                session.commit()
            raise

    def run(self, arguments, initial):
        audience = arguments.get("audience")
        rows = self.current(initial.get("matches", []), audience)
        trace = dict(initial.get("trace", {}))
        state = {
            "retrieved_evidence": rows,
            "source_targets": arguments.get("_authorized_targets") or search_targets(rows),
            "source_attempted_targets": [],
            "context_purpose": "policy_question",
        }
        reason = "current_evidence_handoff"
        versions = sorted({r["version_id"] for r in rows})
        supplement_args = None
        if len(versions) == 1:
            supplement_args = {
                "query": arguments["query"],
                "version_id": versions[0],
                "policy_scope": arguments.get("policy_scope"),
                "strategy": "hybrid_rerank",
                "top_k": 3,
            }
            if audience:
                supplement_args["audience"] = audience
            completed = self.cached("search_knowledge_base", supplement_args)
            if completed and completed.get("status") == "finished":
                extra = self.current(completed["payload"].get("matches", []), audience)
                rows = list({r["id"]: r for r in [*rows, *extra]}.values())
                state["retrieved_evidence"] = rows
                state["source_targets"] = search_targets(rows)
        for step in range(arguments.get("_max_io_steps", 4)):
            self.guard()
            spent = self.spent()
            state.update(source_read_count=spent["reads"], source_read_chars=0)
            targets = available_targets(state)
            raw_targets = [
                t
                for t in state["source_targets"]
                if t["id"] not in state["source_attempted_targets"]
            ]
            known_requests = [
                r.get("read_request") for r in rows if r.get("source_kind") == "source_read"
            ]
            # Reuse completed original reads before consulting another model after recovery.
            cached_target = next(
                (
                    t
                    for t in raw_targets
                    if t["request"] not in known_requests
                    and (self.cached("read_policy_source", t["request"]) or {}).get("status")
                    == "finished"
                ),
                None,
            )
            if cached_target:
                selected = {"action": "read_source", "target_id": cached_target["id"]}
            else:
                targets = [
                    t for t in targets if self.cached("read_policy_source", t["request"]) is None
                ]
                gaps = reference_gaps(state)
                may_supplement = (
                    bool(arguments.get("_allow_gap_search"))
                    and len({r["version_id"] for r in rows}) == 1
                    and spent["supplements"] < 1
                )
                if not targets and not may_supplement:
                    reason = (
                        "no_authorized_target"
                        if any(gaps.values())
                        else "reference_coverage_handoff"
                    )
                    break
                with self.database.sessions() as session:
                    usage = session.get(RunUsage, self.run_id)
                    if usage.model_attempts >= self.settings.return_model_limit:
                        reason = "retrieval_model_budget_exhausted"
                        break
                versions = sorted({r["version_id"] for r in rows})
                actions = ["read_source"] if targets else []
                if len(versions) == 1 and spent["supplements"] < 1:
                    actions.append("supplement")
                actions.append("stop")
                observation = {
                    "allowed_actions": actions,
                    "role": "retrieval_agent",
                    **({"requested_tasks": arguments["_tasks"]} if "_tasks" in arguments else {}),
                    "task": arguments["query"],
                    "binding": {"version_ids": sorted({r["version_id"] for r in rows})},
                    "evidence": [
                        {"source_id": r["id"], "version_id": r["version_id"], "text": r["content"]}
                        for r in rows
                    ],
                    "read_targets": [
                        {
                            "id": t["id"],
                            "section": t["section"],
                            "reason": t["reason"],
                            "offset": t["request"]["offset"],
                            **({"task_id": t.get("task_id")} if "_tasks" in arguments else {}),
                        }
                        for t in targets
                    ],
                    "source_budget": {
                        "reads_remaining": 2 - spent["reads"],
                        "supplement_remaining": 1 - spent["supplements"],
                    },
                    "reference_gaps": gaps,
                    "evidence_fingerprint": fingerprint(rows),
                }
                selected = choose_return_action(
                    self.database,
                    self.run_id,
                    self.settings,
                    observation,
                    limits=self.limits,
                    guard=self.locked_guard,
                )
            if selected.get("action") not in {"read_source", "supplement", "stop"}:
                raise ToolFailure("role_action_forbidden")
            if selected["action"] == "stop":
                reason = "retrieval_agent_stopped"
                break
            if selected["action"] == "supplement":
                versions = sorted({r["version_id"] for r in rows})
                if len(versions) != 1:
                    raise ToolFailure("role_ambiguous_version")
                if supplement_args is None:
                    raise ToolFailure("role_ambiguous_version")
                extra = self.tool("search_knowledge_base", supplement_args)
                checked = self.current(extra.get("matches", []), audience)
                rows = list({r["id"]: r for r in [*rows, *checked]}.values())
                state["retrieved_evidence"] = rows
                state["source_targets"] = list(
                    {t["id"]: t for t in [*state["source_targets"], *search_targets(rows)]}.values()
                )[:8]
                continue
            target = next(
                (t for t in state["source_targets"] if t["id"] == selected.get("target_id")), None
            )
            if target is None:
                raise ToolFailure("role_invalid_target")
            body = ReadInput.model_validate(target["request"])
            payload = self.tool("read_policy_source", body.model_dump(mode="json"))
            artifact = self.current([reading_evidence(payload, body)], audience)[0]
            rows = list({r["id"]: r for r in [*rows, artifact]}.values())
            state["retrieved_evidence"] = rows
            state["source_attempted_targets"] += [target["id"]]
            state["source_targets"] = next_targets(payload, body, state["source_targets"])
        state["retrieved_evidence"] = self.current(rows, audience)
        trace["reference_gaps"] = reference_gaps(state)
        trace["collaborative_reading"] = {
            **self.spent(),
            "stop_reason": reason,
            "observed_chars": sum(
                len(r["content"])
                for r in state["retrieved_evidence"]
                if r.get("source_kind") == "source_read"
            ),
            "readings": [
                {
                    "parent_id": r.get("parent_id"),
                    "offset": r.get("offset"),
                    "chars": len(r["content"]),
                }
                for r in state["retrieved_evidence"]
                if r.get("source_kind") == "source_read"
            ],
        }
        return state["retrieved_evidence"], trace, reason


def run_collaboration(
    database,
    run_id,
    settings,
    arguments,
    initial,
    gateway,
    token,
    limits,
    guard,
    progress=None,
    *,
    execution_guard=None,
    context_options=None,
):
    from workbench.runs import event

    context_options = context_options or {}
    task_mode = settings.knowledge_collaboration_version == "task_board_v2"
    active_agent = "supervisor"
    last_payload = initial
    last_task_board = {}

    def announce(agent, kind, **fields):
        nonlocal active_agent
        if kind == "agent.started":
            active_agent = agent
        guard()
        with database.sessions() as session:
            run = session.get(Run, run_id)
            if run.status != "running":
                raise ToolFailure("cancelled")
            event(session, run, kind, agent=agent, **fields)
            session.commit()

    reader = RetrievalRole(
        database, run_id, settings, gateway, token, limits, guard, execution_guard, announce
    )
    initial_context = question_context(
        arguments["query"],
        initial.get("matches", []),
        audience=arguments.get("audience"),
        retrieval=initial.get("trace", {}),
        **context_options,
    )
    if arguments.get("task_frame"):
        initial_context["task"] = arguments["task_frame"]
    engine = AnswerSession(database, run_id, settings, initial_context, limits, guard, progress)
    if not settings.return_source_reading_enabled or settings.return_decision_mode != "model":
        from workbench.answer_gate import produce_answer

        result = produce_answer(
            database, run_id, settings, initial_context, limits, guard, progress
        )
        if result:
            result["answer_gate"]["agent_mode"] = "single_server_fallback"
        return initial, result
    digest = fingerprint(
        {
            "query": arguments["query"],
            "audience": arguments.get("audience"),
            "latest": initial_context.get("latest_conditions"),
            "tools": initial_context.get("tools"),
            "task": initial_context.get("task"),
            "constraints": initial_context.get("task_constraints"),
        }
    )

    def load(state):
        guard()
        if state.get("input_digest") != digest:
            raise ToolFailure("role_handoff_changed")
        if "context" not in state:
            return
        engine.context = state["context"]
        engine.repair_attempted = state.get("repair_attempted", False)
        if state.get("handoff"):
            handoff = EvidenceHandoff.model_validate(state["handoff"])
            if (
                handoff.context_digest != fingerprint(state["context"])
                or set(handoff.source_ids) != {r["id"] for r in state["matches"]}
                or set(handoff.version_ids) != {r["version_id"] for r in state["matches"]}
            ):
                raise ToolFailure("role_handoff_invalid")
            if task_mode and state.get("annotation_digest") != fingerprint(
                annotate(state["matches"])
            ):
                raise ToolFailure("role_annotations_changed")
            if (
                task_mode
                and state.get("task_board")
                and state["context"].get("policy_task_board") != state["task_board"]
            ):
                raise ToolFailure("role_task_board_changed")
            checked = {
                r["id"]: r for r in reader.current(state["matches"], arguments.get("audience"))
            }
            for item in state["context"]["allowed_evidence"]:
                row = checked.get(item["id"])
                if (
                    row is None
                    or item["version_id"] != row["version_id"]
                    or item["text"] != row["content"]
                    or item.get("audience", {}) != row.get("audience", {})
                    or item.get("section", "") != row.get("section", "")
                    or item.get("text_origin", "parsed_original")
                    != row.get("text_origin", "parsed_original")
                ):
                    raise ToolFailure("role_context_not_current_evidence")

    def annotate(matches):
        from workbench.policy_team import current_annotations

        with database.sessions() as session:
            actor, scopes = verify_grant(session, token, settings)
            if "knowledge:read" not in scopes:
                raise ToolFailure("forbidden")
            return current_annotations(session, actor, matches)

    def planning(state):
        from workbench.policy_team import plan_question, reading_targets

        announce("task_planner", "agent.started")
        matches = reader.current(initial.get("matches", []), arguments.get("audience"))
        tasks = plan_question(arguments["query"])
        targets = reading_targets(tasks, matches, annotate(matches))
        announce("task_planner", "agent.handoff", task_count=len(tasks))
        return {
            "tasks": tasks,
            "suggested_targets": targets,
            "retrieval_passes": 0,
            "input_digest": digest,
            "next_step": "retrieval_agent",
        }

    def audit_evidence(state):
        nonlocal last_task_board
        from workbench.policy_team import board, compact_board, reading_targets

        load(state)
        announce("evidence_coordinator", "agent.started")
        matches = reader.current(state["matches"], arguments.get("audience"))
        annotations = annotate(matches)
        value = board(
            state["tasks"], matches, annotations, state["trace"].get("reference_gaps", {})
        )
        targets = reading_targets(state["tasks"], matches, annotations)
        can_read = bool(targets) and reader.spent()["reads"] < 2
        can_supplement = (
            reader.spent()["supplements"] < 1 and len({r["version_id"] for r in matches}) == 1
        )
        missing = any(t["status"] != "candidate_found" for t in value["tasks"])
        back = (
            state["retrieval_passes"] < 3
            and (can_read or (missing and can_supplement))
            and (missing or any(value["reference_gaps"].values()))
        )
        value["next_step"] = (
            "targeted_read"
            if back
            else "answer_with_boundary"
            if value["unresolved"]
            else "answer_and_review"
        )
        allowed_ids = {e["id"] for e in state["context"]["allowed_evidence"]}
        for item in value["tasks"]:
            mapped = []
            for source_id in item["evidence_ids"]:
                if source_id in allowed_ids:
                    mapped.append(source_id)
                else:
                    mapped.extend(
                        r["id"]
                        for r in matches
                        if r["id"] in allowed_ids and source_id in r.get("covered_chunk_ids", [])
                    )
            item["evidence_ids"] = list(dict.fromkeys(mapped))
        value["stop_reason"] = (
            "scope_difference"
            if value["scope_conflicts"]
            else "retrieval_pass_limit"
            if value["unresolved"] and state["retrieval_passes"] >= 3
            else "source_budget_exhausted"
            if value["unresolved"] and not can_read and not can_supplement
            else "no_more_authorized_source"
            if value["unresolved"] and not back
            else "handoff_for_review"
        )
        public = compact_board(value)
        last_task_board = public
        announce("evidence_coordinator", "agent.task_board", board=public)
        context = dict(state["context"])
        context["policy_task_board"] = public
        if value["unresolved"]:
            context["requires_partial_boundary"] = True
        handoff = {**state["handoff"], "context_digest": fingerprint(context)}
        return {
            "task_board": public,
            "context": context,
            "handoff": handoff,
            "suggested_targets": targets,
            "next_step": "retrieval_agent" if back else "answer_agent",
        }

    def retrieve(state):
        nonlocal last_payload
        announce("retrieval_agent", "agent.started")
        selected_arguments = dict(arguments)
        source_payload = initial
        if task_mode:
            selected_arguments.update(
                _authorized_targets=state.get("suggested_targets", []),
                _max_io_steps=1,
                _tasks=state["tasks"],
            )
            if state.get("matches") is not None:
                source_payload = {**initial, "matches": state["matches"], "trace": state["trace"]}
        if task_mode:
            from workbench.policy_team import board

            preliminary = board(
                state["tasks"],
                source_payload.get("matches", []),
                annotate(source_payload.get("matches", [])),
                {},
            )
            selected_arguments["_allow_gap_search"] = any(
                t["status"] != "candidate_found" for t in preliminary["tasks"]
            )
        matches, trace, reason = reader.run(selected_arguments, source_payload)
        context = question_context(
            arguments["query"],
            matches,
            audience=arguments.get("audience"),
            retrieval=trace,
            **context_options,
        )
        if arguments.get("task_frame"):
            context["task"] = arguments["task_frame"]
        last_payload = {**initial, "matches": matches, "trace": trace}
        spent = reader.spent()
        handoff = EvidenceHandoff(
            source_ids=[r["id"] for r in matches],
            version_ids=sorted({r["version_id"] for r in matches}),
            context_digest=fingerprint(context),
            reads=spent["reads"],
            supplements=spent["supplements"],
            stop_reason=reason,
        )
        announce(
            "retrieval_agent", "agent.handoff", source_count=len(matches), reads=spent["reads"]
        )
        return {
            "matches": matches,
            "trace": trace,
            "context": context,
            "handoff": handoff.model_dump(),
            "input_digest": digest,
            "next_step": "evidence_coordinator" if task_mode else "answer_agent",
            "retrieval_passes": state.get("retrieval_passes", 0) + 1,
            **(
                {"task_board": {}, "annotation_digest": fingerprint(annotate(matches))}
                if task_mode
                else {}
            ),
        }

    def answer(state):
        load(state)
        announce("answer_agent", "agent.started")
        if not state["context"]["allowed_evidence"]:
            return {"result": engine.fallback("no_evidence"), "next_step": "finish"}
        raw = engine.call("generate")["raw"]
        try:
            draft = validate_draft(raw, engine.context)
        except DraftContractIssue as issue:
            parsed = parse_draft(raw, engine.context)
            return {
                "draft": parsed.model_dump(),
                "codes": [issue.code],
                "check_kind": "program",
                "next_step": "supervisor",
            }
        announce("answer_agent", "agent.handoff")
        return {"draft": draft.model_dump(), "next_step": "review_agent"}

    def review(state):
        load(state)
        announce("review_agent", "agent.started")
        draft = validate_draft(state["draft"], engine.context)
        decision = engine.check(draft, repaired=state.get("repair_attempted", False))
        announce("review_agent", "agent.handoff", approved=decision.approved, codes=decision.codes)
        if decision.approved:
            return {
                "result": engine.published(draft, repaired=state.get("repair_attempted", False)),
                "next_step": "finish",
            }
        return {"codes": decision.codes, "check_kind": "semantic", "next_step": "supervisor"}

    def supervise(state):
        announce("supervisor", "agent.started")
        load(state)
        if state.get("repair_attempted"):
            return {
                "result": engine.fallback("repair_rejected", state["codes"]),
                "next_step": "finish",
            }
        if settings.policy_answer_mode != "reviewed_repair":
            return {"result": engine.fallback("blocked", state["codes"]), "next_step": "finish"}
        if not engine.repair_usable():
            return {
                "result": engine.fallback("blocked_evidence_gap", state["codes"]),
                "next_step": "finish",
            }
        return {"next_step": "repair_agent"}

    def repair(state):
        load(state)
        announce("repair_agent", "agent.started")
        draft = engine.repair_generation(
            parse_draft(state["draft"], engine.context), state["codes"], state["check_kind"]
        )
        announce("repair_agent", "agent.handoff")
        return {"draft": draft.model_dump(), "repair_attempted": True, "next_step": "review_agent"}

    graph = StateGraph(CrewState)
    nodes = {
        "retrieval_agent": retrieve,
        "answer_agent": answer,
        "review_agent": review,
        "supervisor": supervise,
        "repair_agent": repair,
    }
    if task_mode:
        nodes.update(task_planner=planning, evidence_coordinator=audit_evidence)
    for name, node in nodes.items():
        from workbench.tracing import trace_node

        graph.add_node(name, trace_node(node, "knowledge_" + name))
        graph.add_conditional_edges(
            name, lambda s: END if s["next_step"] == "finish" else s["next_step"]
        )
    graph.add_edge(START, "task_planner" if task_mode else "retrieval_agent")
    saver = SQLCheckpointSaver(
        database,
        run_id,
        execution_guard=execution_guard,
        namespace="knowledge_roles_v2" if task_mode else "knowledge_roles_v1",
    )
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": saver.thread_scope}}
    try:
        snapshot = compiled.get_state(config)
        if snapshot.values:
            load(snapshot.values)
        if snapshot.values and not snapshot.next:
            final = snapshot.values
        else:
            final = compiled.invoke(
                None if snapshot.values else {"input_digest": digest, "repair_attempted": False},
                config,
            )
        load(final)
        result = final["result"]
        result["answer_gate"]["agent_mode"] = "collaborative"
        if task_mode:
            dispositions = {}
            if result["answer_gate"]["status"] == "approved":
                dispositions = {
                    item["task_id"]: item["status"]
                    for item in final.get("draft", {}).get("task_answers", [])
                }
            public_board = dict(final.get("task_board", {}))
            public_board["tasks"] = [
                {**t, "answer_status": dispositions.get(t["id"], "not_published")}
                for t in public_board.get("tasks", [])
            ]
            result["answer_gate"]["collaboration"] = public_board
            result["answer_gate"]["collaboration_version"] = "task_board_v2"
        result["answer_gate"]["handoff_digest"] = final.get("handoff", {}).get("context_digest")
        return {**initial, "matches": final["matches"], "trace": final["trace"]}, result
    except ToolFailure as exc:
        if exc.code in {"cancelled", "worker_lease_lost", "forbidden", "unauthorized"}:
            raise
        announce(active_agent, "agent.failed", error_code=exc.code)
        result = engine.fallback("collaboration_unavailable")
        if task_mode:
            result["answer_gate"]["collaboration"] = last_task_board
            result["answer_gate"]["failed_role"] = active_agent
        return last_payload, result
    except Exception:
        announce(active_agent, "agent.failed", error_code="role_failed")
        result = engine.fallback("collaboration_invalid")
        if task_mode:
            result["answer_gate"]["collaboration"] = last_task_board
            result["answer_gate"]["failed_role"] = active_agent
        return last_payload, result
