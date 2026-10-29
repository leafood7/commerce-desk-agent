"""Opt-in <=24 requests, shared prior 5 CNY grant, isolated real MCP/RAG/graph flows.

Default validate never reads credentials or invokes a model. No runtime stack settings
are changed. Requests/usage are retained without keys or hidden reasoning content.
"""

import argparse
import json
import secrets
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

from evaluate_return_rag import DEFAULT_DATA, load_dataset

ROOT = Path(__file__).resolve().parents[1]
PHASE = "return-agent-v1-model"


class Recorder:
    def __init__(self, output, settings):
        from evaluation.answer_trust_budget import BudgetLedger
        from workbench.return_decisions import model_decision

        self.output, self.settings = output, settings
        self.ledger = BudgetLedger(ROOT / "work/answer-trust/budget.json")
        self.original, self.label, self.rows = model_decision, "", []

    def invoke(self, settings, observation, **kwargs):
        from evaluation.answer_trust_budget import INPUT_LIMIT, OUTPUT_LIMIT, cost
        from workbench.intent_router import ModelNotSent
        from workbench.quotas import observed_tokens
        from workbench.return_decisions import request_body, validate_decision

        request = request_body(settings, observation)  # Preflight before monetary reserve.
        state = self.ledger.snapshot()
        shared = sum(
            e["charged_cny"]
            for e in state["entries"]
            if e.get("metadata", {}).get("authorization_cny") == 5
        )
        count = sum(e.get("metadata", {}).get("phase") == PHASE for e in state["entries"])
        if count >= 24 or shared + cost(INPUT_LIMIT, OUTPUT_LIMIT) > 5:
            raise ModelNotSent("Shared 5 CNY grant or 24-request cap reached")
        token = self.ledger.reserve(
            {
                "phase": PHASE,
                "case_id": self.label,
                "authorization_cny": 5,
                "grant": "existing_shared_5cny_no_new_runtime_authorization",
            }
        )
        started, response, error = time.perf_counter(), None, None
        try:
            response = self.original(settings, observation, **kwargs)
            return response
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            usage = response.get("usage") if response else None
            valid = (
                isinstance(usage, dict)
                and observed_tokens(usage) is not None
                and all(
                    type(usage.get(k)) is int and usage[k] >= 0
                    for k in ("prompt_tokens", "completion_tokens")
                )
            )
            entry = self.ledger.settle(
                token,
                {
                    "input_tokens": usage["prompt_tokens"],
                    "output_tokens": usage["completion_tokens"],
                }
                if valid
                else None,
                error_type=error,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                model=response.get("model") if response else None,
            )
            parsed, legal = None, False
            if response:
                try:
                    parsed = json.loads(response["proposal"])
                    validate_decision(parsed, observation)
                    legal = True
                except (ValueError, TypeError):
                    pass
            row = {
                "case_id": self.label,
                "request": request,
                "response": response,
                "parsed": parsed,
                "legal_action": legal,
                "error": error,
                "accounting": entry,
            }
            self.rows.append(row)
            with (self.output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def decision_batch(recorder, data):
    for case in data["decision-cases"]:
        recorder.label = case["id"]
        context = {
            "allowed_actions": case["allowed"],
            "stage": case["stage"],
            "latest_conditions": {"item_condition": case["latest_condition"]},
            "prior_condition_is_not_current_fact": case.get("prior_condition"),
        }
        try:
            recorder.invoke(recorder.settings, context)
        except Exception as exc:
            print(case["id"] + ": " + type(exc).__name__, flush=True)


def flow_batch(recorder):
    import chromadb
    import uvicorn
    from alembic import command
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sqlalchemy import select

    from workbench.app import create_app
    from workbench.cli import migration_config
    from workbench.database import Database
    from workbench.ingestion import work_once
    from workbench.knowledge_index import ChromaIndex
    from workbench.knowledge_models import KnowledgeChunk
    from workbench.knowledge_schemas import VersionOptions
    from workbench.knowledge_service import create_version, publish
    from workbench.mcp_gateway import MCPGateway
    from workbench.mcp_server import create_mcp_app
    from workbench.models import Order, OrderItem, User
    from workbench.return_schemas import ReturnPolicyInput
    from workbench.returns import register_policy
    from workbench.seed import seed_demo

    settings = recorder.settings.model_copy(
        update={
            "environment": "test",
            "database_url": SecretStr(
                "sqlite:///" + str(recorder.output / "business.sqlite3").replace("\\", "/")
            ),
            "storage_root": str(recorder.output / "files"),
            "mcp_warm_models": False,
            "run_dispatch_mode": "inline",
        }
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        settings.mcp_url = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
    port = int(settings.mcp_url.split(":")[-1].split("/")[0])
    command.upgrade(migration_config(settings), "head")
    database = Database(settings)
    password = secrets.token_urlsafe(32)
    seed_demo(database, password)

    class IsolatedLocalIndex(ChromaIndex):
        def __init__(self):
            self.client = chromadb.PersistentClient(path=str(recorder.output / "index"))

    index = IsolatedLocalIndex()
    clauses = {
        "scope_clause": "本隔离演示规则仅适用于商品DEMO-3，不适用于其他商品。",
        "window_clause": "本演示申请窗口为实际签收后7天；预计到达不是实际签收。",
        "condition_clause": "本演示申请需要商品未使用；未知状态先核实，其他原因转人工。",
    }
    with database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == "A1003"))
        order.payment_status = "paid"
        item = session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
        item.quantity = 3
        item_id = item.id
        admin = session.scalar(
            select(User).where(User.organization_id == order.organization_id, User.role == "admin")
        )
        admin_id = admin.id
        session.commit()
        _, version, _ = create_version(
            session,
            admin,
            settings,
            "isolated-demo-return.md",
            ("# 隔离演示退货\n\n" + "\n\n".join(clauses.values())).encode(),
            VersionOptions(policy_scope="returns", chunk_chars=300, chunk_overlap=40),
        )
        version_id = version.id
    if not work_once(database, settings, index):
        raise RuntimeError("Isolated real indexing did not process the document")
    with database.sessions() as session:
        admin = session.get(User, admin_id)
        publish(session, admin, version_id)
        chunks = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == version_id)
        ).all()
        refs = {
            k: {"chunk_id": next(c.id for c in chunks if q in c.content), "quote": q}
            for k, q in clauses.items()
        }
        policy = register_policy(
            session,
            admin,
            ReturnPolicyInput(
                version_id=version_id,
                skus=["DEMO-3"],
                window_days=7,
                requires_unused=True,
                administrator_verified=True,
                **refs,
            ),
        )
        policy_id = policy.id
    mcp_settings = settings.model_copy(
        update={
            "agent_router_mode": "rules",
            "return_decision_mode": "rules",
            "agent_router_key": None,
        }
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_mcp_app(mcp_settings, index=index),
            host="127.0.0.1",
            port=port,
            log_level="error",
            timeout_graceful_shutdown=2,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Isolated MCP server did not start")
    flows = []
    try:
        with TestClient(create_app(settings, gateway=MCPGateway(settings))) as client:

            def auth(role):
                response = client.post(
                    "/api/v1/auth/login",
                    json={
                        "organization": "demo-a",
                        "email": role + "@demo.test",
                        "password": password,
                    },
                )
                response.raise_for_status()
                return {"Authorization": "Bearer " + response.json()["access_token"]}

            support, approver, admin_auth = auth("support"), auth("approver"), auth("admin")

            def activate(mode):
                current = client.get("/api/v1/runtime/config", headers=admin_auth).json()
                version = client.post(
                    "/api/v1/runtime/config/versions",
                    headers=admin_auth,
                    json={
                        "model_alias": "server_default" if mode == "model" else "rules",
                        "return_decisions": mode,
                    },
                )
                version.raise_for_status()
                response = client.post(
                    f"/api/v1/runtime/config/versions/{version.json()['id']}/publish",
                    headers=admin_auth,
                    json={"expected_revision": current["revision"]},
                )
                response.raise_for_status()

            def get(run):
                return client.get("/api/v1/runs/" + run["id"], headers=support).json()

            def resume(run, action, **fields):
                response = client.post(
                    f"/api/v1/runs/{run['id']}/resume",
                    headers={**support, "Idempotency-Key": str(uuid4())},
                    json={"action": action, "pause_id": run["output"].get("pause_id"), **fields},
                )
                response.raise_for_status()
                return get(run)

            for label, condition in (
                ("normal", "unused"),
                ("ask_condition", "unknown"),
                ("correction", "unused"),
            ):
                for mode in ("rules", "model"):
                    activate(mode)
                    recorder.label = "flow-" + label + "-" + mode
                    before, started = len(recorder.rows), time.perf_counter()
                    response = client.post(
                        "/api/v1/runs",
                        headers={**support, "Idempotency-Key": str(uuid4())},
                        json={
                            "kind": "aftersales",
                            "query": "申请未使用商品退货，核对实际签收7天及适用范围。",
                            "order_number": "A1003",
                            "return_item_id": item_id,
                            "return_policy_id": policy_id,
                            "return_quantity": 1,
                            "return_condition": condition,
                            "reason": "隔离真实模型开发验收",
                        },
                    )
                    response.raise_for_status()
                    run = get(response.json())
                    if label == "ask_condition" and run["status"] == "awaiting_input":
                        run = resume(run, "supply", return_condition="unused")
                    if label == "correction" and run["status"] == "awaiting_confirmation":
                        run = resume(
                            run, "correct", return_condition="used", reason="更正：商品已使用"
                        )
                    pre = run["status"]
                    if mode == "model" and label != "correction" and pre == "awaiting_confirmation":
                        run = resume(run, "confirm")
                        if run["status"] == "awaiting_approval":
                            request = run["output"]["request"]
                            decision = client.post(
                                f"/api/v1/returns/requests/{request['id']}/decision",
                                headers=approver,
                                json={
                                    "expected_revision": request["revision"],
                                    "action": "approve",
                                    "reason": "隔离演示人工审批替身",
                                },
                            )
                            decision.raise_for_status()
                            run = resume(run, "refresh")
                            if run["status"] == "awaiting_confirmation":
                                run = resume(run, "confirm")
                    elif mode == "rules" and pre == "awaiting_confirmation":
                        client.post(
                            f"/api/v1/runs/{run['id']}/cancel", headers=support, json={}
                        ).raise_for_status()
                    usage = client.get(f"/api/v1/runs/{run['id']}/usage", headers=support).json()
                    flows.append(
                        {
                            "label": label,
                            "mode": mode,
                            "pre_confirmation_status": pre,
                            "final_run": run,
                            "usage": usage,
                            "paid_requests": len(recorder.rows) - before,
                            "elapsed_ms": (time.perf_counter() - started) * 1000,
                        }
                    )
                    (recorder.output / "flows.json").write_text(
                        json.dumps(flows, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        database.close()
    return flows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--output", type=Path, default=ROOT / "work/return-agent-v1/model")
    parser.add_argument("--mode", choices=["decisions", "flows", "all"], default="all")
    args = parser.parse_args()
    manifest, data = load_dataset(DEFAULT_DATA)
    if args.command == "validate":
        print(
            json.dumps(
                {
                    "decision_cases": len(data["decision-cases"]),
                    "max_new_paid_requests": 24,
                    "shared_grant_cny": 5,
                }
            )
        )
        return
    output = args.output.resolve()
    if output.exists():
        parser.error("Output exists; do not overwrite paid traces")
    from evaluation.answer_trust_budget import BudgetLedger

    ledger_path = ROOT / "work/answer-trust/budget.json"
    if not ledger_path.is_file():
        parser.error("Existing shared budget ledger is required; never create a fresh paid budget")
    budget = BudgetLedger(ledger_path).snapshot()
    if sum(e.get("metadata", {}).get("phase") == PHASE for e in budget["entries"]) >= 24:
        parser.error(
            "This stage's 24-request authorization is exhausted; no further paid request allowed"
        )
    from dotenv import dotenv_values
    from pydantic import SecretStr

    import workbench.return_decisions as decisions
    from workbench.config import WorkbenchSettings

    env = dotenv_values(ROOT / ".env")
    key = env.get("COMPATIBLE_API_KEY")
    if not key:
        parser.error("Existing model key unavailable; no paid request sent")
    settings = WorkbenchSettings(
        environment="test",
        agent_router_mode="model",
        return_decision_mode="model",
        agent_router_url="https://api.deepseek.com/chat/completions",
        agent_router_model="deepseek-flash",
        agent_router_key=SecretStr(key),
    )
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                **manifest,
                "mode": args.mode,
                "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                "pricing_checked": "2026-10-29",
                "input_cny_per_million": 2,
                "output_cny_per_million": 8,
                "model": "deepseek-flash",
                "prompt_sha256": decisions.prompt_hash(),
                "temperature": 0,
                "thinking": "disabled",
                "max_output_tokens": 256,
                "retries": 0,
                "full_flows": "post-implementation integration verification, not blind semantic evaluation",
                "review": "author-involved; no independent review claim",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    recorder = Recorder(output, settings)
    before = recorder.ledger.snapshot()["committed_cny"]
    original = decisions.model_decision
    flows, error = [], None
    try:
        if args.mode in {"decisions", "all"}:
            decision_batch(recorder, data)
        decisions.model_decision = recorder.invoke
        if args.mode in {"flows", "all"}:
            flows = flow_batch(recorder)
    except Exception as exc:
        error = type(exc).__name__
    finally:
        decisions.model_decision = original
        final = recorder.ledger.snapshot()
        summary = {
            "paid_requests": len(recorder.rows),
            "legal_proposals": sum(r["legal_action"] for r in recorder.rows),
            "errors": sum(bool(r["error"]) for r in recorder.rows),
            "batch_error": error,
            "charged_or_reserved_cny": final["committed_cny"] - before,
            "total_committed_cny": final["committed_cny"],
            "unknown_usage_calls": sum(
                r["accounting"]["status"] != "settled" for r in recorder.rows
            ),
            "flow_records": len(flows),
            "runtime_model_enabled": False,
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary), flush=True)
    if error:
        raise SystemExit("Isolated batch stopped: " + error + "; preserve artifacts")


if __name__ == "__main__":
    main()
