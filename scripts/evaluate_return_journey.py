"""Frozen return questions through API -> LangGraph -> actual MCP/RAG -> answer gate.

Run only in a newly isolated commercedesk_journey_* database. Provider key is read
from stdin, never an environment variable, command argument, SQL or artifact.
"""

import argparse
import hashlib
import json
import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.models import Order, OrderItem, Organization, now


def load(dataset, identifiers=None):
    manifest = json.loads((dataset / "manifest.json").read_text("utf-8"))
    if (
        hashlib.sha256((dataset / "cases.json").read_bytes()).hexdigest()
        != manifest["cases_sha256"]
    ):
        raise ValueError("Frozen cases changed")
    if (
        hashlib.sha256(
            (Path("/app/eval-protocol") / manifest.get("protocol_file", "protocol.md")).read_bytes()
        ).hexdigest()
        != manifest["protocol_sha256"]
    ):
        raise ValueError("Protocol changed")
    for file, sha in manifest["workbench_files"].items():
        if hashlib.sha256((Path("/app") / file).read_bytes()).hexdigest() != sha:
            raise ValueError("Production baseline changed: " + file)
    data = json.loads((dataset / "cases.json").read_text("utf-8"))
    known = {c["id"] for c in data["cases"]}
    if identifiers and (len(identifiers) != len(set(identifiers)) or set(identifiers) - known):
        raise ValueError("Unknown or duplicate case ID")
    data["cases"] = [c for c in data["cases"] if not identifiers or c["id"] in identifiers]
    return data, manifest


def document_text(policy, case):
    if case["layout"] == "spread_four":
        paragraphs = []
        for key in ("scope", "quality_window", "quality_condition", "quality_proof"):
            value = policy[key]
            padding = ("背景说明不增加业务条件。" * 30)[: 235 - len(value)]
            paragraphs.append(value + padding)
        return "# 本店质量退回章节\n\n" + ("\n\n" + "阅读背景不增加条件。" * 29 + "\n\n").join(
            paragraphs
        )
    if case["layout"] == "sections":
        return "\n\n".join(
            "# " + title + "\n\n" + policy[key]
            for title, key in [
                ("适用商品", "scope"),
                ("无理由时限", "window"),
                ("商品状态", "condition"),
                ("质量时限", "quality_window"),
                ("缺陷确认", "quality_condition"),
            ]
        )
    count = {"one_long": 50, "late_reachable": 110, "late_unreachable": 240}[case["layout"]]
    padding = "资料管理背景说明，不增加退回资格条件。" * count
    return (
        "# 本店退回办法\n\n"
        + policy["scope"]
        + "\n\n"
        + policy["window"]
        + "\n\n"
        + padding
        + "\n\n"
        + policy["condition"]
        + "\n\n"
        + policy["quality_window"]
        + "\n\n"
        + policy["quality_condition"]
    )


def model_adapters(settings, budget, offline):
    import workbench.answer_gate as gate
    import workbench.return_decisions as decisions
    from workbench.answer_contract import messages
    from workbench.context_management import ContextOverflow, pack_messages

    original_gate, original_decision = gate.model_call, decisions.model_decision

    def generation(config, phase, context, draft=None, **kwargs):
        if offline:
            raw = (
                {
                    "claims": [
                        {
                            "text": context["allowed_evidence"][0]["text"],
                            "evidence_ids": [context["allowed_evidence"][0]["id"]],
                        }
                    ]
                }
                if phase in {"generate", "repair"} and context["allowed_evidence"]
                else {"cannot_confirm": True}
                if phase in {"generate", "repair"}
                else {"approved": True, "codes": []}
            )
            return {
                "raw": json.dumps(raw, ensure_ascii=False),
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                "response_model": "offline-schema-substitute",
            }
        try:
            packed, _ = pack_messages(
                config,
                messages(phase, context, draft),
                output_tokens=config.policy_answer_max_tokens,
            )
        except ContextOverflow as exc:
            raise gate.GateNotSent(str(exc)) from exc
        return budget.call(
            phase,
            packed,
            config.policy_answer_max_tokens,
            lambda: original_gate(config, phase, context, draft, **kwargs),
        )

    def decision(config, observation, **kwargs):
        if offline:
            action = observation["allowed_actions"][0]
            proposal = {"action": action}
            if action == "read_source":
                proposal["target_id"] = observation["read_targets"][0]["id"]
            return {
                "proposal": json.dumps(proposal),
                "usage": {"prompt_tokens": 50, "completion_tokens": 8, "total_tokens": 58},
                "model": "offline-schema-substitute",
            }
        body = decisions.request_body(config, observation)
        return budget.call(
            "decision",
            body["messages"],
            config.agent_router_max_output_tokens,
            lambda: original_decision(config, observation, **kwargs),
        )

    gate.model_call = generation
    decisions.model_decision = decision


