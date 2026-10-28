"""Paired fixed-evidence and production-MCP runs; no model judge."""
import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from contextlib import AsyncExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from evaluation.answer_trust_budget import BudgetLedger, BudgetedModel, BudgetStop
from evaluation.answer_trust_data import (
    ROOT, DATA, BASELINE_REF, VARIANTS, LAYERS, candidate_snapshot, digest,
    documents, load_cases, read_json, validate_frozen, variant_order,
    verify_candidate_lock, write_json,
)

DEFAULT_LEDGER = ROOT / "work/answer-trust/budget.json"
FROZEN_MODULES = ("answer_policy", "policy_evidence", "supportpilot_agent")

@contextmanager
def frozen_baseline(root=ROOT, source_root=None):
    """Load actual frozen agent + checker + guidance, never the candidate graph.

    Shared business/tool/model modules must be byte-equivalent (newline-normalized)
    to the baseline, or loading fails instead of silently mixing implementations.
    """
    for folder in ("src/business", "src/tool_protocol", "src/core"):
        names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASELINE_REF, "--", folder], cwd=root).decode().splitlines()
        for name in names:
            if not name.endswith(".py"):
                continue
            original = subprocess.check_output(["git", "show", BASELINE_REF + ":" + name], cwd=root)
            current = (root / name).read_bytes().replace(b"\r\n", b"\n")
            if original.replace(b"\r\n", b"\n") != current:
                raise ValueError("Baseline shared dependency changed: " + name)
    previous = {f"agents.{name}": sys.modules.get(f"agents.{name}") for name in FROZEN_MODULES}
    modules, hashes = {}, {}
    try:
        for name in FROZEN_MODULES:
            relative = f"src/agents/{name}.py"
            expected = subprocess.check_output(["git", "show", BASELINE_REF + ":" + relative], cwd=root)
            content = (Path(source_root) / relative).read_bytes() if source_root else expected
            if content.replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
                raise ValueError("Supplied baseline source differs from frozen commit")
            import hashlib
            hashes[relative] = hashlib.sha256(content).hexdigest()
            module = types.ModuleType("agents." + name)
            module.__file__ = f"git:{BASELINE_REF}/{relative}"
            sys.modules[module.__name__] = module
            exec(compile(content, module.__file__, "exec"), module.__dict__)
            modules[name] = module
        yield modules["supportpilot_agent"], hashes
    finally:
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

def serialize_message(message):
    return message.model_dump(mode="json")

def parse_turn(messages):
    """Extract source snapshots without importing old one-off experiment scripts."""
    from business.policy_context import flatten_policy_matches
    pending, evidence, tools, queries, tool_errors = {}, {}, [], [], []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            pending[call["id"]] = call
            if call["name"] == "search_knowledge_base":
                queries.append(call)
        if getattr(message, "type", None) != "tool":
            continue
        call = pending.get(message.tool_call_id, {})
        record = {"call_id": message.tool_call_id, "name": message.name or call.get("name"),
            "args": call.get("args", {}), "content": message.content, "status": getattr(message, "status", "success")}
        tools.append(record)
        try:
            payload = json.loads(message.content)
        except (ValueError, TypeError):
            payload = {}
        if payload.get("status") in {"error", "blocked_evaluation"}:
            tool_errors.append({"name": record["name"], "status": payload["status"]})
        if record["name"] == "search_knowledge_base":
            for item in flatten_policy_matches(payload.get("matches", [])):
                evidence[item["id"]] = item
    return {"evidence": list(evidence.values()), "tool_results": tools, "queries": queries,
        "tool_errors": tool_errors, "unexpected_write_attempts": sum(t["name"] == "request_shipping" for t in tools)}

async def baseline_fixed(module, question, evidence, config, history):
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    messages = [*history, HumanMessage(content=question),
        AIMessage(content="", tool_calls=[{"id": "fixed_policy", "name": "search_knowledge_base", "args": {"query": question}}]),
        ToolMessage(name="search_knowledge_base", tool_call_id="fixed_policy",
            content=json.dumps({"status": "found" if evidence else "no_evidence", "matches": evidence}, ensure_ascii=False))]
    state = {"messages": messages, "policy_check": {"mode": "retrieve", "status": "checked",
        "query": question, "evidence_ids": [], "reason": "冻结证据实验，不再检索。"}}
    update = await module.call_model(state, config, tools=[])
    response = update["messages"][-1]
    return {"answer": response.content, "messages": [*messages, response], "answer_draft": {},
        "answer_audit": {"mode": "off", "status": "baseline", "semantic_check_called": False}}

