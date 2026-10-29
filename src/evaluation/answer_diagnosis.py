"""第一阶段诊断：冻结基线、真实MCP、逐调用计费，不给模型答案自动判分。"""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from evaluation.answer_trust_budget import (
    INPUT_LIMIT,
    OUTPUT_LIMIT,
    BudgetedModel,
    BudgetLedger,
    BudgetStop,
    cost,
)
from evaluation.answer_trust_data import BASELINE_REF, ROOT, documents, read_json, write_json

PHASE = "answer-diagnosis-v1"
DIRECTORY = ROOT / "docs/diagnosis-v1"
LEDGER = ROOT / "work/answer-trust/budget.json"
LIMIT_CNY = 20.0
ANSWER_LIMIT = 40
ABLATION = "docs/experiments/2026-10-25/context-ablation/scorecards-30-v1.jsonl"
DEV = "data/evaluation/answer_trust/dev.jsonl"
HISTORY = "docs/mcp-semantic-final-v2.json"
PHONE = "docs/rag-miss-fix-offline.json"
MODEL_CONFIG = {
    "model": "deepseek-flash",
    "temperature": 0,
    "max_tokens": 2048,
    "thinking": "disabled",
    "retries": 0,
    "timeout": 60,
}
RETRIEVAL = {
    "strategy": "hybrid_rerank",
    "pool_size": 12,
    "top_k": 3,
    "threshold": -2,
    "context_budget": 1800,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(directory=DIRECTORY):
    """只读取公开历史题与dev，不读取或校验最终测试题正文。"""
    directory = Path(directory)
    manifest = directory / "manifest.json"
    if manifest.exists():
        return validate(directory)
    old = {
        c["id"]: c
        for c in map(
            json.loads,
            filter(str.strip, (ROOT / ABLATION).read_text(encoding="utf-8-sig").splitlines()),
        )
    }
    dev = {
        c["id"]: c
        for c in map(
            json.loads, filter(str.strip, (ROOT / DEV).read_text(encoding="utf-8-sig").splitlines())
        )
    }
    definitions = [
        ("ablation-018", "policy_scope", "current_unknown"),
        ("ablation-009", "policy_scope", "current_unknown"),
        ("ablation-027", "capability", "current_unknown"),
        ("trust-dev-014", "capability", "not_previously_run"),
        ("ablation-015", "regression", "historically_fixed"),
        ("ablation-030", "regression", "historically_fixed"),
        ("trust-dev-013", "regression", "similar_issue_fixed"),
        ("trust-dev-017", "normal_completion", "not_previously_run"),
    ]
    cases = []
    for case_id, purpose, status in definitions:
        source = old.get(case_id) or dev[case_id]
        groups = source.get("evidence_by_turn") or [
            source.get("evidence_groups") or source.get("scorecard", {}).get("evidence_groups", [])
        ] * len(source["turns"])
        cases.append(
            {
                "id": case_id,
                "turns": source["turns"],
                "purpose": purpose,
                "historical_status": status,
                "source": ABLATION if case_id in old else DEV,
                "evidence_by_turn": groups,
                "review_points": source.get("reference_points") or source.get("scorecard"),
                "fault_injection": None,
            }
        )
    phone = next(c for c in read_json(ROOT / PHONE)["targets"] if c["id"] == "phone")
    cases.append(
        {
            "id": "phone",
            "turns": [phone["query"]],
            "purpose": "retrieval_control",
            "historical_status": "historically_fixed",
            "source": PHONE,
            "evidence_by_turn": [[["return_digital_policy.txt-1"]]],
            "review_points": ["合理开箱不当然失去退货资格；保留期限、完好及购买提示条件。"],
            "fault_injection": None,
        }
    )
    named = next(
        r["case"] for r in read_json(ROOT / HISTORY)["rows"] if r["case"]["id"] == "new-name"
    )
    cases.append(
        {
            "id": "new-name",
            "turns": named["turns"],
            "purpose": "empty_evidence_control",
            "historical_status": "historically_fixed",
            "source": HISTORY,
            "evidence_by_turn": [[]],
            "review_points": [
                "实际传给Agent的检索为空时，不追加食品限定等未经支持的政策；活动名称不是适用范围。"
            ],
            "fault_injection": "empty_policy_after_real_mcp",
        }
    )
    corpus = documents()
    for case in cases:
        for groups in case["evidence_by_turn"]:
            for group in groups:
                if any(key not in corpus for key in group):
                    raise ValueError("诊断评分卡含不存在的政策片段")
    write_json(directory / "cases.json", cases, exclusive=True)
    paths = [ABLATION, DEV, HISTORY, PHONE, "data/demo_orders.json", "uv.lock"]
    paths += [
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in sorted((ROOT / "data/knowledge").glob("*.txt"))
    ]
    protocol = {
        "version": 1,
        "phase": PHASE,
        "baseline_ref": BASELINE_REF,
        "created_at": datetime.now(UTC).isoformat(),
        "model": MODEL_CONFIG,
        "retrieval": RETRIEVAL,
        "limit_cny": LIMIT_CNY,
        "answer_limit": ANSWER_LIMIT,
        "case_count": len(cases),
        "initial_turns": sum(len(c["turns"]) for c in cases),
        "source_hashes": {p: sha(ROOT / p) for p in paths},
        "cases_sha256": sha(directory / "cases.json"),
        "review": "AI-assisted diagnosis; author confirmation pending; no automatic semantic grades",
        "pricing": {
            "input_uncached_peak_cny_per_million": 2,
            "output_peak_cny_per_million": 8,
            "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
            "verified_date": "2026-10-28",
            "basis": "conservative upper-rate estimate, not invoice",
        },
        "fixed_clock": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    write_json(manifest, protocol, exclusive=True)
    return protocol


def validate(directory=DIRECTORY):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest["phase"] != PHASE or manifest["baseline_ref"] != BASELINE_REF:
        raise ValueError("诊断身份不符")
    if sha(directory / "cases.json") != manifest["cases_sha256"]:
        raise ValueError("冻结诊断样本被修改")
    for path, expected in manifest["source_hashes"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("冻结输入被修改：" + path)
    cases = read_json(directory / "cases.json")
    if len(cases) != 10 or sum(len(c["turns"]) for c in cases) != 12:
        raise ValueError("诊断样本应为10场景12轮")
    return manifest


class DiagnosticLedger(BudgetLedger):
    """全项目100元账本内再限制本阶段20元，回答槽跨批次累计。"""

    def reserve(self, metadata=None):
        maximum = cost(INPUT_LIMIT, OUTPUT_LIMIT)
        with self.transaction() as state:
            spent = sum(e["charged_cny"] for e in state["entries"])
            stage = sum(
                e["charged_cny"] for e in state["entries"] if e["metadata"].get("phase") == PHASE
            )
            if spent + maximum > state["limit_cny"] or stage + maximum > LIMIT_CNY:
                raise BudgetStop("诊断阶段或项目总预算不足，停止模型调用")
            token = uuid4().hex
            state["entries"].append(
                {
                    "id": token,
                    "status": "reserved",
                    "reserved_cny": maximum,
                    "charged_cny": maximum,
                    "started_at": datetime.now(UTC).isoformat(),
                    "metadata": {**(metadata or {}), "phase": PHASE},
                    "usage": None,
                }
            )
            write_json(self.path, state)
            return token

    def answer_slot(self, metadata):
        with self.transaction() as state:
            slots = state.setdefault("diagnostic_answers", [])
            if len(slots) >= ANSWER_LIMIT:
                raise BudgetStop("已到40份回答上限（含中断的预留槽）")
            slot = {"id": uuid4().hex, "status": "reserved", **metadata}
            slots.append(slot)
            write_json(self.path, state)
            return slot["id"]

    def finish_answer(self, slot_id, status):
        with self.transaction() as state:
            next(s for s in state["diagnostic_answers"] if s["id"] == slot_id)["status"] = status
            write_json(self.path, state)

    def stage_snapshot(self):
        state = self.snapshot()
        entries = [e for e in state["entries"] if e["metadata"].get("phase") == PHASE]
        return {
            "project_committed_cny": state["committed_cny"],
            "stage_committed_cny": sum(e["charged_cny"] for e in entries),
            "unknown_or_pending_calls": sum(e["status"] != "settled" for e in entries),
            "model_calls": len(entries),
            "answer_slots": len(state.get("diagnostic_answers", [])),
            "phase_limit_cny": LIMIT_CNY,
            "project_limit_cny": state["limit_cny"],
        }


class TracingModel:
    def __init__(self, model, ledger, records, context, bindings=None, tools=None):
        self.model, self.ledger, self.records, self.context = model, ledger, records, context
        self.bindings, self.tools = bindings or {}, tools or []

    def bind(self, **kwargs):
        return TracingModel(
            self.model.bind(**kwargs),
            self.ledger,
            self.records,
            self.context,
            {**self.bindings, **kwargs},
            self.tools,
        )

    def bind_tools(self, tools, **kwargs):
        from langchain_core.utils.function_calling import convert_to_openai_tool

        definitions = [convert_to_openai_tool(t) for t in tools]
        bound = self.model.bind_tools(tools, **kwargs) if tools else self.model
        return TracingModel(
            bound, self.ledger, self.records, self.context, {**self.bindings, **kwargs}, definitions
        )

    async def ainvoke(self, messages, config=None):
        record = {
            "metadata": {**self.context},
            "request_messages": [m.model_dump(mode="json") for m in messages],
            "tools": self.tools,
            "bindings": self.bindings,
        }
        self.records.append(record)
        payments = []
        started = perf_counter()
        try:
            response = await BudgetedModel(self.model, self.ledger, payments, self.context).ainvoke(
                messages, config
            )
            record["response_message"] = response.model_dump(mode="json")
            return response
        except BaseException as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            record["payments"] = payments


def factory(ledger, records, context):
    import httpx
    from langchain_openai import ChatOpenAI

    # Avoid stale proxy keep-alive sockets. This is not a request retry.
    client = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=0), timeout=60)

    model = ChatOpenAI(
        model=MODEL_CONFIG["model"],
        base_url="https://api.deepseek.com",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        temperature=0,
        max_tokens=2048,
        max_retries=0,
        timeout=60,
        streaming=False,
        extra_body={"thinking": {"type": "disabled"}},
        http_async_client=client,
    )

    def get_model(_name):
        return TracingModel(model, ledger, records, context)

    get_model.aclose = client.aclose
    return get_model


@contextmanager
def isolated_connection(directory, manifest):
    from business.order_repository import initialize_demo_orders
    from tool_protocol.client import server_connection

    directory = Path(directory)
    db = directory / "orders.sqlite3"
    initialize_demo_orders(db)
    now = datetime.fromisoformat(manifest["fixed_clock"])
    seed = read_json(ROOT / "data/demo_orders.json")
    with sqlite3.connect(db) as connection:
        for row in seed:
            paid = now - timedelta(hours=row["hours_since_payment"])
            connection.execute(
                "UPDATE orders SET paid_at=?, promised_ship_at=? WHERE order_id=?",
                (paid.isoformat(), (paid + timedelta(hours=48)).isoformat(), row["order_id"]),
            )
        connection.row_factory = sqlite3.Row
        orders = [dict(r) for r in connection.execute("SELECT * FROM orders ORDER BY order_id")]
    index = directory / "index"
    shutil.copytree(ROOT / "data/supportpilot/chroma", index)
    connection = server_connection()
    connection["env"].update(
        {
            "SUPPORTPILOT_DB_PATH": str(db),
            "SUPPORTPILOT_CHROMA_PATH": str(index),
            "SUPPORTPILOT_RETRIEVAL_STRATEGY": "hybrid_rerank",
            "SUPPORTPILOT_RAG_POOL_SIZE": "12",
            "SUPPORTPILOT_RERANK_THRESHOLD": "-2",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "SUPPORTPILOT_MCP_TIMEOUT": "180",
        }
    )
    # read_timeout_seconds is set when server_connection is constructed.
    connection["session_kwargs"]["read_timeout_seconds"] = timedelta(seconds=180)
    yield connection, orders


async def run_baseline(output, ids=None, repetition=1, directory=DIRECTORY, continued_from=None):
    manifest = validate(directory)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("保留已有运行记录，请使用新输出路径")
    cases = read_json(Path(directory) / "cases.json")
    if ids:
        selected = ids.split(",")
        if len(set(selected)) != len(selected) or not set(selected) <= {c["id"] for c in cases}:
            raise ValueError("未知或重复的场景ID")
        cases = [c for c in cases if c["id"] in selected]
    if repetition < 1:
        raise ValueError("重复编号必须为正数")
    continuation = None
    if continued_from:
        previous = read_json(continued_from)
        if previous["phase"] != PHASE or previous["manifest_sha256"] != sha(
            Path(directory) / "manifest.json"
        ):
            raise ValueError("续跑来源与冻结诊断不一致")
        continuation = {"path": str(Path(continued_from).resolve()), "sha256": sha(continued_from)}
    import tempfile

    from langchain_core.messages import HumanMessage
    from langchain_core.tools import StructuredTool, tool
    from langgraph.checkpoint.memory import InMemorySaver

    # Initialize the package before temporarily replacing its three frozen modules.
    import agents.supportpilot_agent  # noqa: F401
    from evaluation.answer_trust_runner import frozen_baseline, parse_turn
    from tool_protocol.client import readonly_mcp_tools

    ledger = DiagnosticLedger(LEDGER)
    calls, context = [], {}
    get_model = None
    report = {
        "phase": PHASE,
        "kind": "baseline_mcp",
        "status": "running",
        "manifest_sha256": sha(Path(directory) / "manifest.json"),
        "repetition": repetition,
        "started_at": datetime.now(UTC).isoformat(),
        "rows": [],
        "human_review": "pending",
        "source_manifest": manifest,
        "continued_from": continuation,
        "harness_hashes": {
            p: sha(ROOT / p)
            for p in [
                "src/evaluation/answer_diagnosis.py",
                "src/evaluation/answer_trust_runner.py",
                "src/evaluation/answer_trust_budget.py",
                "scripts/diagnose_answer_failures.py",
            ]
        },
    }
    write_json(output, report, exclusive=True)

    @tool
    def request_shipping(order_id: str, kind: str = "normal", reason: str = "用户要求") -> str:
        """用户明确要求催发货时登记申请。普通催单用normal，加急用expedite。诊断中禁止实际写入。"""
        return json.dumps(
            {"status": "blocked_evaluation", "message": "诊断阻断写入，未登记工单或通知仓库。"},
            ensure_ascii=False,
        )

    try:
        with (
            frozen_baseline() as (baseline, hashes),
            tempfile.TemporaryDirectory(prefix="diagnosis-", dir=ROOT / "work") as temp,
        ):
            report["baseline_source_hashes"] = hashes
            request_shipping.description = baseline.request_shipping.description
            with isolated_connection(temp, manifest) as (connection, orders):
                report["orders_snapshot"] = orders
                get_model = factory(ledger, calls, context)
                with patch.object(baseline, "get_model", get_model):
                    async with readonly_mcp_tools(connection) as remote:
                        report["tool_contracts"] = [
                            {
                                "name": t.name,
                                "description": t.description,
                                "schema": t.args_schema
                                if isinstance(t.args_schema, dict)
                                else t.args_schema.model_json_schema(),
                            }
                            for t in remote
                        ]
                        for case in cases:
                            transport = []

                            def adapt(original):
                                async def invoke(config, **kwargs):
                                    value = await original.ainvoke(kwargs, config)
                                    delivered = value
                                    if (
                                        case["fault_injection"]
                                        and original.name == "search_knowledge_base"
                                    ):
                                        delivered = json.dumps(
                                            {
                                                "status": "no_evidence",
                                                "matches": [],
                                                "message": "诊断空证据注入对照；真实MCP已执行。",
                                            },
                                            ensure_ascii=False,
                                        )
                                    transport.append(
                                        {
                                            "name": original.name,
                                            "args": kwargs,
                                            "transport_result": value,
                                            "delivered_result": delivered,
                                            "fault_injection": case["fault_injection"],
                                        }
                                    )
                                    return delivered

                                # RunnableConfig annotation is required for LangChain injection.
                                from langchain_core.runnables import RunnableConfig

                                invoke.__annotations__["config"] = RunnableConfig
                                return StructuredTool(
                                    name=original.name,
                                    description=original.description,
                                    args_schema=original.args_schema,
                                    coroutine=invoke,
                                )

                            tools = [*(adapt(t) for t in remote), request_shipping]
                            graph = baseline.build_supportpilot_graph(
                                tools=tools, checkpointer=InMemorySaver()
                            )
                            config = {
                                "configurable": {
                                    "thread_id": uuid4().hex,
                                    "policy_check_mode": "auto",
                                },
                                "recursion_limit": 16,
                            }
                            length = 0
                            for number, question in enumerate(case["turns"], 1):
                                context.clear()
                                context.update(
                                    {
                                        "phase": PHASE,
                                        "run": str(output),
                                        "case_id": case["id"],
                                        "turn": number,
                                        "kind": "baseline",
                                        "repetition": repetition,
                                    }
                                )
                                slot = ledger.answer_slot(dict(context))
                                row = {
                                    "case_id": case["id"],
                                    "turn": number,
                                    "question": question,
                                    "history_user_turns": case["turns"][:number],
                                    "fault_injection": case["fault_injection"],
                                    "status": "running",
                                    "calls": [],
                                    "transport": [],
                                    "human_review": "pending",
                                }
                                report["rows"].append(row)
                                before, transport_start = len(calls), len(transport)
                                started = perf_counter()
                                try:
                                    result = await graph.ainvoke(
                                        {"messages": [HumanMessage(content=question)]}, config
                                    )
                                    new = result["messages"][length:]
                                    row.update(
                                        {
                                            "status": "complete",
                                            "answer": result["messages"][-1].content,
                                            "policy_check": result.get("policy_check"),
                                            **parse_turn(new),
                                            "messages": [
                                                m.model_dump(mode="json")
                                                for m in result["messages"]
                                            ],
                                        }
                                    )
                                    length = len(result["messages"])
                                except BaseException as exc:
                                    row.update(
                                        {"status": "error", "error_type": type(exc).__name__}
                                    )
                                    raise
                                finally:
                                    ledger.finish_answer(slot, row["status"])
                                    row.update(
                                        {
                                            "elapsed_ms": (perf_counter() - started) * 1000,
                                            "calls": calls[before:],
                                            "transport": transport[transport_start:],
                                        }
                                    )
                                    report["budget"] = ledger.stage_snapshot()
                                    write_json(output, report)
                                print(
                                    f"{case['id']} turn={number} repetition={repetition}: complete",
                                    flush=True,
                                )
        report["status"] = "complete"
    except BudgetStop:
        report["status"] = "budget_stopped"
        raise
    except BaseException:
        report["status"] = "failed"
        raise
    finally:
        if get_model is not None:
            await get_model.aclose()
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["budget"] = ledger.stage_snapshot()
        write_json(output, report)
    return report