def auth(client, password, role="support"):
    result = client.post(
        "/api/v1/auth/login",
        json={"organization": "demo-a", "email": role + "@demo.test", "password": password},
    )
    result.raise_for_status()
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def seed_case(client, db, index, settings, headers, policy, case, tag):
    admin = headers["admin"]
    support = headers["support"]
    sku = "AUDIT-" + case["id"].upper()
    terms = {k: v.format(sku=sku) for k, v in policy.items()}
    text = document_text(terms, case)
    reply = client.post(
        "/api/v1/knowledge/documents",
        headers=admin,
        data={
            "title": "独立退回任务 " + case["id"],
            "policy_scope": "returns",
            "chunk_strategy": "parent_child",
            "chunk_chars": "240",
            "chunk_overlap": "20",
        },
        files={"file": ("audit.md", text.encode(), "text/markdown")},
    )
    reply.raise_for_status()
    version = reply.json()["version"]["id"]
    from workbench.ingestion import work_once
    from workbench.knowledge_models import KnowledgeChunk

    while True:
        state = client.get("/api/v1/knowledge/versions/" + version, headers=admin).json()
        if state["status"] in {"ready", "failed"}:
            break
        if not work_once(db, settings, index):
            raise RuntimeError("Ingestion queue unexpectedly empty")
    if state["status"] != "ready":
        raise RuntimeError("Fixture ingestion failed")
    client.post(
        "/api/v1/knowledge/versions/" + version + "/publish", headers=admin, json={}
    ).raise_for_status()
    with db.sessions() as session:
        chunks = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == version)
        ).all()

        def ref(key):
            row = next((r for r in chunks if terms[key] in r.content), None)
            if row is None:
                raise RuntimeError("Gold clause split across chunks: " + key)
            return {"chunk_id": row.id, "quote": terms[key]}

        org = session.scalar(select(Organization).where(Organization.slug == "demo-a"))
        order = Order(
            organization_id=org.id,
            number="JOURNEY-" + case["id"].split("-")[-1] + "-" + tag,
            customer_name="isolated-generated-not-for-model",
            payment_status="paid",
            shipping_status="delivered",
            signed_at=now() - timedelta(days=case["signed_days"])
            if case["signed_days"] is not None
            else None,
            estimated_delivery="人工隔离预计信息，不是签收事实",
            data_source="journey_audit_generated",
        )
        session.add(order)
        session.flush()
        item = OrderItem(
            organization_id=org.id,
            order_id=order.id,
            sku=sku,
            product_name="隔离退回样品",
            quantity=4,
            unit_price_cents=8900,
        )
        session.add(item)
        session.commit()
        number, item_id = order.number, item.id
        quality = case["basis"] == "quality_issue"
        body = {
            "version_id": version,
            "skus": [sku],
            "window_days": 27 if quality else 9,
            "requires_unused": not quality,
            "return_basis": case["basis"],
            "administrator_verified": True,
            "scope_clause": ref("scope"),
            "window_clause": ref("quality_window" if quality else "window"),
            "condition_clause": ref("quality_condition" if quality else "condition"),
        }
        if quality:
            body.update(
                required_facts=["quality_verdict"],
                quality_requirement="defect_confirmed",
                quality_clause=ref(
                    "quality_proof" if "quality_proof" in terms else "quality_condition"
                ),
            )
    registered = client.post("/api/v1/returns/policies", headers=admin, json=body)
    registered.raise_for_status()
    if case.get("quality"):
        inspection = client.post(
            "/api/v1/quality/inspections",
            headers={**support, "Idempotency-Key": uuid4().hex},
            json={
                "order_number": number,
                "item_id": item_id,
                "quantity": 1,
                "reason": "独立题集人工生成的演示缺陷记录",
            },
        )
        inspection.raise_for_status()
        row = inspection.json()
        for action, extra in [
            ("start", {}),
            ("complete", {"verdict": "failed", "finding": "defect_confirmed"}),
        ]:
            changed = client.post(
                "/api/v1/quality/inspections/" + row["id"] + "/transition",
                headers=headers["approver"],
                json={
                    "action": action,
                    "expected_revision": row["revision"],
                    "note": "人工编写的隔离演示记录，不代表真实商品质检",
                    **extra,
                },
            )
            changed.raise_for_status()
            row = changed.json()
    return {
        "number": number,
        "item_id": item_id,
        "policy_id": registered.json()["id"],
        "terms": terms,
        "version_id": version,
    }


