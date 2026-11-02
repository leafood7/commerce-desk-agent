"""Frozen-family inquiry check; isolated SQL/real MCP/RAG, optional authorized model."""

import argparse
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr

from workbench.config import WorkbenchSettings
from workbench.database import Database


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def covered(texts, needed):
    return [quote for quote in needed if any(quote in text for text in texts)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["offline", "paid"])
    parser.add_argument("--arm", choices=["baseline_frozen", "candidate_v2"], required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--answer-mode",
        choices=["contract", "reviewed", "reviewed_repair", "all"],
        default="reviewed",
    )
    parser.add_argument(
        "--agent-mode", choices=["single", "collaborative", "both"], default="single"
    )
    parser.add_argument("--question-type", choices=["intent", "rule", "both"], default="both")
    args = parser.parse_args()
    if args.arm == "baseline_frozen" and args.answer_mode != "reviewed":
        parser.error("The frozen baseline supports its original review mode only")
    freeze = json.loads((args.dataset / "candidate-freeze.json").read_text("utf-8"))
    raw = args.dataset / "new-family-final.json"
    if hashlib.sha256(raw.read_bytes()).hexdigest() != freeze["final_sha256"]:
        parser.error("Frozen final questions changed")
    import workbench

    source = Path(workbench.__file__).parent
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.py")}
    if actual != freeze["source_hashes"][args.arm]:
        parser.error("Code differs from preregistered arm")
    families = json.loads(raw.read_text("utf-8"))
    cases = []
    for family in families:
        for kind in ("intent", "rule") if args.question_type == "both" else (args.question_type,):
            question = (
                family["question"]
                if kind == "rule"
                else family["question"].split("具体", 1)[0] + "这个情况帮我看看。"
            )
            cases.append(
                {
                    **family,
                    "id": family["id"] + "-" + kind,
                    "question": question,
                    "question_type": kind,
                }
            )
    known = {c["id"] for c in cases}
    if args.case_id and (len(set(args.case_id)) != len(args.case_id) or set(args.case_id) - known):
        parser.error("Unknown or duplicate case ID")
    cases = [c for c in cases if not args.case_id or c["id"] in args.case_id]
    expanded = []
    for position, case in enumerate(cases):
        modes = (
            ["contract", "reviewed", "reviewed_repair"]
            if args.answer_mode == "all"
            else [args.answer_mode]
        )
        if position % 2:
            modes = list(reversed(modes))
        agents = ["single", "collaborative"] if args.agent_mode == "both" else [args.agent_mode]
        if position % 2:
            agents.reverse()
        expanded.extend(
            {**case, "answer_mode": mode, "agent_mode": agent} for mode in modes for agent in agents
        )
    cases = expanded
    if args.output.exists():
        parser.error("Preserve prior results; output already exists")
    args.output.mkdir(parents=True)
    offline = args.mode == "offline"
    key = "offline-no-provider-key" if offline else json.loads(input())["api_key"]
    # Frozen source is imported from an archive directory, while immutable demo
    # resources and the existing offline model cache remain at the original app root.
    # This changes resource lookup only, not any frozen product source file.
    import workbench.config as resource_config

    resource_config.ROOT = Path("/app")
    settings = WorkbenchSettings(
        _env_file=None,
        storage_root="/app/data/workbench/files",
        run_dispatch_mode="inline",
        retrieval_context_mode="parent_child",
        context_chars=350,
        context_history_enabled=False,
        agent_router_mode="model",
        return_decision_mode="model",
        return_source_reading_enabled=True,
        policy_answer_mode="reviewed_repair"
        if args.answer_mode in {"all", "reviewed_repair"}
        else "reviewed",
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
    if not str(db.engine.url.database).startswith("commercedesk_journey_"):
        raise ValueError("Dedicated isolated database required")
    db.check_schema()
    import evaluate_return_journey as helper
    from return_journey_budget import AuditBudget
    from sqlalchemy import func, select

    from workbench.app import create_app
    from workbench.knowledge_index import ChromaIndex
    from workbench.mcp_gateway import ToolFailure
    from workbench.mcp_server import create_mcp_app
    from workbench.return_models import ReturnRequest
    from workbench.seed import seed_demo
    from workbench.tool_contracts import CONTRACTS

    password = os.environ["JOURNEY_DEMO_PASSWORD"]
    seed_demo(db, password)
    budget = (
        None
        if offline
        else AuditBudget(
            "/app/work/answer-trust/budget.json",
            args.output,
            allowed_cases={c["id"] for c in cases},
            allowed_arms={args.arm},
        )
    )
    helper.model_adapters(settings, budget, offline)
    index = ChromaIndex(settings)
    index.warm()
    server = uvicorn.Server(
        uvicorn.Config(
            create_mcp_app(settings, index=index), host="0.0.0.0", port=8083, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if time.monotonic() >= deadline or not thread.is_alive():
            raise RuntimeError("MCP server startup failed")
        time.sleep(0.05)
    app = create_app(settings)
    calls = []
    actual_tool = app.state.gateway.call

    async def traced(name, arguments, token):
        record = {"name": name, "arguments": arguments}
        calls.append(record)
        if not CONTRACTS[name][3]:
            record["error"] = "write_blocked"
            raise ToolFailure("evaluation_write_blocked")
        start = time.perf_counter()
        try:
            result = await actual_tool(name, arguments, token)
            record["result"] = result
            return result
        except Exception as exc:
            record["error"] = type(exc).__name__
            raise
        finally:
            record["elapsed_ms"] = (time.perf_counter() - start) * 1000

    app.state.gateway.call = traced
    records = []
    fixtures = {}
    write(
        args.output / "manifest.json",
        {
            "arm": args.arm,
            "freeze": freeze,
            "actual_source": actual,
            "offline_model_substitute": offline,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "version_order": "separate_processes_sequential_no_causal_latency_claim",
            "provider_key_saved": False,
        },
    )
    try:
        with TestClient(app) as client:
            headers = {
                role: helper.auth(client, password, role)
                for role in ("admin", "support", "approver")
            }
            head = client.get("/api/v1/runtime/config", headers=headers["admin"]).json()
            flags = {
                "return_flow_version": "guided_v3",
                "return_retrieval_mode": "clause_bound",
                "return_source_reading": True,
                "return_decisions": "model",
                "model_alias": "server_default",
                "policy_answer_mode": cases[0]["answer_mode"],
                "top_k": 1,
            }
            if args.arm == "candidate_v2":
                flags.update(return_task_clarification=True, return_reference_check=True)
                flags["knowledge_agent_mode"] = cases[0]["agent_mode"]
            profile = client.post(
                "/api/v1/runtime/config/versions", headers=headers["admin"], json=flags
            )
            profile.raise_for_status()
            client.post(
                "/api/v1/runtime/config/versions/" + profile.json()["id"] + "/publish",
                headers=headers["admin"],
                json={"expected_revision": head["revision"]},
            ).raise_for_status()
            for case in cases:
                if budget and budget.stopped:
                    break
                if (
                    flags["policy_answer_mode"] != case["answer_mode"]
                    or flags.get("knowledge_agent_mode", "single") != case["agent_mode"]
                ):
                    flags["policy_answer_mode"] = case["answer_mode"]
                    if args.arm == "candidate_v2":
                        flags["knowledge_agent_mode"] = case["agent_mode"]
                    current = client.get("/api/v1/runtime/config", headers=headers["admin"]).json()
                    profile = client.post(
                        "/api/v1/runtime/config/versions", headers=headers["admin"], json=flags
                    )
                    profile.raise_for_status()
                    client.post(
                        "/api/v1/runtime/config/versions/" + profile.json()["id"] + "/publish",
                        headers=headers["admin"],
                        json={"expected_revision": current["revision"]},
                    ).raise_for_status()
                if case["id"] in fixtures:
                    fixture = fixtures[case["id"]]
                else:
                    policy = {
                        "scope": "本演示政策只适用于本店商品 {sku}，其他商品与商家不适用。",
                        "window": "未配置专项时限的标准样品自实际签收后9天内可申请。",
                        "condition": "无理由申请须商品未使用，独立专项条款仍需同时核对。",
                        "quality_window": "",
                        "quality_condition": "",
                    }
                    original_doc = helper.document_text
                    helper.document_text = lambda terms, selected: (
                        "# 第三条 演示核对基础\n\n"
                        + "\n\n".join(terms[k] for k in ("scope", "window", "condition"))
                        + "\n\n"
                        + selected["document"]
                    )
                    fixture_case = {
                        **case,
                        "layout": "sections",
                        "condition": "unused",
                        "basis": "no_reason",
                        "signed_days": 2,
                    }
                    fixture = helper.seed_case(
                        client,
                        db,
                        index,
                        settings,
                        headers,
                        policy,
                        fixture_case,
                        uuid4().hex[:8].upper(),
                    )
                    helper.document_text = original_doc
                    # Match the public input normalization contract for generated orders.
                    from workbench.models import Order

                    normalized_number = fixture["number"].upper()
                    with db.sessions() as session:
                        seeded = session.scalar(
                            select(Order).where(
                                Order.number == fixture["number"],
                                Order.data_source == "journey_audit_generated",
                            )
                        )
                        if seeded is None:
                            raise RuntimeError("Generated fixture order not found")
                        seeded.number = normalized_number
                        session.commit()
                    fixture["number"] = normalized_number
                    fixtures[case["id"]] = fixture
                if budget:
                    budget.current = {
                        "case_id": case["id"],
                        "arm": args.arm,
                        "turn": 0,
                        "protocol_sha256": freeze["protocol_v2_sha256"],
                        "answer_mode": case["answer_mode"],
                        "agent_mode": case["agent_mode"],
                    }
                cursor = len(calls)
                start = time.perf_counter()
                response = client.post(
                    "/api/v1/runs",
                    headers=headers["support"],
                    json={
                        "kind": "aftersales",
                        "query": case["question"],
                        "order_number": fixture["number"],
                        "return_item_id": fixture["item_id"],
                        "return_policy_id": fixture["policy_id"],
                        "return_quantity": 1,
                        "return_condition": "unused",
                        "reason": "隔离新题，仅供诊断",
                    },
                )
                response.raise_for_status()
                run = client.get(
                    "/api/v1/runs/" + response.json()["id"], headers=headers["support"]
                ).json()
                turns = [{"run": run, "tools": list(calls[cursor:])}]
                if run.get("output", {}).get("pause_kind") == "task_clarification":
                    cursor = len(calls)
                    selected = client.post(
                        "/api/v1/runs/" + run["id"] + "/resume",
                        headers={**headers["support"], "Idempotency-Key": uuid4().hex},
                        json={
                            "action": "choose_task",
                            "pause_id": run["output"]["pause_id"],
                            "return_task": "policy",
                        },
                    )
                    selected.raise_for_status()
                    run = client.get("/api/v1/runs/" + run["id"], headers=headers["support"]).json()
                    turns.append({"run": run, "tools": list(calls[cursor:])})
                texts = []
                reading = 0
                for turn in turns:
                    for tool in turn["tools"]:
                        payload = tool.get("result", {})
                        if tool["name"] == "search_knowledge_base":
                            texts.extend(r["content"] for r in payload.get("matches", []))
                        elif tool["name"] == "get_return_policy":
                            texts.extend(
                                r["content"] for r in payload.get("evidence", {}).get("sources", [])
                            )
                        elif tool["name"] == "read_policy_source" and "content" in payload:
                            reading += 1
                            texts.append(payload["content"])
                output = run.get("output") or {}
                present = covered(texts, case["necessary"])
                wrong = run["status"] == "awaiting_confirmation" or bool(output.get("proposal"))
                score = {
                    "target_state": run["status"] == "completed",
                    "necessary_quotes": len(present),
                    "needed_quotes": len(case["necessary"]),
                    "wrong_application_preview": wrong,
                    "unnecessary_pause": run["status"] == "awaiting_input",
                    "required_task_choices": sum(
                        t["run"].get("output", {}).get("pause_kind") == "task_clarification"
                        for t in turns
                    ),
                    "reads": reading,
                    "gate_status": output.get("answer_gate", {}).get("status"),
                    "semantic_review": "author_pending",
                }
                score["program_goal"] = (
                    score["target_state"]
                    and len(present) == len(case["necessary"])
                    and score["gate_status"] in {"approved", "contract_valid"}
                    and not wrong
                )
                row = {
                    "case": case,
                    "fixture": fixture,
                    "arm": args.arm,
                    "turns": turns,
                    "score": score,
                    "elapsed_ms": (time.perf_counter() - start) * 1000,
                    "usage": client.get(
                        "/api/v1/runs/" + run["id"] + "/usage", headers=headers["support"]
                    ).json(),
                    "events": client.get(
                        "/api/v1/runs/" + run["id"] + "/events", headers=headers["support"]
                    ).text,
                }
                records.append(row)
                with (args.output / "runs.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                write(
                    args.output / "progress.json",
                    {
                        "runs": len(records),
                        "last": case["id"],
                        "budget": budget.summary() if budget else {"requests": 0},
                    },
                )
            with db.sessions() as session:
                requests = session.scalar(select(func.count()).select_from(ReturnRequest))
            summary = {
                "runs": len(records),
                "arm": args.arm,
                "paid": not offline,
                "program_goals": sum(r["score"]["program_goal"] for r in records),
                "wrong_application_previews": sum(
                    r["score"]["wrong_application_preview"] for r in records
                ),
                "unnecessary_pauses": sum(r["score"]["unnecessary_pause"] for r in records),
                "complete_necessary_quotes": sum(
                    r["score"]["necessary_quotes"] == r["score"]["needed_quotes"] for r in records
                ),
                "return_requests_in_isolated_database": requests,
                "budget": budget.summary() if budget else {"requests": 0},
                "agents": {
                    agent: {
                        "runs": sum(r["case"]["agent_mode"] == agent for r in records),
                        "program_goals": sum(
                            r["case"]["agent_mode"] == agent and r["score"]["program_goal"]
                            for r in records
                        ),
                    }
                    for agent in {r["case"]["agent_mode"] for r in records}
                },
                "modes": {
                    mode: {
                        "runs": sum(r["case"]["answer_mode"] == mode for r in records),
                        "program_goals": sum(
                            r["case"]["answer_mode"] == mode and r["score"]["program_goal"]
                            for r in records
                        ),
                        "repairs_attempted": sum(
                            r["case"]["answer_mode"] == mode
                            and bool(
                                r["turns"][-1]["run"]
                                .get("output", {})
                                .get("answer_gate", {})
                                .get("repair", {})
                                .get("attempted")
                            )
                            for r in records
                        ),
                    }
                    for mode in {r["case"]["answer_mode"] for r in records}
                },
            }
            write(args.output / "summary.json", summary)
            print(json.dumps(summary))
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
