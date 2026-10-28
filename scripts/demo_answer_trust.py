"""无付费调用的三段机制演示；脚本模型不是语义质量评测。"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("USE_FAKE_MODEL", "true")

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from agents.answer_guard import SEMANTIC_INSTRUCTIONS
from agents.policy_evidence import CHECK_INSTRUCTIONS
from agents.supportpilot_agent import build_supportpilot_graph


def evidence():
    source = "coupon_scope_policy.txt"
    paragraphs = [line.strip() for line in (ROOT / "data/knowledge" / source)
                  .read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return {"id": source + "-0", "source": source,
            "content": paragraphs[0] + "\n\n" + paragraphs[1]}


class ScriptedDemoModel:
    """明确的脚本替身：演示控制流，不冒充真实模型判断。"""

    def __init__(self, draft):
        self.draft = draft
        self.semantic_calls = 0

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None):
        instruction = messages[0].content
        if instruction == CHECK_INSTRUCTIONS:
            data = {"mode": "retrieve", "query": "优惠券适用范围",
                    "evidence_ids": [], "reason": "离线演示固定取证路径"}
        elif instruction == SEMANTIC_INSTRUCTIONS:
            self.semantic_calls += 1
            data = {"approved": True, "reason_codes": [],
                    "reason": "脚本预设的通过结果，仅演示发布机制"}
        else:
            data = self.draft
        return AIMessage(content=json.dumps(data, ensure_ascii=False))


async def run_case(name, question, draft, matches, expected):
    @tool
    def search_knowledge_base(query: str) -> str:
        """离线演示：返回固定的真实政策片段或明确空结果。"""
        return json.dumps({"status": "found" if matches else "no_evidence",
                           "matches": matches}, ensure_ascii=False)

    model = ScriptedDemoModel(draft)
    with patch("agents.supportpilot_agent.get_model", return_value=model):
        graph = build_supportpilot_graph(tools=[search_knowledge_base])
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            {"configurable": {"answer_guard_mode": "guarded"}},
        )
    audit = result["answer_audit"]
    assert audit["status"] == expected, (name, audit)
    assert model.semantic_calls <= 1
    answer = result["messages"][-1]
    assert answer.additional_kwargs.get("answer_guard_published")
    assert "fabricated-policy" not in answer.content
    return {"scenario": name, "question": question, "status": audit["status"],
            "semantic_calls": model.semantic_calls, "answer": answer.content,
            "reason_codes": audit.get("reason_codes", [])}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    row = evidence()
    valid = {"claims": [{"kind": "policy", "text": "仅限食品的品类券不能用于购买显示器。",
                         "evidence_ids": [row["id"]]}],
             "missing_conditions": [], "cannot_confirm": False}
    invalid = {"claims": [{"kind": "policy", "text": "这张券可以通用。",
                           "evidence_ids": ["fabricated-policy"]}],
               "missing_conditions": [], "cannot_confirm": False}
    unknown = {"claims": [], "missing_conditions": [], "cannot_confirm": True}
    rows = [
        await run_case("虚构引用被拦截", "食品限定券能买显示器吗？", invalid, [row], "rejected"),
        await run_case("真实片段关联后发布", "券面写仅限食品，能买显示器吗？", valid, [row], "approved"),
        await run_case("无证据时说明边界", "未收录会员卡的年费是多少？", unknown, [], "approved"),
    ]
    report = {"kind": "scripted_mechanism_demo", "paid_model_calls": 0,
              "limitation": "生成与语义判断均为脚本替身；不证明真实模型质量。", "rows": rows}
    for row in rows:
        print(f"\n{row['scenario']}（{row['status']}）\n{row['answer']}")
    print("\n以上仅为离线机制演示，无真实模型调用。")
    if args.output:
        if args.output.exists():
            parser.error("输出已存在，请选择新路径，避免覆盖演示记录。")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report["created_at"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
