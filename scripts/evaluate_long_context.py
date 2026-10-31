"""Mechanical long-context replay; no generated answer or semantic accuracy claim."""

import argparse
import hashlib
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from workbench.config import WorkbenchSettings
    from workbench.context_management import ContextOverflow, pack_messages, token_upper_bound
    from workbench.return_decisions import decision_messages
    from workbench.return_observations import decision_observation

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/long_context_v1")
    p.add_argument("--version", action="append", choices=["legacy", "budget", "summary"])
    p.add_argument("--case-id", action="append")
    p.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "data/evaluation/long_context_v1/baseline_observations.py.txt",
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    manifest = json.loads((a.dataset / "manifest.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256((a.dataset / "cases.json").read_bytes()).hexdigest()
        != manifest["cases_sha256"]
        or hashlib.sha256(
            (ROOT / "docs/fullstack/long-context-protocol.md").read_bytes()
        ).hexdigest()
        != manifest["protocol_sha256"]
    ):
        p.error("Frozen inputs changed")
    supported = [
        "context-001",
        "context-002",
        "context-007",
        "context-008",
        "context-009",
        "context-011",
    ]
    ids = a.case_id or supported
    if len(ids) != len(set(ids)) or set(ids) - set(supported):
        p.error(
            "Unknown/duplicate/unsupported replay case ID; other scenarios use integration tests"
        )
    arms = a.version or ["legacy", "budget", "summary"]
    if len(arms) != len(set(arms)):
        p.error("Duplicate version")
    if a.output.exists():
        p.error("Output exists; preserve original experiment")
    old = runpy.run_path(str(a.baseline))["decision_observation"]
    settings = WorkbenchSettings()
    units = [
        [
            {"id": f"u{i}", "role": "human", "content": "此前以为未使用，需等待最新确认。"},
            {
                "id": f"a{i}",
                "role": "assistant",
                "content": "历史助手曾说已退款，但没有实际结算字段。",
            },
        ]
        for i in range(120)
    ]
    frame = {
        "recent_units": units[-4:],
        "summary": [
            {
                "source_message_id": f"u{i}",
                "quote": "先前用户描述，仅导航而非事实",
                "kind": "user_quote_not_fact",
            }
            for i in range(12)
        ],
    }
    a.output.mkdir(parents=True)
    rows = []
    for case in ids:
        state = {
            "query": "请核对最新条件",
            "order_number": "A1001",
            "return_item_id": "item-a",
            "return_policy_id": "policy-current",
            "return_quantity": 1,
            "return_condition": "used",
            "return_basis": "no_reason",
            "reason": "用户已更正为使用过",
            "evidence": [
                {
                    "id": "c-current",
                    "version_id": "v-current",
                    "content": "适用本商家，实际签收7天内且未使用；质量问题期限不得套用。",
                }
            ],
        }
        if case == "context-001":
            state["query"] = "背景描述。" * 150 + "最后更正：已使用。"
        if case == "context-002":
            state["reason"] = "历史补充。" * 60 + "最后更正：已使用。"
        if case in ["context-007", "context-008"]:
            state["evidence"][0]["content"] = (
                "条款背景。" * 180 + "关键例外：仅未使用，排除本题情形。"
            )
        if case == "context-009":
            state["query"] = "必要完整任务。" * 1600
        if case == "context-011":
            frame = {
                **frame,
                "recent_units": [
                    [
                        {"id": "h", "role": "human", "content": "查询"},
                        {
                            "id": "call",
                            "role": "assistant",
                            "tool_calls": [{"id": "tool-1"}],
                            "content": "",
                        },
                        {
                            "id": "result",
                            "role": "tool",
                            "tool_call_id": "tool-1",
                            "content": "工具正文。" * 1500,
                        },
                    ]
                ],
            }
        for arm in arms:
            observation = (
                old(state, ["read_order", "search_policy"])
                if arm == "legacy"
                else decision_observation(state, ["read_order", "search_policy"])
            )
            messages = decision_messages(observation)
            refused = False
            meta = {}
            if arm != "legacy":
                try:
                    messages, meta = pack_messages(
                        settings, messages, frame if arm == "summary" else {}
                    )
                except ContextOverflow:
                    refused = True
                    messages = []
            exact = (
                not refused
                and observation["task"] == state["query"]
                and observation["latest_conditions"]["reason"] == state["reason"]
                and observation["evidence"][0]["text"] == state["evidence"][0]["content"]
            )
            row = {
                "case_id": case,
                "version": arm,
                "model_refused_before_dispatch": refused,
                "all_required_text_preserved": exact,
                "input_upper_bound": token_upper_bound(messages) if messages else None,
                "budget": meta,
                "messages": messages,
                "scope": "mechanical assembly stress replay, not generated answer score",
            }
            rows.append(row)
    (a.output / "traces.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    result = {
        "records": len(rows),
        "stress_cases": len(ids),
        "paid_calls": 0,
        "baseline_sha256": hashlib.sha256(a.baseline.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frozen": manifest,
        "arms": {
            arm: {
                "records": sum(r["version"] == arm for r in rows),
                "required_text_preserved": sum(
                    r["version"] == arm and r["all_required_text_preserved"] for r in rows
                ),
                "model_preflight_refusals": sum(
                    r["version"] == arm and r["model_refused_before_dispatch"] for r in rows
                ),
            }
            for arm in arms
        },
        "limits": [
            "Original model input did not include full history; managed inputs may be larger.",
            "UTF8 upper bound is not actual model token usage.",
            "No model semantic quality or task success claim from this replay.",
            "Oversized synthetic adapter input exceeds normal API limits and is an adversarial direct-call check.",
        ],
    }
    (a.output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k in ["records", "stress_cases", "paid_calls", "arms"]
            }
        )
    )


if __name__ == "__main__":
    main()
