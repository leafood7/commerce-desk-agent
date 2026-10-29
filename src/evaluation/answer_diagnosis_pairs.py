"""仅重放已记录的最终生成请求，单因素改变证据；不运行候选机制。"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from evaluation.answer_diagnosis import (
    DIRECTORY,
    LEDGER,
    PHASE,
    DiagnosticLedger,
    factory,
    sha,
    validate,
)
from evaluation.answer_trust_data import documents, read_json, write_json


def transform_request(messages, change, corpus):
    """返回独立副本；只改最后一次知识库工具返回，其余输入逐字保留。"""
    from business.policy_context import flatten_policy_matches

    result = copy.deepcopy(messages)
    calls = {c["id"]: c["name"] for m in result for c in m.get("tool_calls", [])}
    positions = [
        i
        for i, m in enumerate(result)
        if m["type"] == "tool" and calls.get(m["tool_call_id"]) == "search_knowledge_base"
    ]
    if not positions:
        raise ValueError("源请求没有已配对的知识库结果")
    index = positions[-1]
    payload = json.loads(result[index]["content"])
    available = flatten_policy_matches(payload.get("matches", []))
    if change["kind"] == "add_missing":
        additions = [
            corpus[key] for key in change["ids"] if key not in {r["id"] for r in available}
        ]
        if not additions:
            raise ValueError("指定必要证据已存在，不能称为补证干预")
        payload["matches"] = [*payload.get("matches", []), *additions]
    elif change["kind"] == "filter_sources":
        keep = set(change["sources"])
        payload["matches"] = [r for r in available if r["source"] in keep]
    elif change["kind"] == "filter_ids":
        keep = set(change["ids"])
        if not keep <= {r["id"] for r in available}:
            raise ValueError("筛选不能新增源请求没有的片段")
        payload["matches"] = [r for r in available if r["id"] in keep]
    else:
        raise ValueError("不支持的诊断干预")
    if not payload["matches"]:
        raise ValueError("本轮筛选不允许意外清空证据")
    payload["status"] = "found"
    result[index]["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def restore_messages(records):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    kinds = {"ai": AIMessage, "human": HumanMessage, "system": SystemMessage, "tool": ToolMessage}
    return [kinds[r["type"]].model_validate(r) for r in records]


async def run_pairs(spec_path, output):
    manifest = validate()
    spec_path, output = Path(spec_path), Path(output)
    if output.exists():
        raise FileExistsError("不得覆盖配对结果")
    spec = read_json(spec_path)
    if len(spec["targets"]) > 4:
        raise ValueError("第一阶段最多4个目标轮（含正常对照）")
    ledger = DiagnosticLedger(LEDGER)
    calls, metadata = [], {}
    get_model = factory(ledger, calls, metadata)
    report = {
        "phase": PHASE,
        "kind": "fixed_request_pairs",
        "status": "running",
        "rows": [],
        "manifest_sha256": sha(DIRECTORY / "manifest.json"),
        "spec_sha256": sha(spec_path),
        "model_config": manifest["model"],
        "human_review": "pending",
        "started_at": datetime.now(UTC).isoformat(),
        "harness_hashes": {"answer_diagnosis_pairs.py": sha(__file__)},
        "limitations": "人工证据干预；固定首次真实历史与请求，只证明可修复空间，不证明自动化机制有效。",
    }
    write_json(output, report, exclusive=True)
    corpus = documents()
    try:
        for target in spec["targets"]:
            source_path = Path(target["source"])
            source = read_json(source_path)
            if source["manifest_sha256"] != report["manifest_sha256"]:
                raise ValueError("源运行与当前冻结输入不一致")
            row = next(
                r
                for r in source["rows"]
                if r["case_id"] == target["case_id"] and r["turn"] == target["turn"]
            )
            if row["status"] != "complete":
                raise ValueError("不能重放未完成回答")
            final_call = row["calls"][-1]
            if final_call["response_message"]["content"] != row["answer"] or final_call[
                "response_message"
            ].get("tool_calls"):
                raise ValueError("最后调用不是已记录的最终回答")
            original = final_call["request_messages"]
            changed = transform_request(original, target["change"], corpus)
            tools = final_call["tools"]
            model = (
                get_model(None).bind_tools(tools, tool_choice="none") if tools else get_model(None)
            )
            # Both arms fix tool_choice=none: generation-only, with no new retrieval or write.
            if final_call["bindings"]:
                model = model.bind(**final_call["bindings"])
            for repeat, order in [
                (1, ["original", "intervention"]),
                (2, ["intervention", "original"]),
            ]:
                for arm in order:
                    metadata.clear()
                    metadata.update(
                        {
                            "phase": PHASE,
                            "case_id": target["case_id"],
                            "turn": target["turn"],
                            "kind": "fixed_pair",
                            "arm": arm,
                            "repetition": repeat,
                            "run": str(output.resolve()),
                        }
                    )
                    slot = ledger.answer_slot(dict(metadata))
                    before = len(calls)
                    item = {
                        **metadata,
                        "status": "running",
                        "change": target["change"],
                        "source": str(source_path),
                        "source_sha256": sha(source_path),
                        "normal_control": target.get("normal_control", False),
                        "human_review": "pending",
                    }
                    report["rows"].append(item)
                    try:
                        response = await model.ainvoke(
                            restore_messages(original if arm == "original" else changed)
                        )
                        if response.tool_calls:
                            raise ValueError("固定证据实验禁止新增工具调用")
                        item.update({"answer": response.content, "status": "complete"})
                    except BaseException as exc:
                        item.update({"status": "error", "error_type": type(exc).__name__})
                        raise
                    finally:
                        ledger.finish_answer(slot, item["status"])
                        item["calls"] = calls[before:]
                        report["budget"] = ledger.stage_snapshot()
                        write_json(output, report)
                    print(
                        f"{target['case_id']} fixed_pair={repeat} arm={arm}: complete", flush=True
                    )
        report["status"] = "complete"
    except BaseException:
        report["status"] = "failed"
        raise
    finally:
        await get_model.aclose()
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["budget"] = ledger.stage_snapshot()
        write_json(output, report)
    return report