def model_factory(ledger, records, metadata):
    from langchain_openai import ChatOpenAI
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("DEEPSEEK_API_KEY is required; never put it in the result file")
    model = ChatOpenAI(model="deepseek-flash", base_url="https://api.deepseek.com",
        api_key=os.environ["DEEPSEEK_API_KEY"], temperature=0, max_tokens=2048,
        max_retries=0, timeout=60, streaming=False,
        extra_body={"thinking": {"type": "disabled"}})
    return lambda _name: BudgetedModel(model, ledger, records, metadata)

def selection(values, allowed, label):
    selected = values.split(",")
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(allowed):
        raise ValueError(f"Invalid {label}")
    return tuple(selected)

async def run(args):
    validate_frozen()
    if not args.online:
        raise ValueError("run requires --online; validate makes no paid calls")
    if Path(args.ledger).resolve() != DEFAULT_LEDGER.resolve():
        raise BudgetStop("All runs must use work/answer-trust/budget.json; alternate ledgers are forbidden")
    if args.repeat < 1:
        raise ValueError("repeat must be positive")
    lock = None
    if args.split == "holdout":
        if not args.candidate_lock:
            raise ValueError("Holdout requires a candidate source/config lock created before reading it")
        lock = verify_candidate_lock(args.candidate_lock)
    cases = load_cases(args.split, args.ids)
    variants = selection(args.variants, VARIANTS, "variants")
    layers = selection(args.layers, LAYERS, "layers")
    destination = Path(args.output).resolve()
    if destination.exists():
        raise FileExistsError("Keep prior results; choose a new output path")
    corpus = documents()
    ledger = BudgetLedger(DEFAULT_LEDGER)
    from langchain_core.messages import HumanMessage, AIMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from agents import supportpilot_agent as candidate
    report = {"metadata": {"version": 1, "status": "running", "split": args.split,
        "case_ids": [c["id"] for c in cases], "variants": list(variants), "layers": list(layers),
        "repeat": args.repeat, "baseline_ref": BASELINE_REF, "source_hashes": candidate_snapshot(),
        "dataset_manifest_sha256": digest(DATA / "manifest.json"), "started_at": datetime.now(timezone.utc).isoformat(),
        "candidate_lock": lock, "model": "deepseek-flash", "temperature": 0, "max_output_tokens": 2048,
        "thinking": "disabled", "sdk_retries": 0, "model_timeout_seconds": 60,
        "synthetic_data": True, "human_review": "pending", "business_writes": "blocked",
        "limitations": "Synthetic family-split cases, author review, not independent real-world accuracy. No automatic semantic grades."},
        "rows": []}
    write_json(destination, report, exclusive=True)
    @tool
    def blocked_shipping(order_id: str, kind: str = "normal", reason: str = "evaluation") -> str:
        """Evaluation records attempted writes but never performs business writes."""
        return json.dumps({"status": "blocked_evaluation", "message": "评测禁止业务写入"}, ensure_ascii=False)
    blocked_shipping.name = "request_shipping"
    records = []
    context = {}
    try:
        with frozen_baseline(source_root=args.baseline_root) as (baseline, hashes):
            report["metadata"]["baseline_source_hashes"] = hashes
            factory = model_factory(ledger, records, context)
            with patch.object(candidate, "get_model", factory), patch.object(baseline, "get_model", factory):
                async with AsyncExitStack() as stack:
                    mcp_tools = None
                    if "mcp" in layers:
                        from tool_protocol.client import readonly_mcp_tools, server_connection
                        from business.order_repository import initialize_demo_orders
                        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="trust-eval-", dir=ROOT / "work"))
                        db = Path(temporary) / "orders.sqlite3"
                        initialize_demo_orders(db)
                        connection = server_connection()
                        connection["env"]["SUPPORTPILOT_DB_PATH"] = str(db)
                        index_source = Path(connection["env"].get("SUPPORTPILOT_CHROMA_PATH", "data/supportpilot/chroma"))
                        if not index_source.is_absolute():
                            index_source = ROOT / index_source
                        index_copy = Path(temporary) / "index"
                        shutil.copytree(index_source, index_copy)
                        connection["env"]["SUPPORTPILOT_CHROMA_PATH"] = str(index_copy)
                        start = perf_counter()
                        mcp_tools = await stack.enter_async_context(readonly_mcp_tools(connection))
                        report["metadata"]["mcp_startup_ms"] = (perf_counter() - start) * 1000
                    for layer in layers:
                        for index, case in enumerate(cases):
                            for variant in variant_order(index, variants, args.repeat):
                                context.clear()
                                context.update({"run": str(destination), "case_id": case["id"], "variant": variant,
                                    "layer": layer, "repeat": args.repeat})
                                module = baseline if variant == "baseline" else candidate
                                mode = "off" if variant == "baseline" else variant
                                config = {"configurable": {"thread_id": uuid4().hex, "policy_check_mode": "auto",
                                    "answer_guard_mode": mode}, "recursion_limit": 16}
                                row = {"case_id": case["id"], "category": case["category"], "family": case["family"],
                                    "variant": variant, "layer": layer, "repeat": args.repeat, "status": "running",
                                    "turns": [], "calls": []}
                                report["rows"].append(row)
                                call_start = len(records)
                                graph = module.build_supportpilot_graph(tools=[*mcp_tools, blocked_shipping], checkpointer=InMemorySaver()) if layer == "mcp" else None
                                history, length = [], 0
                                for turn_no, question in enumerate(case["turns"], 1):
                                    context["turn"] = turn_no
                                    start = perf_counter()
                                    turn_calls_start = len(records)
                                    turn = {"question": question}
                                    row["turns"].append(turn)
                                    try:
                                        if layer == "fixed":
                                            evidence = [corpus[k] for k in case["evidence_ids"]]
                                            if variant == "baseline":
                                                result = await baseline_fixed(module, question, evidence, config, history)
                                            else:
                                                result = await module.answer_fixed_evidence(question, evidence, config, history=history)
                                            messages = result["messages"]
                                            history = [*history, HumanMessage(content=question), AIMessage(content=str(result["answer"]))]
                                            turn.update({"answer": result["answer"], "evidence": evidence,
                                                "tool_results": [], "queries": [], "unexpected_write_attempts": 0,
                                                "answer_draft": result.get("answer_draft", {}), "answer_audit": result.get("answer_audit", {})})
                                        else:
                                            result = await graph.ainvoke({"messages": [HumanMessage(content=question)]}, config)
                                            messages = result["messages"][length:]
                                            length = len(result["messages"])
                                            turn.update({"answer": result["messages"][-1].content, **parse_turn(messages),
                                                "policy_check": result.get("policy_check", {}), "answer_draft": result.get("answer_draft", {}),
                                                "answer_audit": result.get("answer_audit", {})})
                                        turn["messages"] = [serialize_message(m) for m in messages]
                                        turn["status"] = "complete"
                                    except BudgetStop:
                                        raise
                                    except Exception as exc:
                                        turn.update({"status": "error", "error_type": type(exc).__name__, "answer": ""})
                                    finally:
                                        turn["elapsed_ms"] = (perf_counter() - start) * 1000
                                        turn["calls"] = records[turn_calls_start:]
                                        row["calls"] = records[call_start:]
                                        report["budget"] = ledger.snapshot()
                                        write_json(destination, report)
                                    if turn["status"] != "complete":
                                        break
                                row["status"] = "complete" if len(row["turns"]) == len(case["turns"]) and all(t["status"] == "complete" for t in row["turns"]) else "error"
                                write_json(destination, report)
                                print(f"{layer} {case['id']} {variant}: {row['status']}", flush=True)
        report["metadata"]["status"] = "complete"
    except BudgetStop as exc:
        report["metadata"]["status"] = "budget_stopped"
        report["failure"] = {"error_type": type(exc).__name__, "reason": str(exc)}
    except BaseException as exc:
        report["metadata"]["status"] = "failed"
        report["failure"] = {"error_type": type(exc).__name__}
        raise
    finally:
        report["metadata"]["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["budget"] = ledger.snapshot()
        write_json(destination, report)
    return report