def score(case, fixture, turns):
    needed = [fixture["terms"][k] for k in case["needed"]]
    final = turns[-1]
    output = final["run"].get("output") or {}
    texts = []
    waste = []
    read_windows = set()
    repeated = 0
    noncontributing = 0
    for turn in turns:
        for call in turn["tools"]:
            name = call["name"]
            result = call.get("result") or {}
            if name == "search_knowledge_base":
                texts.extend(m["content"] for m in result.get("matches", []))
            if name == "read_policy_source" and result.get("content"):
                before = {q for q in needed if any(q in t for t in texts)}
                if set(needed) <= before:
                    waste.append(
                        {"turn": turn["turn"], "reason": "needed_evidence_already_present"}
                    )
                key = (
                    result["version_id"],
                    result["parent_id"],
                    result["offset"],
                    result["content"],
                )
                if key in read_windows:
                    repeated += 1
                read_windows.add(key)
                after = {q for q in needed if q in result["content"]}
                if not after - before:
                    noncontributing += 1
                texts.append(result["content"])
    final_texts = []
    for call in final["tools"]:
        result = call.get("result") or {}
        if call["name"] == "search_knowledge_base":
            final_texts.extend(m["content"] for m in result.get("matches", []))
        elif call["name"] == "read_policy_source" and result.get("content"):
            final_texts.append(result["content"])
    # Correction invalidates the prior turn's evidence in this frozen implementation.
    # Keep earlier evidence for repeat-work diagnosis, not current-answer coverage.
    covered = [q for q in needed if any(q in t for t in final_texts)]
    seen_before = [q for q in needed if any(q in t for t in texts)]
    info = case["task"] == "information"
    ask = output.get("missing") or []
    unneeded_ask = info and final["run"]["status"] == "awaiting_input"
    known_ask = case["condition"] != "unknown" and "return_condition" in ask
    expected = case.get("continuation_expect") if len(turns) > 1 else case["expected_status"]
    status_ok = final["run"]["status"] == expected
    if case.get("expected_pause") and final["run"]["status"] == "awaiting_input":
        status_ok = status_ok and output.get("pause_kind") == case["expected_pause"]
    wrong_preview = info and final["run"]["status"] == "awaiting_confirmation"
    gate = output.get("answer_gate") or {}
    if info:
        supported_fact = not needed and bool((output.get("fact_assessment") or {}).get("items"))
        answered = bool(gate.get("status") == "approved" or supported_fact)
    else:
        answered = True
    if case.get("continuation") and len(turns) == 1:
        status_ok = False
    return {
        "scorer_version": 2,
        "ever_seen_quotes": len(seen_before),
        "necessary_quotes": len(needed),
        "covered_quotes": len(covered),
        "missing_quotes": [q for q in needed if q not in covered],
        "question_evidence_complete": len(needed) == len(covered),
        "unnecessary_reads": waste,
        "noncontributing_read_calls": noncontributing,
        "repeated_windows": repeated,
        "unnecessary_pause": bool(unneeded_ask or known_ask),
        "wrong_application_preview": wrong_preview,
        "target_state_reached": status_ok,
        "answer_published_or_fact_rendered": answered,
        "target_achieved": status_ok
        and not wrong_preview
        and not unneeded_ask
        and answered
        and len(covered) == len(needed),
        "semantic_answer_review": "author_pending",
        "reachable_missed_read": "requires_trace_review" if len(covered) < len(needed) else False,
        "gate_status": gate.get("status"),
        "pause_kind": output.get("pause_kind"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "offline", "paid"])
    parser.add_argument("--dataset", type=Path, default=Path("/app/eval-data"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--version", choices=["both", "baseline", "reading"], default="both")
    parser.add_argument(
        "--baseline",
        choices=["same-current-code-reading-off"],
        default="same-current-code-reading-off",
    )
    args = parser.parse_args()
    try:
        data, manifest = load(args.dataset, args.case_id)
    except ValueError as exc:
        parser.error(str(exc))
    if args.output.exists():
        parser.error("Output exists; preserve original results")
    if args.command == "validate":
        print(json.dumps({"cases": len(data["cases"]), "arms": args.version, "paid_calls": 0}))
        return
    args.output.mkdir(parents=True)
    offline = args.command == "offline"
    key = "offline-no-provider-key" if offline else json.loads(input())["api_key"]
    settings = WorkbenchSettings(
        _env_file=None,
        storage_root="/app/data/workbench/files",
        run_dispatch_mode="inline",
        retrieval_context_mode="parent_child",
        context_chars=900,
        context_history_enabled=False,
        agent_router_mode="model",
        return_decision_mode="model",
        return_source_reading_enabled=True,
        policy_answer_mode="reviewed",
        policy_answer_max_tokens=1024,
        policy_answer_timeout_seconds=30,
        agent_router_url="https://api.deepseek.com/chat/completions",
        agent_router_model="deepseek-flash",
        agent_router_key=SecretStr(key),
        mcp_url="http://127.0.0.1:8083/mcp",
        mcp_bind_host="0.0.0.0",
        mcp_warm_models=False,
        mcp_timeout_seconds=30,
        mcp_read_retries=0,
        chroma_url="http://chroma:8000",
    )
    db = Database(settings)
    if not db.engine.url.database.startswith("commercedesk_journey_"):
        raise ValueError("New isolated database required")
    from alembic import command
    from return_journey_budget import AuditBudget
    from sqlalchemy import inspect

    from evaluation.answer_trust_data import write_json
    from workbench.app import create_app
    from workbench.cli import migration_config
    from workbench.knowledge_index import ChromaIndex
    from workbench.mcp_gateway import ToolFailure
    from workbench.mcp_server import create_mcp_app
    from workbench.seed import seed_demo
    from workbench.tool_contracts import CONTRACTS

    if not inspect(db.engine).has_table("alembic_version"):
        command.upgrade(migration_config(settings), "head")
    else:
        db.check_schema()
    password = os.environ["JOURNEY_DEMO_PASSWORD"]
    seed_demo(db, password)
    budget = (
        None
        if offline
        else AuditBudget(
            "/app/work/answer-trust/budget.json",
            args.output,
            allowed_cases={c["id"] for c in data["cases"]},
        )
    )
    model_adapters(settings, budget, offline)
    index = ChromaIndex(settings)
    index.warm()
    server = uvicorn.Server(
        uvicorn.Config(
            create_mcp_app(settings, index=index), host="0.0.0.0", port=8083, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    app = create_app(settings)
    calls = []
    actual = app.state.gateway.call

    async def traced(name, arguments, token):
        item = {"name": name, "arguments": arguments}
        start = time.perf_counter()
        calls.append(item)
        if not CONTRACTS[name][3]:
            item["error"] = "write_blocked"
            raise ToolFailure("evaluation_write_blocked")
        try:
            item["result"] = await actual(name, arguments, token)
            return item["result"]
        except Exception as exc:
            item["error"] = type(exc).__name__
            raise
        finally:
            item["elapsed_ms"] = (time.perf_counter() - start) * 1000

    app.state.gateway.call = traced
    records = []
    tag = uuid4().hex[:8].upper()
    revision = 0
    write_json(
        args.output / "manifest.json",
        {
            "dataset": manifest,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "offline_model_substitute": offline,
            "model": "deepseek-flash",
            "context_chars": 900,
            "top_k": 2,
            "provider_key_saved": False,
        },
    )
    try:
        with TestClient(app) as client:
            headers = {
                role: auth(client, password, role) for role in ["admin", "support", "approver"]
            }
            revision = client.get("/api/v1/runtime/config", headers=headers["admin"]).json()[
                "revision"
            ]
            for position, case in enumerate(data["cases"]):
                fixture = seed_case(client, db, index, settings, headers, data["policy"], case, tag)
                arms = ["baseline", "reading"] if position % 2 == 0 else ["reading", "baseline"]
                if args.version != "both":
                    arms = [args.version]
                for arm in arms:
                    if budget and budget.stopped:
                        break
                    profile = client.post(
                        "/api/v1/runtime/config/versions",
                        headers=headers["admin"],
                        json={
                            "model_alias": "server_default",
                            "return_decisions": "model",
                            "return_retrieval_mode": "clause_bound",
                            "return_source_reading": arm == "reading",
                            "policy_answer_mode": "reviewed",
                            "top_k": 2,
                        },
                    )
                    profile.raise_for_status()
                    client.post(
                        "/api/v1/runtime/config/versions/" + profile.json()["id"] + "/publish",
                        headers=headers["admin"],
                        json={"expected_revision": revision},
                    ).raise_for_status()
                    revision += 1
                    if budget:
                        budget.current = {"case_id": case["id"], "arm": arm, "turn": 0}
                    body = {
                        "kind": "aftersales",
                        "query": case["question"],
                        "order_number": fixture["number"],
                        "return_item_id": fixture["item_id"],
                        "return_policy_id": fixture["policy_id"],
                        "return_quantity": 1,
                        "return_condition": case["condition"],
                        "return_basis": case["basis"],
                        "reason": "新编隔离任务：" + case["id"],
                    }
                    cursor = len(calls)
                    started = time.perf_counter()
                    result = client.post("/api/v1/runs", headers=headers["support"], json=body)
                    result.raise_for_status()
                    run = client.get(
                        "/api/v1/runs/" + result.json()["id"], headers=headers["support"]
                    ).json()
                    turns = [
                        {
                            "turn": 0,
                            "run": run,
                            "tools": calls[cursor:],
                            "elapsed_ms": (time.perf_counter() - started) * 1000,
                        }
                    ]
                    if (
                        case.get("continuation")
                        and run["status"] == "awaiting_confirmation"
                        and not (budget and budget.stopped)
                    ):
                        if budget:
                            budget.current = {"case_id": case["id"], "arm": arm, "turn": 1}
                        cursor = len(calls)
                        started = time.perf_counter()
                        response = client.post(
                            "/api/v1/runs/" + run["id"] + "/resume",
                            headers={**headers["support"], "Idempotency-Key": uuid4().hex},
                            json={
                                "action": "correct",
                                "pause_id": run["output"]["pause_id"],
                                **case["continuation"],
                            },
                        )
                        response.raise_for_status()
                        run = client.get(
                            "/api/v1/runs/" + run["id"], headers=headers["support"]
                        ).json()
                        turns.append(
                            {
                                "turn": 1,
                                "run": run,
                                "tools": calls[cursor:],
                                "elapsed_ms": (time.perf_counter() - started) * 1000,
                            }
                        )
                    usage = client.get(
                        "/api/v1/runs/" + run["id"] + "/usage", headers=headers["support"]
                    ).json()
                    events = client.get(
                        "/api/v1/runs/" + run["id"] + "/events", headers=headers["support"]
                    ).text
                    row = {
                        "case_id": case["id"],
                        "family": case["family"],
                        "arm": arm,
                        "question": case["question"],
                        "oracle": case,
                        "fixture": fixture,
                        "turns": turns,
                        "usage": usage,
                        "events": events,
                        "score": score(case, fixture, turns),
                    }
                    records.append(row)
                    with (args.output / "runs.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    write_json(
                        args.output / "progress.json",
                        {
                            "runs": len(records),
                            "last": case["id"] + ":" + arm,
                            "budget": budget.summary() if budget else {"requests": 0},
                        },
                    )
                if budget and budget.stopped:
                    break
                if position == 1 and budget:
                    write_json(
                        args.output / "pilot-cost.json",
                        {
                            "cases": 2,
                            "budget": budget.summary(),
                            "linear_projection_cny": budget.summary()["charged_cny"]
                            * len(data["cases"])
                            / 2,
                            "parameters_changed": False,
                        },
                    )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        summary = {
            "runs": len(records),
            "cases": len({r["case_id"] for r in records}),
            "paid": not offline,
            "budget": budget.summary() if budget else {"requests": 0},
            "arms": {},
        }
        for arm in ["baseline", "reading"]:
            items = [r for r in records if r["arm"] == arm]
            summary["arms"][arm] = {
                "runs": len(items),
                "target_achieved": sum(r["score"]["target_achieved"] for r in items),
                "evidence_complete": sum(r["score"]["question_evidence_complete"] for r in items),
                "unnecessary_pause": sum(r["score"]["unnecessary_pause"] for r in items),
                "wrong_application_preview": sum(
                    r["score"]["wrong_application_preview"] for r in items
                ),
                "unnecessary_reads": sum(len(r["score"]["unnecessary_reads"]) for r in items),
                "repeated_windows": sum(r["score"]["repeated_windows"] for r in items),
            }
        write_json(args.output / "summary.json", summary)
        print(json.dumps(summary))
        db.close()


if __name__ == "__main__":
    main()
